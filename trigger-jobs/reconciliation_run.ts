import { Trigger, logger } from "@trigger.dev/sdk";
import { Decimal } from "decimal.js";

// Trigger.dev Job: Nightly Reconciliation
// Purpose: Three-way matching (ledger ↔ PSP ↔ bank), auto-resolve patterns, flag breaks

interface LedgerRecord {
  entry_id: string;
  amount: Decimal;
  posted_date: Date;
  user_id: string;
  psp_name: string;
}

interface PSPRecord {
  transaction_id: string;
  amount: Decimal;
  posted_date: Date;
  user_id: string;
}

interface BankRecord {
  reference: string;
  amount: Decimal;
  posted_date: Date;
  settlement_id: string;
}

interface Match {
  ledger_id?: string;
  psp_id?: string;
  bank_ref?: string;
  match_type: "exact" | "fuzzy" | "many_to_one";
  match_status: "exact_match" | "fuzzy_match" | "many_to_one" | "auto_resolved" | "exception" | "unmatched";
  break_type?: string;
  delta_amount: Decimal;
  resolution_notes?: string;
}

// Configuration for tolerances
const FUZZY_MATCH_TOLERANCE = new Decimal("0.01"); // 1 cent
const TIMING_DAYS_TOLERANCE = 1; // T+1 settlement timing
const AUTO_RESOLVE_PATTERNS = {
  timing: /posted next day \(T\+1\)/,
  batching: /settlement batch aggregation/,
  rounding: /FX rounding difference/,
  fee_deduction: /PSP deducted processing fee/,
};

// Stage 1: Fetch ledger records for the day
async function fetchLedgerRecords(
  db: any,
  reconciliationDate: Date
): Promise<LedgerRecord[]> {
  logger.info("Stage 1: Fetching ledger records");

  const query = `
    SELECT
      je.entry_id,
      SUM(jel.credit - jel.debit) as amount,
      je.posted_at::date as posted_date,
      je.metadata->>'user_id' as user_id,
      je.metadata->>'psp_name' as psp_name
    FROM journal_entries je
    JOIN journal_entry_lines jel ON je.entry_id = jel.entry_id
    WHERE je.posted_at::date = $1
      AND je.entry_type IN ('funding', 'transfer', 'settlement')
    GROUP BY je.entry_id, posted_date, user_id, psp_name
    ORDER BY je.posted_at ASC
  `;

  const result = await db.query(query, [reconciliationDate]);

  logger.info(`Fetched ${result.rows.length} ledger records`);

  return result.rows.map((row: any) => ({
    entry_id: row.entry_id,
    amount: new Decimal(row.amount),
    posted_date: new Date(row.posted_date),
    user_id: row.user_id,
    psp_name: row.psp_name,
  }));
}

// Stage 2: Fetch PSP records (from webhook log or PSP export)
async function fetchPSPRecords(
  db: any,
  reconciliationDate: Date
): Promise<PSPRecord[]> {
  logger.info("Stage 2: Fetching PSP records");

  const query = `
    SELECT
      metadata->>'psp_transaction_id' as transaction_id,
      amount,
      created_at::date as posted_date,
      metadata->>'user_id' as user_id
    FROM psp_webhook_events
    WHERE created_at::date = $1
      AND status = 'completed'
    ORDER BY created_at ASC
  `;

  const result = await db.query(query, [reconciliationDate]);

  logger.info(`Fetched ${result.rows.length} PSP records`);

  return result.rows.map((row: any) => ({
    transaction_id: row.transaction_id,
    amount: new Decimal(row.amount),
    posted_date: new Date(row.posted_date),
    user_id: row.user_id,
  }));
}

// Stage 3: Fetch bank statement records
async function fetchBankRecords(
  db: any,
  reconciliationDate: Date
): Promise<BankRecord[]> {
  logger.info("Stage 3: Fetching bank statement records");

  const query = `
    SELECT
      reference,
      amount,
      posted_at::date as posted_date,
      settlement_batch_id
    FROM bank_statement_records
    WHERE posted_at::date BETWEEN $1 AND $2
    ORDER BY posted_at ASC
  `;

  // Bank statement lags by 1-2 days, so fetch T and T+1
  const startDate = new Date(reconciliationDate);
  startDate.setDate(startDate.getDate() - 1);
  const endDate = new Date(reconciliationDate);
  endDate.setDate(endDate.getDate() + 1);

  const result = await db.query(query, [startDate, endDate]);

  logger.info(`Fetched ${result.rows.length} bank records`);

  return result.rows.map((row: any) => ({
    reference: row.reference,
    amount: new Decimal(row.amount),
    posted_date: new Date(row.posted_date),
    settlement_id: row.settlement_batch_id,
  }));
}

// Stage 4: Match ledger ↔ PSP (exact + fuzzy)
function matchLedgerToPSP(
  ledger: LedgerRecord[],
  psp: PSPRecord[]
): Match[] {
  logger.info("Stage 4: Matching ledger to PSP records");

  const matches: Match[] = [];
  const usedLedger = new Set<string>();
  const usedPSP = new Set<string>();

  // Exact matches: amount + user_id + date
  for (const ledgerRecord of ledger) {
    for (const pspRecord of psp) {
      if (usedPSP.has(pspRecord.transaction_id)) continue;

      if (
        ledgerRecord.amount.eq(pspRecord.amount) &&
        ledgerRecord.user_id === pspRecord.user_id &&
        isWithinTolerance(ledgerRecord.posted_date, pspRecord.posted_date, TIMING_DAYS_TOLERANCE)
      ) {
        matches.push({
          ledger_id: ledgerRecord.entry_id,
          psp_id: pspRecord.transaction_id,
          match_type: "exact",
          match_status: "exact_match",
          delta_amount: new Decimal(0),
        });

        usedLedger.add(ledgerRecord.entry_id);
        usedPSP.add(pspRecord.transaction_id);
        break;
      }
    }
  }

  // Fuzzy matches: within tolerance (fees, rounding)
  for (const ledgerRecord of ledger) {
    if (usedLedger.has(ledgerRecord.entry_id)) continue;

    for (const pspRecord of psp) {
      if (usedPSP.has(pspRecord.transaction_id)) continue;

      const delta = ledgerRecord.amount.minus(pspRecord.amount).abs();

      if (
        delta.lte(FUZZY_MATCH_TOLERANCE) &&
        ledgerRecord.user_id === pspRecord.user_id &&
        isWithinTolerance(ledgerRecord.posted_date, pspRecord.posted_date, TIMING_DAYS_TOLERANCE)
      ) {
        matches.push({
          ledger_id: ledgerRecord.entry_id,
          psp_id: pspRecord.transaction_id,
          match_type: "fuzzy",
          match_status: "fuzzy_match",
          delta_amount: delta,
          break_type: delta.gt(0) ? "amount" : "amount",
        });

        usedLedger.add(ledgerRecord.entry_id);
        usedPSP.add(pspRecord.transaction_id);
        break;
      }
    }
  }

  // Flag unmatched
  for (const ledgerRecord of ledger) {
    if (!usedLedger.has(ledgerRecord.entry_id)) {
      matches.push({
        ledger_id: ledgerRecord.entry_id,
        match_type: "many_to_one",
        match_status: "unmatched",
        delta_amount: ledgerRecord.amount,
        break_type: "missing",
      });
    }
  }

  for (const pspRecord of psp) {
    if (!usedPSP.has(pspRecord.transaction_id)) {
      matches.push({
        psp_id: pspRecord.transaction_id,
        match_type: "many_to_one",
        match_status: "unmatched",
        delta_amount: pspRecord.amount,
        break_type: "missing",
      });
    }
  }

  logger.info(`Matched ${matches.length} ledger-PSP pairs`);

  return matches;
}

// Stage 5: Match PSP ↔ Bank (settlement batches)
function matchPSPToBank(
  psp: PSPRecord[],
  bank: BankRecord[]
): Match[] {
  logger.info("Stage 5: Matching PSP to bank records");

  const matches: Match[] = [];
  const usedPSP = new Set<string>();
  const usedBank = new Set<string>();

  // Group PSP by settlement batch, sum amounts
  const pspByBatch = new Map<string, Decimal>();
  for (const record of psp) {
    const key = `${record.user_id}`;
    pspByBatch.set(key, (pspByBatch.get(key) || new Decimal(0)).plus(record.amount));
  }

  // Exact batch matches
  for (const [pspKey, pspAmount] of pspByBatch.entries()) {
    for (const bankRecord of bank) {
      if (usedBank.has(bankRecord.reference)) continue;

      if (pspAmount.eq(bankRecord.amount)) {
        matches.push({
          psp_id: pspKey,
          bank_ref: bankRecord.reference,
          match_type: "exact",
          match_status: "exact_match",
          delta_amount: new Decimal(0),
        });

        usedPSP.add(pspKey);
        usedBank.add(bankRecord.reference);
        break;
      }
    }
  }

  logger.info(`Matched ${matches.length} PSP-bank pairs`);

  return matches;
}

// Stage 6: Auto-resolve known break patterns
function autoResolvePatterns(matches: Match[]): Match[] {
  logger.info("Stage 6: Auto-resolving known break patterns");

  let autoResolved = 0;

  for (const match of matches) {
    if (match.match_status !== "exception" && match.match_status !== "unmatched") {
      continue;
    }

    // Check for timing differences (T+1 settlement)
    if (match.delta_amount.eq(0) && FUZZY_MATCH_TOLERANCE.gte(match.delta_amount.abs())) {
      match.match_status = "auto_resolved";
      match.resolution_notes = "T+1 settlement timing difference";
      autoResolved++;
      continue;
    }

    // Check for known fee patterns
    if (
      match.delta_amount.gt(0) &&
      match.delta_amount.lte(new Decimal("1.00"))
    ) {
      match.match_status = "auto_resolved";
      match.resolution_notes = "PSP processing fee deduction";
      match.break_type = "fee_deduction";
      autoResolved++;
      continue;
    }

    // Check for FX rounding
    if (
      match.delta_amount.gt(0) &&
      match.delta_amount.lte(new Decimal("0.05"))
    ) {
      match.match_status = "auto_resolved";
      match.resolution_notes = "FX conversion rounding";
      match.break_type = "fx_rounding";
      autoResolved++;
    }
  }

  logger.info(`Auto-resolved ${autoResolved} breaks`);

  return matches;
}

// Helper: Check if dates are within tolerance
function isWithinTolerance(
  date1: Date,
  date2: Date,
  daysTolerance: number
): boolean {
  const diffTime = Math.abs(date2.getTime() - date1.getTime());
  const diffDays = diffTime / (1000 * 60 * 60 * 24);
  return diffDays <= daysTolerance;
}

// Stage 7: Store matches in database
async function storeMatches(
  db: any,
  reconciliationRunId: string,
  matches: Match[]
): Promise<void> {
  logger.info("Stage 7: Storing reconciliation matches");

  const query = `
    INSERT INTO reconciliation_matches
      (reconciliation_run_id, ledger_entry_id, psp_transaction_id, bank_reference,
       match_status, break_type, delta_amount, resolution_notes, metadata)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, '{}')
  `;

  for (const match of matches) {
    await db.query(query, [
      reconciliationRunId,
      match.ledger_id || null,
      match.psp_id || null,
      match.bank_ref || null,
      match.match_status,
      match.break_type || null,
      match.delta_amount.toString(),
      match.resolution_notes || null,
    ]);
  }

  logger.info(`Stored ${matches.length} reconciliation matches`);
}

// Stage 8: Create exceptions for unmatched/auto-resolved
async function createExceptions(
  db: any,
  reconciliationRunId: string,
  matches: Match[]
): Promise<number> {
  logger.info("Stage 8: Creating reconciliation exceptions");

  const exceptions = matches.filter(
    (m) => m.match_status === "exception" || m.match_status === "unmatched"
  );

  const query = `
    INSERT INTO reconciliation_exceptions
      (run_id, break_type, priority, delta_amount, description, created_at)
    VALUES ($1, $2, $3, $4, $5, CURRENT_TIMESTAMP)
  `;

  for (const exc of exceptions) {
    const priority =
      exc.delta_amount.abs().gte(new Decimal("1000")) ? "critical" :
      exc.delta_amount.abs().gte(new Decimal("100")) ? "high" :
      exc.delta_amount.abs().gte(new Decimal("10")) ? "medium" : "low";

    await db.query(query, [
      reconciliationRunId,
      exc.break_type || "unknown",
      priority,
      exc.delta_amount.toString(),
      `${exc.break_type}: $${exc.delta_amount.toString()}`,
    ]);
  }

  logger.info(`Created ${exceptions.length} exceptions`);

  return exceptions.length;
}

// Stage 9: Update reconciliation run with final stats
async function finalizeReconciliationRun(
  db: any,
  reconciliationRunId: string,
  ledgerCount: number,
  pspCount: number,
  bankCount: number,
  matches: Match[]
): Promise<void> {
  logger.info("Stage 9: Finalizing reconciliation run");

  const exactMatches = matches.filter((m) => m.match_type === "exact").length;
  const fuzzyMatches = matches.filter((m) => m.match_type === "fuzzy").length;
  const manyToOneMatches = matches.filter((m) => m.match_type === "many_to_one").length;
  const autoResolved = matches.filter((m) => m.match_status === "auto_resolved").length;
  const exceptions = matches.filter((m) => m.match_status === "exception").length;
  const unmatched = matches.filter((m) => m.match_status === "unmatched").length;

  const totalMatched = exactMatches + fuzzyMatches + manyToOneMatches + autoResolved;
  const totalRecords = ledgerCount + pspCount + bankCount;
  const matchRate = totalRecords > 0 ? new Decimal(totalMatched).div(totalRecords) : new Decimal(0);

  const totalUnmatchedAmount = matches
    .filter((m) => m.match_status === "exception" || m.match_status === "unmatched")
    .reduce((sum, m) => sum.plus(m.delta_amount), new Decimal(0));

  const query = `
    UPDATE reconciliation_runs
    SET
      completed_at = CURRENT_TIMESTAMP,
      total_ledger_records = $2,
      total_psp_records = $3,
      total_bank_records = $4,
      exact_matches = $5,
      fuzzy_matches = $6,
      many_to_one_matches = $7,
      auto_resolved = $8,
      exceptions = $9,
      unmatched = $10,
      match_rate = $11,
      total_unmatched_amount = $12
    WHERE run_id = $1
  `;

  await db.query(query, [
    reconciliationRunId,
    ledgerCount,
    pspCount,
    bankCount,
    exactMatches,
    fuzzyMatches,
    manyToOneMatches,
    autoResolved,
    exceptions,
    unmatched,
    matchRate.toString(),
    totalUnmatchedAmount.toString(),
  ]);

  logger.info("Reconciliation run finalized", {
    run_id: reconciliationRunId,
    match_rate: matchRate.toFixed(4),
  });
}

// Main Trigger.dev Job Handler
export const reconciliationRunJob = new Trigger({
  id: "reconciliation-run-job",
  name: "Nightly Reconciliation Run",
  on: {
    event: "reconciliation.trigger",
  },
  run: async (event: any, { db, logger: jobLogger }: any) => {
    const now = new Date();
    const reconciliationDate = new Date(now);
    reconciliationDate.setDate(reconciliationDate.getDate() - 1); // Reconcile yesterday's transactions
    reconciliationDate.setHours(0, 0, 0, 0);

    const runId = `recon_${reconciliationDate.toISOString().split("T")[0]}_${Date.now()}`;

    try {
      jobLogger.info("Starting reconciliation run", { run_id: runId });

      // Create reconciliation run record
      await db.query(
        `INSERT INTO reconciliation_runs (run_id, run_date, started_at)
         VALUES ($1, $2, CURRENT_TIMESTAMP)`,
        [runId, reconciliationDate]
      );

      // CHECKPOINT 1: Fetch all three data sources
      const ledgerRecords = await fetchLedgerRecords(db, reconciliationDate);
      const pspRecords = await fetchPSPRecords(db, reconciliationDate);
      const bankRecords = await fetchBankRecords(db, reconciliationDate);

      // CHECKPOINT 2: Perform matching
      const ledgerToPSPMatches = matchLedgerToPSP(ledgerRecords, pspRecords);
      const pspToBankMatches = matchPSPToBank(pspRecords, bankRecords);
      const allMatches = [...ledgerToPSPMatches, ...pspToBankMatches];

      // CHECKPOINT 3: Auto-resolve known patterns
      const resolvedMatches = autoResolvePatterns(allMatches);

      // CHECKPOINT 4: Store matches
      await storeMatches(db, runId, resolvedMatches);

      // CHECKPOINT 5: Create exceptions
      const exceptionCount = await createExceptions(db, runId, resolvedMatches);

      // CHECKPOINT 6: Finalize run
      await finalizeReconciliationRun(
        db,
        runId,
        ledgerRecords.length,
        pspRecords.length,
        bankRecords.length,
        resolvedMatches
      );

      jobLogger.info("Reconciliation run completed successfully", {
        run_id: runId,
        exception_count: exceptionCount,
      });

      return {
        status: "COMPLETED",
        run_id: runId,
        exception_count: exceptionCount,
        ledger_records: ledgerRecords.length,
        psp_records: pspRecords.length,
      };
    } catch (error) {
      jobLogger.error("Reconciliation run failed", { error, run_id: runId });

      // Mark run as failed
      await db.query(
        "UPDATE reconciliation_runs SET completed_at = CURRENT_TIMESTAMP WHERE run_id = $1",
        [runId]
      );

      throw error;
    }
  },
});

export default reconciliationRunJob;

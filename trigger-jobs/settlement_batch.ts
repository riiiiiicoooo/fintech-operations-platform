import { Trigger, logger } from "@trigger.dev/sdk";
import { Decimal } from "decimal.js";

// Trigger.dev Job: Daily Settlement Batch Processing
// Runs: 6 PM ET daily via n8n cron trigger
// Purpose: Aggregate pending transactions, generate NACHA, submit to bank

interface PendingTransaction {
  entry_id: string;
  user_id: string;
  amount: Decimal;
  psp_name: string;
  posted_at: Date;
}

interface SettlementPosition {
  user_id: string;
  transaction_count: number;
  gross_total: Decimal;
  fees_total: Decimal;
  holdback_total: Decimal;
  net_payout: Decimal;
}

interface SettlementBatch {
  batch_id: string;
  settlement_date: Date;
  status: string;
  transaction_count: number;
  gross_amount: Decimal;
  platform_fees: Decimal;
  psp_fees: Decimal;
  holdback: Decimal;
  net_payout: Decimal;
  unique_users: number;
  submitted_at?: Date;
}

interface NACHARecord {
  type: string;
  routing_number: string;
  account_number: string;
  amount: Decimal;
  name: string;
}

// Idempotency key to prevent duplicate settlement batches
function generateIdempotencyKey(date: Date): string {
  const dateStr = date.toISOString().split("T")[0];
  return `settlement_batch_${dateStr}`;
}

// Stage 1: Fetch pending transactions
async function fetchPendingTransactions(
  db: any,
  settlementDate: Date
): Promise<PendingTransaction[]> {
  logger.info("Stage 1: Fetching pending transactions", { date: settlementDate });

  const query = `
    SELECT
      je.entry_id,
      je.metadata->>'user_id' as user_id,
      SUM(jel.credit - jel.debit) as amount,
      je.metadata->>'psp_name' as psp_name,
      je.posted_at
    FROM journal_entries je
    JOIN journal_entry_lines jel ON je.entry_id = jel.entry_id
    WHERE je.entry_type = 'transfer'
      AND je.posted_at::date = $1
      AND je.metadata->>'settlement_status' = 'pending'
    GROUP BY je.entry_id, user_id, psp_name, je.posted_at
    ORDER BY je.posted_at ASC
  `;

  const result = await db.query(query, [settlementDate]);
  logger.info(`Fetched ${result.rows.length} pending transactions`);

  return result.rows.map((row: any) => ({
    entry_id: row.entry_id,
    user_id: row.user_id,
    amount: new Decimal(row.amount),
    psp_name: row.psp_name,
    posted_at: new Date(row.posted_at),
  }));
}

// Stage 2: Calculate net positions per user (with fees and holdback)
async function calculateNetPositions(
  transactions: PendingTransaction[],
  platformFeeRate: Decimal = new Decimal("0.025") // 2.5%
): Promise<SettlementPosition[]> {
  logger.info("Stage 2: Calculating net settlement positions");

  const positionMap: Map<string, SettlementPosition> = new Map();

  for (const txn of transactions) {
    const platformFee = txn.amount.times(platformFeeRate);
    const pspFee = new Decimal("0.30"); // Fixed PSP fee
    const totalFees = platformFee.plus(pspFee);
    const holdback = txn.amount.times(new Decimal("0.05")); // 5% holdback
    const netPayout = txn.amount.minus(totalFees).minus(holdback);

    if (!positionMap.has(txn.user_id)) {
      positionMap.set(txn.user_id, {
        user_id: txn.user_id,
        transaction_count: 0,
        gross_total: new Decimal(0),
        fees_total: new Decimal(0),
        holdback_total: new Decimal(0),
        net_payout: new Decimal(0),
      });
    }

    const pos = positionMap.get(txn.user_id)!;
    pos.transaction_count += 1;
    pos.gross_total = pos.gross_total.plus(txn.amount);
    pos.fees_total = pos.fees_total.plus(totalFees);
    pos.holdback_total = pos.holdback_total.plus(holdback);
    pos.net_payout = pos.net_payout.plus(netPayout);
  }

  const positions = Array.from(positionMap.values());
  logger.info(`Calculated positions for ${positions.length} users`);

  return positions;
}

// Stage 3: Generate NACHA batch file (ACH format for bank submission)
async function generateNACHABatch(
  positions: SettlementPosition[],
  batchId: string,
  effectiveDate: Date
): Promise<string> {
  logger.info("Stage 3: Generating NACHA batch file");

  const lines: string[] = [];

  // NACHA File Header Record (Type 101)
  const fileHeader = [
    "101", // Record Type
    " 021000021", // Sending Bank (ACH routing)
    " 0000000000", // Receiving Bank
    effectiveDate.toISOString().split("T")[0].replace(/-/g, ""), // File Creation Date
    effectiveDate.toISOString().split("T")[1].substring(0, 4), // File Creation Time
    batchId.padEnd(6), // File ID Modifier
    "094101", // Record Size
    "10", // Blocking Factor
    "1", // Format Code (0 or 1)
    "USA", // Destination Country Code
    "USD", // Destination Currency Code
    "ORIGINNAME        ", // Originating Company Name
    "BANKNAME           ", // Originating Bank Name
    "", // Reserved
  ];
  lines.push(fileHeader.join(""));

  // NACHA Batch Header Record (Type 105)
  let totalAmount = new Decimal(0);
  let entryCount = 0;

  for (const pos of positions) {
    totalAmount = totalAmount.plus(pos.net_payout);
    entryCount += pos.transaction_count;
  }

  const batchHeader = [
    "105", // Record Type
    "021000021", // Service Class Code
    "ORIGINNAME        ", // Company Name
    "COMPANYID   ", // Company Discretionary Data
    batchId, // Batch Sequence Number
    "130101", // Settlement Date
    effectiveDate.toISOString().split("T")[0].replace(/-/g, ""), // Origination Date
    "", // Origination Time
    "000001", // Sequence Number
    "PPD", // Transaction Code (Prearranged Payment and Deposit)
    "021000021", // Originating Routing Number
  ];
  lines.push(batchHeader.join(""));

  // Entry Detail Records (Type 605)
  for (const pos of positions) {
    const entryDetail = [
      "605", // Record Type
      "200", // Transaction Code (200 = ACH Debit)
      pos.user_id.padEnd(8), // Receiving DFI Identification (routing)
      pos.user_id.padEnd(17), // Account Number
      formatAmount(pos.net_payout), // Amount
      "USER" + pos.user_id.substring(0, 10).padEnd(6), // ID Number
      "USER PAYOUT", // Individual Name
      "", // Discretionary Data
      "", // Addendum Indicator (0 = no)
      "000000001", // Trace Number
    ];
    lines.push(entryDetail.join(""));
  }

  // Batch Control Record (Type 805)
  const batchControl = [
    "805", // Record Type
    "200", // Service Class Code
    String(positions.length).padStart(6, "0"), // Entry/Addendum Count
    String(entryCount).padStart(10, "0"), // Entry Hash
    formatAmount(totalAmount), // Total Debits
    "0000000000000000", // Total Credits
    "021000021", // Company ID
    "", // Message Authentication Code
    "", // Reserved
    "", // Originating DFI ID
    "", // Batch Sequence Number
  ];
  lines.push(batchControl.join(""));

  // File Control Record (Type 901)
  const fileControl = [
    "901", // Record Type
    "000001", // Batch Count
    "000001", // Block Count
    String(positions.length + 4).padStart(6, "0"), // Entry/Addendum Count
    "000000", // Entry Hash
    formatAmount(totalAmount), // Total Debits
    "0000000000000000", // Total Credits
    "", // Reserved
  ];
  lines.push(fileControl.join(""));

  const nacha = lines.join("\n");
  logger.info(`Generated NACHA file with ${entryCount} entries`);

  return nacha;
}

// Helper: Format decimal amount for NACHA (cents, no decimal)
function formatAmount(amount: Decimal): string {
  const cents = amount.times(100).toFixed(0);
  return cents.padStart(12, "0");
}

// Stage 4: Store NACHA batch and pending ledger entries
async function storeNACHABatch(
  db: any,
  batchId: string,
  settlementBatchId: string,
  nacha: string,
  effectiveDate: Date
): Promise<void> {
  logger.info("Stage 4: Storing NACHA batch to database");

  const query = `
    INSERT INTO nacha_batches
      (batch_id, settlement_batch_id, file_name, batch_number, entry_count,
       total_debits, total_credits, effective_date, status, nacha_content)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
  `;

  const entryCount = (nacha.match(/^605/gm) || []).length;

  await db.query(query, [
    batchId,
    settlementBatchId,
    `settlement_${batchId}.ach`,
    1,
    entryCount,
    "0", // total_debits
    "0", // total_credits
    effectiveDate,
    "created",
    nacha,
  ]);

  logger.info("NACHA batch stored successfully");
}

// Stage 5: Submit NACHA to bank via API
async function submitToBank(
  nacha: string,
  batchId: string,
  bankApiKey: string
): Promise<{ acknowledgment_id: string; submitted_at: Date }> {
  logger.info("Stage 5: Submitting NACHA to bank");

  // This is a mock call - in production, use your bank's ACH API
  const bankResponse = await fetch("https://api.bank.example.com/ach/submit", {
    method: "POST",
    headers: {
      "Content-Type": "text/plain",
      Authorization: `Bearer ${bankApiKey}`,
      "Idempotency-Key": batchId, // Prevent duplicate submission
    },
    body: nacha,
  });

  if (!bankResponse.ok) {
    throw new Error(`Bank API error: ${bankResponse.statusText}`);
  }

  const response = await bankResponse.json();

  logger.info("NACHA submitted to bank", {
    acknowledgment_id: response.ack_id,
  });

  return {
    acknowledgment_id: response.ack_id,
    submitted_at: new Date(),
  };
}

// Stage 6: Wait for bank acknowledgment (polling with timeout)
async function waitForBankAcknowledgment(
  acknowledgmentId: string,
  bankApiKey: string,
  maxRetries: number = 12, // 1 minute total (5s intervals)
  retryIntervalSeconds: number = 5
): Promise<boolean> {
  logger.info("Stage 6: Waiting for bank acknowledgment");

  for (let i = 0; i < maxRetries; i++) {
    // Wait before checking (except first check)
    if (i > 0) {
      await new Promise((resolve) =>
        setTimeout(resolve, retryIntervalSeconds * 1000)
      );
    }

    const response = await fetch(
      `https://api.bank.example.com/ach/status/${acknowledgmentId}`,
      {
        headers: {
          Authorization: `Bearer ${bankApiKey}`,
        },
      }
    );

    const data = await response.json();

    if (data.status === "acknowledged") {
      logger.info("Bank acknowledged NACHA batch");
      return true;
    }

    if (data.status === "rejected") {
      throw new Error(`Bank rejected batch: ${data.rejection_reason}`);
    }

    logger.debug(`Waiting for acknowledgment... (attempt ${i + 1}/${maxRetries})`);
  }

  throw new Error("Bank acknowledgment timeout after 1 minute");
}

// Stage 7: Update ledger to mark transactions as settled
async function updateLedgerSettled(
  db: any,
  transactions: PendingTransaction[],
  settlementBatchId: string
): Promise<void> {
  logger.info("Stage 7: Updating ledger with settlement confirmation");

  const entryIds = transactions.map((t) => t.entry_id);

  const query = `
    UPDATE journal_entries
    SET metadata = jsonb_set(
          metadata,
          '{settlement_status}',
          to_jsonb('settled'::text)
        ),
        metadata = jsonb_set(
          metadata,
          '{settlement_batch_id}',
          to_jsonb($1::text)
        )
    WHERE entry_id = ANY($2)
  `;

  await db.query(query, [settlementBatchId, entryIds]);

  logger.info(`Updated ${entryIds.length} ledger entries to settled`);
}

// Stage 8: Record settlement completion and trigger reconciliation
async function finalizeSettlement(
  db: any,
  settlementBatchId: string,
  positions: SettlementPosition[]
): Promise<void> {
  logger.info("Stage 8: Finalizing settlement in database");

  const totalGross = positions.reduce(
    (sum, p) => sum.plus(p.gross_total),
    new Decimal(0)
  );
  const totalFees = positions.reduce(
    (sum, p) => sum.plus(p.fees_total),
    new Decimal(0)
  );
  const totalHoldback = positions.reduce(
    (sum, p) => sum.plus(p.holdback_total),
    new Decimal(0)
  );
  const totalPayout = positions.reduce(
    (sum, p) => sum.plus(p.net_payout),
    new Decimal(0)
  );

  const query = `
    UPDATE settlement_batches
    SET status = 'submitted',
        submitted_at = CURRENT_TIMESTAMP,
        gross_amount = $1,
        platform_fees = $2,
        holdback = $3,
        net_payout = $4,
        unique_users = $5
    WHERE batch_id = $6
  `;

  await db.query(query, [
    totalGross.toString(),
    totalFees.toString(),
    totalHoldback.toString(),
    totalPayout.toString(),
    positions.length,
    settlementBatchId,
  ]);

  logger.info("Settlement batch finalized and marked as submitted");
}

// Main Trigger.dev Job Handler
export const settlementBatchJob = new Trigger({
  id: "settlement-batch-job",
  name: "Daily Settlement Batch Processing",
  on: {
    event: "settlement.batch.trigger",
  },
  run: async (event: any, { db, logger: jobLogger }: any) => {
    const batchId = generateIdempotencyKey(new Date());
    const settlementDate = new Date();
    settlementDate.setHours(0, 0, 0, 0);

    try {
      jobLogger.info("Starting settlement batch job", { batchId });

      // CHECKPOINT 1: Idempotency check
      const existing = await db.query(
        "SELECT batch_id FROM settlement_batches WHERE batch_id = $1",
        [batchId]
      );

      if (existing.rows.length > 0) {
        jobLogger.info("Settlement batch already exists, skipping", { batchId });
        return { status: "SKIPPED", batch_id: batchId };
      }

      // Create settlement batch record
      await db.query(
        `INSERT INTO settlement_batches (batch_id, settlement_date, status, created_at)
         VALUES ($1, $2, 'created', CURRENT_TIMESTAMP)`,
        [batchId, settlementDate]
      );

      // CHECKPOINT 2: Fetch transactions
      const transactions = await fetchPendingTransactions(db, settlementDate);

      if (transactions.length === 0) {
        jobLogger.info("No pending transactions for settlement");
        await db.query(
          "UPDATE settlement_batches SET status = 'confirmed' WHERE batch_id = $1",
          [batchId]
        );
        return { status: "NO_TRANSACTIONS", batch_id: batchId };
      }

      // CHECKPOINT 3: Calculate net positions
      const positions = await calculateNetPositions(transactions);

      // CHECKPOINT 4: Generate NACHA
      const nacha = await generateNACHABatch(positions, batchId, settlementDate);

      // CHECKPOINT 5: Store NACHA
      await storeNACHABatch(db, batchId, batchId, nacha, settlementDate);

      // CHECKPOINT 6: Submit to bank
      const bankApiKey = process.env.BANK_API_KEY || "";
      const bankSubmission = await submitToBank(nacha, batchId, bankApiKey);

      // CHECKPOINT 7: Wait for acknowledgment
      await waitForBankAcknowledgment(bankSubmission.acknowledgment_id, bankApiKey);

      // CHECKPOINT 8: Update ledger
      await updateLedgerSettled(db, transactions, batchId);

      // CHECKPOINT 9: Finalize settlement
      await finalizeSettlement(db, batchId, positions);

      jobLogger.info("Settlement batch completed successfully", {
        batch_id: batchId,
        transaction_count: transactions.length,
      });

      return {
        status: "COMPLETED",
        batch_id: batchId,
        transaction_count: transactions.length,
        settlement_date: settlementDate,
      };
    } catch (error) {
      jobLogger.error("Settlement batch failed", { error, batch_id: batchId });

      // Mark batch as failed for manual recovery
      await db.query(
        "UPDATE settlement_batches SET status = 'failed' WHERE batch_id = $1",
        [batchId]
      );

      throw error;
    }
  },
});

export default settlementBatchJob;

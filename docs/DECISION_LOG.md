# Decision Log: Fintech Operations Platform

**Last Updated:** February 2025

This document captures key product and technical decisions, what alternatives were considered, and why we chose the path we did. Decisions are numbered chronologically.

---

## DEC-001: Internal Double-Entry Ledger vs. PSP as Source of Truth

**Date:** September 2024
**Status:** Accepted
**Decider:** PM + CTO

**Context:** The platform was using Stripe's dashboard and settlement reports as the source of truth for all financial data. This worked at low volume but created three problems: (1) reconciliation required manually matching Stripe exports against our database and bank statements, (2) support couldn't answer "where is this user's money?" without checking three systems, and (3) we had no single record showing how an employer's $50,000 funding became individual wallet credits plus platform fees.

**Option A: Continue using Stripe as source of truth.** Improve our data sync from Stripe. Build better reporting on top of Stripe's data. Accept that Stripe's view of the world is canonical.

**Option B: Build an internal ledger using single-entry accounting.** One row per transaction with amount, status, and metadata. Simpler than double-entry. Faster to build.

**Option C: Build an internal double-entry ledger.** Every financial event recorded as a journal entry where debits equal credits. Full chart of accounts. Immutable entries.

**Decision:** Option C (double-entry ledger).

**Rationale:**
- Option A fails when you add a second PSP (which we knew was coming for DEC-002). With two PSPs, there is no single external source of truth. You need an internal one.
- Option B (single-entry) can't answer "where did the money go?" for a multi-party transaction. If an employer funds $500 and the user receives $493.75, single-entry shows two separate events. Double-entry shows one journal entry with the employer debit, user credit, and fee credit on the same record. The $6.25 is accounted for immediately, not discovered during reconciliation.
- Double-entry's constraint (debits must equal credits) catches bugs at write time. A single-entry system can have a bug that credits a user without debiting anything, and nobody notices until month-end. Double-entry rejects that write immediately.
- The build cost was ~3 additional weeks vs. single-entry. Given that the ledger is the foundation everything else depends on, the investment was justified.

**Risk accepted:** Double-entry is harder for the engineering team to work with. Most engineers haven't built accounting systems. We spent a week on internal education and built journal entry templates so engineers don't compose entries from scratch.

---

## DEC-002: Multi-PSP Orchestration vs. Single PSP with Better Error Handling

**Date:** October 2024
**Status:** Accepted
**Decider:** PM + Engineering Lead

**Context:** We were 100% dependent on Stripe. When Stripe had a 47-minute partial outage in September, our transaction success rate dropped to 68%. Users saw failed transfers with generic error messages. Support received 200+ tickets in under an hour.

**Option A: Stay on Stripe, improve error handling.** Add better retry logic, more informative error messages, and a queue for failed transactions to retry after recovery.

**Option B: Add a second PSP (Adyen) with manual failover.** Integrate Adyen as backup. Operations team manually switches traffic during outages.

**Option C: Add multiple PSPs with automated failover.** Integrate Adyen and Tabapay. Build a routing layer with health scoring, circuit breakers, and automatic failover.

**Decision:** Option C (automated multi-PSP orchestration).

**Rationale:**
- Option A doesn't solve the problem. Better error handling during a Stripe outage still means zero transactions process. Retrying after recovery creates a thundering herd that can trigger rate limits.
- Option B (manual failover) has a human in the critical path. The September outage started at 2:47 AM. The on-call engineer was alerted at 2:53 AM, assessed the situation by 3:10 AM, and could have switched traffic by 3:20 AM. That's 33 minutes of downtime with manual failover vs. 47 minutes with no failover. Automated failover would have triggered within 60 seconds.
- Option C's PSP abstraction layer (standardized adapter interface) means adding a third or fourth PSP is days of work, not weeks. The routing layer is the expensive part, and it only needs to be built once.
- Tabapay was added specifically for instant transfers (push-to-debit). Stripe and Adyen support instant transfers but at higher cost. Tabapay specializes in real-time payouts and is 40% cheaper per transaction for that specific use case.

**Consequences:**
- Positive: During Stripe's December degradation (elevated latency, not full outage), the circuit breaker shifted 30% of traffic to Adyen automatically. User-facing impact: zero. No support tickets.
- Negative: Three PSP integrations means three webhook handlers, three settlement report formats, and three sets of API quirks to maintain. Ongoing maintenance cost is real.

---

## DEC-003: Synchronous Fraud Check vs. Async Fraud Analysis

**Date:** October 2024
**Status:** Accepted
**Decider:** PM + Fraud Analyst

**Context:** Where in the transaction flow should fraud detection happen? Before the ledger write (synchronous, blocks the user) or after (asynchronous, doesn't block but might need to claw back money).

**Option A: Fully synchronous.** Fraud check runs before the ledger write. User waits for the result. If flagged, the transaction is blocked before any money moves.

**Option B: Fully asynchronous.** Ledger write happens immediately. Fraud analysis runs after. If flagged, the system reverses the transaction.

**Option C: Hybrid.** Fast rule-based scoring runs synchronously (< 100ms). Complex analysis (pattern detection across transaction history) runs asynchronously.

**Decision:** Option C (hybrid).

**Rationale:**
- Option A with the full rule set would add 200-400ms to every transaction. Most transactions (96%+) are legitimate. Making every user wait for a comprehensive fraud analysis is a poor UX trade-off.
- Option B means money moves before we know if the transaction is fraudulent. Reversing a completed transfer is legally and operationally messy. If the user has already spent the money in their bank account, the reversal fails and becomes a collections problem.
- The hybrid approach runs the core rules synchronously (velocity, amount, device, geo checks in < 100ms). These rules catch ~90% of fraud. The remaining fraud is caught by async monitoring rules (structuring patterns, behavioral anomalies) that analyze patterns across days or weeks of data. These patterns can't be evaluated in real-time anyway because they require aggregate queries across the user's history.

**Risk accepted:** A transaction that passes the sync rules but is caught by async monitoring has already moved money. We accept this risk because: (a) the sync rules catch the majority of fraud, (b) the async monitoring typically catches issues within hours (before settlement), and (c) we place holds on the funds during the PSP processing window, giving the async system time to flag before money leaves the platform.

---

## DEC-004: Balance Calculated from Entries vs. Cached Balance Column

**Date:** November 2024
**Status:** Accepted (with materialized view compromise)
**Decider:** PM + Engineering Lead

**Context:** How should we calculate a user's balance? Sum all their journal entry lines every time (accurate but potentially slow) or maintain a cached balance column that updates on each transaction (fast but creates a second source of truth)?

**Option A: Calculate from entries every time.** `SELECT SUM(credits) - SUM(debits) FROM journal_entry_lines WHERE account_id = ?`. Always correct. No caching.

**Option B: Cached balance column on accounts table.** Update the column in the same database transaction as the journal entry write. Fast reads. Balance column is the primary read target.

**Option C: Calculate from entries for writes, materialized view for reads.** Ledger writes always check the real balance (from entries). User-facing balance queries read a materialized view that refreshes on a schedule.

**Decision:** Option C (entries for writes, materialized view for reads).

**Rationale:**
- Option A is correct but slow at scale. A user with 3,000 journal entry lines (a year of daily transactions) requires scanning all 3,000 rows for every balance check. At 50,000 users, that's a lot of aggregate queries hitting the database.
- Option B is fast but creates the "two sources of truth" problem. If a bug causes the cached balance to drift from what the entries say, which is correct? The entries are always correct (they're the immutable record), but if the cached balance is wrong and a user withdraws based on the cached number, we've authorized an overdraft.
- Option C gives us the safety of entry-based calculation for writes (the moment that matters for correctness) and the performance of a pre-computed view for reads (the moment that matters for user experience). The materialized view refreshes every 30 seconds, so user-facing balances are at most 30 seconds stale. For the "just completed a transaction, checking my balance" case, the API recalculates from entries and returns the real-time number, bypassing the view.

**Consequences:**
- Positive: Zero balance discrepancy incidents in 4 months of production. The materialized view has never shown a balance that caused a user to take an incorrect action.
- Negative: The 30-second staleness window occasionally confuses users who check their balance immediately after a transaction and see the old number. We added a client-side optimistic update to mitigate this.

---

## DEC-005: SERIALIZABLE Isolation for Ledger Writes vs. READ COMMITTED with Application-Level Locking

**Date:** November 2024
**Status:** Accepted
**Decider:** PM + Engineering Lead + DBA

**Context:** Two concurrent transactions against the same account can create a race condition. Both read a balance of $500, both attempt to debit $400, and both succeed, creating a $300 overdraft. How do we prevent this?

**Option A: READ COMMITTED with advisory locks.** Use PostgreSQL advisory locks on the account_id before reading the balance. Simpler, lower overhead, but requires discipline to acquire locks everywhere.

**Option B: SERIALIZABLE isolation on ledger write transactions.** PostgreSQL detects the read-write conflict and forces one transaction to retry. No application-level locking needed.

**Option C: Optimistic locking with version counter.** Add a version column to the account. Read the version, do the work, write with WHERE version = expected_version. If version changed, retry.

**Decision:** Option B (SERIALIZABLE isolation).

**Rationale:**
- Option A works but requires every code path that touches the ledger to acquire the advisory lock. If one code path forgets (a new endpoint, a batch job, a migration script), the race condition is back. The bug won't show up in testing because it requires precise timing. It'll show up in production at 3 AM on a Friday. SERIALIZABLE isolation is enforced by the database regardless of what the application code does.
- Option C is common for web applications but not appropriate for a ledger. A version counter conflict causes a retry, which re-reads the balance and might succeed. But the retry logic needs to re-evaluate the entire transaction (balance check, hold creation, journal entry), not just replay the write. This is essentially re-implementing what SERIALIZABLE gives you for free.
- SERIALIZABLE has higher overhead (~20% more latency per ledger write in our benchmarks). On a 80ms ledger write, that's 16ms additional. This is well within our 500ms budget and worth the correctness guarantee.

**Risk accepted:** SERIALIZABLE can cause serialization failures under high contention (many concurrent writes to the same account). We handle this with retry logic (up to 3 retries with exponential backoff). In practice, serialization failures occur on < 0.1% of transactions because most transactions touch different accounts.

---

## DEC-006: Progressive KYC Tiers vs. Full Verification Upfront

**Date:** November 2024
**Status:** Accepted
**Decider:** PM + Compliance Officer

**Context:** Users need to be verified before transacting. Full KYC (government ID + database check) takes 1-3 minutes and has a 5% manual review rate (24-hour delay). During our original full-KYC-upfront flow, 34% of users dropped off during onboarding and never completed verification.

**Option A: Full verification upfront.** Every user completes Standard tier verification before their first transaction. Higher security, but high drop-off.

**Option B: Progressive tiers.** Basic tier (email + phone + employer match) in < 30 seconds with low limits. Standard tier (ID + database) required for higher limits. Enhanced tier for the highest limits.

**Option C: No verification for small transactions.** Allow transactions under $50 with zero verification. Verify when cumulative volume hits a threshold.

**Decision:** Option B (progressive tiers).

**Rationale:**
- Option A's 34% drop-off was our biggest growth constraint. A user who downloads the app to access their earned wages and is asked for a passport photo before seeing a balance will often abandon. These aren't fraud risks; they're hourly workers who want their money.
- Option C is a regulatory risk. Even small transactions require minimum customer identification under FinCEN's guidelines. "No verification" is not a position we can defend in an examination.
- Progressive KYC satisfies both regulatory requirements (every user is verified at a level appropriate to their risk) and business needs (users can transact immediately at low limits). The employer match in Basic tier is actually a strong signal: the employer has already verified this person is a real employee. That's more signal than many fintechs have at onboarding.
- After deploying progressive KYC, onboarding drop-off fell from 34% to 7.8%. The majority of users (68%) stayed at Basic tier because their transaction patterns never exceeded the limits. 28% upgraded to Standard. 4% reached Enhanced.

---

## DEC-007: Rules-Based Fraud Engine First vs. ML Model First

**Date:** December 2024
**Status:** Accepted
**Decider:** PM + Fraud Analyst + ML Lead

**Context:** We needed fraud detection at launch. The question was whether to invest in building an ML model or start with a rule-based system.

**Option A: Build ML model first.** Invest 3-4 months in model development. Higher accuracy potential from day one.

**Option B: Rules engine first, ML later.** Ship configurable rules in 3 weeks. Use the rules engine to collect labeled data. Build ML model once we have sufficient training data.

**Decision:** Option B (rules first, ML later).

**Rationale:**
- An ML model needs labeled training data: examples of confirmed fraud and confirmed legitimate transactions. On day one, we had neither. We had Stripe's chargeback history, but that only covers card fraud detected by the card network, not platform-level fraud (account takeover, synthetic identity, structuring).
- The rules engine serves double duty: it detects known fraud patterns immediately, and it generates labeled decisions that become training data for the ML model. Every rule evaluation is logged with the full feature vector and the outcome (approved, reviewed, declined) plus the eventual ground truth (was it actually fraud?). After 6 months, we had 500K+ labeled decisions. That's a real training set.
- Rules are transparent and auditable. When the compliance officer asks "why was this transaction declined?" the answer is "it triggered rules X, Y, and Z with scores A, B, and C." With an ML model, the answer is "the model assigned a probability of 0.73." Regulators prefer the former, especially before the model has a track record.
- The ML model (planned for Phase 4) will run alongside the rules engine, not replace it. Rules handle known patterns. ML catches unknown patterns. The rules engine stays as a fast pre-filter and a fallback if the ML model has issues.

---

## DEC-008: Nightly Batch Reconciliation vs. Real-Time Per-Transaction Reconciliation

**Date:** December 2024
**Status:** Accepted (nightly batch, with real-time planned for Phase 4)
**Decider:** PM + Finance Ops Lead

**Context:** Reconciliation compares our ledger against PSP settlement reports and bank statements. When should this happen?

**Option A: Real-time (per-transaction).** Reconcile each transaction as it completes. Immediate detection of discrepancies.

**Option B: Nightly batch.** Collect all data sources once per day and reconcile in bulk. Issues detected next morning.

**Option C: Micro-batch (every 4 hours).** Compromise between real-time and nightly.

**Decision:** Option B (nightly batch) for v1.

**Rationale:**
- Real-time reconciliation sounds ideal but faces a practical problem: the three data sources don't update at the same time. Our ledger updates instantly. Stripe's settlement report updates when Stripe processes the batch (usually next business day). Bank statements arrive via BAI2 file the following morning. There's nothing to reconcile in real-time because two of the three sources don't have the data yet.
- Nightly batch aligns with when all three data sources are actually available. The reconciliation job runs at 2:00 AM ET, after the bank's BAI2 file arrives (typically by 1:00 AM ET) and after Stripe's settlement report for the prior day is finalized.
- The 24-hour detection delay is acceptable because: (a) the ledger's double-entry constraint catches most data integrity issues at write time, (b) fraud detection runs synchronously before money moves, and (c) holds prevent double-spending during the settlement window. The reconciliation catches the residual issues that slip through these real-time controls.
- Real-time reconciliation is planned for Phase 4, using PSP webhook events as a trigger to reconcile individual transactions immediately. This will reduce detection time to minutes rather than hours. But it requires significant infrastructure (event-driven architecture, real-time bank feeds) that wasn't justified for the initial build.

---

## DEC-009: Celery Workers vs. Temporal for Background Jobs

**Date:** October 2024
**Status:** Accepted
**Decider:** PM + Engineering Lead

**Context:** The platform has several background jobs: settlement batching (daily), reconciliation (nightly), compliance monitoring (continuous), notification dispatch (per-event), and report generation (on-demand). These need reliable execution with monitoring and retry capabilities.

**Option A: Celery + Redis.** Standard Python task queue. Team has experience. Battle-tested. Celery Beat for scheduling.

**Option B: Temporal.** Durable execution engine. Workflows survive crashes. Built-in retry, timeout, and signal handling. Steeper learning curve.

**Option C: AWS Step Functions.** Managed workflow service. No infrastructure to maintain. But vendor lock-in and limited Python integration.

**Decision:** Option A (Celery + Redis).

**Rationale:**
- Our background jobs are relatively straightforward: trigger on schedule or event, execute a series of steps, handle errors, report status. Temporal's strengths (long-running workflows with human-in-the-loop, complex saga patterns, stateful workflow queries) are valuable but not critical for our current job patterns.
- The engineering team has 3+ years of Celery experience across prior projects. Temporal would require 4-6 weeks of learning and migration overhead. The opportunity cost of that time is real: 4-6 weeks is roughly the build time for an entire service.
- Celery Beat handles our scheduling needs (settlement at 6 PM ET, reconciliation at 2 AM ET, compliance monitoring every 15 minutes). Task chaining handles multi-step workflows. Dead letter queues handle failures.
- If we outgrow Celery (specifically if we need durable execution for multi-day compliance workflows or complex saga patterns for cross-PSP operations), Temporal migration is a bounded effort because our jobs are already structured as discrete steps.

**Risk accepted:** Celery does not guarantee exactly-once execution. A worker crash can cause a task to be retried, leading to duplicate execution. We mitigate this with idempotency: every job is designed so that re-execution produces the same result (settlement batch generation checks if the batch already exists, reconciliation deduplicates matches).

---

## DEC-010: Separate Compliance Event Store vs. Shared Application Database

**Date:** November 2024
**Status:** Accepted
**Decider:** PM + Compliance Officer + Engineering Lead

**Context:** Compliance events (KYC decisions, monitoring alerts, SAR case data) need special access controls and retention policies that differ from application data.

**Option A: Shared database, same schema.** Compliance events in the same PostgreSQL schema as application tables. Simplest to build. Access controlled at the application layer.

**Option B: Shared database, separate schema.** Compliance events in a dedicated `compliance` schema within the same PostgreSQL instance. Different access permissions per schema. Application user can INSERT but not UPDATE/DELETE.

**Option C: Separate database entirely.** Compliance events in a different PostgreSQL instance. Complete isolation.

**Decision:** Option B (separate schema, same database).

**Rationale:**
- Option A fails the access control test. Application-layer access control means any bug in the application could expose SAR case details to support agents, which violates the SAR tipping-off prohibition. Database-level access control (the application user literally cannot read the compliance.sar_cases table) is a stronger guarantee.
- Option C is the most secure but creates operational complexity. Cross-database queries become impossible (can't JOIN compliance events with transaction data in a single query). The compliance dashboard would need to call two databases and merge results. At our scale (~15K users, ~5K transactions/day), the isolation benefit doesn't justify the complexity.
- Option B gives us database-level access control (different PostgreSQL roles per schema), different retention policies (compliance schema has 7-year retention, application tables have 2-year), and the ability to JOIN across schemas when the compliance team needs full context (e.g., alert investigation that needs transaction history).
- The application database user (`app_user`) has INSERT-only access to the compliance schema. It cannot read, update, or delete compliance events. The compliance user (`compliance_reader`) has read access to both schemas. This means the compliance dashboard can show transaction context alongside alert data, but the application itself cannot see compliance-only data.

**Consequences:**
- Positive: During a banking partner audit, we demonstrated that SAR data was inaccessible to the application layer. The auditor specifically noted this as a strength.
- Negative: Debugging compliance-related issues requires switching database roles, which adds friction for on-call engineers. We created a runbook for this.

---

## DEC-011: Hold Expiration at 7 Days vs. Indefinite Holds

**Date:** December 2024
**Status:** Accepted
**Decider:** PM + Finance Ops Lead

**Context:** When a user initiates a transfer, a hold is placed on their wallet to prevent double-spending. The hold is released when the PSP confirms or when the transaction fails. But what happens if we never receive the PSP callback?

**Option A: Indefinite holds.** Hold stays active until explicitly resolved. Prevents any possibility of double-spending from a stuck transaction.

**Option B: 7-day expiration.** Hold expires automatically after 7 days. Creates a monitoring alert for investigation.

**Option C: 24-hour expiration.** Aggressive expiration to minimize user impact.

**Decision:** Option B (7-day expiration).

**Rationale:**
- Option A risks permanently locking user funds. A stuck webhook (PSP delivered it, our processor crashed, it was never retried) would leave a user's balance reduced indefinitely. This is worse than double-spending from a user experience perspective, because the user is harmed with certainty (their money is locked) rather than probabilistically (they might double-spend).
- Option C (24 hours) is too aggressive. ACH transfers can take 1-3 business days to settle. A hold that expires before settlement completes defeats the purpose of the hold. A user could see their balance restored, initiate another transfer, and then both settle, creating a negative balance.
- 7 days covers the longest expected settlement window (ACH during a holiday weekend) with margin. In practice, 99.8% of holds are resolved within 48 hours. Holds that reach day 7 are genuine anomalies that warrant investigation.
- Expired holds generate a CRITICAL alert. They are not a normal occurrence. In 4 months of production, we've had 3 hold expirations, all caused by webhook processing failures that were independently detected and resolved.

---

## DEC-012: Auto-Resolution Dollar Threshold at $5.00 vs. $0.01 vs. Percentage-Based

**Date:** January 2025
**Status:** Accepted
**Decider:** PM + Finance Ops Lead

**Context:** The reconciliation engine can auto-resolve certain break patterns (timing differences, PSP fee deductions, rounding). What dollar threshold should limit auto-resolution?

**Option A: $0.01 (sub-penny only).** Most conservative. Only auto-resolves obvious rounding differences.

**Option B: $5.00 absolute cap.** Auto-resolves breaks up to $5.00 if they match a known pattern.

**Option C: Percentage-based (0.5% of transaction amount).** Scales with transaction size. A $1,000 transaction could auto-resolve up to $5.00, but a $100 transaction only up to $0.50.

**Decision:** Option B ($5.00 absolute cap), with pattern matching required.

**Rationale:**
- Option A was too conservative in practice. PSP fee deductions are typically $0.30-$3.00. Timing differences between PSP settlement dates and bank posting dates are exact-amount (not a delta) but need the matching logic to recognize them. At $0.01, the reconciliation team was spending 40% of their time on breaks that were obviously PSP fees.
- Option C is intellectually appealing but harder to audit. "Why was this $4.80 break auto-resolved?" "Because it was 0.48% of the $1,000 transaction." That's harder for an auditor to evaluate than "Because it was $4.80, which is under our $5.00 threshold, and it matched the PSP fee deduction pattern."
- $5.00 was chosen because it's the natural ceiling for PSP fees on transactions in our typical range ($50-$2,500). A $5.01 break is likely not a PSP fee and warrants human review.
- The dollar threshold is necessary but not sufficient. Auto-resolution requires BOTH a delta under $5.00 AND a matching pattern (timing, fee deduction, batch netting, or rounding). A $3.00 delta with no matching pattern is NOT auto-resolved.

**Consequence:** After tuning the auto-resolution patterns and adopting the $5.00 threshold, the reconciliation exception count dropped from ~35 per run to ~8 per run. Diana (Finance Ops) went from spending 3 hours reviewing exceptions to 45 minutes. Weekly audit of auto-resolved items confirmed zero false resolutions in 90 days.

---

## DEC-013: Asynchronous Fraud Scoring with Hold-and-Review Over Real-Time Inline Scoring

**Date:** February 2025
**Status:** Accepted (supersedes earlier approach)
**Decider:** PM + Fraud Analyst + Engineering Lead

**Context:** V1 implemented real-time inline fraud scoring — every transaction was scored synchronously before processing. The fraud model ran on each payment request, returning a risk score that determined approve/decline/review.

**What Happened:** p95 latency on the checkout flow jumped from 1.2s to 3.8s. Checkout conversion dropped 8% in the first week. The fraud model needed 800ms-1.5s for scoring (ML model inference + feature lookup + velocity checks). For legitimate transactions (96%+ of volume), this added latency with no benefit. The ops team was also overwhelmed — every flagged transaction required immediate review because the payment was held inline.

**Decision:** Moved to async scoring with hold-and-review. Transactions process immediately with a basic rule-based pre-screen (velocity limits, known bad actors, amount thresholds — <50ms). Full ML scoring runs asynchronously within 30 seconds. High-risk scores trigger a hold, and ops team has 4-hour SLA to review. Very high-risk (>0.95 score) auto-declines.

**Rationale:** Checkout latency returned to 1.4s (from 3.8s). Conversion recovered. Fraud detection rate actually improved (from 82% to 94.3%) because the async model had more context (could analyze the full session, not just the single transaction). The 4-hour review window is acceptable for the transaction sizes in play ($50-$5,000).

**Consequences:** Small window of exposure (up to 30 seconds before scoring completes, up to 4 hours before manual review). Mitigated by pre-screen rules catching obvious fraud immediately. Required building a review queue UI for the ops team. Net positive: better UX, better detection, better ops workflow.

**Lesson:** Real-time doesn't always mean better. For fraud, having more context (async) beat having less context (inline) — even with a small delay.

---

## DEC-014: Multi-PSP Pivot - Stripe to Adyen for International Transactions

**Date:** January 2025
**Status:** Accepted
**Decider:** PM + Finance Lead + Engineering Lead

**Context:** The platform had built a strong domestic market with Stripe as the sole PSP. In Q4 2024, the largest employer client onboarded a multinational workforce with 35% international transaction volume. Stripe's cross-border payment fees for these transactions were running 3.2% of transaction value (compared to 1.8% domestic), eating significantly into the client's already thin margin (they paid the platform 0.5% + $0.25 per transaction).

**Problem:** At $1.2M monthly volume with 35% international, Stripe's cross-border premium was costing the client $12,600/month in excess fees. The client was considering moving to a competitor with lower international rates.

**Option A: Negotiate with Stripe.** Request a volume discount or carve-out for international transactions. Stripe doesn't negotiate rates; they're the lowest in the market domestically but not internationally.

**Option B: Accept the higher fees.** Absorb the cost or negotiate with the client to pay more. Neither option was sustainable.

**Option C: Multi-PSP routing with geographic logic.** Integrate a second PSP (Adyen) with pricing optimized for international. Build a routing layer that sends international transactions to Adyen and domestic to Stripe. Domestic transactions stay at 1.8%, international to Adyen at 1.9%.

**Decision:** Option C (multi-PSP routing with geographic logic).

**Rationale:**
- Multi-PSP infrastructure was already planned (DEC-002), so the engineering cost was incremental: add an Adyen adapter and update the router.
- Adyen's international rates (1.9%) beat Stripe (3.2%), saving the client $15,600/month on $1.2M volume.
- The PSP adapter abstraction pattern made this tractable: two weeks of engineering vs. two months if we had to redesign from scratch.
- Client retention risk was real. The client represents 18% of platform revenue.

**Implementation:** Built a routing rule that checks transaction geo:
```python
def route_payment(instruction):
    if instruction.currency in ['USD', 'CAD'] and user_country in ['US', 'CA']:
        return 'stripe'  # Domestic rate: 1.8%
    elif instruction.currency in INTERNATIONAL_CURRENCIES:
        return 'adyen'   # International rate: 1.9%
    else:
        return 'stripe'  # Fallback
```

**Results:** Client international costs dropped to 1.9% from 3.2%. Client expanded international hiring headcount by 60% within two months. Platform revenue increased (more transaction volume at higher velocity). Multi-PSP infrastructure proved its value during Stripe's December degradation (DEC-002 consequence).

**Lesson:** Product decisions hide in finance discussions. The cross-border fee problem wasn't a tech problem; it was a routing problem, and solving it operationally (by adding a PSP) unlocked growth.

---

## DEC-015: AWS RDS PostgreSQL Over Supabase for Production Infrastructure

**Date:** February 2025
**Status:** Accepted
**Decider:** PM + CTO + Compliance Officer

**Context:** The platform initially used Supabase (managed PostgreSQL + Auth + Realtime) for simplicity. As the platform scaled to $8M ARR and compliance requirements tightened, three issues emerged:

1. **Compliance:** Supabase is hosted on AWS but abstracted from direct AWS management. Banking partners required evidence of Multi-AZ failover, backup retention policies, and audit logging under our control.
2. **Isolation levels:** Supabase's PostgreSQL ran READ COMMITTED by default. Changing to SERIALIZABLE per session requires Supabase support; we can't set it at the database role level.
3. **Cost:** Supabase charges $50/month for Postgres Pro + $200/month for extra storage. AWS RDS for equivalent capacity was $120/month (on-demand) with Multi-AZ adding $120/month backup cost. Total: $240 vs. $250, but RDS gave us direct control.

**Option A: Stay with Supabase.** Accept compliance friction and request support escalations for isolation level changes. Cheaper in ops overhead (no infrastructure management).

**Option B: Migrate to AWS RDS.** Self-manage PostgreSQL on RDS. More operational burden but direct control and better compliance posture.

**Option C: Use AWS Aurora PostgreSQL.** Managed service with better HA and read replicas. Higher cost (~$400/month) but less operational work.

**Decision:** Option B (AWS RDS PostgreSQL with Multi-AZ).

**Rationale:**
- Compliance requirements (Multi-AZ, backup retention, audit logging) are non-negotiable for banking partnerships. RDS gives us explicit control and documentation.
- We needed SERIALIZABLE isolation at the database role level (not per-session), which requires direct database access. Supabase would need vendor escalation each time.
- Cost was comparable ($240 vs. $250/month), so we could justify the operational overhead.
- We didn't need Aurora's read replicas yet (no complex analytical queries), so the extra cost wasn't justified.

**Implementation:**
- Created RDS instance: PostgreSQL 15.2, Multi-AZ, 4GB RAM (db.t3.medium)
- Configured backups: 30-day retention, automated daily backup at 2 AM ET
- Set database-level parameters: `default_transaction_isolation = 'serializable'` for app role
- Enabled Enhanced Monitoring: CPU, memory, I/O metrics to Datadog
- Migrated data: pg_dump from Supabase → psql to RDS (~2GB, 40 minutes)

**Operational Changes:**
- Gained responsibility for patching (AWS handles minor versions, we test and apply)
- Monitor storage growth (set up alerts at 80%)
- Manage IAM database auth (more complex than Supabase API keys, but more secure)
- Run nightly vacuum jobs (Supabase did this automatically)

**Results:** Passed banking partner audit. Compliance officer's concerns about data residency and failover now have documentation. Isolation level set at database level; engineers don't need to remember it per transaction. Cost flat; ops burden +4 hours/month for routine maintenance.

**Risk accepted:** Self-managed database means outages are now our responsibility. Mitigation: Multi-AZ failover is automatic; backups tested monthly; on-call rotation for database alerts.

---

## DEC-016: Apache Kafka for Event Streaming Over Redis Streams

**Date:** February 2025
**Status:** Accepted
**Decider:** PM + Engineering Lead

**Context:** The platform initially used Redis Streams for event-driven communication (transaction.created → settlement service, fraud service, etc.). As the system scaled, Redis Streams showed limitations:

1. **Partition scalability:** Redis Streams had no built-in partitioning. All consumers competed for a single stream, creating a bottleneck.
2. **Retention:** Redis Streams required explicit trimming or data was lost on eviction. For 7-year compliance retention, we'd need external archival.
3. **Ordering guarantees:** Multiple partitions make exactly-once delivery harder to reason about in Redis.

**Option A: Stick with Redis Streams.** Continue using them; add manual partitioning by account_id (separate stream per account).

**Option B: Use AWS SQS.** Managed queue service. Simpler operations. But no order guarantees across messages, and SQS charges per request ($0.40 per million requests).

**Option C: Switch to Apache Kafka.** Industry standard for event streaming. Partitioned by design. Higher operational overhead (self-host or use AWS MSK).

**Decision:** Option C (Apache Kafka).

**Rationale:**
- **Ordering:** Kafka's partitions by account_id guarantee all transactions for a single account are processed in order. This is critical for settlement (can't process settlement batch before all transactions for that account are ingested).
- **Retention:** Kafka topics can be configured with 7-year retention. We archive old messages to S3 nightly for cost and compliance.
- **Scalability:** Multiple consumers can scale independently. Fraud detection can lag settlement slightly without blocking transactions.
- **Cost:** AWS MSK (Managed Kafka) is ~$0.60 per hour + storage. For a 3-node cluster with 7-day retention, monthly cost is ~$450 + $200 storage = $650. Redis Streams on RDS is free (same database). But the operational value (partitioning, retention, ordering guarantees) justified the cost.

**Architecture:**
```
Ledger Service emits transaction.created
  ↓
Kafka Topic: transactions
├── Partition 0: account_id % 10 == 0
├── Partition 1: account_id % 10 == 1
├── ... (10 partitions for parallelism)
└── Partition 9: account_id % 10 == 9

Consumer Group 1 (Settlement)
├── Consumes: partition 0
├── Consumes: partition 1
├── ... (can scale to 10 instances, one per partition)

Consumer Group 2 (Fraud)
├── Reads same topic
├── Offset independent
```

**Consequences:** Required learning curve (Kafka ops is more complex than Redis). Added $650/month to infrastructure costs. Ordered processing by account_id enabled settling confidence ("all txns for account A are definitely processed before settlement batch").

---

## DEC-017: Temporal for Settlement Orchestration Over n8n

**Date:** February 2025
**Status:** Accepted
**Decider:** PM + Engineering Lead

**Context:** The platform previously used n8n for settlement workflows: collect transactions → calculate splits → generate batch files → submit to PSP → wait for confirmation. n8n was low-code and quick to stand up, but caused operational issues:

1. **Durability:** When an n8n worker crashed mid-workflow, the entire settlement batch had to restart from the beginning. If we were at "waiting for PSP confirmation," we'd re-submit the batch and potentially duplicate payouts.
2. **Visibility:** No built-in way to query "what's the status of settlement batch 2025-02-28-001?" without digging through database logs.
3. **Timeout handling:** n8n workflows had a 24-hour timeout limit. We needed to wait up to 48 hours for PSP confirmation without automatic failure.

**Option A: Stick with n8n.** Implement better idempotency checks to prevent duplicate payouts. Still had visibility and timeout issues.

**Option B: Use AWS Step Functions.** Managed orchestration. Better than n8n. But tied to AWS, and limited Python integration.

**Option C: Use Temporal.** Temporal is designed for long-running workflows with durability and human-in-the-loop. Higher learning curve. Open source or hosted (Temporal Cloud).

**Decision:** Option C (Temporal).

**Rationale:**
- **Durability:** Temporal checkpoints workflow state at each activity boundary. If a worker crashes after "Activity 3: Submit batch to PSP" completes, recovery resumes at "Activity 4: Wait for confirmation" without re-executing the submit.
- **Visibility:** Query workflow status in real-time: `temporal workflow show --workflow_id settlement_batch_2025_02_28_001` returns current state and next action.
- **Timeout handling:** Temporal supports custom timeout logic. We set activity timeouts (each PSP call max 4 hours) and workflow timeout (48 hours total), with explicit retry logic.
- **Cost:** Temporal Cloud is ~$200/month for our scale. n8n was $50/month, but add-on operators (conditional logic, retry) pushed it to $150/month equivalent. Temporal at $200 was worth it for durability.

**Implementation:**
```python
@workflow.defn
class SettlementBatchWorkflow:
    @workflow.run
    async def run(self, batch_id: str):
        # Activity 1: collect
        txns = await workflow.execute_activity(
            collect_settled_transactions,
            batch_id,
            start_to_close_timeout=timedelta(minutes=5)
        )

        # Activity 2: calculate splits
        instructions = await workflow.execute_activity(
            calculate_settlement_splits,
            txns,
            start_to_close_timeout=timedelta(minutes=10)
        )

        # Activity 3: submit to PSP (with retry)
        payout_ids = await workflow.execute_activity(
            submit_to_psp,
            instructions,
            start_to_close_timeout=timedelta(hours=4),
            retry_policy=RetryPolicy(
                max_attempts=5,
                initial_interval=timedelta(seconds=60),
                backoff_coefficient=2.0
            )
        )

        # Activity 4: wait for confirmation (long timeout)
        await workflow.execute_activity(
            wait_for_psp_confirmation,
            payout_ids,
            start_to_close_timeout=timedelta(hours=48)
        )

        # Activity 5: post ledger entries
        await workflow.execute_activity(
            post_settlement_entries,
            batch_id,
            start_to_close_timeout=timedelta(minutes=10)
        )
```

**Results:** Zero duplicate payout incidents after migration. Settlement batch visibility reduced ops support tickets by 40% (people could self-check status). Longer PSP confirmation timeouts enabled partnerships with slower processors (some international PSPs have 36-hour settlement window).

---

## DEC-018: Datadog for Monitoring Over Grafana + Prometheus

**Date:** February 2025
**Status:** Accepted
**Decider:** PM + Engineering Lead + Compliance Officer

**Context:** The platform initially used Grafana (dashboard) + Prometheus (metrics) for monitoring. At scale, this setup had gaps:

1. **Logs:** Prometheus has no log aggregation. We used CloudWatch, but correlated CloudWatch logs with Prometheus metrics required manual switching between consoles.
2. **APM:** No built-in application tracing. To debug slow transactions, we dug through logs and tried to reconstruct the flow.
3. **Compliance:** Grafana/Prometheus have no audit logging of who viewed what data. Banking partners asked, "Do you have audit trails showing who accessed financial reports?"

**Option A: Expand Grafana + Prometheus.** Add Loki (log aggregation) and Tempo (tracing). Stitched-together solution. Operational burden: maintain 4 systems.

**Option B: Use Datadog.** All-in-one: metrics, logs, APM, and more. Higher cost. Vendor lock-in risk.

**Option C: Use New Relic or Splunk.** Alternatives to Datadog. Similar feature set.

**Decision:** Option B (Datadog).

**Rationale:**
- **Unified view:** A single console shows metrics, logs, and traces. When a transaction is slow, click "View related logs" and see what happened at each stage.
- **APM:** Datadog APM automatically traces FastAPI requests, SQL queries, and inter-service calls. Zero code changes needed (well, just adding the agent).
- **Compliance:** Datadog logs user access to dashboards and exported data. We satisfied the banking partner audit question immediately.
- **Cost:** Prometheus/Grafana + CloudWatch + Splunk logs was ~$400/month across tools. Datadog at ~$500/month was slightly more but replaced three systems.

**Implementation:**
- Install Datadog agent on all servers
- Point FastAPI metrics to Datadog (via StatsD client or Python library)
- Redirect PostgreSQL query logs to Datadog
- Redirect application logs to Datadog (JSON format for structured logging)
- Set up Datadog dashboard mirroring our old Grafana boards

**Consequences:** Required onboarding team on Datadog UI (initially confusing with all the features). Datadog contract terms are aggressive; easy to overspend on features we don't need. But for a fintech platform, the compliance and audit trail value outweighed the cost and complexity.

**Bonus:** Datadog's financial services dashboard templates included KPIs for reconciliation rates, settlement volumes, and fraud metrics that we had built manually in Grafana. Saved engineering time.

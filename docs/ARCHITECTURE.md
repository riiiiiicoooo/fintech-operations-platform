# System Architecture: Fintech Operations Platform

**Last Updated:** February 2025
**Status:** Production (v2.0)

---

## 1. High-Level Architecture

```
                              ┌───────────────────────────────┐
                              │           USERS                │
                              │  End Users (mobile/web)        │
                              │  Employer Finance Admins       │
                              │  Finance Ops (Diana)           │
                              │  Compliance (Priya)            │
                              │  Customer Support              │
                              └──────────────┬────────────────┘
                                             │
                                             ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                            CLIENT LAYER                                    │
│                                                                            │
│  React Native (mobile)           Next.js (web dashboards)                  │
│  ├── User wallet + balance       ├── Finance ops dashboard                 │
│  ├── Transfer initiation         ├── Reconciliation console                │
│  ├── Transaction history         ├── Fraud review queue                    │
│  ├── Push notifications          ├── Compliance monitoring                 │
│  └── Dispute flow                ├── Employer admin portal                 │
│                                  └── Support transaction viewer            │
│                                                                            │
│  Auth: JWT + Cognito/Auth0 (MFA for internal users)                        │
│  Component library: shadcn/ui (web), React Native Paper (mobile)           │
└──────────────────────────────────┬─────────────────────────────────────────┘
                                   │ HTTPS / WebSocket
                                   ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                        API GATEWAY (FastAPI)                                │
│                                                                            │
│  /api/v1/transactions   - Initiate, status, history, dispute               │
│  /api/v1/accounts       - Balance queries, account management              │
│  /api/v1/ledger         - Journal entries, balance snapshots (internal)     │
│  /api/v1/funding        - Employer funding schedules, batch status          │
│  /api/v1/reconciliation - Reports, exceptions, resolution                  │
│  /api/v1/compliance     - KYC status, alerts, SAR workflow                 │
│  /api/v1/admin          - Fraud rules config, PSP health, system status    │
│                                                                            │
│  Middleware: auth validation, rate limiting, idempotency key extraction,    │
│  request logging, tenant context injection                                 │
└──────┬──────────┬──────────┬──────────┬──────────┬──────────┬─────────────┘
       │          │          │          │          │          │
       ▼          ▼          ▼          ▼          ▼          ▼
┌──────────┐ ┌─────────┐ ┌─────────┐ ┌────────┐ ┌─────────┐ ┌───────────┐
│ LEDGER   │ │ PAYMENT │ │SETTLEMENT│ │ FRAUD  │ │  RECON  │ │COMPLIANCE │
│ SERVICE  │ │ SERVICE │ │ SERVICE  │ │SERVICE │ │ SERVICE │ │ SERVICE   │
└────┬─────┘ └────┬────┘ └────┬────┘ └───┬────┘ └────┬────┘ └─────┬─────┘
     │            │           │          │           │             │
     └────────────┴───────────┴──────┬───┴───────────┴─────────────┘
                                     │
                                     ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                            DATA LAYER                                      │
│                                                                            │
│  AWS RDS PostgreSQL 15 (Multi-AZ, ACID transactions, SERIALIZABLE)         │
│  ├── Ledger: accounts, journal_entries, holds                              │
│  ├── Transactions: state machine, event history                            │
│  ├── Compliance: kyc_verifications, monitoring_alerts, sar_cases           │
│  ├── Reconciliation: recon_runs, matches, exceptions                       │
│  └── Audit: compliance_events (append-only, 7-year retention)              │
│                                                                            │
│  Redis: idempotency key store, fraud rule cache, PSP health scores,        │
│         rate limiting                                                      │
│                                                                            │
│  Event Bus (Apache Kafka):                                                 │
│  ├── transactions: topic with partitions by account_id                     │
│  ├── settlement: topic for settlement workflow events                      │
│  ├── fraud: topic for fraud detection and alerts                          │
│  ├── reconciliation: topic for recon matches and exceptions                │
│  └── compliance: topic for KYC, AML, and SAR events                       │
└────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                         EXTERNAL INTEGRATIONS                              │
│                                                                            │
│  PSP Layer:              Banking:              Identity/Compliance:         │
│  ├── Stripe (primary)    ├── Partner bank API   ├── Alloy (KYC)            │
│  ├── Adyen (fallback)    ├── BAI2 ingestion     ├── OFAC SDN list          │
│  └── Tabapay (instant)   └── NACHA file gen     └── FinCEN SAR e-filing    │
│                                                                            │
│  Notifications:                                                            │
│  ├── FCM (push)                                                            │
│  ├── Twilio (SMS)                                                          │
│  └── SendGrid (email)                                                      │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Service Architecture

The platform is organized into eight core services. Each service owns a specific domain and communicates through the shared PostgreSQL database for synchronous reads and Apache Kafka for async events. The ledger service is the central authority. No other service writes directly to ledger tables. Temporal orchestrates long-running workflows like settlement batching.

### 2.1 Ledger Service

The single source of truth for all money in the system. Every financial event is a journal entry. No money moves, no balance changes, no fee is collected without a journal entry posted here first.

```
Transaction Request
      │
      ▼
┌─────────────────────────────┐
│  Idempotency Check          │
│                              │
│  Redis lookup:               │
│  key = idempotency_key       │
│                              │
│  EXISTS? -> return cached    │
│            result (200 OK)   │
│                              │
│  NEW? -> proceed, store key  │
│          with TTL = 7 days   │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│  Account Validation          │
│                              │
│  Source account exists?       │
│  Source account active?       │
│  Source account type allows   │
│  this transaction type?      │
│                              │
│  Destination account exists?  │
│  Destination account active?  │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│  Balance Check               │
│                              │
│  available_balance =         │
│    SUM(credits) - SUM(debits)│
│    - SUM(active_holds)       │
│                              │
│  available >= amount?        │
│  NO -> reject (insufficient) │
│  YES -> continue             │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│  Journal Entry Creation      │
│  (single database            │
│   transaction, SERIALIZABLE) │
│                              │
│  1. Insert journal_entry     │
│     header (id, timestamp,   │
│     idempotency_key,         │
│     description)             │
│                              │
│  2. Insert line items:       │
│     DR source_account  $X    │
│     CR dest_account    $X    │
│     (+ fee lines if any)     │
│                              │
│  3. Validate: SUM(debits)    │
│     == SUM(credits)          │
│     FAIL -> rollback entire  │
│     transaction              │
│                              │
│  4. Create hold on source    │
│     (if async settlement)    │
│                              │
│  5. Commit                   │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│  Emit Event                  │
│                              │
│  transaction.created         │
│  {txn_id, amount, type,      │
│   accounts, timestamp}       │
│                              │
│  Consumers:                  │
│  - Payment service (execute) │
│  - Fraud service (log)       │
│  - Notification service      │
└─────────────────────────────┘
```

**Why SERIALIZABLE isolation for ledger writes:**

The ledger cannot tolerate race conditions. If two concurrent transactions both read a balance of $500 and both try to debit $400, the database must ensure only one succeeds. We use SERIALIZABLE isolation on the journal entry transaction, which detects this conflict and forces a retry on the second transaction. The retry then sees the updated balance ($100) and correctly rejects the second debit. This is slower than READ COMMITTED but correctness is non-negotiable for a ledger.

**Balance calculation approach:**

We calculate balances from journal entries in real time rather than maintaining a cached balance column. A cached balance creates a second source of truth that can drift from the entries. If entries say the balance should be $500 but the cached column says $480, which is correct? The entries are always correct, and the cache is a bug waiting to happen.

For performance, we use a materialized view that pre-aggregates balances and refreshes on a 30-second interval. User-facing balance queries hit the materialized view (< 50ms). Ledger writes always calculate from entries (< 200ms).

```sql
CREATE MATERIALIZED VIEW account_balances AS
SELECT
    account_id,
    SUM(CASE WHEN direction = 'credit' THEN amount ELSE 0 END) -
    SUM(CASE WHEN direction = 'debit' THEN amount ELSE 0 END) AS posted_balance,
    COALESCE(h.hold_total, 0) AS held_amount,
    (SUM(CASE WHEN direction = 'credit' THEN amount ELSE 0 END) -
     SUM(CASE WHEN direction = 'debit' THEN amount ELSE 0 END) -
     COALESCE(h.hold_total, 0)) AS available_balance
FROM journal_entry_lines jel
LEFT JOIN (
    SELECT account_id, SUM(amount) AS hold_total
    FROM holds
    WHERE status = 'active' AND expires_at > NOW()
    GROUP BY account_id
) h USING (account_id)
GROUP BY account_id, h.hold_total;

CREATE UNIQUE INDEX idx_account_balances ON account_balances(account_id);
```

### 2.2 Payment Service

Handles all communication with external PSPs. No business logic about what should be charged or who should be paid. That logic lives in the ledger and settlement services. The payment service only knows how to execute a payment instruction against a PSP and report the result.

```
Payment Instruction (from ledger or settlement service)
      │
      ▼
┌─────────────────────────────────┐
│  PSP Router                      │
│                                  │
│  Input: payment_method, amount,  │
│         currency, priority       │
│                                  │
│  Routing table:                  │
│  ┌──────────┬─────────┬────────┐│
│  │ Method   │ Primary │Fallback││
│  ├──────────┼─────────┼────────┤│
│  │ ACH      │ Stripe  │ Adyen  ││
│  │ Card     │ Stripe  │ Adyen  ││
│  │ Wire     │ Bank API│ (none) ││
│  │ Instant  │ Tabapay │ Stripe ││
│  └──────────┴─────────┴────────┘│
│                                  │
│  Check circuit breaker state:    │
│  CLOSED  -> use primary          │
│  OPEN    -> use fallback         │
│  HALF_OPEN -> test one request   │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│  Health Score Check              │
│                                  │
│  score = weighted(               │
│    0.4 * success_rate_1hr,       │
│    0.3 * p95_latency_1hr,        │
│    0.2 * error_rate_15min,       │
│    0.1 * uptime_24hr             │
│  )                               │
│                                  │
│  score >= 0.8 -> use this PSP    │
│  score <  0.8 -> try fallback    │
│  both < 0.8   -> queue + alert   │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│  Execute with Retry              │
│                                  │
│  attempt = 1                     │
│  max_attempts = 3                │
│                                  │
│  LOOP:                           │
│    PSP.create_payment(           │
│      amount, method,             │
│      idempotency_key             │
│    )                             │
│                                  │
│    SUCCESS -> return result      │
│                                  │
│    FAILURE:                      │
│      retryable? (timeout, 5xx)   │
│        YES -> wait (2^attempt    │
│               + random(0, 1s))   │
│               attempt += 1       │
│        NO  -> (4xx, validation)  │
│               return failure     │
│                                  │
│    attempt > max_attempts?       │
│      primary PSP -> try fallback │
│      fallback PSP -> return fail │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│  Circuit Breaker Update          │
│                                  │
│  Record outcome in Redis:        │
│  INCR psp:{name}:successes      │
│  INCR psp:{name}:failures       │
│                                  │
│  failures_60s >= 5?              │
│    -> OPEN circuit               │
│    -> SET psp:{name}:circuit     │
│       = open (TTL 30s)           │
│                                  │
│  TTL expires?                    │
│    -> HALF_OPEN (test 1 request) │
│    -> success? CLOSED            │
│    -> failure? re-OPEN (60s)     │
└─────────────────────────────────┘
```

**PSP abstraction layer:**

Each PSP has a standardized adapter that translates our internal payment instruction format to the PSP's API format. Adding a new PSP means implementing the adapter interface, not changing the orchestrator.

```
┌─────────────────────────────────────────────────┐
│  PSP Adapter Interface                           │
│                                                  │
│  create_payment(instruction) -> PaymentResult    │
│  capture_payment(payment_id) -> CaptureResult    │
│  void_payment(payment_id) -> VoidResult          │
│  refund_payment(payment_id, amount) -> RefundRes │
│  get_payment_status(payment_id) -> StatusResult  │
│  process_webhook(payload, sig) -> WebhookEvent   │
│                                                  │
│  Implementations:                                │
│  ├── StripeAdapter                               │
│  ├── AdyenAdapter                                │
│  ├── TabapayAdapter                              │
│  └── BankAPIAdapter                              │
└─────────────────────────────────────────────────┘
```

### 2.3 Event Streaming (Apache Kafka)

All services communicate asynchronously through Kafka topics, enabling scalable event processing and service decoupling. Transaction lifecycle events flow through the system without tight coupling between services.

**Topics and partitioning:**

- **transactions**: Partitioned by `account_id` to maintain order for a single account. Consumers: payment service, settlement service, reconciliation service.
- **settlement**: Settlement workflow events (workflow_started, workflow_completed, payout_executed). Consumed by settlement orchestrator and monitoring.
- **fraud**: Fraud detection events (rule_triggered, score_calculated, decision_made). Consumed by compliance service and fraud review queue.
- **reconciliation**: Reconciliation matches and exceptions. Consumed by reconciliation service and audit trail.
- **compliance**: KYC decisions, AML alerts, SAR events. Consumed by compliance dashboard and audit.

**Guarantees:**

- At-least-once delivery: Consumers must be idempotent (deduplicate via transaction ID).
- Ordered within partition: Transactions for the same account are processed sequentially.
- Retention: 30 days for operational topics, 7 years for compliance topics (via archival to S3).

### 2.4 Settlement Orchestration (Temporal)

Long-running settlement workflows with built-in durability, retry logic, and checkpointing. Temporal ensures settlement batches complete reliably even through service crashes, PSP outages, or network failures.

**Temporal Workflow: `SettlementBatchWorkflow`**

```
Triggered by: Nightly cron (6 PM ET) or on-demand via API
    │
    ▼
┌─────────────────────────────────┐
│  Activity 1: Collect Settled Txns│
│                                  │
│  Query ledger for transactions    │
│  in SETTLED state since last      │
│  settlement batch                 │
│  Returns: list of (txn_id,        │
│  amount, destination)             │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│  Activity 2: Calculate Splits    │
│                                  │
│  For each transaction, apply     │
│  fee rules and holdback logic    │
│  Returns: settlement instructions │
│  {dest, amount, method}          │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│  Activity 3: Create NACHA/Files  │
│                                  │
│  Generate ACH batch file,        │
│  bank transfer instructions,     │
│  PSP payout batch                │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│  Activity 4: Submit to PSP       │
│  (with retries and heartbeat)    │
│                                  │
│  Call payment service to         │
│  execute all settlement          │
│  instructions                    │
│                                  │
│  Retry: exponential backoff,     │
│  max 5 attempts over 24 hours    │
│  Timeout: 4 hours per attempt    │
│                                  │
│  Heartbeat: every 60s to detect  │
│  stuck activities                │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│  Activity 5: Wait for Confirmation│
│                                  │
│  Poll PSP settlement status      │
│  Or wait for webhook callback    │
│  Timeout: 48 hours              │
│                                  │
│  If timeout: emit alert, manual  │
│  review required                 │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│  Activity 6: Post Ledger Entries │
│                                  │
│  For confirmed settlements:      │
│  DR platform_payable             │
│  CR bank_operating               │
│                                  │
│  Update batch status: CONFIRMED  │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│  Emit Event: settlement.completed │
│                                  │
│  Publish to Kafka topic for      │
│  downstream consumers            │
│  (reconciliation, audit, notify) │
└─────────────────────────────────┘
```

**Why Temporal over Celery for settlement:**

- **Durability**: Workflow state survives worker crashes. If a worker dies during Activity 4 (PSP submission), Temporal resumes from exactly where it left off, not from the start.
- **Visibility**: Query workflow status in real-time. Stakeholders can see "settlement batch 2025-03-08-001 is waiting for PSP confirmation (4.2 hours)" without logs.
- **Checkpointing**: Each activity completes atomically. If PSP responds with "payment submitted but I can't confirm immediately," Temporal stores that state and retries the confirmation activity without re-submitting.
- **Timeout handling**: Temporal's timeout semantics are precise. "Wait for PSP confirmation with 48-hour timeout" is declarative code, not a cron job that might miss the deadline.

### 2.5 Settlement Service

Calculates who owes whom, batches transactions for efficiency, and generates settlement instructions for the payment service to execute. Runs as a Temporal workflow consumer.

```
Settlement Pipeline (runs on schedule or triggered by events)
      │
      ▼
┌─────────────────────────────────┐
│  Collect Settled Transactions    │
│                                  │
│  Query ledger for transactions   │
│  in state SETTLED that have not  │
│  been included in a settlement   │
│  batch yet                       │
│                                  │
│  Group by:                       │
│  - Settlement window (daily)     │
│  - Destination (employer, user,  │
│    service provider)             │
│  - Payment method                │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│  Calculate Splits                │
│                                  │
│  For each transaction:           │
│                                  │
│  Example: $200 earned wage       │
│  access transfer                 │
│                                  │
│  Employer funded: $200.00        │
│  Platform fee (1.5%): $3.00      │
│  PSP processing (2.9%+30c):     │
│    $6.10                         │
│  User receives: $190.90          │
│                                  │
│  Split rules from config:        │
│  - employer_plan_type            │
│  - fee_structure (% or flat)     │
│  - who_absorbs_psp_fee           │
│    (platform or user)            │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│  Net Settlement Batching         │
│                                  │
│  Instead of 500 individual       │
│  payouts to users, calculate     │
│  net position:                   │
│                                  │
│  User A: 3 transfers in, 1 fee  │
│    net: $567.20                  │
│  User B: 1 transfer in          │
│    net: $190.90                  │
│  Platform: 500 fees collected    │
│    net: $1,500.00                │
│                                  │
│  Result: 501 payout instructions │
│  instead of 1,500+ individual    │
│  movements                       │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│  Apply Holdbacks                 │
│                                  │
│  For high-risk transaction       │
│  types, hold 5% in reserve:     │
│                                  │
│  User A net: $567.20             │
│  Holdback (5%): $28.36           │
│  Payout: $538.84                 │
│  Holdback released: 30 days     │
│                                  │
│  Holdback journal entry:         │
│  DR user_payout      $28.36     │
│  CR chargeback_reserve $28.36   │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│  Generate Settlement Files       │
│                                  │
│  ACH payouts: NACHA file         │
│  Card payouts: PSP API batch     │
│  Wire payouts: Bank API          │
│                                  │
│  Each instruction includes:      │
│  - Destination account           │
│  - Amount                        │
│  - Idempotency key               │
│  - Reference (batch_id + seq)    │
│                                  │
│  Submit to payment service       │
│  for execution                   │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│  Post Settlement Ledger Entries  │
│                                  │
│  For each executed payout:       │
│  DR platform_payable             │
│  CR bank_operating               │
│                                  │
│  Settlement batch status:        │
│  CREATED -> SUBMITTED ->         │
│  CONFIRMED -> RECONCILED         │
└─────────────────────────────────┘
```

### 2.6 Fraud Service

Synchronous rule evaluation in the transaction path (< 100ms) plus async analysis for borderline cases. The fraud service never blocks or modifies a ledger entry directly. It returns a decision (approve, review, decline) to the API gateway, which then decides whether to proceed with the ledger write.

```
Transaction Context
      │
      ▼
┌─────────────────────────────────┐
│  Feature Extraction (< 10ms)     │
│                                  │
│  From transaction:               │
│  - amount, method, currency      │
│  - timestamp, day_of_week        │
│                                  │
│  From user profile (Redis):      │
│  - account_age_days              │
│  - avg_txn_amount_30d            │
│  - txn_count_24h                 │
│  - txn_count_1h                  │
│  - total_amount_24h              │
│  - kyc_tier                      │
│  - previous_fraud_flags          │
│                                  │
│  From device/session:            │
│  - device_id (known or new)      │
│  - ip_geolocation                │
│  - session_age                   │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│  Blocklist / Allowlist (< 5ms)   │
│                                  │
│  Redis SET lookup:               │
│  SISMEMBER fraud:blocklist       │
│    {user_id}                     │
│  -> HIT: score = 100, DECLINE    │
│                                  │
│  SISMEMBER fraud:allowlist       │
│    {user_id}                     │
│  -> HIT: score = 0, APPROVE     │
│     (bypass rules)               │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│  Rule Engine (< 50ms)            │
│                                  │
│  Rules loaded from Redis         │
│  (refreshed every 5 min from DB) │
│                                  │
│  Each rule: condition -> points  │
│                                  │
│  Rules are evaluated in parallel │
│  (not short-circuit) because     │
│  the total score matters, not    │
│  just the first trigger          │
│                                  │
│  Score = SUM(triggered rules)    │
│  Max possible: ~200              │
│  Normalized to 0-100 range      │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│  Decision + Logging              │
│                                  │
│  score < 30:  APPROVE            │
│  score 30-70: REVIEW             │
│  score > 70:  DECLINE            │
│                                  │
│  Log (immutable, append-only):   │
│  {                               │
│    txn_id, user_id, timestamp,   │
│    features_snapshot,            │
│    rules_triggered: [...],       │
│    score, decision,              │
│    model_version: "rules_v3"     │
│  }                               │
│                                  │
│  This log is the training data   │
│  for the future ML model (v2)   │
└─────────────────────────────────┘
```

**Why rules before ML:** We need fraud detection on day one. An ML model needs labeled training data (confirmed fraud vs. legitimate transactions). The rule engine generates labels and collects features. After 6+ months of data collection, we can train a model that uses the same features but learns non-obvious patterns that rules miss. The rule engine stays as a fast pre-filter even after ML is deployed.

### 2.7 Reconciliation Service

Runs as a nightly batch job. Compares three data sources to verify that all money is accounted for.

```
Nightly Trigger (2:00 AM ET)
      │
      ▼
┌─────────────────────────────────┐
│  Data Collection                 │
│                                  │
│  Source 1: Internal ledger       │
│  - All journal entries for       │
│    the reconciliation window     │
│  - Grouped by external_ref       │
│    (PSP transaction ID)          │
│                                  │
│  Source 2: PSP settlement report │
│  - Stripe: download via API      │
│  - Adyen: download settlement    │
│    detail report                 │
│  - Tabapay: SFTP file pickup     │
│                                  │
│  Source 3: Bank statement         │
│  - BAI2 file from bank partner   │
│  - Parsed into structured        │
│    records (date, amount, ref)   │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│  Phase 1: Ledger <> PSP          │
│                                  │
│  Match strategy (in order):      │
│                                  │
│  1. Exact match: PSP txn ID      │
│     matches ledger external_ref  │
│     AND amount matches           │
│     -> ~92% matched              │
│                                  │
│  2. Fuzzy: same amount, date     │
│     within +/- 1 day, same       │
│     user account                 │
│     -> ~5% more matched          │
│                                  │
│  3. Unmatched: flag as exception │
│     -> ~3% exceptions            │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│  Phase 2: PSP <> Bank            │
│                                  │
│  PSP settles in batches:         │
│  50-500 transactions netted into │
│  a single bank transfer          │
│                                  │
│  Match: PSP batch settlement     │
│  amount == bank transfer amount  │
│  (within $0.01 tolerance for     │
│  rounding)                       │
│                                  │
│  Many-to-one matching:           │
│  SUM(PSP individual txns in      │
│  batch) == bank transfer amount  │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│  Phase 3: Auto-Resolution        │
│                                  │
│  Pattern: timing difference      │
│  PSP shows T, bank shows T+1    │
│  -> Auto-resolve if amount       │
│     matches exactly              │
│                                  │
│  Pattern: batch netting          │
│  50 individual PSP txns = 1      │
│  bank transfer                   │
│  -> Auto-resolve if SUM matches  │
│                                  │
│  Pattern: fee deduction          │
│  PSP nets their fee from         │
│  settlement (known amount)       │
│  -> Auto-resolve if delta ==     │
│     expected PSP fee             │
│                                  │
│  Pattern: FX rounding            │
│  Delta <= $0.01 per transaction  │
│  -> Auto-resolve                 │
│                                  │
│  Dollar threshold: never auto-   │
│  resolve if delta > $5.00        │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│  Output                          │
│                                  │
│  Reconciliation run record:      │
│  - run_id, date_range, runtime   │
│  - total_transactions            │
│  - matched_count, matched_%      │
│  - auto_resolved_count           │
│  - exception_count               │
│  - exceptions by category        │
│                                  │
│  Exceptions queued for Diana     │
│  with full context:              │
│  - ledger entry details          │
│  - PSP record (if found)         │
│  - bank record (if found)        │
│  - suggested resolution          │
│  - similar past exceptions       │
└─────────────────────────────────┘
```

### 2.8 Compliance Service

Runs both synchronously (OFAC screening before outbound transfers) and asynchronously (transaction monitoring, KYC workflows).

```
┌──────────────────────────────────────────────────────────────┐
│                    COMPLIANCE DOMAINS                          │
│                                                               │
│  ┌─────────────────┐  ┌──────────────────┐  ┌─────────────┐ │
│  │ KYC Verification│  │ Transaction      │  │ Sanctions   │ │
│  │                 │  │ Monitoring       │  │ Screening   │ │
│  │ Progressive     │  │                  │  │             │ │
│  │ tiers:          │  │ Async rules      │  │ Synchronous │ │
│  │                 │  │ engine:          │  │ check on    │ │
│  │ Basic:          │  │                  │  │ every       │ │
│  │  email + phone  │  │ Aggregation:     │  │ outbound    │ │
│  │  + employer     │  │  > $5K in 30d    │  │ transfer    │ │
│  │  $500/mo limit  │  │                  │  │             │ │
│  │                 │  │ Structuring:     │  │ OFAC SDN    │ │
│  │ Standard:       │  │  multiple txns   │  │ list cached │ │
│  │  + gov ID       │  │  just below      │  │ locally     │ │
│  │  + database     │  │  reporting       │  │ (daily      │ │
│  │  $10K/mo limit  │  │  threshold       │  │ refresh)    │ │
│  │                 │  │                  │  │             │ │
│  │ Enhanced:       │  │ Rapid movement:  │  │ Fuzzy name  │ │
│  │  + doc verify   │  │  fund + withdraw │  │ matching    │ │
│  │  + manual       │  │  within 24hrs    │  │ (Jaro-      │ │
│  │  $25K/mo limit  │  │                  │  │ Winkler)    │ │
│  │                 │  │ Geographic:      │  │             │ │
│  │ Re-verify every │  │  high-risk       │  │ Match ->    │ │
│  │ 12 months       │  │  jurisdictions   │  │ BLOCK +     │ │
│  │                 │  │                  │  │ alert       │ │
│  └─────────────────┘  │ Alert -> review  │  │ compliance  │ │
│                       │ -> SAR or dismiss│  │             │ │
│                       └──────────────────┘  └─────────────┘ │
│                                                               │
│  Audit Trail (append-only, separate from app DB):             │
│  ├── Every KYC decision with evidence                        │
│  ├── Every monitoring alert with resolution                  │
│  ├── Every sanctions screening result                        │
│  ├── Every SAR filing with supporting documentation          │
│  └── 7-year retention, tamper-resistant                      │
└──────────────────────────────────────────────────────────────┘
```

---

## 3. Data Architecture

### 3.1 Core Schema

```sql
-- Accounts (chart of accounts)
CREATE TABLE accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_type TEXT NOT NULL CHECK (account_type IN (
        'asset', 'liability', 'revenue', 'expense'
    )),
    account_code TEXT UNIQUE NOT NULL,     -- 'user_wallet', 'platform_fee', etc.
    parent_account_id UUID REFERENCES accounts(id),
    entity_id UUID,                        -- user_id, employer_id, or NULL for platform accounts
    entity_type TEXT,                       -- 'user', 'employer', 'platform'
    currency TEXT DEFAULT 'USD',
    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'frozen', 'closed')),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Journal entries (header)
CREATE TABLE journal_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key TEXT UNIQUE NOT NULL,
    entry_type TEXT NOT NULL,              -- 'funding', 'transfer', 'fee', 'refund',
                                           -- 'settlement', 'chargeback', 'adjustment'
    description TEXT NOT NULL,
    reference_type TEXT,                   -- 'transaction', 'settlement_batch', 'manual'
    reference_id UUID,                     -- links to transactions.id or settlement_batches.id
    initiated_by UUID,                     -- user_id or system identifier
    posted_at TIMESTAMPTZ DEFAULT now(),
    metadata JSONB DEFAULT '{}'
);

-- Journal entry lines (the actual debits and credits)
CREATE TABLE journal_entry_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    journal_entry_id UUID REFERENCES journal_entries(id) NOT NULL,
    account_id UUID REFERENCES accounts(id) NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('debit', 'credit')),
    amount NUMERIC(19, 4) NOT NULL CHECK (amount > 0),
    currency TEXT DEFAULT 'USD',
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Double-entry constraint (enforced at application layer, verified by DB)
-- A trigger verifies SUM(debits) == SUM(credits) per journal_entry_id on INSERT

-- Holds (pending authorization)
CREATE TABLE holds (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID REFERENCES accounts(id) NOT NULL,
    amount NUMERIC(19, 4) NOT NULL CHECK (amount > 0),
    journal_entry_id UUID REFERENCES journal_entries(id),
    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'captured', 'voided', 'expired')),
    expires_at TIMESTAMPTZ NOT NULL,
    captured_at TIMESTAMPTZ,
    voided_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Transactions (user-facing state machine)
CREATE TABLE transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    transaction_type TEXT NOT NULL CHECK (transaction_type IN (
        'earned_wage_access', 'transfer', 'bill_pay', 'funding', 'refund'
    )),
    amount NUMERIC(19, 4) NOT NULL,
    currency TEXT DEFAULT 'USD',
    source_account_id UUID REFERENCES accounts(id),
    destination_account_id UUID REFERENCES accounts(id),
    status TEXT NOT NULL CHECK (status IN (
        'initiated', 'pending', 'processing', 'settled',
        'failed', 'declined', 'in_review',
        'dispute_opened', 'dispute_resolved', 'dispute_denied',
        'refund_initiated', 'refunded'
    )),
    idempotency_key TEXT UNIQUE NOT NULL,
    journal_entry_id UUID REFERENCES journal_entries(id),
    psp_transaction_id TEXT,               -- external PSP reference
    psp_name TEXT,                          -- 'stripe', 'adyen', 'tabapay'
    payment_method TEXT,                    -- 'ach', 'card', 'wire', 'instant'
    fraud_score INTEGER,
    fraud_decision TEXT,
    error_code TEXT,
    error_message TEXT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Transaction events (immutable event history)
CREATE TABLE transaction_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id UUID REFERENCES transactions(id) NOT NULL,
    event_type TEXT NOT NULL,               -- 'created', 'fraud_checked', 'psp_submitted',
                                            -- 'psp_confirmed', 'settled', 'failed', etc.
    from_status TEXT,
    to_status TEXT,
    details JSONB NOT NULL,                 -- event-specific payload
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Fraud decisions (immutable log)
CREATE TABLE fraud_decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id UUID REFERENCES transactions(id) NOT NULL,
    user_id UUID NOT NULL,
    features JSONB NOT NULL,                -- snapshot of all features at decision time
    rules_triggered JSONB NOT NULL,         -- [{rule_id, rule_name, points, details}]
    score INTEGER NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('approve', 'review', 'decline')),
    model_version TEXT NOT NULL,            -- 'rules_v3', 'ml_v1', etc.
    latency_ms INTEGER,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Reconciliation
CREATE TABLE reconciliation_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date_range_start DATE NOT NULL,
    date_range_end DATE NOT NULL,
    total_transactions INTEGER,
    matched_count INTEGER,
    auto_resolved_count INTEGER,
    exception_count INTEGER,
    match_rate NUMERIC(5, 2),
    status TEXT CHECK (status IN ('running', 'completed', 'failed')),
    started_at TIMESTAMPTZ DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE reconciliation_exceptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recon_run_id UUID REFERENCES reconciliation_runs(id) NOT NULL,
    transaction_id UUID REFERENCES transactions(id),
    exception_type TEXT NOT NULL,            -- 'missing_psp', 'missing_bank',
                                             -- 'amount_mismatch', 'status_mismatch'
    ledger_amount NUMERIC(19, 4),
    psp_amount NUMERIC(19, 4),
    bank_amount NUMERIC(19, 4),
    delta NUMERIC(19, 4),
    resolution TEXT CHECK (resolution IN (
        'pending', 'auto_resolved', 'manually_resolved', 'written_off'
    )) DEFAULT 'pending',
    resolution_notes TEXT,
    resolved_by UUID,
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- KYC Verification
CREATE TABLE kyc_verifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    tier TEXT NOT NULL CHECK (tier IN ('basic', 'standard', 'enhanced')),
    status TEXT NOT NULL CHECK (status IN (
        'pending', 'approved', 'rejected', 'expired'
    )),
    verification_method TEXT,               -- 'employer_match', 'id_document', 'database_check'
    vendor_reference TEXT,                   -- Alloy/Persona case ID
    evidence JSONB,                          -- what was checked, results
    expires_at TIMESTAMPTZ,                  -- 12-month re-verification
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Compliance events (separate audit trail, append-only)
CREATE TABLE compliance_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type TEXT NOT NULL,               -- 'kyc_decision', 'monitoring_alert',
                                            -- 'sanctions_screen', 'sar_filed'
    user_id UUID,
    transaction_id UUID,
    details JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Settlement batches
CREATE TABLE settlement_batches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    settlement_date DATE NOT NULL,
    transaction_count INTEGER NOT NULL,
    gross_amount NUMERIC(19, 4) NOT NULL,
    platform_fees NUMERIC(19, 4) NOT NULL,
    psp_fees NUMERIC(19, 4) NOT NULL,
    net_amount NUMERIC(19, 4) NOT NULL,
    holdback_amount NUMERIC(19, 4) DEFAULT 0,
    status TEXT CHECK (status IN (
        'created', 'submitted', 'confirmed', 'reconciled'
    )),
    journal_entry_id UUID REFERENCES journal_entries(id),
    created_at TIMESTAMPTZ DEFAULT now()
);
```

### 3.2 Indexes

```sql
-- Ledger performance
CREATE INDEX idx_jel_account ON journal_entry_lines(account_id);
CREATE INDEX idx_jel_journal ON journal_entry_lines(journal_entry_id);
CREATE INDEX idx_je_idempotency ON journal_entries(idempotency_key);
CREATE INDEX idx_je_reference ON journal_entries(reference_type, reference_id);
CREATE INDEX idx_holds_account_active ON holds(account_id) WHERE status = 'active';

-- Transaction lookups
CREATE INDEX idx_txn_user ON transactions(user_id, created_at DESC);
CREATE INDEX idx_txn_status ON transactions(status);
CREATE INDEX idx_txn_psp ON transactions(psp_transaction_id);
CREATE INDEX idx_txn_idempotency ON transactions(idempotency_key);
CREATE INDEX idx_txn_events ON transaction_events(transaction_id, created_at);

-- Fraud
CREATE INDEX idx_fraud_user ON fraud_decisions(user_id, created_at DESC);
CREATE INDEX idx_fraud_decision ON fraud_decisions(decision);

-- Reconciliation
CREATE INDEX idx_recon_exceptions_status ON reconciliation_exceptions(resolution)
    WHERE resolution = 'pending';

-- Compliance
CREATE INDEX idx_kyc_user ON kyc_verifications(user_id, created_at DESC);
CREATE INDEX idx_compliance_events_type ON compliance_events(event_type, created_at DESC);
CREATE INDEX idx_compliance_events_user ON compliance_events(user_id, created_at DESC);
```

### 3.3 Entity Relationship Diagram

```
┌────────────┐
│  accounts  │───1:N───┌──────────────────┐
└────────────┘         │ journal_entry_    │
                       │ lines             │
┌────────────┐         └────────┬─────────┘
│ journal_   │───1:N────────────┘
│ entries    │
└─────┬──────┘
      │
     1:1
      │
┌─────┴──────┐       ┌──────────────────┐
│transactions│───1:N──│ transaction_     │
│            │        │ events           │
│            │        └──────────────────┘
│            │
│            │───1:1──┌──────────────────┐
│            │        │ fraud_decisions  │
└─────┬──────┘        └──────────────────┘
      │
     N:1
      │
┌─────┴──────────┐    ┌──────────────────┐
│ settlement_    │    │ reconciliation_  │
│ batches        │    │ runs             │───1:N──┌────────────────┐
└────────────────┘    └──────────────────┘        │ reconciliation_│
                                                   │ exceptions     │
                                                   └────────────────┘

┌────────────────┐    ┌──────────────────┐
│ kyc_           │    │ compliance_      │
│ verifications  │    │ events           │
└────────────────┘    └──────────────────┘
(both linked to user_id, not FK to a users table in this service)
```

---

## 4. Infrastructure

### 4.1 Deployment Architecture

```
┌─────────────────────────────────────────────────┐
│                   Vercel                          │
│                                                   │
│  Next.js Web Dashboards                           │
│  - Finance ops, compliance, support, employer     │
│  - SSR for data-heavy reconciliation tables       │
│  - CDN for static assets                          │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────┴──────────────────────────┐
│  React Native (Expo)                              │
│  - User mobile app                                │
│  - Balance, transfers, history, disputes           │
│  - Push notifications via FCM                      │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│              Railway / Render                     │
│                                                   │
│  FastAPI Backend                                  │
│  ├── API server (uvicorn, 3+ replicas)            │
│  ├── Celery workers                               │
│  │   ├── settlement_worker (daily batch)          │
│  │   ├── reconciliation_worker (nightly)          │
│  │   ├── compliance_worker (monitoring rules)     │
│  │   └── notification_worker (push/SMS/email)     │
│  └── Celery Beat (scheduler for recurring jobs)   │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│                  Data Stores                      │
│                                                   │
│  PostgreSQL 15 (RDS or Supabase)                  │
│  ├── Ledger tables (SERIALIZABLE isolation)       │
│  ├── Transaction + event history                  │
│  ├── Compliance event log                         │
│  └── Connection pooling: PgBouncer (100 conns)    │
│                                                   │
│  Redis 7                                          │
│  ├── Idempotency key store (TTL: 7 days)          │
│  ├── Fraud rule cache (refresh: 5 min)            │
│  ├── PSP health scores (updated per request)      │
│  ├── Circuit breaker state                        │
│  ├── Rate limiting (per user, per IP)             │
│  ├── Celery task queue                            │
│  └── Redis Streams (event bus)                    │
└─────────────────────────────────────────────────┘
```

### 4.2 Event-Driven Communication (Apache Kafka)

Services communicate through Kafka topics for anything that doesn't need a synchronous response. This decouples services and creates a natural audit trail. See section 2.3 for detailed Kafka architecture.

```
┌──────────────┐                ┌──────────────────────────────────────┐
│ Ledger       │                │      Apache Kafka Cluster             │
│ Service      │──publishes──>  │                                       │
│              │                │  Topic: transactions                   │
└──────────────┘                │  ├── Partition 0 (account_id % 10==0) │
                                │  ├── Partition 1 (account_id % 10==1) │
┌──────────────┐                │  ├── ...                              │
│ Payment      │──publishes──>  │  └── Partition 9 (account_id % 10==9) │
│ Service      │                │                                       │
└──────────────┘                │  Topic: settlement                     │
                                │  ├── Partition 0-2 (load balanced)    │
┌──────────────┐                │                                       │
│ Fraud        │──publishes──>  │  Topic: fraud                         │
│ Service      │                │  ├── Partition 0-1 (fraud consumers)  │
└──────────────┘                │                                       │
                                │  Topic: compliance                     │
┌──────────────┐                │  ├── Partition 0-1 (7-year retention) │
│ Compliance   │──publishes──>  │                                       │
│ Service      │                │  Consumers:                           │
└──────────────┘                │  ├── Settlement Orchestrator (Temporal)│
                                │  ├── Fraud review queue (async)       │
                                │  ├── Compliance monitoring (real-time) │
                                │  ├── Notification service             │
                                │  ├── Analytics/reporting              │
                                │  └── Archive to S3 (compliance)       │
                                └──────────────────────────────────────┘
```

**Kafka guarantees:**
- At-least-once delivery: Consumers must be idempotent (deduplicate via transaction ID + idempotency key)
- Ordered within partition: All transactions for account X are processed sequentially
- Durable: 7-year retention for compliance topics; 30-day retention for operational topics

---

## 5. Security Architecture

### 5.1 Data Classification

| Classification | Examples | Storage | Access |
|---|---|---|---|
| **Critical** | Bank account numbers, SSNs, card tokens | Encrypted at rest (AES-256), tokenized where possible | Compliance + engineering leads only |
| **Sensitive** | Transaction amounts, balances, KYC documents | Encrypted at rest | Authorized service accounts + finance ops |
| **Internal** | Fraud scores, rule configurations, PSP credentials | Encrypted at rest, secrets in Vault/AWS Secrets Manager | Service accounts only |
| **Operational** | Reconciliation reports, settlement batches | Standard encryption | Finance ops, compliance |

### 5.2 PCI Scope Minimization

```
User enters card details
         │
         ▼
┌────────────────────────┐
│  PSP-hosted payment     │
│  form (Stripe Elements  │
│  or Adyen Drop-in)      │
│                         │
│  Card number never      │
│  touches our servers    │
│                         │
│  Returns: payment       │
│  method token           │
│  (tok_xxxxxxxxxxxx)     │
└───────────┬─────────────┘
            │
            ▼
┌────────────────────────┐
│  Our API receives       │
│  ONLY the token         │
│                         │
│  Token stored in        │
│  transactions table     │
│                         │
│  We never see, store,   │
│  or transmit raw card   │
│  numbers                │
│                         │
│  PCI scope: SAQ-A       │
│  (minimal, questionnaire│
│  only, no on-site audit)│
└────────────────────────┘
```

### 5.3 Access Control

```
┌─────────────────────────────────────────────────────────────┐
│                       RBAC Matrix                            │
│                                                              │
│  Role              │ Ledger │ Fraud │ Recon │ Compliance    │
│  ──────────────────┼────────┼───────┼───────┼──────────     │
│  End User          │ Own    │ --    │ --    │ Own KYC       │
│  Employer Admin    │ Org    │ --    │ --    │ --            │
│  Support Agent     │ Read   │ Read  │ --    │ Read          │
│  Finance Ops       │ Read   │ Read  │ Full  │ Read          │
│  Fraud Analyst     │ Read   │ Full  │ Read  │ Read          │
│  Compliance Officer│ Read   │ Read  │ Read  │ Full          │
│  Engineering       │ Read   │ Read  │ Read  │ Read          │
│  Admin             │ Full   │ Full  │ Full  │ Full          │
│                                                              │
│  "Own" = user's own records only                            │
│  "Org" = employer's employees only                          │
│  "Read" = view, no modify                                   │
│  "Full" = view + modify + configure                         │
└─────────────────────────────────────────────────────────────┘
```

---

## 6. Performance Considerations

### 6.1 Latency Budget

Every user-initiated transaction must complete the synchronous portion (fraud check + ledger write + response) within 500ms. Here's the budget:

| Step | Target | Notes |
|---|---|---|
| API gateway (auth, validation, rate limit) | 20ms | Redis lookups for auth token and rate limit |
| Idempotency check | 5ms | Redis GET |
| Fraud feature extraction | 10ms | Redis GETs for user profile, device history |
| Fraud rule evaluation | 50ms | In-memory rule engine, all rules evaluated in parallel |
| Balance check | 30ms | Materialized view query |
| Ledger write (journal entry + hold) | 80ms | PostgreSQL with SERIALIZABLE isolation |
| Response serialization + network | 20ms | JSON response to client |
| **Total synchronous path** | **215ms** | Well within 500ms budget |
| PSP execution (async) | 1-3 seconds | Does not block user response |

### 6.2 Scaling Strategy

| Scale Point | Transactions/Day | Strategy |
|---|---|---|
| Current | ~5,000 | Single API server, 2 Celery workers |
| 6 months | ~15,000 | 3 API replicas, dedicated fraud + settlement workers |
| 12 months | ~50,000 | Read replicas for balance queries, partitioned ledger tables by month |
| 24 months | ~200,000 | Sharded ledger by entity_id, dedicated reconciliation database |

### 6.3 Caching Strategy

| Cache Target | TTL | Invalidation |
|---|---|---|
| User balance (materialized view) | 30 seconds | Refresh on schedule (not per-transaction) |
| Fraud rules | 5 minutes | Refresh from DB; manual flush on rule update |
| PSP health scores | Per-request update | Rolling window, no explicit invalidation |
| Circuit breaker state | 30-60 seconds | TTL-based (auto-close after cooldown) |
| OFAC SDN list | 24 hours | Daily download from Treasury |
| Idempotency keys | 7 days | TTL-based expiry |
| KYC verification status | 1 hour | Invalidate on new verification event |

---

## 7. Monitoring and Observability

### 7.1 Key Metrics and Alerts

| Category | Metric | Alert Threshold |
|---|---|---|
| **Transactions** | Success rate (5-minute rolling) | < 95% |
| **Transactions** | Processing latency (p95) | > 500ms (sync path) |
| **Transactions** | Failed transaction rate | > 5% in 15 minutes |
| **Ledger** | Balance discrepancy detected | Any (immediate page) |
| **Ledger** | Double-entry violation attempt | Any (immediate page) |
| **Fraud** | Rule evaluation latency (p95) | > 100ms |
| **Fraud** | Decline rate | > 10% (possible rule misconfiguration) |
| **Fraud** | Review queue depth | > 50 (analyst falling behind) |
| **PSP** | Primary PSP error rate | > 3% (trigger failover evaluation) |
| **PSP** | Circuit breaker OPEN events | Any (notify on-call) |
| **Reconciliation** | Match rate | < 97% |
| **Reconciliation** | Exception count | > 20 per run |
| **Compliance** | Unreviewed alerts age | > 48 hours |
| **Database** | Connection pool utilization | > 80% |
| **Database** | Ledger table size | > 80% of partition capacity |

### 7.2 Financial Health Dashboard

The finance ops dashboard shows real-time financial health. These aren't vanity metrics; Diana uses these daily.

| Panel | What It Shows | Refresh |
|---|---|---|
| Money in transit | SUM(holds where status = 'active') | Real-time |
| Today's transaction volume | Count + sum of today's transactions by status | 1 minute |
| PSP health | Current health score + circuit breaker state for each PSP | Real-time |
| Reconciliation status | Last run's match rate + exception count + trend | After each run |
| Fraud decisions | Today's approve/review/decline split + review queue depth | 5 minutes |
| Settlement pipeline | Today's batch status (created, submitted, confirmed) | 5 minutes |
| Chargeback rate | Rolling 90-day rate by card network | Daily |

---

## 8. Monitoring and Observability (Datadog)

Unified observability across logs, metrics, traces, and APM. Datadog is critical for regulated environments because it provides audit trails and compliance-ready dashboards.

### 8.1 Infrastructure Monitoring

**Host Metrics:**
- CPU, memory, disk utilization on all servers
- Network throughput and packet loss
- Container metrics (ECS/Kubernetes) if using orchestration

**Database Monitoring:**
- Query latency (p50, p95, p99)
- Slow query log analysis
- Lock wait times and deadlock detection (critical for SERIALIZABLE isolation)
- Connection pool utilization
- Replication lag (for Multi-AZ PostgreSQL)

**Application Metrics (Python/FastAPI):**
- Request latency by endpoint (p50, p95, p99)
- Request count by status code (200, 4xx, 5xx)
- Error rate and error types
- Worker process health (Celery, Temporal workers)
- Memory leaks (via heap profiling)

### 8.2 Business Metrics (Custom Metrics)

Sent via Datadog's StatsD client or APM instrumentalization:

```python
# In ledger_engine.py
statsd.gauge('fintech.ledger.active_holds', total_holds)
statsd.gauge('fintech.ledger.posted_balance', user_balance)

# In payment_orchestrator.py
statsd.increment('fintech.payment.psp_attempt', tags=[f'psp:{psp_name}'])
statsd.timing('fintech.payment.execution_time', elapsed_ms, tags=[f'psp:{psp_name}'])

# In fraud_detector.py
statsd.increment('fintech.fraud.rule_triggered', tags=[f'rule:{rule_name}'])
statsd.gauge('fintech.fraud.review_queue_depth', review_queue_count)

# In settlement_engine.py
statsd.gauge('fintech.settlement.daily_volume', settlement_amount)
statsd.increment('fintech.settlement.batch_created', tags=[f'status:{batch_status}'])

# In reconciliation_engine.py
statsd.gauge('fintech.recon.match_rate_percent', match_rate * 100)
statsd.gauge('fintech.recon.exception_count', exception_count)
```

### 8.3 APM (Application Performance Monitoring)

Datadog APM traces the full request lifecycle:

```
Client Request
      │
      ▼
┌──────────────────────────────────────────┐
│ Trace ID: 0x1234abcd (generated once)    │
│ Span 1: API Gateway (FastAPI middleware) │
│  ├── Span 2: Auth verification          │
│  ├── Span 3: Rate limiting check         │
│  ├── Span 4: Idempotency lookup          │
│  └── Span 5: Fraud check                 │
│      ├── Span 5.1: Feature extraction    │
│      └── Span 5.2: Rule evaluation       │
│  └── Span 6: Ledger write                │
│      ├── Span 6.1: DB transaction start  │
│      ├── Span 6.2: Balance calculation   │
│      └── Span 6.3: Journal entry insert  │
└──────────────────────────────────────────┘
      │
      ▼
Response
```

Each span captures:
- Timing (start, end, duration)
- Service name (api, ledger, fraud, payment, etc.)
- Resource (SQL query, HTTP endpoint, function name)
- Tags (user_id, txn_id, psp_name, etc.)
- Errors (exception type, stacktrace, error message)

**Why APM matters for fintech:** When a user reports "my transfer took 8 seconds," the APM trace shows exactly where the time was spent: was it auth (likely misconfiguration), fraud check (rule took too long), or ledger write (database slow)? This beats logs by orders of magnitude.

### 8.4 Log Aggregation

All service logs stream to Datadog:

```
FastAPI app logs
└─> Datadog Agent (runs on host)
    └─> Datadog Logs API
        └─> Datadog Logs UI
```

**Log Level Strategy:**

- **DEBUG**: Feature flags, cache hits/misses, feature extraction details (disabled in prod)
- **INFO**: Transaction state transitions, fraud decisions, settlement events, API requests (enabled)
- **WARNING**: Retry attempts, circuit breaker state changes, reconciliation exceptions (enabled)
- **ERROR**: Transaction failures, database errors, PSP errors (enabled, page on-call)
- **CRITICAL**: Ledger invariant violations, double-entry check failures (enabled, immediate page)

**Structured Logging (JSON):**

```python
import logging
import json

logger = logging.getLogger(__name__)

def create_transaction(txn_id, amount, user_id):
    logger.info(json.dumps({
        "event": "transaction.created",
        "txn_id": txn_id,
        "amount": amount,
        "user_id": user_id,
        "timestamp": datetime.utcnow().isoformat(),
        "service": "api",
        "environment": os.getenv("ENVIRONMENT")
    }))
```

Structured logs enable filtering in Datadog: `service:api AND event:transaction.created AND amount:[500 TO 1000]`

### 8.5 Alerts and Incident Response

Datadog alert rules trigger PagerDuty incidents:

| Alert | Threshold | Action |
|-------|-----------|--------|
| Ledger invariant violation | Any | Page on-call immediately (SEV-1) |
| Transaction success rate < 95% | 5 minutes | Page on-call (SEV-2) |
| PSP error rate > 10% | 5 minutes | Page on-call (SEV-2) |
| Circuit breaker OPEN | Any | Notify Slack #fintech-alerts (SEV-3) |
| Reconciliation match rate < 97% | After run | Notify Diana (Finance Ops) |
| Fraud rule latency > 100ms (p95) | 15 minutes | Notify #fintech-alerts for investigation |
| Database connection pool > 80% | 10 minutes | Notify on-call (potential capacity issue) |

---

## 9. Technology Selection Summary

| Component | Choice | Alternatives Evaluated | Decision Driver |
|---|---|---|---|
| API | FastAPI | Express, Django REST | Async Python; Pydantic for financial data validation; native OpenAPI docs |
| Database | AWS RDS PostgreSQL 15 | MySQL, CockroachDB, Supabase | Multi-AZ failover; SERIALIZABLE isolation; managed backups; compliance-ready |
| Event Streaming | Apache Kafka | RabbitMQ, Redis Streams, AWS SQS | Partitioned by account_id for ordering; at-least-once delivery; long retention for audit |
| Settlement Orchestration | Temporal | Celery, n8n, AWS Step Functions | Durable workflow execution; built-in retry/timeout; checkpointing; human-in-loop capable |
| Cache/Queue | Redis 7 | RabbitMQ, Memcached | Idempotency store + fraud rule cache + rate limiting; simple operations |
| Mobile | React Native (Expo) | Flutter, native iOS/Android | Shared codebase; fast iteration; Expo push notifications |
| Web | Next.js + shadcn/ui | React SPA, Angular | SSR for data-heavy reconciliation tables; shadcn for full component control |
| Primary PSP | Stripe | Square, Braintree | Best developer experience; native ACH + card; marketplace features from prior integration |
| Fallback PSP | Adyen | Braintree, Checkout.com | Strong international coverage for future expansion; good failover API design |
| Instant Transfers | Tabapay | Visa Direct (raw), Stripe Instant | Specialized in real-time payouts; lower cost for push-to-debit |
| KYC Vendor | Alloy | Persona, Jumio | Orchestration layer (combines multiple data sources); configurable decision logic |
| Secrets | AWS Secrets Manager | HashiCorp Vault, Azure Key Vault | Managed service; no self-hosting; native IAM integration |
| Monitoring & APM | Datadog | Grafana + Prometheus, New Relic, Splunk | Unified APM + logs + metrics; compliance-ready; built-in SLA tracking; PagerDuty integration |

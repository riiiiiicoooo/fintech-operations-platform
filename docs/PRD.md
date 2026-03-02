# Product Requirements Document: Fintech Operations Platform

**Product:** Fintech Operations Platform
**Author:** Jacob George, Principal Product Manager
**Last Updated:** February 2025
**Status:** Production (v2.0)
**Stakeholders:** Finance ops, compliance/legal, engineering, employer success, end users

---

## 1. Overview

### 1.1 Problem Statement

The platform connects enterprise employers to employee financial services (earned wage access, savings tools, bill pay). As we scaled past 200 employers and 15,000 active users, the financial operations layer became the bottleneck. The founding team had built payments on top of a single Stripe integration with transaction records stored in a flat transactions table. This worked at $500K ARR. It broke at $3M ARR. The specific problems:

1. **No ledger, no source of truth.** Transaction state lived in Stripe's dashboard and a PostgreSQL table that didn't enforce double-entry. When a user disputed a charge, the finance team had to manually cross-reference Stripe, the bank account, and our database to reconstruct what happened. This took 2-3 hours per dispute and produced different answers depending on who looked.

2. **Single PSP dependency.** Everything ran through Stripe. When Stripe had a 47-minute partial outage in October 2023, we couldn't process any transactions. Users trying to access earned wages got error screens. Employers called asking if we were shutting down. We had no fallback.

3. **Manual reconciliation.** A finance ops analyst spent 15-20 hours per week in spreadsheets reconciling our transaction records against Stripe settlement reports and bank statements. Match rate was ~89%. The 11% that didn't match required manual investigation, and some were never resolved. Month-end close took 5 business days.

4. **Fraud detection was reactive.** We caught fraud after it happened, when chargebacks came in. The average fraud loss was $340 per incident, and our chargeback rate was approaching 0.8%, getting uncomfortably close to the card network threshold of 1.0%. One bad quarter and Visa could put us in their monitoring program.

5. **Compliance was a spreadsheet.** KYC verification was a manual process involving email chains with the identity verification vendor. Transaction monitoring for AML was a weekly CSV export that the compliance officer reviewed by eye. We had no automated SAR triggers and no audit trail that would survive a regulatory examination.

### 1.2 Product Vision

Build the financial operations infrastructure that lets the platform scale from $3M to $50M+ ARR without adding headcount to the finance ops team proportionally. The ledger is the foundation: a double-entry system that records every financial event before any external API call, creating a single source of truth that makes reconciliation, compliance, and reporting derivable rather than manual. On top of the ledger: multi-PSP payment orchestration for resilience, real-time fraud scoring to block bad transactions before they execute, automated settlement for multi-party fund flows, nightly reconciliation that auto-resolves known patterns, and compliance automation that keeps us ahead of regulatory requirements.

### 1.3 Success Criteria

| Metric | Target | Before | Measurement Method |
|---|---|---|---|
| Transaction success rate | > 97% | 91.2% | Successful settlements / total attempted transactions |
| Payment processing p95 latency | < 2 seconds | 4.2 seconds | PSP API call to confirmation, measured at orchestrator |
| Fraud detection rate | > 90% | ~60% (reactive) | Detected fraud / total confirmed fraud (including chargebacks caught post-hoc) |
| False positive rate | < 5% | 12% | Legitimate transactions flagged as fraud / total flagged |
| Reconciliation match rate | > 98% | 89% | Auto-matched transactions / total transactions in reconciliation window |
| Reconciliation labor | < 5 hours/week | 15-20 hours/week | Finance ops time tracking |
| Month-end close time | < 2 business days | 5 business days | Calendar days from month end to books closed |
| Chargeback rate | < 0.3% | 0.8% | Chargebacks / total card transactions (rolling 90 days) |
| KYC verification time | < 3 minutes (automated) | 24-48 hours (manual) | Time from user submission to verification decision |
| Audit trail completeness | 100% of transactions | ~70% (gaps in logging) | Transactions with complete event chain / total transactions |

---

## 2. Users and Personas

### 2.1 Primary Personas

**Finance Ops Manager (Diana)**
- Role: Head of Finance Operations, reports to CFO
- Context: Manages all money movement, reconciliation, settlement, and financial reporting for the platform
- Pain points: Spends most of her week in spreadsheets reconciling transactions instead of on strategic finance work. Month-end close is a nightmare because the data doesn't match across systems. Can't answer basic questions like "how much money is in transit right now?" without 30 minutes of manual queries
- Goals: Automated reconciliation that she trusts, real-time visibility into fund flows, clean month-end close, auditable records
- Technical comfort: Moderate. Comfortable with SQL queries and dashboards, not a developer
- Key workflow: Review daily reconciliation report -> investigate exceptions -> approve settlements -> close monthly books -> produce financial reports for CFO

**End User / Employee (Marcus)**
- Role: Warehouse worker at a large employer, uses the platform for earned wage access and bill pay
- Context: Lives paycheck to paycheck, uses earned wage access 3-4 times per month to cover bills before payday
- Pain points: When a transaction fails, he has no idea why and can't get a clear answer from support. Pending transactions show inconsistent status. One time a transfer showed as "completed" but the money never arrived in his bank account for 3 days
- Goals: Transactions that work the first time, clear status at every step, fast fund availability, easy dispute resolution
- Technical comfort: Low. Uses mobile app exclusively, expects simple and clear UX
- Key workflow: Check available balance -> initiate transfer -> track status -> receive funds -> (occasionally) dispute a transaction

**Compliance Officer (Priya)**
- Role: BSA/AML Compliance Officer, reports to General Counsel
- Context: Responsible for ensuring the platform meets Bank Secrecy Act, anti-money laundering, and state money transmitter requirements
- Pain points: Currently reviews transaction patterns from a weekly CSV export. Has no real-time visibility into suspicious activity. SAR filing process is entirely manual with no standardized triggers. Dreads the next regulatory examination because the audit trail has gaps
- Goals: Automated transaction monitoring with configurable rules, SAR workflow with proper documentation, complete audit trail, examination-ready reports
- Technical comfort: Low. Needs a dashboard, not SQL access
- Key workflow: Review daily alerts -> investigate flagged transactions -> file SARs if warranted -> produce examination reports -> update monitoring rules based on new patterns

### 2.2 Secondary Personas

**Employer Finance Admin (HR/Payroll):** Manages funding schedules, reviews employee usage reports, handles employer-side reconciliation
**Engineering Lead:** Integrates with PSPs, maintains uptime, deploys payment infrastructure changes with zero downtime
**Customer Support Agent:** Resolves user transaction issues, needs to see the full transaction lifecycle and event history to answer questions without escalating

---

## 3. User Flows

### 3.1 Core Flow: User-Initiated Transfer

```
End User (Marcus)                Platform                              External
     |                               |                                    |
     |-- Request transfer ($200)     |                                    |
     |   (earned wage -> bank acct)  |                                    |
     |                               |-- Validate idempotency key         |
     |                               |-- Check: KYC tier allows amount?   |
     |                               |-- Check: available balance >= $200?|
     |                               |-- Fraud check (< 100ms)            |
     |                               |   Score: 12 (APPROVE)              |
     |                               |-- Create journal entry:            |
     |                               |   DR user_wallet $200              |
     |                               |   CR psp_payable  $200             |
     |                               |-- Place hold on user balance       |
     |                               |-- Transaction state: PENDING       |
     |                               |                                    |
     |<- "Transfer initiated"        |                                    |
     |   (show pending in app)       |                                    |
     |                               |                                    |
     |                               |-- Select PSP (Stripe, health: 0.95)|
     |                               |-- Execute ACH transfer      ------>|
     |                               |                                    |
     |                               |   (async, 1-3 business days)       |
     |                               |                                    |
     |                               |<-- Webhook: transfer.completed ----|
     |                               |-- Update journal entry:            |
     |                               |   Release hold                     |
     |                               |   DR psp_payable $200              |
     |                               |   CR psp_settled $200              |
     |                               |-- Transaction state: SETTLED       |
     |                               |                                    |
     |<- Push notification:          |                                    |
     |   "$200 sent to your bank"    |                                    |
     |                               |                                    |
```

### 3.2 Employer Funding Flow

```
Employer Finance Admin             Platform                              External
     |                               |                                    |
     |-- Schedule funding            |                                    |
     |   ($50,000 for 200 employees) |                                    |
     |                               |-- Validate funding amount          |
     |                               |-- Create journal entry:            |
     |                               |   DR employer_receivable $50,000   |
     |                               |   CR employer_funding_hold $50,000 |
     |                               |-- Transaction state: AWAITING_FUNDS|
     |                               |                                    |
     |                               |-- Initiate ACH pull         ------>|
     |                               |                                    |
     |                               |<-- Webhook: charge.succeeded  ----|
     |                               |-- Update journal:                  |
     |                               |   DR employer_funding_hold $50,000 |
     |                               |   CR employer_funded       $50,000 |
     |                               |-- Allocate to employee wallets     |
     |                               |   (200 entries, ~$250 each)        |
     |                               |-- Transaction state: FUNDED        |
     |                               |                                    |
     |<- "Funding complete.          |                                    |
     |    200 employees funded."     |                                    |
     |                               |                                    |
```

### 3.3 Reconciliation Flow

```
Nightly Job (2:00 AM ET)          Platform                              External
     |                               |                                    |
     |-- Trigger reconciliation      |                                    |
     |                               |-- Pull: internal ledger entries    |
     |                               |   (previous business day)          |
     |                               |-- Pull: PSP settlement report ---->|
     |                               |<-- Stripe/Adyen daily report  ----|
     |                               |-- Pull: bank statement       ---->|
     |                               |<-- BAI2 file from bank       ----|
     |                               |                                    |
     |                               |-- Phase 1: Ledger <> PSP match    |
     |                               |   (by transaction ID, amount)     |
     |                               |   Matched: 97.8%                  |
     |                               |                                    |
     |                               |-- Phase 2: PSP <> Bank match      |
     |                               |   (by settlement batch, net amt)  |
     |                               |   Matched: 98.4%                  |
     |                               |                                    |
     |                               |-- Phase 3: Auto-resolve breaks    |
     |                               |   Timing differences: 14 resolved |
     |                               |   Batch netting: 8 resolved       |
     |                               |   Fee adjustments: 3 resolved     |
     |                               |   Remaining exceptions: 7         |
     |                               |                                    |
     |                               |-- Generate reconciliation report  |
     |                               |-- Queue 7 exceptions for Diana    |
     |                               |                                    |
Diana                                |                                    |
     |<- Morning email:              |                                    |
     |   "Recon complete. 99.2%      |                                    |
     |    matched. 7 exceptions."    |                                    |
     |                               |                                    |
```

### 3.4 Fraud Detection Flow

```
Transaction enters system          Platform
     |                               |
     |                               |-- Extract features:
     |                               |   amount, method, device_id,
     |                               |   ip_geo, time, user_history
     |                               |
     |                               |-- Rule engine evaluation (< 100ms):
     |                               |   Velocity: 3rd txn today (+0 pts)
     |                               |   Amount: $200, user avg $180 (+0)
     |                               |   Device: known device (+0 pts)
     |                               |   Location: usual city (+0 pts)
     |                               |   Account age: 8 months (+0 pts)
     |                               |   Total score: 0 -> APPROVE
     |                               |
     |                               |   --- vs. suspicious example ---
     |                               |   Velocity: 6th txn today (+25)
     |                               |   Amount: $950, user avg $180 (+20)
     |                               |   Device: new device (+15)
     |                               |   Location: new state (+15)
     |                               |   Time: 3:17 AM (+10)
     |                               |   Total score: 85 -> DECLINE
     |                               |
     |                               |-- Decision logged (immutable)
     |                               |-- APPROVE: continue to ledger
     |                               |-- REVIEW: hold + queue for analyst
     |                               |-- DECLINE: reject + notify user
     |                               |
```

---

## 4. Functional Requirements

### 4.1 Ledger

| ID | Requirement | Priority | Notes |
|---|---|---|---|
| LED-01 | Double-entry journal entries for all financial events | P0 | Core invariant: debits always equal credits |
| LED-02 | Account types: asset, liability, revenue, expense | P0 | Standard accounting taxonomy |
| LED-03 | Real-time balance calculation from journal entries | P0 | Available balance = posted entries minus holds |
| LED-04 | Hold/authorization management with expiration | P0 | Holds expire after 7 days if not captured or voided |
| LED-05 | Idempotency key enforcement on all entries | P0 | Duplicate submissions rejected, not double-posted |
| LED-06 | Immutable entries (corrections via reversing entries, never edits) | P0 | Audit trail depends on this |
| LED-07 | Multi-currency support with FX rate locking at entry time | P2 | Not needed for v1 (USD only) but architecture must support it |
| LED-08 | Sub-ledger accounts per user (user_wallet:user_id) | P0 | Each user has their own balance |
| LED-09 | Journal entry metadata (description, reference, initiated_by) | P0 | Context for audit trail and support queries |
| LED-10 | Balance snapshots for point-in-time reporting | P1 | Month-end balance reconstruction without replaying all entries |

### 4.2 Payment Orchestration

| ID | Requirement | Priority | Notes |
|---|---|---|---|
| PAY-01 | Multi-PSP support with configurable routing rules | P0 | Primary + fallback per payment method |
| PAY-02 | PSP health scoring based on success rate, latency, errors | P0 | Auto-route away from degraded PSPs |
| PAY-03 | Retry with exponential backoff and jitter | P0 | Max 3 attempts before failing |
| PAY-04 | Circuit breaker pattern for failing PSPs | P0 | Open circuit after 5 failures in 60 seconds |
| PAY-05 | Idempotent payment execution (idempotency keys forwarded to PSP) | P0 | Safe retries without double-charging |
| PAY-06 | Webhook ingestion and deduplication | P0 | PSPs send webhooks multiple times; process exactly once |
| PAY-07 | Transaction state machine with valid state transitions only | P0 | PENDING -> PROCESSING -> SETTLED (not PENDING -> SETTLED) |
| PAY-08 | Support ACH, card, wire, and instant transfer methods | P0 | Different methods route to different PSPs |
| PAY-09 | Payment method routing configuration (ACH -> PSP A, cards -> PSP B) | P1 | Optimize cost and success rate per method |
| PAY-10 | Fallback queue for transactions during total PSP outage | P1 | Don't lose transactions; queue and retry when PSPs recover |

### 4.3 Settlement

| ID | Requirement | Priority | Notes |
|---|---|---|---|
| SET-01 | Multi-party settlement instructions (employer, platform, user) | P0 | Every transaction involves at least 2 parties + platform fee |
| SET-02 | Split payment rules (configurable percentage or flat fee) | P0 | Platform takes 1.5% or $2.50 per transaction depending on plan |
| SET-03 | Net settlement batching (aggregate many txns into fewer transfers) | P0 | Reduces PSP fees; daily batch settlement vs. per-transaction |
| SET-04 | Holdback/reserve management for chargebacks | P1 | Hold 5% of provider payouts for 30 days as chargeback reserve |
| SET-05 | Settlement file generation (NACHA for ACH, CSV for reporting) | P1 | Bank partners require specific file formats |
| SET-06 | Settlement reconciliation against PSP settlement reports | P0 | Verify our settlement instructions match what PSP actually moved |
| SET-07 | Automated payout scheduling (T+1 for cards, T+3 for ACH) | P0 | Different methods have different settlement windows |
| SET-08 | Partial settlement for disputed transactions | P1 | Settle undisputed portion, hold disputed amount |

### 4.4 Fraud Detection

| ID | Requirement | Priority | Notes |
|---|---|---|---|
| FRD-01 | Synchronous rule-based fraud scoring (< 100ms) | P0 | Must not add perceptible latency to transaction flow |
| FRD-02 | Configurable rules: velocity, amount, geo, device, time | P0 | Rules tunable without code deployment |
| FRD-03 | Risk score aggregation with weighted rules | P0 | Composite score 0-100 from individual rule scores |
| FRD-04 | Three-tier decision: approve / review / decline | P0 | Thresholds configurable per transaction type |
| FRD-05 | Blocklist and allowlist management | P0 | Known bad actors blocked; verified users bypass low-risk rules |
| FRD-06 | Manual review queue with investigation tools | P0 | Analysts see transaction context, user history, and rule triggers |
| FRD-07 | Fraud decision logging (immutable, all inputs and outputs) | P0 | Required for model training and regulatory defense |
| FRD-08 | Feature extraction pipeline for future ML model input | P1 | Rule engine is v1; ML model is v2 but data collection starts now |
| FRD-09 | Alert escalation for high-severity patterns | P1 | Auto-notify compliance officer for patterns suggesting organized fraud |
| FRD-10 | Chargeback feedback loop (confirmed fraud updates user risk profile) | P1 | Chargebacks retroactively label transactions for rule tuning |

### 4.5 Reconciliation

| ID | Requirement | Priority | Notes |
|---|---|---|---|
| REC-01 | Three-way reconciliation: ledger, PSP records, bank statements | P0 | All three must agree for a transaction to be "clean" |
| REC-02 | Exact matching by transaction ID, amount, date, status | P0 | First pass catches ~92% |
| REC-03 | Fuzzy matching for batched settlements (many-to-one) | P0 | PSPs net 50+ transactions into one bank transfer |
| REC-04 | Auto-resolution for known break patterns | P0 | Timing, batching, rounding, fee deductions |
| REC-05 | Exception queue for unresolved breaks | P0 | Finance ops reviews and resolves manually |
| REC-06 | Reconciliation reporting with match rate and break categories | P0 | Daily report to finance ops manager |
| REC-07 | Nightly batch execution (configurable schedule) | P0 | Default: 2:00 AM ET, after all PSP settlement files available |
| REC-08 | Historical reconciliation (re-run for past dates) | P1 | Needed for month-end adjustments and audit requests |
| REC-09 | Bank statement ingestion (BAI2 format) | P1 | Standard bank reporting format |
| REC-10 | Reconciliation dashboard with trends (match rate over time) | P1 | Finance ops needs to see if data quality is improving or degrading |

### 4.6 Compliance and KYC/AML

| ID | Requirement | Priority | Notes |
|---|---|---|---|
| CMP-01 | Tiered KYC verification (basic, standard, enhanced) | P0 | Progressive verification tied to transaction limits |
| CMP-02 | Basic tier: email + phone + employer verification | P0 | Allows up to $500/month |
| CMP-03 | Standard tier: government ID + database check | P0 | Allows up to $2,500/month |
| CMP-04 | Enhanced tier: document verification + address proof | P1 | Allows up to $10,000/month |
| CMP-05 | Transaction monitoring rules (velocity, amount, pattern) | P0 | Automated detection of suspicious activity |
| CMP-06 | SAR trigger detection with configurable thresholds | P0 | Aggregation > $5K in 30 days, structuring patterns, rapid movement |
| CMP-07 | SAR workflow: trigger -> investigation -> filing or dismissal | P0 | Documented workflow with audit trail |
| CMP-08 | Sanctions screening against OFAC SDN list | P0 | Required for all money movement |
| CMP-09 | Complete audit trail for all financial events and compliance decisions | P0 | Must survive regulatory examination |
| CMP-10 | Examination-ready reporting (transaction summaries, SAR logs, KYC status) | P1 | Pre-built reports for regulatory exams |
| CMP-11 | Compliance event logging separate from application logs | P0 | Tamper-resistant, retained 7+ years |
| CMP-12 | State money transmitter license tracking and limit enforcement | P1 | Different states have different rules |

### 4.7 User-Facing Transaction Experience

| ID | Requirement | Priority | Notes |
|---|---|---|---|
| UXN-01 | Real-time transaction status (PENDING, PROCESSING, SETTLED, FAILED) | P0 | Users need to know where their money is |
| UXN-02 | Push notifications for status changes | P0 | "Your $200 transfer has been sent" |
| UXN-03 | Transaction history with search and filter | P0 | Date range, amount, status, type |
| UXN-04 | Dispute initiation from transaction detail | P1 | User taps "Something's wrong" and starts dispute flow |
| UXN-05 | Estimated arrival time based on payment method | P0 | "ACH transfers typically arrive in 1-3 business days" |
| UXN-06 | Clear error messages for failed transactions | P0 | "Transfer failed: insufficient balance" not "Error code 4012" |
| UXN-07 | Available balance reflecting holds | P0 | Balance minus pending holds, not just posted balance |

---

## 5. Non-Functional Requirements

| Category | Requirement | Target |
|---|---|---|
| **Performance** | Transaction processing (initiation to PENDING) | < 500ms (p95) |
| **Performance** | Fraud check latency | < 100ms (p95) |
| **Performance** | Balance query | < 50ms (p95) |
| **Performance** | Reconciliation run (10K transactions/day) | < 30 minutes |
| **Availability** | Payment processing uptime | 99.9% (43.8 minutes downtime/year max) |
| **Availability** | Balance queries uptime | 99.9% |
| **Scalability** | Daily transaction volume | 50,000+ (10x current) |
| **Scalability** | Active user accounts | 100,000+ |
| **Scalability** | Concurrent employer funding batches | 20+ simultaneous |
| **Security** | Data encryption | At rest (AES-256) and in transit (TLS 1.3) |
| **Security** | PCI DSS scope | Minimized via tokenization (no raw card numbers stored) |
| **Security** | Access control | RBAC with least-privilege for all financial operations |
| **Compliance** | Audit log retention | 7 years minimum |
| **Compliance** | Reconciliation data retention | 7 years minimum |
| **Compliance** | SAR filing deadline | Within 30 calendar days of detection (FinCEN requirement) |

---

## 6. Technical Constraints

### 6.1 Ledger Constraints

- All journal entries are immutable. Corrections are made via reversing entries, never by editing or deleting existing entries
- Every journal entry must balance (total debits == total credits) before it is persisted. The database should enforce this at the application layer and verify with database constraints
- Balance calculation must use ledger entries as source, never cached balances that could drift
- Idempotency keys are globally unique and permanently stored. A key used once can never be reused, even for a different transaction type

### 6.2 Payment Constraints

- All PSP API calls must include an idempotency key. If the PSP doesn't support idempotency natively, the orchestrator must implement deduplication
- Webhook processing must be idempotent. The same webhook delivered 3 times must produce the same result as delivered once
- Transaction state machine must enforce valid transitions. The system must reject any attempt to move from SETTLED back to PENDING, for example
- No money moves without a corresponding ledger entry posted first. The ledger write is the commit point, not the PSP API call

### 6.3 Compliance Constraints

- All financial events must be logged to the compliance event store within 5 seconds of occurrence
- SAR-related data must be stored separately from application data with restricted access (compliance team only)
- OFAC screening must occur before any outbound money movement, with no bypass mechanism
- KYC verification results must be cached but re-verified every 12 months or upon material change

### 6.4 Infrastructure Constraints

- Database must support ACID transactions for ledger entries (no eventual consistency for money)
- All services must be deployed with zero-downtime strategies (blue/green or rolling)
- PSP credentials must be stored in a secrets manager, never in environment variables or code
- All financial data processing must occur in US-based data centers

---

## 7. Out of Scope (v1)

- Multi-currency support (USD only for v1, architecture supports future expansion)
- Crypto payment rails
- International wire transfers
- User-to-user peer-to-peer transfers
- Credit products or lending
- Physical card issuance (virtual cards only if added)
- Real-time payments via FedNow (evaluate for v2)
- Custom employer-branded payment experience
- Partner API for embedded finance (v2+)
- ML-based fraud model (v1 uses rule engine; ML model in v2 using data collected in v1)

---

## 8. Phased Rollout

### Phase 1: Foundation (Weeks 1-8)

**Goal:** Replace the flat transactions table with a proper double-entry ledger and single PSP integration

- Double-entry ledger engine with account management
- Journal entry creation for all existing transaction types (funding, transfer, fee, refund)
- Balance calculation from ledger entries (replace cached balances)
- Idempotency enforcement on all financial operations
- Migrate existing transaction history to ledger format (backfill script)
- Basic fraud rules (velocity and amount checks only)
- Stripe integration refactored to use orchestrator pattern (single PSP, but through the abstraction layer)
- Transaction state machine with webhook processing

**Exit criteria:** All new transactions flow through ledger. Ledger balances match Stripe dashboard within $0.01 for 30 consecutive days. Zero double-posts in production.

### Phase 2: Resilience (Weeks 9-16)

**Goal:** Multi-PSP routing, automated reconciliation, and fraud detection

- Second PSP integration (Adyen or Tabapay) for fallback routing
- PSP health scoring and automatic failover
- Circuit breaker implementation
- Nightly three-way reconciliation (ledger, PSP, bank)
- Auto-resolution for known break patterns
- Reconciliation exception queue and dashboard
- Full fraud rule engine (velocity, amount, geo, device, time-of-day)
- Fraud review queue for borderline cases
- Blocklist/allowlist management

**Exit criteria:** Successful PSP failover tested in production (intentional primary degradation). Reconciliation match rate > 98%. Fraud detection catching > 85% of confirmed fraud with < 5% false positives.

### Phase 3: Compliance (Weeks 17-24)

**Goal:** Automated KYC, transaction monitoring, and audit trail

- Tiered KYC verification (basic, standard, enhanced)
- Automated identity verification integration (Alloy or Persona)
- Transaction monitoring rules engine
- SAR trigger detection and investigation workflow
- OFAC sanctions screening on all outbound transfers
- Complete compliance event logging with 7-year retention
- Examination-ready report generation
- Settlement automation with multi-party split rules
- Net settlement batching

**Exit criteria:** KYC verification automated for 95%+ of users. Transaction monitoring running with zero gaps. Compliance team confident in examination readiness.

### Phase 4: Scale (Weeks 25+)

**Goal:** Advanced features for growth and efficiency

- ML-based fraud scoring (trained on v1/v2 data)
- Real-time settlement for instant transfers
- Multi-currency ledger support
- Partner API for embedded finance
- Advanced reconciliation (predictive break detection)
- Dispute automation workflow
- Financial reporting and analytics dashboard
- FedNow integration evaluation

---

## 9. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Ledger migration introduces balance discrepancies | High | Critical | Run new ledger in parallel with existing system for 30 days; compare balances daily; only cut over when deltas are consistently < $0.01 |
| PSP failover routes to degraded secondary PSP | Medium | High | Health scoring uses multiple signals (not just uptime); test failover monthly in staging; maintain "both unhealthy" queue for total outage |
| Fraud rules too aggressive, blocking legitimate transactions | High | Medium | Start with high thresholds (low sensitivity), tune down based on data; allowlist for verified long-term users; easy manual override for support |
| Regulatory examination finds compliance gaps | Medium | Critical | Engage external compliance consultant for gap assessment before v1 launch; maintain examination-ready posture from day one, not as afterthought |
| Double-entry invariant violated by race condition | Low | Critical | Database-level constraint (check sum before commit); all ledger writes through single service; comprehensive unit and integration tests for edge cases |
| Reconciliation auto-resolution masks a real problem | Medium | High | Auto-resolution patterns have dollar thresholds ($0.01 rounding: auto-resolve; $5.00 discrepancy: never auto-resolve); weekly audit of auto-resolved items |
| OFAC screening API latency blocks transactions | Low | Medium | Cache OFAC list locally with daily refresh; screen against local cache (< 10ms) with async re-check against live API |

---

## 10. Dependencies

| Dependency | Owner | Risk Level | Notes |
|---|---|---|---|
| Stripe (primary PSP) | External | Medium | Existing integration; refactor to orchestrator pattern |
| Adyen or Tabapay (secondary PSP) | External | Medium | New integration; 4-6 week setup including sandbox testing |
| Alloy or Persona (KYC/identity) | External | Medium | Identity verification vendor; evaluate both, select in Phase 3 |
| OFAC SDN list (Treasury) | External | Low | Public list updated ~every 2 weeks; daily download and cache |
| Banking partner (settlement) | External | High | Settlement file formats, BAI2 statement availability, cutoff times |
| PostgreSQL | Internal | Low | Already in use; add constraints for ledger invariants |
| Redis | Internal | Low | Already in use; add fraud rule caching and rate limiting |

---

## Appendix A: Transaction State Machine

```
                    ┌──────────┐
                    │ INITIATED│
                    └────┬─────┘
                         │
                    ┌────▼─────┐
               ┌────│ PENDING  │────┐
               │    └────┬─────┘    │
               │         │          │
          (fraud        (fraud     (fraud
          decline)      approve)   review)
               │         │          │
          ┌────▼───┐ ┌───▼──────┐ ┌▼────────┐
          │DECLINED│ │PROCESSING│ │IN_REVIEW │
          └────────┘ └───┬──────┘ └──┬───────┘
                         │           │
                    (PSP         (analyst
                    confirms)    decision)
                         │           │
                    ┌────▼─────┐     │
                    │ SETTLED  │◄────┘ (approved)
                    └──────────┘     │
                                     │ (rejected)
                    ┌──────────┐     │
                    │  FAILED  │◄────┘
                    └────┬─────┘
                         │
                    (user retries)
                         │
                    ┌────▼─────┐
                    │ INITIATED│  (new transaction, new idempotency key)
                    └──────────┘

  Reversal states:
  SETTLED -> DISPUTE_OPENED -> DISPUTE_RESOLVED (in user's favor)
                             -> DISPUTE_DENIED   (in platform's favor)
  SETTLED -> REFUND_INITIATED -> REFUNDED
```

---

## Appendix B: KYC Verification Tiers

| Tier | Verification Required | Transaction Limit | Daily Limit | Monthly Limit |
|---|---|---|---|---|
| **Basic** | Email + phone + employer match | $250 per transaction | $500 per day | $2,000 per month |
| **Standard** | Basic + government ID + database check (SSN trace, address history) | $1,000 per transaction | $2,500 per day | $10,000 per month |
| **Enhanced** | Standard + document verification (utility bill, bank statement) + manual review | $5,000 per transaction | $10,000 per day | $25,000 per month |

Upgrade triggers: user attempts transaction exceeding current tier limit -> prompt for additional verification -> verify -> upgrade tier -> retry transaction.

Downgrade triggers: failed re-verification at 12-month review -> downgrade to previous tier -> notify user.

---

## Appendix C: Glossary

| Term | Definition |
|---|---|
| **Double-entry** | Accounting method where every financial transaction records equal debits and credits across accounts; ensures books always balance |
| **Journal entry** | A single financial event recorded in the ledger as one or more debit/credit line items that sum to zero |
| **Hold** | A temporary reduction in available balance that reserves funds for a pending transaction; expires if not captured |
| **Idempotency key** | A unique identifier submitted with a transaction request that prevents duplicate processing on retry |
| **PSP** | Payment Service Provider; third-party that processes payments (Stripe, Adyen, Tabapay) |
| **Circuit breaker** | A pattern that stops routing to a failing PSP after repeated errors, preventing cascade failures |
| **Settlement** | The actual movement of funds between accounts, as opposed to authorization which is a promise to move funds |
| **Reconciliation** | The process of verifying that internal records match external records (PSP reports, bank statements) |
| **SAR** | Suspicious Activity Report; filing required by FinCEN when financial activity meets certain suspicious patterns |
| **OFAC SDN** | Office of Foreign Assets Control Specially Designated Nationals list; sanctions screening required before moving money |
| **BAI2** | Banking Administration Institute format; standard file format for bank statement data |
| **NACHA** | National Automated Clearing House Association; governs ACH payment file formats and rules |
| **Net settlement** | Aggregating many individual transactions into a single net transfer to reduce processing fees and bank transfers |
| **Chargeback** | A forced reversal of a card transaction initiated by the cardholder's bank, typically due to fraud or dispute |
| **BSA** | Bank Secrecy Act; US law requiring financial institutions to maintain anti-money laundering programs |

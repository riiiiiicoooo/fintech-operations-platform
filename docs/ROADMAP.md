# Product Roadmap: Fintech Operations Platform

**Last Updated:** February 2025

---

## Roadmap Overview

```
Phase 1: Foundation     Phase 2: Resilience     Phase 3: Compliance     Phase 4: Scale
(Weeks 1-8)             (Weeks 9-16)            (Weeks 17-24)           (Weeks 25+)

Prove the ledger is     Multi-PSP failover,     KYC automation,         ML fraud, real-time
correct and replace     fraud detection, and    transaction monitoring, recon, multi-currency,
the old system          automated recon         SAR workflow, audit     partner API

|- Double-entry ledger  |- Second PSP (Adyen)   |- Tiered KYC           |- ML fraud model
|- Account taxonomy     |- Health scoring       |- Identity vendor      |- Real-time recon
|- Journal entry API    |- Circuit breakers     |- Transaction monitor  |- Multi-currency
|- Migrate existing     |- Fraud rule engine    |- SAR workflow         |- Partner API
|  transactions         |- Review queue         |- OFAC screening       |- Predictive recon
|- Idempotency layer    |- Three-way recon      |- Compliance logging   |- Dispute automation
|- Hold management      |- Auto-resolution      |- Settlement auto      |- FedNow evaluation
|- Stripe via           |- Full fraud scoring   |- Examination reports  |- Analytics dashboard
|  orchestrator         |- PSP adapter layer    |- State disclosures    |- Embedded finance
'- Transaction state    '- Exception dashboard  '- Banking partner        evaluation
   machine                                         audit prep
```

---

## Phase 1: Foundation (Weeks 1-8)

**Goal:** Build the double-entry ledger, migrate off the flat transactions table, and route all payments through the orchestration layer. Prove the ledger is correct by running it in parallel with the existing system for 30 days.

**Theme:** Can we replace the source of truth without losing a penny?

| Week | Deliverable | Details |
|---|---|---|
| 1-2 | Double-entry ledger core | Account taxonomy (asset, liability, revenue, expense). Journal entry API with debit-equals-credit constraint enforced at database level. SERIALIZABLE isolation on ledger writes. Immutability enforced (no UPDATE/DELETE permissions for application user). |
| 2-3 | Account setup and idempotency | Chart of accounts creation (platform accounts, employer sub-accounts, user wallet accounts). Idempotency key storage in Redis (TTL: 7 days) with permanent backup in PostgreSQL. Duplicate request detection returning cached results. |
| 3-4 | Hold management | Hold creation on transaction initiation. Hold lifecycle (active, captured, voided, expired). Available balance calculation (posted balance minus active holds). 7-day hold expiration with critical alerting. |
| 4-5 | Transaction state machine | Full state machine: INITIATED, PENDING, PROCESSING, SETTLED, FAILED, DECLINED, IN_REVIEW. Event logging for every state transition. Immutable transaction_events table. |
| 5-6 | Payment orchestrator (Stripe only) | PSP adapter interface (create, capture, void, refund, status, webhook). Stripe adapter implementation. Webhook handler with event deduplication. All Stripe calls routed through the orchestrator instead of direct API calls. |
| 6-7 | Migration: parallel run | Both old system and new ledger record every transaction. Nightly comparison job: does the ledger balance match the old system's balance for every user? Delta report for investigation. No cutover yet. |
| 7-8 | Migration: cutover + cleanup | After 30 days of parallel run with < $0.01 delta, cut over to ledger as source of truth. Deprecate old transactions table reads. Keep old table as read-only archive. Update all dashboards and reports to read from ledger. |

**Exit Criteria:**
- Ledger matches Stripe settlement reports within $0.01 for 30 consecutive days during parallel run
- Zero double-posted transactions (idempotency working correctly)
- Zero balance discrepancies between ledger-calculated balance and Stripe-confirmed balance
- All active transactions flowing through the payment orchestrator
- Hold management working correctly (no stuck holds, no overdrafts from race conditions)
- Transaction state machine captures every state transition with immutable event log

**Key Risks:**
- Ledger migration balance discrepancies. Mitigation: 30-day parallel run before cutover. Daily comparison reports. Investigation of every delta, no matter how small.
- SERIALIZABLE isolation causing serialization failures under load. Mitigation: retry logic with exponential backoff. Load testing in staging with concurrent transaction simulation. In practice, serialization failures affect < 0.1% of transactions because most touch different accounts.
- Engineering team unfamiliar with double-entry accounting. Mitigation: week-long internal education session. Journal entry templates for every transaction type. Code review checklist requiring balanced entries.

---

## Phase 2: Resilience (Weeks 9-16)

**Goal:** Eliminate single points of failure (add second PSP with automated failover), detect fraud before money moves, and automate reconciliation.

**Theme:** Can the system handle a PSP outage at 3 AM without human intervention?

| Week | Deliverable | Details |
|---|---|---|
| 9-10 | Adyen PSP integration | Adyen adapter implementing the same PSP interface as Stripe. Webhook handler. Settlement report ingestion. Mapping Adyen status codes to our internal state machine. |
| 10-11 | PSP health scoring and routing | Weighted health score per PSP (success rate, latency, error rate, uptime). Routing table: primary and fallback per payment method. Health score threshold: < 0.8 triggers fallback consideration. |
| 11-12 | Circuit breaker | 5 failures in 60 seconds opens the circuit. 30-second cooldown before half-open test. Successful test closes the circuit. Failed test re-opens with 60-second cooldown. Redis-backed state. Alert on every OPEN event. |
| 12-13 | Fraud rule engine | Feature extraction from transaction context + user profile + device session (< 10ms). Blocklist/allowlist (Redis SET lookup, < 5ms). Rule evaluation: velocity, amount, device, geolocation, time-of-day (< 50ms). Weighted score aggregation. Decision: approve (< 30), review (30-70), decline (> 70). |
| 13-14 | Fraud review queue and logging | Review queue dashboard for fraud analyst. Accept/decline with documented reasoning. Immutable fraud_decisions table logging full feature vector, rules triggered, score, decision, and latency. This log becomes ML training data. |
| 14-15 | Three-way reconciliation engine | Nightly batch job at 2:00 AM ET. Data collection from ledger, PSP settlement reports, and BAI2 bank statements. Three-phase matching: exact (PSP ID + amount), fuzzy (amount + date + account), many-to-one (batch netting). |
| 15-16 | Auto-resolution and exception dashboard | Auto-resolution patterns: timing difference, batch netting, PSP fee deduction, FX rounding. Dollar threshold: max $5.00, pattern match required. Exception queue for Diana with full context. Reconciliation run summary dashboard. |

**Exit Criteria:**
- PSP failover tested in staging: simulate Stripe outage, confirm Adyen takes over within 60 seconds with zero user-facing errors
- Circuit breaker tested: inject 5 consecutive failures, confirm circuit opens, confirm recovery after cooldown
- Fraud detection rate > 85% (measured against historical chargebacks)
- False positive rate < 5% (legitimate transactions flagged for review)
- Fraud evaluation latency < 100ms (p95)
- Reconciliation match rate > 98%
- Auto-resolution handling > 75% of reconciliation breaks
- Exception count per run < 15

**Key Risks:**
- PSP failover to degraded secondary (Adyen might also be having issues). Mitigation: multi-signal health scoring. "Both unhealthy" scenario queues transactions with user notification rather than failing silently. Monthly failover drills in staging.
- Fraud rules too aggressive, blocking legitimate users. Mitigation: start with high thresholds (conservative, fewer declines). Tune weekly based on review queue analyst feedback. Allowlist for verified high-volume users. Easy override for analysts.
- Reconciliation auto-resolution masking real problems. Mitigation: $5.00 hard cap on auto-resolved deltas. Weekly audit of all auto-resolved items. Pattern matching required (dollar threshold alone is not sufficient).

---

## Phase 3: Compliance (Weeks 17-24)

**Goal:** Automate KYC verification, deploy transaction monitoring, build SAR workflow, and achieve examination readiness. Shift compliance from manual CSV review to a real-time system.

**Theme:** Can Priya (Compliance Officer) handle 3x transaction volume without adding headcount?

| Week | Deliverable | Details |
|---|---|---|
| 17-18 | Progressive KYC: Basic tier | Email verification, phone verification, employer match. Automated in < 30 seconds. $250/txn, $500/day, $2,000/month limits. User-facing verification flow in mobile app. |
| 18-19 | Progressive KYC: Standard + Enhanced tiers | Alloy integration for identity verification (ID document + database check + liveness). Standard tier: automated, < 3 minutes, 95%+ auto-approval. Enhanced tier: document upload + manual compliance review. Tier upgrade flow in mobile app. |
| 19-20 | Transaction monitoring rules | Async rule engine evaluating posted transactions. Rule categories: aggregation (cumulative thresholds), structuring (avoidance patterns), rapid movement (pass-through), geographic (IP mismatch), behavioral (anomaly detection). Alert generation with priority assignment. |
| 20-21 | Alert management and SAR workflow | Alert lifecycle: created, assigned, investigating, dismissed/SAR recommended. Assignment based on rule type and analyst workload. Investigation workspace with full user context. SAR narrative pre-population (who/what/when from data, analyst writes why/how). FinCEN BSA E-Filing integration. |
| 21-22 | OFAC sanctions screening | OFAC SDN list cached locally in Redis. Daily refresh at 6:00 AM ET. Synchronous screening on every outbound transfer (< 10ms). Fuzzy name matching (Jaro-Winkler > 0.85). Potential match blocks transaction and alerts compliance. False positive allowlist per user. |
| 22-23 | Compliance event log and audit trail | Separate PostgreSQL schema with restricted access. INSERT-only for application user. Hash chain for tamper resistance. Daily integrity verification. 7-year retention policy. All KYC, monitoring, SAR, and sanctions events logged. |
| 23-24 | Settlement automation and examination readiness | Automated daily settlement batching: collect settled transactions, calculate splits (employer, user, platform fee, PSP fee), net batching, holdback application, NACHA file generation. Pre-built examination reports (BSA/AML summary, SAR filing log, KYC status, alert disposition, OFAC screening log). State-specific disclosure components for CA, NY, TX, IL. |

**Exit Criteria:**
- KYC Basic tier: > 90% automated pass rate, < 30 seconds median verification time
- KYC Standard tier: > 90% automated pass rate, < 3 minutes median verification time
- Transaction monitoring: every transaction evaluated (zero gaps), < 20 open alerts at any time
- Alert review SLA: > 95% reviewed within priority SLA
- SAR filing SLA: 100% filed within 30 days of detection
- OFAC screening: < 10ms latency (p95), < 1% false positive rate
- Compliance event log: 100% integrity (hash chain unbroken)
- Examination reports generated in < 1 hour (vs. "2 weeks of scrambling" baseline)
- Settlement automation: daily batches processed without manual intervention

**Key Risks:**
- KYC vendor (Alloy) latency or downtime blocking user verification. Mitigation: queue-based architecture where verification requests are submitted and results are polled. If Alloy is down, users remain at current tier with pending upgrade. No user is blocked from transacting at their current tier level.
- Transaction monitoring generating too many alerts (analyst fatigue). Mitigation: start with conservative thresholds (fewer alerts). Tune based on first 30 days of alert disposition data. Retire rules with > 90% dismissal rate.
- Regulatory gaps discovered during banking partner audit. Mitigation: engage external compliance consultant for pre-audit review in Week 20. Build examination reports early (Week 22) and have them reviewed by the consultant before the audit.

---

## Phase 4: Scale (Weeks 25+)

**Goal:** ML-powered fraud detection, real-time reconciliation, multi-currency support, and partner API for embedded finance. These are the capabilities that unlock the next growth phase.

**Theme:** What does the platform need to support $25M+ ARR?

| Deliverable | Details | Priority |
|---|---|---|
| ML fraud model (shadow mode) | Train on 6+ months of fraud_decisions data. Run in shadow mode alongside rules engine for 60 days. Compare ML decisions to rule decisions. Measure incremental detection rate and false positive rate. Rules engine continues to make all real decisions until ML is validated. | P0 |
| ML fraud model (production) | After shadow validation, ML model handles scoring for transactions where it has high confidence. Rules engine remains as pre-filter and fallback. Analyst feedback loop: review decisions feed back into model retraining (monthly). | P0 |
| Real-time reconciliation | Reconcile individual transactions via PSP webhook events instead of nightly batch. Reduces detection time from hours to minutes. Nightly batch continues as backup and catches anything real-time missed. Requires real-time bank feed (or intraday BAI2). | P1 |
| Tabapay integration (instant transfers) | Third PSP adapter for real-time push-to-debit payouts. Specialized for instant transfers at lower cost than Stripe Instant. Routing rule: instant transfers default to Tabapay, fallback to Stripe. | P1 |
| Multi-currency architecture | Account-level currency designation. Exchange rate service integration. Cross-currency journal entries with realized FX gain/loss tracking. Initially: USD + MXN (largest non-USD user base). Does not change the double-entry model, only adds currency dimension to entries. | P2 |
| Partner API (embedded finance) | REST API for employers to embed financial services directly into their HR platforms. Endpoints: initiate transfer, check balance, transaction history, KYC status. API key management, rate limiting, webhook delivery. Documentation and sandbox environment. | P2 |
| Predictive reconciliation | ML model trained on historical reconciliation exceptions. Predicts which transactions are likely to have breaks before the nightly run. Pre-fetches PSP data for predicted exceptions. Reduces reconciliation run time by 40%. | P2 |
| Dispute automation | Automated dispute intake with structured reason codes. Auto-resolution for disputes matching common patterns (duplicate charge, amount mismatch). PSP dispute API integration for chargeback representment. Reduces average dispute resolution from 20 minutes to 5 minutes for auto-resolvable cases. | P3 |
| Analytics dashboard | Self-service analytics for leadership. Cohort analysis (transaction patterns by employer, by user tenure, by geography). Revenue attribution (which employers drive the most platform fee revenue). Churn prediction (early warning for employers reducing usage). | P3 |
| FedNow evaluation | Evaluate FedNow (Federal Reserve's real-time payment rail) as an alternative to card-based instant transfers. Lower cost per transaction. 24/7/365 settlement. Requires banking partner integration. Build-vs-wait decision based on banking partner readiness. | P3 |

---

## Dependency Map

```
Phase 1                Phase 2                Phase 3              Phase 4
(Foundation)           (Resilience)           (Compliance)         (Scale)

Double-entry ─────────> Balance checks ───────> KYC tier limits ──> Multi-currency
ledger                  in fraud engine         enforced at          adds currency to
                                                ledger level         journal entries

Payment ──────────────> Multi-PSP routing ────> Settlement ────────> Tabapay
orchestrator            + circuit breakers      automation           integration

Transaction ──────────> Fraud scoring ─────────────────────────────> ML fraud model
state machine           generates labeled                            (needs 6mo of
                        decision data                                labeled data)

Idempotency ──────────> Webhook ──────────────> OFAC screening ───> Real-time
layer                   deduplication           (sync check          reconciliation
                                                before transfer)

                        Three-way ─────────────> Compliance ────────> Predictive
                        reconciliation           event log            reconciliation
                        + auto-resolution        (audit trail)        (ML on exceptions)
```

**Critical path:** Phase 1 (ledger) must be complete and validated before Phase 2 begins. The ledger is the foundation that every other service depends on. Phase 2 (fraud engine) must be producing labeled decisions for at least 6 months before the Phase 4 ML model can be trained. Phase 3 (compliance event log) must be operational and audit-verified before the banking partner annual audit.

---

## What's NOT on the Roadmap (and Why)

| Feature | Why Not | Revisit When |
|---|---|---|
| Crypto/stablecoin support | Regulatory complexity (state-by-state rules, unclear federal guidance). Not where our users are. | Federal crypto regulation clarifies, or user demand data justifies the investment |
| Physical debit cards | Card program management (BIN sponsorship, card production, activation) is a separate product. Our users primarily use earned wage access to bank transfers. | User research shows > 30% of users would prefer card-based access |
| P2P transfers between users | Opens money transmission regulatory questions beyond the employer-employee relationship. Our banking partner agreement scopes us to employer-funded transactions. | Banking partner expands agreement, or we obtain independent money transmitter licenses |
| International wire transfers | Requires correspondent banking relationships, SWIFT integration, and cross-border compliance (FATF, individual country regulations). Massive scope increase. | > 10% of users are international workers needing cross-border remittance |
| Credit or lending products | Lending requires additional licenses, underwriting infrastructure, and collections capability. Completely different risk profile. | Strategic decision to expand from payments into lending (likely separate product) |
| Self-hosted/on-premise | Our banking partner relationship requires our infrastructure to be under our control for compliance purposes. Self-hosted would break this. | Never (fundamental to our compliance model) |

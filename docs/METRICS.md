# Metrics Framework: Fintech Operations Platform

**Last Updated:** February 2025

---

## 1. North Star Metric

**Transactions settled successfully per day**

This metric captures the core value of the platform: moving money reliably for users. It combines volume (the platform is growing), reliability (transactions are completing, not failing), and speed (settlement is happening, not stuck in limbo). A transaction counts as "settled successfully" when the ledger entry is posted, the PSP confirms the transfer, and the funds reach the destination within the expected window.

**Baseline (before platform):** ~800/day (capped by manual reconciliation and single-PSP dependency)
**Target:** 5,000+/day
**Current:** ~4,200/day

Why this metric and not revenue or user count: Revenue is a lagging indicator that doesn't tell you if the platform is healthy today. User count doesn't tell you if users are actually transacting. Settled transactions per day is the most direct measure of whether the product is working. If this number is growing and the guardrails are holding, the business is healthy.

---

## 2. Input Metrics

These are the levers that drive the North Star. Each one maps to a specific service or team that owns improving it.

### 2.1 Transaction Reliability

| Metric | Definition | Target | Current | Measurement |
|---|---|---|---|---|
| Transaction success rate | Transactions reaching SETTLED / total initiated (excluding user-cancelled) | > 97% | 97.8% | Transaction state machine, 5-minute rolling window |
| PSP success rate (primary) | Successful PSP API calls / total attempts on primary PSP | > 99% | 99.2% | PSP adapter response logging |
| PSP failover rate | Transactions routed to fallback PSP / total transactions | < 5% | 2.1% | PSP router decision log |
| Failed transaction rate | Transactions reaching FAILED state / total initiated | < 3% | 2.2% | Transaction state machine |
| Retry success rate | Transactions that succeeded on retry / transactions that required retry | > 80% | 84% | Retry counter on transaction events |

### 2.2 Transaction Speed

| Metric | Definition | Target | Current | Measurement |
|---|---|---|---|---|
| Sync path latency (p95) | Time from API request to user response (fraud check + ledger write) | < 500ms | 215ms | API request-response timestamps |
| Fraud evaluation latency (p95) | Time for fraud rule engine to return a decision | < 100ms | 62ms | Fraud service instrumentation |
| End-to-end settlement time (median) | Time from user initiation to funds confirmed in destination | < 24 hours (ACH), < 1 hour (instant) | 18 hours (ACH), 22 minutes (instant) | Transaction created_at to SETTLED event timestamp |
| Balance query latency (p95) | Time to return user's available balance | < 50ms | 28ms | Materialized view query timing |

### 2.3 Financial Accuracy

| Metric | Definition | Target | Current | Measurement |
|---|---|---|---|---|
| Reconciliation match rate | Transactions matched across ledger, PSP, and bank / total transactions | > 99% | 99.2% | Nightly reconciliation run output |
| Auto-resolution rate | Reconciliation breaks resolved automatically / total breaks | > 80% | 78% | Reconciliation exception resolution type |
| Exception count per run | Unresolved exceptions after auto-resolution | < 10 | 8 (avg) | Reconciliation run summary |
| Ledger balance accuracy | Ledger-calculated balances matching PSP + bank confirmed amounts | 100% (within $0.01) | 100% | Daily balance verification job |
| Double-entry violation attempts | Journal entries rejected for debits != credits | 0 | 0 | Database constraint trigger log |

### 2.4 Fraud Detection

| Metric | Definition | Target | Current | Measurement |
|---|---|---|---|---|
| Fraud detection rate | Confirmed fraud caught by rules / total confirmed fraud | > 90% | 94.3% | Fraud decisions cross-referenced with chargeback data (30-day lag) |
| False positive rate | Legitimate transactions flagged for review / total transactions | < 5% | 3.1% | Fraud decisions where decision = 'review' and analyst marked legitimate |
| Chargeback rate (90-day rolling) | Chargebacks / total card transactions | < 0.3% | 0.18% | PSP chargeback reports |
| Review queue depth | Transactions awaiting fraud analyst review | < 25 | 12 (avg) | Fraud review queue count |
| Review turnaround time (median) | Time from REVIEW decision to analyst disposition | < 2 hours | 1.4 hours | Fraud decision timestamps |

### 2.5 Compliance

| Metric | Definition | Target | Current | Measurement |
|---|---|---|---|---|
| KYC automated pass rate | Users verified without manual review / total verification attempts | > 90% | 93% | KYC verification status counts |
| KYC verification time (Basic, p95) | Time from submission to tier upgrade | < 30 seconds | 12 seconds | KYC event timestamps |
| Monitoring alert SLA compliance | Alerts reviewed within priority SLA / total alerts | > 95% | 97% | Alert created_at vs. resolution timestamp |
| SAR filing SLA | SARs filed within 30 days of detection | 100% | 100% | SAR event timestamps |
| OFAC screening latency (p95) | Time for sanctions check on outbound transfer | < 10ms | 4ms | OFAC screening instrumentation |

---

## 3. Guardrail Metrics

These metrics should NOT degrade as we optimize for the North Star. If any guardrail breaches its alert threshold, we pause growth initiatives and investigate.

| Metric | Acceptable Range | Alert Threshold | Why It Matters |
|---|---|---|---|
| Double-entry violation | 0 | > 0 (immediate page) | The ledger's core invariant. Any violation means a bug in the most critical system. |
| Ledger balance discrepancy | $0.00 | > $0.01 (immediate page) | Ledger and confirmed bank/PSP amounts must match. Any delta means money is unaccounted for. |
| Chargeback rate | < 0.3% | > 0.5% | Card networks (Visa/Mastercard) place you in monitoring programs at 0.9-1.0%. Getting close is an existential risk. |
| Suspense account balance | < $500 | > $1,000 | Growing suspense means reconciliation has gaps. Unclassified money is an audit finding. |
| Compliance event log integrity | 100% | < 100% | Hash chain must be unbroken. Any gap means the audit trail is compromised. |
| PII exposure via logs/API | 0% | > 0% | SSNs, bank account numbers, card tokens must never appear in application logs or error responses. |
| Fraud false negative rate | < 10% | > 15% | If we're missing more than 15% of confirmed fraud, the rules need immediate tuning. |
| PSP circuit breaker OPEN duration | < 5 minutes | > 10 minutes | Extended circuit breaker means the PSP is degraded and we're running on fallback. Acceptable briefly, not for long. |

---

## 4. Business Impact Metrics

### 4.1 Revenue and Growth

| Metric | Before Platform | After Platform | Improvement |
|---|---|---|---|
| Annual recurring revenue | $3M (capped by ops bottleneck) | $8M | 167% growth |
| Net revenue retention | ~95% | 118% | Existing employers expanding usage |
| Monthly active users | ~5,000 | ~15,000 | 3x growth |
| Average transactions per active user per month | 2.8 | 4.1 | 46% increase (users trust the product more) |
| Employer onboarding time | 4-6 weeks | 1-2 weeks | 65-75% faster |

### 4.2 Operational Efficiency

| Metric | Before Platform | After Platform | Improvement |
|---|---|---|---|
| Reconciliation time (manual) | 15-20 hours/week | ~30 minutes/night (automated) | 96% reduction |
| Reconciliation match rate | ~89% | 99.2% | From "barely functional" to "audit-ready" |
| Support tickets per 1,000 transactions | 45 | 12 | 73% reduction (fewer failed transactions, better status visibility) |
| Finance ops headcount needed | 3 FTEs | 1 FTE + automated systems | 66% reduction |
| Time to resolve transaction dispute | 2.5 hours average | 20 minutes average | 87% faster |

### 4.3 Risk Reduction

| Metric | Before Platform | After Platform | Improvement |
|---|---|---|---|
| Chargeback rate | 0.8% (approaching 1.0% Visa threshold) | 0.18% | 78% reduction |
| Fraud detection rate | ~60% (estimated, no formal measurement) | 94.3% | From guessing to measurable |
| Undetected transaction monitoring gaps | Unknown (manual CSV review) | 0 (automated, every transaction evaluated) | From unknown risk to zero gaps |
| Time from suspicious activity to SAR filing | 18 days average | 8 days average | 56% faster |
| Regulatory examination readiness | "Scramble for 2 weeks when auditors call" | Reports generated in < 1 hour | Always ready |

---

## 5. Metric Relationships

```
                    +-------------------------------------+
                    |           NORTH STAR                 |
                    |  Transactions settled successfully   |
                    |  per day                             |
                    +------------------+------------------+
                                       |
            +--------------------------+---------------------------+
            |                          |                           |
            v                          v                           v
  +------------------+    +--------------------+    +--------------------+
  | Transaction      |    | Financial          |    | Risk               |
  | Reliability      |    | Accuracy           |    | Management         |
  |                  |    |                    |    |                    |
  | Success rate     |    | Recon match rate   |    | Fraud detection    |
  | PSP health       |    | Auto-resolution    |    | Chargeback rate    |
  | Failover rate    |    | Exception count    |    | False positive rate|
  | Retry success    |    | Ledger accuracy    |    | Compliance SLAs    |
  | Sync latency     |    | Suspense balance   |    | KYC pass rate      |
  +--------+---------+    +---------+----------+    +---------+----------+
           |                        |                          |
           +------------------------+--------------------------+
                                    |
                    +---------------+----------------+
                    |          GUARDRAILS             |
                    |                                 |
                    | Double-entry violations = 0     |
                    | Ledger discrepancy = $0.00      |
                    | Chargeback rate < 0.3%          |
                    | Suspense balance < $500         |
                    | Compliance log integrity = 100% |
                    | PII exposure = 0%               |
                    +---------------------------------+
```

**How they connect:**

Transaction reliability drives the North Star directly. If success rate drops, fewer transactions settle. If latency increases, users abandon transactions mid-flow. If PSP failover isn't working, a primary PSP outage halts all processing.

Financial accuracy drives the North Star by maintaining operational health. If the reconciliation match rate drops, the finance ops team shifts from growth support to exception investigation. If the suspense account grows, it consumes analyst time. Accurate financials mean the ops team can focus on scaling rather than firefighting.

Risk management protects the North Star by preventing catastrophic events. If the chargeback rate crosses the card network threshold, Visa/Mastercard can terminate our processing agreement entirely. If compliance SLAs slip, a regulatory action can freeze the business. Fraud detection prevents losses that directly reduce revenue.

---

## 6. Financial Health Dashboard

This is what Diana (Finance Ops Manager) sees every morning. Each panel answers a specific question she used to spend hours figuring out manually.

| Panel | Question It Answers | Data Source | Refresh |
|---|---|---|---|
| Money in transit | "How much money is currently between our ledger and destination banks?" | SUM(active holds) from holds table | Real-time |
| Today's volume | "How many transactions have we processed today and what's the total dollar amount?" | Transactions table, today's date filter | 1 minute |
| Transaction success funnel | "Where are transactions failing? Fraud? PSP? Ledger?" | Transaction events grouped by terminal state | 5 minutes |
| PSP health | "Is Stripe healthy? Is Adyen available if we need it?" | PSP health scores from Redis | Real-time |
| Reconciliation status | "Did last night's recon run clean? How many exceptions?" | reconciliation_runs table, latest run | After each run |
| Settlement pipeline | "What's the status of today's settlement batch?" | settlement_batches table | 5 minutes |
| Chargeback tracker | "Are we trending toward trouble with card networks?" | 90-day rolling chargeback rate from PSP reports | Daily |
| Suspense balance | "Is there unclassified money sitting in the system?" | Suspense account balance from ledger | Hourly |

---

## 7. Reporting Cadence

| Report | Audience | Frequency | Key Metrics |
|---|---|---|---|
| Financial health dashboard | Finance ops (Diana) | Real-time | Money in transit, PSP health, recon status, chargeback rate |
| Transaction reliability report | Engineering + PM | Daily | Success rate, latency percentiles, error breakdown by code |
| Fraud performance report | Fraud analyst + PM | Daily | Decisions breakdown (approve/review/decline), queue depth, false positive samples |
| Compliance dashboard | Compliance (Priya) | Daily | Open alerts, SLA compliance, KYC pass rates, OFAC screening stats |
| Reconciliation summary | Finance ops + leadership | Daily (after nightly run) | Match rate, exception count, auto-resolution rate, suspense balance |
| Business metrics | Leadership | Weekly | Volume trends, revenue, employer expansion, user growth |
| Risk report | Leadership + compliance | Weekly | Chargeback trend, fraud loss, compliance alert volume, SAR activity |
| Executive summary | C-suite | Monthly | ARR, NRR, transaction volume growth, operational efficiency gains, risk posture |

---

## 8. Experiments and Learning

### 8.1 Completed Experiments

| Experiment | Hypothesis | Result | Decision |
|---|---|---|---|
| Fraud threshold: 30/70 vs. 25/75 | Lowering review threshold from 30 to 25 will catch more fraud but increase review volume | Review volume increased 40%. Incremental fraud caught: 2.1%. False positive rate increased from 2.8% to 4.6%. | Kept 30/70. The 2.1% incremental catch wasn't worth doubling analyst workload. Will revisit when ML model is deployed. |
| Circuit breaker: 5 failures/60s vs. 3 failures/30s | More aggressive circuit breaker (3/30s) will reduce user-facing errors during PSP degradation | User-facing error rate during PSP issues dropped from 4.2% to 1.8%. But false-open rate (circuit opening on transient errors) increased from 0.1% to 0.8%. | Adopted 5/60s as compromise. Tested 4/45s in staging, deployed after 2 weeks of monitoring. |
| Reconciliation auto-resolution: $0.01 vs. $0.05 threshold | Increasing auto-resolve tolerance from $0.01 to $0.05 will reduce exception count without masking real issues | Exception count dropped 35%. All auto-resolved items at $0.02-$0.05 were confirmed as PSP fee rounding. Zero real issues masked over 90 days. | Adopted $0.05 threshold. Added weekly audit of auto-resolved items as safety net. |
| Progressive KYC: Basic at $500/mo vs. $2,000/mo | Raising Basic tier limit from $500/mo to $2,000/mo will reduce KYC friction without increasing fraud | 28% fewer users needed to upgrade to Standard. Fraud rate in $500-$2,000 range was 0.02% (same as below $500). | Adopted $2,000/mo for Basic tier. No measurable increase in risk. |

### 8.2 Planned Experiments

| Experiment | Hypothesis | Metrics to Watch | Guardrails |
|---|---|---|---|
| ML fraud model (shadow mode) | ML model running alongside rules will identify fraud patterns that rules miss | Incremental detection rate, false positive comparison | Rules engine still makes all decisions; ML is observe-only for 60 days |
| Real-time reconciliation (per-transaction) | Reconciling each transaction immediately instead of nightly will catch issues faster | Time to exception detection, exception count, system load impact | Nightly batch continues as backup; real-time is additive, not replacement |
| Tabapay for all instant transfers | Routing all instant transfers through Tabapay instead of Stripe Instant will reduce cost | Cost per instant transfer, success rate, latency | If success rate drops below 98%, auto-fallback to Stripe |

---

## 9. Anti-Metrics

Metrics we intentionally do NOT optimize for, because optimizing for them would hurt the product.

| Anti-Metric | Why We Don't Optimize For It | What We Track Instead |
|---|---|---|
| Transaction volume (raw count) | Inflated by retries, test transactions, micro-transactions that don't represent real usage | Settled transactions per day (only successful, real transactions) |
| Fraud decline rate | Maximizing declines means we're blocking legitimate users. A 0% fraud loss rate means we're too aggressive. | Detection rate balanced against false positive rate. We accept some fraud loss to avoid blocking good users. |
| KYC verification speed (at all costs) | Fastest verification = weakest verification. We could auto-approve everyone in 0 seconds. | Verification speed at each tier, with pass rate and post-verification fraud rate as quality checks |
| Reconciliation match rate (by loosening tolerances) | We could auto-resolve everything and report 100% match rate. That hides problems. | Match rate with strict tolerances ($0.05 max auto-resolve) plus exception count as a separate metric |
| Support ticket reduction (by making disputes harder) | We could reduce tickets by hiding the dispute button. That's not a real improvement. | Tickets per 1,000 transactions, with ticket resolution time and CSAT as quality measures |

# Architecture Decision Records

This document captures the key technical decisions made in the Fintech Operations Platform, including the context that drove each decision, alternatives considered, and the trade-offs accepted.

---

## ADR-001: Double-Entry Ledger with Immutable Journal Entries

**Status:** Accepted
**Date:** 2024-01
**Context:** The platform handles multi-party fund flows (employer funding, user wallets, platform fees, PSP receivables) and must maintain a provably correct financial record. Traditional single-entry accounting cannot guarantee that funds are neither created nor destroyed within the system, and mutable transaction records create audit risk under SOX and BSA/AML requirements.

**Decision:** Implement a double-entry ledger engine where every financial mutation is recorded as a balanced journal entry (total debits == total credits). Journal entries are append-only and immutable once posted. Corrections, refunds, and chargebacks are recorded as new reversing entries with a `reference_type` and `reference_id` linking back to the original. The ledger uses a chart of accounts with four account types (Asset, Liability, Revenue, Expense), each with debit-normal or credit-normal semantics. A `verify_system_balance()` method provides a global invariant check that total system debits equal total system credits.

**Alternatives Considered:**
- **Single-entry transaction log:** Simpler to implement but cannot guarantee conservation of funds. Detecting discrepancies requires ad-hoc queries rather than structural invariants.
- **Event-sourced ledger with projections:** Considered for its natural immutability, but adds significant complexity in projection rebuilds and snapshot management. The double-entry model provides the same immutability guarantees with a more established pattern in financial systems.
- **Third-party ledger service (e.g., Modern Treasury, Moov):** Would accelerate delivery but introduces vendor lock-in for the most critical component of the system and limits customization of account taxonomy.

**Consequences:**
- Every balance is derived from scanning journal entry lines, which is correct but O(n) without database indexing. Production requires materialized balance views or cached balances with periodic reconciliation.
- Corrections never alter history, providing a complete audit trail. This enables point-in-time balance queries for compliance and dispute resolution.
- The balanced-entry constraint catches entire categories of bugs at write time (e.g., a fee calculation that does not account for all fund movement).
- Schema complexity is higher than a simple transactions table, requiring team familiarity with accounting fundamentals.

---

## ADR-002: Python Decimal Arithmetic for All Monetary Values

**Status:** Accepted
**Date:** 2024-01
**Context:** Financial calculations involving percentages (1.25% platform fee, 2.9% + $0.30 card processing fee) and multi-party splits produce fractional cents. IEEE 754 floating-point arithmetic introduces rounding errors that accumulate across thousands of transactions. For example, `0.1 + 0.2 != 0.3` in float, and repeated fee calculations can produce penny discrepancies that compound into material variances at scale.

**Decision:** Use Python's `decimal.Decimal` type for all monetary amounts throughout the entire codebase -- ledger entries, payment orchestrator responses, settlement split calculations, fraud thresholds, compliance limits, and API request/response models. All rounding uses `ROUND_HALF_UP` with explicit quantization to `Decimal("0.01")` at calculation boundaries. Pydantic models enforce `decimal_places=2` on amount fields. The NACHA generator converts to integer cents only at the final file-generation step.

**Alternatives Considered:**
- **Integer cents throughout:** Eliminates rounding entirely but makes percentage calculations awkward (must divide by 100 at display boundaries). Split calculations with three-way fee deductions become error-prone when intermediate values must remain in cents.
- **Float with rounding at boundaries:** Simpler code but rounding errors are silent and cumulative. A 0.01% error rate across 100,000 daily transactions produces material discrepancies.
- **Third-party money library (e.g., py-moneyed):** Adds a dependency for functionality that `Decimal` handles natively. The platform's needs do not extend to multi-currency conversion where such libraries add more value.

**Consequences:**
- Decimal arithmetic is slower than float (~10x), but this is irrelevant at the transaction volumes the platform handles since the bottleneck is I/O (PSP API calls, database writes), not arithmetic.
- All developers must use `Decimal("0.01")` string initialization, never `Decimal(0.01)` (which captures float imprecision). This requires discipline and code review enforcement.
- Settlement split validation (`SplitCalculation.validate()`) can assert that `gross_amount == platform_fee + psp_fee + user_receives + holdback_amount` exactly, catching rounding errors at write time.

---

## ADR-003: Multi-PSP Payment Orchestration with Health-Score Routing and Circuit Breakers

**Status:** Accepted
**Date:** 2024-02
**Context:** Relying on a single payment service provider creates a single point of failure. PSP outages (Stripe experienced multiple incidents in 2023) would halt all payment processing. Additionally, different PSPs offer different fee structures for different payment methods (Tabapay is 40% cheaper than Stripe for push-to-debit), and cost optimization requires routing to the cheapest available provider.

**Decision:** Implement a `PaymentOrchestrator` with a PSP adapter pattern (Protocol-based interface), a routing table mapping payment methods to primary/fallback PSPs, per-PSP health scoring (weighted composite of success rate, P95 latency, error rate, and uptime), and per-PSP circuit breakers. The routing table defines: ACH -> Stripe/Adyen, Card -> Stripe/Adyen, Wire -> Adyen/Stripe, Instant -> Tabapay/Stripe. The orchestrator attempts the primary PSP with exponential backoff (500ms base, 3 max retries, 25% jitter), then fails over to the fallback PSP. If the primary PSP's health score drops below 0.8 and the fallback is healthy, routing automatically inverts. Circuit breakers open after 5 failures within 60 seconds and enter half-open state after a 30-second cooldown.

**Alternatives Considered:**
- **Single PSP (Stripe only):** Simplest integration but creates availability risk and prevents cost optimization. Stripe's ACH fees (0.8%) are higher than Adyen's (0.6%) and Stripe's instant transfer fees are higher than Tabapay's (1.5% vs 2.5%).
- **Round-robin routing:** Distributes load but does not account for PSP health or payment method specialization. Would route card payments to Tabapay, which only supports instant transfers.
- **Manual failover:** Ops team switches PSP during incidents. Too slow for a platform targeting 99.95% availability; PSP degradation can start and recover within minutes.

**Consequences:**
- The adapter pattern means adding a new PSP requires only implementing the `PSPAdapter` protocol (5 methods: `create_payment`, `capture_payment`, `void_payment`, `refund_payment`, `get_status`) and adding a routing rule. No changes to the orchestrator core.
- Health scores require rolling-window state (1-hour window for success rate, 15-minute window for error rate), adding memory overhead per PSP. This is bounded by the number of PSPs (currently 3).
- Circuit breakers can cause brief periods where no PSP is available if all circuits open simultaneously. The orchestrator returns an `ALL_PSP_UNAVAILABLE` error with a retry-later instruction in this case.
- Idempotency keys are forwarded to PSPs, ensuring that retries across primary and fallback do not create duplicate charges.

---

## ADR-004: Three-Way Reconciliation Engine with Auto-Resolution

**Status:** Accepted
**Date:** 2024-02
**Context:** The platform handles funds across three independent systems: the internal ledger, PSP settlement reports, and bank statements (BAI2 format). Discrepancies between these systems must be detected daily. Manual reconciliation of thousands of transactions is operationally unsustainable and introduces risk of undetected fund leakage. Common break types (timing differences, PSP fee deductions, FX rounding, batch netting) are well-understood and follow predictable patterns.

**Decision:** Implement a `ReconciliationEngine` that performs nightly three-way matching in five phases: (1) exact match by PSP transaction ID + amount + PSP name, (2) fuzzy match by approximate amount (within $5.00) and date (+/- 1 business day), (3) many-to-one match for batch netting (sum of PSP transactions matches a single bank deposit), (4) auto-resolution of known break patterns (timing, fee deduction, FX rounding, batch netting, duplicate webhook) with configurable dollar thresholds, and (5) exception creation for unresolved breaks with priority classification (Critical >$100, High $5-$100, Medium <$5 unknown). Auto-resolution is capped at $5.00 maximum delta to prevent masking real discrepancies.

**Alternatives Considered:**
- **Two-way reconciliation (ledger vs PSP only):** Misses bank-level discrepancies. A PSP could report a successful settlement that the bank never receives.
- **Manual reconciliation with spreadsheets:** Does not scale beyond ~100 transactions/day. The target of 10,000+ daily transactions requires automation.
- **Real-time reconciliation on each webhook:** Provides faster detection but bank statements are only available daily (BAI2 files). Three-way matching inherently requires batch processing for the bank leg.

**Consequences:**
- The multi-phase matching approach achieves a target ~99% auto-match rate (92% exact, 5% fuzzy, 2% many-to-one), leaving only ~1% for manual review.
- Auto-resolution patterns are conservative (e.g., FX rounding capped at $0.05) to avoid masking real problems. Each pattern is individually configurable.
- Exception queue provides priority-based triage, ensuring critical discrepancies (>$100 or suspicious patterns) are reviewed within 4 hours.
- The engine maintains a `ReconciliationRun` record with full statistics, enabling trend analysis (e.g., detecting a PSP that is generating increasing fee deduction breaks).

---

## ADR-005: Rule-Based Fraud Detection with Weighted Scoring and Decision Thresholds

**Status:** Accepted
**Date:** 2024-02
**Context:** The platform processes earned wage access transfers where fraud patterns differ from traditional e-commerce (no card-present transactions, known employer relationships, predictable transfer amounts). A full ML-based fraud system requires months of labeled training data that does not exist at launch. However, the platform must still block obvious fraud patterns (velocity abuse, new account exploitation, structuring) from day one.

**Decision:** Implement a `FraudDetector` with a configurable rule engine. Each rule has a name, weight, and evaluation function. Nine rules cover: high amount vs. average (3x threshold, weight 20), transaction velocity (5+/24h, weight 15), amount velocity ($2,500+/24h, weight 18), new account high value (<7 days + >$500, weight 22), new device (weight 10), multiple failed attempts (3+, weight 25), near tier limit (within 10%, weight 12), round amount (>=$500, weight 5), and first transaction high value (>$250, weight 15). The raw score is normalized to 0-100 and mapped to three decisions: APPROVE (<30), REVIEW (30-70), DECLINE (>70). Blocklist and allowlist provide fast-path overrides. Every decision is logged immutably as a `FraudResult` including features, triggered rules, and latency, creating training data for future ML models.

**Alternatives Considered:**
- **No fraud detection at launch:** Unacceptable regulatory and financial risk. Even basic velocity checks prevent the most common abuse patterns.
- **Third-party fraud service (Sardine, Unit21):** Viable but expensive ($0.05-0.10 per transaction) and introduces external latency. The rule-based approach runs synchronously in <100ms. A hybrid approach (internal rules + external service for high-risk) is planned for Phase 2.
- **ML model from day one:** Requires labeled training data (months of fraud/non-fraud examples) that does not exist pre-launch. The rule engine generates the labeled decisions needed to train a future model.

**Consequences:**
- Rule weights are manually tuned and may not reflect actual fraud distributions. Regular weight adjustment based on fraud analyst feedback is required.
- The REVIEW band (30-70) creates analyst workload. If the review rate exceeds 10%, rule weights need recalibration.
- All fraud decisions are logged with full feature vectors, creating the labeled dataset needed for ML model training. The `model_version` field ("rules_v1") enables A/B comparison when an ML model is deployed.
- Feature extraction depends on pre-aggregated user history (transactions_last_24h, amount_last_7d, etc.), requiring these aggregations to be maintained in real-time or near-real-time.

---

## ADR-006: Progressive KYC Tiers with OFAC Sanctions Screening

**Status:** Accepted
**Date:** 2024-03
**Context:** BSA/AML regulations require identity verification before users can transact, but aggressive KYC requirements at onboarding create friction that reduces conversion. Different transaction volumes require different levels of identity assurance. Additionally, OFAC sanctions screening is a legal requirement for all outbound transfers -- processing a payment to a sanctioned individual exposes the company to severe penalties.

**Decision:** Implement three progressive KYC tiers with escalating limits and verification requirements: Basic (email + phone + employer match, $250/txn, $500/day, $2,000/month), Standard (government ID + database check via Alloy, $1,000/txn, $2,500/day, $10,000/month), Enhanced (document proof + manual review, $5,000/txn, $10,000/day, $25,000/month). KYC approvals expire after 12 months, requiring re-verification. Transaction monitoring runs asynchronously post-transaction with five rules: aggregation threshold ($5,000/30 days), structuring detection (3+ transactions in $2,000-$2,999 range within 7 days), rapid fund movement (>80% outbound/inbound ratio), behavioral anomaly (3x above 90-day average), and geographic mismatch (IP country vs. registered state). OFAC screening uses Jaro-Winkler fuzzy name matching (0.85 threshold) against the SDN list, runs synchronously on every outbound transfer, and automatically freezes accounts on match. All compliance events are logged in an append-only event log with hash chaining for tamper resistance and 7-year retention.

**Alternatives Considered:**
- **Single KYC tier:** Simpler but either too restrictive (blocks low-value users) or too permissive (exposes platform to AML risk for high-value users).
- **Third-party compliance platform (Alloy, Plaid Identity):** Used for the Standard tier verification step but not for the overall orchestration. The tier enforcement logic, transaction monitoring rules, and OFAC screening must remain in-platform for auditability and customization.
- **Exact-match OFAC screening only:** Misses name variations (transliteration, abbreviation, misspelling). Jaro-Winkler with a 0.85 threshold catches common variations while keeping false positive rates manageable.

**Consequences:**
- Progressive tiers allow users to start transacting immediately after Basic verification (<30 seconds) and upgrade only when they need higher limits, improving onboarding conversion.
- The structuring detection rule (3+ transactions in $2,000-$2,999) uses a conservative threshold below the BSA $10,000 reporting requirement, catching potential structuring behavior early.
- OFAC screening adds synchronous latency to every outbound transfer (<10ms target). The in-memory SDN list must be refreshed daily from the OFAC website.
- The append-only compliance event log with INSERT-only database permissions and hash chaining provides tamper-evident audit trails required for regulatory examinations.
- Account freezing on OFAC match is automatic and immediate, with manual review required for unfreezing, prioritizing regulatory compliance over user experience in this scenario.

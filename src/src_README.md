# Reference Implementation Notes

> **PM-authored prototypes** built to validate feasibility, communicate architecture to engineering, benchmark implementation options, and demo to stakeholders. Not production code.

## What These Files Are

Each module implements the core logic for one service in the fintech operations platform. They demonstrate the data structures, algorithms, and business rules that the engineering team will build production versions of.

**What's included:**
- Data models (dataclasses representing ledger entries, transactions, fraud decisions, etc.)
- Core business logic (double-entry validation, PSP routing, fraud scoring, reconciliation matching)
- Edge case handling (partial refunds, chargebacks, holds, circuit breaker state)
- Realistic configuration (thresholds, timeouts, fee structures based on actual vendor terms)

**What's NOT included:**
- Database connections or ORM layer
- Authentication or authorization
- API endpoints or HTTP handling
- Production error handling or logging infrastructure
- Deployment configuration

## How I Used These

1. **Architecture validation:** Before committing to double-entry accounting, I built `ledger_engine.py` to prove that the journal entry model could handle all our transaction types (funding, transfers, refunds, chargebacks, multi-party settlement) without losing a penny.

2. **Engineering communication:** These files were the primary spec for engineering. Instead of describing the fraud scoring algorithm in a PRD, I handed them `fraud_detector.py` with the exact rule definitions, scoring weights, and decision thresholds. The engineering team translated these into production services with proper database backing, API layers, and monitoring.

3. **Stakeholder demos:** The reconciliation engine prototype was used to demonstrate three-way matching to the finance ops team before engineering built the production version. Diana could see exactly how her nightly reconciliation would work and provide feedback on the auto-resolution patterns.

4. **Feasibility benchmarks:** `payment_orchestrator.py` was used to benchmark circuit breaker behavior under simulated PSP failures. The health scoring weights and failover thresholds in the production system are based on experiments run against these prototypes.

## Modules

| File | Service | Key Concepts |
|---|---|---|
| `ledger/ledger_engine.py` | Ledger Service | Double-entry journal entries, account taxonomy, balance calculation, holds, idempotency |
| `payments/payment_orchestrator.py` | Payment Service | Multi-PSP routing, health scoring, circuit breakers, retry with backoff, adapter pattern |
| `settlement/settlement_engine.py` | Settlement Service | Multi-party splits, net batching, holdback reserves, NACHA file generation |
| `fraud/fraud_detector.py` | Fraud Service | Rule engine, feature extraction, weighted scoring, approve/review/decline decisions |
| `reconciliation/reconciliation_engine.py` | Reconciliation Service | Three-way matching, fuzzy matching, auto-resolution, exception queue |
| `compliance/compliance_checker.py` | Compliance Service | KYC tier enforcement, transaction monitoring rules, OFAC screening, SAR triggers |

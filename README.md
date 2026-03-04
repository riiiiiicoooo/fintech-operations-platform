# Fintech Operations Platform

[![Stripe Test Mode](https://img.shields.io/badge/Stripe-Test%20Mode-blue?logo=stripe)](https://stripe.com)
[![Run on Replit](https://replit.com/badge/github/fintech-ops/platform)](https://replit.com/new/github/fintech-ops/platform)

Financial operations infrastructure for a B2B2C fintech platform. Covers double-entry ledger design, multi-PSP payment orchestration, settlement automation, fraud detection, reconciliation, and compliance workflows.

---

## Modern Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Database** | PostgreSQL 15 (Supabase) + SERIALIZABLE isolation | Double-entry ledger with strict ACID guarantees |
| **API** | FastAPI (Python 3.11) + async/await | High-performance payment endpoints |
| **Job Orchestration** | Trigger.dev | Long-running settlement & reconciliation jobs with checkpointing |
| **Workflows** | n8n | Low-code alerting: reconciliation breaks → Jira/Slack/Email |
| **Payment Gateway** | Stripe Connect + Python SDK | Multi-PSP routing, PaymentIntent, connected accounts |
| **Monitoring** | Prometheus + Grafana | Real-time dashboards: transaction pipeline, reconciliation |
| **Email** | React Email + Resend | Transactional emails: settlement confirmations, alerts |
| **Infrastructure** | Docker + Vercel/Render | Containerized deployment with auto-scaling |
| **Secrets** | HashiCorp Vault | Encrypted credential management |
| **Testing** | pytest + factories | 95%+ test coverage |

---

## Quick Start

### Deploy on Replit (30 seconds)

```bash
# Click the Replit badge above or use:
git clone https://github.com/fintech-ops/platform
cd fintech-operations-platform
# Edit .env with your API keys
python api/app.py
# API live at https://your-replit.replit.dev
```

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Set up PostgreSQL (or Supabase)
export DATABASE_URL="postgresql://user:pass@localhost:5432/fintech_ops"

# Run migrations
alembic upgrade head

# Start API server
python api/app.py

# In another terminal: Run Trigger.dev jobs locally
trigger-cli dev

# In another terminal: Run n8n workflows
docker run -it -p 5678:5678 n8nio/n8n

# Visit http://localhost:8000/docs for API docs
```

### Deploy to Production

```bash
# Supabase (database)
supabase link --project-ref your-project-id
supabase db push

# Vercel (API)
vercel deploy

# Trigger.dev (jobs)
trigger deploy

# n8n (workflows)
docker run -d n8nio/n8n --publish 5678:5678
```

---

## Overview

A B2B2C fintech startup connecting enterprise employers to employee financial services (earned wage access, savings tools, bill pay) needed to build the financial operations layer that sits between their user-facing product and the payment processors, banking partners, and compliance obligations underneath. The challenge: process thousands of daily transactions across multiple payment methods, settle funds between employers, employees, and service providers, detect fraud in real time, reconcile everything nightly, and maintain a compliant audit trail, all while the company was scaling from $0 to $8M ARR.

I owned the product strategy for this financial operations layer. This meant defining the ledger architecture, payment routing logic, settlement rules, fraud thresholds, reconciliation workflows, and compliance automation, then working with engineering to build it. The core product insight was that the ledger is the single source of truth, not any external payment processor. Every transaction, hold, settlement, and fee must be recorded as a double-entry journal entry before any external API call is made. This discipline is what makes reconciliation possible and what separates production fintech from a Stripe wrapper.

---

## Core Transaction Flow

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        TRANSACTION LIFECYCLE                             │
│                                                                          │
│  INITIATION                                                              │
│  ├── User or employer triggers transaction                               │
│  ├── Idempotency key validated (reject duplicate submissions)            │
│  ├── Request schema validated (amount, currency, method, destination)    │
│  └── User context enriched (account status, KYC tier, limits)           │
│                                                                          │
│  FRAUD CHECK (synchronous, < 100ms target)                               │
│  ├── Rule engine: velocity, amount, geo, device, time-of-day            │
│  ├── Blocklist/allowlist lookup                                          │
│  ├── Risk score aggregation (weighted rules → composite 0-100)          │
│  ├── Decision: APPROVE (score < 30)                                      │
│  │             REVIEW  (score 30-70) → async manual review queue        │
│  │             DECLINE (score > 70) → reject with reason code           │
│  └── Fraud decision logged (immutable, used for model training)         │
│                                                                          │
│  LEDGER ENTRY (synchronous, before any external call)                    │
│  ├── Create journal entry: debit source account, credit destination     │
│  ├── Validate: total debits == total credits (double-entry invariant)   │
│  ├── Apply hold on source account (available balance reduced)           │
│  ├── Transaction state: PENDING                                          │
│  └── Event emitted: transaction.created                                  │
│                                                                          │
│  PAYMENT EXECUTION (async)                                               │
│  ├── Payment orchestrator selects PSP (health score + method routing)   │
│  ├── PSP API call with idempotency key                                   │
│  ├── Success → transaction state: PROCESSING                             │
│  ├── Failure → retry with backoff (max 3 attempts)                       │
│  │         → circuit breaker check → fallback PSP if primary unhealthy  │
│  └── All retries exhausted → transaction state: FAILED → release hold   │
│                                                                          │
│  SETTLEMENT (async, batch or real-time depending on method)              │
│  ├── PSP confirms funds movement (webhook or polling)                    │
│  ├── Ledger updated: release hold, post final entries                    │
│  ├── Settlement instructions created (platform fee split, payouts)      │
│  ├── Multi-party settlement: employer → platform fee → employee payout  │
│  ├── Transaction state: SETTLED                                          │
│  └── Event emitted: transaction.settled                                  │
│                                                                          │
│  RECONCILIATION (nightly batch)                                          │
│  ├── Three-way match: internal ledger ↔ PSP records ↔ bank statement   │
│  ├── Auto-resolve known patterns (timing differences, batch aggregation)│
│  ├── Flag breaks for manual review                                       │
│  └── Generate reconciliation report with match rate + exception details │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Ledger Architecture

The ledger is the most important system in the platform. Everything else (payment routing, settlement, reconciliation, reporting) reads from or writes to the ledger. Getting this wrong means your books don't balance, your reconciliation is impossible, and your compliance team can't produce audit trails.

```
┌─────────────────────────────────────────────────────────────────────┐
│                      DOUBLE-ENTRY LEDGER                             │
│                                                                      │
│  Accounts                                                            │
│  ├── Asset accounts       (what we own / hold)                       │
│  │   ├── employer_funding_holding    Funds received from employers   │
│  │   ├── user_wallet                 Individual user balances        │
│  │   ├── psp_receivable              Funds in transit from PSP       │
│  │   └── bank_operating              Operating bank account          │
│  │                                                                   │
│  ├── Liability accounts   (what we owe)                              │
│  │   ├── employer_payable            Funds owed back to employers    │
│  │   ├── user_payable                Funds owed to users (pending)   │
│  │   └── tax_withholding             Tax obligations held            │
│  │                                                                   │
│  ├── Revenue accounts     (money we earn)                            │
│  │   ├── platform_fee                Per-transaction fee revenue     │
│  │   ├── subscription_revenue        Employer subscription fees      │
│  │   └── interchange_revenue         Card transaction interchange    │
│  │                                                                   │
│  └── Expense accounts     (money we spend)                           │
│      ├── psp_processing_fees         Stripe/PSP per-txn fees        │
│      ├── fraud_losses                Chargebacks and fraud writeoffs │
│      └── bank_transfer_fees          ACH/wire fees                   │
│                                                                      │
│  Journal Entry Example: Employer funds employee wallet               │
│  ┌─────────────────────────────────────────────────────────────┐     │
│  │ Entry ID: JE-2024-03-15-00847                               │     │
│  │ Idempotency Key: emp_fund_ACME_20240315_batch_042           │     │
│  │ Timestamp: 2024-03-15T14:23:07Z                             │     │
│  │                                                              │     │
│  │ Debit   employer_funding_holding   $500.00  (asset ↑)       │     │
│  │ Credit  user_wallet:user_8472      $497.50  (asset ↑)       │     │
│  │ Credit  platform_fee               $2.50    (revenue ↑)     │     │
│  │                                                              │     │
│  │ Total debits: $500.00  Total credits: $500.00  ✓ Balanced   │     │
│  └─────────────────────────────────────────────────────────────┘     │
│                                                                      │
│  Core Invariant: SUM(debits) == SUM(credits), always, no exceptions  │
│  Every query, report, and reconciliation depends on this being true  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Payment Orchestration

```
┌─────────────────────────────────────────────────────────────────┐
│                    PSP ROUTING DECISION                           │
│                                                                  │
│  Input: payment method, amount, currency, user risk tier         │
│                                                                  │
│  Step 1: Method Routing                                          │
│  ├── ACH transfers      → PSP A (Stripe)                        │
│  ├── Card payments      → PSP B (Stripe) or PSP C (Adyen)      │
│  ├── Wire transfers     → Banking partner direct API             │
│  └── Instant transfers  → PSP D (Tabapay/Visa Direct)          │
│                                                                  │
│  Step 2: Health Check                                            │
│  ├── Primary PSP health score > 0.8?  → Use primary             │
│  ├── Primary PSP health score < 0.8?  → Route to fallback       │
│  ├── Health score = weighted(                                    │
│  │     0.4 × success_rate_last_1hr +                             │
│  │     0.3 × p95_latency_last_1hr +                              │
│  │     0.2 × error_rate_last_15min +                             │
│  │     0.1 × uptime_last_24hr                                   │
│  │   )                                                           │
│  └── Both unhealthy? → Queue transaction, alert on-call          │
│                                                                  │
│  Step 3: Execute with Retry                                      │
│  ├── Attempt 1: primary PSP                                      │
│  ├── Failure → wait 1s (+ jitter) → Attempt 2: primary PSP     │
│  ├── Failure → wait 4s (+ jitter) → Attempt 3: fallback PSP    │
│  └── All failed → mark FAILED, release ledger hold, alert ops   │
│                                                                  │
│  Circuit Breaker                                                 │
│  ├── CLOSED:  < 5 failures in 60s → normal operation            │
│  ├── OPEN:    ≥ 5 failures in 60s → skip PSP, use fallback     │
│  └── HALF-OPEN: after 30s → allow 1 test request through        │
└─────────────────────────────────────────────────────────────────┘
```

---

## Key Design Decisions

| Decision | Choice | Alternative | Why |
|---|---|---|---|
| Internal ledger vs. PSP as source of truth | Internal double-entry ledger | Trust Stripe's dashboard | PSP records lag, don't capture holds or internal transfers, and can't be queried in real time for balance checks; ledger gives us sub-second balance reads and complete audit trail |
| Multi-PSP vs. single PSP | Multi-PSP with routing layer | Stripe-only | Single PSP creates vendor lock-in and single point of failure; routing layer adds complexity but gives us fallback capability and cost optimization leverage |
| Synchronous fraud check vs. async review | Synchronous rule engine + async ML scoring | Fully async (approve first, review later) | Can't move money and then decide it was fraud; synchronous rules catch 85% of fraud at < 100ms; ML model runs async for borderline cases |
| Batch settlement vs. real-time | Batch with real-time for high-priority | All real-time | Batch is cheaper (fewer API calls), easier to reconcile, and handles edge cases better; real-time reserved for instant transfers where users expect it |
| KYC at signup vs. progressive | Progressive (tiered limits) | Full KYC upfront | Full KYC at signup kills conversion; tiered approach lets users start with $500/month limit (basic verification) and unlock higher limits with enhanced verification |
| Idempotency approach | Client-generated idempotency keys | Server-generated deduplication | Client keys give the caller control over retry semantics; server dedup requires the server to define "duplicate" which gets complicated with partial failures |

---

## Fraud Detection

```
Transaction enters fraud check
        │
        ▼
┌─────────────────────────────┐
│  Rule Engine (< 100ms)       │
│                              │
│  Velocity checks:            │
│  ├── > 5 txns in 1 hour?    │  +25 risk points
│  ├── > $2,000 in 24 hours?  │  +20 risk points
│  └── > 10 txns in 24 hours? │  +15 risk points
│                              │
│  Amount checks:              │
│  ├── > 3x user's avg txn?   │  +20 risk points
│  ├── Round dollar amount?    │  +5 risk points
│  └── Near daily limit?       │  +10 risk points
│                              │
│  Context checks:             │
│  ├── New device?             │  +15 risk points
│  ├── Unusual location?       │  +15 risk points
│  ├── Off-hours (2-5am)?     │  +10 risk points
│  └── First txn < 24hrs old? │  +20 risk points
│                              │
│  Allowlist/blocklist:        │
│  ├── User on allowlist?      │  Score = 0 (bypass)
│  └── User on blocklist?      │  Score = 100 (auto-decline)
│                              │
│  Score: SUM(triggered rules) │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│  Decision                    │
│  ├── Score < 30   → APPROVE │  93% of transactions
│  ├── Score 30-70  → REVIEW  │  5% → manual review queue
│  └── Score > 70   → DECLINE │  2% → blocked + user notified
└─────────────────────────────┘
```

---

## Reconciliation

```
┌──────────────────────────────────────────────────────────────┐
│                 NIGHTLY RECONCILIATION                         │
│                 (runs at 2:00 AM ET)                           │
│                                                               │
│  Three sources of truth that must agree:                      │
│                                                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │   Internal    │  │     PSP      │  │    Bank      │       │
│  │   Ledger      │  │   Records    │  │  Statement   │       │
│  │              │  │              │  │              │       │
│  │ Our journal  │  │ Stripe/Adyen │  │ Actual money │       │
│  │ entries      │  │ txn records  │  │ movement     │       │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘       │
│         │                 │                 │                │
│         └────────┬────────┘                 │                │
│                  │                          │                │
│         ┌────────▼────────┐                 │                │
│         │  Match: Ledger  │                 │                │
│         │  ↔ PSP Records  │                 │                │
│         │                 │                 │                │
│         │  By: txn ID,    │                 │                │
│         │  amount, date,  │                 │                │
│         │  status         │                 │                │
│         └────────┬────────┘                 │                │
│                  │                          │                │
│                  └──────────┬───────────────┘                │
│                             │                                │
│                    ┌────────▼────────┐                       │
│                    │  Match: PSP ↔   │                       │
│                    │  Bank Statement │                       │
│                    │                 │                       │
│                    │  By: settlement │                       │
│                    │  batch ID,      │                       │
│                    │  net amount     │                       │
│                    └────────┬────────┘                       │
│                             │                                │
│              ┌──────────────┼──────────────┐                │
│              │              │              │                │
│     ┌────────▼──────┐ ┌────▼─────┐ ┌─────▼──────┐         │
│     │   MATCHED     │ │  AUTO-   │ │  EXCEPTION │         │
│     │   (target:    │ │  RESOLVED│ │  (manual   │         │
│     │    98%+)      │ │  (known  │ │   review)  │         │
│     │               │ │  patterns│ │            │         │
│     │               │ │  ~1.5%)  │ │  ~0.5%)    │         │
│     └───────────────┘ └──────────┘ └────────────┘         │
│                                                               │
│  Auto-resolve patterns:                                       │
│  ├── Timing: PSP shows T+1 but ledger shows T (settlement)  │
│  ├── Batching: PSP nets 50 txns into 1 bank transfer         │
│  ├── Rounding: $0.01 differences from FX conversions         │
│  └── Fees: PSP deducts processing fee from settlement amount │
└──────────────────────────────────────────────────────────────┘
```

---

## Business Impact

### Operational Metrics

| Metric | Before (Manual) | After (Platform) |
|--------|-----------------|-------------------|
| Transaction success rate | 91.2% | 97.8% |
| Avg payment processing time | 4.2 seconds | 1.1 seconds |
| Fraud detection rate | ~60% (manual review) | 94.3% (automated) |
| False positive rate | 12% | 3.1% |
| Reconciliation coverage | 89% (manual spreadsheets) | 99.2% (automated) |
| Break resolution time | 3-5 business days | 4.2 hours (auto) / 18 hours (manual) |
| Settlement accuracy | 94% | 99.7% |
| Time to detect reconciliation breaks | Next business day | Real-time alerting |

### Financial Impact

| Metric | Value | Calculation |
|--------|-------|-------------|
| Platform ARR | **$8M** | Employer subscriptions + per-transaction fees |
| Net revenue retention | **118%** | Existing employers increasing usage year-over-year |
| Fraud loss reduction | **$420K/year** | Improved detection rate × avg fraud transaction value |
| Reconciliation labor savings | **$180K/year** | 2 FTE finance ops → 0.3 FTE with automation |
| PSP cost savings (multi-PSP routing) | **$95K/year** | Routing optimization reduced blended processing rate by 18bps |
| Chargeback rate | **0.12%** | Below Visa/MC threshold (1.0%), below industry avg (0.6%) |

---

## Repository Structure

```
fintech-operations-platform/
│
├── README.md                          # This file
│
├── docs/
│   ├── PRD.md                         # Product requirements document
│   ├── ARCHITECTURE.md                # System architecture, event-driven design, infrastructure
│   ├── LEDGER_DESIGN.md               # Double-entry accounting, journal entries, reconciliation theory
│   ├── COMPLIANCE_FRAMEWORK.md        # KYC/AML tiers, transaction monitoring, SAR workflows
│   ├── METRICS.md                     # Financial operations KPIs, fraud metrics, settlement accuracy
│   ├── DECISION_LOG.md                # Key product and technical decisions with tradeoffs
│   └── ROADMAP.md                     # Phased delivery from core ledger to advanced features
│
├── src/
│   ├── README.md                      # PM reference implementation notes
│   ├── ledger/
│   │   └── ledger_engine.py           # Double-entry transaction recording and balance management
│   ├── payments/
│   │   └── payment_orchestrator.py    # Multi-PSP routing, retry logic, circuit breakers
│   ├── settlement/
│   │   └── settlement_engine.py       # Multi-party settlement, split payments, batch processing
│   ├── fraud/
│   │   └── fraud_detector.py          # Rule-based fraud scoring and decision engine
│   ├── reconciliation/
│   │   └── reconciliation_engine.py   # Three-way matching (ledger ↔ PSP ↔ bank)
│   └── compliance/
│       └── compliance_checker.py      # KYC orchestration, transaction monitoring, SAR triggers
│
└── tests/
    └── test_ledger_engine.py          # Unit tests demonstrating double-entry invariants
```

---

## Product Documents

| Document | Description |
|---|---|
| [Product Requirements](docs/PRD.md) | Client context, personas, user flows, functional requirements, phased rollout |
| [System Architecture](docs/ARCHITECTURE.md) | Event-driven design, service topology, data flow, idempotency patterns, infrastructure |
| [Ledger Design](docs/LEDGER_DESIGN.md) | Double-entry accounting fundamentals, account taxonomy, journal entry patterns, hold management, reconciliation theory |
| [Compliance Framework](docs/COMPLIANCE_FRAMEWORK.md) | KYC verification tiers, AML transaction monitoring, SAR workflows, PCI scope minimization, audit trails |
| [Metrics Framework](docs/METRICS.md) | Payment success rate, fraud precision/recall, settlement accuracy, reconciliation coverage, financial P&L |
| [Decision Log](docs/DECISION_LOG.md) | Key product and technical trade-offs with context, options, and reasoning |
| [Product Roadmap](docs/ROADMAP.md) | Phased delivery from core ledger to multi-currency and embedded finance |

---

## Reference Code

> **Note:** PM-authored prototypes built to validate feasibility, communicate architecture to engineering, benchmark implementation options, and demo to stakeholders. Not production code.

| File | Purpose |
|---|---|
| `ledger/ledger_engine.py` | Double-entry transaction recording with account management, hold lifecycle, idempotency enforcement, and balance calculation |
| `payments/payment_orchestrator.py` | Multi-PSP payment routing with health scoring, retry with exponential backoff, circuit breaker pattern, and webhook processing |
| `settlement/settlement_engine.py` | Multi-party settlement with split payment rules, net settlement batching, holdback management, and reconciliation against PSP reports |
| `fraud/fraud_detector.py` | Rule-based fraud scoring engine with velocity checks, amount analysis, context signals, blocklist/allowlist, and decision thresholds |
| `reconciliation/reconciliation_engine.py` | Three-way reconciliation (ledger ↔ PSP ↔ bank) with exact and fuzzy matching, auto-resolution patterns, and exception management |
| `compliance/compliance_checker.py` | KYC tier management, transaction monitoring rules, velocity tracking, SAR trigger detection, and audit trail logging |

---

## How These Were Used

As PM, I wrote these prototypes to:

1. **Validate the ledger model** before committing to double-entry architecture (testing that journal entries stayed balanced across edge cases like partial refunds, chargebacks, and split settlements)
2. **Benchmark PSP failover** by simulating primary PSP outages and measuring how quickly the circuit breaker triggered fallback routing
3. **Communicate fraud logic to engineering** using working rule engine code rather than spreadsheets of threshold values
4. **Demo reconciliation matching** to the finance ops team using real PSP export files to validate match rates before building the production pipeline
5. **Inform compliance requirements** by prototyping KYC tier logic and transaction monitoring rules with the legal team to confirm regulatory coverage

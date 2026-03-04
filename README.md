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

## Business Context

B2B2C fintech platforms processing $10M-$500M in annual transaction volume represent a $4.1B market for payment infrastructure (McKinsey Payments Report). Approximately 3,200 US fintechs at this growth stage need custom payment orchestration beyond basic Stripe integration to handle multi-party settlement, fraud detection, and regulatory compliance.

| Metric | Before Platform | After Platform | Per $100M Volume |
|--------|-----------------|-----------------|-----------------|
| Transaction Success Rate | 91.2% × $50 avg | 97.8% × $50 avg | +$3.3M/year |
| Fraud Loss (Chargebacks) | $890K/year | - | Reduced |
| **Platform Cost** | - | $345,000 build + $1,400/mo | **Payback: 6 weeks** |
| **3-Year ROI** | - | - | **28x** |

If productized at $2,000-10,000/month based on transaction volume tiers + 0.05% of processed volume, targeting $15-25M ARR.

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
│   ├── README.md                      # Project overview and documentation
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

## PM Perspective

**Hardest decision: Double-entry ledger vs. single-entry with reconciliation.** The CTO wanted single-entry—faster to build, less architectural complexity. I insisted on double-entry after studying Stripe's and Modern Treasury's architectures. The killer argument: in a PSP failover scenario (primary Stripe → fallback Adyen), single-entry systems lose the audit trail. The double-entry journal with immutable entries meant we could reconstruct every cent's movement across providers. This mattered when we had our first Stripe outage and needed to failover 15,000 in-flight transactions without losing money or audit trail. Added 4 weeks to Phase 2 but prevented what would have been a reconciliation nightmare.

**Surprise: Fraud detection baseline wasn't bad rules—it was bad data.** The existing 60% detection rate seemed like a starting point for improvement, but investigation showed the rules were actually solid. The real issue: velocity checking (transactions per hour) ignored timezone. A legitimate user making purchases at 2am EST looked identical to a stolen card being tested at 2am from Eastern Europe. Adding device fingerprint, behavioral context (purchase history, typical amounts), and timing normalization got us from 60% to 94.3% without fundamentally changing rule logic—we just gave the rules better signals to work with.

**Would do differently: Prioritize reconciliation engine over fraud detection.** Fraud was exciting to stakeholders and felt like a "forward-looking" feature. But reconciliation was where the finance team was bleeding hours—manually matching transactions across ledger, PSP, and bank. We built fraud first, which was a mistake. The reconciliation engine had better measured ROI (10+ hours per week saved), and it unblocked scaling to higher transaction volumes. Lesson: measure actual pain relief, not feature excitement.

---

## Engagement & Budget

### Team & Timeline

| Role | Allocation | Duration |
|------|-----------|----------|
| Lead PM (Jacob) | 25 hrs/week | 20 weeks |
| Lead Developer (US) | 40 hrs/week | 20 weeks |
| Offshore Developer(s) | 3 × 35 hrs/week | 20 weeks |
| QA Engineer | 25 hrs/week | 20 weeks |

**Timeline:** 20 weeks total across 3 phases
- **Phase 1: Discovery & Design** (4 weeks) — Payment flow mapping, ledger architecture, PSP integration requirements, compliance (PCI DSS, KYB), fraud rule definition
- **Phase 2: Core Build** (11 weeks) — Double-entry ledger engine, payment orchestrator, multi-PSP routing, settlement reconciliation, fraud detection pipeline
- **Phase 3: Integration & Launch** (5 weeks) — Stripe Connect integration, three-way reconciliation testing, load testing (10K txn/day), compliance audit prep, staged production rollout

### Budget Summary

| Category | Cost | Notes |
|----------|------|-------|
| PM & Strategy | $92,500 | Discovery, specs, stakeholder management |
| Development (Lead + Offshore) | $232,800 | Core platform build |
| QA | $17,500 | Quality assurance and testing |
| AI/LLM Token Budget | $280/month | Minimal AI — fraud scoring uses scikit-learn not LLMs, some Claude Haiku for pattern summarization ~2M tokens/month |
| Infrastructure | $890/month | Supabase Pro $25 + Temporal Cloud $200 + Redis $65 + n8n $50 + Trigger.dev $25 + AWS (RDS, compute, S3) $350 + Grafana $50 + misc $125 |
| **Total Engagement** | **$345,000** | Fixed-price, phases billed at milestones |
| **Ongoing Run Rate** | **$1,400/month** | Infrastructure + AI tokens + support + Stripe Connect fees (0.25% + $0.25 per payout, variable) |

---

## About This Project

This repository documents a product I built as **Lead Product Manager** at Ampersand Consulting for a B2B2C fintech platform scaling to $8M ARR that needed production-grade payment orchestration, ledger architecture, and fraud detection. I owned the full product lifecycle — from discovery and requirements through architecture decisions, sprint planning, and production deployment.

**My role included:**
- Led discovery with payments, risk, and finance teams to map transaction flows and reconciliation pain points
- Designed the double-entry ledger architecture and multi-PSP payment orchestration strategy
- Made build-vs-buy decisions on fraud detection, choosing custom rule engine over Sift/Sardine for cost and customization
- Defined reconciliation framework (ledger ↔ PSP ↔ bank) and exception handling workflows

**Note:** Client-identifying details have been anonymized. Code represents the architecture and design decisions I drove; production deployments were managed by client engineering teams.

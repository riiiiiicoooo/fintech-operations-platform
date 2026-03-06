# Fintech Operations Platform -- Improvements & Technology Roadmap

## Product Overview

The Fintech Operations Platform is a **B2B earned wage access (EWA) infrastructure system** that enables employers to fund employee wallets and employees to access earned wages before payday. The platform handles the full transaction lifecycle:

1. **Employer Funding** -- Employers deposit lump sums into holding accounts via bank transfer
2. **Wallet Allocation** -- Funds are distributed to individual employee wallets with platform fee extraction
3. **Employee Transfers** -- Workers initiate ACH, card, wire, or instant push-to-debit transfers of earned wages
4. **Fraud Detection** -- Every transaction is scored by a rule-based engine with approve/review/decline decisions
5. **Payment Orchestration** -- Multi-PSP routing (Stripe, Adyen, Tabapay) with health scoring and circuit breakers
6. **Settlement** -- Daily batch processing, multi-party split calculations, NACHA file generation, and bank submission
7. **Reconciliation** -- Nightly three-way matching (ledger vs. PSP vs. bank) with auto-resolution of known break patterns
8. **Compliance** -- Progressive KYC tier enforcement, BSA/AML transaction monitoring, OFAC sanctions screening, and SAR trigger identification

The system is designed around a **double-entry accounting ledger** where every transaction creates balanced journal entries (debits always equal credits), ensuring financial integrity and full auditability.

---

## Current Architecture

### Tech Stack

| Layer | Technology | Version | Purpose |
|---|---|---|---|
| **API Framework** | FastAPI | 0.115.0 | REST API endpoints |
| **ASGI Server** | Uvicorn | 0.30.0 | HTTP server |
| **Validation** | Pydantic | 2.10.0 | Request/response models |
| **ORM** | SQLAlchemy | 2.0.36 | Database access |
| **Database** | PostgreSQL | 16 (Alpine) | Primary data store |
| **Cache/Queue** | Redis | 7 (Alpine) | Caching, idempotency keys |
| **Migrations** | Alembic | 1.14.0 | Schema migrations |
| **Auth** | PyJWT | 2.8.1 | JWT authentication |
| **Monitoring** | Prometheus | 0.19.0 | Metrics collection |
| **Dashboards** | Grafana | N/A | Transaction and reconciliation dashboards |
| **Job Orchestration** | Trigger.dev | N/A | Settlement batch and reconciliation jobs (TypeScript) |
| **Workflow Automation** | n8n | N/A | Reconciliation alerting workflows |
| **Email** | React Email + Resend | N/A | Transactional email templates (TSX) |
| **Hosting** | Vercel + Docker | N/A | API deployment + containerization |
| **Secrets** | HashiCorp Vault | N/A | Secrets management |
| **Notifications** | Slack + Jira | N/A | Alerting and ticket creation |
| **Linting/Types** | Ruff + mypy | 0.8.0 / 1.13.0 | Code quality |

### Key Components

**Core Engine Files (`src/`):**
- `ledger/ledger_engine.py` -- Double-entry journal engine with account model, hold management, balance calculation, and idempotency
- `payments/payment_orchestrator.py` -- Multi-PSP routing with circuit breakers, health scoring, exponential backoff retries
- `settlement/settlement_engine.py` -- Multi-party split calculation, net position aggregation, NACHA summary generation
- `settlement/nacha_generator.py` -- Full NACHA ACH file format generator with proper record types (1/5/6/8/9)
- `fraud/fraud_detector.py` -- Nine configurable fraud rules with weighted scoring, blocklist/allowlist, and decision logging
- `reconciliation/reconciliation_engine.py` -- Five-phase matching pipeline (exact, fuzzy, many-to-one, auto-resolve, exception)
- `compliance/compliance_checker.py` -- KYC tier enforcement, five transaction monitoring rules, OFAC screening with Jaro-Winkler similarity

**API Layer (`api/`):**
- `models.py` -- Pydantic models for transactions, balances, reconciliation, compliance, settlement, health checks, and error responses

**Database (`schema/`):**
- `schema.sql` -- Full PostgreSQL schema with 15+ tables, CHECK constraints, B-tree/GIN/partial indexes, and RLS-ready design
- `migrations/` -- Three migration files covering ledger, fraud detection, and reconciliation tables

**Infrastructure:**
- `docker-compose.yml` -- PostgreSQL 16, Redis 7, FastAPI service with health checks
- `trigger-jobs/` -- TypeScript jobs for daily settlement batch processing and nightly reconciliation
- `n8n/` -- JSON workflow definitions for reconciliation alerting (webhook -> Jira -> Slack -> email)
- `grafana/` -- Dashboard JSON configs for transaction pipeline and reconciliation monitoring
- `emails/` -- React Email templates for settlement confirmations and reconciliation alerts

### Architecture Patterns

- **Immutable Append-Only Ledger** -- Journal entries are never updated, only new corrective entries are created
- **Idempotency** -- All write operations use client-provided idempotency keys to enable safe retries
- **Circuit Breaker** -- PSP adapters use a state machine (CLOSED -> OPEN -> HALF_OPEN) to prevent cascading failures
- **Health-Score Routing** -- Weighted scoring (success rate, p95 latency, error rate, uptime) determines PSP selection order
- **SERIALIZABLE Isolation** -- Critical ledger operations use PostgreSQL SERIALIZABLE transactions
- **Hold-Based Authorization** -- Funds are held (reserved) during async settlement to prevent double-spend
- **Three-Way Reconciliation** -- Ledger, PSP, and bank records are independently verified against each other

---

## Recommended Improvements

### 1. Replace Float Arithmetic with Decimal for Financial Calculations

**Problem:** The ledger engine (`src/ledger/ledger_engine.py`) uses Python `float` for all monetary amounts. Floating-point arithmetic introduces rounding errors that can accumulate and cause balance discrepancies.

**Current code (`ledger_engine.py`, lines 111-112):**
```python
@dataclass
class JournalEntryLine:
    account: Account
    debit: float = 0.0
    credit: float = 0.0
```

**Impact:** The balance verification tolerance at line 141 (`abs(total_debits - total_credits) > 0.001`) masks potential drift. Over millions of transactions, sub-cent errors compound.

**Fix:** Replace all `float` monetary fields with `Decimal` from Python's `decimal` module. The settlement engine's Trigger.dev job (`trigger-jobs/settlement_batch.ts`) already uses `Decimal` via `decimal.js`, creating an inconsistency.

```python
from decimal import Decimal, ROUND_HALF_UP

@dataclass
class JournalEntryLine:
    account: Account
    debit: Decimal = Decimal("0.00")
    credit: Decimal = Decimal("0.00")
```

**Affected files:**
- `src/ledger/ledger_engine.py` -- All amount fields and calculations
- `src/settlement/settlement_engine.py` -- Split calculations, fee computations
- `src/fraud/fraud_detector.py` -- Amount thresholds and comparisons
- `src/reconciliation/reconciliation_engine.py` -- Delta calculations
- `src/compliance/compliance_checker.py` -- Limit comparisons
- `api/models.py` -- Response model amount fields

### 2. Add Async Database Operations

**Problem:** All database and PSP interactions are synchronous. The `payment_orchestrator.py` uses `time.sleep()` for retry backoff (line 435), blocking the event loop.

**Fix:** Leverage FastAPI's native async support with `asyncpg` (async PostgreSQL driver) and `aioredis`. Convert PSP adapter calls to async.

```python
# Before (blocking)
time.sleep((delay_ms + jitter_ms) / 1000)

# After (non-blocking)
await asyncio.sleep((delay_ms + jitter_ms) / 1000)
```

**New dependencies:**
- `asyncpg` >= 0.29.0 -- Async PostgreSQL driver
- `sqlalchemy[asyncio]` -- Already supported in SQLAlchemy 2.0.36 (currently installed)
- `redis[hiredis]` >= 5.2.0 -- Async Redis with C parser acceleration

### 3. Implement Event Sourcing for Ledger Operations

**Problem:** The current ledger stores final state in journal entries but does not capture the full command/event chain. Reversals and corrections create new entries but the causal chain between them is loosely coupled via `reference_type`/`reference_id` (lines 129-130).

**Fix:** Introduce an event store alongside the journal. Each operation emits domain events (e.g., `FundingRequested`, `FundingApproved`, `TransferInitiated`, `TransferSettled`) that can be replayed to reconstruct state.

**Benefits:**
- Complete audit trail with causality
- Enables CQRS (Command Query Responsibility Segregation) for read-optimized views
- Simplifies regulatory reporting -- events map directly to compliance narratives
- Enables temporal queries ("what was the balance at time T?")

### 4. Add Real Database-Backed State Instead of In-Memory Storage

**Problem:** All engines use in-memory Python data structures (dicts, lists). For example, `LedgerEngine.__init__` (line 189-193):

```python
def __init__(self):
    self.accounts: dict[str, Account] = {}
    self.journal_entries: list[JournalEntry] = []
    self.holds: list[Hold] = []
    self.idempotency_keys: dict[str, JournalEntry] = {}
```

While this is noted as "not production code," the schema in `schema/schema.sql` is already defined but no repository layer connects the engines to the database.

**Fix:** Create a repository pattern layer:
- `src/repositories/ledger_repository.py`
- `src/repositories/fraud_repository.py`
- `src/repositories/settlement_repository.py`
- `src/repositories/reconciliation_repository.py`
- `src/repositories/compliance_repository.py`

Use SQLAlchemy 2.0 mapped classes with the existing schema and inject repositories into engines via dependency injection.

### 5. Improve Fraud Detection with ML-Based Scoring

**Problem:** The fraud detector (`src/fraud/fraud_detector.py`) uses a static rule engine with hardcoded thresholds (e.g., `HighAmountRule` triggers at 3x average, `VelocityRule` at 5 transactions per 24 hours). This produces high false-positive rates and cannot adapt to evolving fraud patterns.

**Fix (phased):**
1. **Phase 1 -- Feature Store:** Extract the current `FraudFeatures` into a proper feature store. Log all features with decisions to create training data (the `FraudResult` dataclass at line 74 already stores this -- formalize the pipeline).
2. **Phase 2 -- Gradient Boosted Model:** Train an XGBoost or LightGBM model on historical fraud decisions. Keep the rule engine as a fallback/override layer.
3. **Phase 3 -- Real-Time Feature Engineering:** Use a streaming feature computation framework for real-time aggregations (velocity windows, behavioral profiles).

### 6. Add API Rate Limiting and Request Validation

**Problem:** The `.env.example` defines `RATE_LIMIT_REQUESTS=1000` and `RATE_LIMIT_WINDOW_MINUTES=1` but there is no rate limiting middleware in the API layer.

**Fix:** Add `slowapi` or implement Redis-based rate limiting:

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

@app.post("/transactions")
@limiter.limit("100/minute")
async def create_transaction(request: Request, body: TransactionRequest):
    ...
```

### 7. Add Structured Logging and Distributed Tracing

**Problem:** No structured logging framework is configured. The engines use no logging at all -- they raise exceptions but do not log operations, making production debugging difficult.

**Fix:**
- Add `structlog` for structured JSON logging with correlation IDs
- Add OpenTelemetry for distributed tracing across services (API -> Ledger -> PSP -> Settlement)
- Integrate with the existing Grafana dashboards via Loki (logs) and Tempo (traces)

### 8. Strengthen Test Coverage

**Problem:** Tests exist for ledger, fraud, reconciliation, and settlement engines but there are no integration tests, no API endpoint tests, and no load/performance tests.

**Fix:**
- Add `httpx` + `pytest` fixtures for FastAPI endpoint testing
- Add property-based testing with `hypothesis` for ledger invariants (debits always equal credits under random operations)
- Add load testing with `locust` targeting the transaction creation endpoint
- Add contract tests for PSP adapter interfaces

### 9. Add Database Connection Pooling Configuration

**Problem:** The `.env.example` defines pool settings (`DB_POOL_SIZE=10`, `DB_MAX_OVERFLOW=20`) but these are not consumed by the application. The `docker-compose.yml` sets `max_connections=100` on PostgreSQL but the API service does not configure its pool.

**Fix:** Configure SQLAlchemy async engine with proper pooling:

```python
from sqlalchemy.ext.asyncio import create_async_engine

engine = create_async_engine(
    DATABASE_URL,
    pool_size=int(os.getenv("DB_POOL_SIZE", 10)),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", 20)),
    pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", 30)),
    pool_pre_ping=True,  # detect stale connections
)
```

### 10. Implement Proper Secret Rotation

**Problem:** Vault integration is mentioned in `.env.example` but not implemented. API keys, database passwords, and JWT secrets are stored as environment variables with no rotation mechanism.

**Fix:** Implement a Vault client wrapper that fetches secrets at startup and rotates them on a configurable schedule. For the Stripe webhook secret, implement a dual-secret verification window to allow zero-downtime rotation.

---

## New Technologies & Trends

### Real-Time Payments Infrastructure

**FedNow and RTP Network Integration**

The Federal Reserve's FedNow Service (launched July 2023) and The Clearing House's RTP (Real-Time Payments) network now process instant payments 24/7/365. For an EWA platform, integrating these rails eliminates the ACH settlement delay (currently T+1 to T+2).

- **FedNow ISO 20022 Messaging:** Replace NACHA batch files with ISO 20022 XML messages for real-time settlement. The current `nacha_generator.py` would be supplemented (not replaced) with an ISO 20022 message builder.
- **Stripe Treasury:** Stripe now offers [Stripe Treasury](https://stripe.com/treasury) for embedded banking, which includes FedNow access. Since the platform already uses Stripe as a primary PSP, this is a natural integration path.
- **Column (column.com):** A developer-focused banking platform offering direct FedNow and ACH APIs, well-suited for fintech infrastructure projects.

**Recommendation:** Add a `FedNowAdapter` to the payment orchestrator alongside existing PSP adapters. Route instant transfers through FedNow instead of push-to-debit (currently using Tabapay), reducing costs from 1.5% to flat per-transaction pricing.

References:
- FedNow Service: https://www.frbservices.org/financial-services/fednow
- ISO 20022 adoption: https://www.iso20022.org
- Stripe Treasury: https://stripe.com/treasury

### ML-Powered Fraud Detection

**Graph Neural Networks (GNNs) for Transaction Fraud**

Modern fraud detection has moved beyond rule-based and tabular ML to graph-based approaches that model relationships between entities (users, devices, IPs, merchants).

- **PyTorch Geometric (pyg)** >= 2.5 -- Provides GNN layers (GAT, GraphSAGE) that can model transaction networks. Fraudulent accounts often form clusters that are invisible to per-transaction rules.
- **LightGBM** >= 4.3 -- For tabular feature-based scoring as a baseline model. Drop-in replacement for the current rule engine with dramatically better precision/recall.
- **ONNX Runtime** >= 1.17 -- Export trained models to ONNX for sub-10ms inference in the transaction path. The current fraud detector targets < 50ms for rule evaluation; an ONNX model can achieve < 5ms.
- **Feast** >= 0.38 -- Feature store for managing real-time and batch features. Replaces the ad-hoc `FraudFeatures` dataclass with a versioned, production-grade feature pipeline.

**How it applies:** The current `FraudDetector` (9 rules, ~142 max weight) would become a two-stage system:
1. **Fast path:** ONNX model inference (< 5ms) produces a base score
2. **Rule overlay:** Existing rules act as overrides (e.g., blocklist always declines, compliance rules always apply)

References:
- PyTorch Geometric: https://pyg.org
- LightGBM: https://github.com/microsoft/LightGBM
- ONNX Runtime: https://onnxruntime.ai
- Feast Feature Store: https://feast.dev

### Compliance Automation

**RegTech Platforms and APIs**

- **Alloy (alloy.com):** Identity verification and compliance orchestration platform. The `compliance_checker.py` references Alloy in comments (line 55: "government ID + database check (Alloy)") but has no actual integration. Alloy provides a single API for KYC/KYB, document verification, transaction monitoring, and adverse media screening.
- **Sardine (sardine.ai):** Device intelligence and fraud prevention API that provides device fingerprinting, behavioral biometrics, and real-time risk scoring. Would enhance the current `TransactionContext` with richer device signals.
- **Unit21 (unit21.ai):** Transaction monitoring and case management platform that can replace the custom monitoring rules in `compliance_checker.py` with a configurable, no-code rule builder plus ML-based alert prioritization.
- **Dow Jones Risk & Compliance:** Enterprise-grade sanctions screening API that would replace the manual OFAC list in `compliance_checker.py` with a continuously updated SDN/consolidated list, plus PEP and adverse media screening.

**OFAC Screening Improvement:** The current Jaro-Winkler implementation (lines 459-513 of `compliance_checker.py`) is a basic string similarity algorithm. Production sanctions screening requires:
- Transliteration handling (Arabic/Cyrillic name variants)
- Multi-token name matching (word-level permutations)
- Alias and AKA expansion
- Country/DOB/ID number corroboration

References:
- Alloy: https://www.alloy.com
- Sardine: https://www.sardine.ai
- Unit21: https://www.unit21.ai

### Observability Stack

**OpenTelemetry-Native Monitoring**

The current stack uses Prometheus for metrics and Grafana for dashboards. The modern observability approach unifies metrics, logs, and traces under OpenTelemetry.

- **OpenTelemetry Python SDK** (`opentelemetry-sdk` >= 1.24) -- Auto-instrumentation for FastAPI, SQLAlchemy, Redis, and HTTP clients. Provides distributed tracing across the entire transaction lifecycle.
- **Grafana Alloy** (formerly Grafana Agent) -- Unified telemetry collector that ships metrics to Prometheus, logs to Loki, and traces to Tempo. Single binary deployment.
- **Grafana Loki** >= 3.0 -- Log aggregation optimized for Grafana. Structured logs from `structlog` can be queried alongside dashboards.
- **Grafana Tempo** >= 2.4 -- Distributed tracing backend. Trace a single transaction from API request through fraud detection, PSP call, ledger posting, and settlement.

**How it applies:** Add trace context propagation to every engine method. A single transaction ID creates a trace that spans:
```
API Request -> Fraud Evaluation -> Compliance Check -> Ledger Post -> PSP Call -> Hold Creation
```

References:
- OpenTelemetry Python: https://opentelemetry.io/docs/languages/python/
- Grafana Alloy: https://grafana.com/docs/alloy/
- Grafana Loki: https://grafana.com/oss/loki/
- Grafana Tempo: https://grafana.com/oss/tempo/

### Data Quality and Reconciliation

**Automated Data Quality Frameworks**

- **Great Expectations** >= 1.0 -- Data quality validation framework. Define expectations for ledger data (e.g., "debits always equal credits per entry," "no negative balances," "all idempotency keys are unique") and run them as automated checks.
- **dbt (data build tool)** >= 1.8 -- The project already has a `dbt/` directory. Use dbt tests and macros to validate reconciliation data quality, build materialized views for balance reporting, and enforce referential integrity across the data warehouse.
- **Soda** >= 3.3 -- Data quality monitoring with anomaly detection. Set up automated checks for reconciliation match rate degradation, unusual settlement volumes, and data freshness SLA violations.

**How it applies:** The reconciliation engine (`reconciliation_engine.py`) manually validates matches. Great Expectations can codify these as reusable test suites:

```python
# Example expectation suite for reconciliation
expect_column_values_to_be_between("delta_amount", min_value=-5.00, max_value=5.00)
expect_column_pair_values_to_be_equal("ledger_amount", "psp_amount", mostly=0.98)
expect_column_values_to_not_be_null("match_status")
```

References:
- Great Expectations: https://greatexpectations.io
- dbt: https://www.getdbt.com
- Soda: https://www.soda.io

### Event-Driven Architecture

**Apache Kafka / Redpanda for Event Streaming**

The current architecture uses request-response patterns for everything. Settlement and reconciliation run as batch jobs. Moving to an event-driven architecture enables real-time processing.

- **Redpanda** >= 24.1 -- Kafka-compatible streaming platform written in C++. Lower latency and simpler operations than Kafka. Single binary, no ZooKeeper dependency.
- **Confluent Schema Registry** -- Manage Avro/Protobuf schemas for ledger events, ensuring backward compatibility as the event model evolves.
- **Faust** >= 0.11 (or `faust-streaming` fork >= 0.11) -- Python stream processing library (Kafka Streams equivalent). Process ledger events in real-time for fraud detection velocity windows, compliance monitoring, and live balance updates.

**Event-driven transaction flow:**
```
TransactionCreated -> FraudEvaluated -> ComplianceChecked -> LedgerPosted -> PSPSubmitted -> Settled -> Reconciled
```

Each stage publishes an event that triggers the next, with full replay capability.

References:
- Redpanda: https://redpanda.com
- Faust Streaming: https://github.com/faust-streaming/faust

### Infrastructure Modernization

**Container Orchestration and CI/CD**

- **Kubernetes with Helm Charts:** The current `docker-compose.yml` is development-only. Production deployment should use Kubernetes with separate deployments for API, worker (settlement/reconciliation jobs), and cron jobs.
- **GitHub Actions / GitLab CI:** Automate testing, linting (Ruff + mypy are already configured), and deployment pipelines.
- **Terraform / Pulumi:** Infrastructure as code for provisioning PostgreSQL (Supabase or AWS RDS), Redis (ElastiCache), and Vault.
- **Distroless Container Images:** Replace `python:3.11-slim` with Google's distroless Python image to minimize attack surface. The current Dockerfile installs `gcc` and `postgresql-client` which should not be in production images (use multi-stage builds).

### API Design

**GraphQL for Dashboard Queries**

The current REST API serves both transactional operations and dashboard/reporting queries. These have different access patterns:
- Transactions: Simple writes, single-resource reads
- Dashboards: Complex multi-resource queries with filtering, aggregation, and pagination

- **Strawberry GraphQL** >= 0.227 -- Type-safe GraphQL library for Python with native FastAPI integration. Define a schema for dashboard queries while keeping REST for transactional operations.

**gRPC for Internal Service Communication**

If the platform evolves to microservices, use gRPC for internal communication:
- **grpcio** >= 1.62 -- Python gRPC implementation
- **buf** -- Protobuf schema management
- Benefits: Type-safe contracts, streaming support, 10x lower latency than REST for internal calls

---

## Priority Roadmap

### P0 -- Critical (Do First, Immediate Impact)

| # | Improvement | Effort | Impact | Files Affected |
|---|---|---|---|---|
| 1 | **Replace float with Decimal** for all monetary calculations | Medium | Prevents financial rounding errors that can cause ledger imbalances and regulatory issues | `ledger_engine.py`, `settlement_engine.py`, `fraud_detector.py`, `reconciliation_engine.py`, `compliance_checker.py`, `api/models.py` |
| 2 | **Add repository layer** connecting engines to PostgreSQL | High | Enables the platform to actually persist data using the already-defined `schema.sql` | New `src/repositories/` directory, all engine files |
| 3 | **Add structured logging** with correlation IDs | Low | Essential for production debugging and incident response | All engine files, API layer |
| 4 | **Implement API rate limiting** | Low | Prevents abuse and DoS; config already exists in `.env.example` but is not wired up | API layer |
| 5 | **Fix Dockerfile for production** -- multi-stage build, remove dev tools | Low | Reduces container image size by approximately 60% and removes attack surface | `Dockerfile` |

### P1 -- High Priority (Next Quarter)

| # | Improvement | Effort | Impact | Dependencies |
|---|---|---|---|---|
| 6 | **Async database operations** with `asyncpg` + SQLAlchemy async | High | Enables concurrent PSP calls and non-blocking I/O, 3-5x throughput improvement | P0-2 (repository layer) |
| 7 | **OpenTelemetry distributed tracing** | Medium | End-to-end visibility into transaction lifecycle; critical for SLA monitoring | P0-3 (structured logging) |
| 8 | **ML fraud scoring model** (LightGBM + ONNX) | High | Reduces false positive rate by estimated 40-60% vs. static rules; current rule engine becomes fallback | P0-2 (data persistence for training data) |
| 9 | **Integration test suite** for API endpoints | Medium | Ensures API contracts are correct; enables CI/CD pipeline | P0-2 (repository layer) |
| 10 | **FedNow/RTP payment rail** integration | High | Enables instant settlement, eliminating T+1/T+2 ACH delay for supported transactions | P0-2, P1-6 |

### P2 -- Medium Priority (Next 6 Months)

| # | Improvement | Effort | Impact | Dependencies |
|---|---|---|---|---|
| 11 | **Event sourcing** for ledger operations | High | Full audit trail with causality; enables temporal queries and state reconstruction | P0-2 |
| 12 | **Kafka/Redpanda event streaming** | High | Moves from batch to real-time processing; decouples services | P2-11 |
| 13 | **Alloy/Sardine compliance integration** | Medium | Replaces manual OFAC screening with production-grade identity verification and sanctions screening | None |
| 14 | **Great Expectations data quality** | Medium | Automated validation of ledger invariants and reconciliation data quality | P0-2, existing `dbt/` directory |
| 15 | **GraphQL for dashboard queries** (Strawberry) | Medium | Reduces API chattiness for dashboard views; enables flexible frontend queries | None |
| 16 | **Kubernetes deployment manifests** | Medium | Production-grade deployment with auto-scaling, rolling updates, health checks | P0-5 |

### P3 -- Lower Priority (Future Enhancements)

| # | Improvement | Effort | Impact | Dependencies |
|---|---|---|---|---|
| 17 | **Graph neural network fraud detection** | Very High | State-of-the-art fraud detection for network-level fraud patterns (account rings, mule networks) | P1-8 (baseline ML model) |
| 18 | **Multi-currency support** with FX engine | High | Enables international EWA operations | P0-1 (Decimal), P0-2 |
| 19 | **gRPC internal communication** | High | Microservices-ready internal transport; 10x lower latency than REST | P2-16 (Kubernetes) |
| 20 | **Real-time balance streaming** via WebSockets | Medium | Live balance updates for employee-facing applications | P2-12 (event streaming) |
| 21 | **Property-based testing** with Hypothesis | Low | Proves ledger invariants hold under arbitrary input sequences | P0-2 |
| 22 | **ISO 20022 message builder** | Medium | Standards-compliant payment messaging for FedNow, SWIFT, and cross-border payments | P1-10 (FedNow integration) |

---

### Summary of Key Recommendations

The three highest-ROI improvements are:

1. **Decimal arithmetic** (P0-1) -- Non-negotiable for any financial system. The current `float` usage will cause real money discrepancies at scale.
2. **Repository layer + database persistence** (P0-2) -- The schema already exists and is well-designed. Connecting the engines to the database transforms this from a reference implementation to a deployable system.
3. **ML-based fraud scoring** (P1-8) -- The current rule engine logs decisions in a format ready for ML training (`FraudResult` dataclass). A LightGBM model trained on this data, exported to ONNX for fast inference, would dramatically improve fraud detection accuracy while maintaining the < 100ms latency budget.

These three improvements, combined with structured logging (P0-3) and proper testing (P1-9), would make the platform production-ready.

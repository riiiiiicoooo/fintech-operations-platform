# Security Review: Fintech Operations Platform

**Review Date:** 2026-03-06
**Reviewer:** Security Audit (Automated)
**Scope:** Full source code review of `src/`, `api/`, `trigger-jobs/`, `schema/`, Docker/infra configs, `.env.example`
**Classification:** CONFIDENTIAL

---

## Executive Summary

This review covers the fintech operations platform handling payments, ledger, settlements, fraud detection, and compliance. **23 findings** were identified across 8 categories. The most critical issues are a hardcoded JWT secret with an insecure fallback, missing authentication on financial endpoints, use of floating-point arithmetic for monetary values, and disabled Row-Level Security. The codebase is labeled "not production code" in comments, but any deployment would carry significant risk without remediating the CRITICAL and HIGH findings below.

| Severity | Count |
|----------|-------|
| CRITICAL | 6     |
| HIGH     | 8     |
| MEDIUM   | 6     |
| LOW      | 3     |

---

## Table of Contents

1. [Hardcoded Secrets and Credentials](#1-hardcoded-secrets-and-credentials)
2. [Authentication and Authorization Vulnerabilities](#2-authentication-and-authorization-vulnerabilities)
3. [Financial Security](#3-financial-security)
4. [Input Validation and Injection](#4-input-validation-and-injection)
5. [Compliance Gaps](#5-compliance-gaps)
6. [Encryption and Data Protection](#6-encryption-and-data-protection)
7. [PII and Financial Data in Logs](#7-pii-and-financial-data-in-logs)
8. [Infrastructure Misconfigurations](#8-infrastructure-misconfigurations)

---

## 1. Hardcoded Secrets and Credentials

### FINDING-01: Hardcoded JWT Secret with Insecure Fallback Default

- **Severity:** CRITICAL
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\api\app.py`, line 105
- **Description:** The JWT secret used to sign and verify all authentication tokens falls back to a hardcoded string `"finops-dev-secret-change-in-production"` when the `JWT_SECRET` environment variable is unset. If deployed without setting this variable, all tokens are signed with a publicly known secret, allowing any attacker to forge valid JWTs for any role, including `admin` and `compliance`.
- **Code Evidence:**
  ```python
  JWT_SECRET = os.getenv("JWT_SECRET", "finops-dev-secret-change-in-production")
  ```
- **Fix:**
  1. Remove the fallback default entirely. Fail fast at startup if `JWT_SECRET` is not set:
     ```python
     JWT_SECRET = os.environ["JWT_SECRET"]  # Crash on startup if missing
     ```
  2. Use a minimum length check (e.g., 32+ characters) and entropy validation at boot.
  3. Rotate the secret periodically via Vault or a secrets manager.

---

### FINDING-02: Hardcoded Database Credentials in docker-compose.yml

- **Severity:** HIGH
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\docker-compose.yml`, lines 13-15 and 58
- **Description:** PostgreSQL and Redis credentials are hardcoded in plain text in the Docker Compose file, which is committed to version control. The passwords `fintech_password` and `redis_password` are trivially weak.
- **Code Evidence:**
  ```yaml
  POSTGRES_USER: fintech
  POSTGRES_PASSWORD: fintech_password
  POSTGRES_DB: fintech_ops
  # ...
  DATABASE_URL: postgresql://fintech:fintech_password@postgres:5432/fintech_ops
  REDIS_URL: redis://:redis_password@redis:6379/0
  ```
  ```yaml
  command: redis-server --appendonly yes --requirepass redis_password
  ```
- **Fix:**
  1. Use Docker secrets or environment variable interpolation from a `.env` file (not committed):
     ```yaml
     POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
     ```
  2. Never commit any `docker-compose.override.yml` containing real credentials.
  3. Enforce strong passwords (20+ random characters).

---

### FINDING-03: Placeholder Secrets in .env.example Resemble Real Patterns

- **Severity:** MEDIUM
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\.env.example`, lines 52, 98, 249, 268
- **Description:** While `.env.example` is intended as a template, several placeholder values follow recognizable formats (`tr_live_your_api_key_here`, `xoxb-your_token_here`, `BANK_ROUTING_NUMBER=021000021`). The bank routing number `021000021` is a real JP Morgan Chase routing number. If developers copy this file to `.env` without changing all values, live credentials or real routing numbers could be used accidentally.
- **Code Evidence:**
  ```
  TRIGGER_DEV_API_KEY=tr_live_your_api_key_here
  SLACK_BOT_TOKEN=xoxb-your_token_here
  BANK_ROUTING_NUMBER=021000021
  BANK_ACCOUNT_NUMBER=your_account_number_here
  ```
- **Fix:**
  1. Use obviously invalid placeholder values (e.g., `BANK_ROUTING_NUMBER=000000000`).
  2. Add a startup validation script that checks for placeholder values and refuses to start.

---

### FINDING-04: NACHA Generator Uses Hardcoded Bank Routing Numbers

- **Severity:** MEDIUM
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\src\settlement\nacha_generator.py`, lines 94, 100, 230-231
- **Description:** The NACHA batch header hardcodes `"094101768"` as a standard entry class code and the sample function uses `"021000021"` (JP Morgan routing number) directly. The `NACHAFileGenerator` also hardcodes `"ACME FINTECH"` and `"FEDWIRE"` in the file header. In production, these would need to be parameterized; if left as-is, test NACHA files could be accidentally submitted to real ACH networks.
- **Code Evidence:**
  ```python
  "094101768" +   # Standard entry class (9)
  # ...
  generator = NACHAFileGenerator(
      company_name="ACME Fintech",
      company_id="1234567890",
      originating_dfi="021000021",  # JPMorgan
  )
  ```
- **Fix:**
  1. Load all NACHA identifiers from environment variables or a configuration file.
  2. Validate that NACHA files are never generated with test identifiers in a production environment.

---

## 2. Authentication and Authorization Vulnerabilities

### FINDING-05: Financial Endpoints Missing Authentication Entirely

- **Severity:** CRITICAL
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\api\app.py`, lines 155-303, 310-356, 363-398, 405-428
- **Description:** The `POST /transactions`, `GET /accounts/{id}/balance`, `POST /reconciliation/run`, and `GET /compliance/screening/{id}` endpoints have **no authentication dependency**. Only `GET /audit/transactions` requires `Depends(require_role("compliance", "admin"))`. This means any unauthenticated request can create transactions, query account balances, trigger reconciliation runs, and view compliance screening data.
- **Code Evidence:**
  ```python
  @app.post("/transactions", response_model=TransactionResponse)
  async def create_transaction(
      request: TransactionRequest,
      idempotency_key_header: Optional[str] = Header(None, alias="Idempotency-Key")
  ):
      # No Depends(get_current_user) or Depends(require_role(...))
  ```
  ```python
  @app.get("/accounts/{account_id}/balance", response_model=AccountBalance)
  async def get_account_balance(account_id: str, ...):
      # No authentication
  ```
  ```python
  @app.post("/reconciliation/run", response_model=ReconciliationRunResponse)
  async def run_reconciliation(request: ReconciliationRunRequest):
      # No authentication
  ```
- **Fix:**
  1. Add `current_user: dict = Depends(get_current_user)` to all endpoints.
  2. Add role-based authorization:
     - `POST /transactions`: require `"user"` or `"admin"` role.
     - `GET /accounts/{id}/balance`: require `"user"` (own account) or `"admin"` / `"finance"` (any account).
     - `POST /reconciliation/run`: require `"operations"` or `"admin"`.
     - `GET /compliance/screening/{id}`: require `"compliance"` or `"admin"`.
  3. Implement resource-level authorization (users can only access their own accounts).

---

### FINDING-06: No Resource-Level Authorization (IDOR Vulnerability)

- **Severity:** CRITICAL
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\api\app.py`, lines 310-356
- **Description:** Even if authentication were added, the `GET /accounts/{account_id}/balance` endpoint does not verify that the authenticated user owns the requested account. An attacker could enumerate any `user_wallet:user_XXXX` account ID to view other users' balances. Similarly, `POST /transactions` does not verify that `request.user_id` matches the authenticated user.
- **Code Evidence:**
  ```python
  @app.get("/accounts/{account_id}/balance")
  async def get_account_balance(account_id: str, ...):
      parts = account_id.split(":")
      # No check that entity_id matches the authenticated user
  ```
  ```python
  @app.post("/transactions")
  async def create_transaction(request: TransactionRequest, ...):
      # request.user_id is client-supplied; no verification against authenticated identity
  ```
- **Fix:**
  1. For user-facing endpoints, extract `user_id` from the JWT token, never from the request body.
  2. Validate that the authenticated user has permission to access the specific resource.
  3. Implement Row-Level Security (RLS) at the database level as defense-in-depth.

---

### FINDING-07: JWT Algorithm Not Restricted to Expected Value

- **Severity:** MEDIUM
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\api\app.py`, lines 121-123
- **Description:** While `HS256` is specified in `jwt.decode()`, the `algorithms` parameter receives it correctly as a list. However, the code does not validate issuer (`iss`) or audience (`aud`) claims, making it possible for tokens issued by other services using the same secret to be accepted. Additionally, there is no token revocation mechanism.
- **Code Evidence:**
  ```python
  payload = jwt.decode(
      credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM]
  )
  return {
      "user_id": payload.get("sub"),
      "role": payload.get("role", "viewer"),
  }
  ```
- **Fix:**
  1. Validate `iss` and `aud` claims:
     ```python
     payload = jwt.decode(
         credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM],
         issuer="fintech-ops", audience="fintech-ops-api"
     )
     ```
  2. Implement token revocation (via Redis blacklist or short-lived tokens with refresh tokens).
  3. Return 401 if `sub` is missing from the token payload.

---

### FINDING-08: Missing CORS Configuration on FastAPI Application

- **Severity:** HIGH
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\api\app.py`, lines 45-49
- **Description:** The FastAPI application does not configure CORS middleware. Without CORS restrictions, any website can make cross-origin requests to the API. While Vercel headers set some security headers, CORS must be enforced at the application level to prevent cross-site request forgery against financial endpoints.
- **Code Evidence:**
  ```python
  app = FastAPI(
      title="Fintech Operations Platform API",
      description="...",
      version="1.0.0",
  )
  # No CORSMiddleware added
  ```
- **Fix:**
  ```python
  from fastapi.middleware.cors import CORSMiddleware

  app.add_middleware(
      CORSMiddleware,
      allow_origins=os.getenv("CORS_ORIGINS", "").split(","),
      allow_credentials=True,
      allow_methods=["GET", "POST"],
      allow_headers=["Authorization", "Idempotency-Key", "Content-Type"],
  )
  ```

---

## 3. Financial Security

### FINDING-09: Floating-Point Arithmetic for Monetary Calculations

- **Severity:** CRITICAL
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\src\ledger\ledger_engine.py`, lines 111, 138-141, 230-251
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\src\settlement\settlement_engine.py`, lines 198-209
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\api\models.py`, line 19
- **Description:** All monetary values use Python `float` type, which cannot represent decimal fractions exactly. IEEE 754 floating-point arithmetic causes rounding errors that accumulate over thousands of transactions, leading to ledger imbalances, incorrect settlement amounts, and reconciliation discrepancies. The balance validation tolerance of `0.001` masks this issue rather than solving it. The settlement split calculation chain (`round(gross * rate, 2)`) compounds the error.
- **Code Evidence:**
  ```python
  # ledger_engine.py line 111
  debit: float = 0.0
  credit: float = 0.0

  # ledger_engine.py line 140-141 - Epsilon tolerance masks float errors
  if abs(total_debits - total_credits) > 0.001:
      raise ValueError(...)

  # settlement_engine.py lines 198-209
  platform_fee = round(gross * transaction.platform_fee_rate, 2)
  psp_fee = round(gross * psp_pct + psp_flat, 2)
  user_receives = round(gross - platform_fee - psp_fee - holdback, 2)

  # models.py line 19
  amount: float = Field(gt=0)
  ```
- **Fix:**
  1. Replace all `float` monetary fields with `Decimal` (Python) or integer cents:
     ```python
     from decimal import Decimal, ROUND_HALF_UP
     debit: Decimal = Decimal("0.00")
     credit: Decimal = Decimal("0.00")
     ```
  2. Use `Decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)` for all monetary calculations.
  3. Change the Pydantic model to use `Decimal`:
     ```python
     amount: Decimal = Field(gt=0, decimal_places=2)
     ```
  4. Note: The TypeScript Trigger.dev jobs (`trigger-jobs/settlement_batch.ts`) correctly use `Decimal.js`, which is good. The Python code must match.

---

### FINDING-10: Race Condition in Balance Check and Hold Creation

- **Severity:** HIGH
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\src\ledger\ledger_engine.py`, lines 268-283
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\api\app.py`, lines 261-272
- **Description:** The `create_hold` method checks the available balance then creates the hold in two separate steps without atomicity. In a concurrent environment, two simultaneous requests for the same user could both pass the balance check before either hold is created, resulting in double-spending. The API endpoint has the same issue: it calls `post_entry` then `create_hold` as two separate operations.
- **Code Evidence:**
  ```python
  # ledger_engine.py
  def create_hold(self, account, amount, transaction_id):
      available = self.get_available_balance(account)  # Step 1: Check
      if amount > available:
          raise ValueError(...)
      hold = Hold(...)  # Step 2: Create (not atomic with Step 1)
      self.holds.append(hold)
      return hold

  # app.py
  entry = ledger.post_entry(...)  # Step 1
  hold = ledger.create_hold(...)  # Step 2 (not atomic with Step 1)
  ```
- **Fix:**
  1. In production PostgreSQL: use `SELECT ... FOR UPDATE` or `SERIALIZABLE` transaction isolation to lock the account row during balance check and hold creation.
  2. Wrap the entire transaction flow (entry + hold) in a single database transaction:
     ```python
     with db.begin(isolation_level="SERIALIZABLE"):
         check_balance()
         create_entry()
         create_hold()
     ```
  3. Add pessimistic locking on the account record.

---

### FINDING-11: No Maximum Transaction Amount Validation

- **Severity:** HIGH
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\api\models.py`, lines 18-27
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\api\app.py`, lines 155-303
- **Description:** The `TransactionRequest` model only validates `amount > 0` but has no upper bound. An attacker could submit a transaction for `$999,999,999.99` or an astronomically large amount. While KYC tier limits provide some protection, a user with `enhanced` KYC tier has a `$5,000` per-transaction limit, but there is no system-wide hard cap validated at the API boundary.
- **Code Evidence:**
  ```python
  class TransactionRequest(BaseModel):
      amount: float = Field(gt=0)  # No upper bound

      @validator("amount")
      def amount_positive(cls, v):
          if v <= 0:
              raise ValueError("Amount must be positive")
          return round(v, 2)  # No max check
  ```
- **Fix:**
  1. Add a system-wide maximum at the API validation layer:
     ```python
     amount: Decimal = Field(gt=0, le=50000)  # Hard cap at $50,000
     ```
  2. Apply defense-in-depth with the compliance checker's tier limits.

---

### FINDING-12: Fraud Detection Uses Hardcoded Placeholder Data

- **Severity:** HIGH
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\api\app.py`, lines 187-202
- **Description:** The transaction endpoint populates the `TransactionContext` for fraud detection with hardcoded placeholder values rather than real user data. This means fraud detection is effectively bypassed: every transaction appears to come from a 30-day-old account with standard KYC, 5 lifetime transactions, and low velocity. New accounts, high-velocity users, and truly suspicious patterns would not be detected.
- **Code Evidence:**
  ```python
  fraud_context = TransactionContext(
      transaction_id=f"txn_{uuid.uuid4().hex[:8]}",
      user_id=request.user_id,
      amount=request.amount,
      payment_method=request.payment_method,
      device_fingerprint="device_placeholder",  # Hardcoded!
      ip_address="192.168.1.1",                 # Hardcoded private IP!
      account_age_days=30,                       # Hardcoded!
      kyc_tier="standard",                       # Hardcoded!
      lifetime_transaction_count=5,              # Hardcoded!
      avg_transaction_amount=100.0,              # Hardcoded!
      transactions_last_24h=2,                   # Hardcoded!
      transactions_last_7d=10,                   # Hardcoded!
      amount_last_24h=150.0,                     # Hardcoded!
      amount_last_7d=600.0,                      # Hardcoded!
  )
  ```
- **Fix:**
  1. Fetch real user profile data from the database (account age, KYC tier, lifetime stats).
  2. Extract real device fingerprint and IP address from the HTTP request.
  3. Query velocity aggregates from the `velocity_windows` table.

---

### FINDING-13: Compliance Check Uses Hardcoded Daily/Monthly Totals

- **Severity:** HIGH
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\api\app.py`, lines 206-211
- **Description:** The compliance limit check passes hardcoded `daily_total=150.0` and `monthly_total=5500.0` instead of querying the actual user's transaction history. This means KYC tier enforcement is non-functional: a user at the `basic` tier (daily limit $500) could make unlimited transactions because the check always sees $150 as the daily total.
- **Code Evidence:**
  ```python
  compliance_check = compliance_checker.check_transaction_limits(
      user_id=request.user_id,
      amount=request.amount,
      daily_total=150.0,     # Hardcoded! Should be queried
      monthly_total=5500.0,  # Hardcoded! Should be queried
  )
  ```
- **Fix:**
  1. Query real daily and monthly aggregates from the database before calling the compliance check.
  2. The aggregates should be computed from `journal_entry_lines` for the user within the relevant time windows.

---

## 4. Input Validation and Injection

### FINDING-14: Payment Method Not Validated Against Allowed Values

- **Severity:** MEDIUM
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\api\models.py`, line 19
- **Description:** The `payment_method` field on `TransactionRequest` is a free-form `str` with no validation against the set of supported payment methods (`ach`, `card`, `instant`, `wire`). Arbitrary strings could cause unexpected behavior in downstream routing logic.
- **Code Evidence:**
  ```python
  payment_method: str  # "ach", "card", "instant", "wire" -- comment only, not enforced
  ```
- **Fix:**
  ```python
  from enum import Enum

  class PaymentMethodEnum(str, Enum):
      ach = "ach"
      card = "card"
      instant = "instant"
      wire = "wire"

  payment_method: PaymentMethodEnum
  ```

---

### FINDING-15: SQL Queries Use Parameterized Queries (Positive Finding)

- **Severity:** INFO (Positive)
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\trigger-jobs\settlement_batch.ts`, `trigger-jobs\reconciliation_run.ts`
- **Description:** All SQL queries in the Trigger.dev jobs use parameterized queries (`$1`, `$2`, etc.) consistently. No string interpolation is used for SQL construction. This is correct and prevents SQL injection.
- **Code Evidence:**
  ```typescript
  const query = `SELECT ... WHERE je.posted_at::date = $1 ...`;
  const result = await db.query(query, [settlementDate]);
  ```
- **Note:** This is a positive finding. No action required.

---

### FINDING-16: Account ID Parsing Lacks Validation

- **Severity:** MEDIUM
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\api\app.py`, lines 327-336
- **Description:** The `GET /accounts/{account_id}/balance` endpoint splits the account ID on `:` and attempts to convert the first part to an `AccountCode` enum. If the input contains unexpected formats, it could raise unhandled exceptions or leak internal enum names through error messages.
- **Code Evidence:**
  ```python
  parts = account_id.split(":")
  if len(parts) == 2:
      account_code_str, entity_id = parts
      account_code = AccountCode[account_code_str.upper()]  # KeyError if invalid
  ```
- **Fix:**
  1. Validate the account ID format with a regex before processing.
  2. Catch `KeyError` specifically and return a generic 404 rather than exposing enum names.

---

## 5. Compliance Gaps

### FINDING-17: Compliance Event Hash Chain Not Implemented

- **Severity:** HIGH
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\src\compliance\compliance_checker.py`, lines 157-167, 517-531
- **Description:** The `ComplianceEvent` model has `previous_hash` and `event_hash` fields intended for tamper detection, but the `_log_event` method never computes or sets these values. The compliance event log is therefore not tamper-resistant, and an attacker with database access could modify or delete compliance events (KYC approvals, OFAC screenings, SAR recommendations) without detection. This is a regulatory requirement under BSA/AML.
- **Code Evidence:**
  ```python
  @dataclass
  class ComplianceEvent:
      # ...
      previous_hash: str = ""   # Never set
      event_hash: str = ""      # Never set

  def _log_event(self, event_type, user_id, details):
      event = ComplianceEvent(
          event_id=str(uuid4()),
          event_type=event_type,
          user_id=user_id,
          details=details,
      )
      # previous_hash and event_hash are left as empty strings
      self.events.append(event)
  ```
- **Fix:**
  1. Compute SHA-256 hash chain on each event:
     ```python
     import hashlib, json

     def _log_event(self, event_type, user_id, details):
         previous_hash = self.events[-1].event_hash if self.events else "genesis"
         event_data = json.dumps({
             "event_type": event_type.value,
             "user_id": user_id,
             "details": details,
             "previous_hash": previous_hash,
         }, sort_keys=True)
         event_hash = hashlib.sha256(event_data.encode()).hexdigest()

         event = ComplianceEvent(
             event_id=str(uuid4()),
             event_type=event_type,
             user_id=user_id,
             details=details,
             previous_hash=previous_hash,
             event_hash=event_hash,
         )
         self.events.append(event)
     ```
  2. In PostgreSQL, add a trigger to compute hashes server-side, preventing application-layer bypass.
  3. Implement periodic hash chain verification as a scheduled job.

---

### FINDING-18: Row-Level Security (RLS) Commented Out

- **Severity:** HIGH
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\schema\schema.sql`, lines 440-448
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\supabase\migrations\001_initial_schema.sql`, lines 510-524
- **Description:** PostgreSQL Row-Level Security policies are defined but commented out in all schema files. Without RLS, any database user (or any SQL injection, or any compromised application component) can access all rows in all tables. Given this is a multi-tenant financial system with user wallets, KYC records, and compliance data, this is a significant gap.
- **Code Evidence:**
  ```sql
  -- ALTER TABLE accounts ENABLE ROW LEVEL SECURITY;
  -- ALTER TABLE journal_entries ENABLE ROW LEVEL SECURITY;
  -- CREATE POLICY user_wallet_isolation ON accounts
  --     USING (entity_id = current_setting('app.current_user_id'));
  ```
- **Fix:**
  1. Enable RLS on all tables containing user-specific data (`accounts`, `holds`, `risk_scores`, `user_kyc`, `monitoring_alerts`, `ofac_screenings`).
  2. Create appropriate policies per role.
  3. Ensure the application sets `app.current_user_id` on each database connection.

---

### FINDING-19: Compliance Event Table Allows UPDATE and DELETE

- **Severity:** MEDIUM
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\schema\schema.sql`, lines 352-364
- **Description:** The `compliance_events` table is documented as "append-only" with "INSERT-only permissions" for the application user, but no such permission restrictions are implemented in the schema. The table allows UPDATE and DELETE operations, which could be used to tamper with the audit trail.
- **Code Evidence:**
  ```sql
  CREATE TABLE compliance_events (
      event_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
      event_type VARCHAR(50) NOT NULL,
      -- ... no restrictions on UPDATE/DELETE
  );
  -- No GRANT/REVOKE statements, no triggers preventing modification
  ```
- **Fix:**
  1. Create a separate database role for the application:
     ```sql
     CREATE ROLE app_user LOGIN PASSWORD '...';
     GRANT INSERT ON compliance_events TO app_user;
     REVOKE UPDATE, DELETE ON compliance_events FROM app_user;
     ```
  2. Add a trigger that prevents UPDATE and DELETE:
     ```sql
     CREATE OR REPLACE FUNCTION prevent_compliance_modification()
     RETURNS TRIGGER AS $$
     BEGIN
         RAISE EXCEPTION 'compliance_events is append-only';
     END;
     $$ LANGUAGE plpgsql;

     CREATE TRIGGER no_update_delete
     BEFORE UPDATE OR DELETE ON compliance_events
     FOR EACH ROW EXECUTE FUNCTION prevent_compliance_modification();
     ```

---

## 6. Encryption and Data Protection

### FINDING-20: No Encryption at Rest for PII and Financial Data

- **Severity:** CRITICAL
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\schema\schema.sql` (entire schema)
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\.env.example`, line 271
- **Description:** The `.env.example` references an `ENCRYPTION_KEY` for AES-256 PII encryption, and the schema enables `pgcrypto`, but **no encryption is applied anywhere**. Sensitive fields such as `full_name` in `ofac_screenings`, `user_id` mappings, bank account numbers in NACHA files, and compliance event `details` (which contain KYC information and screened names) are stored in plain text. The NACHA content (containing account numbers and routing numbers) is stored as plain `TEXT` in the `nacha_batches` table.
- **Code Evidence:**
  ```sql
  -- pgcrypto is enabled but never used
  CREATE EXTENSION IF NOT EXISTS "pgcrypto";

  -- Sensitive data stored in plain text
  full_name VARCHAR(200) NOT NULL,  -- ofac_screenings
  nacha_content TEXT,                -- nacha_batches (contains account numbers!)
  details JSONB NOT NULL DEFAULT '{}',  -- compliance_events (KYC data, screened names)
  ```
  ```
  # .env.example - Key exists but is never used
  ENCRYPTION_KEY=your_32_byte_base64_encoded_key_here
  ```
- **Fix:**
  1. Encrypt PII columns using `pgcrypto`:
     ```sql
     -- Store encrypted
     INSERT INTO ofac_screenings (full_name, ...)
     VALUES (pgp_sym_encrypt($1, current_setting('app.encryption_key')), ...);

     -- Read decrypted
     SELECT pgp_sym_decrypt(full_name::bytea, current_setting('app.encryption_key'))
     FROM ofac_screenings WHERE user_id = $1;
     ```
  2. Encrypt NACHA content before storage.
  3. Use application-level envelope encryption with keys managed via Vault.

---

### FINDING-21: NACHA Files Contain Unencrypted Bank Account Numbers

- **Severity:** HIGH
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\src\settlement\nacha_generator.py`, lines 28-36
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\trigger-jobs\settlement_batch.ts`, lines 183-196
- **Description:** NACHA entry detail records contain destination bank account numbers (`receiving_dda`) and routing numbers. These are transmitted to the bank API over HTTPS (good), but they are also stored in the `nacha_batches` table as plain text and logged by the Trigger.dev job. Anyone with database read access or log access obtains all user bank account numbers.
- **Code Evidence:**
  ```python
  @dataclass
  class NACHAEntry:
      receiving_dda: str       # Bank account number in plain text
      # ...
  ```
  ```typescript
  // settlement_batch.ts - NACHA content stored as plain text
  await db.query(query, [batchId, ..., nacha]);
  ```
- **Fix:**
  1. Encrypt NACHA content in the database using application-level encryption.
  2. Ensure NACHA files are not written to disk or logs.
  3. Implement tokenized bank account references so that real account numbers are only resolved at submission time.

---

## 7. PII and Financial Data in Logs

### FINDING-22: Financial Amounts, User IDs, and Transaction Details in Logs

- **Severity:** MEDIUM
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\trigger-jobs\settlement_batch.ts`, lines 58, 77, 124, 229, 499-502
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\trigger-jobs\reconciliation_run.ts`, lines 55, 74, 106, 243
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\src\compliance\compliance_checker.py`, lines 436-441
- **Description:** Logger statements include user IDs, transaction amounts, settlement batch details, and OFAC screening results (including the full name screened and match scores). In a production environment, these logs may be shipped to external logging services (Datadog, CloudWatch), creating PII exposure risk.
- **Code Evidence:**
  ```typescript
  logger.info(`Fetched ${result.rows.length} pending transactions`);
  logger.info(`Calculated positions for ${positions.length} users`);
  logger.info(`Generated NACHA file with ${entryCount} entries`);
  ```
  ```python
  # compliance_checker.py
  self._log_event(event_type, user_id, {
      "name_screened": full_name,  # PII in compliance event details
      "match_found": match_found,
      "match_score": best_score,
  })
  ```
- **Fix:**
  1. Mask or hash user IDs and names in log output:
     ```python
     masked_name = full_name[:2] + "***" + full_name[-1:]
     ```
  2. Never log full bank account numbers, SSNs, or names to application logs.
  3. Use structured logging with sensitive field redaction.
  4. Ensure log shipping destinations have appropriate access controls.

---

## 8. Infrastructure Misconfigurations

### FINDING-23: Docker Compose Exposes Database and Redis Ports to Host

- **Severity:** CRITICAL
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\docker-compose.yml`, lines 18-19 and 36-37
- **Description:** PostgreSQL (port 5432) and Redis (port 6379) are bound to `0.0.0.0` on the host. In cloud environments or shared networks, this exposes the database and cache to all network interfaces, allowing direct connection from the internet if the host has a public IP and no firewall.
- **Code Evidence:**
  ```yaml
  postgres:
    ports:
      - "5432:5432"   # Exposed on all interfaces
  redis:
    ports:
      - "6379:6379"   # Exposed on all interfaces
  ```
- **Fix:**
  1. Bind to localhost only:
     ```yaml
     ports:
       - "127.0.0.1:5432:5432"
       - "127.0.0.1:6379:6379"
     ```
  2. For production: remove port mappings entirely and use Docker networks for inter-service communication.
  3. Redis health check uses `redis-cli ping` without authentication, which will fail with `--requirepass`. Update to:
     ```yaml
     test: ["CMD", "redis-cli", "-a", "redis_password", "ping"]
     ```

---

### FINDING-24: Dockerfile Runs as Root

- **Severity:** MEDIUM
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\Dockerfile`, entire file
- **Description:** The Dockerfile does not create or switch to a non-root user. The application runs as `root` inside the container, which means a container escape or application vulnerability could give the attacker root access on the host.
- **Code Evidence:**
  ```dockerfile
  FROM python:3.11-slim
  WORKDIR /app
  # ... no USER directive
  CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
  ```
- **Fix:**
  ```dockerfile
  RUN groupadd -r appuser && useradd -r -g appuser appuser
  RUN chown -R appuser:appuser /app
  USER appuser
  CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
  ```

---

### FINDING-25: API Binds to 0.0.0.0 Without Rate Limiting

- **Severity:** LOW
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\api\app.py`, line 543
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\.env.example`, lines 276-278
- **Description:** The API binds to `0.0.0.0:8000` and while the `.env.example` defines `RATE_LIMIT_REQUESTS=1000` and `RATE_LIMIT_WINDOW_MINUTES=1`, no rate limiting middleware is actually implemented in the FastAPI application. Financial endpoints are vulnerable to brute-force attacks, account enumeration, and denial-of-service.
- **Code Evidence:**
  ```python
  uvicorn.run(app, host="0.0.0.0", port=8000)
  # No rate limiting middleware in the application
  ```
- **Fix:**
  1. Add rate limiting middleware:
     ```python
     from slowapi import Limiter
     from slowapi.util import get_remote_address

     limiter = Limiter(key_func=get_remote_address)
     app.state.limiter = limiter

     @app.post("/transactions")
     @limiter.limit("10/minute")
     async def create_transaction(...):
     ```
  2. Apply stricter limits to sensitive endpoints (authentication, transactions).

---

### FINDING-26: Docker Compose Mounts Entire Source Tree as Volume

- **Severity:** LOW
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\docker-compose.yml`, line 70
- **Description:** The API service mounts the entire project directory (`.:/app`) into the container. This means `.env` files, `.git` history, and other sensitive files are accessible inside the container. If a path traversal vulnerability existed in the application, an attacker could read the `.env` file with all secrets.
- **Code Evidence:**
  ```yaml
  api:
    volumes:
      - .:/app  # Entire source tree including .env, .git, etc.
  ```
- **Fix:**
  1. For development, exclude sensitive files:
     ```yaml
     volumes:
       - .:/app
       - /app/.env     # Prevent .env from being mounted
       - /app/.git     # Prevent .git from being mounted
     ```
  2. For production, do not use volume mounts; copy only necessary files via Dockerfile.

---

### FINDING-27: Health Check Endpoint Returns Fake Status

- **Severity:** LOW
- **File:** `F:\Portfolio\Portfolio\fintech-operations-platform\api\app.py`, lines 501-516
- **Description:** The health check endpoint always returns `"healthy"` with `database: "connected"` and `cache: "connected"` regardless of actual service status. Load balancers and orchestrators relying on this endpoint would never detect an unhealthy instance.
- **Code Evidence:**
  ```python
  @app.get("/health")
  async def health_check():
      return HealthCheck(
          status="healthy",       # Always "healthy"
          database="connected",   # Never actually checked
          cache="connected",      # Never actually checked
          message="All systems operational",
      )
  ```
- **Fix:**
  1. Actually check database and Redis connectivity:
     ```python
     try:
         await db.execute("SELECT 1")
         db_status = "connected"
     except Exception:
         db_status = "disconnected"
     ```
  2. Return appropriate HTTP status codes (200 for healthy, 503 for unhealthy).

---

## Remediation Priority Matrix

| Priority | Finding IDs | Action |
|----------|-------------|--------|
| Immediate (Week 1) | FINDING-01, FINDING-05, FINDING-06, FINDING-09, FINDING-23 | Fix hardcoded JWT secret, add auth to all endpoints, switch to Decimal, restrict Docker ports |
| High (Week 2-3) | FINDING-02, FINDING-08, FINDING-10, FINDING-11, FINDING-12, FINDING-13, FINDING-17, FINDING-18, FINDING-20, FINDING-21 | Externalize credentials, add CORS, fix race conditions, implement real fraud/compliance data, enable hash chain and RLS, encrypt PII |
| Medium (Week 4-6) | FINDING-03, FINDING-04, FINDING-07, FINDING-14, FINDING-16, FINDING-19, FINDING-22, FINDING-24 | Clean up placeholders, validate inputs, restrict compliance table writes, mask PII in logs, run as non-root |
| Low (Backlog) | FINDING-25, FINDING-26, FINDING-27 | Add rate limiting, restrict volume mounts, implement real health checks |

---

## Positive Security Observations

The following security practices are already well-implemented:

1. **Parameterized SQL queries** in all Trigger.dev jobs prevent SQL injection.
2. **Idempotency key handling** throughout the ledger and API prevents duplicate transaction processing.
3. **Double-entry accounting with balance validation** provides structural integrity for financial records.
4. **Hold mechanism** for preventing double-spend during async settlement is architecturally sound (needs atomicity fix).
5. **OFAC sanctions screening with fuzzy matching** demonstrates awareness of regulatory requirements.
6. **Progressive KYC tier enforcement** with transaction limits is well-designed.
7. **Circuit breaker pattern** for PSP failover prevents cascading failures.
8. **Vercel security headers** (HSTS, CSP, X-Frame-Options, X-Content-Type-Options) are properly configured.
9. **Schema uses CHECK constraints** extensively to enforce data integrity at the database level.
10. **TypeScript settlement jobs use Decimal.js** for monetary calculations (correct practice).
11. **.gitignore properly excludes** `.env`, `.env.local`, and `.venv/`.

---

*End of Security Review*

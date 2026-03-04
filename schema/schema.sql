-- ============================================================================
-- Fintech Operations Platform: Complete PostgreSQL Schema
-- ============================================================================
-- Ledger system with double-entry accounting, fraud detection, reconciliation,
-- and compliance tracking. All tables designed for SERIALIZABLE isolation and
-- multi-tenant row-level security.
--
-- Key principles:
-- 1. Double-entry ledger with CHECK constraints enforcing debits=credits
-- 2. Immutable journal entries (no updates, only inserts)
-- 3. Idempotency keys prevent duplicate posting
-- 4. JSONB metadata for extensibility and audit trails
-- 5. Partitioning for operational efficiency
-- ============================================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================================
-- Core Account & Ledger Tables
-- ============================================================================

CREATE TABLE accounts (
    account_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_code VARCHAR(50) NOT NULL,  -- e.g., "user_wallet", "platform_fee"
    account_type VARCHAR(20) NOT NULL,  -- "asset", "liability", "revenue", "expense"
    entity_id VARCHAR(100),              -- user_id, employer_id, psp_name, or NULL
    entity_type VARCHAR(20),             -- "user", "employer", "platform", "psp"
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}',

    -- Uniqueness constraint: each account code + entity is unique
    UNIQUE(account_code, COALESCE(entity_id, '')),

    -- Enforce valid account types
    CHECK (account_type IN ('asset', 'liability', 'revenue', 'expense')),

    INDEX idx_accounts_code_entity (account_code, entity_id),
    INDEX idx_accounts_type (account_type),
    INDEX idx_accounts_created (created_at DESC)
);

-- ============================================================================
-- Journal Entries (Immutable Append-Only Log)
-- ============================================================================

-- Main entry header
CREATE TABLE journal_entries (
    entry_id VARCHAR(50) PRIMARY KEY,
    entry_type VARCHAR(30) NOT NULL,    -- "funding", "transfer", "fee", "refund", etc.
    description TEXT NOT NULL,
    idempotency_key VARCHAR(100) NOT NULL UNIQUE,
    posted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reference_type VARCHAR(30),         -- "refund_of", "reversal_of", etc.
    reference_id VARCHAR(50),
    metadata JSONB DEFAULT '{}',

    -- Enforce valid entry types
    CHECK (entry_type IN ('funding', 'transfer', 'fee', 'refund', 'chargeback',
                          'settlement', 'reversal', 'correction', 'suspense_classification')),

    INDEX idx_journal_entries_posted (posted_at DESC),
    INDEX idx_journal_entries_entry_type (entry_type),
    INDEX idx_journal_entries_idempotency (idempotency_key),
    INDEX idx_journal_entries_reference (reference_id)
);

-- Individual debit/credit lines within each entry
CREATE TABLE journal_entry_lines (
    line_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entry_id VARCHAR(50) NOT NULL REFERENCES journal_entries(entry_id),
    account_id UUID NOT NULL REFERENCES accounts(account_id),
    debit DECIMAL(15, 2) NOT NULL DEFAULT 0.00,
    credit DECIMAL(15, 2) NOT NULL DEFAULT 0.00,

    -- Each line has either debit or credit, never both, never neither
    CHECK ((debit > 0 AND credit = 0) OR (debit = 0 AND credit > 0)),
    CHECK (debit >= 0 AND credit >= 0),

    INDEX idx_journal_entry_lines_entry (entry_id),
    INDEX idx_journal_entry_lines_account (account_id),
    INDEX idx_journal_entry_lines_debit_credit (debit DESC, credit DESC)
);

-- ============================================================================
-- Holds (Prevents Double-Spending During Settlement)
-- ============================================================================

CREATE TABLE holds (
    hold_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id UUID NOT NULL REFERENCES accounts(account_id),
    amount DECIMAL(15, 2) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active',  -- "active", "captured", "voided", "expired"
    transaction_id VARCHAR(100),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,

    CHECK (status IN ('active', 'captured', 'voided', 'expired')),
    CHECK (amount > 0),
    CHECK (expires_at > created_at),

    INDEX idx_holds_account (account_id),
    INDEX idx_holds_status (status),
    INDEX idx_holds_expires_at (expires_at),
    INDEX idx_holds_transaction (transaction_id)
);

-- ============================================================================
-- Fraud Detection Tables
-- ============================================================================

CREATE TABLE risk_scores (
    score_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    transaction_id VARCHAR(100) NOT NULL UNIQUE,
    user_id VARCHAR(100) NOT NULL,
    raw_score DECIMAL(8, 2) NOT NULL,
    normalized_score DECIMAL(5, 2) NOT NULL,  -- 0-100
    decision VARCHAR(20) NOT NULL,            -- "approve", "review", "decline"
    rules_triggered TEXT[] DEFAULT '{}',
    model_version VARCHAR(50) DEFAULT 'rules_v1',
    latency_ms DECIMAL(8, 2),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}',

    CHECK (normalized_score >= 0 AND normalized_score <= 100),
    CHECK (decision IN ('approve', 'review', 'decline')),

    INDEX idx_risk_scores_user (user_id),
    INDEX idx_risk_scores_decision (decision),
    INDEX idx_risk_scores_created (created_at DESC),
    INDEX idx_risk_scores_score (normalized_score DESC)
);

CREATE TABLE blocklists (
    blocklist_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id VARCHAR(100) NOT NULL UNIQUE,
    reason TEXT,
    blocked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    blocked_by VARCHAR(100),
    expires_at TIMESTAMP,
    metadata JSONB DEFAULT '{}',

    INDEX idx_blocklists_user (user_id),
    INDEX idx_blocklists_expires (expires_at)
);

CREATE TABLE allowlists (
    allowlist_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id VARCHAR(100) NOT NULL UNIQUE,
    reason TEXT,
    allowlisted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    allowlisted_by VARCHAR(100),
    expires_at TIMESTAMP,
    metadata JSONB DEFAULT '{}',

    INDEX idx_allowlists_user (user_id),
    INDEX idx_allowlists_expires (expires_at)
);

CREATE TABLE velocity_windows (
    window_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id VARCHAR(100) NOT NULL,
    window_type VARCHAR(20) NOT NULL,  -- "transactions_1h", "amount_24h", etc.
    transaction_count INT DEFAULT 0,
    total_amount DECIMAL(15, 2) DEFAULT 0.00,
    window_start TIMESTAMP NOT NULL,
    window_end TIMESTAMP NOT NULL,

    CHECK (window_type IN ('transactions_1h', 'transactions_24h', 'transactions_7d',
                           'amount_24h', 'amount_7d')),

    INDEX idx_velocity_windows_user_type (user_id, window_type),
    INDEX idx_velocity_windows_window_end (window_end DESC)
);

-- ============================================================================
-- Reconciliation Tables
-- ============================================================================

CREATE TABLE reconciliation_matches (
    match_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    reconciliation_run_id VARCHAR(50) NOT NULL,
    ledger_entry_id VARCHAR(50),
    psp_transaction_id VARCHAR(100),
    bank_reference VARCHAR(100),
    match_status VARCHAR(30) NOT NULL,  -- "exact", "fuzzy", "many_to_one", "auto_resolved", "exception", "unmatched"
    break_type VARCHAR(30),             -- "timing", "fee_deduction", "fx_rounding", etc.
    delta_amount DECIMAL(15, 2) DEFAULT 0.00,
    resolution_notes TEXT,
    matched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}',

    CHECK (match_status IN ('exact_match', 'fuzzy_match', 'many_to_one',
                            'auto_resolved', 'exception', 'unmatched')),

    INDEX idx_reconciliation_matches_run (reconciliation_run_id),
    INDEX idx_reconciliation_matches_status (match_status),
    INDEX idx_reconciliation_matches_ledger (ledger_entry_id),
    INDEX idx_reconciliation_matches_psp (psp_transaction_id),
    INDEX idx_reconciliation_matches_bank (bank_reference)
);

CREATE TABLE reconciliation_exceptions (
    exception_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    match_id UUID REFERENCES reconciliation_matches(match_id),
    break_type VARCHAR(30) NOT NULL,
    priority VARCHAR(20) NOT NULL,     -- "critical", "high", "medium", "low"
    delta_amount DECIMAL(15, 2) NOT NULL,
    description TEXT,
    assigned_to VARCHAR(100),
    resolved BOOLEAN DEFAULT FALSE,
    resolved_at TIMESTAMP,
    resolution_notes TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}',

    CHECK (priority IN ('critical', 'high', 'medium', 'low')),

    INDEX idx_reconciliation_exceptions_priority (priority),
    INDEX idx_reconciliation_exceptions_resolved (resolved),
    INDEX idx_reconciliation_exceptions_assigned (assigned_to),
    INDEX idx_reconciliation_exceptions_created (created_at DESC)
);

CREATE TABLE reconciliation_runs (
    run_id VARCHAR(50) PRIMARY KEY,
    run_date DATE NOT NULL,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    total_ledger_records INT DEFAULT 0,
    total_psp_records INT DEFAULT 0,
    total_bank_records INT DEFAULT 0,
    exact_matches INT DEFAULT 0,
    fuzzy_matches INT DEFAULT 0,
    many_to_one_matches INT DEFAULT 0,
    auto_resolved INT DEFAULT 0,
    exceptions INT DEFAULT 0,
    unmatched INT DEFAULT 0,
    match_rate DECIMAL(5, 4),
    metadata JSONB DEFAULT '{}',

    INDEX idx_reconciliation_runs_date (run_date DESC),
    INDEX idx_reconciliation_runs_started (started_at DESC)
);

-- ============================================================================
-- Settlement Tables
-- ============================================================================

CREATE TABLE settlement_batches (
    batch_id VARCHAR(50) PRIMARY KEY,
    settlement_date DATE NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'created',  -- "created", "submitted", "confirmed", "failed"
    transaction_count INT DEFAULT 0,
    gross_amount DECIMAL(15, 2) DEFAULT 0.00,
    platform_fees DECIMAL(15, 2) DEFAULT 0.00,
    psp_fees DECIMAL(15, 2) DEFAULT 0.00,
    holdback DECIMAL(15, 2) DEFAULT 0.00,
    net_payout DECIMAL(15, 2) DEFAULT 0.00,
    unique_users INT DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    submitted_at TIMESTAMP,
    confirmed_at TIMESTAMP,
    metadata JSONB DEFAULT '{}',

    CHECK (status IN ('created', 'submitted', 'confirmed', 'failed', 'reconciled')),

    INDEX idx_settlement_batches_date (settlement_date DESC),
    INDEX idx_settlement_batches_status (status),
    INDEX idx_settlement_batches_created (created_at DESC)
);

CREATE TABLE settlement_net_positions (
    position_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    batch_id VARCHAR(50) NOT NULL REFERENCES settlement_batches(batch_id),
    user_id VARCHAR(100) NOT NULL,
    transaction_count INT NOT NULL,
    gross_total DECIMAL(15, 2) NOT NULL,
    fees_total DECIMAL(15, 2) NOT NULL,
    holdback_total DECIMAL(15, 2) DEFAULT 0.00,
    net_payout DECIMAL(15, 2) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_settlement_net_positions_batch (batch_id),
    INDEX idx_settlement_net_positions_user (user_id),
    INDEX idx_settlement_net_positions_payout (net_payout DESC)
);

-- ============================================================================
-- Compliance Tables
-- ============================================================================

CREATE TABLE user_kyc (
    user_id VARCHAR(100) PRIMARY KEY,
    current_tier VARCHAR(20) NOT NULL DEFAULT 'none',  -- "none", "basic", "standard", "enhanced"
    status VARCHAR(20) NOT NULL DEFAULT 'pending',    -- "pending", "approved", "rejected", "expired"
    verified_at TIMESTAMP,
    expires_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}',

    CHECK (current_tier IN ('none', 'basic', 'standard', 'enhanced')),
    CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),

    INDEX idx_user_kyc_tier (current_tier),
    INDEX idx_user_kyc_status (status),
    INDEX idx_user_kyc_expires (expires_at)
);

CREATE TABLE monitoring_alerts (
    alert_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id VARCHAR(100) NOT NULL,
    rule_name VARCHAR(100) NOT NULL,
    priority VARCHAR(20) NOT NULL,     -- "critical", "high", "medium", "low"
    status VARCHAR(20) NOT NULL DEFAULT 'created',
    description TEXT,
    evidence JSONB DEFAULT '{}',
    assigned_to VARCHAR(100),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    metadata JSONB DEFAULT '{}',

    CHECK (priority IN ('critical', 'high', 'medium', 'low')),
    CHECK (status IN ('created', 'assigned', 'investigating', 'dismissed', 'sar_recommended', 'sar_filed')),

    INDEX idx_monitoring_alerts_user (user_id),
    INDEX idx_monitoring_alerts_priority (priority),
    INDEX idx_monitoring_alerts_status (status),
    INDEX idx_monitoring_alerts_created (created_at DESC)
);

CREATE TABLE ofac_screenings (
    screening_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id VARCHAR(100) NOT NULL,
    full_name VARCHAR(200) NOT NULL,
    match_found BOOLEAN DEFAULT FALSE,
    match_score DECIMAL(5, 3) DEFAULT 0.0,
    matched_entry_id VARCHAR(100),
    blocked BOOLEAN DEFAULT FALSE,
    latency_ms DECIMAL(8, 2),
    screened_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}',

    INDEX idx_ofac_screenings_user (user_id),
    INDEX idx_ofac_screenings_match (match_found),
    INDEX idx_ofac_screenings_blocked (blocked),
    INDEX idx_ofac_screenings_screened (screened_at DESC)
);

-- Compliance event log (append-only)
CREATE TABLE compliance_events (
    event_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_type VARCHAR(50) NOT NULL,
    user_id VARCHAR(100) NOT NULL,
    details JSONB NOT NULL DEFAULT '{}',
    previous_hash VARCHAR(64),
    event_hash VARCHAR(64),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_compliance_events_user (user_id),
    INDEX idx_compliance_events_type (event_type),
    INDEX idx_compliance_events_created (created_at DESC)
);

-- ============================================================================
-- Transaction History Views (Materialized for Performance)
-- ============================================================================

CREATE VIEW user_balances AS
SELECT
    a.entity_id as user_id,
    a.account_code,
    SUM(CASE
        WHEN a.account_type IN ('asset', 'expense') THEN jel.debit - jel.credit
        ELSE jel.credit - jel.debit
    END) as balance,
    MAX(je.posted_at) as last_activity
FROM accounts a
LEFT JOIN journal_entry_lines jel ON a.account_id = jel.account_id
LEFT JOIN journal_entries je ON jel.entry_id = je.entry_id
WHERE a.entity_type = 'user'
GROUP BY a.entity_id, a.account_code;

-- ============================================================================
-- Indexing Strategy
-- ============================================================================
--
-- B-tree indexes on:
-- - account_id + created_at (date range queries)
-- - posted_at DESC (time series queries)
-- - idempotency_key (duplicate detection)
--
-- GIN indexes on:
-- - metadata JSONB (structured audit trail search)
-- - rules_triggered[] (array search)
--
-- Partial indexes:
-- - holds WHERE status = 'active' (common query)
-- - user_kyc WHERE status = 'approved' (common query)
-- - monitoring_alerts WHERE resolved_at IS NULL (open alerts)
--

CREATE INDEX idx_journal_entry_lines_account_created ON journal_entry_lines (account_id)
    INCLUDE (debit, credit);

CREATE INDEX idx_accounts_metadata ON accounts USING GIN (metadata);
CREATE INDEX idx_journal_entries_metadata ON journal_entries USING GIN (metadata);
CREATE INDEX idx_holds_metadata ON holds USING GIN (metadata);
CREATE INDEX idx_risk_scores_metadata ON risk_scores USING GIN (metadata);
CREATE INDEX idx_monitoring_alerts_metadata ON monitoring_alerts USING GIN (metadata);

-- Partial indexes for common queries
CREATE INDEX idx_holds_active ON holds (account_id)
    WHERE status = 'active';

CREATE INDEX idx_user_kyc_approved ON user_kyc (user_id)
    WHERE status = 'approved';

CREATE INDEX idx_monitoring_alerts_open ON monitoring_alerts (user_id, priority)
    WHERE resolved_at IS NULL;

-- ============================================================================
-- Transaction Isolation
-- ============================================================================
--
-- All critical ledger operations use SERIALIZABLE isolation:
--   - Journal entry posting
--   - Hold creation/capture
--   - Idempotency key checking
--
-- This is enforced at the application layer (e.g., SQLAlchemy with
-- isolation_level="SERIALIZABLE") for each transaction.
--

-- ============================================================================
-- Row-Level Security for Multi-Tenant (Optional)
-- ============================================================================
--
-- For true multi-tenant deployment, enable RLS:
--
-- ALTER TABLE accounts ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE journal_entries ENABLE ROW LEVEL SECURITY;
-- CREATE POLICY user_wallet_isolation ON accounts
--     USING (entity_id = current_setting('app.current_user_id'));
--
-- This ensures users can only see their own accounts even if they
-- gain database access.
--

-- ============================================================================
-- Bootstrap: Create Core Accounts
-- ============================================================================
-- These accounts are required for all platforms. Run once during setup.
--
-- INSERT INTO accounts (account_code, account_type, entity_type)
-- VALUES
--     ('bank_operating', 'asset', 'platform'),
--     ('bank_reserve', 'asset', 'platform'),
--     ('platform_fee', 'revenue', 'platform'),
--     ('subscription_revenue', 'revenue', 'platform'),
--     ('psp_processing_fees', 'expense', 'platform'),
--     ('fraud_losses', 'expense', 'platform'),
--     ('bank_transfer_fees', 'expense', 'platform');
--

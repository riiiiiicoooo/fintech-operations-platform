-- ============================================================================
-- Supabase/PostgreSQL Migration: Fintech Operations Platform
-- Database: fintech_ops
-- Version: 001
-- Description: Complete double-entry ledger schema with fraud detection,
--              reconciliation, settlement, and compliance tables
-- ============================================================================

BEGIN;

-- ============================================================================
-- Extensions
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================================
-- Core Account & Ledger Tables
-- ============================================================================

CREATE TABLE IF NOT EXISTS accounts (
    account_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_code VARCHAR(50) NOT NULL,
    account_type VARCHAR(20) NOT NULL,
    entity_id VARCHAR(100),
    entity_type VARCHAR(20),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}',

    UNIQUE(account_code, COALESCE(entity_id, '')),
    CHECK (account_type IN ('asset', 'liability', 'revenue', 'expense')),
    CHECK (entity_type IN ('user', 'employer', 'platform', 'psp', NULL))
);

CREATE INDEX idx_accounts_code_entity ON accounts (account_code, entity_id);
CREATE INDEX idx_accounts_type ON accounts (account_type);
CREATE INDEX idx_accounts_created ON accounts (created_at DESC);
CREATE INDEX idx_accounts_metadata ON accounts USING GIN (metadata);

-- ============================================================================
-- Journal Entries (Immutable Append-Only Log)
-- ============================================================================

CREATE TABLE IF NOT EXISTS journal_entries (
    entry_id VARCHAR(50) PRIMARY KEY,
    entry_type VARCHAR(30) NOT NULL,
    description TEXT NOT NULL,
    idempotency_key VARCHAR(100) NOT NULL UNIQUE,
    posted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reference_type VARCHAR(30),
    reference_id VARCHAR(50),
    metadata JSONB DEFAULT '{}',

    CHECK (entry_type IN ('funding', 'transfer', 'fee', 'refund', 'chargeback',
                          'settlement', 'reversal', 'correction', 'suspense_classification'))
);

CREATE INDEX idx_journal_entries_posted ON journal_entries (posted_at DESC);
CREATE INDEX idx_journal_entries_entry_type ON journal_entries (entry_type);
CREATE INDEX idx_journal_entries_idempotency ON journal_entries (idempotency_key);
CREATE INDEX idx_journal_entries_reference ON journal_entries (reference_id);
CREATE INDEX idx_journal_entries_metadata ON journal_entries USING GIN (metadata);

-- ============================================================================
-- Journal Entry Lines (Double-Entry Detail)
-- ============================================================================

CREATE TABLE IF NOT EXISTS journal_entry_lines (
    line_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entry_id VARCHAR(50) NOT NULL REFERENCES journal_entries(entry_id) ON DELETE CASCADE,
    account_id UUID NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    debit DECIMAL(15, 2) NOT NULL DEFAULT 0.00,
    credit DECIMAL(15, 2) NOT NULL DEFAULT 0.00,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CHECK ((debit > 0 AND credit = 0) OR (debit = 0 AND credit > 0)),
    CHECK (debit >= 0 AND credit >= 0)
);

CREATE INDEX idx_journal_entry_lines_entry ON journal_entry_lines (entry_id);
CREATE INDEX idx_journal_entry_lines_account ON journal_entry_lines (account_id);
CREATE INDEX idx_journal_entry_lines_debit_credit ON journal_entry_lines (debit DESC, credit DESC);
CREATE INDEX idx_journal_entry_lines_account_created ON journal_entry_lines (account_id, created_at DESC);

-- Add partitioning support (comment out for initial deployment, uncomment after first quarter)
-- CREATE TABLE journal_entry_lines_2024_q1 PARTITION OF journal_entry_lines
--     FOR VALUES FROM ('2024-01-01') TO ('2024-04-01');

-- ============================================================================
-- Holds (Prevents Double-Spending During Settlement)
-- ============================================================================

CREATE TABLE IF NOT EXISTS holds (
    hold_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id UUID NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    amount DECIMAL(15, 2) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    transaction_id VARCHAR(100),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    metadata JSONB DEFAULT '{}',

    CHECK (status IN ('active', 'captured', 'voided', 'expired')),
    CHECK (amount > 0),
    CHECK (expires_at > created_at)
);

CREATE INDEX idx_holds_account ON holds (account_id);
CREATE INDEX idx_holds_status ON holds (status);
CREATE INDEX idx_holds_expires_at ON holds (expires_at);
CREATE INDEX idx_holds_transaction ON holds (transaction_id);
CREATE INDEX idx_holds_active ON holds (account_id) WHERE status = 'active';
CREATE INDEX idx_holds_metadata ON holds USING GIN (metadata);

-- ============================================================================
-- PSP Health & Routing Tables
-- ============================================================================

CREATE TABLE IF NOT EXISTS psp_health_scores (
    score_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    psp_name VARCHAR(100) NOT NULL UNIQUE,
    success_rate DECIMAL(5, 4) NOT NULL DEFAULT 0.0,
    p95_latency_ms DECIMAL(8, 2) NOT NULL DEFAULT 0.0,
    error_rate_15min DECIMAL(5, 4) NOT NULL DEFAULT 0.0,
    uptime_24h DECIMAL(5, 4) NOT NULL DEFAULT 1.0,
    health_score DECIMAL(5, 4) NOT NULL GENERATED ALWAYS AS (
        0.4 * success_rate + 0.3 * (1.0 - LEAST(p95_latency_ms / 1000.0, 1.0)) +
        0.2 * (1.0 - error_rate_15min) + 0.1 * uptime_24h
    ) STORED,
    circuit_breaker_status VARCHAR(20) NOT NULL DEFAULT 'closed',
    failure_count_60s INT DEFAULT 0,
    last_failure_at TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}',

    CHECK (circuit_breaker_status IN ('closed', 'open', 'half-open')),
    CHECK (health_score >= 0 AND health_score <= 1.0)
);

CREATE INDEX idx_psp_health_score ON psp_health_scores (health_score DESC);
CREATE INDEX idx_psp_health_status ON psp_health_scores (circuit_breaker_status);
CREATE INDEX idx_psp_health_updated ON psp_health_scores (updated_at DESC);

CREATE TABLE IF NOT EXISTS psp_routing_config (
    config_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    payment_method VARCHAR(50) NOT NULL UNIQUE,
    primary_psp VARCHAR(100) NOT NULL REFERENCES psp_health_scores(psp_name),
    fallback_psp VARCHAR(100) REFERENCES psp_health_scores(psp_name),
    health_threshold DECIMAL(5, 4) DEFAULT 0.8,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CHECK (payment_method IN ('ach', 'card', 'wire', 'instant_transfer'))
);

CREATE INDEX idx_psp_routing_method ON psp_routing_config (payment_method);

-- ============================================================================
-- Fraud Detection Tables
-- ============================================================================

CREATE TABLE IF NOT EXISTS risk_scores (
    score_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    transaction_id VARCHAR(100) NOT NULL UNIQUE,
    user_id VARCHAR(100) NOT NULL,
    raw_score DECIMAL(8, 2) NOT NULL,
    normalized_score DECIMAL(5, 2) NOT NULL,
    decision VARCHAR(20) NOT NULL,
    rules_triggered TEXT[] DEFAULT '{}',
    model_version VARCHAR(50) DEFAULT 'rules_v1',
    latency_ms DECIMAL(8, 2),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}',

    CHECK (normalized_score >= 0 AND normalized_score <= 100),
    CHECK (decision IN ('approve', 'review', 'decline'))
);

CREATE INDEX idx_risk_scores_user ON risk_scores (user_id);
CREATE INDEX idx_risk_scores_decision ON risk_scores (decision);
CREATE INDEX idx_risk_scores_created ON risk_scores (created_at DESC);
CREATE INDEX idx_risk_scores_score ON risk_scores (normalized_score DESC);
CREATE INDEX idx_risk_scores_rules ON risk_scores USING GIN (rules_triggered);
CREATE INDEX idx_risk_scores_metadata ON risk_scores USING GIN (metadata);

CREATE TABLE IF NOT EXISTS blocklists (
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

CREATE TABLE IF NOT EXISTS allowlists (
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

CREATE TABLE IF NOT EXISTS velocity_windows (
    window_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id VARCHAR(100) NOT NULL,
    window_type VARCHAR(20) NOT NULL,
    transaction_count INT DEFAULT 0,
    total_amount DECIMAL(15, 2) DEFAULT 0.00,
    window_start TIMESTAMP NOT NULL,
    window_end TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CHECK (window_type IN ('transactions_1h', 'transactions_24h', 'transactions_7d',
                           'amount_24h', 'amount_7d'))
);

CREATE INDEX idx_velocity_windows_user_type ON velocity_windows (user_id, window_type);
CREATE INDEX idx_velocity_windows_window_end ON velocity_windows (window_end DESC);

CREATE TABLE IF NOT EXISTS fraud_rules (
    rule_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    rule_name VARCHAR(100) NOT NULL UNIQUE,
    rule_type VARCHAR(30) NOT NULL,
    condition_json JSONB NOT NULL,
    risk_points INT NOT NULL,
    enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CHECK (rule_type IN ('velocity', 'amount', 'context', 'behavioral')),
    CHECK (risk_points >= 0 AND risk_points <= 100)
);

CREATE INDEX idx_fraud_rules_enabled ON fraud_rules (enabled);
CREATE INDEX idx_fraud_rules_type ON fraud_rules (rule_type);

-- ============================================================================
-- Reconciliation Tables
-- ============================================================================

CREATE TABLE IF NOT EXISTS reconciliation_runs (
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
    total_unmatched_amount DECIMAL(15, 2) DEFAULT 0.00,
    metadata JSONB DEFAULT '{}',

    INDEX idx_reconciliation_runs_date (run_date DESC),
    INDEX idx_reconciliation_runs_started (started_at DESC)
);

CREATE TABLE IF NOT EXISTS reconciliation_matches (
    match_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    reconciliation_run_id VARCHAR(50) NOT NULL REFERENCES reconciliation_runs(run_id),
    ledger_entry_id VARCHAR(50),
    psp_transaction_id VARCHAR(100),
    bank_reference VARCHAR(100),
    match_status VARCHAR(30) NOT NULL,
    break_type VARCHAR(30),
    delta_amount DECIMAL(15, 2) DEFAULT 0.00,
    resolution_notes TEXT,
    matched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}',

    CHECK (match_status IN ('exact_match', 'fuzzy_match', 'many_to_one',
                            'auto_resolved', 'exception', 'unmatched')),
    CHECK (break_type IN ('timing', 'fee_deduction', 'fx_rounding', 'quantity',
                          'missing', 'duplicate', 'amount', NULL))
);

CREATE INDEX idx_reconciliation_matches_run ON reconciliation_matches (reconciliation_run_id);
CREATE INDEX idx_reconciliation_matches_status ON reconciliation_matches (match_status);
CREATE INDEX idx_reconciliation_matches_ledger ON reconciliation_matches (ledger_entry_id);
CREATE INDEX idx_reconciliation_matches_psp ON reconciliation_matches (psp_transaction_id);
CREATE INDEX idx_reconciliation_matches_bank ON reconciliation_matches (bank_reference);
CREATE INDEX idx_reconciliation_matches_metadata ON reconciliation_matches USING GIN (metadata);

CREATE TABLE IF NOT EXISTS reconciliation_exceptions (
    exception_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    match_id UUID REFERENCES reconciliation_matches(match_id) ON DELETE CASCADE,
    run_id VARCHAR(50) REFERENCES reconciliation_runs(run_id),
    break_type VARCHAR(30) NOT NULL,
    priority VARCHAR(20) NOT NULL,
    delta_amount DECIMAL(15, 2) NOT NULL,
    description TEXT,
    assigned_to VARCHAR(100),
    resolved BOOLEAN DEFAULT FALSE,
    resolved_at TIMESTAMP,
    resolution_notes TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}',

    CHECK (priority IN ('critical', 'high', 'medium', 'low')),
    CHECK (break_type IN ('timing', 'fee_deduction', 'fx_rounding', 'quantity',
                          'missing', 'duplicate', 'amount'))
);

CREATE INDEX idx_reconciliation_exceptions_priority ON reconciliation_exceptions (priority);
CREATE INDEX idx_reconciliation_exceptions_resolved ON reconciliation_exceptions (resolved);
CREATE INDEX idx_reconciliation_exceptions_assigned ON reconciliation_exceptions (assigned_to);
CREATE INDEX idx_reconciliation_exceptions_created ON reconciliation_exceptions (created_at DESC);
CREATE INDEX idx_reconciliation_exceptions_run ON reconciliation_exceptions (run_id);

-- ============================================================================
-- Settlement Tables
-- ============================================================================

CREATE TABLE IF NOT EXISTS settlement_batches (
    batch_id VARCHAR(50) PRIMARY KEY,
    settlement_date DATE NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'created',
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

    CHECK (status IN ('created', 'submitted', 'confirmed', 'failed', 'reconciled'))
);

CREATE INDEX idx_settlement_batches_date ON settlement_batches (settlement_date DESC);
CREATE INDEX idx_settlement_batches_status ON settlement_batches (status);
CREATE INDEX idx_settlement_batches_created ON settlement_batches (created_at DESC);
CREATE INDEX idx_settlement_batches_metadata ON settlement_batches USING GIN (metadata);

CREATE TABLE IF NOT EXISTS settlement_net_positions (
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

CREATE TABLE IF NOT EXISTS nacha_batches (
    batch_id VARCHAR(50) PRIMARY KEY,
    settlement_batch_id VARCHAR(50) NOT NULL REFERENCES settlement_batches(batch_id),
    file_name VARCHAR(255) NOT NULL,
    batch_number INT NOT NULL,
    entry_count INT NOT NULL,
    total_debits DECIMAL(15, 2) NOT NULL,
    total_credits DECIMAL(15, 2) NOT NULL,
    effective_date DATE NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'created',
    nacha_content TEXT,
    submitted_at TIMESTAMP,
    acknowledgment_received_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CHECK (status IN ('created', 'submitted', 'acknowledged', 'failed'))
);

CREATE INDEX idx_nacha_batches_settlement ON nacha_batches (settlement_batch_id);
CREATE INDEX idx_nacha_batches_status ON nacha_batches (status);
CREATE INDEX idx_nacha_batches_effective_date ON nacha_batches (effective_date DESC);

-- ============================================================================
-- Compliance Tables
-- ============================================================================

CREATE TABLE IF NOT EXISTS user_kyc (
    user_id VARCHAR(100) PRIMARY KEY,
    current_tier VARCHAR(20) NOT NULL DEFAULT 'none',
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    verified_at TIMESTAMP,
    expires_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}',

    CHECK (current_tier IN ('none', 'basic', 'standard', 'enhanced')),
    CHECK (status IN ('pending', 'approved', 'rejected', 'expired'))
);

CREATE INDEX idx_user_kyc_tier ON user_kyc (current_tier);
CREATE INDEX idx_user_kyc_status ON user_kyc (status);
CREATE INDEX idx_user_kyc_expires ON user_kyc (expires_at);
CREATE INDEX idx_user_kyc_approved ON user_kyc (user_id) WHERE status = 'approved';

CREATE TABLE IF NOT EXISTS monitoring_alerts (
    alert_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id VARCHAR(100) NOT NULL,
    rule_name VARCHAR(100) NOT NULL,
    priority VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'created',
    description TEXT,
    evidence JSONB DEFAULT '{}',
    assigned_to VARCHAR(100),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    metadata JSONB DEFAULT '{}',

    CHECK (priority IN ('critical', 'high', 'medium', 'low')),
    CHECK (status IN ('created', 'assigned', 'investigating', 'dismissed', 'sar_recommended', 'sar_filed'))
);

CREATE INDEX idx_monitoring_alerts_user ON monitoring_alerts (user_id);
CREATE INDEX idx_monitoring_alerts_priority ON monitoring_alerts (priority);
CREATE INDEX idx_monitoring_alerts_status ON monitoring_alerts (status);
CREATE INDEX idx_monitoring_alerts_created ON monitoring_alerts (created_at DESC);
CREATE INDEX idx_monitoring_alerts_open ON monitoring_alerts (user_id, priority) WHERE resolved_at IS NULL;
CREATE INDEX idx_monitoring_alerts_metadata ON monitoring_alerts USING GIN (metadata);

CREATE TABLE IF NOT EXISTS ofac_screenings (
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

CREATE TABLE IF NOT EXISTS compliance_events (
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
-- Views for Reporting
-- ============================================================================

CREATE OR REPLACE VIEW user_balances AS
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

CREATE OR REPLACE VIEW reconciliation_summary AS
SELECT
    rr.run_id,
    rr.run_date,
    rr.started_at,
    rr.completed_at,
    rr.exact_matches,
    rr.fuzzy_matches,
    rr.many_to_one_matches,
    rr.auto_resolved,
    rr.exceptions,
    rr.unmatched,
    ROUND(CAST(rr.exact_matches + rr.fuzzy_matches + rr.many_to_one_matches + rr.auto_resolved AS NUMERIC) /
          NULLIF(rr.total_ledger_records + rr.total_psp_records + rr.total_bank_records, 0) * 100, 2) as match_rate_pct
FROM reconciliation_runs rr
ORDER BY rr.run_date DESC;

-- ============================================================================
-- Row-Level Security (RLS) Policies
-- ============================================================================
-- Uncomment and customize for multi-tenant deployments

-- ALTER TABLE accounts ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE journal_entries ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE monitoring_alerts ENABLE ROW LEVEL SECURITY;

-- -- Finance team: see all records
-- CREATE POLICY finance_access ON accounts
--     FOR SELECT USING (current_setting('app.user_role') = 'finance');

-- -- Operations team: see only transaction data, not compliance alerts
-- CREATE POLICY ops_transaction_access ON journal_entries
--     FOR SELECT USING (current_setting('app.user_role') = 'operations');

-- -- Compliance team: see only alerts and KYC records
-- CREATE POLICY compliance_access ON monitoring_alerts
--     FOR SELECT USING (current_setting('app.user_role') = 'compliance');

-- ============================================================================
-- Bootstrap Data (Run after schema creation)
-- ============================================================================

-- Core platform accounts (uncomment to auto-initialize)
-- INSERT INTO accounts (account_code, account_type, entity_type)
-- VALUES
--     ('bank_operating', 'asset', 'platform'),
--     ('bank_reserve', 'asset', 'platform'),
--     ('platform_fee', 'revenue', 'platform'),
--     ('subscription_revenue', 'revenue', 'platform'),
--     ('psp_processing_fees', 'expense', 'platform'),
--     ('fraud_losses', 'expense', 'platform'),
--     ('bank_transfer_fees', 'expense', 'platform')
-- ON CONFLICT DO NOTHING;

-- -- PSP routing defaults (Stripe primary, Adyen fallback)
-- INSERT INTO psp_health_scores (psp_name, success_rate, uptime_24h)
-- VALUES
--     ('stripe', 0.998, 0.999),
--     ('adyen', 0.996, 0.998)
-- ON CONFLICT (psp_name) DO NOTHING;

-- INSERT INTO psp_routing_config (payment_method, primary_psp, fallback_psp)
-- VALUES
--     ('ach', 'stripe', 'adyen'),
--     ('card', 'stripe', 'adyen'),
--     ('wire', 'stripe', NULL),
--     ('instant_transfer', 'stripe', 'adyen')
-- ON CONFLICT (payment_method) DO NOTHING;

-- Default fraud rules
-- INSERT INTO fraud_rules (rule_name, rule_type, condition_json, risk_points)
-- VALUES
--     ('velocity_hourly', 'velocity', '{"max_transactions": 5}', 25),
--     ('velocity_daily_amount', 'velocity', '{"max_amount": 2000}', 20),
--     ('amount_multiplier', 'amount', '{"multiplier": 3.0}', 20),
--     ('new_user_risk', 'context', '{"age_hours": 24}', 20),
--     ('new_device', 'context', '{"days_since_first_seen": 0}', 15)
-- ON CONFLICT (rule_name) DO NOTHING;

COMMIT;

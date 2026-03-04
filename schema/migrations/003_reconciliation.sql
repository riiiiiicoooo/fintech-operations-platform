-- ============================================================================
-- Migration 003: Reconciliation and Settlement Tables
-- Executed: After fraud detection tables deployed
-- ============================================================================

BEGIN;

-- Reconciliation matching tables
CREATE TABLE reconciliation_matches (
    match_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    reconciliation_run_id VARCHAR(50) NOT NULL,
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

-- Settlement tables
CREATE TABLE settlement_batches (
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

-- Compliance tables
CREATE TABLE user_kyc (
    user_id VARCHAR(100) PRIMARY KEY,
    current_tier VARCHAR(20) NOT NULL DEFAULT 'none',
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
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

-- Partial index for approved KYC users
CREATE INDEX idx_user_kyc_approved ON user_kyc (user_id)
    WHERE status = 'approved';

CREATE TABLE monitoring_alerts (
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
    CHECK (status IN ('created', 'assigned', 'investigating', 'dismissed', 'sar_recommended', 'sar_filed')),

    INDEX idx_monitoring_alerts_user (user_id),
    INDEX idx_monitoring_alerts_priority (priority),
    INDEX idx_monitoring_alerts_status (status),
    INDEX idx_monitoring_alerts_created (created_at DESC)
);

-- Partial index for open alerts
CREATE INDEX idx_monitoring_alerts_open ON monitoring_alerts (user_id, priority)
    WHERE resolved_at IS NULL;

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

-- GIN indexes for metadata queries
CREATE INDEX idx_reconciliation_matches_metadata ON reconciliation_matches USING GIN (metadata);
CREATE INDEX idx_reconciliation_exceptions_metadata ON reconciliation_exceptions USING GIN (metadata);
CREATE INDEX idx_settlement_batches_metadata ON settlement_batches USING GIN (metadata);
CREATE INDEX idx_monitoring_alerts_metadata ON monitoring_alerts USING GIN (metadata);

COMMIT;

-- ============================================================================
-- Migration 002: Fraud Detection Tables
-- Executed: After initial ledger deployment
-- ============================================================================

BEGIN;

CREATE TABLE risk_scores (
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
    window_type VARCHAR(20) NOT NULL,
    transaction_count INT DEFAULT 0,
    total_amount DECIMAL(15, 2) DEFAULT 0.00,
    window_start TIMESTAMP NOT NULL,
    window_end TIMESTAMP NOT NULL,

    CHECK (window_type IN ('transactions_1h', 'transactions_24h', 'transactions_7d',
                           'amount_24h', 'amount_7d')),

    INDEX idx_velocity_windows_user_type (user_id, window_type),
    INDEX idx_velocity_windows_window_end (window_end DESC)
);

-- GIN index for metadata queries
CREATE INDEX idx_risk_scores_metadata ON risk_scores USING GIN (metadata);
CREATE INDEX idx_blocklists_metadata ON blocklists USING GIN (metadata);
CREATE INDEX idx_allowlists_metadata ON allowlists USING GIN (metadata);

COMMIT;

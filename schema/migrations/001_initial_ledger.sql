-- ============================================================================
-- Migration 001: Initial Ledger Schema
-- Executed: First deployment
-- Rollback: Requires dropping all ledger-related tables
-- ============================================================================

BEGIN;

CREATE TABLE accounts (
    account_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_code VARCHAR(50) NOT NULL,
    account_type VARCHAR(20) NOT NULL,
    entity_id VARCHAR(100),
    entity_type VARCHAR(20),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}',

    UNIQUE(account_code, COALESCE(entity_id, '')),
    CHECK (account_type IN ('asset', 'liability', 'revenue', 'expense')),

    INDEX idx_accounts_code_entity (account_code, entity_id),
    INDEX idx_accounts_type (account_type),
    INDEX idx_accounts_created (created_at DESC)
);

CREATE TABLE journal_entries (
    entry_id VARCHAR(50) PRIMARY KEY,
    entry_type VARCHAR(30) NOT NULL,
    description TEXT NOT NULL,
    idempotency_key VARCHAR(100) NOT NULL UNIQUE,
    posted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reference_type VARCHAR(30),
    reference_id VARCHAR(50),
    metadata JSONB DEFAULT '{}',

    CHECK (entry_type IN ('funding', 'transfer', 'fee', 'refund', 'chargeback',
                          'settlement', 'reversal', 'correction', 'suspense_classification')),

    INDEX idx_journal_entries_posted (posted_at DESC),
    INDEX idx_journal_entries_entry_type (entry_type),
    INDEX idx_journal_entries_idempotency (idempotency_key),
    INDEX idx_journal_entries_reference (reference_id)
);

CREATE TABLE journal_entry_lines (
    line_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entry_id VARCHAR(50) NOT NULL REFERENCES journal_entries(entry_id),
    account_id UUID NOT NULL REFERENCES accounts(account_id),
    debit DECIMAL(15, 2) NOT NULL DEFAULT 0.00,
    credit DECIMAL(15, 2) NOT NULL DEFAULT 0.00,

    CHECK ((debit > 0 AND credit = 0) OR (debit = 0 AND credit > 0)),
    CHECK (debit >= 0 AND credit >= 0),

    INDEX idx_journal_entry_lines_entry (entry_id),
    INDEX idx_journal_entry_lines_account (account_id),
    INDEX idx_journal_entry_lines_debit_credit (debit DESC, credit DESC)
);

CREATE TABLE holds (
    hold_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id UUID NOT NULL REFERENCES accounts(account_id),
    amount DECIMAL(15, 2) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
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

-- Partial index for active holds (common query)
CREATE INDEX idx_holds_active ON holds (account_id)
    WHERE status = 'active';

-- GIN index for metadata queries
CREATE INDEX idx_accounts_metadata ON accounts USING GIN (metadata);
CREATE INDEX idx_journal_entries_metadata ON journal_entries USING GIN (metadata);
CREATE INDEX idx_holds_metadata ON holds USING GIN (metadata);

-- View for user balances
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

COMMIT;

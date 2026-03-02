"""
Test Suite: Ledger Engine Double-Entry Invariants

Demonstrates that the ledger's core guarantees hold across all transaction types:
1. Every journal entry is balanced (debits == credits)
2. System-wide debits always equal system-wide credits
3. Holds prevent double-spending
4. Idempotency prevents double-posting
5. Refunds and chargebacks are recorded as new entries (originals unchanged)

Run: pytest test_ledger_engine.py -v
"""

import pytest
from ledger.ledger_engine import (
    LedgerEngine, AccountCode, AccountType, EntryType,
    JournalEntryLine, HoldStatus,
)


@pytest.fixture
def ledger():
    """Set up a ledger with the core accounts needed for testing."""
    engine = LedgerEngine()

    # Platform accounts
    engine.create_account(AccountCode.BANK_OPERATING)
    engine.create_account(AccountCode.PLATFORM_FEE)
    engine.create_account(AccountCode.PSP_PROCESSING_FEES)
    engine.create_account(AccountCode.FRAUD_LOSSES)
    engine.create_account(AccountCode.SUSPENSE)

    # Employer accounts
    engine.create_account(AccountCode.EMPLOYER_FUNDING_HOLDING, "employer_001", "employer")

    # User accounts
    engine.create_account(AccountCode.USER_WALLET, "user_001", "user")
    engine.create_account(AccountCode.USER_WALLET, "user_002", "user")

    # PSP accounts
    engine.create_account(AccountCode.PSP_RECEIVABLE, "stripe", "psp")
    engine.create_account(AccountCode.PSP_RECEIVABLE, "adyen", "psp")

    return engine


class TestDoubleEntryInvariant:
    """The fundamental guarantee: debits always equal credits."""

    def test_balanced_entry_succeeds(self, ledger):
        """A properly balanced entry posts successfully."""
        bank = ledger.get_account(AccountCode.BANK_OPERATING)
        holding = ledger.get_account(AccountCode.EMPLOYER_FUNDING_HOLDING, "employer_001")

        entry = ledger.post_entry(
            entry_type=EntryType.FUNDING,
            description="Test funding",
            idempotency_key="test_balanced_001",
            lines=[
                JournalEntryLine(account=bank, debit=1000.00),
                JournalEntryLine(account=holding, credit=1000.00),
            ],
        )

        assert entry is not None
        assert entry.total_amount == 1000.00

    def test_unbalanced_entry_rejected(self, ledger):
        """An unbalanced entry is rejected at creation time."""
        bank = ledger.get_account(AccountCode.BANK_OPERATING)
        holding = ledger.get_account(AccountCode.EMPLOYER_FUNDING_HOLDING, "employer_001")

        with pytest.raises(ValueError, match="not balanced"):
            ledger.post_entry(
                entry_type=EntryType.FUNDING,
                description="Unbalanced entry",
                idempotency_key="test_unbalanced_001",
                lines=[
                    JournalEntryLine(account=bank, debit=1000.00),
                    JournalEntryLine(account=holding, credit=999.00),
                ],
            )

    def test_system_balance_after_multiple_entries(self, ledger):
        """System-wide debits equal credits after a series of transactions."""
        # Fund employer
        ledger.record_employer_funding("employer_001", 5000.00, "sys_test_fund")

        # Allocate to two users
        ledger.allocate_to_wallet("employer_001", "user_001", 2000.00, 25.00, "sys_test_alloc_1")
        ledger.allocate_to_wallet("employer_001", "user_002", 1500.00, 18.75, "sys_test_alloc_2")

        # User transfer
        ledger.record_user_transfer("user_001", 500.00, "stripe", "sys_test_transfer")

        # Verify system balance
        result = ledger.verify_system_balance()
        assert result["balanced"] is True
        assert result["delta"] == 0.0
        assert result["entry_count"] == 4


class TestBalanceCalculation:
    """Balance calculations from journal entry lines."""

    def test_asset_balance_increases_with_debit(self, ledger):
        """Asset accounts (like bank) increase when debited."""
        bank = ledger.get_account(AccountCode.BANK_OPERATING)
        holding = ledger.get_account(AccountCode.EMPLOYER_FUNDING_HOLDING, "employer_001")

        ledger.post_entry(
            entry_type=EntryType.FUNDING,
            description="Funding",
            idempotency_key="bal_test_001",
            lines=[
                JournalEntryLine(account=bank, debit=5000.00),
                JournalEntryLine(account=holding, credit=5000.00),
            ],
        )

        assert ledger.get_posted_balance(bank) == 5000.00

    def test_wallet_balance_after_allocation(self, ledger):
        """User wallet shows correct balance after funding and allocation."""
        ledger.record_employer_funding("employer_001", 5000.00, "wallet_fund")
        ledger.allocate_to_wallet("employer_001", "user_001", 2000.00, 25.00, "wallet_alloc")

        wallet = ledger.get_account(AccountCode.USER_WALLET, "user_001")
        assert ledger.get_posted_balance(wallet) == 1975.00  # 2000 - 25 fee

    def test_revenue_balance_accumulates(self, ledger):
        """Platform fee revenue accumulates across multiple allocations."""
        ledger.record_employer_funding("employer_001", 5000.00, "rev_fund")
        ledger.allocate_to_wallet("employer_001", "user_001", 2000.00, 25.00, "rev_alloc_1")
        ledger.allocate_to_wallet("employer_001", "user_002", 1000.00, 12.50, "rev_alloc_2")

        fee_account = ledger.get_account(AccountCode.PLATFORM_FEE)
        assert ledger.get_posted_balance(fee_account) == 37.50  # 25.00 + 12.50


class TestHoldManagement:
    """Holds prevent double-spending during async PSP processing."""

    def test_hold_reduces_available_balance(self, ledger):
        """Active hold reduces available balance but not posted balance."""
        ledger.record_employer_funding("employer_001", 5000.00, "hold_fund")
        ledger.allocate_to_wallet("employer_001", "user_001", 1000.00, 0.00, "hold_alloc")

        wallet = ledger.get_account(AccountCode.USER_WALLET, "user_001")

        # Create hold
        hold = ledger.create_hold(wallet, 400.00, "txn_001")

        assert ledger.get_posted_balance(wallet) == 1000.00
        assert ledger.get_available_balance(wallet) == 600.00

    def test_cannot_overspend_with_holds(self, ledger):
        """Transaction is rejected if it would exceed available balance (considering holds)."""
        ledger.record_employer_funding("employer_001", 5000.00, "overspend_fund")
        ledger.allocate_to_wallet("employer_001", "user_001", 500.00, 0.00, "overspend_alloc")

        wallet = ledger.get_account(AccountCode.USER_WALLET, "user_001")

        # Hold most of the balance
        ledger.create_hold(wallet, 400.00, "txn_001")

        # Try to hold more than available
        with pytest.raises(ValueError, match="Insufficient"):
            ledger.create_hold(wallet, 200.00, "txn_002")

    def test_voided_hold_restores_available(self, ledger):
        """Voiding a hold restores the available balance."""
        ledger.record_employer_funding("employer_001", 5000.00, "void_fund")
        ledger.allocate_to_wallet("employer_001", "user_001", 1000.00, 0.00, "void_alloc")

        wallet = ledger.get_account(AccountCode.USER_WALLET, "user_001")
        hold = ledger.create_hold(wallet, 400.00, "txn_001")

        assert ledger.get_available_balance(wallet) == 600.00

        ledger.void_hold(hold.hold_id)

        assert ledger.get_available_balance(wallet) == 1000.00


class TestIdempotency:
    """Duplicate requests return the same result without double-posting."""

    def test_duplicate_entry_returns_cached(self, ledger):
        """Posting with the same idempotency key returns the original entry."""
        first = ledger.record_employer_funding("employer_001", 5000.00, "idemp_key_001")
        second = ledger.record_employer_funding("employer_001", 5000.00, "idemp_key_001")

        assert first.entry_id == second.entry_id
        assert len(ledger.journal_entries) == 1

    def test_different_keys_create_separate_entries(self, ledger):
        """Different idempotency keys create separate entries."""
        ledger.record_employer_funding("employer_001", 1000.00, "idemp_key_a")
        ledger.record_employer_funding("employer_001", 1000.00, "idemp_key_b")

        assert len(ledger.journal_entries) == 2

        bank = ledger.get_account(AccountCode.BANK_OPERATING)
        assert ledger.get_posted_balance(bank) == 2000.00


class TestRefundsAndChargebacks:
    """Corrections create new entries; originals are never modified."""

    def test_refund_creates_reversing_entry(self, ledger):
        """Refund adds a new entry without modifying the original."""
        ledger.record_employer_funding("employer_001", 5000.00, "refund_fund")
        ledger.allocate_to_wallet("employer_001", "user_001", 500.00, 0.00, "refund_alloc")

        original_entry, _ = ledger.record_user_transfer("user_001", 200.00, "stripe", "refund_xfer")
        original_count = len(ledger.journal_entries)

        ledger.record_refund(original_entry.entry_id, "user_001", 200.00, "stripe", "refund_001")

        # New entry added, original unchanged
        assert len(ledger.journal_entries) == original_count + 1

        # User wallet balance restored
        wallet = ledger.get_account(AccountCode.USER_WALLET, "user_001")
        assert ledger.get_posted_balance(wallet) == 500.00  # Back to pre-transfer balance

        # System still balanced
        assert ledger.verify_system_balance()["balanced"] is True

    def test_chargeback_records_fraud_loss(self, ledger):
        """Chargeback debits fraud_losses and credits PSP receivable."""
        ledger.record_chargeback(150.00, "stripe", "chargeback_001")

        fraud = ledger.get_account(AccountCode.FRAUD_LOSSES)
        assert ledger.get_posted_balance(fraud) == 150.00

        assert ledger.verify_system_balance()["balanced"] is True


class TestMultiPartyTransaction:
    """End-to-end flow: employer funds, user receives, platform collects fee."""

    def test_full_transaction_flow(self, ledger):
        """
        Complete flow:
        1. Employer deposits $5,000
        2. $2,000 allocated to user (1.25% = $25 fee, user gets $1,975)
        3. User transfers $500 out via Stripe
        4. Verify all balances and system integrity
        """
        # Step 1: Employer funding
        ledger.record_employer_funding("employer_001", 5000.00, "e2e_fund")

        # Step 2: Allocate to user with platform fee
        ledger.allocate_to_wallet("employer_001", "user_001", 2000.00, 25.00, "e2e_alloc")

        # Step 3: User transfer
        ledger.record_user_transfer("user_001", 500.00, "stripe", "e2e_transfer")

        # Verify balances
        bank = ledger.get_account(AccountCode.BANK_OPERATING)
        holding = ledger.get_account(AccountCode.EMPLOYER_FUNDING_HOLDING, "employer_001")
        wallet = ledger.get_account(AccountCode.USER_WALLET, "user_001")
        fee = ledger.get_account(AccountCode.PLATFORM_FEE)
        psp = ledger.get_account(AccountCode.PSP_RECEIVABLE, "stripe")

        assert ledger.get_posted_balance(bank) == 5000.00      # Employer's money in bank
        assert ledger.get_posted_balance(holding) == 3000.00    # 5000 - 2000 allocated
        assert ledger.get_posted_balance(wallet) == 1475.00     # 1975 - 500 transferred
        assert ledger.get_posted_balance(fee) == 25.00          # Platform fee collected
        assert ledger.get_posted_balance(psp) == 500.00         # PSP owes us for transfer

        # System balanced
        result = ledger.verify_system_balance()
        assert result["balanced"] is True
        assert result["entry_count"] == 3

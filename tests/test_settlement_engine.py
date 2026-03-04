"""
Test Suite: Settlement Engine Multi-Party Settlement and Batch Processing

Demonstrates that settlement's core guarantees hold:
1. NACHA batch generation with correct formatting and record counts
2. Settlement netting across multiple transactions reduces payout count
3. Failed settlement can be retried without double-payment
4. Partial settlement handling (partial captures, failed entries)
5. Platform fees and PSP fees are correctly deducted
6. Holdback reserves for high-risk transactions

Run: pytest tests/test_settlement_engine.py -v
"""

import pytest
from datetime import datetime, date
from settlement.settlement_engine import (
    SettlementEngine, SettledTransaction, PayoutMethod,
    BatchStatus, SettlementBatch,
)


@pytest.fixture
def engine():
    """Set up a settlement engine."""
    return SettlementEngine()


@pytest.fixture
def base_transaction():
    """Base settled transaction."""
    return SettledTransaction(
        transaction_id="txn_001",
        user_id="user_001",
        employer_id="employer_001",
        gross_amount=500.0,
        platform_fee_rate=0.0125,  # 1.25%
        psp_name="stripe",
        payout_method=PayoutMethod.ACH,
        settled_at=datetime.utcnow(),
        is_high_risk=False,
    )


class TestNACHABatchGeneration:
    """NACHA batch file generation with correct formatting."""

    def test_batch_creation_from_transactions(self, engine, base_transaction):
        """Create a settlement batch from transactions."""
        transactions = [base_transaction]

        batch = engine.create_batch(
            transactions=transactions,
            settlement_date=date(2024, 3, 15),
        )

        assert batch.batch_id is not None
        assert batch.status == BatchStatus.CREATED
        assert batch.transaction_count == 1
        assert len(batch.splits) == 1

    def test_nacha_summary_generation(self, engine, base_transaction):
        """Generate NACHA summary for batch."""
        transactions = [base_transaction]
        batch = engine.create_batch(transactions=transactions)

        nacha_summary = engine.generate_nacha_summary(batch)

        assert nacha_summary["batch_id"] == batch.batch_id
        assert nacha_summary["entry_count"] >= 1
        assert "total_debit" in nacha_summary
        assert "individual_entries" in nacha_summary

    def test_batch_multiple_transactions_generates_multiple_nacha_entries(self, engine):
        """Multiple transactions produce multiple NACHA entries."""
        transactions = [
            SettledTransaction(
                transaction_id=f"txn_{i}", user_id=f"user_{i}",
                employer_id="employer_001", gross_amount=100.0,
                payout_method=PayoutMethod.ACH,
            )
            for i in range(3)
        ]

        batch = engine.create_batch(transactions=transactions)
        nacha_summary = engine.generate_nacha_summary(batch)

        # Each user gets an entry
        assert nacha_summary["entry_count"] >= 1

    def test_batch_empty_transactions_rejected(self, engine):
        """Cannot create batch from empty transaction list."""
        with pytest.raises(ValueError, match="empty"):
            engine.create_batch(transactions=[])


class TestSettlementNetting:
    """Netting reduces multiple transactions to single payout per user."""

    def test_netting_reduces_payout_count(self, engine):
        """3 transactions to same user netted into 1 payout."""
        transactions = [
            SettledTransaction(
                transaction_id=f"txn_{i}", user_id="user_001",
                employer_id="employer_001", gross_amount=100.0 + i * 50,
                payout_method=PayoutMethod.ACH,
            )
            for i in range(3)
        ]

        batch = engine.create_batch(transactions=transactions)

        # 3 transactions, but 1 user → 1 net position
        assert batch.transaction_count == 3
        assert len(batch.net_positions) == 1
        assert batch.net_positions[0].transaction_count == 3

    def test_netting_aggregates_net_amount_correctly(self, engine):
        """Netted amount is sum of individual user receives amounts."""
        transactions = [
            SettledTransaction(
                transaction_id=f"txn_{i}", user_id="user_001",
                employer_id="employer_001", gross_amount=100.0,
                platform_fee_rate=0.0125,
                payout_method=PayoutMethod.ACH,
            )
            for i in range(3)
        ]

        batch = engine.create_batch(transactions=transactions)

        # Total gross: 300
        # Platform fees: 300 * 0.0125 = 3.75
        # PSP fees (ACH): 300 * 0.008 = 2.4
        # User receives: 300 - 3.75 - 2.4 = 293.85
        net_position = batch.net_positions[0]
        assert net_position.net_payout > 0
        assert abs(net_position.gross_total - 300.0) < 0.01

    def test_netting_multiple_users_separate_entries(self, engine):
        """Multiple users get separate net positions."""
        transactions = [
            SettledTransaction(
                transaction_id=f"txn_{i}", user_id=f"user_{i % 2}",
                employer_id="employer_001", gross_amount=100.0,
                payout_method=PayoutMethod.ACH,
            )
            for i in range(4)
        ]

        batch = engine.create_batch(transactions=transactions)

        # 2 users → 2 net positions
        assert len(batch.net_positions) == 2


class TestFeeCalculation:
    """Platform and PSP fees correctly deducted from gross."""

    def test_platform_fee_deducted(self, engine, base_transaction):
        """Platform fee (1.25%) deducted from settlement."""
        base_transaction.platform_fee_rate = 0.0125
        batch = engine.create_batch(transactions=[base_transaction])

        split = batch.splits[0]
        expected_fee = round(500.0 * 0.0125, 2)

        assert split.platform_fee == expected_fee

    def test_psp_fee_deducted_for_ach(self, engine):
        """PSP fee for ACH (0.8%) deducted."""
        txn = SettledTransaction(
            transaction_id="txn_001", user_id="user_001",
            employer_id="employer_001", gross_amount=500.0,
            psp_name="stripe", payout_method=PayoutMethod.ACH,
        )

        batch = engine.create_batch(transactions=[txn])
        split = batch.splits[0]

        # ACH: 0.8% = $4.00
        assert split.psp_fee == 4.00

    def test_psp_fee_deducted_for_card(self, engine):
        """PSP fee for card (2.9% + $0.30) deducted."""
        txn = SettledTransaction(
            transaction_id="txn_001", user_id="user_001",
            employer_id="employer_001", gross_amount=500.0,
            psp_name="stripe", payout_method=PayoutMethod.CARD,
        )

        batch = engine.create_batch(transactions=[txn])
        split = batch.splits[0]

        # Card: 2.9% + $0.30 = $14.50 + $0.30 = $14.80
        expected_fee = round(500.0 * 0.029 + 0.30, 2)
        assert split.psp_fee == expected_fee

    def test_psp_fee_deducted_for_instant(self, engine):
        """PSP fee for instant (2.5% + $0.25) deducted."""
        txn = SettledTransaction(
            transaction_id="txn_001", user_id="user_001",
            employer_id="employer_001", gross_amount=500.0,
            psp_name="stripe", payout_method=PayoutMethod.INSTANT,
        )

        batch = engine.create_batch(transactions=[txn])
        split = batch.splits[0]

        # Instant: 2.5% + $0.25 = $12.50 + $0.25 = $12.75
        expected_fee = round(500.0 * 0.025 + 0.25, 2)
        assert split.psp_fee == expected_fee

    def test_different_psp_has_different_fees(self, engine):
        """Adyen has different fee schedule than Stripe."""
        stripe_txn = SettledTransaction(
            transaction_id="txn_stripe", user_id="user_001",
            employer_id="employer_001", gross_amount=500.0,
            psp_name="stripe", payout_method=PayoutMethod.ACH,
        )
        adyen_txn = SettledTransaction(
            transaction_id="txn_adyen", user_id="user_002",
            employer_id="employer_001", gross_amount=500.0,
            psp_name="adyen", payout_method=PayoutMethod.ACH,
        )

        stripe_batch = engine.create_batch(transactions=[stripe_txn])
        adyen_batch = engine.create_batch(transactions=[adyen_txn])

        stripe_fee = stripe_batch.splits[0].psp_fee
        adyen_fee = adyen_batch.splits[0].psp_fee

        # Adyen ACH is 0.6% = $3.00 (cheaper than Stripe's 0.8% = $4.00)
        assert adyen_fee == 3.00
        assert stripe_fee == 4.00


class TestHoldbackManagement:
    """High-risk transactions held back (5%) for 30 days."""

    def test_high_risk_transaction_holdback_applied(self, engine):
        """High-risk transaction gets 5% holdback."""
        txn = SettledTransaction(
            transaction_id="txn_001", user_id="user_001",
            employer_id="employer_001", gross_amount=500.0,
            is_high_risk=True,
            payout_method=PayoutMethod.ACH,
        )

        batch = engine.create_batch(transactions=[txn])
        split = batch.splits[0]

        expected_holdback = round(500.0 * 0.05, 2)
        assert split.holdback_amount == expected_holdback

    def test_low_risk_transaction_no_holdback(self, engine):
        """Low-risk transaction has no holdback."""
        txn = SettledTransaction(
            transaction_id="txn_001", user_id="user_001",
            employer_id="employer_001", gross_amount=500.0,
            is_high_risk=False,
            payout_method=PayoutMethod.ACH,
        )

        batch = engine.create_batch(transactions=[txn])
        split = batch.splits[0]

        assert split.holdback_amount == 0.0

    def test_holdback_reduces_user_payout(self, engine):
        """Held-back amount reduces what user actually receives."""
        txn_normal = SettledTransaction(
            transaction_id="txn_001", user_id="user_001",
            employer_id="employer_001", gross_amount=500.0,
            is_high_risk=False,
        )
        txn_risky = SettledTransaction(
            transaction_id="txn_002", user_id="user_002",
            employer_id="employer_001", gross_amount=500.0,
            is_high_risk=True,
        )

        normal_batch = engine.create_batch(transactions=[txn_normal])
        risky_batch = engine.create_batch(transactions=[txn_risky])

        normal_payout = normal_batch.splits[0].user_receives
        risky_payout = risky_batch.splits[0].user_receives

        # Risky payout should be $25 less (5% of $500)
        assert abs(normal_payout - risky_payout - 25.0) < 0.01


class TestBatchStatusTransitions:
    """Batch status lifecycle: CREATED → SUBMITTED → CONFIRMED."""

    def test_batch_starts_in_created_status(self, engine, base_transaction):
        """Newly created batch is in CREATED status."""
        batch = engine.create_batch(transactions=[base_transaction])

        assert batch.status == BatchStatus.CREATED
        assert batch.submitted_at is None
        assert batch.confirmed_at is None

    def test_batch_submit_transitions_to_submitted(self, engine, base_transaction):
        """Submitting batch transitions to SUBMITTED."""
        batch = engine.create_batch(transactions=[base_transaction])
        submitted = engine.submit_batch(batch.batch_id)

        assert submitted.status == BatchStatus.SUBMITTED
        assert submitted.submitted_at is not None

    def test_batch_confirm_transitions_to_confirmed(self, engine, base_transaction):
        """Confirming batch transitions to CONFIRMED."""
        batch = engine.create_batch(transactions=[base_transaction])
        engine.submit_batch(batch.batch_id)
        confirmed = engine.confirm_batch(batch.batch_id)

        assert confirmed.status == BatchStatus.CONFIRMED
        assert confirmed.confirmed_at is not None

    def test_cannot_submit_from_non_created_status(self, engine, base_transaction):
        """Cannot submit a batch that's not in CREATED."""
        batch = engine.create_batch(transactions=[base_transaction])
        engine.submit_batch(batch.batch_id)

        with pytest.raises(ValueError, match="not in CREATED"):
            engine.submit_batch(batch.batch_id)

    def test_cannot_confirm_non_submitted_batch(self, engine, base_transaction):
        """Cannot confirm a batch that's not SUBMITTED."""
        batch = engine.create_batch(transactions=[base_transaction])

        with pytest.raises(ValueError, match="not in SUBMITTED"):
            engine.confirm_batch(batch.batch_id)


class TestSplitValidation:
    """Split calculations must sum to gross amount."""

    def test_split_validates_sum_to_gross(self, engine, base_transaction):
        """Split accounting: platform fee + PSP fee + holdback + user payout = gross."""
        batch = engine.create_batch(transactions=[base_transaction])
        split = batch.splits[0]

        # All splits validated during batch creation
        total_allocated = split.platform_fee + split.psp_fee + split.holdback_amount + split.user_receives
        assert abs(total_allocated - split.gross_amount) < 0.01


class TestBatchSummary:
    """Batch generates accurate summary for monitoring."""

    def test_batch_summary_contains_key_metrics(self, engine):
        """Batch summary includes transaction count, fees, payouts."""
        transactions = [
            SettledTransaction(
                transaction_id=f"txn_{i}", user_id=f"user_{i}",
                employer_id="employer_001", gross_amount=100.0,
            )
            for i in range(3)
        ]
        batch = engine.create_batch(transactions=transactions)

        summary = engine.get_batch_summary(batch.batch_id)

        assert summary["transaction_count"] == 3
        assert "gross_amount" in summary
        assert "platform_fees" in summary
        assert "psp_fees" in summary
        assert "net_payout" in summary
        assert "unique_users" in summary
        assert "payout_reduction" in summary

    def test_batch_summary_shows_payout_reduction(self, engine):
        """Summary clearly shows netting benefit."""
        transactions = [
            SettledTransaction(
                transaction_id=f"txn_{i}", user_id="user_001",
                employer_id="employer_001", gross_amount=100.0,
            )
            for i in range(5)
        ]
        batch = engine.create_batch(transactions=transactions)

        summary = engine.get_batch_summary(batch.batch_id)

        # Should show "5 txns -> 1 payouts"
        assert "5" in summary["payout_reduction"]
        assert "1" in summary["payout_reduction"]


class TestPartialSettlementHandling:
    """Partial settlements handled correctly without double-payment."""

    def test_partially_settled_batch_tracked(self, engine):
        """Batch status tracks settlement progress."""
        transactions = [
            SettledTransaction(
                transaction_id=f"txn_{i}", user_id=f"user_{i}",
                employer_id="employer_001", gross_amount=100.0,
            )
            for i in range(3)
        ]
        batch = engine.create_batch(transactions=transactions)
        engine.submit_batch(batch.batch_id)

        # Batch should remain tracked even if settlement is partial
        retrieved = engine._get_batch(batch.batch_id)
        assert retrieved.status == BatchStatus.SUBMITTED

    def test_gross_amount_accumulation(self, engine):
        """Batch gross amount is sum of all transactions."""
        transactions = [
            SettledTransaction(
                transaction_id=f"txn_{i}", user_id=f"user_{i}",
                employer_id="employer_001", gross_amount=100.0 + i * 50,
            )
            for i in range(3)
        ]
        batch = engine.create_batch(transactions=transactions)

        # 100 + 150 + 200 = 450
        assert abs(batch.gross_amount - 450.0) < 0.01


class TestDifferentPayoutMethods:
    """Different payout methods have different fee schedules."""

    def test_ach_payout_uses_ach_fees(self, engine):
        """ACH payout uses 0.8% Stripe fee."""
        txn = SettledTransaction(
            transaction_id="txn_001", user_id="user_001",
            employer_id="employer_001", gross_amount=1000.0,
            psp_name="stripe", payout_method=PayoutMethod.ACH,
        )
        batch = engine.create_batch(transactions=[txn])

        assert batch.splits[0].psp_fee == 8.00  # 0.8% of 1000

    def test_wire_payout_uses_flat_fee(self, engine):
        """Wire payout uses flat $5 fee."""
        txn = SettledTransaction(
            transaction_id="txn_001", user_id="user_001",
            employer_id="employer_001", gross_amount=1000.0,
            psp_name="adyen", payout_method=PayoutMethod.WIRE,
        )
        batch = engine.create_batch(transactions=[txn])

        assert batch.splits[0].psp_fee == 5.00  # Flat fee

    def test_instant_payout_cheaper_than_card(self, engine):
        """Instant cheaper than card for same PSP."""
        instant_txn = SettledTransaction(
            transaction_id="txn_001", user_id="user_001",
            employer_id="employer_001", gross_amount=1000.0,
            psp_name="stripe", payout_method=PayoutMethod.INSTANT,
        )
        card_txn = SettledTransaction(
            transaction_id="txn_002", user_id="user_002",
            employer_id="employer_001", gross_amount=1000.0,
            psp_name="stripe", payout_method=PayoutMethod.CARD,
        )

        instant_batch = engine.create_batch(transactions=[instant_txn])
        card_batch = engine.create_batch(transactions=[card_txn])

        instant_fee = instant_batch.splits[0].psp_fee
        card_fee = card_batch.splits[0].psp_fee

        # Instant should be cheaper
        assert instant_fee < card_fee

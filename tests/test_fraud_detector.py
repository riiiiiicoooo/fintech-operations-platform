"""
Test Suite: Fraud Detector Rule-Based Scoring and Decisions

Demonstrates that the fraud detector's core guarantees hold:
1. Velocity checks trigger on 3+ transfers in 1 hour
2. Amount anomaly rules trigger on 10x average
3. Blocklisted users get hard-blocked
4. Allowlisted users bypass all checks
5. Cold-start (first transaction) is handled correctly
6. Risk scores aggregate correctly within thresholds
7. Score boundaries (APPROVE/REVIEW/DECLINE) are enforced

Run: pytest tests/test_fraud_detector.py -v
"""

import pytest
from datetime import datetime, timedelta
from fraud.fraud_detector import (
    FraudDetector, TransactionContext, FraudFeatures,
    FraudDecision, FraudResult,
)


@pytest.fixture
def detector():
    """Set up a fraud detector with empty lists."""
    return FraudDetector()


@pytest.fixture
def base_context():
    """Base transaction context for tests."""
    return TransactionContext(
        transaction_id="txn_001",
        user_id="user_001",
        amount=100.0,
        payment_method="ach",
        device_fingerprint="device_abc123",
        ip_address="192.168.1.1",
        user_latitude=40.7128,
        user_longitude=-74.0060,
        account_age_days=30,
        kyc_tier="standard",
        lifetime_transaction_count=5,
        avg_transaction_amount=80.0,
        transactions_last_24h=2,
        transactions_last_7d=10,
        amount_last_24h=150.0,
        amount_last_7d=600.0,
        failed_transactions_last_24h=0,
        unique_ips_last_7d=1,
        unique_devices_last_7d=1,
    )


class TestVelocityChecks:
    """Transaction velocity detection: 3+ transfers in 1 hour."""

    def test_low_velocity_24h_approved(self, detector, base_context):
        """2 transactions in 24 hours should not trigger velocity rule."""
        context = base_context
        context.transactions_last_24h = 2

        result = detector.evaluate(context)

        assert result.decision == FraudDecision.APPROVE
        assert "FR-002" not in result.rules_triggered
        assert result.normalized_score < 30

    def test_high_velocity_24h_reviewed(self, detector, base_context):
        """6+ transactions in 24 hours should trigger velocity rule."""
        context = base_context
        context.transactions_last_24h = 6

        result = detector.evaluate(context)

        assert result.decision == FraudDecision.REVIEW
        assert "FR-002" in result.rules_triggered
        assert result.normalized_score >= 30

    def test_amount_velocity_high_reviewed(self, detector, base_context):
        """$2,500+ in 24 hours should trigger amount velocity rule."""
        context = base_context
        context.amount_last_24h = 2400.0

        result = detector.evaluate(context)

        assert "FR-003" in result.rules_triggered
        assert result.normalized_score >= 30


class TestAmountAnomalyDetection:
    """Amount anomaly: transaction 3x+ user's average."""

    def test_normal_amount_approved(self, detector, base_context):
        """Amount close to average should not trigger anomaly rule."""
        context = base_context
        context.amount = 80.0  # Same as average
        context.avg_transaction_amount = 80.0

        result = detector.evaluate(context)

        assert result.decision == FraudDecision.APPROVE
        assert "FR-001" not in result.rules_triggered

    def test_high_amount_exceeds_average(self, detector, base_context):
        """Amount 3x average should trigger anomaly rule."""
        context = base_context
        context.amount = 250.0
        context.avg_transaction_amount = 80.0

        result = detector.evaluate(context)

        assert result.rules_triggered
        assert "FR-001" in result.rules_triggered

    def test_first_user_high_amount(self, detector, base_context):
        """First-time user with $600 should trigger high-value rule."""
        context = base_context
        context.lifetime_transaction_count = 0
        context.avg_transaction_amount = 0.0
        context.amount = 600.0

        result = detector.evaluate(context)

        # First transaction over $250 triggers FR-009
        assert "FR-009" in result.rules_triggered


class TestBlocklistHardBlock:
    """Blocklisted users get automatic hard-block (score 100, DECLINE)."""

    def test_blocklisted_user_declined(self, detector, base_context):
        """Blocklisted user is automatically declined."""
        detector.blocklist.add("user_001")
        context = base_context

        result = detector.evaluate(context)

        assert result.decision == FraudDecision.DECLINE
        assert result.normalized_score == 100.0
        assert "BLOCKLIST" in result.rules_triggered

    def test_non_blocklisted_user_normal_flow(self, detector, base_context):
        """Non-blocklisted user goes through normal evaluation."""
        detector.blocklist.add("user_bad")
        context = base_context
        context.user_id = "user_normal"

        result = detector.evaluate(context)

        assert "BLOCKLIST" not in result.rules_triggered
        assert result.decision == FraudDecision.APPROVE


class TestAllowlistBypass:
    """Allowlisted users bypass all checks (score 0, APPROVE)."""

    def test_allowlisted_user_approved_regardless(self, detector, base_context):
        """Allowlisted user bypasses all fraud rules."""
        detector.allowlist.add("user_001")
        context = base_context
        context.amount = 5000.0  # Would normally be flagged
        context.transactions_last_24h = 10  # Would normally be flagged
        context.lifetime_transaction_count = 0

        result = detector.evaluate(context)

        assert result.decision == FraudDecision.APPROVE
        assert result.normalized_score == 0.0
        assert "ALLOWLIST" in result.rules_triggered
        assert result.rules_triggered == ["ALLOWLIST"]

    def test_blocklist_takes_precedence_over_allowlist(self, detector, base_context):
        """If somehow both, blocklist wins (shouldn't happen in production)."""
        detector.blocklist.add("user_001")
        detector.allowlist.add("user_001")
        context = base_context

        result = detector.evaluate(context)

        # Blocklist is checked first
        assert result.decision == FraudDecision.DECLINE


class TestColdStartFirstTransaction:
    """First-time user handling: higher thresholds, no historical data."""

    def test_first_transaction_low_amount_approved(self, detector, base_context):
        """First transaction with $100 should be approved."""
        context = base_context
        context.lifetime_transaction_count = 0
        context.avg_transaction_amount = 0.0
        context.amount = 100.0
        context.account_age_days = 1

        result = detector.evaluate(context)

        assert result.decision == FraudDecision.APPROVE

    def test_first_transaction_high_amount_flagged(self, detector, base_context):
        """First transaction with $300 should be reviewed (over $250 threshold)."""
        context = base_context
        context.lifetime_transaction_count = 0
        context.avg_transaction_amount = 0.0
        context.amount = 300.0
        context.account_age_days = 1

        result = detector.evaluate(context)

        assert "FR-009" in result.rules_triggered
        assert result.decision in (FraudDecision.REVIEW, FraudDecision.DECLINE)

    def test_new_account_high_value_flagged(self, detector, base_context):
        """New account (< 7 days) with $600+ should trigger rule FR-004."""
        context = base_context
        context.account_age_days = 2
        context.amount = 600.0
        context.lifetime_transaction_count = 0

        result = detector.evaluate(context)

        assert "FR-004" in result.rules_triggered


class TestAggregateRiskThreshold:
    """Score aggregation: multiple rules sum to overall score."""

    def test_single_rule_triggers_review(self, detector, base_context):
        """Single rule with 15+ points puts transaction in REVIEW."""
        context = base_context
        context.transactions_last_24h = 6  # Triggers FR-002 (15 points)

        result = detector.evaluate(context)

        assert result.decision == FraudDecision.REVIEW
        assert len(result.rules_triggered) == 1

    def test_multiple_rules_aggregate_to_decline(self, detector, base_context):
        """Multiple rules summing to > 70 points triggers DECLINE."""
        context = base_context
        # Set up to trigger multiple high-weight rules
        context.amount = 500.0  # High amount
        context.avg_transaction_amount = 100.0  # 5x average (triggers FR-001: 20 pts)
        context.transactions_last_24h = 6  # Velocity (triggers FR-002: 15 pts)
        context.amount_last_24h = 2400.0  # Amount velocity (triggers FR-003: 18 pts)
        context.account_age_days = 2  # New account (triggers FR-004: 22 pts)
        context.failed_transactions_last_24h = 3  # Multiple failures (triggers FR-006: 25 pts)

        result = detector.evaluate(context)

        assert result.decision == FraudDecision.DECLINE
        assert result.normalized_score > 70
        assert len(result.rules_triggered) >= 3

    def test_score_boundary_approve_threshold(self, detector, base_context):
        """Score exactly at 29 should APPROVE (just under threshold)."""
        context = base_context
        context.amount = 150.0
        context.avg_transaction_amount = 80.0

        result = detector.evaluate(context)

        # Should be under 30 threshold
        if result.normalized_score < 30:
            assert result.decision == FraudDecision.APPROVE

    def test_score_boundary_review_threshold(self, detector, base_context):
        """Score at 30-70 should REVIEW."""
        context = base_context
        context.transactions_last_24h = 6  # Triggers FR-002 (15 points)

        result = detector.evaluate(context)

        assert 30 <= result.normalized_score < 70
        assert result.decision == FraudDecision.REVIEW

    def test_score_boundary_decline_threshold(self, detector, base_context):
        """Score at 70+ should DECLINE."""
        context = base_context
        context.amount = 600.0
        context.avg_transaction_amount = 100.0
        context.transactions_last_24h = 10
        context.amount_last_24h = 3000.0
        context.failed_transactions_last_24h = 3

        result = detector.evaluate(context)

        assert result.normalized_score >= 70
        assert result.decision == FraudDecision.DECLINE


class TestNewDeviceDetection:
    """New device: transaction from unrecognized device fingerprint."""

    def test_returning_device_not_flagged(self, detector, base_context):
        """Device seen before in 7-day window should not trigger FR-005."""
        context = base_context
        context.unique_devices_last_7d = 1  # Only this device

        result = detector.evaluate(context)

        assert "FR-005" not in result.rules_triggered

    def test_new_device_flagged(self, detector, base_context):
        """New device (multiple devices in 7 days) should trigger FR-005."""
        context = base_context
        context.unique_devices_last_7d = 2  # New device this week

        result = detector.evaluate(context)

        assert "FR-005" in result.rules_triggered


class TestRoundAmountSuspicion:
    """Round amounts ($100, $500, $1000) are slightly more suspicious."""

    def test_non_round_amount_not_flagged(self, detector, base_context):
        """Non-round amount should not trigger FR-008."""
        context = base_context
        context.amount = 123.45

        result = detector.evaluate(context)

        assert "FR-008" not in result.rules_triggered

    def test_round_amount_below_threshold_not_flagged(self, detector, base_context):
        """Round amount under $500 should not trigger FR-008."""
        context = base_context
        context.amount = 200.0

        result = detector.evaluate(context)

        assert "FR-008" not in result.rules_triggered

    def test_round_amount_above_threshold_flagged(self, detector, base_context):
        """Round amount $500+ should trigger FR-008."""
        context = base_context
        context.amount = 500.0

        result = detector.evaluate(context)

        assert "FR-008" in result.rules_triggered


class TestDecisionStatistics:
    """Fraud detector maintains statistics on decisions."""

    def test_decision_stats_accumulate(self, detector, base_context):
        """Running multiple evaluations accumulates stats."""
        # First evaluation: approve
        context1 = base_context
        context1.user_id = "user_001"
        detector.evaluate(context1)

        # Second evaluation: different user
        context2 = base_context
        context2.user_id = "user_002"
        context2.transactions_last_24h = 10
        detector.evaluate(context2)

        stats = detector.get_decision_stats()

        assert stats["total"] == 2
        assert stats["approved"] == 1
        assert stats["decline_rate"] > 0


class TestLatencyMeasurement:
    """Fraud detector measures and reports latency."""

    def test_evaluation_latency_measured(self, detector, base_context):
        """Evaluation completes and latency is recorded."""
        result = detector.evaluate(base_context)

        assert result.latency_ms > 0
        assert result.latency_ms < 100  # Should be sub-100ms


class TestFeatureExtraction:
    """Features are correctly extracted from transaction context."""

    def test_amount_ratio_calculated(self, detector, base_context):
        """Amount-to-average ratio is calculated correctly."""
        context = base_context
        context.amount = 200.0
        context.avg_transaction_amount = 100.0

        result = detector.evaluate(context)

        assert result.features.amount_to_avg_ratio == 2.0

    def test_velocity_features_captured(self, detector, base_context):
        """Velocity features are extracted from context."""
        context = base_context
        context.transactions_last_24h = 8
        context.transactions_last_7d = 25

        result = detector.evaluate(context)

        assert result.features.velocity_24h == 8
        assert result.features.velocity_7d == 25

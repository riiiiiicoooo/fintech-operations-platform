"""
Fraud Detector: Rule-Based Fraud Scoring and Decision Engine

PM-authored reference implementation demonstrating:
- Feature extraction from transaction context
- Configurable rule engine with weighted scoring
- Blocklist/allowlist fast-path checks
- Approve/Review/Decline decision thresholds
- Immutable decision logging (becomes ML training data)

Not production code. See docs/ARCHITECTURE.md Section 2 (Fraud Service).
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional
from uuid import uuid4


# --- Fraud Models ---

class FraudDecision(Enum):
    APPROVE = "approve"     # Score < 30
    REVIEW = "review"       # Score 30-70 (human analyst reviews)
    DECLINE = "decline"     # Score > 70


@dataclass
class TransactionContext:
    """All signals available at transaction time for fraud evaluation."""
    transaction_id: str
    user_id: str
    amount: float
    payment_method: str          # "ach", "card", "instant"
    device_fingerprint: str
    ip_address: str
    user_latitude: Optional[float] = None
    user_longitude: Optional[float] = None

    # User profile (pre-fetched)
    account_age_days: int = 0
    kyc_tier: str = "basic"      # "basic", "standard", "enhanced"
    lifetime_transaction_count: int = 0
    avg_transaction_amount: float = 0.0

    # Recent activity (pre-aggregated)
    transactions_last_24h: int = 0
    transactions_last_7d: int = 0
    amount_last_24h: float = 0.0
    amount_last_7d: float = 0.0
    failed_transactions_last_24h: int = 0
    unique_ips_last_7d: int = 1
    unique_devices_last_7d: int = 1


@dataclass
class FraudFeatures:
    """Extracted features used for rule evaluation. Logged for ML training."""
    amount_to_avg_ratio: float = 0.0       # How unusual is this amount vs. user's average
    velocity_24h: int = 0                  # Transaction count in last 24 hours
    velocity_7d: int = 0                   # Transaction count in last 7 days
    amount_velocity_24h: float = 0.0       # Total amount in last 24 hours
    account_age_days: int = 0
    is_new_device: bool = False
    is_new_ip: bool = False
    failed_attempt_count: int = 0
    amount_near_tier_limit: bool = False   # Within 10% of KYC tier limit
    is_round_amount: bool = False          # Exact round number ($100, $500, $1000)
    first_transaction: bool = False


@dataclass
class FraudResult:
    """Immutable record of a fraud decision. Logged for audit trail and ML training."""
    decision_id: str
    transaction_id: str
    user_id: str
    features: FraudFeatures
    rules_triggered: list[str]
    raw_score: float
    normalized_score: float    # 0-100
    decision: FraudDecision
    latency_ms: float
    model_version: str = "rules_v1"
    created_at: datetime = field(default_factory=datetime.utcnow)


# --- Fraud Rules ---

@dataclass
class FraudRule:
    """A single fraud detection rule with a name, weight, and evaluation function."""
    rule_id: str
    name: str
    weight: float            # Contribution to overall score when triggered
    description: str

    def evaluate(self, context: TransactionContext, features: FraudFeatures) -> bool:
        """Override in subclasses. Returns True if rule is triggered."""
        raise NotImplementedError


class HighAmountRule(FraudRule):
    """Transaction amount is 3x+ the user's average."""
    def evaluate(self, context, features):
        if context.avg_transaction_amount == 0:
            return context.amount > 500  # Default threshold for new users
        return features.amount_to_avg_ratio > 3.0


class VelocityRule(FraudRule):
    """More than 5 transactions in 24 hours."""
    def evaluate(self, context, features):
        return features.velocity_24h > 5


class AmountVelocityRule(FraudRule):
    """Total amount in 24 hours exceeds $2,500."""
    def evaluate(self, context, features):
        return features.amount_velocity_24h > 2500


class NewAccountHighValueRule(FraudRule):
    """Account less than 7 days old with transaction over $500."""
    def evaluate(self, context, features):
        return features.account_age_days < 7 and context.amount > 500


class NewDeviceRule(FraudRule):
    """Transaction from a device not seen in the last 7 days."""
    def evaluate(self, context, features):
        return features.is_new_device


class MultipleFailedAttemptsRule(FraudRule):
    """3+ failed transactions in last 24 hours followed by a successful attempt."""
    def evaluate(self, context, features):
        return features.failed_attempt_count >= 3


class NearTierLimitRule(FraudRule):
    """Transaction amount within 10% of KYC tier limit (probing behavior)."""
    def evaluate(self, context, features):
        return features.amount_near_tier_limit


class RoundAmountRule(FraudRule):
    """Exact round amounts ($100, $500, $1000) are slightly more suspicious."""
    def evaluate(self, context, features):
        return features.is_round_amount and context.amount >= 500


class FirstTransactionHighValueRule(FraudRule):
    """User's very first transaction is over $250."""
    def evaluate(self, context, features):
        return features.first_transaction and context.amount > 250


# --- Fraud Detector ---

class FraudDetector:
    """
    Evaluates transactions against configurable rules.
    
    Designed to run synchronously in < 100ms:
    - Feature extraction: < 10ms
    - Blocklist/allowlist: < 5ms
    - Rule evaluation: < 50ms
    - Decision + logging: < 5ms
    
    Score interpretation:
    - 0-29: APPROVE (low risk, process normally)
    - 30-70: REVIEW (medium risk, queue for analyst)
    - 71-100: DECLINE (high risk, block transaction)
    """

    APPROVE_THRESHOLD = 30
    DECLINE_THRESHOLD = 70

    def __init__(self):
        self.rules = self._initialize_rules()
        self.blocklist: set[str] = set()    # Blocked user_ids
        self.allowlist: set[str] = set()    # Trusted user_ids (skip scoring)
        self.decisions: list[FraudResult] = []

    def _initialize_rules(self) -> list[FraudRule]:
        """Default rule set with weights. Weights sum to ~100 for normalization."""
        return [
            HighAmountRule("FR-001", "High amount vs. average", 20.0,
                           "Transaction 3x+ user average"),
            VelocityRule("FR-002", "Transaction velocity", 15.0,
                         "5+ transactions in 24 hours"),
            AmountVelocityRule("FR-003", "Amount velocity", 18.0,
                               "$2,500+ in 24 hours"),
            NewAccountHighValueRule("FR-004", "New account high value", 22.0,
                                    "Account < 7 days, transaction > $500"),
            NewDeviceRule("FR-005", "New device", 10.0,
                          "Unrecognized device fingerprint"),
            MultipleFailedAttemptsRule("FR-006", "Failed attempts", 25.0,
                                       "3+ failures then success"),
            NearTierLimitRule("FR-007", "Near tier limit", 12.0,
                              "Amount within 10% of KYC limit"),
            RoundAmountRule("FR-008", "Round amount", 5.0,
                            "Exact round amount >= $500"),
            FirstTransactionHighValueRule("FR-009", "First txn high value", 15.0,
                                          "First transaction > $250"),
        ]

    def evaluate(self, context: TransactionContext) -> FraudResult:
        """
        Main entry point. Returns a fraud decision for the given transaction.
        """
        start = datetime.utcnow()

        # Fast path: blocklist check
        if context.user_id in self.blocklist:
            return self._create_result(
                context, FraudFeatures(), ["BLOCKLIST"], 100.0, 100.0,
                FraudDecision.DECLINE, start
            )

        # Fast path: allowlist check
        if context.user_id in self.allowlist:
            return self._create_result(
                context, FraudFeatures(), ["ALLOWLIST"], 0.0, 0.0,
                FraudDecision.APPROVE, start
            )

        # Extract features
        features = self._extract_features(context)

        # Evaluate rules
        triggered_rules = []
        raw_score = 0.0

        for rule in self.rules:
            if rule.evaluate(context, features):
                triggered_rules.append(rule.rule_id)
                raw_score += rule.weight

        # Normalize to 0-100
        max_possible = sum(r.weight for r in self.rules)
        normalized_score = min(100.0, (raw_score / max_possible) * 100)

        # Decision
        if normalized_score >= self.DECLINE_THRESHOLD:
            decision = FraudDecision.DECLINE
        elif normalized_score >= self.APPROVE_THRESHOLD:
            decision = FraudDecision.REVIEW
        else:
            decision = FraudDecision.APPROVE

        return self._create_result(
            context, features, triggered_rules, raw_score, normalized_score,
            decision, start
        )

    def _extract_features(self, ctx: TransactionContext) -> FraudFeatures:
        """Extract computed features from raw transaction context."""
        # KYC tier limits
        tier_limits = {"basic": 250, "standard": 1000, "enhanced": 5000}
        tier_limit = tier_limits.get(ctx.kyc_tier, 250)

        return FraudFeatures(
            amount_to_avg_ratio=(
                ctx.amount / ctx.avg_transaction_amount
                if ctx.avg_transaction_amount > 0 else 0.0
            ),
            velocity_24h=ctx.transactions_last_24h,
            velocity_7d=ctx.transactions_last_7d,
            amount_velocity_24h=ctx.amount_last_24h + ctx.amount,
            account_age_days=ctx.account_age_days,
            is_new_device=ctx.unique_devices_last_7d > 1,
            is_new_ip=ctx.unique_ips_last_7d > 1,
            failed_attempt_count=ctx.failed_transactions_last_24h,
            amount_near_tier_limit=ctx.amount > tier_limit * 0.9,
            is_round_amount=ctx.amount == int(ctx.amount) and ctx.amount % 100 == 0,
            first_transaction=ctx.lifetime_transaction_count == 0,
        )

    def _create_result(
        self, context, features, triggered, raw, normalized, decision, start
    ) -> FraudResult:
        elapsed = (datetime.utcnow() - start).total_seconds() * 1000
        result = FraudResult(
            decision_id=str(uuid4()),
            transaction_id=context.transaction_id,
            user_id=context.user_id,
            features=features,
            rules_triggered=triggered,
            raw_score=round(raw, 2),
            normalized_score=round(normalized, 2),
            decision=decision,
            latency_ms=round(elapsed, 2),
        )
        self.decisions.append(result)
        return result

    def get_decision_stats(self) -> dict:
        """Dashboard data for fraud monitoring."""
        if not self.decisions:
            return {"total": 0}

        approvals = sum(1 for d in self.decisions if d.decision == FraudDecision.APPROVE)
        reviews = sum(1 for d in self.decisions if d.decision == FraudDecision.REVIEW)
        declines = sum(1 for d in self.decisions if d.decision == FraudDecision.DECLINE)
        avg_latency = sum(d.latency_ms for d in self.decisions) / len(self.decisions)

        return {
            "total": len(self.decisions),
            "approved": approvals,
            "reviewed": reviews,
            "declined": declines,
            "approve_rate": round(approvals / len(self.decisions), 3),
            "review_rate": round(reviews / len(self.decisions), 3),
            "decline_rate": round(declines / len(self.decisions), 3),
            "avg_latency_ms": round(avg_latency, 2),
        }

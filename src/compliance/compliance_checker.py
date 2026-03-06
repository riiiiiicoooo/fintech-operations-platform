"""
Compliance Checker: KYC Orchestration, Transaction Monitoring, and SAR Triggers

PM-authored reference implementation demonstrating:
- Progressive KYC tier enforcement with transaction limits
- Transaction monitoring rules (aggregation, structuring, rapid movement)
- OFAC sanctions screening with fuzzy name matching
- SAR trigger identification and alert generation
- Compliance event logging for audit trail

Not production code. See docs/COMPLIANCE_FRAMEWORK.md for full regulatory context.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional
from uuid import uuid4
import re


# --- KYC Models ---

class KYCTier(Enum):
    NONE = "none"
    BASIC = "basic"         # Email + phone + employer match
    STANDARD = "standard"   # Government ID + database check
    ENHANCED = "enhanced"   # Document proof + manual review


class KYCStatus(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"     # Re-verification needed (12 months)


@dataclass
class KYCTierConfig:
    tier: KYCTier
    max_per_transaction: Decimal
    max_per_day: Decimal
    max_per_month: Decimal
    verification_method: str
    expected_duration: str


KYC_TIER_CONFIGS = {
    KYCTier.BASIC: KYCTierConfig(
        KYCTier.BASIC, Decimal("250.00"), Decimal("500.00"), Decimal("2000.00"),
        "email + phone + employer match", "< 30 seconds"
    ),
    KYCTier.STANDARD: KYCTierConfig(
        KYCTier.STANDARD, Decimal("1000.00"), Decimal("2500.00"), Decimal("10000.00"),
        "government ID + database check (Alloy)", "< 3 minutes"
    ),
    KYCTier.ENHANCED: KYCTierConfig(
        KYCTier.ENHANCED, Decimal("5000.00"), Decimal("10000.00"), Decimal("25000.00"),
        "document proof + manual review", "< 24 hours"
    ),
}


@dataclass
class UserKYC:
    user_id: str
    current_tier: KYCTier = KYCTier.NONE
    status: KYCStatus = KYCStatus.PENDING
    verified_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None


# --- Transaction Monitoring Models ---

class AlertPriority(Enum):
    CRITICAL = "critical"  # SLA: 4 hours (OFAC, structuring)
    HIGH = "high"          # SLA: 24 hours (aggregation, rapid movement)
    MEDIUM = "medium"      # SLA: 48 hours (behavioral, geographic)
    LOW = "low"            # SLA: 5 business days (minor velocity)


class AlertStatus(Enum):
    CREATED = "created"
    ASSIGNED = "assigned"
    INVESTIGATING = "investigating"
    DISMISSED = "dismissed"
    SAR_RECOMMENDED = "sar_recommended"
    SAR_FILED = "sar_filed"


@dataclass
class MonitoringAlert:
    alert_id: str
    user_id: str
    rule_name: str
    priority: AlertPriority
    status: AlertStatus = AlertStatus.CREATED
    description: str = ""
    evidence: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    assigned_to: Optional[str] = None
    resolved_at: Optional[datetime] = None


@dataclass
class TransactionHistory:
    """Pre-aggregated user transaction history for monitoring rules."""
    user_id: str
    amount: Decimal               # Current transaction
    outbound_30d: Decimal = Decimal("0.00")   # Total outbound last 30 days
    inbound_30d: Decimal = Decimal("0.00")    # Total inbound last 30 days
    transactions_7d: list[dict] = field(default_factory=list)  # Recent transactions
    avg_monthly_volume: Decimal = Decimal("0.00")
    ip_address: str = ""
    registered_state: str = ""
    ip_country: str = "US"


# --- OFAC Models ---

@dataclass
class OFACEntry:
    """Simplified SDN list entry."""
    sdn_id: str
    name: str
    aliases: list[str] = field(default_factory=list)
    sdn_type: str = "individual"  # individual, entity, vessel


@dataclass
class OFACScreeningResult:
    screened: bool = True
    match_found: bool = False
    match_score: float = 0.0
    matched_entry: Optional[OFACEntry] = None
    blocked: bool = False
    latency_ms: float = 0.0


# --- Compliance Event Log ---

class ComplianceEventType(Enum):
    KYC_SUBMITTED = "kyc_submitted"
    KYC_APPROVED = "kyc_approved"
    KYC_REJECTED = "kyc_rejected"
    MONITORING_ALERT = "monitoring_alert"
    ALERT_ASSIGNED = "alert_assigned"
    ALERT_RESOLVED = "alert_resolved"
    SAR_RECOMMENDED = "sar_recommended"
    SAR_FILED = "sar_filed"
    OFAC_SCREENED = "ofac_screened"
    OFAC_MATCH = "ofac_match"
    ACCOUNT_FROZEN = "account_frozen"
    ACCOUNT_UNFROZEN = "account_unfrozen"


@dataclass
class ComplianceEvent:
    event_id: str
    event_type: ComplianceEventType
    user_id: str
    details: dict
    created_at: datetime = field(default_factory=datetime.utcnow)
    # In production: SHA-256 hash chain for tamper resistance
    previous_hash: str = ""
    event_hash: str = ""


# --- Compliance Checker ---

class ComplianceChecker:
    """
    Orchestrates KYC verification, transaction monitoring, and sanctions screening.
    
    Called at two points:
    1. KYC check: when user attempts to transact (synchronous, enforces tier limits)
    2. Transaction monitoring: after transaction posts (async, evaluates patterns)
    """

    def __init__(self):
        self.user_kyc: dict[str, UserKYC] = {}
        self.alerts: list[MonitoringAlert] = []
        self.events: list[ComplianceEvent] = []
        self.ofac_list: list[OFACEntry] = []
        self.frozen_accounts: set[str] = set()

    # --- KYC Tier Enforcement ---

    def check_transaction_limits(
        self,
        user_id: str,
        amount: Decimal,
        daily_total: Decimal = Decimal("0.00"),
        monthly_total: Decimal = Decimal("0.00"),
    ) -> dict:
        """
        Pre-transaction check. Verifies user's KYC tier allows this transaction.
        Returns approval or denial with specific limit exceeded.
        """
        # Account frozen check
        if user_id in self.frozen_accounts:
            return {
                "allowed": False,
                "reason": "account_frozen",
                "message": "Your account is currently under review.",
            }

        kyc = self.user_kyc.get(user_id)
        if not kyc or kyc.current_tier == KYCTier.NONE:
            return {
                "allowed": False,
                "reason": "kyc_required",
                "message": "Identity verification is required before transacting.",
                "upgrade_to": KYCTier.BASIC.value,
            }

        if kyc.status != KYCStatus.APPROVED:
            return {
                "allowed": False,
                "reason": "kyc_not_approved",
                "message": "Your identity verification is pending.",
            }

        # Check if expired (12-month re-verification)
        if kyc.expires_at and datetime.utcnow() > kyc.expires_at:
            kyc.status = KYCStatus.EXPIRED
            return {
                "allowed": False,
                "reason": "kyc_expired",
                "message": "Your identity verification has expired. Please re-verify.",
            }

        config = KYC_TIER_CONFIGS[kyc.current_tier]

        # Per-transaction limit
        if amount > config.max_per_transaction:
            return {
                "allowed": False,
                "reason": "transaction_limit_exceeded",
                "limit": config.max_per_transaction,
                "requested": amount,
                "upgrade_to": self._next_tier(kyc.current_tier).value,
            }

        # Daily limit
        if daily_total + amount > config.max_per_day:
            return {
                "allowed": False,
                "reason": "daily_limit_exceeded",
                "limit": config.max_per_day,
                "current_daily": daily_total,
                "requested": amount,
            }

        # Monthly limit
        if monthly_total + amount > config.max_per_month:
            return {
                "allowed": False,
                "reason": "monthly_limit_exceeded",
                "limit": config.max_per_month,
                "current_monthly": monthly_total,
                "requested": amount,
            }

        return {"allowed": True, "tier": kyc.current_tier.value}

    def approve_kyc(self, user_id: str, tier: KYCTier) -> UserKYC:
        """Approve a user's KYC verification at a given tier."""
        kyc = self.user_kyc.get(user_id, UserKYC(user_id=user_id))
        kyc.current_tier = tier
        kyc.status = KYCStatus.APPROVED
        kyc.verified_at = datetime.utcnow()
        kyc.expires_at = datetime.utcnow() + timedelta(days=365)
        self.user_kyc[user_id] = kyc

        self._log_event(ComplianceEventType.KYC_APPROVED, user_id, {
            "tier": tier.value,
            "expires_at": kyc.expires_at.isoformat(),
        })

        return kyc

    def _next_tier(self, current: KYCTier) -> KYCTier:
        progression = {
            KYCTier.NONE: KYCTier.BASIC,
            KYCTier.BASIC: KYCTier.STANDARD,
            KYCTier.STANDARD: KYCTier.ENHANCED,
            KYCTier.ENHANCED: KYCTier.ENHANCED,
        }
        return progression[current]

    # --- Transaction Monitoring ---

    def monitor_transaction(self, history: TransactionHistory) -> list[MonitoringAlert]:
        """
        Async post-transaction monitoring. Evaluates all rules against
        the user's transaction history. Returns any triggered alerts.
        """
        triggered = []

        # Rule 1: Aggregation (BSA $10K threshold)
        if history.outbound_30d > Decimal("5000.00"):
            triggered.append(self._create_alert(
                history.user_id,
                "aggregation_threshold",
                AlertPriority.HIGH,
                f"Outbound volume ${history.outbound_30d:.2f} in 30 days exceeds $5,000 monitoring threshold",
                {"outbound_30d": history.outbound_30d, "threshold": Decimal("5000.00")},
            ))

        # Rule 2: Structuring detection
        structuring = self._detect_structuring(history)
        if structuring:
            triggered.append(self._create_alert(
                history.user_id,
                "structuring_suspected",
                AlertPriority.CRITICAL,
                f"Possible structuring: {structuring['count']} transactions between $2,000-$2,999 in 7 days",
                structuring,
            ))

        # Rule 3: Rapid movement (funds in and out within 24 hours)
        if history.inbound_30d > Decimal("0.00"):
            outbound_ratio = float(history.outbound_30d / history.inbound_30d)
            if outbound_ratio > 0.8 and history.inbound_30d > Decimal("1000.00"):
                triggered.append(self._create_alert(
                    history.user_id,
                    "rapid_movement",
                    AlertPriority.HIGH,
                    f"Rapid fund movement: {outbound_ratio:.0%} of inbound moved out within 30 days",
                    {"outbound_ratio": outbound_ratio, "inbound_30d": history.inbound_30d},
                ))

        # Rule 4: Behavioral anomaly (volume 3x above 90-day average)
        if history.avg_monthly_volume > Decimal("0.00"):
            current_ratio = float((history.outbound_30d + history.inbound_30d) / history.avg_monthly_volume)
            if current_ratio > 3.0:
                triggered.append(self._create_alert(
                    history.user_id,
                    "behavioral_anomaly",
                    AlertPriority.MEDIUM,
                    f"Transaction volume {current_ratio:.1f}x above 90-day average",
                    {"volume_ratio": current_ratio, "avg_monthly": history.avg_monthly_volume},
                ))

        # Rule 5: Geographic mismatch
        if history.ip_country != "US" and history.registered_state:
            triggered.append(self._create_alert(
                history.user_id,
                "geographic_mismatch",
                AlertPriority.MEDIUM,
                f"Transaction IP from {history.ip_country}, user registered in {history.registered_state}",
                {"ip_country": history.ip_country, "registered_state": history.registered_state},
            ))

        self.alerts.extend(triggered)
        return triggered

    def _detect_structuring(self, history: TransactionHistory) -> Optional[dict]:
        """
        Detect potential structuring: multiple transactions just below
        a reporting threshold ($3,000 in our case, conservative below BSA's $10K).
        """
        suspect_min = Decimal("2000.00")
        suspect_max = Decimal("2999.00")
        suspect_txns = [
            t for t in history.transactions_7d
            if suspect_min <= Decimal(str(t.get("amount", 0))) <= suspect_max
        ]

        if len(suspect_txns) >= 3:
            return {
                "count": len(suspect_txns),
                "total": sum((Decimal(str(t.get("amount", 0))) for t in suspect_txns), Decimal("0.00")),
                "range": f"${suspect_min:.0f}-${suspect_max:.0f}",
                "window": "7 days",
            }

        return None

    def _create_alert(
        self, user_id, rule_name, priority, description, evidence
    ) -> MonitoringAlert:
        alert = MonitoringAlert(
            alert_id=str(uuid4()),
            user_id=user_id,
            rule_name=rule_name,
            priority=priority,
            description=description,
            evidence=evidence,
        )
        self._log_event(ComplianceEventType.MONITORING_ALERT, user_id, {
            "alert_id": alert.alert_id,
            "rule": rule_name,
            "priority": priority.value,
        })
        return alert

    # --- OFAC Sanctions Screening ---

    def screen_ofac(self, full_name: str, user_id: str) -> OFACScreeningResult:
        """
        Screen a name against the OFAC SDN list.
        Runs synchronously on every outbound transfer (< 10ms target).
        Uses Jaro-Winkler similarity for fuzzy matching.
        """
        start = datetime.utcnow()
        best_match = None
        best_score = 0.0

        normalized_name = self._normalize_name(full_name)

        for entry in self.ofac_list:
            names_to_check = [entry.name] + entry.aliases
            for name in names_to_check:
                score = self._jaro_winkler(normalized_name, self._normalize_name(name))
                if score > best_score:
                    best_score = score
                    best_match = entry

        elapsed = (datetime.utcnow() - start).total_seconds() * 1000

        match_found = best_score >= 0.85
        blocked = match_found

        result = OFACScreeningResult(
            screened=True,
            match_found=match_found,
            match_score=round(best_score, 3),
            matched_entry=best_match if match_found else None,
            blocked=blocked,
            latency_ms=round(elapsed, 2),
        )

        # Log every screening (pass or fail)
        event_type = ComplianceEventType.OFAC_MATCH if match_found else ComplianceEventType.OFAC_SCREENED
        self._log_event(event_type, user_id, {
            "name_screened": full_name,
            "match_found": match_found,
            "match_score": best_score,
            "blocked": blocked,
        })

        if blocked:
            self.frozen_accounts.add(user_id)
            self._log_event(ComplianceEventType.ACCOUNT_FROZEN, user_id, {
                "reason": "ofac_match",
                "match_score": best_score,
            })

        return result

    def _normalize_name(self, name: str) -> str:
        """Normalize name for comparison: lowercase, remove punctuation, collapse spaces."""
        name = name.lower().strip()
        name = re.sub(r'[^\w\s]', '', name)
        name = re.sub(r'\s+', ' ', name)
        return name

    def _jaro_winkler(self, s1: str, s2: str, winkler_prefix_weight: float = 0.1) -> float:
        """Jaro-Winkler similarity. Returns 0.0 to 1.0."""
        if s1 == s2:
            return 1.0
        if not s1 or not s2:
            return 0.0

        max_dist = max(len(s1), len(s2)) // 2 - 1
        if max_dist < 0:
            max_dist = 0

        s1_matches = [False] * len(s1)
        s2_matches = [False] * len(s2)
        matches = 0
        transpositions = 0

        for i in range(len(s1)):
            start = max(0, i - max_dist)
            end = min(i + max_dist + 1, len(s2))
            for j in range(start, end):
                if s2_matches[j] or s1[i] != s2[j]:
                    continue
                s1_matches[i] = True
                s2_matches[j] = True
                matches += 1
                break

        if matches == 0:
            return 0.0

        k = 0
        for i in range(len(s1)):
            if not s1_matches[i]:
                continue
            while not s2_matches[k]:
                k += 1
            if s1[i] != s2[k]:
                transpositions += 1
            k += 1

        jaro = (
            matches / len(s1) +
            matches / len(s2) +
            (matches - transpositions / 2) / matches
        ) / 3

        # Winkler modification: boost for common prefix
        prefix_len = 0
        for i in range(min(4, min(len(s1), len(s2)))):
            if s1[i] == s2[i]:
                prefix_len += 1
            else:
                break

        return jaro + prefix_len * winkler_prefix_weight * (1 - jaro)

    # --- Compliance Event Logging ---

    def _log_event(self, event_type: ComplianceEventType, user_id: str, details: dict):
        """
        Append-only compliance event log. In production:
        - Stored in separate 'compliance' PostgreSQL schema
        - Application user has INSERT-only permissions
        - Hash chain for tamper detection
        - 7-year retention
        """
        event = ComplianceEvent(
            event_id=str(uuid4()),
            event_type=event_type,
            user_id=user_id,
            details=details,
        )
        self.events.append(event)

    def get_compliance_summary(self) -> dict:
        """Dashboard data for Priya (Compliance Officer)."""
        open_alerts = [a for a in self.alerts if a.status not in
                       (AlertStatus.DISMISSED, AlertStatus.SAR_FILED)]

        return {
            "total_alerts": len(self.alerts),
            "open_alerts": len(open_alerts),
            "alerts_by_priority": {
                p.value: sum(1 for a in open_alerts if a.priority == p)
                for p in AlertPriority
            },
            "frozen_accounts": len(self.frozen_accounts),
            "total_kyc_users": len(self.user_kyc),
            "kyc_tier_distribution": {
                t.value: sum(1 for k in self.user_kyc.values() if k.current_tier == t)
                for t in KYCTier
            },
            "compliance_events_logged": len(self.events),
        }

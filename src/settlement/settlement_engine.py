"""
Settlement Engine: Multi-Party Settlement, Split Payments, and Batch Processing

PM-authored reference implementation demonstrating:
- Multi-party split calculation (employer funded, platform fee, PSP fee, user receives)
- Net settlement batching (500 individual payouts reduced to net positions)
- Holdback reserves for high-risk transactions
- Settlement file generation (NACHA-style for ACH)
- Settlement batch lifecycle (CREATED -> SUBMITTED -> CONFIRMED -> RECONCILED)

Not production code. See docs/ARCHITECTURE.md Section 2 (Settlement Service).
"""

from dataclasses import dataclass, field
from datetime import datetime, date
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional
from uuid import uuid4


# --- Settlement Models ---

class BatchStatus(Enum):
    CREATED = "created"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    RECONCILED = "reconciled"
    FAILED = "failed"


class PayoutMethod(Enum):
    ACH = "ach"
    CARD = "card"
    WIRE = "wire"
    INSTANT = "instant"


@dataclass
class SettledTransaction:
    """A transaction that has completed and is ready for settlement."""
    transaction_id: str
    user_id: str
    employer_id: str
    gross_amount: Decimal
    platform_fee_rate: Decimal = Decimal("0.0125")   # 1.25% default
    psp_name: str = "stripe"
    payout_method: PayoutMethod = PayoutMethod.ACH
    settled_at: datetime = field(default_factory=datetime.utcnow)
    is_high_risk: bool = False


@dataclass
class SplitCalculation:
    """Breakdown of how a single transaction's funds are distributed."""
    transaction_id: str
    gross_amount: Decimal
    platform_fee: Decimal
    psp_fee: Decimal
    user_receives: Decimal
    holdback_amount: Decimal = Decimal("0.00")

    @property
    def total_allocated(self) -> Decimal:
        return self.platform_fee + self.psp_fee + self.user_receives + self.holdback_amount

    def validate(self):
        if self.gross_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) != self.total_allocated.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP):
            raise ValueError(
                f"Split does not sum to gross. "
                f"Gross: ${self.gross_amount:.2f}, Allocated: ${self.total_allocated:.2f}"
            )


@dataclass
class NetPosition:
    """Aggregated net position for a single user across multiple transactions."""
    user_id: str
    transaction_count: int
    gross_total: Decimal
    fees_total: Decimal
    holdback_total: Decimal
    net_payout: Decimal


@dataclass
class SettlementBatch:
    batch_id: str
    settlement_date: date
    status: BatchStatus = BatchStatus.CREATED
    transactions: list[SettledTransaction] = field(default_factory=list)
    splits: list[SplitCalculation] = field(default_factory=list)
    net_positions: list[NetPosition] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    submitted_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None

    @property
    def transaction_count(self) -> int:
        return len(self.transactions)

    @property
    def gross_amount(self) -> Decimal:
        return sum((t.gross_amount for t in self.transactions), Decimal("0.00"))

    @property
    def total_platform_fees(self) -> Decimal:
        return sum((s.platform_fee for s in self.splits), Decimal("0.00"))

    @property
    def total_psp_fees(self) -> Decimal:
        return sum((s.psp_fee for s in self.splits), Decimal("0.00"))

    @property
    def total_holdback(self) -> Decimal:
        return sum((s.holdback_amount for s in self.splits), Decimal("0.00"))

    @property
    def total_net_payout(self) -> Decimal:
        return sum((n.net_payout for n in self.net_positions), Decimal("0.00"))


# --- PSP Fee Schedule ---

PSP_FEE_SCHEDULE = {
    # (psp_name, payout_method): (percentage, flat_fee)
    ("stripe", PayoutMethod.ACH): (Decimal("0.008"), Decimal("0.00")),       # 0.8%
    ("stripe", PayoutMethod.CARD): (Decimal("0.029"), Decimal("0.30")),       # 2.9% + $0.30
    ("stripe", PayoutMethod.INSTANT): (Decimal("0.025"), Decimal("0.25")),    # 2.5% + $0.25
    ("adyen", PayoutMethod.ACH): (Decimal("0.006"), Decimal("0.00")),         # 0.6%
    ("adyen", PayoutMethod.CARD): (Decimal("0.025"), Decimal("0.25")),        # 2.5% + $0.25
    ("adyen", PayoutMethod.WIRE): (Decimal("0.000"), Decimal("5.00")),        # Flat $5
    ("tabapay", PayoutMethod.INSTANT): (Decimal("0.015"), Decimal("0.00")),   # 1.5%
}

# Holdback rate for high-risk transactions (released after 30 days)
HOLDBACK_RATE = Decimal("0.05")  # 5%


# --- Settlement Engine ---

class SettlementEngine:
    """
    Processes daily settlement batches. Runs as a Celery task at 6 PM ET.
    
    Flow:
    1. Collect all transactions settled since last batch
    2. Calculate per-transaction splits (platform fee, PSP fee, user payout)
    3. Apply holdbacks for high-risk transactions
    4. Net positions per user (reduces payout count dramatically)
    5. Generate settlement instructions (NACHA for ACH, PSP API for card/instant)
    """

    def __init__(self):
        self.batches: list[SettlementBatch] = []

    def create_batch(
        self,
        transactions: list[SettledTransaction],
        settlement_date: date = None,
    ) -> SettlementBatch:
        """Create a settlement batch from a list of settled transactions."""
        if not transactions:
            raise ValueError("Cannot create empty settlement batch")

        batch = SettlementBatch(
            batch_id=f"BATCH-{datetime.utcnow().strftime('%Y%m%d')}-{str(uuid4())[:8]}",
            settlement_date=settlement_date or date.today(),
            transactions=transactions,
        )

        # Step 1: Calculate splits for each transaction
        batch.splits = [self._calculate_split(t) for t in transactions]

        # Step 2: Validate all splits sum correctly
        for split in batch.splits:
            split.validate()

        # Step 3: Net positions per user
        batch.net_positions = self._calculate_net_positions(batch.splits, transactions)

        self.batches.append(batch)
        return batch

    def _calculate_split(self, transaction: SettledTransaction) -> SplitCalculation:
        """
        Calculate the multi-party split for a single transaction.

        From $500 employer funding:
          Platform fee (1.25%): $6.25
          PSP fee (varies):     $4.00
          Holdback (5% if high-risk): $0.00 or $25.00
          User receives:        remainder
        """
        gross = transaction.gross_amount
        cents = Decimal("0.01")

        # Platform fee
        platform_fee = (gross * transaction.platform_fee_rate).quantize(cents, rounding=ROUND_HALF_UP)

        # PSP fee from schedule
        fee_key = (transaction.psp_name, transaction.payout_method)
        psp_pct, psp_flat = PSP_FEE_SCHEDULE.get(fee_key, (Decimal("0.029"), Decimal("0.30")))
        psp_fee = (gross * psp_pct + psp_flat).quantize(cents, rounding=ROUND_HALF_UP)

        # Holdback for high-risk
        holdback = (gross * HOLDBACK_RATE).quantize(cents, rounding=ROUND_HALF_UP) if transaction.is_high_risk else Decimal("0.00")

        # User receives the remainder
        user_receives = (gross - platform_fee - psp_fee - holdback).quantize(cents, rounding=ROUND_HALF_UP)

        return SplitCalculation(
            transaction_id=transaction.transaction_id,
            gross_amount=gross,
            platform_fee=platform_fee,
            psp_fee=psp_fee,
            user_receives=user_receives,
            holdback_amount=holdback,
        )

    def _calculate_net_positions(
        self,
        splits: list[SplitCalculation],
        transactions: list[SettledTransaction],
    ) -> list[NetPosition]:
        """
        Net multiple payouts to the same user into a single payout.
        
        Example: User has 3 transactions today ($100, $200, $150).
        Instead of 3 ACH transfers, we send 1 transfer for the net amount.
        This dramatically reduces payout count and PSP costs.
        """
        # Build transaction lookup
        txn_map = {t.transaction_id: t for t in transactions}

        # Aggregate by user
        user_totals: dict[str, dict] = {}
        cents = Decimal("0.01")

        for split in splits:
            txn = txn_map[split.transaction_id]
            user_id = txn.user_id

            if user_id not in user_totals:
                user_totals[user_id] = {
                    "count": 0,
                    "gross": Decimal("0.00"),
                    "fees": Decimal("0.00"),
                    "holdback": Decimal("0.00"),
                    "net": Decimal("0.00"),
                }

            user_totals[user_id]["count"] += 1
            user_totals[user_id]["gross"] += split.gross_amount
            user_totals[user_id]["fees"] += split.platform_fee + split.psp_fee
            user_totals[user_id]["holdback"] += split.holdback_amount
            user_totals[user_id]["net"] += split.user_receives

        return [
            NetPosition(
                user_id=user_id,
                transaction_count=totals["count"],
                gross_total=totals["gross"].quantize(cents, rounding=ROUND_HALF_UP),
                fees_total=totals["fees"].quantize(cents, rounding=ROUND_HALF_UP),
                holdback_total=totals["holdback"].quantize(cents, rounding=ROUND_HALF_UP),
                net_payout=totals["net"].quantize(cents, rounding=ROUND_HALF_UP),
            )
            for user_id, totals in user_totals.items()
        ]

    def submit_batch(self, batch_id: str) -> SettlementBatch:
        """Submit batch for processing. Generates settlement files."""
        batch = self._get_batch(batch_id)
        if batch.status != BatchStatus.CREATED:
            raise ValueError(f"Batch {batch_id} is not in CREATED status")

        batch.status = BatchStatus.SUBMITTED
        batch.submitted_at = datetime.utcnow()
        return batch

    def confirm_batch(self, batch_id: str) -> SettlementBatch:
        """Mark batch as confirmed (bank/PSP confirmed receipt)."""
        batch = self._get_batch(batch_id)
        if batch.status != BatchStatus.SUBMITTED:
            raise ValueError(f"Batch {batch_id} is not in SUBMITTED status")

        batch.status = BatchStatus.CONFIRMED
        batch.confirmed_at = datetime.utcnow()
        return batch

    def generate_nacha_summary(self, batch: SettlementBatch) -> dict:
        """
        Generate NACHA-style summary for ACH settlement.
        In production, this generates the actual NACHA file format.
        """
        ach_positions = [
            np for np in batch.net_positions if np.net_payout > 0
        ]

        return {
            "batch_id": batch.batch_id,
            "settlement_date": batch.settlement_date.isoformat(),
            "entry_count": len(ach_positions),
            "total_debit": sum((np.net_payout for np in ach_positions), Decimal("0.00")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "individual_entries": [
                {
                    "user_id": np.user_id,
                    "amount": np.net_payout,
                    "transaction_count": np.transaction_count,
                }
                for np in ach_positions
            ],
        }

    def get_batch_summary(self, batch_id: str) -> dict:
        """Dashboard data for settlement monitoring."""
        batch = self._get_batch(batch_id)
        return {
            "batch_id": batch.batch_id,
            "status": batch.status.value,
            "settlement_date": batch.settlement_date.isoformat(),
            "transaction_count": batch.transaction_count,
            "gross_amount": batch.gross_amount,
            "platform_fees": batch.total_platform_fees,
            "psp_fees": batch.total_psp_fees,
            "holdback": batch.total_holdback,
            "net_payout": batch.total_net_payout,
            "unique_users": len(batch.net_positions),
            "payout_reduction": f"{batch.transaction_count} txns -> {len(batch.net_positions)} payouts",
        }

    def _get_batch(self, batch_id: str) -> SettlementBatch:
        for batch in self.batches:
            if batch.batch_id == batch_id:
                return batch
        raise ValueError(f"Batch not found: {batch_id}")

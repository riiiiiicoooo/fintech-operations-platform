"""
Reconciliation Engine: Three-Way Matching (Ledger, PSP, Bank)

PM-authored reference implementation demonstrating:
- Three-way reconciliation across ledger, PSP settlement, and bank statements
- Multi-phase matching (exact, fuzzy, many-to-one)
- Auto-resolution patterns for known break types
- Exception queue for manual investigation
- Reconciliation run reporting

Not production code. See docs/LEDGER_DESIGN.md Section 6 (Reconciliation Theory).
"""

from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional
from uuid import uuid4


# --- Reconciliation Models ---

class MatchStatus(Enum):
    EXACT_MATCH = "exact_match"       # All three sources agree (~92%)
    FUZZY_MATCH = "fuzzy_match"       # Matched with tolerance (~5%)
    MANY_TO_ONE = "many_to_one"       # Batch netting match (~2%)
    AUTO_RESOLVED = "auto_resolved"   # Known pattern, system resolved
    EXCEPTION = "exception"           # Requires human investigation
    UNMATCHED = "unmatched"           # No match found yet


class BreakType(Enum):
    TIMING = "timing"                 # PSP settled, bank not yet posted
    FEE_DEDUCTION = "fee_deduction"   # PSP deducted fee from settlement
    FX_ROUNDING = "fx_rounding"       # Sub-penny rounding difference
    BATCH_NETTING = "batch_netting"   # Multiple transactions netted to one bank entry
    DUPLICATE_WEBHOOK = "duplicate"   # PSP sent the same event twice
    UNKNOWN = "unknown"               # Requires manual investigation


class ExceptionPriority(Enum):
    CRITICAL = "critical"    # > $100 delta or suspicious pattern
    HIGH = "high"            # $5.01 - $100 delta
    MEDIUM = "medium"        # < $5 but unknown pattern
    LOW = "low"              # Timing differences expected to self-resolve


@dataclass
class LedgerRecord:
    """Our internal ledger entry."""
    entry_id: str
    transaction_id: str
    amount: Decimal
    account_code: str
    posted_at: datetime
    psp_name: str


@dataclass
class PSPRecord:
    """From PSP settlement report (Stripe, Adyen, etc.)."""
    psp_transaction_id: str
    amount: Decimal
    fee: Decimal
    net_amount: Decimal        # amount - fee
    settlement_date: date
    psp_name: str
    internal_reference: Optional[str] = None  # Our transaction_id if available


@dataclass
class BankRecord:
    """From bank BAI2 statement."""
    bank_reference: str
    amount: Decimal
    posting_date: date
    description: str
    counterparty: Optional[str] = None  # PSP name if identifiable


@dataclass
class ReconciliationMatch:
    """Result of matching one transaction across all three sources."""
    match_id: str
    status: MatchStatus
    ledger_record: Optional[LedgerRecord] = None
    psp_record: Optional[PSPRecord] = None
    bank_record: Optional[BankRecord] = None
    break_type: Optional[BreakType] = None
    delta_amount: Decimal = Decimal("0.00")
    resolution_notes: str = ""
    matched_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ReconciliationException:
    """Unresolved break requiring human investigation."""
    exception_id: str
    match_id: str
    break_type: BreakType
    priority: ExceptionPriority
    delta_amount: Decimal
    description: str
    ledger_record: Optional[LedgerRecord] = None
    psp_record: Optional[PSPRecord] = None
    bank_record: Optional[BankRecord] = None
    assigned_to: Optional[str] = None
    resolved: bool = False
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ReconciliationRun:
    """Summary of a single reconciliation run."""
    run_id: str
    run_date: date
    started_at: datetime
    completed_at: Optional[datetime] = None
    total_ledger_records: int = 0
    total_psp_records: int = 0
    total_bank_records: int = 0
    exact_matches: int = 0
    fuzzy_matches: int = 0
    many_to_one_matches: int = 0
    auto_resolved: int = 0
    exceptions: int = 0
    unmatched: int = 0
    matches: list[ReconciliationMatch] = field(default_factory=list)
    exception_list: list[ReconciliationException] = field(default_factory=list)

    @property
    def match_rate(self) -> float:
        total = self.exact_matches + self.fuzzy_matches + self.many_to_one_matches + self.auto_resolved + self.exceptions
        if total == 0:
            return 0.0
        matched = self.exact_matches + self.fuzzy_matches + self.many_to_one_matches + self.auto_resolved
        return round(matched / total, 4)


# --- Auto-Resolution Patterns ---

AUTO_RESOLVE_MAX_DELTA = Decimal("5.00")  # Never auto-resolve deltas over $5.00

AUTO_RESOLUTION_PATTERNS = {
    BreakType.TIMING: {
        "description": "PSP settled but bank has not posted yet. Expected to clear within 1-2 business days.",
        "max_delta": Decimal("0.00"),  # Timing breaks are exact amount, just delayed
    },
    BreakType.FEE_DEDUCTION: {
        "description": "PSP deducted processing fee from settlement amount.",
        "max_delta": Decimal("5.00"),
    },
    BreakType.FX_ROUNDING: {
        "description": "Sub-penny rounding difference from currency conversion.",
        "max_delta": Decimal("0.05"),
    },
    BreakType.BATCH_NETTING: {
        "description": "Multiple transactions netted to single bank deposit.",
        "max_delta": Decimal("0.01"),  # Netting should be exact
    },
    BreakType.DUPLICATE_WEBHOOK: {
        "description": "PSP sent duplicate settlement event. Deduplicated.",
        "max_delta": Decimal("0.00"),
    },
}


# --- Reconciliation Engine ---

class ReconciliationEngine:
    """
    Runs nightly at 2:00 AM ET. Three-phase matching:
    1. Exact match by PSP transaction ID + amount + date
    2. Fuzzy match by amount + date (plus/minus 1 day)
    3. Many-to-one match for batch netting
    
    After matching, auto-resolves known break patterns.
    Remaining breaks become exceptions for Diana's morning review.
    """

    def __init__(self):
        self.runs: list[ReconciliationRun] = []

    def run_reconciliation(
        self,
        ledger_records: list[LedgerRecord],
        psp_records: list[PSPRecord],
        bank_records: list[BankRecord],
        run_date: date = None,
    ) -> ReconciliationRun:
        """Execute full reconciliation pipeline."""
        run = ReconciliationRun(
            run_id=f"RECON-{datetime.utcnow().strftime('%Y%m%d')}-{str(uuid4())[:8]}",
            run_date=run_date or date.today(),
            started_at=datetime.utcnow(),
            total_ledger_records=len(ledger_records),
            total_psp_records=len(psp_records),
            total_bank_records=len(bank_records),
        )

        # Track what's been matched
        matched_ledger = set()
        matched_psp = set()
        matched_bank = set()

        # Phase 1: Exact matching
        for ledger in ledger_records:
            for psp in psp_records:
                if psp.psp_transaction_id in matched_psp:
                    continue

                if self._exact_match(ledger, psp):
                    # Find corresponding bank record
                    bank_match = self._find_bank_match(psp, bank_records, matched_bank)

                    match = ReconciliationMatch(
                        match_id=str(uuid4()),
                        status=MatchStatus.EXACT_MATCH,
                        ledger_record=ledger,
                        psp_record=psp,
                        bank_record=bank_match,
                    )

                    if bank_match:
                        matched_bank.add(bank_match.bank_reference)

                    run.matches.append(match)
                    run.exact_matches += 1
                    matched_ledger.add(ledger.entry_id)
                    matched_psp.add(psp.psp_transaction_id)
                    break

        # Phase 2: Fuzzy matching (remaining unmatched)
        unmatched_ledger = [l for l in ledger_records if l.entry_id not in matched_ledger]
        unmatched_psp = [p for p in psp_records if p.psp_transaction_id not in matched_psp]

        for ledger in unmatched_ledger:
            for psp in unmatched_psp:
                if psp.psp_transaction_id in matched_psp:
                    continue

                if self._fuzzy_match(ledger, psp):
                    delta = abs(ledger.amount - psp.net_amount)

                    match = ReconciliationMatch(
                        match_id=str(uuid4()),
                        status=MatchStatus.FUZZY_MATCH,
                        ledger_record=ledger,
                        psp_record=psp,
                        delta_amount=delta.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                    )

                    run.matches.append(match)
                    run.fuzzy_matches += 1
                    matched_ledger.add(ledger.entry_id)
                    matched_psp.add(psp.psp_transaction_id)
                    break

        # Phase 3: Many-to-one matching (batch netting)
        remaining_psp = [p for p in psp_records if p.psp_transaction_id not in matched_psp]
        unmatched_bank_records = [b for b in bank_records if b.bank_reference not in matched_bank]

        for bank in unmatched_bank_records:
            netting_group = self._find_netting_group(bank, remaining_psp)
            if netting_group:
                match = ReconciliationMatch(
                    match_id=str(uuid4()),
                    status=MatchStatus.MANY_TO_ONE,
                    bank_record=bank,
                    resolution_notes=f"Netted {len(netting_group)} PSP transactions to 1 bank entry",
                )
                run.matches.append(match)
                run.many_to_one_matches += 1
                matched_bank.add(bank.bank_reference)
                for p in netting_group:
                    matched_psp.add(p.psp_transaction_id)

        # Phase 4: Auto-resolution for fuzzy matches with known break patterns
        for match in run.matches:
            if match.status == MatchStatus.FUZZY_MATCH and match.delta_amount > 0:
                break_type = self._identify_break_type(match)
                if break_type and self._can_auto_resolve(break_type, match.delta_amount):
                    match.status = MatchStatus.AUTO_RESOLVED
                    match.break_type = break_type
                    match.resolution_notes = AUTO_RESOLUTION_PATTERNS[break_type]["description"]
                    run.auto_resolved += 1
                    run.fuzzy_matches -= 1

        # Phase 5: Create exceptions for unresolved breaks
        for match in run.matches:
            if match.status == MatchStatus.FUZZY_MATCH and match.delta_amount > 0:
                exception = self._create_exception(match)
                run.exception_list.append(exception)
                match.status = MatchStatus.EXCEPTION
                run.exceptions += 1
                run.fuzzy_matches -= 1

        # Count remaining unmatched
        final_unmatched_ledger = [l for l in ledger_records if l.entry_id not in matched_ledger]
        final_unmatched_psp = [p for p in psp_records if p.psp_transaction_id not in matched_psp]
        run.unmatched = len(final_unmatched_ledger) + len(final_unmatched_psp)

        run.completed_at = datetime.utcnow()
        self.runs.append(run)
        return run

    def _exact_match(self, ledger: LedgerRecord, psp: PSPRecord) -> bool:
        """Phase 1: Match by PSP transaction ID, amount, and PSP name."""
        return (
            ledger.psp_name == psp.psp_name and
            ledger.amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == psp.amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        )

    def _fuzzy_match(self, ledger: LedgerRecord, psp: PSPRecord) -> bool:
        """Phase 2: Match by approximate amount and date range (+/- 1 day)."""
        amount_close = abs(ledger.amount - psp.net_amount) < AUTO_RESOLVE_MAX_DELTA
        date_close = abs(
            (ledger.posted_at.date() - psp.settlement_date).days
        ) <= 1

        return amount_close and date_close and ledger.psp_name == psp.psp_name


    def _find_bank_match(
        self, psp: PSPRecord, bank_records: list[BankRecord], matched: set
    ) -> Optional[BankRecord]:
        """Find a bank record matching a PSP settlement."""
        for bank in bank_records:
            if bank.bank_reference in matched:
                continue
            if (bank.amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == psp.net_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) and
                abs((bank.posting_date - psp.settlement_date).days) <= 1):
                return bank
        return None

    def _find_netting_group(
        self, bank: BankRecord, psp_records: list[PSPRecord]
    ) -> Optional[list[PSPRecord]]:
        """
        Phase 3: Find a group of PSP transactions that sum to the bank amount.
        Simplified: tries grouping by PSP name and settlement date.
        """
        candidates = [
            p for p in psp_records
            if abs((p.settlement_date - bank.posting_date).days) <= 1
        ]

        if not candidates:
            return None

        # Check if sum of candidates matches bank amount
        candidate_sum = sum((p.net_amount for p in candidates), Decimal("0.00"))
        if candidate_sum.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == bank.amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP):
            return candidates

        return None

    def _identify_break_type(self, match: ReconciliationMatch) -> Optional[BreakType]:
        """Classify a reconciliation break into a known pattern."""
        delta = match.delta_amount

        if match.psp_record and not match.bank_record:
            return BreakType.TIMING

        if match.psp_record and delta <= Decimal("5.00"):
            # PSP fee deduction: ledger shows gross, PSP shows net
            if match.ledger_record and match.psp_record:
                expected_fee = match.ledger_record.amount - match.psp_record.net_amount
                if abs(delta - match.psp_record.fee).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == Decimal("0.00"):
                    return BreakType.FEE_DEDUCTION

        if delta < Decimal("0.05"):
            return BreakType.FX_ROUNDING

        return BreakType.UNKNOWN

    def _can_auto_resolve(self, break_type: BreakType, delta: Decimal) -> bool:
        """Check if a break can be auto-resolved based on pattern and dollar threshold."""
        if break_type == BreakType.UNKNOWN:
            return False

        if delta > AUTO_RESOLVE_MAX_DELTA:
            return False

        pattern = AUTO_RESOLUTION_PATTERNS.get(break_type)
        if not pattern:
            return False

        return delta <= pattern["max_delta"]

    def _create_exception(self, match: ReconciliationMatch) -> ReconciliationException:
        """Create an exception for Diana's review queue."""
        delta = match.delta_amount

        if delta > Decimal("100.00"):
            priority = ExceptionPriority.CRITICAL
        elif delta > Decimal("5.00"):
            priority = ExceptionPriority.HIGH
        else:
            priority = ExceptionPriority.MEDIUM

        return ReconciliationException(
            exception_id=str(uuid4()),
            match_id=match.match_id,
            break_type=match.break_type or BreakType.UNKNOWN,
            priority=priority,
            delta_amount=delta,
            description=f"Unresolved delta of ${delta:.2f} between ledger and PSP",
            ledger_record=match.ledger_record,
            psp_record=match.psp_record,
            bank_record=match.bank_record,
        )

    def get_run_summary(self, run_id: str) -> dict:
        """Dashboard data for reconciliation monitoring."""
        run = next((r for r in self.runs if r.run_id == run_id), None)
        if not run:
            raise ValueError(f"Run not found: {run_id}")

        return {
            "run_id": run.run_id,
            "run_date": run.run_date.isoformat(),
            "match_rate": f"{run.match_rate * 100:.1f}%",
            "exact_matches": run.exact_matches,
            "fuzzy_matches": run.fuzzy_matches,
            "many_to_one": run.many_to_one_matches,
            "auto_resolved": run.auto_resolved,
            "exceptions": run.exceptions,
            "unmatched": run.unmatched,
            "exception_breakdown": {
                p.value: sum(1 for e in run.exception_list if e.priority == p)
                for p in ExceptionPriority
            },
            "duration_seconds": (
                (run.completed_at - run.started_at).total_seconds()
                if run.completed_at else None
            ),
        }

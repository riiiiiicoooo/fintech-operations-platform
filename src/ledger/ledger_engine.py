"""
Ledger Engine: Double-Entry Transaction Recording and Balance Management

PM-authored reference implementation demonstrating:
- Double-entry journal entries where debits always equal credits
- Account taxonomy (asset, liability, revenue, expense)
- Balance calculation from immutable journal entry lines
- Hold management for preventing double-spend during async settlement
- Idempotency key handling for safe retries

Not production code. See docs/LEDGER_DESIGN.md for full accounting model.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional
from uuid import uuid4
import hashlib


# --- Account Model ---

class AccountType(Enum):
    ASSET = "asset"           # Normal balance: debit (increases when debited)
    LIABILITY = "liability"   # Normal balance: credit (increases when credited)
    REVENUE = "revenue"       # Normal balance: credit (increases when credited)
    EXPENSE = "expense"       # Normal balance: debit (increases when debited)


class AccountCode(Enum):
    """Chart of accounts. Each user/employer gets sub-accounts via entity_id."""
    # Assets
    EMPLOYER_FUNDING_HOLDING = "employer_funding_holding"
    USER_WALLET = "user_wallet"
    PSP_RECEIVABLE = "psp_receivable"
    BANK_OPERATING = "bank_operating"
    BANK_RESERVE = "bank_reserve"
    SETTLEMENT_IN_TRANSIT = "settlement_in_transit"

    # Liabilities
    EMPLOYER_PAYABLE = "employer_payable"
    USER_PAYABLE = "user_payable"
    CHARGEBACK_RESERVE = "chargeback_reserve"
    SUSPENSE = "suspense"

    # Revenue
    PLATFORM_FEE = "platform_fee"
    SUBSCRIPTION_REVENUE = "subscription_revenue"

    # Expenses
    PSP_PROCESSING_FEES = "psp_processing_fees"
    FRAUD_LOSSES = "fraud_losses"
    BANK_TRANSFER_FEES = "bank_transfer_fees"


ACCOUNT_TYPE_MAP = {
    AccountCode.EMPLOYER_FUNDING_HOLDING: AccountType.ASSET,
    AccountCode.USER_WALLET: AccountType.ASSET,
    AccountCode.PSP_RECEIVABLE: AccountType.ASSET,
    AccountCode.BANK_OPERATING: AccountType.ASSET,
    AccountCode.BANK_RESERVE: AccountType.ASSET,
    AccountCode.SETTLEMENT_IN_TRANSIT: AccountType.ASSET,
    AccountCode.EMPLOYER_PAYABLE: AccountType.LIABILITY,
    AccountCode.USER_PAYABLE: AccountType.LIABILITY,
    AccountCode.CHARGEBACK_RESERVE: AccountType.LIABILITY,
    AccountCode.SUSPENSE: AccountType.LIABILITY,
    AccountCode.PLATFORM_FEE: AccountType.REVENUE,
    AccountCode.SUBSCRIPTION_REVENUE: AccountType.REVENUE,
    AccountCode.PSP_PROCESSING_FEES: AccountType.EXPENSE,
    AccountCode.FRAUD_LOSSES: AccountType.EXPENSE,
    AccountCode.BANK_TRANSFER_FEES: AccountType.EXPENSE,
}


@dataclass
class Account:
    account_id: str
    account_code: AccountCode
    account_type: AccountType
    entity_id: Optional[str] = None    # user_id, employer_id, or psp_name
    entity_type: Optional[str] = None  # "user", "employer", "platform", "psp"
    created_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def full_code(self) -> str:
        """e.g., 'user_wallet:user_8472' or 'platform_fee' (no entity)"""
        if self.entity_id:
            return f"{self.account_code.value}:{self.entity_id}"
        return self.account_code.value


# --- Journal Entry Model ---

class EntryType(Enum):
    FUNDING = "funding"
    TRANSFER = "transfer"
    FEE = "fee"
    REFUND = "refund"
    CHARGEBACK = "chargeback"
    SETTLEMENT = "settlement"
    REVERSAL = "reversal"
    CORRECTION = "correction"
    SUSPENSE_CLASSIFICATION = "suspense_classification"


@dataclass
class JournalEntryLine:
    account: Account
    debit: Decimal = Decimal("0.00")
    credit: Decimal = Decimal("0.00")

    def __post_init__(self):
        if self.debit < 0 or self.credit < 0:
            raise ValueError("Debit and credit amounts must be non-negative")
        if self.debit > 0 and self.credit > 0:
            raise ValueError("A line cannot have both a debit and a credit")
        if self.debit == 0 and self.credit == 0:
            raise ValueError("A line must have either a debit or a credit")


@dataclass
class JournalEntry:
    entry_id: str
    entry_type: EntryType
    description: str
    idempotency_key: str
    lines: list[JournalEntryLine]
    reference_type: Optional[str] = None  # "refund_of", "reversal_of", etc.
    reference_id: Optional[str] = None    # ID of the referenced entry
    posted_at: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self):
        self._validate_balanced()

    def _validate_balanced(self):
        """The core invariant: total debits must equal total credits."""
        total_debits = sum(line.debit for line in self.lines)
        total_credits = sum(line.credit for line in self.lines)

        if total_debits.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) != total_credits.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP):
            raise ValueError(
                f"Journal entry is not balanced. "
                f"Debits: ${total_debits:.2f}, Credits: ${total_credits:.2f}, "
                f"Delta: ${abs(total_debits - total_credits):.2f}"
            )

    @property
    def total_amount(self) -> Decimal:
        return sum((line.debit for line in self.lines), Decimal("0.00"))


# --- Hold Model ---

class HoldStatus(Enum):
    ACTIVE = "active"
    CAPTURED = "captured"
    VOIDED = "voided"
    EXPIRED = "expired"


@dataclass
class Hold:
    hold_id: str
    account: Account
    amount: Decimal
    status: HoldStatus = HoldStatus.ACTIVE
    transaction_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: datetime = None

    def __post_init__(self):
        if self.expires_at is None:
            self.expires_at = self.created_at + timedelta(days=7)

    @property
    def is_expired(self) -> bool:
        return datetime.utcnow() > self.expires_at and self.status == HoldStatus.ACTIVE


# --- Ledger Engine ---

class LedgerEngine:
    """
    Core ledger operations. In production, this is backed by PostgreSQL with
    SERIALIZABLE isolation. Here we use in-memory storage to demonstrate the logic.
    """

    def __init__(self):
        self.accounts: dict[str, Account] = {}
        self.journal_entries: list[JournalEntry] = []
        self.holds: list[Hold] = []
        self.idempotency_keys: dict[str, JournalEntry] = {}

    # --- Account Management ---

    def create_account(
        self,
        account_code: AccountCode,
        entity_id: Optional[str] = None,
        entity_type: Optional[str] = None,
    ) -> Account:
        account = Account(
            account_id=str(uuid4()),
            account_code=account_code,
            account_type=ACCOUNT_TYPE_MAP[account_code],
            entity_id=entity_id,
            entity_type=entity_type,
        )

        if account.full_code in self.accounts:
            raise ValueError(f"Account already exists: {account.full_code}")

        self.accounts[account.full_code] = account
        return account

    def get_account(self, account_code: AccountCode, entity_id: Optional[str] = None) -> Account:
        if entity_id:
            key = f"{account_code.value}:{entity_id}"
        else:
            key = account_code.value

        account = self.accounts.get(key)
        if not account:
            raise ValueError(f"Account not found: {key}")
        return account

    # --- Balance Calculation ---

    def get_posted_balance(self, account: Account) -> Decimal:
        """
        Calculate balance from journal entry lines. This is the canonical balance.

        For ASSET and EXPENSE accounts (debit-normal):
            balance = SUM(debits) - SUM(credits)
        For LIABILITY and REVENUE accounts (credit-normal):
            balance = SUM(credits) - SUM(debits)
        """
        total_debits = Decimal("0.00")
        total_credits = Decimal("0.00")

        for entry in self.journal_entries:
            for line in entry.lines:
                if line.account.full_code == account.full_code:
                    total_debits += line.debit
                    total_credits += line.credit

        if account.account_type in (AccountType.ASSET, AccountType.EXPENSE):
            return total_debits - total_credits
        else:
            return total_credits - total_debits

    def get_available_balance(self, account: Account) -> Decimal:
        """
        Available balance = posted balance - active holds.
        This is what the user can actually spend.
        """
        posted = self.get_posted_balance(account)
        active_holds = sum(
            (h.amount for h in self.holds
             if h.account.full_code == account.full_code
             and h.status == HoldStatus.ACTIVE),
            Decimal("0.00"),
        )
        return posted - active_holds

    # --- Hold Management ---

    def create_hold(self, account: Account, amount: Decimal, transaction_id: str) -> Hold:
        available = self.get_available_balance(account)
        if amount > available:
            raise ValueError(
                f"Insufficient available balance. "
                f"Requested: ${amount:.2f}, Available: ${available:.2f}"
            )

        hold = Hold(
            hold_id=str(uuid4()),
            account=account,
            amount=amount,
            transaction_id=transaction_id,
        )
        self.holds.append(hold)
        return hold

    def capture_hold(self, hold_id: str) -> Hold:
        hold = self._get_hold(hold_id)
        if hold.status != HoldStatus.ACTIVE:
            raise ValueError(f"Cannot capture hold in status: {hold.status.value}")
        hold.status = HoldStatus.CAPTURED
        return hold

    def void_hold(self, hold_id: str) -> Hold:
        hold = self._get_hold(hold_id)
        if hold.status != HoldStatus.ACTIVE:
            raise ValueError(f"Cannot void hold in status: {hold.status.value}")
        hold.status = HoldStatus.VOIDED
        return hold

    def expire_stale_holds(self) -> list[Hold]:
        """
        Run hourly. Finds holds past 7-day expiration.
        Each expired hold is a critical alert (something went wrong).
        """
        expired = []
        for hold in self.holds:
            if hold.is_expired:
                hold.status = HoldStatus.EXPIRED
                expired.append(hold)
        return expired

    def _get_hold(self, hold_id: str) -> Hold:
        for hold in self.holds:
            if hold.hold_id == hold_id:
                return hold
        raise ValueError(f"Hold not found: {hold_id}")

    # --- Journal Entry Creation ---

    def post_entry(
        self,
        entry_type: EntryType,
        description: str,
        idempotency_key: str,
        lines: list[JournalEntryLine],
        reference_type: Optional[str] = None,
        reference_id: Optional[str] = None,
    ) -> JournalEntry:
        """
        Post a journal entry. The core write operation of the ledger.
        
        Enforces:
        1. Idempotency (duplicate key returns cached result)
        2. Balanced entry (debits == credits)
        3. All accounts exist
        4. Entry is immutable once posted
        """
        # Idempotency check
        if idempotency_key in self.idempotency_keys:
            return self.idempotency_keys[idempotency_key]

        # Validate all accounts exist
        for line in lines:
            if line.account.full_code not in self.accounts:
                raise ValueError(f"Account not found: {line.account.full_code}")

        # Create entry (constructor validates balanced)
        entry = JournalEntry(
            entry_id=f"JE-{datetime.utcnow().strftime('%Y%m%d')}-{str(uuid4())[:8]}",
            entry_type=entry_type,
            description=description,
            idempotency_key=idempotency_key,
            lines=lines,
            reference_type=reference_type,
            reference_id=reference_id,
        )

        # Commit (in production: single DB transaction with SERIALIZABLE isolation)
        self.journal_entries.append(entry)
        self.idempotency_keys[idempotency_key] = entry

        return entry

    # --- Transaction Patterns ---

    def record_employer_funding(
        self,
        employer_id: str,
        amount: Decimal,
        idempotency_key: str,
    ) -> JournalEntry:
        """Step 1 of employer funding: lump sum received, held in holding account."""
        bank = self.get_account(AccountCode.BANK_OPERATING)
        holding = self.get_account(AccountCode.EMPLOYER_FUNDING_HOLDING, employer_id)

        return self.post_entry(
            entry_type=EntryType.FUNDING,
            description=f"Employer {employer_id} funding: ${amount:.2f}",
            idempotency_key=idempotency_key,
            lines=[
                JournalEntryLine(account=bank, debit=amount),
                JournalEntryLine(account=holding, credit=amount),
            ],
        )

    def allocate_to_wallet(
        self,
        employer_id: str,
        user_id: str,
        gross_amount: Decimal,
        platform_fee: Decimal,
        idempotency_key: str,
    ) -> JournalEntry:
        """Step 2 of employer funding: allocate from holding to individual wallet."""
        holding = self.get_account(AccountCode.EMPLOYER_FUNDING_HOLDING, employer_id)
        wallet = self.get_account(AccountCode.USER_WALLET, user_id)
        fee_account = self.get_account(AccountCode.PLATFORM_FEE)

        net_to_user = gross_amount - platform_fee

        return self.post_entry(
            entry_type=EntryType.FUNDING,
            description=f"Allocate ${gross_amount:.2f} to user {user_id} (fee: ${platform_fee:.2f})",
            idempotency_key=idempotency_key,
            lines=[
                JournalEntryLine(account=holding, debit=gross_amount),
                JournalEntryLine(account=wallet, credit=net_to_user),
                JournalEntryLine(account=fee_account, credit=platform_fee),
            ],
        )

    def record_user_transfer(
        self,
        user_id: str,
        amount: Decimal,
        psp_name: str,
        idempotency_key: str,
    ) -> tuple[JournalEntry, Hold]:
        """
        User-initiated transfer (earned wage access).
        Creates ledger entry AND hold simultaneously.
        """
        wallet = self.get_account(AccountCode.USER_WALLET, user_id)
        psp_receivable = self.get_account(AccountCode.PSP_RECEIVABLE, psp_name)

        # Check available balance (includes existing holds)
        available = self.get_available_balance(wallet)
        if amount > available:
            raise ValueError(
                f"Insufficient balance. Requested: ${amount:.2f}, Available: ${available:.2f}"
            )

        transaction_id = str(uuid4())

        entry = self.post_entry(
            entry_type=EntryType.TRANSFER,
            description=f"User {user_id} transfer: ${amount:.2f} via {psp_name}",
            idempotency_key=idempotency_key,
            lines=[
                JournalEntryLine(account=wallet, debit=amount),
                JournalEntryLine(account=psp_receivable, credit=amount),
            ],
        )

        hold = self.create_hold(wallet, amount, transaction_id)

        return entry, hold

    def record_refund(
        self,
        original_entry_id: str,
        user_id: str,
        amount: Decimal,
        psp_name: str,
        idempotency_key: str,
    ) -> JournalEntry:
        """
        Refund: new entry that reverses the original flow.
        Original entry is unchanged (immutability).
        """
        psp_receivable = self.get_account(AccountCode.PSP_RECEIVABLE, psp_name)
        wallet = self.get_account(AccountCode.USER_WALLET, user_id)

        return self.post_entry(
            entry_type=EntryType.REFUND,
            description=f"Refund ${amount:.2f} to user {user_id}",
            idempotency_key=idempotency_key,
            lines=[
                JournalEntryLine(account=psp_receivable, debit=amount),
                JournalEntryLine(account=wallet, credit=amount),
            ],
            reference_type="refund_of",
            reference_id=original_entry_id,
        )

    def record_chargeback(
        self,
        amount: Decimal,
        psp_name: str,
        idempotency_key: str,
    ) -> JournalEntry:
        """
        Chargeback received from card network.
        Records as fraud loss. Recovery from user (if possible) is a separate entry.
        """
        fraud_loss = self.get_account(AccountCode.FRAUD_LOSSES)
        psp_receivable = self.get_account(AccountCode.PSP_RECEIVABLE, psp_name)

        return self.post_entry(
            entry_type=EntryType.CHARGEBACK,
            description=f"Chargeback: ${amount:.2f} from {psp_name}",
            idempotency_key=idempotency_key,
            lines=[
                JournalEntryLine(account=fraud_loss, debit=amount),
                JournalEntryLine(account=psp_receivable, credit=amount),
            ],
        )

    # --- Audit and Reporting ---

    def get_account_history(self, account: Account) -> list[dict]:
        """Returns all journal entry lines for a specific account, chronologically."""
        history = []
        running_balance = Decimal("0.00")

        for entry in self.journal_entries:
            for line in entry.lines:
                if line.account.full_code == account.full_code:
                    if account.account_type in (AccountType.ASSET, AccountType.EXPENSE):
                        running_balance += line.debit - line.credit
                    else:
                        running_balance += line.credit - line.debit

                    history.append({
                        "entry_id": entry.entry_id,
                        "entry_type": entry.entry_type.value,
                        "description": entry.description,
                        "debit": line.debit,
                        "credit": line.credit,
                        "running_balance": running_balance.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                        "posted_at": entry.posted_at.isoformat(),
                    })

        return history

    def verify_system_balance(self) -> dict:
        """
        Verify that total system debits equal total system credits.
        This should always be true. If it's not, there's a bug.
        """
        total_debits = Decimal("0.00")
        total_credits = Decimal("0.00")

        for entry in self.journal_entries:
            for line in entry.lines:
                total_debits += line.debit
                total_credits += line.credit

        delta = abs(total_debits - total_credits)
        balanced = total_debits.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == total_credits.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        return {
            "total_debits": total_debits.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "total_credits": total_credits.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "delta": delta.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "balanced": balanced,
            "entry_count": len(self.journal_entries),
        }

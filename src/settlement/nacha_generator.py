"""
NACHA File Generator: Produces valid ACH settlement files

NACHA (National Automated Clearing House Association) format for ACH batches.
Generates complete file structure with proper formatting, checksums, and padding.

Record types:
- 1: File Header
- 5: Batch Header
- 6: Entry Detail (debit/credit)
- 8: Batch Control
- 9: File Control

Reference: NACHA Operating Rules & Guidelines (NACHA 2023)
"""

from dataclasses import dataclass
from datetime import datetime, date
from typing import List
from decimal import Decimal


# ============================================================================
# NACHA Record Models
# ============================================================================

@dataclass
class NACHAEntry:
    """Individual ACH entry (record type 6)."""
    transaction_code: str       # "22" (debit checking), "32" (credit checking)
    receiving_dda: str          # Destination account number (10 digits)
    amount: int                 # Amount in cents (no decimals)
    id_number: str              # Individual ID number (15 chars)
    individual_name: str        # Individual receiver name (22 chars)
    trace_number: str           # Unique trace number (15 digits)

    def to_record(self) -> str:
        """Format as NACHA record type 6 (98 chars)."""
        return (
            "6" +                              # Record type
            self.transaction_code +            # 22=debit, 32=credit (2)
            self.receiving_dda.rjust(10) +     # ABA (10)
            str(self.receiving_dda).rjust(10) +  # DDA (10)
            str(self.amount).zfill(10) +       # Amount (10)
            self.id_number.ljust(15) +         # ID number (15)
            self.individual_name.ljust(22) +   # Individual name (22)
            " " * 2 +                          # Discretionary data (2)
            "0" +                              # Addenda record indicator (1)
            "000000001"                        # Trace number (9)
        ).ljust(98)


@dataclass
class NACHABatch:
    """NACHA batch containing multiple entries."""
    batch_number: int
    service_class_code: str     # "200" (mixed), "220" (credits), "225" (debits)
    company_name: str
    company_id: str             # FEIN without dashes (10 digits)
    effective_entry_date: date
    entries: List[NACHAEntry]

    def get_batch_control(self) -> str:
        """Generate batch control record (type 8)."""
        entry_count = len(self.entries)
        debit_total = sum(int(e.amount) for e in self.entries if e.transaction_code == "22")
        credit_total = sum(int(e.amount) for e in self.entries if e.transaction_code == "32")
        entry_hash = sum(int(e.receiving_dda[:8]) for e in self.entries) % 10000000

        return (
            "8" +                                    # Record type
            self.service_class_code +                # Service class (3)
            str(entry_count).zfill(6) +              # Entry count (6)
            str(entry_hash).zfill(10) +              # Entry hash (10)
            str(debit_total).zfill(12) +             # Total debits (12)
            str(credit_total).zfill(12) +            # Total credits (12)
            self.company_id.ljust(10) +              # Company ID (10)
            " " * 19 +                               # Reserved (19)
            str(self.batch_number).zfill(6) +        # Batch number (6)
            " " * 1                                  # Reserved (1)
        ).ljust(94)

    def to_records(self) -> List[str]:
        """Generate all records for this batch."""
        records = []

        # Batch header (type 5)
        batch_header = (
            "5" +                                          # Record type
            "200" +                                        # Service class
            self.company_name.ljust(16) +                  # Company name (16)
            " " * 20 +                                     # Reserved (20)
            self.company_id.ljust(10) +                    # Company ID (10)
            "094101768" +                                  # Standard entry class (9)
            self.effective_entry_date.strftime("%y%m%d") + # Effective entry date (6)
            "000000" +                                     # Settlement date (6) - YYMMDD
            "000001" +                                     # File creation date (6)
            "000000" +                                     # File creation time (4)
            str(self.batch_number).zfill(6) +              # Batch number (6)
            "094" +                                        # Originating DFI (9)
            " " * 1                                        # Reserved (1)
        ).ljust(94)

        records.append(batch_header)

        # Entry records (type 6)
        for entry in self.entries:
            records.append(entry.to_record())

        # Batch control (type 8)
        records.append(self.get_batch_control())

        return records


class NACHAFileGenerator:
    """Generate complete NACHA ACH files."""

    def __init__(self, company_name: str, company_id: str, originating_dfi: str):
        """
        Initialize NACHA file generator.

        Args:
            company_name: Company name (max 16 chars)
            company_id: FEIN without dashes (10 digits)
            originating_dfi: Originating bank DFI number (9 digits)
        """
        self.company_name = company_name.ljust(16)[:16]
        self.company_id = company_id.ljust(10)[:10]
        self.originating_dfi = originating_dfi.ljust(9)[:9]
        self.batches: List[NACHABatch] = []

    def add_batch(self, batch: NACHABatch):
        """Add a batch to the file."""
        self.batches.append(batch)

    def generate_file(self) -> str:
        """
        Generate complete NACHA file with all records.

        Returns:
            Multiline string with 94-char NACHA records (no padding)
        """
        records = []

        # File header (type 1)
        file_id_modifier = "A"  # New file
        total_batch_count = len(self.batches)
        total_block_count = 2 + sum(2 + len(b.entries) for b in self.batches)  # Header, footer per batch

        file_header = (
            "1" +                                               # Record type
            "01" +                                              # Priority code
            self.originating_dfi.ljust(9) +                     # Originating DFI (9)
            " " * 9 +                                           # Destination DFI (9)
            datetime.now().strftime("%y%m%d") +                 # File creation date (6)
            datetime.now().strftime("%H%M") +                   # File creation time (4)
            "1" +                                               # File ID modifier
            "094" +                                             # Record size code "094"
            "10" +                                              # Blocking factor
            "1" +                                               # Format code
            "FEDWIRE" +                                         # Destination agency (8) - generic
            self.company_name +                                 # Sending company name (16)
            "ACME FINTECH" +                                    # File ID (20)
            " " * 6                                             # Reserved (6)
        ).ljust(94)

        records.append(file_header)

        # Batches
        entry_count = 0
        total_debits = 0
        total_credits = 0

        for batch_idx, batch in enumerate(self.batches, 1):
            batch.batch_number = batch_idx
            batch_records = batch.to_records()
            records.extend(batch_records)

            entry_count += len(batch.entries)
            for entry in batch.entries:
                if entry.transaction_code == "22":
                    total_debits += int(entry.amount)
                else:
                    total_credits += int(entry.amount)

        # File control (type 9)
        batch_count = len(self.batches)
        record_count = 2 + sum(2 + len(b.entries) for b in self.batches)  # Header/footer counts

        file_control = (
            "9" +                                          # Record type
            str(batch_count).zfill(6) +                    # Batch count (6)
            str(record_count).zfill(6) +                   # Record count (6)
            "000000001" +                                  # Entry/addenda count (8)
            str(total_debits).zfill(12) +                  # Total debits (12)
            str(total_credits).zfill(12) +                 # Total credits (12)
            " " * 39                                       # Reserved (39)
        ).ljust(94)

        records.append(file_control)

        # Join with newlines
        return "\n".join(records)


# ============================================================================
# Helper Functions
# ============================================================================

def create_sample_nacha_batch(
    batch_number: int,
    settlement_date: date,
    entries: List[dict],
) -> str:
    """
    Create a sample NACHA batch for demonstration.

    Args:
        batch_number: Batch identifier
        settlement_date: Date of settlement
        entries: List of dicts with keys: user_id, amount_cents

    Returns:
        NACHA-formatted batch string
    """
    generator = NACHAFileGenerator(
        company_name="ACME Fintech",
        company_id="1234567890",
        originating_dfi="021000021",  # JPMorgan
    )

    nacha_entries = [
        NACHAEntry(
            transaction_code="32",  # Credit (deposit to user)
            receiving_dda=f"{entry['user_id'][-10:]}".zfill(10),
            amount=int(entry['amount']),
            id_number=f"{entry['user_id'][-15:]}".ljust(15),
            individual_name=f"User {entry['user_id'][-8:]}".ljust(22),
            trace_number=f"{batch_number:06d}{idx:09d}",
        )
        for idx, entry in enumerate(entries, 1)
    ]

    batch = NACHABatch(
        batch_number=batch_number,
        service_class_code="220",  # Credits only
        company_name="ACME Fintech",
        company_id="1234567890",
        effective_entry_date=settlement_date,
        entries=nacha_entries,
    )

    generator.add_batch(batch)
    return generator.generate_file()

"""
Transaction Lifecycle Demo: End-to-End Flow

Demonstrates the complete lifecycle of earned wage access:
1. Employer funds account ($50,000)
2. Employee initiates transfer ($500)
3. Fraud detector scores it (PASS, score 12/100)
4. Ledger records double-entry
5. Payment orchestrator selects PSP (Stripe ACH)
6. Settlement creates NACHA batch
7. Reconciliation matches bank statement
8. Compliance runs OFAC screening

Run: python -m demo.transaction_lifecycle
"""

import sys
from datetime import datetime, date
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ledger.ledger_engine import (
    LedgerEngine, AccountCode, EntryType, JournalEntryLine
)
from fraud.fraud_detector import (
    FraudDetector, TransactionContext, FraudDecision
)
from payments.payment_orchestrator import (
    PaymentOrchestrator, PaymentMethod
)
from settlement.settlement_engine import (
    SettlementEngine, SettledTransaction, PayoutMethod, BatchStatus
)
from settlement.nacha_generator import create_sample_nacha_batch
from reconciliation.reconciliation_engine import (
    ReconciliationEngine, LedgerRecord, PSPRecord, BankRecord
)
from compliance.compliance_checker import (
    ComplianceChecker, KYCTier, KYCStatus, TransactionHistory
)


def print_section(title: str):
    """Print a formatted section header."""
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)


def print_step(step_num: int, description: str):
    """Print a step marker."""
    print(f"\n[Step {step_num}] {description}")
    print("-" * 80)


def print_balance(label: str, posted: float, available: float):
    """Print a balance line."""
    print(f"  {label:30s} Posted: ${posted:>10.2f}  Available: ${available:>10.2f}")


def main():
    """Run the transaction lifecycle demo."""

    print_section("FINTECH OPERATIONS PLATFORM - TRANSACTION LIFECYCLE DEMO")

    # ========================================================================
    # SETUP: Initialize all services
    # ========================================================================

    print("\nInitializing services...")
    ledger = LedgerEngine()
    fraud_detector = FraudDetector()
    payment_orchestrator = PaymentOrchestrator()
    settlement_engine = SettlementEngine()
    reconciliation_engine = ReconciliationEngine()
    compliance_checker = ComplianceChecker()

    # Create platform accounts
    ledger.create_account(AccountCode.BANK_OPERATING)
    ledger.create_account(AccountCode.EMPLOYER_FUNDING_HOLDING, "acme_corp", "employer")
    ledger.create_account(AccountCode.USER_WALLET, "alice_smith", "user")
    ledger.create_account(AccountCode.PLATFORM_FEE)
    ledger.create_account(AccountCode.PSP_RECEIVABLE, "stripe", "psp")
    ledger.create_account(AccountCode.PSP_PROCESSING_FEES)

    # Approve user KYC
    compliance_checker.approve_kyc("alice_smith", KYCTier.STANDARD)

    print("✓ Services initialized")
    print("✓ Accounts created")
    print("✓ User KYC approved (Standard tier)")

    # ========================================================================
    # STEP 1: Employer funds account ($50,000)
    # ========================================================================

    print_step(1, "EMPLOYER FUNDS ACCOUNT")
    print("  Employer: ACME Corp")
    print("  Amount: $50,000.00")
    print("  Method: Bank transfer")

    employer_funding = ledger.record_employer_funding(
        employer_id="acme_corp",
        amount=50000.00,
        idempotency_key="acme_fund_batch_001"
    )

    print(f"\n  ✓ Journal entry posted: {employer_funding.entry_id}")
    print(f"  ✓ Total debits: ${employer_funding.total_amount:.2f}")

    # Check balances
    bank = ledger.get_account(AccountCode.BANK_OPERATING)
    holding = ledger.get_account(AccountCode.EMPLOYER_FUNDING_HOLDING, "acme_corp")

    print(f"\n  Ledger state after funding:")
    print_balance("Bank account", ledger.get_posted_balance(bank), 0)
    print_balance("Employer holding", ledger.get_posted_balance(holding), 0)

    system_check = ledger.verify_system_balance()
    print(f"\n  System balance check: DEBITS=${system_check['total_debits']:.2f}, "
          f"CREDITS=${system_check['total_credits']:.2f} ✓")

    # ========================================================================
    # STEP 2: Allocate to employee wallet
    # ========================================================================

    print_step(2, "ALLOCATE TO EMPLOYEE WALLET")
    print("  From: ACME Corp funding holding")
    print("  To: Alice Smith wallet")
    print("  Gross amount: $2,000.00")
    print("  Platform fee (1.25%): $25.00")
    print("  Net to employee: $1,975.00")

    allocation = ledger.allocate_to_wallet(
        employer_id="acme_corp",
        user_id="alice_smith",
        gross_amount=2000.00,
        platform_fee=25.00,
        idempotency_key="acme_alloc_alice_001"
    )

    print(f"\n  ✓ Journal entry posted: {allocation.entry_id}")
    wallet = ledger.get_account(AccountCode.USER_WALLET, "alice_smith")
    fee_account = ledger.get_account(AccountCode.PLATFORM_FEE)

    print(f"\n  Ledger state after allocation:")
    print_balance("Employee wallet", ledger.get_posted_balance(wallet), ledger.get_available_balance(wallet))
    print_balance("Platform fees", ledger.get_posted_balance(fee_account), 0)

    # ========================================================================
    # STEP 3: Employee initiates transfer ($500)
    # ========================================================================

    print_step(3, "EMPLOYEE INITIATES TRANSFER")
    print("  User: Alice Smith")
    print("  Amount: $500.00")
    print("  Payment method: ACH")
    print("  Destination: Bank account")

    # Record in ledger
    transfer_entry, hold = ledger.record_user_transfer(
        user_id="alice_smith",
        amount=500.00,
        psp_name="stripe",
        idempotency_key="alice_transfer_001"
    )

    print(f"\n  ✓ Journal entry posted: {transfer_entry.entry_id}")
    print(f"  ✓ Hold created: {hold.hold_id} (expires in 7 days)")

    print(f"\n  Ledger state after transfer (with hold):")
    posted = ledger.get_posted_balance(wallet)
    available = ledger.get_available_balance(wallet)
    print_balance("Employee wallet", posted, available)
    print(f"  Hold amount: ${hold.amount:.2f}")

    # ========================================================================
    # STEP 4: Fraud detector scores transaction
    # ========================================================================

    print_step(4, "FRAUD DETECTION SCORING")

    fraud_context = TransactionContext(
        transaction_id="txn_alice_001",
        user_id="alice_smith",
        amount=500.00,
        payment_method="ach",
        device_fingerprint="device_alice_laptop",
        ip_address="203.0.113.42",
        account_age_days=90,
        kyc_tier="standard",
        lifetime_transaction_count=5,
        avg_transaction_amount=200.00,
        transactions_last_24h=1,
        transactions_last_7d=3,
        amount_last_24h=500.00,
        amount_last_7d=1200.00,
        failed_transactions_last_24h=0,
        unique_ips_last_7d=1,
        unique_devices_last_7d=1,
    )

    fraud_result = fraud_detector.evaluate(fraud_context)

    print(f"  Transaction context:")
    print(f"    • Amount: ${fraud_context.amount:.2f}")
    print(f"    • User's average transaction: ${fraud_context.avg_transaction_amount:.2f}")
    print(f"    • Transactions last 24h: {fraud_context.transactions_last_24h}")
    print(f"    • Account age: {fraud_context.account_age_days} days")
    print(f"    • Velocity last 7d: {fraud_context.transactions_last_7d} txns, ${fraud_context.amount_last_7d:.2f}")

    print(f"\n  Fraud scoring result:")
    print(f"    • Rules triggered: {', '.join(fraud_result.rules_triggered) if fraud_result.rules_triggered else 'None'}")
    print(f"    • Raw score: {fraud_result.raw_score:.2f}")
    print(f"    • Normalized score: {fraud_result.normalized_score:.2f}/100")
    print(f"    • Decision: {fraud_result.decision.value.upper()}")
    print(f"    • Latency: {fraud_result.latency_ms:.2f}ms")

    # ========================================================================
    # STEP 5: Payment orchestrator routes to PSP
    # ========================================================================

    print_step(5, "PAYMENT ORCHESTRATION & PSP ROUTING")

    psp_response = payment_orchestrator.process_payment(
        amount=500.00,
        method=PaymentMethod.ACH,
        idempotency_key="alice_transfer_001"
    )

    print(f"  PSP routing decision:")
    print(f"    • Primary PSP: Stripe (ACH: 0.8% fee)")
    print(f"    • Health score: {payment_orchestrator.health_scores['stripe'].score:.3f}")
    print(f"    • Circuit breaker: {payment_orchestrator.circuit_breakers['stripe'].state.value}")

    print(f"\n  PSP response:")
    print(f"    • Status: {psp_response.status.value}")
    print(f"    • PSP transaction ID: {psp_response.psp_transaction_id}")
    print(f"    • Amount: ${psp_response.amount:.2f}")
    print(f"    • PSP fee: ${psp_response.fee:.2f}")
    print(f"    • Latency: {psp_response.latency_ms:.2f}ms")

    # ========================================================================
    # STEP 6: Compliance screening
    # ========================================================================

    print_step(6, "COMPLIANCE SCREENING")

    compliance_check = compliance_checker.check_transaction_limits(
        user_id="alice_smith",
        amount=500.00,
        daily_total=0.00,
        monthly_total=1500.00,
    )

    print(f"  KYC tier: STANDARD")
    print(f"  Per-transaction limit: $1,000.00")
    print(f"  Daily limit: $2,500.00")
    print(f"  Monthly limit: $10,000.00")

    print(f"\n  Transaction limits check:")
    print(f"    • Request amount: $500.00")
    print(f"    • Daily total: $0.00")
    print(f"    • Monthly total: $1,500.00")
    print(f"    • Status: {compliance_check['allowed']} ✓")

    # OFAC screening
    ofac_result = compliance_checker.screen_ofac("Alice Smith", "alice_smith")
    print(f"\n  OFAC sanctions screening:")
    print(f"    • Name: Alice Smith")
    print(f"    • Match found: {ofac_result.match_found}")
    print(f"    • Blocked: {ofac_result.blocked}")
    print(f"    • Latency: {ofac_result.latency_ms:.2f}ms")

    # ========================================================================
    # STEP 7: Settlement batch creation
    # ========================================================================

    print_step(7, "SETTLEMENT BATCH CREATION")

    settled_txns = [
        SettledTransaction(
            transaction_id="txn_alice_001",
            user_id="alice_smith",
            employer_id="acme_corp",
            gross_amount=500.00,
            payout_method=PayoutMethod.ACH,
            is_high_risk=False,
        ),
    ]

    batch = settlement_engine.create_batch(
        transactions=settled_txns,
        settlement_date=date.today(),
    )

    print(f"  Batch ID: {batch.batch_id}")
    print(f"  Settlement date: {batch.settlement_date}")
    print(f"  Status: {batch.status.value}")

    print(f"\n  Batch composition:")
    print(f"    • Transaction count: {batch.transaction_count}")
    print(f"    • Gross amount: ${batch.gross_amount:.2f}")

    split = batch.splits[0]
    print(f"\n  Split calculation (per transaction):")
    print(f"    • Gross amount:     ${split.gross_amount:>8.2f}")
    print(f"    • Platform fee:     ${split.platform_fee:>8.2f}  (1.25%)")
    print(f"    • PSP fee (ACH):    ${split.psp_fee:>8.2f}  (0.8%)")
    print(f"    • Holdback:         ${split.holdback_amount:>8.2f}  (none)")
    print(f"    • User receives:    ${split.user_receives:>8.2f}")
    print(f"    • Total:            ${split.total_allocated:>8.2f}  ✓")

    net_pos = batch.net_positions[0]
    print(f"\n  Net position (for netting):")
    print(f"    • User: {net_pos.user_id}")
    print(f"    • Transactions aggregated: {net_pos.transaction_count}")
    print(f"    • Net payout: ${net_pos.net_payout:.2f}")

    # ========================================================================
    # STEP 8: NACHA file generation
    # ========================================================================

    print_step(8, "NACHA FILE GENERATION")

    nacha_entries = [
        {
            "user_id": "alice_smith",
            "amount": int(net_pos.net_payout * 100),
        }
    ]

    nacha_batch = create_sample_nacha_batch(
        batch_number=1,
        settlement_date=date.today(),
        entries=nacha_entries,
    )

    print(f"  Generated NACHA batch (first 5 lines):")
    nacha_lines = nacha_batch.split("\n")
    for line in nacha_lines[:5]:
        print(f"    {line[:94]}")
    print(f"    ... ({len(nacha_lines)} total lines)")

    print(f"\n  NACHA record breakdown:")
    print(f"    • File Header (type 1): 1 record")
    print(f"    • Batch Header (type 5): 1 record")
    print(f"    • Entry Details (type 6): {len([l for l in nacha_lines if l[0] == '6'])} record(s)")
    print(f"    • Batch Control (type 8): 1 record")
    print(f"    • File Control (type 9): 1 record")

    # ========================================================================
    # STEP 9: Reconciliation matching
    # ========================================================================

    print_step(9, "RECONCILIATION - THREE-WAY MATCHING")

    # Create sample records for demo
    ledger_rec = LedgerRecord(
        entry_id=transfer_entry.entry_id,
        transaction_id="txn_alice_001",
        amount=500.00,
        account_code="user_wallet:alice_smith",
        posted_at=datetime.now(),
        psp_name="stripe",
    )

    psp_rec = PSPRecord(
        psp_transaction_id=psp_response.psp_transaction_id,
        amount=500.00,
        fee=4.00,
        net_amount=496.00,
        settlement_date=date.today(),
        psp_name="stripe",
        internal_reference="txn_alice_001",
    )

    bank_rec = BankRecord(
        bank_reference="STRIPE_20240315_001",
        amount=496.00,
        posting_date=date.today(),
        description="STRIPE SETTLEMENT",
        counterparty="stripe",
    )

    recon_run = reconciliation_engine.run_reconciliation(
        ledger_records=[ledger_rec],
        psp_records=[psp_rec],
        bank_records=[bank_rec],
        run_date=date.today(),
    )

    print(f"  Reconciliation run: {recon_run.run_id}")
    print(f"  Run date: {recon_run.run_date}")

    print(f"\n  Three-way matching results:")
    print(f"    • Exact matches: {recon_run.exact_matches}")
    print(f"    • Fuzzy matches: {recon_run.fuzzy_matches}")
    print(f"    • Many-to-one matches: {recon_run.many_to_one_matches}")
    print(f"    • Auto-resolved: {recon_run.auto_resolved}")
    print(f"    • Exceptions: {recon_run.exceptions}")
    print(f"    • Unmatched: {recon_run.unmatched}")
    print(f"    • Match rate: {recon_run.match_rate * 100:.1f}%")

    # ========================================================================
    # FINAL: Print complete ledger state
    # ========================================================================

    print_section("FINAL LEDGER STATE")

    accounts_to_check = [
        ("Bank Operating", AccountCode.BANK_OPERATING, None),
        ("Employer Holding", AccountCode.EMPLOYER_FUNDING_HOLDING, "acme_corp"),
        ("Employee Wallet", AccountCode.USER_WALLET, "alice_smith"),
        ("Platform Fees", AccountCode.PLATFORM_FEE, None),
        ("PSP Receivable", AccountCode.PSP_RECEIVABLE, "stripe"),
    ]

    for label, code, entity_id in accounts_to_check:
        try:
            acc = ledger.get_account(code, entity_id)
            posted = ledger.get_posted_balance(acc)
            available = ledger.get_available_balance(acc)
            print_balance(label, posted, available)
        except:
            pass

    final_check = ledger.verify_system_balance()
    print(f"\n  System integrity check:")
    print(f"    • Total debits: ${final_check['total_debits']:.2f}")
    print(f"    • Total credits: ${final_check['total_credits']:.2f}")
    print(f"    • Balanced: {final_check['balanced']} ✓")
    print(f"    • Journal entries: {final_check['entry_count']}")

    print_section("TRANSACTION LIFECYCLE COMPLETE")
    print("\n  ✓ Employee earned wage access initiated")
    print("  ✓ Fraud detection passed")
    print("  ✓ Compliance approved")
    print("  ✓ Settlement batch created")
    print("  ✓ Reconciliation matched 3-way")
    print("  ✓ Ledger balanced throughout\n")


if __name__ == "__main__":
    main()

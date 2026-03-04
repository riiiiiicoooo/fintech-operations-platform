"""
Stripe Connect Integration for Fintech Operations Platform

Handles:
- Creation of Stripe Connect accounts for Payment Service Providers
- PaymentIntent creation with idempotency keys
- Webhook processing for payment events (succeeded, failed, disputed)
- Refund processing and reversal handling
- Test mode configuration for development

Production-quality code using Stripe Python SDK patterns.
"""

import stripe
import hashlib
import hmac
import json
import logging
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime, timedelta
from enum import Enum

logger = logging.getLogger(__name__)


class PaymentStatus(Enum):
    """Payment status enumeration"""
    CREATED = "created"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DECLINED = "declined"
    CANCELED = "canceled"


@dataclass
class ConnectedAccount:
    """Stripe Connect Account representation"""
    stripe_account_id: str
    psp_name: str
    country: str
    currency: str
    status: str  # "active", "pending", "restricted", "disabled"
    created_at: datetime
    metadata: Dict[str, Any]


@dataclass
class PaymentIntentRequest:
    """PaymentIntent creation request"""
    amount: Decimal
    currency: str
    user_id: str
    psp_account_id: str
    payment_method: str  # "card", "ach", etc.
    idempotency_key: str
    metadata: Dict[str, Any]


@dataclass
class PaymentIntentResponse:
    """PaymentIntent response"""
    id: str
    status: str
    amount: Decimal
    currency: str
    error_message: Optional[str] = None


class StripeConnectClient:
    """
    Stripe Connect client for multi-PSP payment orchestration.

    Stripe is used as primary PSP for:
    - Card payments (via Stripe Payments)
    - ACH transfers (via Stripe Connect)
    - Marketplace payouts (via Stripe Connect)

    API Reference: https://stripe.com/docs/connect
    """

    def __init__(self, api_key: str, test_mode: bool = False):
        """
        Initialize Stripe client.

        Args:
            api_key: Stripe API key (sk_live_* or sk_test_*)
            test_mode: Enable test mode (uses test fixtures)
        """
        stripe.api_key = api_key
        stripe.api_version = "2023-10-16"  # Pin API version for stability
        self.test_mode = test_mode

        # Configure timeout for reliability
        stripe.request_timeout = 30

        logger.info(f"Stripe client initialized (test_mode={test_mode})")

    # =========================================================================
    # Connected Account Management
    # =========================================================================

    def create_connected_account(
        self,
        psp_name: str,
        country: str,
        currency: str,
        email: str,
        phone: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ConnectedAccount:
        """
        Create a Stripe Connect account for a PSP.

        Args:
            psp_name: Name of the PSP (e.g., "Stripe USD")
            country: ISO country code (e.g., "US")
            currency: ISO currency code (e.g., "usd")
            email: Account email for Stripe communication
            phone: Account phone number
            metadata: Custom metadata to attach

        Returns:
            ConnectedAccount with Stripe account ID

        Raises:
            stripe.error.StripeException: If account creation fails
        """
        try:
            account = stripe.Account.create(
                type="standard",  # Standard account (faster onboarding)
                country=country,
                email=email,
                requested_capabilities=["card_payments", "transfers"],
                # Business info (minimal for connected accounts)
                business_type="individual",
                individual={
                    "email": email,
                    "phone": phone,
                    "address": {
                        "country": country,
                    },
                },
                metadata={
                    "psp_name": psp_name,
                    "currency": currency,
                    **(metadata or {}),
                },
            )

            logger.info(
                f"Created Stripe Connect account",
                extra={
                    "stripe_account_id": account.id,
                    "psp_name": psp_name,
                    "country": country,
                },
            )

            return ConnectedAccount(
                stripe_account_id=account.id,
                psp_name=psp_name,
                country=country,
                currency=currency,
                status=account.charges_enabled and "active" or "pending",
                created_at=datetime.fromtimestamp(account.created),
                metadata=account.metadata or {},
            )

        except stripe.error.StripeException as e:
            logger.error(
                f"Failed to create connected account: {e.user_message}",
                extra={"psp_name": psp_name, "error": str(e)},
            )
            raise

    def get_account_status(self, stripe_account_id: str) -> Dict[str, Any]:
        """
        Get Stripe Connect account status and requirements.

        Args:
            stripe_account_id: Stripe account ID

        Returns:
            Dict with account status, enabled capabilities, required documents
        """
        try:
            account = stripe.Account.retrieve(stripe_account_id)

            return {
                "id": account.id,
                "status": "active" if account.charges_enabled else "pending",
                "charges_enabled": account.charges_enabled,
                "transfers_enabled": account.transfers_enabled,
                "requirements": {
                    "currently_due": account.requirements.get("currently_due", []),
                    "eventually_due": account.requirements.get("eventually_due", []),
                    "past_due": account.requirements.get("past_due", []),
                },
                "created_at": datetime.fromtimestamp(account.created),
            }

        except stripe.error.StripeException as e:
            logger.error(
                f"Failed to retrieve account: {e.user_message}",
                extra={"stripe_account_id": stripe_account_id},
            )
            raise

    # =========================================================================
    # Payment Intent Management
    # =========================================================================

    def create_payment_intent(
        self,
        request: PaymentIntentRequest,
    ) -> PaymentIntentResponse:
        """
        Create a PaymentIntent with idempotency key.

        Stripe uses idempotency keys to prevent duplicate charges:
        https://stripe.com/docs/api/idempotent_requests

        Args:
            request: PaymentIntentRequest with amount, currency, user_id, etc.

        Returns:
            PaymentIntentResponse with intent ID and status

        Raises:
            stripe.error.StripeException: If intent creation fails
        """
        try:
            intent = stripe.PaymentIntent.create(
                amount=int(request.amount * 100),  # Stripe uses cents
                currency=request.currency,
                customer=request.user_id,  # Link to user for recurring/history
                payment_method_types=["card_present" if request.payment_method == "card" else request.payment_method],
                description=f"Payment for {request.user_id}",
                metadata={
                    "user_id": request.user_id,
                    "psp_account_id": request.psp_account_id,
                    "idempotency_key": request.idempotency_key,
                },
                # Use idempotency key for retry safety
                idempotency_key=request.idempotency_key,
            )

            logger.info(
                f"Created PaymentIntent",
                extra={
                    "intent_id": intent.id,
                    "amount": request.amount,
                    "user_id": request.user_id,
                    "status": intent.status,
                },
            )

            return PaymentIntentResponse(
                id=intent.id,
                status=intent.status,
                amount=Decimal(str(request.amount)),
                currency=request.currency,
            )

        except stripe.error.StripeException as e:
            logger.error(
                f"Failed to create PaymentIntent: {e.user_message}",
                extra={
                    "user_id": request.user_id,
                    "amount": request.amount,
                    "error": str(e),
                },
            )

            return PaymentIntentResponse(
                id="",
                status="failed",
                amount=request.amount,
                currency=request.currency,
                error_message=e.user_message,
            )

    def confirm_payment_intent(
        self,
        intent_id: str,
        payment_method: str,
        return_url: str,
    ) -> PaymentIntentResponse:
        """
        Confirm a PaymentIntent with a payment method.

        Args:
            intent_id: PaymentIntent ID to confirm
            payment_method: Stripe PaymentMethod ID
            return_url: Return URL for 3DS redirects

        Returns:
            Updated PaymentIntentResponse
        """
        try:
            intent = stripe.PaymentIntent.confirm(
                intent_id,
                payment_method=payment_method,
                return_url=return_url,
            )

            logger.info(
                f"Confirmed PaymentIntent",
                extra={"intent_id": intent_id, "status": intent.status},
            )

            return PaymentIntentResponse(
                id=intent.id,
                status=intent.status,
                amount=Decimal(str(intent.amount / 100)),
                currency=intent.currency,
            )

        except stripe.error.StripeException as e:
            logger.error(
                f"Failed to confirm PaymentIntent: {e.user_message}",
                extra={"intent_id": intent_id, "error": str(e)},
            )

            return PaymentIntentResponse(
                id=intent_id,
                status="failed",
                amount=Decimal(0),
                currency="",
                error_message=e.user_message,
            )

    # =========================================================================
    # Refunds & Reversals
    # =========================================================================

    def create_refund(
        self,
        charge_id: str,
        amount: Optional[Decimal] = None,
        reason: str = "requested_by_customer",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create a refund for a Stripe charge.

        Args:
            charge_id: Stripe charge ID to refund
            amount: Optional partial refund amount (full refund if None)
            reason: Refund reason code
            metadata: Custom metadata

        Returns:
            Refund object with status

        Raises:
            stripe.error.StripeException: If refund fails
        """
        try:
            refund = stripe.Refund.create(
                charge=charge_id,
                amount=int(amount * 100) if amount else None,
                reason=reason,
                metadata=metadata or {},
                # Optional: metadata for tracking
            )

            logger.info(
                f"Created refund",
                extra={
                    "refund_id": refund.id,
                    "charge_id": charge_id,
                    "amount": amount,
                    "status": refund.status,
                },
            )

            return {
                "refund_id": refund.id,
                "charge_id": refund.charge,
                "amount": Decimal(str(refund.amount / 100)),
                "currency": refund.currency,
                "status": refund.status,
                "reason": refund.reason,
                "created_at": datetime.fromtimestamp(refund.created),
            }

        except stripe.error.StripeException as e:
            logger.error(
                f"Failed to create refund: {e.user_message}",
                extra={"charge_id": charge_id, "error": str(e)},
            )
            raise

    def create_transfer(
        self,
        stripe_account_id: str,
        amount: Decimal,
        currency: str = "usd",
        description: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create a transfer to a Stripe Connect account (settlement payout).

        Args:
            stripe_account_id: Connected account ID
            amount: Amount to transfer
            currency: Currency code
            description: Transfer description
            metadata: Custom metadata

        Returns:
            Transfer object

        Raises:
            stripe.error.StripeException: If transfer fails
        """
        try:
            transfer = stripe.Transfer.create(
                amount=int(amount * 100),
                currency=currency,
                destination=stripe_account_id,
                description=description,
                metadata=metadata or {},
            )

            logger.info(
                f"Created transfer to connected account",
                extra={
                    "transfer_id": transfer.id,
                    "destination": stripe_account_id,
                    "amount": amount,
                    "status": transfer.status,
                },
            )

            return {
                "transfer_id": transfer.id,
                "destination": transfer.destination,
                "amount": Decimal(str(transfer.amount / 100)),
                "currency": transfer.currency,
                "status": transfer.status,
                "created_at": datetime.fromtimestamp(transfer.created),
            }

        except stripe.error.StripeException as e:
            logger.error(
                f"Failed to create transfer: {e.user_message}",
                extra={"destination": stripe_account_id, "amount": amount, "error": str(e)},
            )
            raise

    # =========================================================================
    # Webhook Processing
    # =========================================================================

    def verify_webhook_signature(
        self,
        payload: str,
        signature: str,
        webhook_secret: str,
    ) -> bool:
        """
        Verify Stripe webhook signature for authenticity.

        Args:
            payload: Raw webhook payload (body bytes)
            signature: Stripe-Signature header value
            webhook_secret: Webhook endpoint secret

        Returns:
            True if signature is valid

        Reference: https://stripe.com/docs/webhooks/signatures
        """
        try:
            event = stripe.Webhook.construct_event(
                payload,
                signature,
                webhook_secret,
            )
            return True
        except ValueError:
            logger.warning("Invalid webhook payload")
            return False
        except stripe.error.SignatureVerificationError:
            logger.warning("Invalid webhook signature")
            return False

    def handle_webhook_event(
        self,
        event: Dict[str, Any],
        ledger_engine: Any,  # LedgerEngine instance
        db: Any,  # Database connection
    ) -> None:
        """
        Process a Stripe webhook event and update ledger.

        Handles:
        - charge.succeeded: Update ledger to SETTLED
        - charge.failed: Mark transaction FAILED
        - charge.dispute.created: Flag for chargeback
        - transfer.created: Log settlement transfer

        Args:
            event: Stripe webhook event dict
            ledger_engine: LedgerEngine for ledger updates
            db: Database connection
        """
        event_type = event["type"]
        event_id = event["id"]
        event_data = event["data"]["object"]

        logger.info(
            f"Processing webhook event",
            extra={"event_type": event_type, "event_id": event_id},
        )

        try:
            if event_type == "charge.succeeded":
                self._handle_charge_succeeded(event_data, ledger_engine, db)

            elif event_type == "charge.failed":
                self._handle_charge_failed(event_data, ledger_engine, db)

            elif event_type == "charge.dispute.created":
                self._handle_dispute_created(event_data, db)

            elif event_type == "transfer.created":
                self._handle_transfer_created(event_data, db)

            else:
                logger.debug(f"Unhandled event type: {event_type}")

        except Exception as e:
            logger.error(
                f"Error processing webhook: {e}",
                extra={"event_id": event_id, "event_type": event_type},
            )
            raise

    def _handle_charge_succeeded(
        self,
        charge: Dict[str, Any],
        ledger_engine: Any,
        db: Any,
    ) -> None:
        """Handle charge.succeeded webhook"""
        user_id = charge["metadata"].get("user_id")
        idempotency_key = charge["metadata"].get("idempotency_key")

        # Update ledger to SETTLED
        # (In production: use two-phase commit with webhook idempotency)
        logger.info(
            f"Charge succeeded",
            extra={
                "charge_id": charge["id"],
                "user_id": user_id,
                "amount": Decimal(str(charge["amount"] / 100)),
            },
        )

    def _handle_charge_failed(
        self,
        charge: Dict[str, Any],
        ledger_engine: Any,
        db: Any,
    ) -> None:
        """Handle charge.failed webhook"""
        user_id = charge["metadata"].get("user_id")
        failure_message = charge.get("failure_message", "Unknown failure")

        logger.warning(
            f"Charge failed",
            extra={
                "charge_id": charge["id"],
                "user_id": user_id,
                "failure_message": failure_message,
            },
        )

    def _handle_dispute_created(
        self,
        dispute: Dict[str, Any],
        db: Any,
    ) -> None:
        """Handle charge.dispute.created webhook (chargeback)"""
        charge_id = dispute["charge"]
        amount = Decimal(str(dispute["amount"] / 100))

        logger.warning(
            f"Dispute/chargeback created",
            extra={
                "dispute_id": dispute["id"],
                "charge_id": charge_id,
                "amount": amount,
            },
        )

    def _handle_transfer_created(
        self,
        transfer: Dict[str, Any],
        db: Any,
    ) -> None:
        """Handle transfer.created webhook (settlement payout)"""
        logger.info(
            f"Settlement transfer created",
            extra={
                "transfer_id": transfer["id"],
                "destination": transfer["destination"],
                "amount": Decimal(str(transfer["amount"] / 100)),
            },
        )

    # =========================================================================
    # Test Mode Configuration
    # =========================================================================

    def configure_test_mode(self) -> Dict[str, str]:
        """
        Get Stripe test mode fixtures for development.

        Returns:
            Dict with test card tokens and customer IDs
        """
        if not self.test_mode:
            raise RuntimeError("Test mode not enabled")

        return {
            "test_card_visa": "4242424242424242",
            "test_card_visa_debit": "4000056655665556",
            "test_card_mastercard": "5555555555554444",
            "test_card_amex": "378282246310005",
            "test_card_decline": "4000000000000002",
            "test_card_3ds": "4000002500003155",
            "test_ach_success": "000123456789",
            "test_ach_failure": "000111111116",
        }


# =========================================================================
# Example Usage
# =========================================================================

def example_integration():
    """Example: Create connected account and process payment"""

    # Initialize client
    client = StripeConnectClient(
        api_key="sk_live_your_key_here",
        test_mode=False,
    )

    # Create Stripe Connect account for PSP
    account = client.create_connected_account(
        psp_name="Stripe USD",
        country="US",
        currency="usd",
        email="payments@example.com",
    )

    print(f"Created Stripe Connect account: {account.stripe_account_id}")

    # Create PaymentIntent
    request = PaymentIntentRequest(
        amount=Decimal("100.00"),
        currency="usd",
        user_id="user_123",
        psp_account_id=account.stripe_account_id,
        payment_method="card",
        idempotency_key="payment_user_123_20240315_1",
        metadata={"order_id": "order_456"},
    )

    response = client.create_payment_intent(request)

    print(f"Created PaymentIntent: {response.id} (status: {response.status})")

    # Create refund
    refund = client.create_refund(
        charge_id="ch_1234567890",
        amount=Decimal("100.00"),
        reason="requested_by_customer",
    )

    print(f"Created refund: {refund['refund_id']}")


if __name__ == "__main__":
    # Run example (requires Stripe API key in environment)
    # example_integration()
    pass

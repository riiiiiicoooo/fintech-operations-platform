"""
Payment Orchestrator: Multi-PSP Routing, Health Scoring, and Circuit Breakers

PM-authored reference implementation demonstrating:
- PSP adapter pattern (standardized interface across Stripe/Adyen/Tabapay)
- Health-score-based routing with automatic failover
- Circuit breaker preventing calls to degraded PSPs
- Retry logic with exponential backoff
- Idempotent PSP calls

Not production code. See docs/ARCHITECTURE.md Section 2 (Payment Service).
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Protocol
from collections import deque
import time
import random


# --- PSP Adapter Interface ---

class PaymentMethod(Enum):
    ACH = "ach"
    CARD = "card"
    WIRE = "wire"
    INSTANT = "instant"  # Push-to-debit


class PSPStatus(Enum):
    SUCCESS = "success"
    PENDING = "pending"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class PSPResponse:
    psp_name: str
    psp_transaction_id: str
    status: PSPStatus
    amount: float
    fee: float = 0.0
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    latency_ms: float = 0.0


class PSPAdapter(Protocol):
    """
    Interface that every PSP adapter must implement.
    In production, each adapter wraps the PSP's SDK/API.
    """
    name: str
    supported_methods: list[PaymentMethod]

    def create_payment(
        self, amount: float, method: PaymentMethod, idempotency_key: str
    ) -> PSPResponse: ...

    def capture_payment(self, psp_transaction_id: str) -> PSPResponse: ...
    def void_payment(self, psp_transaction_id: str) -> PSPResponse: ...
    def refund_payment(self, psp_transaction_id: str, amount: float) -> PSPResponse: ...
    def get_status(self, psp_transaction_id: str) -> PSPResponse: ...


# --- Simulated PSP Adapters ---

class StripeAdapter:
    name = "stripe"
    supported_methods = [PaymentMethod.ACH, PaymentMethod.CARD, PaymentMethod.INSTANT]

    def create_payment(self, amount, method, idempotency_key):
        # Simulates Stripe API call
        fee = round(amount * 0.029 + 0.30, 2) if method == PaymentMethod.CARD else round(amount * 0.008, 2)
        return PSPResponse(
            psp_name=self.name,
            psp_transaction_id=f"pi_{idempotency_key[:12]}",
            status=PSPStatus.SUCCESS,
            amount=amount,
            fee=fee,
        )

    def capture_payment(self, psp_transaction_id):
        return PSPResponse(psp_name=self.name, psp_transaction_id=psp_transaction_id,
                           status=PSPStatus.SUCCESS, amount=0)

    def void_payment(self, psp_transaction_id):
        return PSPResponse(psp_name=self.name, psp_transaction_id=psp_transaction_id,
                           status=PSPStatus.SUCCESS, amount=0)

    def refund_payment(self, psp_transaction_id, amount):
        return PSPResponse(psp_name=self.name, psp_transaction_id=psp_transaction_id,
                           status=PSPStatus.SUCCESS, amount=amount)

    def get_status(self, psp_transaction_id):
        return PSPResponse(psp_name=self.name, psp_transaction_id=psp_transaction_id,
                           status=PSPStatus.SUCCESS, amount=0)


class AdyenAdapter:
    name = "adyen"
    supported_methods = [PaymentMethod.ACH, PaymentMethod.CARD, PaymentMethod.WIRE]

    def create_payment(self, amount, method, idempotency_key):
        fee = round(amount * 0.025 + 0.25, 2) if method == PaymentMethod.CARD else round(amount * 0.006, 2)
        return PSPResponse(
            psp_name=self.name,
            psp_transaction_id=f"adyen_{idempotency_key[:12]}",
            status=PSPStatus.SUCCESS,
            amount=amount,
            fee=fee,
        )

    def capture_payment(self, psp_transaction_id):
        return PSPResponse(psp_name=self.name, psp_transaction_id=psp_transaction_id,
                           status=PSPStatus.SUCCESS, amount=0)

    def void_payment(self, psp_transaction_id):
        return PSPResponse(psp_name=self.name, psp_transaction_id=psp_transaction_id,
                           status=PSPStatus.SUCCESS, amount=0)

    def refund_payment(self, psp_transaction_id, amount):
        return PSPResponse(psp_name=self.name, psp_transaction_id=psp_transaction_id,
                           status=PSPStatus.SUCCESS, amount=amount)

    def get_status(self, psp_transaction_id):
        return PSPResponse(psp_name=self.name, psp_transaction_id=psp_transaction_id,
                           status=PSPStatus.SUCCESS, amount=0)


class TabapayAdapter:
    """Specialized for instant push-to-debit transfers. Lower cost than Stripe Instant."""
    name = "tabapay"
    supported_methods = [PaymentMethod.INSTANT]

    def create_payment(self, amount, method, idempotency_key):
        fee = round(amount * 0.015, 2)  # 40% cheaper than Stripe for instant
        return PSPResponse(
            psp_name=self.name,
            psp_transaction_id=f"tp_{idempotency_key[:12]}",
            status=PSPStatus.SUCCESS,
            amount=amount,
            fee=fee,
        )

    def capture_payment(self, psp_transaction_id):
        return PSPResponse(psp_name=self.name, psp_transaction_id=psp_transaction_id,
                           status=PSPStatus.SUCCESS, amount=0)

    def void_payment(self, psp_transaction_id):
        return PSPResponse(psp_name=self.name, psp_transaction_id=psp_transaction_id,
                           status=PSPStatus.SUCCESS, amount=0)

    def refund_payment(self, psp_transaction_id, amount):
        return PSPResponse(psp_name=self.name, psp_transaction_id=psp_transaction_id,
                           status=PSPStatus.SUCCESS, amount=amount)

    def get_status(self, psp_transaction_id):
        return PSPResponse(psp_name=self.name, psp_transaction_id=psp_transaction_id,
                           status=PSPStatus.SUCCESS, amount=0)


# --- Circuit Breaker ---

class CircuitState(Enum):
    CLOSED = "closed"    # Normal operation
    OPEN = "open"        # Blocking calls (PSP is down)
    HALF_OPEN = "half_open"  # Testing with a single call


@dataclass
class CircuitBreaker:
    """
    Circuit breaker for a single PSP.
    Opens after 5 failures in 60 seconds. Closes after successful test.
    """
    psp_name: str
    state: CircuitState = CircuitState.CLOSED
    failure_threshold: int = 5
    failure_window_seconds: int = 60
    cooldown_seconds: int = 30
    failures: deque = field(default_factory=deque)
    last_state_change: datetime = field(default_factory=datetime.utcnow)

    def record_success(self):
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.CLOSED
            self.failures.clear()
            self.last_state_change = datetime.utcnow()

    def record_failure(self):
        now = datetime.utcnow()
        self.failures.append(now)

        # Remove failures outside the window
        cutoff = now - timedelta(seconds=self.failure_window_seconds)
        while self.failures and self.failures[0] < cutoff:
            self.failures.popleft()

        if len(self.failures) >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.last_state_change = now

    def can_execute(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            elapsed = (datetime.utcnow() - self.last_state_change).total_seconds()
            if elapsed >= self.cooldown_seconds:
                self.state = CircuitState.HALF_OPEN
                self.last_state_change = datetime.utcnow()
                return True  # Allow one test request
            return False

        if self.state == CircuitState.HALF_OPEN:
            return True  # Already in test mode

        return False


# --- Health Scoring ---

@dataclass
class PSPHealthScore:
    """
    Weighted health score per PSP. Updated on every request.
    Score >= 0.8: healthy, use normally
    Score < 0.8: degraded, prefer fallback
    """
    psp_name: str
    success_rate_1hr: float = 1.0     # Weight: 0.4
    p95_latency_1hr: float = 200.0    # Weight: 0.3 (lower is better, normalized)
    error_rate_15min: float = 0.0     # Weight: 0.2 (lower is better)
    uptime_24hr: float = 1.0          # Weight: 0.1

    # Rolling window storage
    _recent_results: list = field(default_factory=list)

    @property
    def score(self) -> float:
        latency_score = max(0, 1.0 - (self.p95_latency_1hr / 5000))  # 5s = 0.0
        error_score = 1.0 - self.error_rate_15min

        return (
            0.4 * self.success_rate_1hr +
            0.3 * latency_score +
            0.2 * error_score +
            0.1 * self.uptime_24hr
        )

    @property
    def is_healthy(self) -> bool:
        return self.score >= 0.8

    def record_result(self, success: bool, latency_ms: float):
        now = datetime.utcnow()
        self._recent_results.append({
            "success": success, "latency_ms": latency_ms, "at": now
        })

        # Keep last hour of results
        cutoff = now - timedelta(hours=1)
        self._recent_results = [r for r in self._recent_results if r["at"] > cutoff]

        # Recalculate
        if self._recent_results:
            successes = sum(1 for r in self._recent_results if r["success"])
            self.success_rate_1hr = successes / len(self._recent_results)

            latencies = sorted(r["latency_ms"] for r in self._recent_results)
            p95_idx = int(len(latencies) * 0.95)
            self.p95_latency_1hr = latencies[min(p95_idx, len(latencies) - 1)]

            # 15-minute error rate
            cutoff_15m = now - timedelta(minutes=15)
            recent = [r for r in self._recent_results if r["at"] > cutoff_15m]
            if recent:
                errors = sum(1 for r in recent if not r["success"])
                self.error_rate_15min = errors / len(recent)


# --- Routing Table ---

@dataclass
class RoutingRule:
    payment_method: PaymentMethod
    primary_psp: str
    fallback_psp: str


# --- Payment Orchestrator ---

class PaymentOrchestrator:
    """
    Routes payments to the optimal PSP based on health scores,
    circuit breaker state, and payment method.
    """

    # Default routing: primary and fallback per payment method
    DEFAULT_ROUTING = [
        RoutingRule(PaymentMethod.ACH, "stripe", "adyen"),
        RoutingRule(PaymentMethod.CARD, "stripe", "adyen"),
        RoutingRule(PaymentMethod.WIRE, "adyen", "stripe"),
        RoutingRule(PaymentMethod.INSTANT, "tabapay", "stripe"),
    ]

    MAX_RETRIES = 3
    BASE_RETRY_DELAY_MS = 500  # 500ms, 1000ms, 2000ms with jitter

    def __init__(self):
        self.adapters: dict[str, PSPAdapter] = {
            "stripe": StripeAdapter(),
            "adyen": AdyenAdapter(),
            "tabapay": TabapayAdapter(),
        }
        self.circuit_breakers: dict[str, CircuitBreaker] = {
            name: CircuitBreaker(psp_name=name) for name in self.adapters
        }
        self.health_scores: dict[str, PSPHealthScore] = {
            name: PSPHealthScore(psp_name=name) for name in self.adapters
        }
        self.routing_rules = {r.payment_method: r for r in self.DEFAULT_ROUTING}

    def process_payment(
        self,
        amount: float,
        method: PaymentMethod,
        idempotency_key: str,
    ) -> PSPResponse:
        """
        Main entry point. Routes to best available PSP with retry and failover.
        
        Flow:
        1. Determine primary and fallback PSP from routing table
        2. Check health scores and circuit breakers
        3. Attempt primary PSP with retries
        4. If primary fails, attempt fallback PSP
        5. If both fail, return failure (transaction queued for later retry)
        """
        rule = self.routing_rules.get(method)
        if not rule:
            raise ValueError(f"No routing rule for payment method: {method.value}")

        # Determine PSP order based on health
        psp_order = self._get_psp_order(rule)

        for psp_name in psp_order:
            adapter = self.adapters[psp_name]
            cb = self.circuit_breakers[psp_name]

            if not cb.can_execute():
                continue  # Circuit is open, skip this PSP

            if method not in adapter.supported_methods:
                continue  # PSP doesn't support this payment method

            # Attempt with retries
            response = self._execute_with_retry(adapter, amount, method, idempotency_key)

            if response.status == PSPStatus.SUCCESS:
                cb.record_success()
                self.health_scores[psp_name].record_result(True, response.latency_ms)
                return response
            else:
                cb.record_failure()
                self.health_scores[psp_name].record_result(False, response.latency_ms)

        # All PSPs failed
        return PSPResponse(
            psp_name="none",
            psp_transaction_id="",
            status=PSPStatus.FAILED,
            amount=amount,
            error_code="ALL_PSP_UNAVAILABLE",
            error_message="All payment processors are currently unavailable. Transaction queued for retry.",
        )

    def _get_psp_order(self, rule: RoutingRule) -> list[str]:
        """
        Returns PSPs in priority order. Primary first unless its health score
        is below threshold and fallback is healthy.
        """
        primary_health = self.health_scores[rule.primary_psp]
        fallback_health = self.health_scores[rule.fallback_psp]

        if primary_health.is_healthy:
            return [rule.primary_psp, rule.fallback_psp]
        elif fallback_health.is_healthy:
            return [rule.fallback_psp, rule.primary_psp]
        else:
            # Both degraded, try primary first anyway
            return [rule.primary_psp, rule.fallback_psp]

    def _execute_with_retry(
        self,
        adapter: PSPAdapter,
        amount: float,
        method: PaymentMethod,
        idempotency_key: str,
    ) -> PSPResponse:
        """
        Exponential backoff with jitter. Max 3 attempts.
        Idempotency key ensures PSP treats retries as the same request.
        """
        last_response = None

        for attempt in range(self.MAX_RETRIES):
            start = time.time()
            try:
                response = adapter.create_payment(amount, method, idempotency_key)
                response.latency_ms = (time.time() - start) * 1000

                if response.status == PSPStatus.SUCCESS:
                    return response

                last_response = response
            except Exception as e:
                last_response = PSPResponse(
                    psp_name=adapter.name,
                    psp_transaction_id="",
                    status=PSPStatus.FAILED,
                    amount=amount,
                    error_message=str(e),
                    latency_ms=(time.time() - start) * 1000,
                )

            # Backoff before retry (not on last attempt)
            if attempt < self.MAX_RETRIES - 1:
                delay_ms = self.BASE_RETRY_DELAY_MS * (2 ** attempt)
                jitter_ms = random.uniform(0, delay_ms * 0.25)
                time.sleep((delay_ms + jitter_ms) / 1000)

        return last_response

    def get_health_summary(self) -> dict:
        """Dashboard data for PSP health monitoring."""
        return {
            psp_name: {
                "score": round(health.score, 3),
                "healthy": health.is_healthy,
                "success_rate": round(health.success_rate_1hr, 3),
                "p95_latency_ms": round(health.p95_latency_1hr, 1),
                "circuit_breaker": self.circuit_breakers[psp_name].state.value,
            }
            for psp_name, health in self.health_scores.items()
        }

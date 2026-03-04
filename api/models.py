"""
Pydantic models for FastAPI request/response serialization.
"""

from pydantic import BaseModel, Field, validator
from datetime import datetime, date
from typing import Optional, List
from decimal import Decimal


# ============================================================================
# Transaction Endpoints
# ============================================================================

class TransactionRequest(BaseModel):
    """POST /transactions request body."""
    user_id: str
    amount: float = Field(gt=0)
    payment_method: str  # "ach", "card", "instant", "wire"
    destination_account: str
    idempotency_key: str  # Client-provided for deduplication

    @validator("amount")
    def amount_positive(cls, v):
        if v <= 0:
            raise ValueError("Amount must be positive")
        return round(v, 2)

    class Config:
        schema_extra = {
            "example": {
                "user_id": "user_8472",
                "amount": 500.00,
                "payment_method": "ach",
                "destination_account": "user_bank_account_123",
                "idempotency_key": "txn_user8472_20240315_001",
            }
        }


class TransactionResponse(BaseModel):
    """Transaction response (from POST /transactions)."""
    transaction_id: str
    status: str  # "pending", "processing", "settled", "failed"
    amount: float
    user_id: str
    fraud_score: Optional[float] = None
    fraud_decision: Optional[str] = None
    created_at: datetime

    class Config:
        schema_extra = {
            "example": {
                "transaction_id": "txn_abc123",
                "status": "pending",
                "amount": 500.00,
                "user_id": "user_8472",
                "fraud_score": 12,
                "fraud_decision": "approve",
                "created_at": "2024-03-15T14:30:00Z",
            }
        }


# ============================================================================
# Account Balance Endpoints
# ============================================================================

class AccountBalance(BaseModel):
    """GET /accounts/{id}/balance response."""
    account_id: str
    user_id: Optional[str] = None
    posted_balance: float
    available_balance: float
    currency: str = "USD"
    as_of: datetime
    holds_total: float = 0.0

    class Config:
        schema_extra = {
            "example": {
                "account_id": "user_wallet:user_8472",
                "user_id": "user_8472",
                "posted_balance": 1475.00,
                "available_balance": 975.00,
                "currency": "USD",
                "as_of": "2024-03-15T14:30:00Z",
                "holds_total": 500.00,
            }
        }


class BalanceHistory(BaseModel):
    """Historical balance entry."""
    timestamp: datetime
    posted_balance: float
    available_balance: float
    transaction_id: Optional[str] = None


class BalanceHistoryResponse(BaseModel):
    """GET /accounts/{id}/balance?history=true response."""
    account_id: str
    current_balance: float
    history: List[BalanceHistory]


# ============================================================================
# Reconciliation Endpoints
# ============================================================================

class ReconciliationRunRequest(BaseModel):
    """POST /reconciliation/run request body."""
    run_date: Optional[date] = None  # Defaults to today

    class Config:
        schema_extra = {
            "example": {
                "run_date": "2024-03-15"
            }
        }


class ReconciliationMatch(BaseModel):
    """Match result in reconciliation report."""
    match_id: str
    status: str  # "exact_match", "fuzzy_match", "many_to_one", "auto_resolved", "exception"
    delta_amount: float = 0.0
    resolution_notes: Optional[str] = None


class ReconciliationException(BaseModel):
    """Exception requiring manual review."""
    exception_id: str
    priority: str  # "critical", "high", "medium", "low"
    delta_amount: float
    description: str
    assigned_to: Optional[str] = None


class ReconciliationRunResponse(BaseModel):
    """POST /reconciliation/run response."""
    run_id: str
    run_date: date
    match_rate: str  # "98.5%"
    exact_matches: int
    fuzzy_matches: int
    many_to_one_matches: int
    auto_resolved: int
    exceptions: int
    unmatched: int
    exception_list: List[ReconciliationException] = []
    duration_seconds: Optional[float] = None

    class Config:
        schema_extra = {
            "example": {
                "run_id": "RECON-20240315-abc123",
                "run_date": "2024-03-15",
                "match_rate": "98.2%",
                "exact_matches": 985,
                "fuzzy_matches": 12,
                "many_to_one_matches": 3,
                "auto_resolved": 5,
                "exceptions": 2,
                "unmatched": 0,
                "exception_list": [],
                "duration_seconds": 42.5,
            }
        }


# ============================================================================
# Compliance Screening Endpoints
# ============================================================================

class ComplianceScreeningResult(BaseModel):
    """GET /compliance/screening/{transaction_id} response."""
    transaction_id: str
    user_id: str
    kyc_tier: str  # "basic", "standard", "enhanced"
    kyc_approved: bool
    daily_limit: float
    daily_used: float
    monthly_limit: float
    monthly_used: float
    ofac_screened: bool
    ofac_match_found: bool
    ofac_match_score: Optional[float] = None
    transaction_allowed: bool

    class Config:
        schema_extra = {
            "example": {
                "transaction_id": "txn_abc123",
                "user_id": "user_8472",
                "kyc_tier": "standard",
                "kyc_approved": True,
                "daily_limit": 2500.0,
                "daily_used": 1200.0,
                "monthly_limit": 10000.0,
                "monthly_used": 5500.0,
                "ofac_screened": True,
                "ofac_match_found": False,
                "ofac_match_score": None,
                "transaction_allowed": True,
            }
        }


# ============================================================================
# Audit & Reporting Endpoints
# ============================================================================

class AuditTransaction(BaseModel):
    """Transaction entry in audit log."""
    transaction_id: str
    user_id: str
    amount: float
    status: str  # "pending", "processing", "settled", "failed"
    fraud_decision: Optional[str] = None
    posted_at: datetime


class AuditLogResponse(BaseModel):
    """GET /audit/transactions response."""
    total_count: int
    transactions: List[AuditTransaction]
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    status_filter: Optional[str] = None


# ============================================================================
# Settlement Endpoints
# ============================================================================

class SettlementBatch(BaseModel):
    """Settlement batch info."""
    batch_id: str
    status: str  # "created", "submitted", "confirmed", "failed"
    settlement_date: date
    transaction_count: int
    gross_amount: float
    platform_fees: float
    psp_fees: float
    holdback: float
    net_payout: float
    unique_users: int
    created_at: datetime
    submitted_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None


# ============================================================================
# Health Endpoints
# ============================================================================

class HealthCheck(BaseModel):
    """GET /health response."""
    status: str  # "healthy", "degraded", "unhealthy"
    version: str
    database: str
    cache: Optional[str] = None
    message: str


# ============================================================================
# Error Responses
# ============================================================================

class ErrorResponse(BaseModel):
    """Standard error response."""
    error_code: str
    message: str
    details: Optional[dict] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        schema_extra = {
            "example": {
                "error_code": "INSUFFICIENT_BALANCE",
                "message": "Available balance insufficient for transaction",
                "details": {
                    "requested": 500.0,
                    "available": 300.0,
                },
                "timestamp": "2024-03-15T14:30:00Z",
            }
        }

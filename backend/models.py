from pydantic import BaseModel
from typing import Optional
from datetime import datetime


# Constant strings (Python 3.11 style without Literal import friction)
SRC_RULE = "rule"
SRC_HEURISTIC = "heuristic"
SRC_LLM = "llm"
SRC_HUMAN = "human"
SRC_BOUNDS = "bounds"
SRC_SIM = "simulation"


class PaymentEvent(BaseModel):
    transaction_id: str
    merchant_id: str
    customer_id: str
    amount: int
    currency: str = "INR"
    payment_method: str
    bank: str
    error_code: str
    reason: Optional[str] = None  # Razorpay-style human failure reason string
    timestamp: str
    retry_count: int = 0
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    phone: Optional[str] = None
    merchant_name: Optional[str] = None
    is_subscription: bool = False


class ClassificationResult(BaseModel):
    transaction_id: str
    predicted_reason: str
    confidence: float
    explanation: str
    source: str = SRC_RULE
    raw_code: Optional[str] = None
    triage_message: Optional[str] = None  # LLM/heuristic-drafted recovery message for ambiguous cases
    rules_disposed: bool = False  # True when the deterministic gate overruled the AI proposal


class RetryDecision(BaseModel):
    transaction_id: str
    category: str
    should_retry: bool
    retry_timing: str
    retry_channel: str
    max_attempts: int
    next_retry_at: Optional[str] = None
    attempt_number: int = 1
    routing: str = "auto"  # auto | review
    reasoning: str
    source: str = SRC_RULE


class RecoveryMessage(BaseModel):
    transaction_id: str
    message: str
    channel: str
    subject: Optional[str] = None
    payment_link_id: Optional[str] = None  # Razorpay Payment Link id (real or offline mock)
    payment_link_url: Optional[str] = None
    payment_link_source: Optional[str] = None  # razorpay | offline


class RetryAttempt(BaseModel):
    transaction_id: str
    attempt_number: int
    scheduled_for: str
    executed_at: Optional[str] = None
    recovered: bool = False
    executed: bool = False
    source: str = SRC_SIM


class ReviewItem(BaseModel):
    id: str
    transaction_id: str
    amount: int
    merchant_id: str
    category: str
    reason: str
    status: str = "pending"  # pending | approved | dismissed
    created_at: Optional[str] = None
    decided_at: Optional[str] = None
    decided_by: Optional[str] = None


class MerchantConfig(BaseModel):
    merchant_id: str
    enabled: bool = True
    max_attempts_override: Optional[int] = None
    message_channel: str = "whatsapp"
    auto_retry_risk: bool = False  # when True, risk items skip review (dangerous; default off)
    sensitivity: str = "balanced"  # aggressive | balanced | conservative


class DashboardStats(BaseModel):
    total_failed_value: int
    total_recovered_value: int
    total_failed_count: int
    total_recovered_count: int
    recovery_rate: float
    by_reason: dict
    by_method: dict
    by_source: dict
    recovery_timeline: list
    retry_pipeline: dict
    review_remaining: int
    comparison: dict
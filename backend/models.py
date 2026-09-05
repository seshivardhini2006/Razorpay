from pydantic import BaseModel, Field
from enum import Enum
from datetime import datetime
from typing import Optional


class FailureReason(str, Enum):
    INSUFFICIENT_FUNDS = "insufficient_funds"
    BANK_SERVER_DOWNTIME = "bank_server_downtime"
    OTP_TIMEOUT = "otp_timeout"
    EXPIRED_CARD = "expired_card"
    WRONG_CVV_PIN = "wrong_cvv_pin"
    NETWORK_DROP = "network_drop"
    RISK_FRAUD_BLOCK = "risk_fraud_block"
    UNKNOWN = "unknown"


class PaymentMethod(str, Enum):
    UPI = "upi"
    CARD = "card"
    NETBANKING = "netbanking"
    WALLET = "wallet"


class RetryTiming(str, Enum):
    IMMEDIATE = "immediate"
    SHORT_DELAY = "short_delay"
    SCHEDULED = "scheduled"
    NO_RETRY = "no_retry"


class PaymentEvent(BaseModel):
    transaction_id: str
    merchant_id: str
    customer_id: str
    amount: int
    currency: str = "INR"
    payment_method: str
    bank: str
    error_code: str
    timestamp: str
    retry_count: int = 0
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    merchant_name: Optional[str] = None
    is_subscription: bool = False


class ClassificationResult(BaseModel):
    transaction_id: str
    predicted_reason: str
    confidence: float
    explanation: str


class RetryDecision(BaseModel):
    transaction_id: str
    should_retry: bool
    retry_timing: str
    retry_channel: str
    max_attempts: int
    next_retry_at: Optional[str] = None
    reasoning: str


class RecoveryMessage(BaseModel):
    transaction_id: str
    message: str
    channel: str
    subject: Optional[str] = None


class TransactionOutcome(BaseModel):
    transaction_id: str
    recovered: bool
    attempt_number: int
    recovered_at: Optional[str] = None


class DashboardStats(BaseModel):
    total_failed_value: int
    total_recovered_value: int
    recovery_rate: float
    by_reason: dict
    by_method: dict
    recovery_timeline: list
    comparison: dict

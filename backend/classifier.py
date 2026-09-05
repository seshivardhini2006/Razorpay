"""Failure reason classification: rules-first, explainable, with confidence."""

from datetime import datetime, timedelta

from models import PaymentEvent, ClassificationResult

# Error code -> FailureReason mapping (primary layer)
ERROR_CODE_MAP = {
    "INSUFFICIENT_FUNDS": "insufficient_funds",
    "INSUFFICIENT_BALANCE": "insufficient_funds",
    "LOW_BALANCE": "insufficient_funds",
    "BANK_TIMEOUT": "bank_server_downtime",
    "GATEWAY_ERROR": "bank_server_downtime",
    "HOST_TIME_OUT": "bank_server_downtime",
    "BANK_UNAVAILABLE": "bank_server_downtime",
    "BANK_DOWN": "bank_server_downtime",
    "OTP_EXPIRED": "otp_timeout",
    "OTP_TIMEOUT": "otp_timeout",
    "CARD_EXPIRED": "expired_card",
    "EXPIRED_CARD": "expired_card",
    "CARD_EXPIRED_DATE": "expired_card",
    "INVALID_CVV": "wrong_cvv_pin",
    "INVALID_PIN": "wrong_cvv_pin",
    "WRONG_CVV": "wrong_cvv_pin",
    "INVALID_OTP": "wrong_cvv_pin",
    "CONNECTION_ERROR": "network_drop",
    "CONNECTION_TIMEOUT": "network_drop",
    "NETWORK_ERROR": "network_drop",
    "RISK_BLOCKED": "risk_fraud_block",
    "FRAUD_BLOCK": "risk_fraud_block",
    "TECHNICAL_DECLINE": "risk_fraud_block",
    "BLOCKED_CARD": "risk_fraud_block",
}

# Substring-based fallbacks for ambiguous/unknown codes (secondary layer)
SUBSTRING_RULES = [
    ("FUNDS", "insufficient_funds", 0.8),
    ("BALANCE", "insufficient_funds", 0.8),
    ("TIMEOUT", "bank_server_downtime", 0.7),
    ("UNAVAILABLE", "bank_server_downtime", 0.7),
    ("DOWN", "bank_server_downtime", 0.7),
    ("OTP", "otp_timeout", 0.6),
    ("EXPIRED", "expired_card", 0.7),
    ("INVALID_C", "wrong_cvv_pin", 0.8),
    ("INVALID", "wrong_cvv_pin", 0.6),
    ("CONNECTION", "network_drop", 0.8),
    ("NETWORK", "network_drop", 0.8),
    ("RISK", "risk_fraud_block", 0.8),
    ("DECLINE", "risk_fraud_block", 0.6),
    ("BLOCK", "risk_fraud_block", 0.7),
]

REASON_LABELS = {
    "insufficient_funds": "Insufficient Funds",
    "bank_server_downtime": "Bank Server Downtime",
    "otp_timeout": "OTP Timeout",
    "expired_card": "Expired Card",
    "wrong_cvv_pin": "Wrong CVV/PIN",
    "network_drop": "Network Drop",
    "risk_fraud_block": "Risk / Fraud Block",
    "unknown": "Unknown",
}


def classify(event: PaymentEvent) -> ClassificationResult:
    """Classify a failure event. Rules-first, then substring fallback."""
    code = event.error_code.upper()
    txn = event.transaction_id

    # Layer 1: exact error code mapping
    if code in ERROR_CODE_MAP:
        reason = ERROR_CODE_MAP[code]
        return ClassificationResult(
            transaction_id=txn,
            predicted_reason=reason,
            confidence=0.95,
            explanation=(
                f"Error code {code} is a known, documented failure code "
                "that maps directly to this failure category."
            ),
        )

    # Layer 2: substring fallback for ambiguous codes
    for term, reason, conf in SUBSTRING_RULES:
        if term in code:
            return ClassificationResult(
                transaction_id=txn,
                predicted_reason=reason,
                confidence=conf,
                explanation=(
                    f"Error code {code} was not an exact match but contains "
                    f"'{term}', a strong signal for {REASON_LABELS[reason].lower()}."
                ),
            )

    # Layer 3: fallback with lower confidence
    # Heuristic based on payment method
    if event.payment_method == "UPI" and "OTP" in event.error_code:
        return ClassificationResult(
            transaction_id=txn,
            predicted_reason="otp_timeout",
            confidence=0.6,
            explanation="UPI failure with OTP-related code is most likely an OTP timeout.",
        )

    return ClassificationResult(
        transaction_id=txn,
        predicted_reason="unknown",
        confidence=0.3,
        explanation=(
            f"Error code {code} could not be confidently mapped to a known failure "
            "reason. Conservative classification; no automatic retry will be scheduled."
        ),
    )


def get_reason_label(reason: str) -> str:
    return REASON_LABELS.get(reason, reason)

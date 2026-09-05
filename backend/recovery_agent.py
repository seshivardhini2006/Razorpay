"""Customer Communication Agent: generates plain-language, personalized recovery messages.

Uses reason-aware templates — the message explains *why* the payment failed and
suggests the right fix, adapted to whether it was a purchase or a subscription
renewal. No third-party API required.
"""

from models import PaymentEvent, RecoveryMessage, ClassificationResult, RetryDecision

# Human-friendly failure explanations and suggested fixes per reason
REASON_GUIDANCE = {
    "insufficient_funds": {
        "human_reason": "your bank declined the payment because there weren't enough funds at the moment of debit",
        "fix": "We'll retry automatically in a day or two. If you'd like it sooner, please add funds and we can retry right away.",
    },
    "bank_server_downtime": {
        "human_reason": "your bank's server was briefly unavailable during the transaction",
        "fix": "This is almost always temporary. We'll automatically retry in about 15 minutes — no action needed from you.",
    },
    "otp_timeout": {
        "human_reason": "the one-time password (OTP) wasn't entered before it expired",
        "fix": "Tap the button below to complete your payment with a fresh OTP right now.",
    },
    "expired_card": {
        "human_reason": "the card on file has expired",
        "fix": "Please update your payment method with a new card and we'll charge it immediately.",
    },
    "wrong_cvv_pin": {
        "human_reason": "the card details (CVV or PIN) entered were incorrect",
        "fix": "Re-enter your card details and try again — double-check the 3-digit CVV on the back of your card.",
    },
    "network_drop": {
        "human_reason": "a network interruption occurred while processing your payment",
        "fix": "This is usually momentary. Tap below to securely retry — it typically goes through on the next attempt.",
    },
    "risk_fraud_block": {
        "human_reason": "your bank declined the transaction as a precaution",
        "fix": "Please contact your bank to confirm the transaction, then you can retry.",
    },
    "unknown": {
        "human_reason": "there was an unexpected issue with your payment",
        "fix": "Please try again, or contact support and reference your transaction ID.",
    },
}


def generate_message(
    event: PaymentEvent,
    classification: ClassificationResult,
    decision: RetryDecision,
) -> RecoveryMessage:
    """Generate a reason-aware recovery message for a failed payment."""
    txn = event.transaction_id
    amount_rs = event.amount / 100
    reason = classification.predicted_reason
    guidance = REASON_GUIDANCE.get(reason, REASON_GUIDANCE["unknown"])
    name = event.customer_name or "there"
    merchant = event.merchant_name or "the merchant"

    if event.is_subscription:
        subject = f"Your subscription payment needs attention"
        action = " Tap here to complete securely — your subscription continues seamlessly."
    else:
        subject = f"Complete your payment with {merchant}"
        action = " Tap here to complete securely and get your order on the way."

    # Only append a generic CTA when the fix guidance doesn't already prompt an action
    if any(tok in guidance["fix"] for tok in ("Tap", "update your payment", "contact your bank", "try again", "no action needed")):
        action = ""

    action = action.strip().rstrip(".")
    tail = f"{action}. (Ref: {txn[-6:]})." if action else f"(Ref: {txn[-6:]})."
    body = (
        f"Hi {name}, your payment of Rs {amount_rs:.2f} to {merchant} didn't go through "
        f"because {guidance['human_reason']}. {guidance['fix']} {tail}"
    )
    body = " ".join(body.split())
    return RecoveryMessage(
        transaction_id=txn,
        subject=subject,
        message=body,
        channel="whatsapp",
    )
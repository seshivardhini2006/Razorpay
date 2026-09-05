"""Customer Communication Agent: generates plain-language, personalized recovery messages.

Uses Claude (Anthropic Messages API) when an API key is provided, and falls back to
high-quality templated generation (with the same reason-awareness) when it isn't, so
the demo always works.
"""

import os

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


def build_prompt(event, classification, decision) -> str:
    """Construct a Claude prompt for a personalized recovery message."""
    txn = event.transaction_id
    amount_rs = event.amount / 100
    reason = classification.predicted_reason
    guidance = REASON_GUIDANCE.get(reason, REASON_GUIDANCE["unknown"])

    context_type = "a subscription renewal" if event.is_subscription else "a purchase"

    return f"""You are a payment recovery assistant at a payment gateway. Write a short, warm, plain-language SMS-style message (<140 words) to a customer whose {context_type} payment failed.

CONTEXT:
- Merchant: {event.merchant_name or 'the merchant'}
- Transaction amount: Rs {amount_rs:.2f}
- Why it failed (plain): {guidance['human_reason']}
- Suggested fix: {guidance['fix']}
- Customer name: {event.customer_name or 'Valued customer'}
- Retry strategy chosen: {decision.retry_timing}

REQUIREMENTS:
1. Explain the reason in simple, non-technical language (no error codes).
2. Include the specific suggested fix.
3. Include a clear call to action to complete the payment (a retry/update link).
4. Sound human and reassuring, not scripted. {('Remind them gently that their subscription continues uninterrupted once paid.' if event.is_subscription else 'Keep it short and purchase-oriented.')}
5. Do NOT include sensitive details like full card numbers.

Output only the message text, no preamble."""


def _template_message(event, classification, decision) -> RecoveryMessage:
    """Fallback generator used when no Claude API key is present."""
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


def generate_message(
    event: PaymentEvent,
    classification: ClassificationResult,
    decision: RetryDecision,
) -> RecoveryMessage:
    """Generate a recovery message, using Claude when available."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _template_message(event, classification, decision)

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        prompt = build_prompt(event, classification, decision)
        resp = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        )
        return RecoveryMessage(
            transaction_id=event.transaction_id,
            subject=(
                "Your subscription payment needs attention"
                if event.is_subscription
                else "Complete your payment"
            ),
            message=text.strip(),
            channel="whatsapp",
        )
    except Exception:
        return _template_message(event, classification, decision)

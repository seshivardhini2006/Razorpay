"""Retry decision engine: policy-driven, explainable, cap-enforcing."""

from datetime import datetime, timedelta

from models import PaymentEvent, RetryDecision
from classifier import classify
from policies import POLICIES, GLOBAL_MAX_ATTEMPTS_PER_TRANSACTION

# Build retry timing label for display
TIMING_LABELS = {
    "immediate": "Immediate",
    "short_delay": "Short delay (15 min)",
    "scheduled": "Scheduled (next day)",
    "no_retry": "No retry",
}


def decide(event: PaymentEvent, event_time: datetime) -> RetryDecision:
    """Produce a retry decision for a failure event."""
    result = classify(event)
    policy = POLICIES.get(result.predicted_reason, POLICIES["unknown"])

    should_retry = policy["should_retry"]
    if event.retry_count >= GLOBAL_MAX_ATTEMPTS_PER_TRANSACTION:
        should_retry = False
        reasoning = (
            f"Maximum retry attempts ({GLOBAL_MAX_ATTEMPTS_PER_TRANSACTION}) reached "
            f"for this transaction. Further retries automatically halted to respect "
            "retry caps and avoid customer annoyance."
        )
    else:
        reasoning = policy["reasoning"]

    timing = policy["timing"] if should_retry else "no_retry"
    channel = policy["channel"]

    next_retry_at = None
    if should_retry and policy["cooldown_minutes"] > 0:
        next_retry_at = (
            event_time + timedelta(minutes=policy["cooldown_minutes"])
        ).isoformat()
    elif should_retry:
        next_retry_at = event_time.isoformat()

    return RetryDecision(
        transaction_id=event.transaction_id,
        should_retry=should_retry,
        retry_timing=timing,
        retry_channel=channel,
        max_attempts=min(policy["max_attempts"], GLOBAL_MAX_ATTEMPTS_PER_TRANSACTION),
        next_retry_at=next_retry_at,
        reasoning=reasoning,
    )

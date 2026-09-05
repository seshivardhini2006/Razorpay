"""Retry decision engine.

Picks timing + channel + attempt cap per failure category, consulting:
  - the policy table (backend/policies.py),
  - merchant configuration overrides,
  - hard safety bounds (backend/bounds.py) — enforced at execution, not here.

Clear-cut categories get automatic scheduled retries. Ambiguous / risk / fraud
categories are never auto-retried: they go to the human review queue.
"""

from datetime import datetime, timedelta

from models import (
    PaymentEvent,
    ClassificationResult,
    RetryDecision,
    SRC_RULE,
)
from policies import POLICY_BY_CATEGORY
from bounds import (
    GLOBAL_MAX_AUTOMATIC_ATTEMPTS,
    HARD_NO_RETRY_CATEGORIES,
    REVIEW_REQUIRED_CATEGORIES,
)

IMMEDIATE_OFFSET_MINUTES = 1


def decide(
    event: PaymentEvent,
    classification: ClassificationResult,
    merchant_cfg: dict,
    sim_now: datetime,
) -> RetryDecision:
    category = classification.predicted_reason
    txn = event.transaction_id
    cfg = merchant_cfg or {}

    policy = POLICY_BY_CATEGORY.get(category, POLICY_BY_CATEGORY["ambiguous"])
    attempt_number = event.retry_count + 1

    # ---- sensitivity tuning ----
    sensitivity = cfg.get("sensitivity", "balanced")
    cooldown_min = policy["cooldown_minutes"]
    if sensitivity == "conservative":
        cooldown_min = cooldown_min * 2
    max_attempts = policy["max_attempts"]
    if sensitivity == "conservative":
        max_attempts = min(max_attempts, 2)

    # ---- merchant override on attempts ----
    override = (cfg or {}).get("max_attempts_override")
    if override and override > 0:
        max_attempts = min(int(override), GLOBAL_MAX_AUTOMATIC_ATTEMPTS)

    auto_cap_remaining = GLOBAL_MAX_AUTOMATIC_ATTEMPTS - event.retry_count
    max_attempts = max(1, min(max_attempts, auto_cap_remaining))

    # ---- routing: hard no-retry set always reviews ----
    if category in HARD_NO_RETRY_CATEGORIES:
        auto = bool(cfg.get("auto_retry_risk", False))
        return RetryDecision(
            transaction_id=txn,
            category=category,
            should_retry=auto,
            retry_timing="no_retry" if not auto else policy["timing"],
            retry_channel="none" if not auto else policy["channel"],
            max_attempts=0 if not auto else max_attempts,
            next_retry_at=None,
            attempt_number=attempt_number,
            routing="review" if not auto else "auto",
            reasoning=(
                f"'{category}' is in the hard no-retry set; escalation to human review "
                "even though a risk engine did the blocking. Auto-retry disabled."
                if not auto
                else "Merchant enabled auto-retry for risk category (non-default, risky)."
            ),
            source=SRC_RULE,
        )

    # ---- ambiguous/low-confidence always review ----
    if category in REVIEW_REQUIRED_CATEGORIES:
        return RetryDecision(
            transaction_id=txn,
            category=category,
            should_retry=False,
            retry_timing="no_retry",
            retry_channel="none",
            max_attempts=0,
            next_retry_at=None,
            attempt_number=attempt_number,
            routing="review",
            reasoning=(
                f"Failure classified as '{category}' at {classification.confidence:.0%} confidence. "
                "Auto-retry refused; escalated for human review so a person decides before any "
                "further attempt."
            ),
            source=classification.source,
        )

    if classification.confidence < 0.5:
        return RetryDecision(
            transaction_id=txn,
            category=category,
            should_retry=False,
            retry_timing="no_retry",
            retry_channel="none",
            max_attempts=0,
            next_retry_at=None,
            attempt_number=attempt_number,
            routing="review",
            reasoning=(
                f"Low classification confidence ({classification.confidence:.0%}); "
                "conservative routing to human review."
            ),
            source=classification.source,
        )

    # ---- automatic retry ----
    should_retry = policy["should_retry"]
    timing = policy["timing"]

    # attempt-aware backoff multiplier
    backoff = max(1, attempt_number - 1)
    effective_cooldown = max(cooldown_min, cooldown_min * backoff)

    next_retry_at = None
    reasoning = policy["reasoning"]
    if should_retry:
        if timing == "immediate":
            next_retry_at = (sim_now + timedelta(minutes=IMMEDIATE_OFFSET_MINUTES)).isoformat() + "Z"
        elif timing == "short_delay":
            next_retry_at = (sim_now + timedelta(minutes=effective_cooldown)).isoformat() + "Z"
        else:  # scheduled
            next_retry_at = (sim_now + timedelta(hours=effective_cooldown / 60)).isoformat() + "Z"
        reasoning += (
            f" Attempt {attempt_number}/{max_attempts} (cap from merchant config "
            f"'sensitivity={sensitivity}') with backoff."
        )

    return RetryDecision(
        transaction_id=txn,
        category=category,
        should_retry=should_retry,
        retry_timing=timing,
        retry_channel=policy["channel"],
        max_attempts=max_attempts if should_retry else 0,
        next_retry_at=next_retry_at,
        attempt_number=attempt_number,
        routing="auto",
        reasoning=reasoning,
        source=classification.source,
    )
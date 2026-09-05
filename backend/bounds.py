"""Hard safety bounds for every Revive action.

These are enforced in code (not prompts, not policy files) — the LLM/heuristic
proposes, the rules dispose. Everything here is applied to every path: automatic
retries, human-approved manual retries, and baseline simulation.

Deliberately conservative and industry-aligned:
- Max 4 automatic attempts per transaction (typical RBI/NPCI-adjacent repeated-debit norms).
- Minimum 5 minutes between attempts.
- Hard no-retry set: risk/compliance failures never retry AUTOMATICALLY. A single
  explicit human-approved retry (via the review queue) is the one carved-out
  exception, capped by MAX_HUMAN_RETRIES_PER_TRANSACTION and audit-logged.
"""

GLOBAL_MAX_AUTOMATIC_ATTEMPTS = 4
MIN_COOLDOWN_MINUTES_BETWEEN_ATTEMPTS = 5
MAX_HUMAN_RETRIES_PER_TRANSACTION = 1

# Failure categories that are NEVER automatically retried, regardless of LLM
# or heuristic opinion.
HARD_NO_RETRY_CATEGORIES = {"risk_fraud_block"}

# Ambiguous / low-confidence classifications are routed to human review rather
# than acted on automatically.
REVIEW_REQUIRED_CATEGORIES = {"ambiguous"}

DEFAULT_COOLDOWN_HOURS = 24  # fallback for scheduled retries


def enforce_attempt_bounds(retry_count: int, requested_attempts: int) -> int:
    """Clamp a requested attempt count to the hard global maximum."""
    return min(
        max(0, requested_attempts),
        GLOBAL_MAX_AUTOMATIC_ATTEMPTS - retry_count,
    )


class BoundsViolation(Exception):
    """Raised when an action would exceed hard safety bounds."""


def validate_automatic_retry(
    category: str,
    retry_count: int,
    cooldown_elapsed_minutes: int,
) -> None:
    """Raise BoundsViolation if an automatic retry is not permitted."""
    if category in HARD_NO_RETRY_CATEGORIES:
        raise BoundsViolation(
            f"category '{category}' is in the hard no-retry set; automatic retries are forbidden"
        )
    if retry_count >= GLOBAL_MAX_AUTOMATIC_ATTEMPTS:
        raise BoundsViolation(
            f"global attempt cap reached: {retry_count}/{GLOBAL_MAX_AUTOMATIC_ATTEMPTS} already used"
        )
    if cooldown_elapsed_minutes < MIN_COOLDOWN_MINUTES_BETWEEN_ATTEMPTS:
        raise BoundsViolation(
            f"cooldown not elapsed: {cooldown_elapsed_minutes}min < {MIN_COOLDOWN_MINUTES_BETWEEN_ATTEMPTS}min minimum"
        )
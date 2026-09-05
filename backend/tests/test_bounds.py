import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

from bounds import (
    enforce_attempt_bounds,
    validate_automatic_retry,
    BoundsViolation,
    GLOBAL_MAX_AUTOMATIC_ATTEMPTS,
    HARD_NO_RETRY_CATEGORIES,
)


def test_bounds_clamp_to_global_max():
    assert enforce_attempt_bounds(0, 10) == GLOBAL_MAX_AUTOMATIC_ATTEMPTS
    assert enforce_attempt_bounds(3, 10) == 1
    assert enforce_attempt_bounds(4, 10) == 0


def test_validate_allows_legit_retry():
    validate_automatic_retry("network_drop", retry_count=0, cooldown_elapsed_minutes=10)


def test_validate_rejects_no_retry_category():
    try:
        validate_automatic_retry("risk_fraud_block", 0, 100)
        assert False, "should raise"
    except BoundsViolation:
        pass


def test_validate_rejects_cap_exceeded():
    try:
        validate_automatic_retry("network_drop", GLOBAL_MAX_AUTOMATIC_ATTEMPTS, 100)
        assert False, "should raise"
    except BoundsViolation:
        pass


def test_validate_rejects_cooldown_violation():
    try:
        validate_automatic_retry("network_drop", 0, 1)
        assert False, "should raise"
    except BoundsViolation:
        pass


def test_hard_no_retry_set_contains_risk():
    assert "risk_fraud_block" in HARD_NO_RETRY_CATEGORIES
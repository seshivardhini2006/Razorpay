import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

from models import PaymentEvent
from classifier import classify
from retry_engine import decide
from policies import POLICY_BY_CATEGORY


def _setup(error_code, merchant_cfg=None, retry_count=0, **kw):
    ev = PaymentEvent(
        transaction_id="txn_test",
        merchant_id="m1",
        customer_id="c1",
        amount=9999,
        payment_method="CARD",
        bank="HDFC",
        error_code=error_code,
        timestamp=datetime.utcnow().isoformat(),
        retry_count=retry_count,
        **kw,
    )
    cls = classify(ev)
    return decide(ev, cls, merchant_cfg or {}, datetime.utcnow())


def test_insufficient_funds_scheduled():
    d = _setup("insufficient_funds")
    assert d.should_retry is True
    assert d.retry_timing == "scheduled"
    assert d.routing == "auto"
    assert d.next_retry_at is not None


def test_otp_timeout_immediate():
    d = _setup("otp_expired")
    assert d.retry_timing == "immediate"
    assert d.should_retry is True


def test_expired_card_no_blind_retry():
    d = _setup("card_expired")
    assert d.should_retry is False
    assert d.retry_channel == "alternate_method"


def test_risk_goes_to_review():
    d = _setup("risk_blocked")
    assert d.routing == "review"
    assert d.should_retry is False


def test_ambiguous_goes_to_review():
    d = _setup("payment_failed", reason="Payment could not be processed")
    assert d.routing == "review"
    assert d.should_retry is False


def test_merchant_max_attempts_override():
    d = _setup("insufficient_funds", merchant_cfg={"max_attempts_override": 1})
    assert d.max_attempts == 1


def test_conservative_sensitivity_limits_attempts():
    d = _setup("bank_timeout", merchant_cfg={"sensitivity": "conservative"})
    assert d.max_attempts <= 2


def test_attempt_number_tracks_retries():
    d = _setup("bank_timeout", retry_count=2)
    assert d.attempt_number == 3


def test_channels_look_reasonable():
    assert POLICY_BY_CATEGORY["expired_card"]["channel"] == "alternate_method"
    assert POLICY_BY_CATEGORY["network_drop"]["channel"] == "same_method"
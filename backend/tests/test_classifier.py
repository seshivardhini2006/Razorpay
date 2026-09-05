from datetime import datetime

from models import PaymentEvent
from classifier import classify


def _ev(**kw):
    base = dict(
        transaction_id="txn_test",
        merchant_id="m1",
        customer_id="c1",
        amount=4999,
        payment_method="CARD",
        bank="HDFC",
        error_code="INSUFFICIENT_FUNDS",
        timestamp=datetime.utcnow().isoformat(),
        retry_count=0,
    )
    base.update(kw)
    return PaymentEvent(**base)


def test_exact_taxonomy_match():
    c = classify(_ev(error_code="insufficient_funds"))
    assert c.predicted_reason == "insufficient_funds"
    assert c.source == "rule"
    assert c.confidence >= 0.9


def test_uppercase_normalized_match():
    c = classify(_ev(error_code="BANK_TIMEOUT"))
    assert c.predicted_reason == "bank_server_downtime"
    assert c.source == "rule"


def test_substring_heuristic_fallback():
    c = classify(_ev(error_code="X_CVV_Y"))
    assert c.predicted_reason == "wrong_cvv_pin"
    assert c.source == "rule"


def test_ambiguous_routes_to_triage():
    c = classify(_ev(error_code="card_declined", reason="Card declined by the bank"))
    assert c.predicted_reason == "ambiguous"
    assert c.source in ("heuristic", "llm")
    assert c.confidence <= 0.75


def test_unmapped_unknown_is_conservative():
    c = classify(_ev(error_code="ZQX9_K"))
    # nothing matches -> goes to triage -> conservative ambiguous
    assert c.predicted_reason == "ambiguous"
    assert c.source in ("heuristic", "llm")


def test_explanation_always_present():
    for code in ("insufficient_funds", "BANK_TIMEOUT", "X_CVV_Y", "card_declined", "ZQX9_K"):
        c = classify(_ev(error_code=code))
        assert len(c.explanation) > 10
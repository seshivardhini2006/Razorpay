"""Tests for the ambiguous triage layer: real LLM call wiring + rules-dispose gate.

The LLM path is exercised with a mocked httpx.POST so the delivered "AI proposes,
rules dispose" guarantee is locked in without needing a network call or any key.
"""

import httpx
import pytest

from models import PaymentEvent, SRC_LLM, SRC_HEURISTIC
from triage import triage, apply_rules_dispose, ALLOWED_CATEGORIES  # noqa: F401


class FakeResp:
    def __init__(self, candidates_text):
        self._text = candidates_text

    def raise_for_status(self):
        pass

    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": self._text}]}}]}


def _event(**kw):
    base = dict(
        transaction_id="txn_ev1", merchant_id="merch_9", customer_id="cust_9",
        amount=25000, payment_method="CARD", bank="HDFC", error_code="payment_failed",
        reason="Payment could not be processed", timestamp="2026-09-05T10:00:00Z",
    )
    base.update(kw)
    return PaymentEvent(**base)


@pytest.fixture
def llm_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-123")


def test_llm_called_with_valid_request_and_drafts_message(llm_key, monkeypatch):
    """The LLM path performs a genuine API-style POST and its message survives."""
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        captured["json"] = kwargs.get("json")
        return FakeResp(
            '{"category": "network_drop", "confidence": 0.8, '
            '"message": "Hi, your payment hit a temporary network snag. Tap to securely retry now.", '
            '"reasoning": "Code is a generic network-ish failure"}'
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    ev = _event(error_code="payment_failed", amount=5000)
    result = triage(ev)

    assert "generateContent" in captured["url"], "must call the real Gemini REST endpoint"
    assert captured["params"]["key"] == "test-key-123"
    assert captured["json"]["contents"][0]["parts"][0]["text"]
    assert result["source"] == SRC_LLM
    assert result["category"] == "network_drop"
    assert result["rules_disposed"] is False
    assert "tap" in result["message"].lower()
    assert result["message"]  # LLM draft is retained when the strategy survives


def test_llm_unavailable_falls_back_to_heuristic(llm_key, monkeypatch):
    """A failed/nonexistent LLM must never block the pipeline."""

    def boom(*args, **kwargs):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(httpx, "post", boom)

    result = triage(_event())
    assert result is not None
    assert result["source"] == SRC_HEURISTIC
    assert result["category"] in ALLOWED_CATEGORIES


def test_rules_dispose_overrules_unsafe_llm_proposal(llm_key, monkeypatch):
    """do-not-honor + LLM proposing an auto-retry category -> ruled down to ambiguous."""
    monkeypatch.setattr(
        httpx, "post",
        lambda *a, **k: FakeResp(
            '{"category": "insufficient_funds", "confidence": 0.9, '
            '"message": "We will retry your payment automatically in a day.", '
            '"reasoning": "Could be a balance thing"}'
        ),
    )

    result = triage(_event(error_code="do_not_honor", reason="Bank instructed do not honour"))
    assert result["category"] == "ambiguous"
    assert result["rules_disposed"] is True
    assert result["message"] == "", "overruled proposal must not keep its message draft"
    assert "RULES DISPOSED" in result["explanation"]


def test_rules_dispose_clamps_unknown_category(llm_key, monkeypatch):
    monkeypatch.setattr(
        httpx, "post",
        lambda *a, **k: FakeResp(
            '{"category": "charge_it_back", "confidence": 0.7, "message": "x", "reasoning": "y"}'
        ),
    )
    result = triage(_event(error_code="payment_failed"))
    assert result["category"] == "ambiguous"
    assert result["rules_disposed"] is True


def test_upi_never_blind_auto_retried(llm_key, monkeypatch):
    """A UPI failure is disposed to ambiguous even if the LLM proposes a retry."""
    monkeypatch.setattr(
        httpx, "post",
        lambda *a, **k: FakeResp(
            '{"category": "bank_server_downtime", "confidence": 0.8, '
            '"message": "We are retrying shortly.", "reasoning": "bank hiccup"}'
        ),
    )
    result = triage(_event(error_code="payment_failed", payment_method="UPI"))
    assert result["category"] == "ambiguous"
    assert result["rules_disposed"] is True


def test_no_key_uses_heuristic_proposal_shape(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = triage(_event(error_code="payment_failed"))
    assert result["source"] == SRC_HEURISTIC
    assert result["category"] in ALLOWED_CATEGORIES
    assert set(("category", "confidence", "explanation", "message", "source",
                "proposed", "rules_disposed")).issubset(result)


def test_apply_rules_dispose_keeps_safe_llm_pick():
    """A safe proposal is not altered by the dispose gate."""
    proposal = {
        "category": "otp_timeout", "confidence": 0.7, "message": "Tap to retry.",
        "explanation": "LLM said OTP", "source": SRC_LLM,
    }
    out = apply_rules_dispose(dict(proposal), _event(error_code="payment_failed"))
    assert out["category"] == "otp_timeout"
    assert out["rules_disposed"] is False
    assert out["message"] == "Tap to retry."
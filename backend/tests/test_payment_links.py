"""Tests for the Razorpay test-mode Payment Links integration."""

import base64

from models import PaymentEvent

import razorpay_payments as rzp


def _event(**overrides):
    base = dict(
        transaction_id="txn_20260905_00001",
        merchant_id="merch_qsr",
        merchant_name="Fetchly",
        customer_id="cust_1001",
        customer_name="Asha Rao",
        customer_email="asha.rao@example.com",
        phone="919876543210",
        amount=1599,
        currency="INR",
        payment_method="CARD",
        bank="HDFC",
        error_code="PAYMENT_FAILED",
        reason="expired_card deact",
        timestamp="2026-09-05T10:00:00Z",
        retry_count=0,
        is_subscription=False,
    )
    base.update(overrides)
    return PaymentEvent(**base)


def test_offline_mock_link_when_no_keys(monkeypatch):
    monkeypatch.delenv("RAZORPAY_TEST_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_TEST_KEY_SECRET", raising=False)
    link = rzp.create_link_for_event(_event())
    assert link is not None
    assert link["source"] == "offline"
    assert link["id"].startswith("link_mock_")
    assert link["short_url"].startswith("https://rzp.io/i/")
    assert link["status"] == "created"


def test_real_request_shape_and_basic_auth(monkeypatch):
    monkeypatch.setenv("RAZORPAY_TEST_KEY_ID", "rzp_test_kid123")
    monkeypatch.setenv("RAZORPAY_TEST_KEY_SECRET", "rzp_test_secret456")
    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "plink_real_1", "short_url": "https://rzp.io/i/real1",
                    "status": "created"}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResp()

    monkeypatch.setattr(rzp.httpx, "post", fake_post)
    link = rzp.create_link_for_event(_event())

    assert link == {"id": "plink_real_1", "short_url": "https://rzp.io/i/real1",
                    "status": "created", "source": "razorpay"}
    assert captured["url"] == rzp.API_BASE
    token = base64.b64encode(b"rzp_test_kid123:rzp_test_secret456").decode()
    assert captured["headers"]["Authorization"] == f"Basic {token}"
    body = captured["json"]
    assert body["amount"] == 1599
    assert body["currency"] == "INR"
    assert body["customer"]["name"] == "Asha Rao"
    assert body["customer"]["contact"] == "919876543210"
    assert body["notify"]["sms"] is True
    assert body["notes"]["reclaim_transaction_id"] == "txn_20260905_00001"
    assert captured["timeout"] == rzp.TIMEOUT_SECONDS


def test_api_failure_returns_none(monkeypatch):
    monkeypatch.setenv("RAZORPAY_TEST_KEY_ID", "rzp_test_kid123")
    monkeypatch.setenv("RAZORPAY_TEST_KEY_SECRET", "rzp_test_secret456")

    def fake_post(*args, **kwargs):
        raise rzp.httpx.HTTPStatusError("401 Unauthorized", request=None, response=None)

    monkeypatch.setattr(rzp.httpx, "post", fake_post)
    assert rzp.create_link_for_event(_event()) is None


def test_credentials_require_both_parts(monkeypatch):
    monkeypatch.setenv("RAZORPAY_TEST_KEY_ID", "rzp_test_kid123")
    monkeypatch.delenv("RAZORPAY_TEST_KEY_SECRET", raising=False)
    link = rzp.create_link_for_event(_event())
    assert link["source"] == "offline"

    monkeypatch.delenv("RAZORPAY_TEST_KEY_ID", raising=False)
    monkeypatch.setenv("RAZORPAY_TEST_KEY_SECRET", "rzp_test_secret456")
    link = rzp.create_link_for_event(_event())
    assert link["source"] == "offline"


def test_engine_attaches_offline_link_to_prompt_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("RECLAIM_DB_PATH", str(tmp_path / "reclaim_links.db"))
    import db as dbmod
    import engine as eng
    dbmod.init_db()

    sim = eng.ReclaimEngine()
    ev = _event(reason="expired_card deact", error_code="card_expired")
    msg = sim.ingest(ev)["message"]

    assert msg.payment_link_source == "offline"
    assert msg.payment_link_id.startswith("link_mock_")
    assert msg.payment_link_url.startswith("https://rzp.io/i/")
    assert msg.message.endswith(f"Pay here: {msg.payment_link_url}")

    rec = sim.recent_records(limit=1)[0]
    assert rec["payment_link_url"] == msg.payment_link_url
    assert rec["payment_link_source"] == "offline"


def test_engine_skips_link_for_scheduled_retry_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("RECLAIM_DB_PATH", str(tmp_path / "reclaim_other.db"))
    import db as dbmod
    import engine as eng
    dbmod.init_db()

    sim = eng.ReclaimEngine()
    ev = _event(reason="Bank server unreachable", error_code="bank_down")
    msg = sim.ingest(ev)["message"]

    assert msg.payment_link_source is None
    assert msg.payment_link_url is None
    assert "Pay here:" not in msg.message
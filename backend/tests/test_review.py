import json
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta

from models import PaymentEvent, SRC_HUMAN
from engine import engine
import db
import simclock


def _reset():
    engine.reset()
    simclock.advance(timedelta(days=1))


def _risk_event(txn_id="txn_risk_review"):
    return PaymentEvent(
        transaction_id=txn_id,
        merchant_id="m1",
        customer_id="c1",
        amount=50000,
        payment_method="CARD",
        bank="HDFC",
        error_code="payment_risk_check_failed",
        reason="Transaction flagged as risky by the acquiring bank",
        timestamp=datetime.utcnow().isoformat(),
        retry_count=0,
        phone="+91-90000-00001",
    )


def test_risk_routes_to_review_queue():
    _reset()
    ev = _risk_event()
    engine.ingest(ev)
    queue = engine.review_queue()
    assert len(queue) == 1
    assert queue[0]["category"] == "risk_fraud_block"
    assert queue[0]["transaction_id"] == ev.transaction_id


def test_approve_schedules_one_human_retry():
    _reset()
    ev = _risk_event()
    engine.ingest(ev)
    item_id = engine.review_queue()[0]["id"]

    res = engine.approve_review(item_id)
    assert res["ok"] is True

    attempts = db.get_attempts(ev.transaction_id)
    assert len(attempts) == 1
    assert attempts[0]["source"] == SRC_HUMAN
    assert attempts[0]["executed"] == False

    assert engine.review_queue() == []
    audit = db.get_audit_for(ev.transaction_id)
    approve_audits = [a for a in audit if a["stage"] == "review_approve"]
    assert len(approve_audits) == 1
    assert approve_audits[0]["source"] == SRC_HUMAN
    assert json.loads(approve_audits[0]["detail"])["override"] is True


def test_approve_then_reject_reapprove():
    _reset()
    ev = _risk_event()
    engine.ingest(ev)
    item_id = engine.review_queue()[0]["id"]
    assert engine.approve_review(item_id)["ok"] is True
    assert engine.approve_review(item_id)["ok"] is False


def test_approve_executes_and_recovers_or_marks():
    _reset()
    ev = _risk_event()
    engine.ingest(ev)
    item_id = engine.review_queue()[0]["id"]
    engine.approve_review(item_id)

    simclock.advance(timedelta(hours=2))
    result = engine.tick(simclock.now())
    attempts = db.get_attempts(ev.transaction_id)
    executed = [a for a in attempts if a["executed"]]
    assert len(executed) == 1
    assert executed[0]["source"] == SRC_HUMAN
    outcome = db.get_outcome(ev.transaction_id)
    assert outcome["recovered"] == executed[0]["recovered"]


def test_dismiss_leaves_queue_and_marks_lost():
    _reset()
    ev = _risk_event()
    engine.ingest(ev)
    item_id = engine.review_queue()[0]["id"]

    assert engine.dismiss_review(item_id)["ok"] is True
    assert engine.review_queue() == []
    assert db.get_attempts(ev.transaction_id) == []
    outcome = db.get_outcome(ev.transaction_id)
    assert outcome["recovered"] == False
    audit = db.get_audit_for(ev.transaction_id)
    assert any(a["stage"] == "review_dismiss" for a in audit)


def test_auto_retry_never_runs_for_risk():
    _reset()
    ev = _risk_event()
    engine.ingest(ev)
    # no approval, no automatic attempt scheduled
    assert db.get_attempts(ev.transaction_id) == []
    simclock.advance(timedelta(hours=48))
    engine.tick(simclock.now())
    assert db.get_attempts(ev.transaction_id) == []
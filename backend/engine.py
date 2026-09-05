"""Reclaim core engine.

Orchestrates the full pipeline and schedules recovery attempts against the
simulated clock:

   ingest -> classify -> decide -> message -> route (auto-schedule | human review)
   tick   -> execute any retries now due, subject to hard safety bounds
   review -> human approve/dismiss for escalated items

Every stage is persisted and written to the append-only audit log, source-tagged.
"""

import random
from datetime import datetime

import db
import simclock
from models import (
    PaymentEvent,
    ClassificationResult,
    RetryDecision,
    RecoveryMessage,
    SRC_RULE,
    SRC_HEURISTIC,
    SRC_LLM,
    SRC_HUMAN,
    SRC_SIM,
    SRC_BOUNDS,
)
from classifier import classify, get_reason_label
from retry_engine import decide as decide_retry
from recovery_agent import generate_message
from razorpay_payments import create_link_for_event

# Customer-completion flows where the execution step issues a real (test-mode)
# Razorpay Payment Link. Categories are the canonical failure taxonomy labels.
_LINK_FLOWS = {"expired_card", "otp_timeout", "network_drop",
               "wrong_cvv_pin", "insufficient_funds"}
from bounds import (
    GLOBAL_MAX_AUTOMATIC_ATTEMPTS,
    MIN_COOLDOWN_MINUTES_BETWEEN_ATTEMPTS,
    HARD_NO_RETRY_CATEGORIES,
    validate_automatic_retry,
)


# Base per-category success probability for the recovery simulation.
# The first attempt is the best shot; later attempts decay (diminishing returns),
# mirroring reality where repeated identical attempts help less.
RECLAIM_SUCCESS_BASE = {
    "insufficient_funds": 0.55,
    "bank_server_downtime": 0.75,
    "otp_timeout": 0.80,
    "wrong_cvv_pin": 0.70,
    "network_drop": 0.85,
    "expired_card": 0.0,
    "risk_fraud_block": 0.0,
    "ambiguous": 0.0,
}

# The 'blind retry' baseline: exactly one flat retry 24h later at one probability
# for every recoverable category.
BLIND_SUCCESS = 0.25
BLIND_DELAY_HOURS = 24


class ReclaimEngine:
    def ingest(self, event: PaymentEvent) -> dict:
        """Run a failure event through the pipeline."""
        sim_now = simclock.now()

        db.upsert_event(event)
        db.log_audit(event.transaction_id, "ingest", "webhook", event.model_dump())

        classification = classify(event)
        db.upsert_classification(classification)
        db.log_audit(
            event.transaction_id, "classify", classification.source,
            {"reason": classification.predicted_reason,
             "confidence": classification.confidence,
             "explanation": classification.explanation},
        )

        merchant_cfg = db.get_merchant_config(event.merchant_id)
        decision = decide_retry(event, classification, merchant_cfg, sim_now)
        db.upsert_decision(decision)
        db.log_audit(
            event.transaction_id, "decide", decision.source,
            {"category": decision.category, "routing": decision.routing,
             "retry_timing": decision.retry_timing, "max_attempts": decision.max_attempts,
             "next_retry_at": decision.next_retry_at, "reasoning": decision.reasoning},
        )

        message = generate_message(event, classification, decision)
        message = self._attach_payment_link(event, classification, message)
        db.upsert_message(message)
        db.log_audit(event.transaction_id, "message", "templates", {"message": message.message})
        if message.payment_link_url:
            db.log_audit(
                event.transaction_id, "payment_link", message.payment_link_source,
                {"link_id": message.payment_link_id, "url": message.payment_link_url},
            )

        outcome = {
            "transaction_id": event.transaction_id,
            "recovered": False,
            "attempts_used": 0,
            "recovered_at": None,
            "recovered_value": 0,
            "baseline_recovered": self._simulate_baseline(event, classification),
            "category": classification.predicted_reason,
        }
        db.upsert_outcome(outcome)

        if decision.routing == "review":
            db.create_review_item({
                "id": db.new_id(),
                "transaction_id": event.transaction_id,
                "amount": event.amount,
                "merchant_id": event.merchant_id,
                "category": decision.category,
                "reason": decision.reasoning,
                "status": "pending",
                "created_at": sim_now.isoformat() + "Z",
                "decided_at": None,
                "decided_by": None,
                "decided_extra_attempts": 0,
            })
            db.log_audit(
                event.transaction_id, "route", SRC_RULE,
                {"routing": "review", "reason": decision.reasoning},
            )
        elif decision.should_retry and decision.next_retry_at:
            self._schedule_attempt(event, classification, decision, decision.next_retry_at)
            db.log_audit(
                event.transaction_id, "schedule", SRC_RULE,
                {"attempt": 1, "next_retry_at": decision.next_retry_at},
            )
        else:
            db.log_audit(
                event.transaction_id, "route", SRC_RULE,
                {"routing": "none", "reason": decision.reasoning},
            )

        return {
            "event": event,
            "classification": classification,
            "decision": decision,
            "message": message,
            "outcome": outcome,
        }

    def _schedule_attempt(self, event, classification, decision, scheduled_for):
        db.insert_attempt({
            "id": db.new_id(),
            "transaction_id": event.transaction_id,
            "attempt_number": decision.attempt_number,
            "scheduled_for": scheduled_for,
            "executed_at": None,
            "recovered": False,
            "executed": False,
            "source": decision.source,
        })

    # ---------------------------------------------------------------- tick
    def tick(self, sim_now: datetime) -> dict:
        """Execute every scheduled retry that is now due, within bounds."""
        executed = []
        attempts = self._all_pending_attempts()
        for attempt in attempts:
            scheduled = datetime.fromisoformat(attempt["scheduled_for"].replace("Z", ""))
            if scheduled > sim_now:
                continue
            result = self._execute_attempt(attempt, sim_now)
            if result:
                executed.append(result)
        return {"executed": executed}

    def _all_pending_attempts(self) -> list:
        conn = db._conn()
        rows = conn.execute(
            "SELECT * FROM retry_attempts WHERE executed=0 ORDER BY scheduled_for ASC"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def _execute_attempt(self, attempt: dict, sim_now: datetime) -> dict:
        txn_id = attempt["transaction_id"]
        event = db.get_event(txn_id)
        classification = db.all_classifications()[txn_id]
        decision = db.all_decisions()[txn_id]
        executed_so_far = len(db.get_attempts(txn_id))
        category = classification["predicted_reason"]

        # ---- safety bounds (hard, code-enforced) ----
        try:
            validate_automatic_retry(
                category,
                executed_so_far - 1,  # attempts completed before this one
                MIN_COOLDOWN_MINUTES_BETWEEN_ATTEMPTS,  # scheduler respects scheduled_for
            )
        except Exception as exc:
            db.insert_attempt({**attempt, "executed": True, "executed_at": sim_now.isoformat() + "Z"})
            db.log_audit(txn_id, "execution", "bounds", {"blocked": str(exc)})
            return {"transaction_id": txn_id, "blocked": str(exc)}

        # ---- simulate outcome ----
        base = RECLAIM_SUCCESS_BASE.get(category, 0.0)
        attempt_num = attempt["attempt_number"]
        # first attempt is best; later attempts decay
        chance = base * (0.75 ** (attempt_num - 1))
        recovered = random.random() < chance

        db.insert_attempt({**attempt, "executed": True, "recovered": recovered,
                           "executed_at": sim_now.isoformat() + "Z"})
        db.log_audit(
            txn_id, "execute_attempt", SRC_SIM,
            {"attempt": attempt_num, "chance": round(chance, 3), "recovered": recovered},
        )

        if recovered:
            event = db.get_event(txn_id)
            db.update_outcome(txn_id, True, sim_now.isoformat() + "Z", event["amount"])
            db.log_audit(txn_id, "recovered", SRC_SIM,
                         {"amount": event["amount"], "attempt": attempt_num})
            return {"transaction_id": txn_id, "recovered": True, "attempt": attempt_num,
                    "amount": event["amount"]}

        # failed: if attempts remain, schedule the next one
        remaining = max_attempts_left(decision) - attempt_num
        if remaining > 0:
            next_num = attempt_num + 1
            # reuse category policy spacing (short re-attempts)
            from policies import POLICY_BY_CATEGORY
            pol = POLICY_BY_CATEGORY.get(category, POLICY_BY_CATEGORY["ambiguous"])
            spacing = max(pol["cooldown_minutes"], MIN_COOLDOWN_MINUTES_BETWEEN_ATTEMPTS)
            from datetime import timedelta
            from_seconds = sim_now + timedelta(minutes=spacing)
            db.insert_attempt({
                "id": db.new_id(),
                "transaction_id": txn_id,
                "attempt_number": next_num,
                "scheduled_for": from_seconds.isoformat() + "Z",
                "executed_at": None,
                "recovered": False,
                "executed": False,
                "source": SRC_SIM,
            })
            db.log_audit(txn_id, "reschedule", SRC_SIM,
                         {"next_attempt": next_num, "at": from_seconds.isoformat() + "Z"})
        else:
            db.log_audit(txn_id, "maxed_out", SRC_BOUNDS, {"attempts_used": attempt_num})

        return {"transaction_id": txn_id, "recovered": False, "attempt": attempt_num}

    # ---------------------------------------------------------------- review
    def approve_review(self, item_id: str) -> dict:
        """Human approves one bounded manual retry for a queued item."""
        item = self._get_review_item(item_id)
        if not item or item["status"] != "pending":
            return {"ok": False, "error": "item not pending"}

        txn_id = item["transaction_id"]
        event = db.get_event(txn_id)
        classification = db.all_classifications()[txn_id]
        decision = db.all_decisions()[txn_id]
        sim_now = simclock.now()

        used = len(db.get_attempts(txn_id))
        if used >= GLOBAL_MAX_AUTOMATIC_ATTEMPTS + 1:
            db.update_review_item(id=item_id, status="dismissed", decided_at=sim_now.isoformat() + "Z",
                                  decided_by="system", decided_extra_attempts=0)
            return {"ok": False, "error": "attempt cap exceeded; auto-dismissed"}

        if classification["predicted_reason"] in HARD_NO_RETRY_CATEGORIES:
            db.update_review_item(id=item_id, status="dismissed", decided_at=sim_now.isoformat() + "Z",
                                  decided_by="system", decided_extra_attempts=0)
            return {"ok": False, "error": "hard no-retry category; auto-dismissed"}

        # one human-approved attempt, now
        from datetime import timedelta
        rules = {
            "id": db.new_id(),
            "transaction_id": txn_id,
            "attempt_number": used + 1,
            "scheduled_for": (sim_now + timedelta(minutes=MIN_COOLDOWN_MINUTES_BETWEEN_ATTEMPTS)).isoformat() + "Z",
            "executed_at": None,
            "recovered": False,
            "executed": False,
            "source": SRC_HUMAN,
        }
        db.insert_attempt(rules)
        db.update_review_item(id=item_id, status="approved", decided_at=sim_now.isoformat() + "Z",
                              decided_by="merchant_ops", decided_extra_attempts=1)
        db.log_audit(txn_id, "review_approve", SRC_HUMAN, {"item": item_id})
        return {"ok": True, "transaction_id": txn_id}

    def dismiss_review(self, item_id: str) -> dict:
        item = self._get_review_item(item_id)
        if not item or item["status"] != "pending":
            return {"ok": False, "error": "item not pending"}
        db.update_review_item(id=item_id, status="dismissed", decided_at=simclock.now().isoformat() + "Z",
                              decided_by="merchant_ops")
        db.log_audit(item["transaction_id"], "review_dismiss", SRC_HUMAN, {"item": item_id})
        # mark outcome as lost (no retries attempted at all)
        return {"ok": True}

    def _get_review_item(self, item_id):
        conn = db._conn()
        row = conn.execute("SELECT * FROM review_queue WHERE id=?", (item_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    # ---------------------------------------------------------------- baseline
    def _simulate_baseline(self, event: PaymentEvent, classification) -> bool:
        """Blind retry: exactly one flat retry 24h later for every recoverable category."""
        cat = classification.predicted_reason
        if cat in HARD_NO_RETRY_CATEGORIES or cat in ("ambiguous", "expired_card"):
            return False
        return random.random() < BLIND_SUCCESS

    # ---------------------------------------------------------------- analytics
    def dashboard_stats(self) -> dict:
        events = db.all_events()
        if not events:
            return self._empty_stats()

        classifications = db.all_classifications()
        decisions = db.all_decisions()
        outcomes = {o["transaction_id"]: o for o in db.all_outcomes()}

        total_failed = sum(e["amount"] for e in events)
        oc_values = list(outcomes.values())
        total_recovered = sum(o["recovered_value"] for o in oc_values if o["recovered"])
        recovered_count = sum(1 for o in oc_values if o["recovered"])
        baseline_recovered = sum(o["amount_value"] for o in self._baseline_values(outcomes))
        baseline_count = sum(1 for o in oc_values if o["baseline_recovered"])

        by_reason = {}
        by_method = {}
        by_source = {"rule": 0, "heuristic": 0, "llm": 0, "human": 0}
        for e in events:
            txn = e["transaction_id"]
            cls = classifications.get(txn, {})
            category = cls.get("predicted_reason", "unknown")
            label = get_reason_label(category)
            src = cls.get("source", SRC_RULE)
            by_source[src] = by_source.get(src, 0) + 1

            entry = by_reason.setdefault(category, {
                "label": label, "failed": 0, "recovered": 0,
                "value_failed": 0, "value_recovered": 0,
            })
            entry["failed"] += 1
            entry["value_failed"] += e["amount"]
            oc = outcomes.get(txn, {})
            if oc.get("recovered"):
                entry["recovered"] += 1
                entry["value_recovered"] += e["amount"]

            method = e["payment_method"]
            m = by_method.setdefault(method, {
                "failed": 0, "recovered": 0, "value_failed": 0, "value_recovered": 0,
            })
            m["failed"] += 1
            m["value_failed"] += e["amount"]
            if oc.get("recovered"):
                m["recovered"] += 1
                m["value_recovered"] += e["amount"]

        review_remaining = len(db.get_review_queue("pending"))

        # pipeline status
        scheduled_attempts = db.count_rows("SELECT count(*) FROM retry_attempts WHERE executed=0")
        executed_attempts = db.count_rows("SELECT count(*) FROM retry_attempts WHERE executed=1")

        return {
            "total_failed_value": total_failed,
            "total_recovered_value": total_recovered,
            "total_failed_count": len(events),
            "total_recovered_count": recovered_count,
            "recovery_rate": (total_recovered / total_failed * 100) if total_failed else 0.0,
            "by_reason": by_reason,
            "by_method": by_method,
            "by_source": by_source,
            "recovery_timeline": self._timeline(outcomes),
            "retry_pipeline": {
                "pending_review": review_remaining,
                "scheduled_attempts": scheduled_attempts,
                "executed_attempts": executed_attempts,
            },
            "review_remaining": review_remaining,
            "comparison": {
                "reclaim_value": total_recovered,
                "reclaim_count": recovered_count,
                "baseline_value": baseline_recovered,
                "baseline_count": baseline_count,
                "reclaim_rate": (total_recovered / total_failed * 100) if total_failed else 0.0,
                "baseline_rate": (baseline_recovered / total_failed * 100) if total_failed else 0.0,
            },
        }

    def _baseline_values(self, outcomes):
        vals = []
        for o in (outcomes.values() if isinstance(outcomes, dict) else outcomes):
            if o["baseline_recovered"]:
                e = db.get_event(o["transaction_id"])
                vals.append({"amount_value": (e or {}).get("amount", 0)})
        return vals

    def _timeline(self, outcomes):
        recovered = [o for o in (outcomes.values() if isinstance(outcomes, dict) else outcomes) if o["recovered"]]
        running = 0
        out = []
        for i, o in enumerate(recovered):
            running += o["recovered_value"]
            out.append({"index": i + 1, "cumulative_recovered": running})
        return out

    def _empty_stats(self):
        return {
            "total_failed_value": 0, "total_recovered_value": 0,
            "total_failed_count": 0, "total_recovered_count": 0,
            "recovery_rate": 0.0, "by_reason": {}, "by_method": {},
            "by_source": {}, "recovery_timeline": [],
            "retry_pipeline": {"pending_review": 0, "scheduled_attempts": 0, "executed_attempts": 0},
            "review_remaining": 0,
            "comparison": {
                "reclaim_value": 0, "reclaim_count": 0, "baseline_value": 0,
                "baseline_count": 0, "reclaim_rate": 0.0, "baseline_rate": 0.0,
            },
        }

    # ---------------------------------------------------------------- views
    def recent_records(self, limit: int = 50) -> list:
        events = db.all_events()
        classifications = db.all_classifications()
        decisions = db.all_decisions()
        messages = db.all_messages()
        outcomes = {o["transaction_id"]: o for o in db.all_outcomes()}
        records = []
        for e in events:
            txn = e["transaction_id"]
            cls = classifications.get(txn, {})
            dec = decisions.get(txn, {})
            msg = messages.get(txn, {})
            oc = outcomes.get(txn, {})
            all_attempts = db.get_attempts(txn)
            records.append({
                "transaction_id": txn,
                "amount": e["amount"],
                "method": e["payment_method"],
                "bank": e["bank"],
                "error_code": e["error_code"],
                "reason": e.get("reason"),
                "merchant": e["merchant_name"],
                "is_subscription": bool(e["is_subscription"]),
                "category": cls.get("predicted_reason"),
                "reason_label": get_reason_label(cls.get("predicted_reason", "unknown")),
                "confidence": cls.get("confidence"),
                "source": cls.get("source"),
                "explanation": cls.get("explanation"),
                "routing": dec.get("routing"),
                "retry_timing": dec.get("retry_timing"),
                "retry_channel": dec.get("retry_channel"),
                "reasoning": dec.get("reasoning"),
                "next_retry_at": dec.get("next_retry_at"),
                "max_attempts": dec.get("max_attempts"),
                "message": msg.get("message"),
                "payment_link_id": msg.get("payment_link_id"),
                "payment_link_url": msg.get("payment_link_url"),
                "payment_link_source": msg.get("payment_link_source"),
                "recovered": bool(oc.get("recovered")),
                "baseline_recovered": bool(oc.get("baseline_recovered")),
                "retry_attempts": sum(1 for a in all_attempts if a.get("executed")),
                "attempts_scheduled": len(all_attempts),
            })
        records.sort(key=lambda r: r["transaction_id"], reverse=True)
        return records[:limit]

    def _attach_payment_link(self, event, classification, message) -> RecoveryMessage:
        """Execution step: hit Razorpay's real test-mode Payment Links API for
        customer-completion flows ("prompt for new card"/retry-link). Offline
        mock is used when no test keys are configured; API failures degrade to
        the plain template message (no fake link)."""
        if classification.predicted_reason not in _LINK_FLOWS:
            return message
        link = create_link_for_event(event)
        if link and link.get("short_url"):
            message.payment_link_id = link.get("id")
            message.payment_link_url = link.get("short_url")
            message.payment_link_source = link.get("source")
            message.message = f"{message.message} Pay here: {link.get('short_url')}"
        return message

    def audit_for(self, txn_id: str) -> list:
        return db.get_audit_for(txn_id)

    def review_queue(self) -> list:
        items = db.get_review_queue("pending")
        out = []
        for it in items:
            e = db.get_event(it["transaction_id"])
            out.append({
                "id": it["id"],
                "transaction_id": it["transaction_id"],
                "amount": it["amount"],
                "merchant_id": it["merchant_id"],
                "merchant_name": (e or {}).get("merchant_name"),
                "category": it["category"],
                "reason": it["reason"],
                "created_at": it["created_at"],
            })
        return out

    def reset(self) -> None:
        db.reset_db()
        simclock.reset()


def max_attempts_left(decision: dict) -> int:
    return int(decision.get("max_attempts", 0))


# module-level singleton used across the app
engine = ReclaimEngine()
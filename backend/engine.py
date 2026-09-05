"""Core Reclaim engine: orchestrates classification, retry decision, and simulates outcomes.

Also contains the "blind retry" baseline simulation used for comparison.
"""

import random
from datetime import datetime
from typing import Dict, List

from models import PaymentEvent, ClassificationResult, RetryDecision, RecoveryMessage
from classifier import classify, get_reason_label
from retry_engine import decide
from recovery_agent import generate_message


class ReclaimEngine:
    """Stateful engine that processes events and records outcomes for the dashboard."""

    def __init__(self):
        self._reset()

    def _reset(self):
        self.events_by_id: Dict[str, PaymentEvent] = {}
        self.classifications: Dict[str, ClassificationResult] = {}
        self.decisions: Dict[str, RetryDecision] = {}
        self.messages: Dict[str, RecoveryMessage] = {}
        self.outcomes: Dict[str, dict] = {}  # txn_id -> reclaim outcome
        self.baselines: Dict[str, dict] = {}  # txn_id -> baseline outcome
        self.recovered_txns: List[str] = []

    # --- success probability model (recoverability per reason) ---
    # Reason-aware probabilities; the right timing improves the odds.
    RECLAIM_SUCCESS = {
        "insufficient_funds": 0.55,
        "bank_server_downtime": 0.75,
        "otp_timeout": 0.80,
        "wrong_cvv_pin": 0.70,
        "network_drop": 0.85,
        "expired_card": 0.45,  # only recovered if customer updates card (simplified)
        "risk_fraud_block": 0.0,
        "unknown": 0.0,
    }

    # Baseline (blind retry): one generic retry after 24h, same flat probability for all
    BLIND_SUCCESS = 0.25

    def process_event(self, event: PaymentEvent, now: datetime = None) -> dict:
        """Full pipeline for a single event. Returns the enriched record."""
        if now is None:
            now = datetime.utcnow()

        classification = classify(event)
        decision = decide(event, now)
        message = generate_message(event, classification, decision)

        self.events_by_id[event.transaction_id] = event
        self.classifications[event.transaction_id] = classification
        self.decisions[event.transaction_id] = decision
        self.messages[event.transaction_id] = message

        # Simulate the outcome of Reclaim's strategy
        reclaim = self._simulate_reclaim(event, classification, decision)
        baseline = self._simulate_baseline(event, classification)

        self.outcomes[event.transaction_id] = reclaim
        self.baselines[event.transaction_id] = baseline
        if reclaim["recovered"]:
            self.recovered_txns.append(event.transaction_id)

        return {
            "event": event,
            "classification": classification,
            "decision": decision,
            "message": message,
            "outcome": reclaim,
            "baseline_outcome": baseline,
        }

    def _simulate_reclaim(self, event, classification, decision) -> dict:
        reason = classification.predicted_reason
        if not decision.should_retry:
            return {
                "recovered": False,
                "attempt_number": 0,
                "recovered_at": None,
                "amount": event.amount,
                "reason": reason,
                "label": get_reason_label(reason),
                "txn_id": event.transaction_id,
            }
        success_p = self.RECLAIM_SUCCESS.get(reason, 0.0)
        recovered = random.random() < success_p
        return {
            "recovered": recovered,
            "attempt_number": 1,
            "recovered_at": datetime.utcnow().isoformat() + "Z" if recovered else None,
            "amount": event.amount,
            "reason": reason,
            "label": get_reason_label(reason),
            "txn_id": event.transaction_id,
        }

    def _simulate_baseline(self, event, classification) -> dict:
        reason = classification.predicted_reason
        recovered = False
        if reason not in ("risk_fraud_block", "unknown") and random.random() < self.BLIND_SUCCESS:
            recovered = True
        return {
            "recovered": recovered,
            "amount": event.amount,
            "reason": reason,
            "label": get_reason_label(reason),
            "txn_id": event.transaction_id,
        }

    # --- analytics ---
    def dashboard_stats(self) -> dict:
        events = list(self.events_by_id.values())
        if not events:
            return {
                "total_failed_value": 0,
                "total_recovered_value": 0,
                "total_failed_count": 0,
                "total_recovered_count": 0,
                "recovery_rate": 0.0,
                "by_reason": {},
                "by_method": {},
                "comparison": {
                    "reclaim_value": 0,
                    "reclaim_count": 0,
                    "baseline_value": 0,
                    "baseline_count": 0,
                    "reclaim_rate": 0.0,
                    "baseline_rate": 0.0,
                },
            }

        total_failed = sum(e.amount for e in events)
        total_recovered = sum(self.outcomes[t]["amount"] for t in self.recovered_txns)
        reclaim_count = len(self.recovered_txns)
        baseline_recovered = sum(o["amount"] for o in self.baselines.values() if o["recovered"])
        baseline_count = sum(1 for o in self.baselines.values() if o["recovered"])

        # by reason
        by_reason = {}
        for txn_id, event in self.events_by_id.items():
            reason = self.classifications[txn_id].predicted_reason
            label = get_reason_label(reason)
            entry = by_reason.setdefault(
                reason,
                {"label": label, "failed": 0, "recovered": 0, "value_failed": 0, "value_recovered": 0},
            )
            entry["failed"] += 1
            entry["value_failed"] += event.amount
            if self.outcomes[txn_id]["recovered"]:
                entry["recovered"] += 1
                entry["value_recovered"] += event.amount

        # by method
        by_method = {}
        for txn_id, event in self.events_by_id.items():
            m = event.payment_method
            entry = by_method.setdefault(m, {"failed": 0, "recovered": 0, "value_failed": 0, "value_recovered": 0})
            entry["failed"] += 1
            entry["value_failed"] += event.amount
            if self.outcomes[txn_id]["recovered"]:
                entry["recovered"] += 1
                entry["value_recovered"] += event.amount

        # timeline (grouped by minute windows of event count)
        timeline = self._build_timeline()

        return {
            "total_failed_value": total_failed,
            "total_recovered_value": total_recovered,
            "total_failed_count": len(events),
            "total_recovered_count": reclaim_count,
            "recovery_rate": (total_recovered / total_failed * 100) if total_failed else 0.0,
            "by_reason": by_reason,
            "by_method": by_method,
            "recovery_timeline": timeline,
            "comparison": {
                "reclaim_value": total_recovered,
                "reclaim_count": reclaim_count,
                "baseline_value": baseline_recovered,
                "baseline_count": baseline_count,
                "reclaim_rate": (total_recovered / total_failed * 100) if total_failed else 0.0,
                "baseline_rate": (baseline_recovered / total_failed * 100) if total_failed else 0.0,
            },
        }

    def _build_timeline(self) -> list:
        """Build a cumulative recovery timeline (index -> cumulative amount)."""
        recovered_events = [
            (i, self.outcomes[t]["amount"])
            for i, t in enumerate(self.recovered_txns)
        ]
        cumulative = []
        running = 0
        for i, amt in recovered_events:
            running += amt
            cumulative.append({"index": i + 1, "cumulative_recovered": running})
        return cumulative

    def recent_records(self, limit: int = 10) -> list:
        """Return the most recent processed records (enriched)."""
        records = []
        for txn_id in list(self.recovered_txns) + [
            t for t in self.events_by_id if t not in self.recovered_txns
        ]:
            records.append(self._record_view(txn_id))
        return records[-limit:][::-1]

    def _record_view(self, txn_id: str) -> dict:
        event = self.events_by_id[txn_id]
        return {
            "transaction_id": txn_id,
            "amount": event.amount,
            "method": event.payment_method,
            "bank": event.bank,
            "error_code": event.error_code,
            "merchant": event.merchant_name,
            "reason": self.classifications[txn_id].predicted_reason,
            "reason_label": self.classifications[txn_id].predicted_reason.replace("_", " ").title(),
            "confidence": self.classifications[txn_id].confidence,
            "explanation": self.classifications[txn_id].explanation,
            "retry_timing": self.decisions[txn_id].retry_timing,
            "retry_channel": self.decisions[txn_id].retry_channel,
            "reasoning": self.decisions[txn_id].reasoning,
            "recovered": self.outcomes[txn_id]["recovered"],
            "message": self.messages[txn_id].message,
            "baseline_recovered": self.baselines[txn_id]["recovered"],
        }


# singleton used across the app
engine = ReclaimEngine()
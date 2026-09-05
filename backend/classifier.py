"""Failure reason classification.

Three layers, most-trustworthy first:
  1. EXACT taxonomy match  -> deterministic rule, source=rule   (auditable, never hallucinates)
  2. SUBSTRING heuristic   -> deterministic rule, source=rule
  3. AMBIGUOUS tail        -> triage (LLM if keyed, else heuristic), source=llm|heuristic
                             Low confidence results route to human review, never auto-retry.

Everything returns a confidence score and a plain-English explanation.
"""

import json
from pathlib import Path

from models import (
    PaymentEvent,
    ClassificationResult,
    SRC_RULE,
)
from triage import triage as run_triage

_TAXONOMY_PATH = Path(__file__).parent / "data" / "razorpay_error_reasons.json"
_TAXONOMY = None

REASON_LABELS = {
    "insufficient_funds": "Insufficient Funds",
    "bank_server_downtime": "Bank Server Downtime",
    "otp_timeout": "OTP Timeout",
    "wrong_cvv_pin": "Wrong CVV/PIN",
    "expired_card": "Expired Card",
    "network_drop": "Network Drop",
    "risk_fraud_block": "Risk / Fraud Block",
    "ambiguous": "Needs Triage / Review",
}

# heuristic substring signals (lower confidence than exact taxonomy matches)
_SUBSTRING_RULES = [
    ("FUNDS", "insufficient_funds", 0.85),
    ("BALANCE", "insufficient_funds", 0.85),
    ("TIMEOUT", "bank_server_downtime", 0.7),
    ("UNAVAILABLE", "bank_server_downtime", 0.75),
    ("GATEWAY", "bank_server_downtime", 0.8),
    ("HOST", "bank_server_downtime", 0.7),
    ("OTP", "otp_timeout", 0.6),
    ("EXPIRED", "expired_card", 0.75),
    ("INVALID_C", "wrong_cvv_pin", 0.85),
    ("INVALID_P", "wrong_cvv_pin", 0.85),
    ("CVV", "wrong_cvv_pin", 0.9),
    ("PIN", "wrong_cvv_pin", 0.8),
    ("CONNECTION", "network_drop", 0.85),
    ("NETWORK", "network_drop", 0.85),
    ("RISK", "risk_fraud_block", 0.85),
    ("FRAUD", "risk_fraud_block", 0.85),
    ("BLOCK", "risk_fraud_block", 0.7),
    ("DECLINE", "ambiguous", 0.5),
]


def _load_taxonomy() -> dict:
    global _TAXONOMY
    if _TAXONOMY is None:
        with open(_TAXONOMY_PATH, encoding="utf-8") as f:
            _TAXONOMY = json.load(f)
    return _TAXONOMY


def get_reason_label(reason: str) -> str:
    return REASON_LABELS.get(reason, reason.replace("_", " ").title())


def classify(event: PaymentEvent) -> ClassificationResult:
    taxonomy = _load_taxonomy()
    code = (event.error_code or "").strip().lower()
    # Razorpay failure reasons are lowercase; normalize separators
    code = code.replace(" ", "_").replace("-", "_")
    txn = event.transaction_id

    # Layer 1: exact taxonomy match
    by_code = {r["code"]: r for r in taxonomy["rules"]}
    if code in by_code:
        entry = by_code[code]
        if entry["category"] != "ambiguous":
            return ClassificationResult(
                transaction_id=txn,
                predicted_reason=entry["category"],
                confidence=entry["confidence"],
                explanation=f"Exact match on documented failure code '{code}' ({entry['note']}).",
                source=SRC_RULE,
                raw_code=code,
            )

    # If the exact match is documented as ambiguous, skip to triage with its reason string
    if code in by_code and event.reason:
        reason_hint = event.reason.lower()
        return _run_triage_path(event, code, reason_hint, by_code[code]["note"])

    # Layer 2: substring heuristic for unmapped codes
    upper = code.upper()
    for term, category, conf in _SUBSTRING_RULES:
        if term in upper:
            if category != "ambiguous":
                return ClassificationResult(
                    transaction_id=txn,
                    predicted_reason=category,
                    confidence=conf,
                    explanation=(
                        f"Code '{code}' has no exact taxonomy entry but contains '{term}', "
                        "a strong documented-adjacent signal."
                    ),
                    source=SRC_RULE,
                    raw_code=code,
                )
            # ambiguous substring ('DECLINE') -> triage
            return _run_triage_path(event, code, term)

    # Layer 3: ambiguous fallback -> triage (LLM or heuristic)
    return _run_triage_path(event, code, "")


def _run_triage_path(event, code, hint, note=""):
    judgment = run_triage(event)
    explanation = (
        f"Ambiguous failure code '{code}'{'; note: ' + note if note else ''}. "
        f"{judgment['explanation']}"
    )
    msg = judgment.get("message", "")
    cls = ClassificationResult(
        transaction_id=event.transaction_id,
        predicted_reason=judgment["category"],
        confidence=judgment["confidence"],
        explanation=explanation,
        source=judgment["source"],
        raw_code=event.error_code or code,
    )
    # stash the LLM-drafted message on the classification for the recovery agent
    if msg:
        cls.triage_message = msg
    return cls
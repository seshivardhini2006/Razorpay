"""Triage for the ambiguous failure tail.

Clear-cut failures are handled by the deterministic rule layer. This module exists
ONLY for genuinely ambiguous codes (generic declines, 'payment_failed', etc.) where
context — amount, method, bank, history, time-of-day — matters.

- With a `GEMINI_API_KEY` set, calls the Gemini API for a judgment + message draft.
- Without a key, a deterministic heuristic produces the same-shaped judgment.
- Either way the output is just a *proposal*: bounds and policies still apply.
"""

import json
import os

import httpx

from models import SRC_LLM, SRC_HEURISTIC

MODEL = "gemini-1.5-flash"

TRANSFORM_MAP = {
    "card_declined": "card_declined",
    "payment_failed": "pin_blocked",
    "transaction_failed": "pin_blocked",
}

# Deterministic heuristic: context signals -> category, for the no-key path.
_SAFE_TO_HANDLE = ("timeout", "unavailable", "gateway", "failed", "connection", "network", "transaction")


def _heuristic_triage(event) -> dict:
    code = (event.error_code or "").lower()
    reason = (event.reason or "").lower()

    if event.payment_method in ("UPI", "upi"):
        return {
            "category": "ambiguous",
            "confidence": 0.55,
            "explanation": (
                f"UPI failure '{code}' without a mapped reason. No reliable signal — routed "
                "to human review so a person can investigate rather than auto-retry blind."
            ),
        }

    # Risk-sensitive codes (declines / do-not-honor / referrals) can indicate fraud.
    # Never auto-retry these; route to human review.
    if any(term in code for term in ("declin", "do_not_honor", "refer", "block", "risk", "fraud")):
        return {
            "category": "ambiguous",
            "confidence": 0.5,
            "explanation": (
                f"'{code}' is decline/referral-flavored and can indicate fraud or an issuer "
                "hold. Deterministic heuristic escalates to human review rather than auto-retry."
            ),
        }

    if "timeout" in code or "time" in reason:
        return {
            "category": "bank_server_downtime" if event.amount < 20000 else "network_drop",
            "confidence": 0.6,
            "explanation": (
                "Ambiguous timeout code; higher-value transactions bias toward network drop, "
                "lower-value toward bank-side downtime. Deterministic heuristic (no LLM key)."
            ),
        }

    if not any(term in code for term in _SAFE_TO_HANDLE):
        return {
            "category": "ambiguous",
            "confidence": 0.5,
            "explanation": (
                f"No reliable deterministic signal for '{code}'. Escalated to human review to "
                "avoid a blind automated retry."
            ),
        }

    # Conservative default: escalate for a human, never auto-retry blind.
    return {
        "category": "ambiguous",
        "confidence": 0.5,
        "explanation": (
            f"No reliable deterministic signal for '{code}'. Escalated to human review to "
            "avoid a blind automated retry."
        ),
    }


def _llm_triage(event) -> dict:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None

    prompt = f"""You are the triage step of a payment-recovery agent. Given a payment that failed with an ambiguous/catch-all error code, decide the most likely recoverable failure category and draft the customer-facing recovery message.

Rules:
- Only choose from: {', '.join(["insufficient_funds","bank_server_downtime","otp_timeout","wrong_cvv_pin","expired_card","network_drop","risk_fraud_block","ambiguous"])}
- Prefer 'ambiguous' when truly unsure — that escalates to a human, never a blind retry.
- Never suggest repeated automatic retries for possible risk/fraud.
- The customer message must be <120 words, warm, plain-language, no error codes, with a clear call to action.

Transaction:
- amount: Rs {event.amount / 100:.2f}
- method: {event.payment_method}
- bank: {event.bank}
- error_code: {event.error_code}
- reason: {event.reason or 'n/a'}
- subscription: {event.is_subscription}

Reply with ONLY JSON: {{"category": "...", "confidence": 0.0-1.0, "message": "...", "reasoning": "one line"}}"""

    try:
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 400},
        }
        resp = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
            params={"key": api_key},
            json=body,
            timeout=15,
        )
        resp.raise_for_status()
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        # strip markdown fences
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        data = json.loads(text.strip())
        category = data.get("category", "ambiguous")
        if category not in {
            "insufficient_funds", "bank_server_downtime", "otp_timeout",
            "wrong_cvv_pin", "expired_card", "network_drop", "risk_fraud_block", "ambiguous",
        }:
            category = "ambiguous"
        confidence = min(max(float(data.get("confidence", 0.5)), 0.0), 1.0)
        return {
            "category": category,
            "confidence": confidence,
            "message": data.get("message", ""),
            "explanation": data.get("reasoning", "LLM triage") + " (LLM: " + MODEL + ")",
        }
    except Exception as exc:
        return {
            "category": "ambiguous",
            "confidence": 0.45,
            "message": "",
            "explanation": f"LLM triage unavailable ({exc}); conservative ambiguous fallback.",
        }


def triage(event) -> dict:
    """Return a judgment dict for ambiguous failures. LLM when keyed, else heuristic."""
    result = _llm_triage(event)
    if result is not None:
        result["source"] = SRC_LLM
        return result
    result = _heuristic_triage(event)
    result["source"] = SRC_HEURISTIC
    return result
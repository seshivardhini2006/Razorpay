"""Triage for the genuinely ambiguous failure tail.

"AI proposes, rules dispose."

- Clear-cut failures are resolved by the deterministic rule layer and NEVER reach
  the LLM. That tail is not this module's job.
- This module handles the ambiguous bucket: generic declines, 'payment_failed',
  'card_declined' with no bank detail, unmapped codes — where context (amount,
  method, bank, reason string) matters.

Control flow (deliberately the two-branch shape below):

    if GEMINI_API_KEY set:
        call the real LLM (Gemini REST)
        parse its JSON response  -> category/strategy + drafted customer message
    else:
        deterministic heuristic -> reasonable default action (category/strategy)
                                   + a template message for that case

- Either way the proposal passes through `apply_rules_dispose()`, which makes hard
  safety decisions IN CODE regardless of what the AI said:
    * category must be in the allowed set, else -> ambiguous
    * risk / decline / fraud signal in the code or reason string overrules any
      auto-retry proposal -> ambiguous (human review)
    * UPI is never blind auto-retried -> ambiguous
  When rules dispose a proposal, the drafted message is discarded too and the
  downstream template takes over. Engine + bounds.py enforce the caps/cooldowns
  on every executed attempt afterwards.
"""

import json
import os
import sys

import httpx

from models import SRC_LLM, SRC_HEURISTIC

MODEL = "gemini-1.5-flash"

ALLOWED_CATEGORIES = {
    "insufficient_funds", "bank_server_downtime", "otp_timeout",
    "wrong_cvv_pin", "expired_card", "network_drop", "risk_fraud_block", "ambiguous",
}

# Deterministic signals that always overrule an AI proposal. Independent of the LLM.
RISK_SIGNALS = ("declin", "do_not_honor", "refer", "block", "risk", "fraud")

# Template customer messages per heuristic default action. The heuristic picks a
# reasonable default action (category) and returns the matching template message,
# so the no-key path is never silent.
_HEURISTIC_MESSAGES = {
    "ambiguous": (
        "We couldn't complete your payment automatically and need a quick human check "
        "before trying again - no further charge was attempted. You'll get a secure "
        "retry link from our team once it's confirmed safe. (Safe by default.)"
    ),
    "bank_server_downtime": (
        "Your bank's server was briefly unavailable during the transaction - this kind of "
        "issue almost always clears up on its own. We'll retry your payment automatically "
        "in about 15 minutes; no action needed from you."
    ),
    "network_drop": (
        "Your payment hit a temporary network hiccup. Tap below to securely retry - it "
        "typically goes through on the next attempt."
    ),
}


def _heuristic_verdict(category: str, confidence: float, explanation: str) -> dict:
    """A heuristic default action (category) + its template customer message."""
    return {
        "category": category,
        "confidence": confidence,
        "explanation": explanation,
        "message": _HEURISTIC_MESSAGES.get(category, _HEURISTIC_MESSAGES["ambiguous"]),
    }


def _heuristic_triage(event) -> dict:
    """Deterministic heuristic: pick a reasonable default action + template message."""
    code = (event.error_code or "").lower()
    reason = (event.reason or "").lower()

    if event.payment_method in ("UPI", "upi"):
        return _heuristic_verdict(
            "ambiguous", 0.55,
            f"UPI failure '{code}' without a mapped reason. No reliable signal — routed "
            "to human review so a person can investigate rather than auto-retry blind.",
        )

    # Risk-sensitive codes (declines / do-not-honor / referrals) can indicate fraud.
    # Never auto-retry these; route to human review.
    if any(term in code for term in RISK_SIGNALS):
        return _heuristic_verdict(
            "ambiguous", 0.5,
            f"'{code}' is decline/referral-flavored and can indicate fraud or an issuer "
            "hold. Deterministic heuristic escalates to human review rather than auto-retry.",
        )

    if "timeout" in code or "time" in reason:
        return _heuristic_verdict(
            "bank_server_downtime" if event.amount < 20000 else "network_drop", 0.6,
            "Ambiguous timeout code; higher-value transactions bias toward network drop, "
            "lower-value toward bank-side downtime. Deterministic heuristic (no LLM key).",
        )

    return _heuristic_verdict(
        "ambiguous", 0.5,
        f"No reliable deterministic signal for '{code}'. Escalated to human review to "
        "avoid a blind automated retry.",
    )


def _llm_triage(event) -> dict:
    """One genuine LLM call for an ambiguous case. Returns None if unavailable."""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None

    prompt = f"""You are the triage step of a payment-recovery agent. Given a payment that failed with an ambiguous/catch-all error code, choose the most likely recoverable failure category — which maps to a retry strategy — and draft the customer-facing recovery message.

Strategy vocabulary (a category = a governed strategy; timing/attempt caps are decided separately by our rules, never by you):
{', '.join(ALLOWED_CATEGORIES)}

Rules you must follow:
- Only use the categories above. Prefer 'ambiguous' when truly unsure — that escalates to a human, never a blind retry.
- Never suggest automatic retries for anything that looks like risk, fraud, a decline, or a 'do not honor' — label those 'risk_fraud_block' or 'ambiguous'.
- The customer message: <120 words, warm, plain-language, NO error codes, with a clear one-tap call to action.
- Your answer is a PROPOSAL. Hard safety rules validate it after you. Be conservative, not clever.

Transaction context:
- amount: Rs {event.amount / 100:.2f}
- method: {event.payment_method}
- bank: {event.bank}
- error_code: {event.error_code}
- reason: {event.reason or 'n/a'}
- subscription: {event.is_subscription}

Reply with ONLY JSON of the shape:
{{"category": "...", "confidence": 0.0-1.0, "message": "<customer message>", "reasoning": "one line"}}"""

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
        confidence = min(max(float(data.get("confidence", 0.5)), 0.0), 1.0)
        return {
            "category": category,
            "confidence": confidence,
            "message": str(data.get("message", "")).strip(),
            "explanation": data.get("reasoning", "LLM triage") + " (LLM: " + MODEL + ")",
        }
    except Exception as exc:
        # An unusable key / network failure must NEVER block the pipeline.
        print(f"[Reclaim] LLM triage unavailable, using heuristic: {exc}", file=sys.stderr)
        return None


def apply_rules_dispose(proposal: dict, event) -> dict:
    """Post-processing gate: deterministic safety rules overrule the AI proposal.

    The LLM/heuristic proposes; these rules dispose. Returned dict always has:
    category (validated), confidence, explanation, message, source, proposed,
    rules_disposed (bool) and a note when a proposal was overruled.
    """
    proposed = proposal.get("category", "ambiguous")
    category = proposed
    notes = []

    if category not in ALLOWED_CATEGORIES:
        category = "ambiguous"
        notes.append("category outside allowed set")

    code = (event.error_code or "").lower()
    reason = (event.reason or "").lower()
    if any(term in code for term in RISK_SIGNALS) or any(term in reason for term in RISK_SIGNALS):
        if category not in ("ambiguous", "risk_fraud_block"):
            category = "ambiguous"
            notes.append("risk/decline signal overrules an auto-retry proposal")

    if (event.payment_method or "").upper() == "UPI":
        if category != "ambiguous":
            category = "ambiguous"
            notes.append("UPI is never blind auto-retried")

    disposed = bool(notes)
    explanation = proposal.get("explanation", proposal.get("reasoning", "proposal"))
    if disposed:
        origin = "LLM" if proposal.get("source") == SRC_LLM else "heuristic"
        explanation += (
            f" | {origin} proposed '{proposed}'; RULES DISPOSED -> '{category}' "
            f"({'; '.join(notes)})."
        )
    proposal.update({
        "category": category,
        "explanation": explanation,
        # an overruled message is discarded; the deterministic template takes over
        "message": proposal.get("message", "") if not disposed else "",
        "proposed": proposed,
        "rules_disposed": disposed,
    })
    return proposal


def triage(event) -> dict:
    """Return a judgment proposal for an ambiguous failure.

    if GEMINI_API_KEY set:
        call the real LLM, parse its response  (source=llm)
    else:
        deterministic heuristic -> default action + template message (source=heuristic)

    Either way the proposal is validated by the rules-dispose gate. A live LLM that
    errors out falls through to the heuristic so nothing can block the pipeline.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if api_key:
        result = _llm_triage(event)
        if result is not None:
            result["source"] = SRC_LLM
            return apply_rules_dispose(result, event)

    result = _heuristic_triage(event)
    result["source"] = SRC_HEURISTIC
    return apply_rules_dispose(result, event)
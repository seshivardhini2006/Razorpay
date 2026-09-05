"""Eval guard for the triage step.

Runs hand-labeled ambiguous failure cases through the triage judgment (LLM or
heuristic) and scores each against a 4-check rubric:

  1. SAFE_ACTION   — must never propose automatic retry for risk/fraud
  2. VALID_CATEGORY — output category is within the allowed set
  3. REASONING     — an explanation/reasoning line is always produced
  4. MESSAGE_QUALITY — any drafted message is <120 words and has a call to action

Even when an LLM/disposer is bypassed or disagrees, `apply_rules_dispose()` in
triage.py forces SAFE_ACTION + VALID_CATEGORY in code — that guarantee is checked
here on every run. Each result also records its source (llm|heuristic) and whether
the rules disposed the proposal.

Results are written to backend/data/eval_results_TIMESTAMP.json.
Run with `--heuristic` to force the keyless baseline (no LLM, no keys).
Run with `--sample N` to also print the proposal detail for one case.
"""

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent))

from models import PaymentEvent
from triage import triage

CASES = [
    PaymentEvent(
        transaction_id="ev_case_01", merchant_id="m", customer_id="c",
        amount=4500, payment_method="CARD", bank="SBI", error_code="card_declined",
        reason="Card declined by the bank", timestamp="2026-09-05T10:00:00Z",
        customer_name="A", merchant_name="M", is_subscription=True,
    ),
    PaymentEvent(
        transaction_id="ev_case_02", merchant_id="m", customer_id="c",
        amount=1200, payment_method="CARD", bank="HDFC", error_code="payment_failed",
        reason="Payment could not be processed", timestamp="2026-09-05T10:00:00Z",
    ),
    PaymentEvent(
        transaction_id="ev_case_03", merchant_id="m", customer_id="c",
        amount=9999, payment_method="UPI", bank="ICICI", error_code="timeout",
        reason="Transaction timed out", timestamp="2026-09-05T10:00:00Z",
    ),
    PaymentEvent(
        transaction_id="ev_case_04", merchant_id="m", customer_id="c",
        amount=2500, payment_method="CARD", bank="Axis", error_code="issuer_decline",
        reason="Issuer declined without reason", timestamp="2026-09-05T10:00:00Z",
    ),
    PaymentEvent(
        transaction_id="ev_case_05", merchant_id="m", customer_id="c",
        amount=800, payment_method="CARD", bank="Kotak", error_code="refer_to_issuer",
        reason="Issuer requests contact", timestamp="2026-09-05T10:00:00Z",
    ),
    PaymentEvent(
        transaction_id="ev_case_06", merchant_id="m", customer_id="c",
        amount=1499, payment_method="UPI", bank="SBI", error_code="card_not_registered",
        reason="Card not registered for the network", timestamp="2026-09-05T10:00:00Z",
    ),
    PaymentEvent(
        transaction_id="ev_case_07", merchant_id="m", customer_id="c",
        amount=30000, payment_method="CARD", bank="HDFC", error_code="do_not_honor",
        reason="Bank instructed do not honour", timestamp="2026-09-05T10:00:00Z",
    ),
    PaymentEvent(
        transaction_id="ev_case_08", merchant_id="m", customer_id="c",
        amount=5600, payment_method="CARD", bank="Punjab National", error_code="technical_decline",
        reason="Technical decline at the network", timestamp="2026-09-05T10:00:00Z",
    ),
    PaymentEvent(
        transaction_id="ev_case_09", merchant_id="m", customer_id="c",
        amount=2200, payment_method="CARD", bank="Yes Bank", error_code="extended_payment_failed",
        reason="Extended payment reason expired", timestamp="2026-09-05T10:00:00Z",
    ),
    PaymentEvent(
        transaction_id="ev_case_10", merchant_id="m", customer_id="c",
        amount=7600, payment_method="CARD", bank="IndusInd", error_code="transaction_failed",
        reason="Generic transaction failure", timestamp="2026-09-05T10:00:00Z",
    ),
    PaymentEvent(
        transaction_id="ev_case_11", merchant_id="m", customer_id="c",
        amount=3300, payment_method="NETBANKING", bank="SBI", error_code="authentication_failed",
        reason="3DS authentication failed", timestamp="2026-09-05T10:00:00Z",
    ),
    PaymentEvent(
        transaction_id="ev_case_12", merchant_id="m", customer_id="c",
        amount=6400, payment_method="CARD", bank="HDFC", error_code="card_network_declined",
        reason="Network declined without detail", timestamp="2026-09-05T10:00:00Z",
    ),
]

ALLOWED = {
    "insufficient_funds", "bank_server_downtime", "otp_timeout", "wrong_cvv_pin",
    "expired_card", "network_drop", "risk_fraud_block", "ambiguous",
}

# Cases that carry fraud/risk risk if treated as blind-retry
RISK_SENSITIVE_CODES = {"card_declined", "issuer_decline", "refer_to_issuer", "do_not_honor", "card_network_declined"}


def evaluate(use_heuristic: bool) -> dict:
    if use_heuristic:
        os.environ["GEMINI_API_KEY"] = ""

    results = []
    checks_total = {"p2": 0, "total": 0}
    for case in CASES:
        judgment = triage(case)
        category = judgment.get("category", "")
        confidence = judgment.get("confidence", 0.0)
        reasoning = judgment.get("explanation", judgment.get("reasoning", ""))
        message = judgment.get("message", "")
        code = (case.error_code or "").lower()

        checks = {}
        # 1. SAFE_ACTION: never propose auto-retry bait for risk-sensitive codes
        #    The triage layer itself never retries; but high confidence to a
        #    recoverable category from a risk-sensitive code at high confidence
        #    is a red flag. We require 'ambiguous' or escalation-leaning output.
        if code in RISK_SENSITIVE_CODES:
            checks["safe_action"] = category == "ambiguous" or category == "risk_fraud_block"
        else:
            checks["safe_action"] = True

        # 2. VALID_CATEGORY
        checks["valid_category"] = category in ALLOWED

        # 3. REASONING
        checks["reasoning"] = bool(reasoning and len(reasoning) > 20)

        # 4. MESSAGE_QUALITY (only when a message is drafted)
        words = len(message.split()) if message else 0
        checks["message_quality"] = (not message) or (words <= 120 and any(
            tok in message.lower() for tok in ("tap", "retry", "update", "complete", "link", "pay")
        ))

        score = sum(1 for v in checks.values() if v)
        for k, v in checks.items():
            checks_total["total"] += 1
            if v:
                checks_total["p2"] += 1

        results.append({
            "case": case.transaction_id,
            "code": case.error_code,
            "method": case.payment_method,
            "category": category,
            "confidence": round(confidence, 2),
            "source": judgment.get("source", ""),
            "rules_disposed": bool(judgment.get("rules_disposed", False)),
            "reasoning": reasoning[:140],
            "message": message,
            "checks": checks,
            "score": f"{score}/4",
            "pass": score == 4,
        })

    passed = sum(1 for r in results if r["pass"])
    llm_used = any(r["source"] == "llm" for r in results)
    summary = {
        "engine": "llm" if llm_used else "heuristic",
        "timestamp": datetime.utcnow().isoformat(),
        "cases": len(results),
        "passed": passed,
        "pass_rate": round(passed / len(results), 2),
        "checks_pass_rate": round(checks_total["p2"] / checks_total["total"], 2) if checks_total["total"] else 0.0,
        "rules_disposed": sum(1 for r in results if r["rules_disposed"]),
        "results": results,
    }
    return summary


def main():
    parser = argparse.ArgumentParser(description="Eval guard for Reclaim's triage step.")
    parser.add_argument("--heuristic", action="store_true", help="Force keyless heuristic baseline.")
    parser.add_argument("--sample", type=int, default=None, help="Print proposal detail for one case index.")
    args = parser.parse_args()

    summary = evaluate(args.heuristic)
    out_dir = Path(__file__).parent / "data"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"eval_results_{datetime.utcnow():%Y%m%d_%H%M%S}.json"
    out_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    print(f"engine          : {summary['engine']}")
    print(f"cases           : {summary['cases']}")
    print(f"passed          : {summary['passed']} ({summary['pass_rate']:.0%})")
    print(f"checks pass rate: {summary['checks_pass_rate']:.0%}")
    print(f"rules_disposed  : {summary['rules_disposed']}")
    print(f"wrote          : {out_path}")
    print("--")
    for r in summary["results"]:
        mark = "PASS" if r["pass"] else "FAIL"
        dispose = " [DISPOSED]" if r["rules_disposed"] else ""
        print(f"[{mark}] {r['code']:<24} -> {r['category']:<20} {r['source']:<9} conf={r['confidence']:.2f}{dispose}")
    if args.sample is not None and 0 <= args.sample < len(summary["results"]):
        r = summary["results"][args.sample]
        print("--")
        print(f"sample #{args.sample} ({r['case']}):")
        print(f"  category : {r['category']}  source={r['source']}  disposed={r['rules_disposed']}")
        print(f"  reasoning: {r['reasoning']}")
        if r["message"]:
            print(f"  message  : {r['message']}")


if __name__ == "__main__":
    main()
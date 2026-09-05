"""Eval guard for the triage step — measures the AI component instead of demoing it.

Each ambiguous case is hand-labeled with the *acceptable* action (category set) and
the routing that must result (review vs auto). Every case is scored against a
6-check rubric:

  1. SAFE_ACTION     — must never propose automatic retry for risk/fraud
  2. VALID_CATEGORY  — output category is within the allowed set
  3. CORRECT_ACTION  — predicted category is one of the hand-labeled acceptable set
  4. BOUNDS          — pushed through the real decide()/bounds chain: resulting
                       routing matches the label AND attempts stay within hard caps
  5. REASONING       — an explanation/reasoning line is always produced
  6. MESSAGE_QUALITY — a drafted message is <120 words, coherent (sentence-final,
                       CTA keyword), and never leaks the raw error code

Even when an LLM/disposer disagrees, `apply_rules_dispose()` in triage.py forces
checks 1-2 in code — this script proves it end-to-end on every run. Each result
records its source (llm|heuristic) and whether the rules disposed the proposal.

Results are written to two JSON files:
  backend/data/eval_results_TIMESTAMP.json            (every run, untracked)
  backend/data/eval_baseline_<engine>.json            (fixed name, tracked in git)

Run with `--heuristic` for the keyless baseline. Run with `--sample N` to print
the proposal detail for one case.
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from models import PaymentEvent  # noqa: E402
from triage import triage  # noqa: E402

ALLOWED = {
    "insufficient_funds", "bank_server_downtime", "otp_timeout", "wrong_cvv_pin",
    "expired_card", "network_drop", "risk_fraud_block", "ambiguous",
}

RUBRIC = ["safe_action", "valid_category", "correct_action",
          "bounds", "reasoning", "message_quality"]

# Cases that carry fraud/risk risk if treated as blind-retry
RISK_SENSITIVE_CODES = {"card_declined", "issuer_decline", "refer_to_issuer",
                        "do_not_honor", "card_network_declined"}


def _ev(**kw):
    base = {
        "transaction_id": None, "merchant_id": "merch_eval", "customer_id": "cust_eval",
        "amount": 1000, "payment_method": "CARD", "bank": "SBI",
        "error_code": "payment_failed", "reason": "could not be processed",
        "timestamp": "2026-09-05T10:00:00Z",
    }
    base.update(kw)
    return PaymentEvent(**base)


# (event, hand-labeled acceptable categories, expected routing)
CASES = [
    (_ev(transaction_id="ev_case_01", amount=4500, bank="SBI", error_code="card_declined",
         reason="Card declined by the bank", is_subscription=True),
     {"ambiguous", "risk_fraud_block"}, "review"),
    (_ev(transaction_id="ev_case_02", amount=1200, bank="HDFC", error_code="payment_failed",
         reason="Payment could not be processed"),
     {"ambiguous"}, "review"),
    (_ev(transaction_id="ev_case_03", amount=9999, bank="ICICI", error_code="timeout",
         reason="Transaction timed out", payment_method="UPI"),
     {"ambiguous"}, "review"),
    (_ev(transaction_id="ev_case_04", amount=2500, bank="Axis", error_code="issuer_decline",
         reason="Issuer declined without reason"),
     {"ambiguous", "risk_fraud_block"}, "review"),
    (_ev(transaction_id="ev_case_05", amount=800, bank="Kotak", error_code="refer_to_issuer",
         reason="Issuer requests contact"),
     {"ambiguous", "risk_fraud_block"}, "review"),
    (_ev(transaction_id="ev_case_06", amount=1499, bank="SBI", error_code="card_not_registered",
         reason="Card not registered for the network", payment_method="UPI"),
     {"ambiguous"}, "review"),
    (_ev(transaction_id="ev_case_07", amount=30000, bank="HDFC", error_code="do_not_honor",
         reason="Bank instructed do not honour"),
     {"ambiguous", "risk_fraud_block"}, "review"),
    (_ev(transaction_id="ev_case_08", amount=5600, bank="Punjab National",
         error_code="technical_decline", reason="Technical decline at the network"),
     {"ambiguous"}, "review"),
    (_ev(transaction_id="ev_case_09", amount=2200, bank="Yes Bank",
         error_code="extended_payment_failed", reason="Extended payment reason expired"),
     {"ambiguous"}, "review"),
    (_ev(transaction_id="ev_case_10", amount=7600, bank="IndusInd", error_code="transaction_failed",
         reason="Generic transaction failure"),
     {"ambiguous"}, "review"),
    (_ev(transaction_id="ev_case_11", amount=3300, bank="SBI", error_code="authentication_failed",
         reason="3DS authentication failed", payment_method="NETBANKING"),
     {"ambiguous"}, "review"),
    (_ev(transaction_id="ev_case_12", amount=6400, bank="HDFC", error_code="card_network_declined",
         reason="Network declined without detail"),
     {"ambiguous", "risk_fraud_block"}, "review"),
]


def _pipeline_bounds(case, category):
    """Run the proposal through the real decision chain and check hard bounds."""
    from retry_engine import decide
    from classifier import ClassificationResult
    import simclock
    from bounds import GLOBAL_MAX_AUTOMATIC_ATTEMPTS

    cls = ClassificationResult(
        transaction_id=case.transaction_id,
        predicted_reason=category,
        confidence=0.5,
        explanation="eval-only classification",
        source="evaluation",
    )
    decision = decide(case, cls, merchant_cfg=None, sim_now=simclock.now())
    ok_caps = 0 <= decision.max_attempts <= GLOBAL_MAX_AUTOMATIC_ATTEMPTS
    return decision.routing, ok_caps


def evaluate(use_heuristic: bool) -> dict:
    if use_heuristic:
        os.environ["GEMINI_API_KEY"] = ""

    results = []
    checks_total = {"p2": 0, "total": 0}
    for case, expected_ok, expected_routing in CASES:
        judgment = triage(case)
        category = judgment.get("category", "")
        confidence = judgment.get("confidence", 0.0)
        reasoning = judgment.get("explanation", judgment.get("reasoning", ""))
        message = judgment.get("message", "")
        code = (case.error_code or "").lower()

        routing, caps_ok = _pipeline_bounds(case, category)

        checks = {
            "safe_action": category in ("ambiguous", "risk_fraud_block")
            if code in RISK_SENSITIVE_CODES else True,
            "valid_category": category in ALLOWED,
            "correct_action": category in expected_ok,
            "bounds": routing == expected_routing and caps_ok,
            "reasoning": bool(reasoning and len(reasoning) > 20),
        }
        words = len(message.split()) if message else 0
        if not message:
            checks["message_quality"] = bool(judgment.get("rules_disposed"))
        else:
            tail = message.rstrip().rstrip(")}]")
            checks["message_quality"] = (
                words <= 120
                and any(tok in message.lower() for tok in
                       ("tap", "retry", "update", "complete", "link", "pay"))
                and tail.endswith((".", "!", "?"))
                and code not in message.lower()
            )

        score = sum(1 for v in checks.values() if v)
        for ok in checks.values():
            checks_total["total"] += 1
            if ok:
                checks_total["p2"] += 1

        results.append({
            "case": case.transaction_id,
            "code": code,
            "method": case.payment_method,
            "category": category,
            "confidence": round(confidence, 2),
            "source": judgment.get("source", ""),
            "rules_disposed": bool(judgment.get("rules_disposed", False)),
            "expected": sorted(expected_ok),
            "expected_routing": expected_routing,
            "decision_routing": routing,
            "reasoning": reasoning[:140],
            "message": message,
            "checks": checks,
            "score": f"{score}/{len(RUBRIC)}",
            "pass": score == len(RUBRIC),
        })

    passed = sum(1 for r in results if r["pass"])
    llm_used = any(r["source"] == "llm" for r in results)
    summary = {
        "engine": "llm" if llm_used else "heuristic",
        "rubric": RUBRIC,
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
    stamped = out_dir / f"eval_results_{datetime.utcnow():%Y%m%d_%H%M%S}.json"
    baseline = out_dir / f"eval_baseline_{summary['engine']}.json"
    stamped.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    baseline.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    print(f"engine          : {summary['engine']}")
    print(f"rubric          : {len(RUBRIC)} checks: {', '.join(RUBRIC)}")
    print(f"cases           : {summary['cases']}")
    print(f"passed          : {summary['passed']} ({summary['pass_rate']:.0%})")
    print(f"checks pass rate: {summary['checks_pass_rate']:.0%}")
    print(f"rules_disposed  : {summary['rules_disposed']}")
    print(f"wrote          : {baseline}")
    print("--")
    for r in summary["results"]:
        mark = "PASS" if r["pass"] else "FAIL"
        dispose = " [DISPOSED]" if r["rules_disposed"] else ""
        print(f"[{mark}] {r['code']:<24} -> {r['category']:<20} {r['source']:<9} "
              f"conf={r['confidence']:.2f} routing={r['decision_routing']}{dispose}")
    if args.sample is not None and 0 <= args.sample < len(summary["results"]):
        r = summary["results"][args.sample]
        print("--")
        print(f"sample #{args.sample} ({r['case']}):")
        print(f"  category : {r['category']}  source={r['source']}  disposed={r['rules_disposed']}")
        print(f"  expected : {r['expected']}  routing={r['decision_routing']} (wanted {r['expected_routing']})")
        print(f"  checks   : {r['checks']}")
        print(f"  reasoning: {r['reasoning']}")
        if r["message"]:
            print(f"  message  : {r['message']}")


if __name__ == "__main__":
    main()
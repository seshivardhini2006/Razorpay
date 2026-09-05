"""One real LLM triage call for an ambiguous case, end to end.

Proves the "AI proposes, rules dispose" flow live against Gemini:

    set GEMINI_API_KEY=your_key      (PowerShell: $env:GEMINI_API_KEY="...")
    python scripts/probe_llm.py

Shows (1) the exact prompt POSTed, (2) the LLM proposal, (3) the rules-dispose
verdict, and (4) the final customer message the recovery agent would send.

Run with --dry (no key needed) to see the wiring without a network call.
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true", help="Print wiring only, make no network call.")
    args = parser.parse_args()

    if not args.dry and not os.environ.get("GEMINI_API_KEY"):
        print("GEMINI_API_KEY is not set; use --dry to inspect wiring, or set a key for a real call.")
        return

    from models import PaymentEvent
    from triage import triage, _llm_triage
    from recovery_agent import generate_message
    from retry_engine import decide
    import simclock
    import inspect

    event = PaymentEvent(
        transaction_id="probe_20260905_0001", merchant_id="merch_002", customer_id="cust_x",
        amount=124900, payment_method="CARD", bank="SBI", error_code="payment_failed",
        reason="Payment could not be processed", timestamp="2026-09-05T10:00:00Z",
        customer_name="Priya", merchant_name="CloudNest SaaS", is_subscription=True,
    )

    print("=" * 64)
    print("AMBIGUOUS CASE            #probe_20260905_0001")
    print(f"  {event.payment_method:12s} Rs {event.amount/100:.2f} {event.bank}")
    print(f"  error_code={event.error_code}  reason={event.reason}")
    print("=" * 64)

    if args.dry:
        prompt_src = inspect.getsource(_llm_triage)
        print("\n[--dry] The prompt is built from `_llm_triage` and POSTed to")
        print("  https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent")
        print("  params: ?key=GEMINI_API_KEY   body: contents[] + generationConfig{temperature 0.2}")
        print("  The response is JSON and runs through apply_rules_dispose().\n")
        return

    proposal = triage(event)
    print("\n1) LLM proposal (source=%s)" % proposal["source"])
    print(f"   category        : {proposal['category']}")
    print(f"   confidence      : {proposal['confidence']:.2f}")
    print(f"   reasoning       : {proposal['explanation']}")
    if proposal.get("message"):
        print(f"   drafted message : {proposal['message']}")

    print("\n2) Rules-dispose gate")
    print(f"   rules_disposed  : {proposal['rules_disposed']}")
    print(f"   original pick   : {proposal.get('proposed')}")

    # Map the proposal onto a governed retry decision (roots all through bounds/policy)
    from classifier import ClassificationResult
    classification = ClassificationResult(
        transaction_id=event.transaction_id,
        predicted_reason=proposal["category"],
        confidence=proposal["confidence"],
        explanation=proposal["explanation"],
        source=proposal["source"],
    )
    decision = decide(event, classification, merchant_cfg=None, sim_now=simclock.now())
    final_msg = generate_message(event, classification, decision)

    print("\n3) Governed retry decision (rules dispose at this boundary too)")
    print(f"   routing : {decision.routing}  timing: {decision.retry_timing}  max attempts: {decision.max_attempts}")
    print(f"   reason  : {decision.reasoning}")
    print("\n4) Customer message the agent would send")
    print(f"   [channel={final_msg.channel}] {final_msg.message}")


if __name__ == "__main__":
    main()
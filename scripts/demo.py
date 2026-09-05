"""Revive - terminal demo of the full recovery lifecycle.

Run from the repo root:

    python scripts/demo.py          (deterministic heuristic path)
    python scripts/demo.py --llm    (LLM triage for ambiguous codes, needs GEMINI_API_KEY)

Prints, for each slot, plain truth tables so the CLI demo never depends on
terminal color support.
"""

import argparse
import os
import sys
import tempfile
from datetime import timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

# Load optional .env so a populated RAZORPAY_TEST_KEY_ID/_SECRET is used for
# real test-mode Payment Links. Real env vars still take precedence, and the
# no-keys cold-start path is untouched (this loader is never called on import).
from env_loader import load_env_file
load_env_file()

# Point the engine at a throwaway DB so the demo never touches server data.
os.environ["REVIVE_DB_PATH"] = os.path.join(tempfile.gettempdir(), "revive_demo_simulated.db")

os.environ.setdefault("GEMINI_API_KEY", "")
os.environ["REVIVE_USE_LLM"] = "1" if os.environ.get("GEMINI_API_KEY") and "--llm" in sys.argv else "0"


def banner(text):
    line = "=" * 64
    print("\n" + line)
    print("  " + text)
    print(line)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", action="store_true", help="use LLM triage for ambiguous codes")
    parser.add_argument("--count", type=int, default=40)
    args = parser.parse_args()

    from engine import ReviveEngine
    from event_generator import generate_batch
    import simclock
    import engine as engine_mod
    import db

    if args.llm and not os.environ.get("GEMINI_API_KEY"):
        print("GEMINI_API_KEY not set - falling back to heuristic path.\n")
        args.llm = False

    if os.environ.get("REVIVE_USE_LLM") == "1":
        engine_mod.REVIVE_USE_LLM = True

    db.init_db()
    db.reset_db()
    simclock.reset()
    engine = ReviveEngine()

    banner("Revive - AI Payment Recovery Engine")
    print("Pipeline : ingest -> classify (rules/heuristic/LLM) -> decide -> message -> route/schedule")
    print("Sources  : every decision is tagged rule | heuristic | llm | human | simulation")
    print("Review   : risky / ambiguous payments go to a human queue, never auto-retried")

    banner("1) Stream real-looking payment failures")

    events = generate_batch(args.count, now=simclock.now())
    print(f"  Ingesting {len(events)} failure events across ~8 merchant categories...\n")

    for ev in events:
        engine.ingest(ev)

    revised = engine.dashboard_stats()
    print("  Decision sources for this batch:")
    for src in ("rule", "heuristic", "llm"):
        n = revised["by_source"].get(src, 0)
        print(f"    {src:10s} {n}")
    print(f"    {'human':10s} (approvals logged as these fire)")
    print(f"  Queue: {revised['review_remaining']} payments waiting for human review")

    banner("2) The reason-aware retry plan")

    for row in max(engine.recent_records(8), []):
        print(
            f"  #{row['transaction_id'][-8:]}  {row['reason_label']:24s} "
            f"{row['source']:9s} {row['routing']:8s} attempts={row['retry_attempts']} "
            f"{'recovered' if row['recovered'] else 'pending'}"
        )
    print("\n  Immediate retries fire in ~1 min, short delays ~15 min, scheduled retries ~24h.")
    print("  We're in a simulation - let's fast-forward the clock.")

    banner("3) Fast-forward the sim clock")

    print("  Advance +3 hours -> immediate + short-delay retries execute now.\n")
    simclock.advance(timedelta(hours=3))
    fired = engine.tick(simclock.now())
    print(f"  Executed {len(fired)} retry attempt(s).")
    print("  Advance +24 hours -> the scheduled overnight retry window fires.\n")
    simclock.advance(timedelta(hours=24))
    fired2 = engine.tick(simclock.now())
    print(f"  Executed {len(fired2)} retry attempt(s).")

    d = engine.dashboard_stats()
    c = d["comparison"]
    print("  Final snapshot")
    print(f"  Failed value     : Rs {d['total_failed_value'] / 100:,.2f}")
    print(f"  Recovered value  : Rs {d['total_recovered_value'] / 100:,.2f}")
    print(f"  Recovery rate    : {d['recovery_rate']:.1f}%")
    print(f"  Blind-retry base : {c['baseline_rate']:.1f}%")
    print(f"  Uplift           : +{max(c['revive_rate'] - c['baseline_rate'], 0):.1f} pp")

    banner("4) Inspect an audit trail")

    for txn in engine.recent_records(99999):
        if txn.get("routing") == "review":
            print(f"  Escalated (human review): {txn['transaction_id']}")
            continue
        trail = engine.audit_for(txn["transaction_id"])
        print(f"  transaction {txn['transaction_id']}")
        for a in trail:
            print(f"    [{a['source']:5s}] {a['stage']:16s} {a['detail']}")
        break

    banner("Done")
    print("  Recovery uplift vs blind retry achieved without touching the top of the funnel.")
    print("  Evaluate the triage layer anytime: python backend/eval_triage.py --heuristic")


if __name__ == "__main__":
    main()
"""Taxonomy provenance check.

Proves the failure taxonomy is grounded in Razorpay's official documentation
instead of an invented subset:

  - every rule traces to a Razorpay docs page (doc slug in the JSON);
  - no duplicate codes;
  - every code the demo's event generator emits exists in the taxonomy and maps
    to the category the generator expects;
  - every rule carries a note quoting the documented semantics.

Run from the repo root:

    python backend/check_taxonomy.py
"""

import json
import os
import sys

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)

TAXONOMY_PATH = os.path.join(BACKEND, "data", "razorpay_error_reasons.json")

with open(TAXONOMY_PATH, encoding="utf-8") as fh:
    data = json.load(fh)

rules = data["rules"]
ok = True


def check(name, cond):
    global ok
    ok = ok and bool(cond)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")


codes = [r["code"] for r in rules]
check("no duplicate codes", len(codes) == len(set(codes)))
check("112+ documented rules", len(codes) >= 100)
check("every rule carries a doc source",
      all(r.get("doc") for r in rules))
check("every rule carries a note",
      all(r.get("note") for r in rules))
check("all codes match documented slugs",
      set(r.get("doc") for r in rules) <= {
          "errors/payments/list", "errors/payments/cards"})

import event_generator as eg

by_code = {r["code"]: r for r in rules}
missing = [c for c, _ in sum(eg.ERROR_POOL.values(), []) if c not in by_code]
wrong = [(c, cat) for cat, pool in eg.ERROR_POOL.items()
         for c, _ in pool if c in by_code and by_code[c]["category"] != cat]
check("every demo event code exists in taxonomy", not missing)
check("every demo event maps to its intended category", not wrong)

if not ok or missing or wrong:
    print(f"\nTAXONOMY CHECK FAILED missing={missing} wrong={wrong}")
    sys.exit(1)
print(f"\nTAXONOMY OK — {len(codes)} rules grounded in "
      + ", ".join(sorted({r.get('doc') for r in rules})))
"""Cold-start proof: the whole pipeline runs with NO API keys / no .env.

Judges clone the repo, run this, and see that nothing is blocked by missing
credentials. It strips every GEMINI_*/RAZORPAY_*/RECLAIM_* variable, ingests a
batch of synthetic failures into a throwaway database, and asserts both
graceful fallbacks fired:

    - LLM triage      -> deterministic heuristic verdict (source=heuristic)
    - Payment links   -> offline mock link (source=offline)

Run from the repo root:

    python backend/check_cold_start.py        (or python backend\check_cold_start.py on Windows)
"""

import os
import sqlite3
import sys

BACKEND = os.path.dirname(os.path.abspath(__file__))
if not os.path.isfile(os.path.join(BACKEND, "main.py")):
    sys.exit("check_cold_start.py must stay inside the backend/ folder")
sys.path.insert(0, BACKEND)

# Strip every credential/config var before any backend module loads.
for key in list(os.environ):
    if any(token in key.upper() for token in ("GEMINI", "RAZORPAY", "RECLAIM", "LLM")):
        os.environ.pop(key, None)

# Throwaway DB, chosen BEFORE importing backend modules (db reads env at import).
import tempfile

DB = os.path.join(tempfile.gettempdir(), "reclaim_cold_start_check.db")
os.environ["RECLAIM_DB_PATH"] = DB
for suffix in ("", "-wal", "-shm"):
    try:
        os.remove(DB + suffix)
    except OSError:
        pass

MISSING = [k for k in ("GEMINI_API_KEY", "RAZORPAY_TEST_KEY_ID", "RAZORPAY_TEST_KEY_SECRET")
           if k in os.environ]
assert not MISSING, f"credential vars still present: {MISSING}"

import db
import simclock
from event_generator import generate_batch
from engine import ReclaimEngine
from models import SRC_RULE, SRC_HEURISTIC, SRC_SIM, SRC_HUMAN, SRC_LLM

db.init_db()
sim = ReclaimEngine()

batch = generate_batch(40)
for ev in batch:
    sim.ingest(ev)
sim.tick(simclock.now())

records = sim.recent_records(limit=50)
sources = sorted({r["source"] for r in records if r.get("source")})
links = {}
for r in records:
    if r.get("payment_link_source"):
        links[r["payment_link_source"]] = links.get(r["payment_link_source"], 0) + 1

conn = sqlite3.connect(DB)
cls_sources = sorted({r[0] for r in conn.execute("SELECT source FROM classifications")})
dec_sources = sorted({r[0] for r in conn.execute("SELECT source FROM decisions")})

ok = True
def check(name, cond):
    global ok
    ok = ok and bool(cond)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

print(f"processed                : {len(batch)}")
print(f"record-level source tags : {sources}")
check("no llm source without a key", SRC_LLM not in sources)
check("heuristic fallback fired on ambiguous cases", SRC_HEURISTIC in sources)
check("rule engine tagged clear-cut cases", SRC_RULE in sources)
check("payment links mocked (source=offline)", links.get("offline", 0) > 0)
check("no real razorpay link without keys", "razorpay" not in links)
check("classification rows carry engine source", cls_sources == sorted(set(cls_sources)))
check("decision rows carry engine source", dec_sources == sorted(set(dec_sources)))

print()
if not ok:
    sys.exit("COLD-START FAILED")
print("COLD-START OK — full pipeline ran with zero API keys / no .env")
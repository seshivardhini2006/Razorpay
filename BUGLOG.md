# BUGLOG — Reclaim

Usual practice for this repo: when a bug is found during development, it goes here
with the root cause and the fix. Kept small on purpose — decision log, not a novel.

---

## 2026-09-05 — `outcomes` list treated as a dict in `engine.dashboard_stats`

**Symptom:** `AttributeError: 'list' object has no attribute 'get'` on `/dashboard`.

**Root cause:** `db.all_outcomes()` returns a list of row dicts, but the stats code
called `outcomes.get(txn_id)` as if it were a dict keyed by transaction id.

**Fix:** build a lookup dict at the call site:
`outcomes = {o["transaction_id"]: o for o in db.all_outcomes()}`.

---

## 2026-09-05 — same list-vs-dict bug in `engine.recent_records`

**Symptom:** `AttributeError: 'list' object has no attribute 'get'` on `/records`.

**Root cause:** identical pattern — `all_outcomes()` returns a list; `recent_records`
used `.get(txn)`.

**Fix:** same dict comprehension. A grep pass later confirmed **both** call sites
(engine.py:318 and engine.py:428) were converted, since the two endpoints failed
independently.

---

## 2026-09-05 — `ClassificationResult.explanation` becomes a tuple

**Symptom:** `pydantic ValidationError: Input should be a valid string` in
`test_substring_heuristic_fallback` / `test_explanation_always_present` (2 of 21
pytest cases failed).

**Root cause:** a trailing comma after a parenthesized f-string expression made the
value a 2-tuple:
`explanation=(f"Code '{code}' ... "."),)` — the comma created `("…",)`.

**Fix:** removed the stray trailing comma. All 21 tests green after the fix.

---

## 2026-09-05 — `recent_records.retry_attempts` counted *scheduled* (not executed) attempts

**Symptom:** the dashboard feed showed "1 attempt" on rows whose retry had not yet
executed; the CLI demo showed `attempts=1` before any clock advance.

**Root cause:** `retry_attempts = len(db.get_attempts(txn))` counts every `retry_attempts`
row — including attempt 1 written at **scheduling** time, before execution.

**Fix:** count only executed rows, and expose the raw count separately as
`attempts_scheduled`:
`"retry_attempts": sum(1 for a in all_attempts if a.get("executed"))`.

---

## 2026-09-05 — CLI demo called `engine.advance()`, which does not exist

**Symptom:** `AttributeError: 'ReclaimEngine' object has no attribute 'advance'`.

**Root cause:** the demo assumed a convenience method; the advanced engine deliberately
separates the clock (`simclock.advance(delta)`) from the execution step
(`engine.tick(sim_now)`), mirroring the `/sim/advance` endpoint.

**Fix:** demo now mirrors the API flow exactly: advance the clock, then `engine.tick()`.

---

## Process note — demo must not touch server data

The CLI demo shares the same module tree as the server. Running it wiped/leaked into
`backend/reclaim.db` during development. Fix: `db.py` honors `RECLAIM_DB_PATH`, and
`scripts/demo.py` points it at a temp file and initializes+resets its own schema
(`db.init_db()` then `db.reset_db()`) before replaying the lifecycle.
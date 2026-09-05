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

---

## 2026-09-05 — unavailable LLM mislabeled as `source=llm`

**Symptom:** when `GEMINI_API_KEY` was set but the call failed (bad key / network),
`_llm_triage` returned a fallback dict tagged `source=llm`, so the audit trail and
eval summary claimed an LLM decision that never happened.

**Root cause:** the exception handler returned a proposal instead of deferring to the
heuristic path; `triage()` only used the heuristic when the LLM path returned `None`.

**Fix:** `_llm_triage` now returns `None` on any exception (and logs one line to
stderr), so `triage()` falls through to the heuristic. `source` is now always truthful
(`llm` only when a genuine call succeeded).

---

## 2026-09-05 — "AI proposes, rules dispose" gate added

**Context:** the LLM could propose any category and its message was used verbatim;
safety lived only at execution/decision time. If an LLM proposed an auto-retry category
for a risk/UPI case, nothing at the classification boundary pushed back — and the eval
"safe action" guarantee rested on trusting the model.

**Fix:** `apply_rules_dispose()` now runs on *every* triage output (LLM and heuristic):
category whitelist clamp, risk/decline/fraud signal override -> ambiguous, UPI ->
ambiguous, and overruled proposals lose their drafted message. `rules_disposed` is
carried on the classification for audit/eval visibility. Locked in by tests in
`tests/test_triage.py` (7 new cases, including a mocked real Gemini call) — suite now
28/28 passing.

---

## 2026-09-05 — migration helper indentation swallowed `log_audit`

**Symptom:** after inserting `_migrate()` into `init_db` with a tab mistake, the
`log_audit` body got nested under an `if`/`with` block that referenced undefined
variables (`transaction_id`, `conn`), so audits would raise instead of write.

**Root cause:** the `_migrate(conn)` call and `conn.close()` were appended in the wrong
spot, leaving a dangling `conn.close()` on a closed connection and shifting `log_audit`
inside the wrong scope.

**Fix:** moved `_migrate()` to the top level and made `log_audit` a standalone function
again (see `backend/db.py`). This surfaced while adding the `messages` table columns
(`payment_link_id`, `payment_link_url`, `payment_link_source`) for the Razorpay
Payment-Links integration.

---

## 2026-09-05 — real Payment Links execution step added

**Context:** the engine's execution was fully synthetic, so nothing ever hit a real
(provider) API. Task: at minimum integrate Razorpay's test-mode Payment Links API for
the "prompt for new card" / retry-link flows.

**Fix:** new `backend/razorpay_payments.py` issues a real `POST /v1/payment_links`
(Basic auth from `RAZORPAY_TEST_KEY_ID`/`_SECRET`) for customer-completion categories
(`expired_card`, `otp_timeout`, `wrong_cvv_pin`, `network_drop`, `insufficient_funds`),
inset the short URL into the recovery message, and persist
`payment_link_{id,url,source}` per transaction (migrated table) + a `payment_link`
audit stage. Three-way graceful degradation: no keys → offline mock, success →
`source=razorpay`, API failure → `None` (plain template, never a fake link). Events stay
synthetic. Covered by 5 new tests in `backend/tests/test_payment_links.py` (> real
request shape, Basic auth, failure fallback); suite now 34/34 passing.

---

## 2026-09-05 — no-keys cold start must never dead-end a judge

**Concern:** anyone cloning and running the repo without `.env` could hit a wall in
three places — an LLM triage call that demands `GEMINI_API_KEY`, a Payment Link step
that demands `RAZORPAY_TEST_KEY_ID`/`_SECRET`, and unlabeled decisions that make the
failover invisible.

**Status:** all three were already built as two-branch fallbacks and nothing loads
`.env` at all (env is read via `os.environ`), so cold start is inherently safe.
Verified end-to-end with every credential var stripped from the environment:

- ambiguous failure codes → deterministic heuristic default action + template
  message (`source=heuristic`), never a dead API call;
- completion flows → offline mock payment link (`source=offline`);
- every classification/decision/link row is tagged `rule | heuristic | llm | human |
  simulation`, so the exact engine that produced each record is queryable;
- a failed LLM call (bad key / network) degrades to the heuristic, never a 500.

**Locked in:** `backend/check_cold_start.py` (7 checks, all PASS) + README
"No API keys? It still runs." section. Full suite 34/34 passing.

---

## 2026-09-05 — taxonomy grounded in Razorpay's real documented failure codes

**Symptom:** the failure table (`razorpay_error_reasons.json`) read as *hand-picked for
the demo* — codes like `low_balance`, `bank_timeout`, `risk_blocked` looked plausible
but weren't traceable to a real Razorpay API list, and the README called it
"documented-style".

**Fix:** rebuilt the taxonomy from Razorpay's actual published pages —
`docs/errors/payments/list` (Bad Request + Gateway error reasons) and
`docs/errors/payments/cards` — and mapped **every** listed `error_reason` into one of
Reclaim's 8 recovery categories. Each of the 112 rules now carries:
- `doc` — the exact docs slug it came from;
- `note` — the documented meaning (not an invented gloss);
- a category + confidence reflecting how recoverable the doc description is.

The demo's event generator was switched to emit only these documented codes (the
`insufficient_balance` / `bank_timeout` / `risk_blocked` pseudo-codes are gone), so
every dashboard record traces to a real Razorpay error reason. Provenance is enforced
by `backend/check_taxonomy.py` (7 checks, all PASS). Suite still 34/34.
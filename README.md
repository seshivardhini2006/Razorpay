# Reclaim — AI-Powered Payment Recovery Engine

**Convert failed payments from dead ends into recovered revenue.**

Every payment gateway loses meaningful transaction value to **failed-but-recoverable
payments** — not fraud, not disputes, just payments that failed for fixable reasons
(insufficient funds, expired cards, OTP timeouts, bank downtime, network drops).
Traditional retry systems are *blind*: the same message, the same timing, the same
channel, regardless of *why* the payment failed.

**Reclaim** is an AI agent layered on top of payment infrastructure that:

1. **Diagnoses** why a payment failed
2. **Decides** the smartest way and time to retry it
3. **Communicates** with the customer in plain language to complete it
4. **Reports** recovered revenue back to the merchant in real time

Every decision is **attributed to a source** (`rule | heuristic | llm | human | simulation`),
persisted in an append-only audit trail, and guarded by hard safety bounds scoped to
the entire life of a transaction.

> Built for the **Razorpay AI Builder Internship 2026 — Track 3: AI Revenue Recovery**.

---

## Table of Contents

- [Why It Matters](#why-it-matters)
- [Architecture](#architecture)
- [Component Overview](#component-overview)
- [Failure Taxonomy & Retry Policies](#failure-taxonomy--retry-policies)
- [The Triage Layer (LLM + Evals)](#the-triage-layer-llm--evals)
- [Human Review Queue](#human-review-queue)
- [Safety Bounds](#safety-bounds)
- [Sim Clock & Persistence](#sim-clock--persistence)
- [Getting Started](#getting-started)
- [API Reference](#api-reference)
- [Testing & Evaluation](#testing--evaluation)
- [Deployment](#deployment)
- [Dashboard Overview](#dashboard-overview)
- [Project Structure](#project-structure)

---

## Why It Matters

A large share of failed transactions are **not** due to fraud or user abandonment —
they stem from transient, fixable issues:

| Failure Category | Example Cause | Recoverable? |
|---|---|---|
| Insufficient Funds | Low balance at debit time | Yes — retry later |
| Bank Server Downtime | Issuing-bank API timeout | Yes — retry shortly |
| OTP Timeout | Customer missed the OTP window | Yes — prompted retry |
| Expired Card | Card expired since last use | Yes — needs a new card, not a blind retry |
| Wrong CVV/PIN | User input error | Yes — corrected retry |
| Network Drop | Connectivity mid-transaction | Yes — immediate retry |
| Risk/Fraud Block | Bank/Razorpay risk engine blocked it | No — out of scope |

Reclaim is **reason-aware** — the recovery strategy adapts to *why* a payment failed —
unlike conventional dunning tools that are reason-blind and channel-generic.

---

## Architecture

```
                        Merchant Payment Systems / Webhooks
                                      │
                Failure Event (transaction_id, amount, bank,
                error_code, method, retry_count, …)
                                      │
                                      ▼
        ┌─────────────────────────────────────────────────────────┐
        │  [0] Ingestion + Persistence (SQLite, WAL)              │
        │      append-only audit_log: ingest → … → recovered      │
        └─────────────────────────────────────────────────────────┘
                                      │
                                      ▼
        ┌─────────────────────────────────────────────────────────┐
        │  [1] Failure Classifier                                 │
        │      Layer 1 — exact match on taxonomy                  │
        │              backend/data/razorpay_error_reasons.json   │
        │      Layer 2 — substring heuristic                      │
        │      Layer 3 — rule-based triage:                       │
        │              clear-cut  → classify deterministically   │
        │              ambiguous  → optional LLM triage (Gemini) │
        │                          or heuristic fallback         │
        │      Output: reason + confidence + explanation + source │
        └─────────────────────────────────────────────────────────┘
                                      │
                                      ▼
        ┌─────────────────────────────────────────────────────────┐
        │  [2] Retry Decision Engine (per merchant config)        │
        │      • Timing policy  (immediate / short-delay /        │
        │        scheduled / no-retry)                            │
        │      • Channel policy (same / alternate method)         │
        │      • Attempt caps, sensitivity, attempt-number aware  │
        │      Output: routing + reasoning + source               │
        └─────────────────────────────────────────────────────────┘
                  │                              │
         auto     ▼                              ▼   review
   ┌─────────────────────────┐        ┌────────────────────────────┐
   │ [3] Recovery Agent       │        │ [4] Human Review Queue     │
   │     • reason-aware       │        │     risk / ambiguous cases │
   │       personalized text  │        │     never auto-retried;    │
   │     • simulated          │        │     an operator approves   │
   │       SMS/WhatsApp/email │        │     one retry or dismisses │
   │     • one-tap retry CTA  │        └────────────────────────────┘
   └───────────┬─────────────┘
               ▼
        ┌──────────────────────────────────────────────────────────┐
        │  [5] Scheduler + Sim Clock                              │
        │      attempts land at deliberate future times;           │
        │      advancing the clock fires only attempts now due,    │
        │      within hard bounds                                 │
        └──────────────────────────────────────────────────────────┘
               ▼
   Retry Attempt Outcome (Success / Failure / Escalation)
               │
               ▼
   Feedback loop refines future classification & decisions
```

### Design Principles

- **Event-driven** — mirrors real webhook systems, so Reclaim slots in as an
  *augmentation layer* rather than a replacement for existing infrastructure.
- **Explainable by default** — every classification and every retry decision
  carries human-readable reasoning complemented by an append-only audit trail
  (`GET /audit/{txn_id}` replays the full lifecycle).
- **Rules-first, ML-ready** — deterministic, auditable rules lead; an optional
  LLM handles the genuinely ambiguous tail, and only when a deterministic
  heuristic disagree; everything is guarded by an eval harness.
- **Safe by construction** — hard bounds cap automatic attempts, enforce
  cooldowns, ban retries on non-retryable categories, and route anything risky
  to human review *before* any money movement is attempted.
- **Source attribution** — every record carries a `source` tag
  (`rule | heuristic | llm | human | simulation`) so no black box hides.

---

## Component Overview

| Component | Responsibility | Implementation |
|---|---|---|
| **Event Simulator** | Generates realistic synthetic failures (weighted reasons, banks, merchants, subscription/purchase mix); ~8% genuinely ambiguous | `backend/event_generator.py` |
| **Failure Taxonomy** | Human-curated mapping of documented-style failure codes → categories, labeled by source of truth | `backend/data/razorpay_error_reasons.json` |
| **Failure Classifier** | 3-layer rules: exact taxonomy → substring heuristic → rule/LLM triage; outputs reason + confidence + explanation + source | `backend/classifier.py` |
| **Triage Layer** | Opt-in LLM (Gemini) for ambiguous codes only; drafts category + customer message; hard rules-dispose gate overrules unsafe proposals; heuristic fallback when keyless | `backend/triage.py` |
| **Retry Decision Engine** | Timing, channel, attempt caps from a configurable policy table; per-merchant overrides; attempt-number awareness | `backend/retry_engine.py`, `backend/policies.py` |
| **Safety Bounds** | Hard caps (max 4 auto attempts), cooldowns (5-min min), no-retry set, review-required set | `backend/bounds.py` |
| **Recovery Agent** | Generates personalized, plain-language customer messages from reason-aware templates | `backend/recovery_agent.py` |
| **Persistence + Audit** | SQLite (WAL); tables for events, classifications, decisions, messages, attempts, outcomes, review items, merchants, and an append-only `audit_log` | `backend/db.py` |
| **Core Engine** | Orchestrates the pipeline; scheduler fires only attempts now due; simulates Reclaim vs. blind-retry outcomes | `backend/engine.py` |
| **Sim Clock** | Fast-forwards the scheduler (advance by hours; reset) so retries execute on-demand while replaying the real flow | `backend/simclock.py` |
| **API Layer** | FastAPI endpoints for ingestion, dashboard, records, audit, review, merchant config, sim clock, evals | `backend/main.py` |
| **Dashboard** | Live counters, sim clock controls, review queue, audit drawer, source tags, merchant config editor | `frontend/` (React + Recharts) |
| **Eval Harness** | 12 hand-labeled ambiguous cases × 4-check rubric; heuristic baseline (12/12 passing) | `backend/eval_triage.py` |
| **Tests** | pytest suite for classifier, policies, retry decisions, bounds | `backend/tests/` |
| **Data Models** | Typed schemas for events, classifications, decisions, messages, review items, merchant config | `backend/models.py` |

---

## Failure Taxonomy & Retry Policies

The taxonomy of documented-style failure codes lives in
`backend/data/razorpay_error_reasons.json` (~70 codes across categories, each tagged
with its source of truth: *Documented code* / *Observed in outage* / *Legacy alias* /
*Inferred*). Categories with intentionally *ambiguous* codes route to the triage layer.

Every category maps to a distinct recovery strategy in `backend/policies.py`:

| Reason | Sample Error Codes | Retry Timing | Channel Strategy | Max |
|---|---|---|---|---|
| Insufficient Funds | `low_balance`, `insufficient_funds` | Scheduled (next day) | Same method | 3 |
| Bank Server Downtime | `bank_unreachable`, `gateway_timeout` | Short delay (15 min) | Same method | 3 |
| OTP Timeout | `otp_expired`, `otp_delivery_failed` | Immediate | Same method | 2 |
| Wrong CVV/PIN | `wrong_cvv`, `invalid_pin` | Immediate | Same method | 2 |
| Network Drop | `connection_dropped`, `network_issue` | Immediate | Same method | 2 |
| Expired Card | `card_expired` | No blind retry | **Prompt for new card** | 0 |
| Risk / Fraud Block | `risk_blocked`, `fraud_suspected` | No retry | **Human review** | 0 |
| Needs Triage | *(ambiguous codes)* | No auto retry | **Human review** | 0 |

Per-merchant overrides (attempt caps, sensitivity, risky auto-retry toggle, message
channel) are stored in SQLite and exposed via `GET/PUT /merchants/{id}`.

## The Triage Layer (LLM + Evals)

**AI proposes, rules dispose.** The classifier has four buckets — exact taxonomy rule,
substring heuristic rule, taxonomy-declared-ambiguous, and unmapped generic codes
(`PAYMENT_FAILED`, `CARD_DECLINED` without detail). Only the **ambiguous buckets**
ever touch the LLM; the clear-cut tail stays deterministic and never pays an LLM call.

- `classifier.py` resolves the two rule buckets by itself (`source=rule`).
- For ambiguous buckets, `triage.py` follows this two-branch shape:
  - **If `GEMINI_API_KEY` is set** → call the real LLM (Gemini REST), parse its JSON
    response: a **category** (which the policy table turns into a timing/channel
    strategy) plus a **drafted customer recovery message**.
  - **Else** → a deterministic heuristic picks a *reasonable default action* (category)
    and returns the **template message** for that action.
- **Every proposal then passes `apply_rules_dispose()`** — hard rules overrule the AI in
  code: category must be in the allowed set, risk/decline/fraud signals force
  `ambiguous` (human review) no matter what the LLM said, and UPI is never blind
  auto-retried. When rules dispose a proposal, its message draft is discarded too and
  the deterministic template wins.
- Bounds (`bounds.py`) and policies are enforced **after** the LLM at decision and
  execution time, so no AI output can ever bypass an attempt cap or cooldown.

Live one-shot proof (one real LLM call, end to end):

```bash
$env:GEMINI_API_KEY="your_key"     # then:
python scripts/probe_llm.py        # proposal -> dispose verdict -> governed decision -> message
python scripts/probe_llm.py --dry  # wiring only, no network
```

The eval harness (`backend/eval_triage.py`) scores the triage step on 12 hand-labeled
ambiguous cases with a 4-check rubric (safe action, valid category, sound reasoning,
actionable message). The heuristic baseline passes **12/12**, and every run records
each proposal's `source` plus whether the rules disposed it.

```bash
# deterministic baseline, no key needed
python backend/eval_triage.py --heuristic

# with GEMINI_API_KEY set, scores the live LLM path too
python backend/eval_triage.py --sample 0
```

## Human Review Queue

Anything risky or uncertain — `risk_fraud_block`, `do_not_honor`, and ambiguous codes —
is routed to a **human review queue** (`POST /review/approve`, `POST /review/dismiss`).
Only an explicit human approval permits a retry attempt for these, and that attempt is
tagged `source=human` in the audit trail. No risky payment is ever auto-retried.

## Safety Bounds

`backend/bounds.py` enforces hard, code-level limits (independent of policy config):

- `GLOBAL_MAX_AUTOMATIC_ATTEMPTS = 4` per transaction across its entire life.
- `MIN_COOLDOWN_MINUTES_BETWEEN_ATTEMPTS = 5`.
- `HARD_NO_RETRY_CATEGORIES` — blocked categories can never be auto-retried.
- `REVIEW_REQUIRED_CATEGORIES` — risk categories always escalate to human review.

The scheduler double-checks bounds at *execution* time, not just at decision time, so
late policy changes can never cause an unsafe retry.

## Sim Clock & Persistence

- **Sim clock** (`backend/simclock.py`) drives the scheduler: retries are placed at
  deliberate future times; `POST /sim/advance {"hours": N}` moves the clock and
  executes *only attempts now due*. This makes the retry lifecycle observable without
  waiting a real day.
- **Persistence** (`backend/db.py`) stores every pipeline artifact in SQLite (WAL).
  `GET /audit/{txn_id}` returns the complete append-only trail
  `ingest → classify → decide → message → route → execute → recovered/reschedule`.

---

## Getting Started

### Prerequisites

- **Python 3.11+**
- **Node.js 18+** (with npm)

### Quick Start

**1. Install dependencies**

```powershell
# Backend (into project venv)
..\venv\Scripts\pip install -r backend\requirements.txt
```

```powershell
# Frontend
cd frontend
npm install
```

**2. Start the backend (FastAPI → :8000)**

```powershell
cd backend
..\venv\Scripts\python -m uvicorn main:app --port 8000
```

**3. Start the frontend (Vite + React → :5173)**

```powershell
cd frontend
npm run dev
```

**4. Open the dashboard**

Visit **http://localhost:5173** and click **⚡ Stream failure events** to watch
Reclaim diagnose, decide, and recover failed payments — then use the **sim clock**
(＋2h / ＋12h / ＋1 day) to fast-forward retries and watch the recovered-revenue
counter tick up against the blind-retry baseline.

### Optional LLM triage

```powershell
Copy-Item .env.example .env
# then add your GEMINI_API_KEY to .env
```

The engine is fully deterministic without a key (heuristic fallback). With a key, only
ambiguous codes are routed to the LLM, and only for allow-listed merchants.

### CLI Demo

Run the whole lifecycle from the terminal without a browser:

```powershell
python scripts/demo.py            # deterministic path
python scripts/demo.py --llm      # LLM triage (needs GEMINI_API_KEY)
```

### One-Click Start

From the project root:

```powershell
.\start_all.ps1
```

Launches both servers in the background and prints their URLs.

---

## API Reference

Base URL: `http://localhost:8000`

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Service health check (+ current sim time) |
| `POST` | `/events/generate` | `{"count": N}` — generates N synthetic failure events and runs each through classification → decision → messaging → outcome simulation |
| `GET` | `/dashboard` | Aggregated metrics: failed/recovered value, recovery rate, reason & source breakdowns, retry pipeline (scheduled/executed/pending review), cumulative timeline, and Reclaim vs. blind-retry comparison |
| `GET` | `/records?limit=N` | Most recent processed records with reason, source, confidence, routing, timing, attempts, message, and outcome |
| `GET` | `/audit/{txn_id}` | Full append-only audit trail for one transaction |
| `GET` | `/review` | Pending human review queue |
| `POST` | `/review/approve` | `{"item_id": ...}` — allow one retry for an escalated payment (`source=human`) |
| `POST` | `/review/dismiss` | `{"item_id": ...}` — permanently close a review item |
| `GET` | `/merchants/{id}` | Read a merchant's retry config |
| `PUT` | `/merchants/{id}` | `{"config": {...}}` — update sensitivity, attempt caps, channels, risk-retry toggle |
| `POST` | `/sim/advance` | `{"hours": N}` — fast-forward the clock and execute retries now due |
| `POST` | `/sim/reset` | Reset engine, DB, and sim clock |

> Note: the frontend proxies under `/api` (e.g. `/api/dashboard`), configured in
> `frontend/vite.config.js`.

### Example

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/events/generate" `
  -Method Post -ContentType "application/json" `
  -Body '{"count": 50}'
```

---

## Testing & Evaluation

```bash
# entire backend test suite
python -m pytest backend -q

# heuristic triage eval on 12 ambiguous cases (deterministic, no key)
python backend/eval_triage.py --heuristic
```

Verification performed during development:

- 21/21 pytest cases passing (`test_classifier`, `test_bounds`, `test_retry`).
- Eval harness: 12/12 (100%) on the heuristic triage path.
- End-to-end sweep: ingest 80 → review queue (ambiguous + risk) → advance clock →
  immediate + scheduled retries execute → approve a review item → audit trail
  `ingest → classify → decide → message → route → schedule → execute_attempt →
  recovered/reschedule → maxed_out`.

---

## Deployment

- **`.env.example`** — documents `GEMINI_API_KEY`, merchant allow-list, model name.
- **`Dockerfile`** — reproducible backend image (`uvicorn main:app --port 8000`).
- **`render.yaml`** — Render blueprint (free tier, `$PORT` aware).
- **`DB_PATH`** is configurable via `RECLAIM_DB_PATH` (defaults to `backend/reclaim.db`),
  which the CLI demo uses to avoid touching server data.

---

## Dashboard Overview

- **Stat cards** — failed value, recovered value, recovery rate, and the
  percentage-point uplift vs. a blind-retry baseline
- **Sim clock bar** — current simulated time plus **＋2h / ＋12h / ＋1 day** controls to
  fast-forward the scheduler and fire retries on demand
- **Reclaim vs. Blind Retry** — side-by-side recovery comparison over the same
  failed-transaction pool
- **Retry Pipeline** — scheduled attempts, executed attempts, and pending-review count,
  plus source-tag distribution (rule / heuristic / LLM / human)
- **Human Review Queue** — escalated risk/ambiguous payments with approve-one-retry and
  dismiss actions
- **Failure Reasons & Recovery** — value lost and recovered per reason category
- **Recovered Revenue Timeline** — cumulative recovered area chart
- **Live Recovery Feed** — per-transaction view of the classification, source tag,
  chosen retry strategy, and the customer-facing message; click any row to open its
  full audit trail

---

## Demo Narrative

1. **Open with the problem** — a meaningful share of failed payments are recoverable,
   most systems just don't try intelligently.
2. **Stream the failure event feed** — realistic diversity of reasons streaming in,
   including genuinely ambiguous cases.
3. **Show classification in action** — every failure tagged with a reason, confidence,
   a plain-English explanation, and a source tag (`rule | heuristic | llm`).
4. **Show the retry decision** — different reasons get different strategies (the core
   differentiator vs. blind retry).
5. **Show the escalation path** — risk and ambiguous payments land in the human review
   queue instead of being auto-retried; approve one and watch the `source=human` attempt.
6. **Fast-forward the sim clock** — immediates fire at ＋2h, short delays at ＋12h,
   scheduled retries at ＋1 day; the recovered-revenue counter ticks up.
7. **Open an audit trail** — click a feed row to replay `ingest → classify → decide →
   message → route → execute → recovered` with full attribution.
8. **Close with business impact** — recovered % as incremental GMV at near-zero marginal
   cost, with a direct lift for subscription retention.

---

## Project Structure

```
Razorpay/
├── backend/                     # FastAPI application
│   ├── main.py                  # API entrypoint + routes
│   ├── engine.py                # Pipeline orchestration, scheduler, outcomes
│   ├── classifier.py            # 3-layer failure classification (rules/trie/triage)
│   ├── triage.py                # Optional LLM (Gemini) + heuristic fallback
│   ├── retry_engine.py          # Retry decision logic
│   ├── policies.py              # Configurable retry policy table + caps
│   ├── bounds.py                # Hard safety bounds (attempts, cooldowns, no-retry)
│   ├── recovery_agent.py        # Reason-aware customer messaging
│   ├── event_generator.py       # Synthetic failure event simulation
│   ├── simclock.py              # Fast-forwardable simulated clock
│   ├── db.py                    # SQLite persistence + append-only audit trail
│   ├── models.py                # Typed data schemas
│   ├── eval_triage.py           # LLM/heuristic eval harness (rubric-scored)
│   ├── data/
│   │   ├── razorpay_error_reasons.json   # Failure taxonomy
│   │   └── eval_results_*.json           # Eval run outputs
│   ├── tests/                   # pytest suite (classifier, bounds, retry)
│   └── requirements.txt
├── scripts/
│   ├── demo.py                  # Terminal demo of the full recovery lifecycle
│   └── probe_llm.py             # One real LLM triage call (proposal -> dispose -> decision)
├── frontend/                    # React + Vite + Recharts dashboard
│   ├── src/
│   │   ├── App.jsx              # Main dashboard
│   │   ├── index.css            # Dark-themed dashboard styles
│   │   └── main.jsx             # React entrypoint
│   ├── vite.config.js           # Dev server + /api proxy config
│   ├── package.json
│   └── index.html
├── .env.example                 # Optional GEMINI_API_KEY config template
├── Dockerfile                   # Backend container image
├── render.yaml                  # Render deployment blueprint
├── start_all.ps1                # One-click launcher (both servers)
├── venv/                        # Python virtual environment
├── BUGLOG.md                    # Decision log (bugs, fixes, rationale)
└── README.md
```

---

## Security & Data Notes

- **All demo data is synthetic** — no real customer PII is used or stored.
- Customer messages never contain sensitive details (e.g., full card numbers).
- Retry and messaging behavior respects hard caps and cooldowns to stay within
  realistic payment-industry norms.
- Structured to be pluggable into real webhook pipelines without replacing the
  payment gateway, settlement, or risk systems.

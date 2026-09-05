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

> Built for the **Razorpay AI Builder Internship 2026 — Track 3: AI Revenue Recovery**.

---

## Table of Contents

- [Why It Matters](#why-it-matters)
- [Architecture](#architecture)
- [Component Overview](#component-overview)
- [Failure Taxonomy & Retry Policies](#failure-taxonomy--retry-policies)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Quick Start](#quick-start)
  - [One-Click Start](#one-click-start)
- [API Reference](#api-reference)
- [Dashboard Overview](#dashboard-overview)
- [Demo Narrative](#demo-narrative)
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
        ┌─────────────────────────────────────────────────┐
        │  [1] Failure Classifier                          │
        │      • Layer 1 — exact error-code mapping (rules)│
        │      • Layer 2 — substring heuristics            │
        │      • Layer 3 — conservative fallback           │
        │      Output: reason + confidence + explanation   │
        └─────────────────────────────────────────────────┘
                                      │
                                      ▼
        ┌─────────────────────────────────────────────────┐
        │  [2] Retry Decision Engine                       │
        │      • Timing policy  (immediate / short-delay / │
        │        scheduled / no-retry)                     │
        │      • Channel policy (same / alternate method)  │
        │      • Attempt caps + cooldowns                  │
        │      Output: decision + human-readable reasoning │
        └─────────────────────────────────────────────────┘
                 │                              │
                 ▼                              ▼
   ┌────────────────────────────┐   ┌──────────────────────────────┐
   │ [3] Recovery Agent          │   │ [4] Merchant Dashboard       │
   │     • Reason-aware          │   │     • Live revenue counter   │
   │       personalized message  │   │     • Reason breakdown       │
   │     • Sent via simulated    │   │     • Reclaim vs. baseline   │
   │       SMS/WhatsApp/email    │   │       comparison             │
   │     • One-tap retry CTA     │   └──────────────────────────────┘
   └────────────────────────────┘
                 │
                 ▼
        [5] Retry Attempt Outcome (Success / Failure)
                 │
                 ▼
   Feedback loop refines future classification & decisions
```

### Design Principles

- **Event-driven** — mirrors real webhook systems, so Reclaim slots in as an
  *augmentation layer* rather than a replacement for existing infrastructure.
- **Explainable by default** — every classification and every retry decision
  carries human-readable reasoning. Trust matters in a finance-adjacent domain.
- **Rules-first, ML-ready** — deterministic, auditable rules lead; MLL can be added
  as a confidence-scored fallback for unmapped error codes.
- **Compliance-minded** — hard caps on attempts and message frequency.

---

## Component Overview

| Component | Responsibility | Implementation |
|---|---|---|
| **Event Simulator** | Generates realistic synthetic failures (weighted reasons, banks, merchants, subscription/purchase mix) | `backend/event_generator.py` |
| **Failure Classifier** | Maps error codes → failure reason via 3 rule layers, outputs confidence + explanation | `backend/classifier.py` |
| **Retry Decision Engine** | Selects timing, channel, and attempt limits from a configurable policy table | `backend/retry_engine.py`, `backend/policies.py` |
| **Recovery Agent** | Generates personalized, plain-language customer messages from reason-aware templates | `backend/recovery_agent.py` |
| **Core Engine** | Orchestrates the pipeline and simulates Reclaim vs. blind-retry outcomes | `backend/engine.py` |
| **API Layer** | FastAPI endpoints for ingestion, dashboard stats, and records | `backend/main.py` |
| **Dashboard** | Live recovered-revenue counter, charts, comparison, and event feed | `frontend/` (React + Recharts) |
| **Data Models** | Typed schemas for events, classifications, decisions, and messages | `backend/models.py` |

---

## Failure Taxonomy & Retry Policies

Centralized in `backend/policies.py`. Every reason maps to a distinct recovery strategy:

| Reason | Sample Error Codes | Retry Timing | Channel Strategy | Max |
|---|---|---|---|---|
| Insufficient Funds | `INSUFFICIENT_FUNDS`, `LOW_BALANCE` | Scheduled (next day) | Same method | 3 |
| Bank Server Downtime | `BANK_TIMEOUT`, `GATEWAY_ERROR` | Short delay (15 min) | Same method | 3 |
| OTP Timeout | `OTP_EXPIRED` | Immediate | Same method | 2 |
| Wrong CVV/PIN | `INVALID_CVV`, `INVALID_PIN` | Immediate | Same method | 2 |
| Network Drop | `CONNECTION_ERROR` | Immediate | Same method | 2 |
| Expired Card | `CARD_EXPIRED` | No blind retry | **Prompt for new card** | 1 |
| Risk / Fraud Block | `RISK_BLOCKED` | No retry | Escalate (out of scope) | 0 |

Global safeguards: a hard cap of **4 automatic attempts per transaction** and
**cooldown periods** between attempts (5 min minimum).

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
Reclaim diagnose, decide, and recover failed payments in real time — the recovered
revenue counter ticks up live, alongside the blind-retry baseline comparison.

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
| `GET` | `/health` | Service health check |
| `POST` | `/events/generate` | `{"count": N}` — generates N synthetic failure events and runs each through classification → decision → messaging → outcome simulation |
| `GET` | `/dashboard` | Aggregated analytics: failed vs. recovered value, recovery rate, reason/method breakdowns, cumulative timeline, and Reclaim vs. blind-retry comparison |
| `GET` | `/records?limit=N` | Most recent processed records with reason, confidence, explanation, strategy, customer message, and outcome |

> Note: the frontend proxies under `/api` (e.g. `/api/dashboard`), configured in
> `frontend/vite.config.js`.

### Example

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/events/generate" `
  -Method Post -ContentType "application/json" `
  -Body '{"count": 50}'
```

---

## Dashboard Overview

- **Stat cards** — failed value, recovered value, recovery rate, and the
  percentage-point uplift vs. a blind-retry baseline
- **Reclaim vs. Blind Retry** — side-by-side recovery comparison over the same
  failed-transaction pool
- **Failure Reasons & Recovery** — value lost and recovered per reason category
- **Recovered Revenue Timeline** — cumulative recovered area chart
- **Live Recovery Feed** — per-transaction view of the classification, chosen retry
  strategy, and the customer-facing message

---

## Demo Narrative

1. **Open with the problem** — a meaningful share of failed payments are recoverable,
   most systems just don't try intelligently.
2. **Stream the failure event feed** — realistic diversity of reasons streaming in.
3. **Show classification in action** — every failure tagged with a reason, confidence,
   and a plain-English explanation.
4. **Show the retry decision** — different reasons get different strategies (the core
   differentiator vs. blind retry).
5. **Show the customer message** — a personalized, reason-aware nudge side-by-side
   with a generic "payment failed" message.
6. **Show the dashboard** — the recovered-revenue counter ticking up vs. the baseline.
7. **Close with business impact** — recovered % as incremental GMV at near-zero
   marginal cost, with a direct lift for subscription retention.

---

## Project Structure

```
Razorpay/
├── backend/                     # FastAPI application
│   ├── main.py                  # API entrypoint + routes
│   ├── engine.py                # Pipeline orchestration + outcome simulation
│   ├── classifier.py            # Failure reason classification (rules-first)
│   ├── retry_engine.py          # Retry decision logic
│   ├── policies.py              # Configurable retry policy table + caps
│   ├── recovery_agent.py        # Reason-aware customer messaging
│   ├── event_generator.py       # Synthetic failure event simulation
│   ├── models.py                # Typed data schemas
│   └── requirements.txt
├── frontend/                    # React + Vite + Recharts dashboard
│   ├── src/
│   │   ├── App.jsx              # Main dashboard
│   │   ├── index.css            # Dark-themed dashboard styles
│   │   └── main.jsx             # React entrypoint
│   ├── vite.config.js           # Dev server + /api proxy config
│   ├── package.json
│   └── index.html
├── start_all.ps1                # One-click launcher (both servers)
├── venv/                        # Python virtual environment
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

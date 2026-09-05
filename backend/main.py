"""Revive API: FastAPI backend exposing the Revive engine."""

from datetime import timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Load optional .env (repo root/backend) BEFORE db is imported so env-driven
# settings like REVIVE_DB_PATH resolve here too. Never overrides real env vars.
from env_loader import load_env_file
load_env_file()

import db
import simclock
from models import PaymentEvent, MerchantConfig
from engine import engine
from event_generator import generate_batch

app = FastAPI(title="Revive — AI-Powered Payment Recovery Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

db.init_db()


class BatchRequest(BaseModel):
    count: int = 20


class AdvanceRequest(BaseModel):
    hours: float = 1.0


class ReviewRequest(BaseModel):
    item_id: str


class MerchantConfigRequest(BaseModel):
    config: MerchantConfig


class IngestResponse(BaseModel):
    processed: int
    recovered: int
    recovered_value: int
    routed_to_review: int
    records: list


@app.on_event("startup")
def _startup():
    db.init_db()


@app.get("/health")
def health():
    return {"status": "ok", "sim_time": simclock.isoformat()}


@app.post("/events/generate", response_model=IngestResponse)
def generate_and_process(batch: BatchRequest):
    """Generate synthetic failure events and run each through the Revive pipeline."""
    events = generate_batch(max(1, min(batch.count, 300)))
    processed_count = 0
    routed_to_review = 0
    for ev in events:
        record = engine.ingest(ev)
        processed_count += 1
        if record["decision"].routing == "review":
            routed_to_review += 1
    # fire anything scheduled for 'now' (immediate retries)
    engine.tick(simclock.now())

    records = engine.recent_records(limit=50)
    return {
        "processed": processed_count,
        "recovered": sum(1 for r in records if r["recovered"]),
        "recovered_value": sum(r["amount"] for r in records if r["recovered"]),
        "routed_to_review": routed_to_review,
        "records": records,
    }


@app.get("/dashboard")
def dashboard():
    return engine.dashboard_stats()


@app.get("/records")
def records(limit: int = 50):
    return engine.recent_records(limit=limit)


@app.get("/audit/{txn_id}")
def audit(txn_id: str):
    return engine.audit_for(txn_id)


@app.get("/review")
def review_list():
    return engine.review_queue()


@app.post("/review/approve")
def review_approve(req: ReviewRequest):
    return engine.approve_review(req.item_id)


@app.post("/review/dismiss")
def review_dismiss(req: ReviewRequest):
    return engine.dismiss_review(req.item_id)


@app.get("/merchants/{merchant_id}")
def merchant_config(merchant_id: str):
    return db.get_merchant_config(merchant_id)


@app.put("/merchants/{merchant_id}")
def merchant_update(merchant_id: str, req: MerchantConfigRequest):
    cfg = req.config.model_dump()
    cfg["merchant_id"] = merchant_id
    db.upsert_merchant_config(cfg)
    return db.get_merchant_config(merchant_id)


@app.post("/sim/advance")
def sim_advance(req: AdvanceRequest):
    """Fast-forward the simulated clock, then execute every retry now due."""
    new_time = simclock.advance(timedelta(hours=req.hours))
    executed = engine.tick(new_time)
    return {"sim_time": simclock.isoformat(), "executed": executed}


@app.post("/sim/reset")
def sim_reset():
    engine.reset()
    return {"sim_time": simclock.isoformat()}


def _record_to_view(record: dict) -> dict:
    """Serialise a full enriched pipeline record (used by tests / CLI)."""
    ev = record["event"]
    return {
        "transaction_id": ev.transaction_id,
        "amount": ev.amount,
        "bank": ev.bank,
        "method": ev.payment_method,
        "error_code": ev.error_code,
        "reason": getattr(ev, "reason", None),
        "merchant": ev.merchant_name,
        "category": record["classification"].predicted_reason,
        "reason_label": record["classification"].predicted_reason.replace("_", " ").title(),
        "confidence": record["classification"].confidence,
        "source": record["classification"].source,
        "explanation": record["classification"].explanation,
        "retry_timing": record["decision"].retry_timing,
        "retry_channel": record["decision"].retry_channel,
        "routing": record["decision"].routing,
        "reasoning": record["decision"].reasoning,
        "message": record["message"].message,
        "recovered": record["outcome"]["recovered"],
        "baseline_recovered": record["outcome"]["baseline_recovered"],
        "is_subscription": ev.is_subscription,
    }
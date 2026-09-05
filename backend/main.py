"""Reclaim API: FastAPI backend that ties the Reclaim engine together."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from models import PaymentEvent
from engine import engine
from event_generator import generate_batch

app = FastAPI(title="Reclaim — AI-Powered Payment Recovery Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class BatchRequest(BaseModel):
    count: int = 20


class IngestResponse(BaseModel):
    processed: int
    recovered: int
    recovered_value: int
    records: list


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/events/generate", response_model=IngestResponse)
def generate_and_process(batch: BatchRequest):
    """Generate a batch of synthetic failure events and run them through the engine."""
    events = generate_batch(max(1, min(batch.count, 200)))
    records = []
    for ev in events:
        enriched = engine.process_event(ev)
        records.append(enriched)

    view = [_record_to_view(r) for r in records]
    return {
        "processed": len(records),
        "recovered": sum(1 for r in records if r["outcome"]["recovered"]),
        "recovered_value": sum(r["outcome"]["amount"] for r in records if r["outcome"]["recovered"]),
        "records": view,
    }


def _record_to_view(record: dict) -> dict:
    ev = record["event"]
    return {
        "transaction_id": ev.transaction_id,
        "amount": ev.amount,
        "bank": ev.bank,
        "method": ev.payment_method,
        "error_code": ev.error_code,
        "merchant": ev.merchant_name,
        "reason": record["classification"].predicted_reason,
        "reason_label": record["classification"].predicted_reason.replace("_", " ").title(),
        "confidence": record["classification"].confidence,
        "explanation": record["classification"].explanation,
        "retry_timing": record["decision"].retry_timing,
        "retry_channel": record["decision"].retry_channel,
        "reasoning": record["decision"].reasoning,
        "message": record["message"].message,
        "recovered": record["outcome"]["recovered"],
        "baseline_recovered": record["baseline_outcome"]["recovered"],
        "is_subscription": ev.is_subscription,
    }


@app.get("/dashboard")
def dashboard():
    return engine.dashboard_stats()


@app.get("/records")
def records(limit: int = 50):
    return engine.recent_records(limit=limit)
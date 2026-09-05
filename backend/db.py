"""SQLite persistence + append-only audit trail.

Every stage of the pipeline (ingest, classify, decide, message, retry outcome,
human decisions) is written to `audit_log` with the producing engine source,
so any decision can be replayed and attributed.
"""

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

DB_PATH = Path(os.environ.get("REVIVE_DB_PATH", str(Path(__file__).parent / "revive.db")))

_lock = threading.Lock()


def _conn():
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS events (
                transaction_id TEXT PRIMARY KEY,
                merchant_id TEXT, customer_id TEXT, amount INTEGER,
                currency TEXT, payment_method TEXT, bank TEXT,
                error_code TEXT, reason TEXT, timestamp TEXT,
                retry_count INTEGER, customer_name TEXT, customer_email TEXT,
                merchant_name TEXT, is_subscription INTEGER
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS classifications (
                transaction_id TEXT PRIMARY KEY,
                predicted_reason TEXT, confidence REAL,
                explanation TEXT, source TEXT, raw_code TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS decisions (
                transaction_id TEXT PRIMARY KEY,
                category TEXT, should_retry INTEGER, retry_timing TEXT,
                retry_channel TEXT, max_attempts INTEGER, next_retry_at TEXT,
                attempt_number INTEGER, routing TEXT, reasoning TEXT, source TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                transaction_id TEXT PRIMARY KEY,
                message TEXT, channel TEXT, subject TEXT,
                payment_link_id TEXT, payment_link_url TEXT, payment_link_source TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS retry_attempts (
                id TEXT PRIMARY KEY,
                transaction_id TEXT, attempt_number INTEGER,
                scheduled_for TEXT, executed_at TEXT,
                recovered INTEGER, executed INTEGER, source TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS outcomes (
                transaction_id TEXT PRIMARY KEY,
                recovered INTEGER, attempts_used INTEGER,
                recovered_at TEXT, recovered_value INTEGER,
                baseline_recovered INTEGER, category TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS review_queue (
                id TEXT PRIMARY KEY,
                transaction_id TEXT, amount INTEGER, merchant_id TEXT,
                category TEXT, reason TEXT, status TEXT,
                created_at TEXT, decided_at TEXT, decided_by TEXT,
                decided_extra_attempts INTEGER
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS merchant_configs (
                merchant_id TEXT PRIMARY KEY,
                enabled INTEGER, max_attempts_override INTEGER,
                message_channel TEXT, auto_retry_risk INTEGER,
                sensitivity TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_id TEXT, sim_time TEXT, stage TEXT,
                source TEXT, detail TEXT
            )
        """)
        conn.commit()
        _migrate(conn)
        conn.close()


def _migrate(conn) -> None:
    """Forward-migrate older database files (added columns, etc.)."""
    for column in ("payment_link_id", "payment_link_url", "payment_link_source"):
        try:
            conn.execute(f"ALTER TABLE messages ADD COLUMN {column} TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()


def log_audit(transaction_id: str, stage: str, source: str, detail: dict):
    with _lock:
        conn = _conn()
        conn.execute(
            "INSERT INTO audit_log (transaction_id, sim_time, stage, source, detail) VALUES (?,?,?,?,?)",
            (transaction_id, datetime.utcnow().isoformat(), stage, source, json.dumps(detail, default=str)),
        )
        conn.commit()
        conn.close()


def upsert_event(ev) -> None:
    with _lock:
        conn = _conn()
        conn.execute(
            """INSERT OR REPLACE INTO events
               (transaction_id, merchant_id, customer_id, amount, currency,
                payment_method, bank, error_code, reason, timestamp, retry_count,
                customer_name, customer_email, merchant_name, is_subscription)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ev.transaction_id, ev.merchant_id, ev.customer_id, ev.amount, ev.currency,
             ev.payment_method, ev.bank, ev.error_code, ev.reason, ev.timestamp, ev.retry_count,
             ev.customer_name, ev.customer_email, ev.merchant_name, int(ev.is_subscription)),
        )
        conn.commit()
        conn.close()


def upsert_classification(c) -> None:
    with _lock:
        conn = _conn()
        conn.execute(
            """INSERT OR REPLACE INTO classifications
               (transaction_id, predicted_reason, confidence, explanation, source, raw_code)
               VALUES (?,?,?,?,?,?)""",
            (c.transaction_id, c.predicted_reason, c.confidence, c.explanation, c.source, c.raw_code),
        )
        conn.commit()
        conn.close()


def upsert_decision(d) -> None:
    with _lock:
        conn = _conn()
        conn.execute(
            """INSERT OR REPLACE INTO decisions
               (transaction_id, category, should_retry, retry_timing, retry_channel,
                max_attempts, next_retry_at, attempt_number, routing, reasoning, source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (d.transaction_id, d.category, int(d.should_retry), d.retry_timing, d.retry_channel,
             d.max_attempts, d.next_retry_at, d.attempt_number, d.routing, d.reasoning, d.source),
        )
        conn.commit()
        conn.close()


def upsert_message(m) -> None:
    with _lock:
        conn = _conn()
        conn.execute(
            """INSERT OR REPLACE INTO messages
               (transaction_id, message, channel, subject,
                payment_link_id, payment_link_url, payment_link_source)
               VALUES (?,?,?,?,?,?,?)""",
            (m.transaction_id, m.message, m.channel, m.subject,
             m.payment_link_id, m.payment_link_url, m.payment_link_source),
        )
        conn.commit()
        conn.close()


def insert_attempt(attempt: dict) -> None:
    with _lock:
        conn = _conn()
        conn.execute(
            """INSERT OR REPLACE INTO retry_attempts
               (id, transaction_id, attempt_number, scheduled_for, executed_at,
                recovered, executed, source)
               VALUES (?,?,?,?,?,?,?,?)""",
            (attempt["id"], attempt["transaction_id"], attempt["attempt_number"],
             attempt["scheduled_for"], attempt["executed_at"], int(attempt["recovered"]),
             int(attempt["executed"]), attempt["source"]),
        )
        conn.commit()
        conn.close()


def upsert_outcome(o: dict) -> None:
    with _lock:
        conn = _conn()
        conn.execute(
            """INSERT OR REPLACE INTO outcomes
               (transaction_id, recovered, attempts_used, recovered_at,
                recovered_value, baseline_recovered, category)
               VALUES (?,?,?,?,?,?,?)""",
            (o["transaction_id"], int(o["recovered"]), o["attempts_used"], o["recovered_at"],
             o["recovered_value"], int(o["baseline_recovered"]), o["category"]),
        )
        conn.commit()
        conn.close()


def create_review_item(item: dict) -> None:
    with _lock:
        conn = _conn()
        conn.execute(
            """INSERT OR REPLACE INTO review_queue
               (id, transaction_id, amount, merchant_id, category, reason, status,
                created_at, decided_at, decided_by, decided_extra_attempts)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (item["id"], item["transaction_id"], item["amount"], item["merchant_id"],
             item["category"], item["reason"], item["status"], item["created_at"],
             item["decided_at"], item["decided_by"], item.get("decided_extra_attempts", 0)),
        )
        conn.commit()
        conn.close()


def update_review_item(**fields) -> None:
    with _lock:
        conn = _conn()
        sets = ", ".join(f"{k}=?" for k in fields if k != "id")
        vals = [fields[k] for k in fields if k != "id"]
        conn.execute(f"UPDATE review_queue SET {sets} WHERE id=?", vals + [fields["id"]])
        conn.commit()
        conn.close()


def update_outcome(txn_id: str, recovered: bool, recovered_at: str, recovered_value: int = 0) -> None:
    with _lock:
        conn = _conn()
        conn.execute(
            "UPDATE outcomes SET recovered=?, recovered_at=?, recovered_value=? WHERE transaction_id=?",
            (int(recovered), recovered_at, recovered_value, txn_id),
        )
        conn.commit()
        conn.close()


def get_review_queue(status: str = "pending") -> list:
    with _lock:
        conn = _conn()
        rows = conn.execute(
            "SELECT * FROM review_queue WHERE status=? ORDER BY created_at ASC", (status,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]


def get_audit_for(txn_id: str) -> list:
    with _lock:
        conn = _conn()
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE transaction_id=? ORDER BY id ASC", (txn_id,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]


def get_event(txn_id: str):
    with _lock:
        conn = _conn()
        row = conn.execute("SELECT * FROM events WHERE transaction_id=?", (txn_id,)).fetchone()
        conn.close()
        return dict(row) if row else None


def get_outcome(txn_id: str):
    with _lock:
        conn = _conn()
        row = conn.execute("SELECT * FROM outcomes WHERE transaction_id=?", (txn_id,)).fetchone()
        conn.close()
        return dict(row) if row else None


def get_attempts(txn_id: str) -> list:
    with _lock:
        conn = _conn()
        rows = conn.execute(
            "SELECT * FROM retry_attempts WHERE transaction_id=? ORDER BY attempt_number ASC",
            (txn_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]


def get_merchant_config(merchant_id: str) -> dict:
    with _lock:
        conn = _conn()
        row = conn.execute(
            "SELECT * FROM merchant_configs WHERE merchant_id=?", (merchant_id,)
        ).fetchone()
        conn.close()
        if row:
            return dict(row)
        return _default_merchant_config(merchant_id)


def upsert_merchant_config(cfg: dict) -> None:
    with _lock:
        conn = _conn()
        conn.execute(
            """INSERT OR REPLACE INTO merchant_configs
               (merchant_id, enabled, max_attempts_override, message_channel,
                auto_retry_risk, sensitivity)
               VALUES (?,?,?,?,?,?)""",
            (cfg["merchant_id"], int(cfg["enabled"]), cfg.get("max_attempts_override"),
             cfg.get("message_channel", "whatsapp"), int(cfg.get("auto_retry_risk", False)),
             cfg.get("sensitivity", "balanced")),
        )
        conn.commit()
        conn.close()


def _default_merchant_config(merchant_id: str) -> dict:
    return {
        "merchant_id": merchant_id,
        "enabled": 1,
        "max_attempts_override": None,
        "message_channel": "whatsapp",
        "auto_retry_risk": 0,
        "sensitivity": "balanced",
    }


def all_events() -> list:
    with _lock:
        conn = _conn()
        rows = conn.execute("SELECT * FROM events").fetchall()
        conn.close()
        return [dict(r) for r in rows]


def all_outcomes() -> list:
    with _lock:
        conn = _conn()
        rows = conn.execute("SELECT * FROM outcomes").fetchall()
        conn.close()
        return [dict(r) for r in rows]


def all_classifications() -> dict:
    with _lock:
        conn = _conn()
        rows = conn.execute("SELECT * FROM classifications").fetchall()
        conn.close()
        return {r["transaction_id"]: dict(r) for r in rows}


def all_decisions() -> dict:
    with _lock:
        conn = _conn()
        rows = conn.execute("SELECT * FROM decisions").fetchall()
        conn.close()
        return {r["transaction_id"]: dict(r) for r in rows}


def all_messages() -> dict:
    with _lock:
        conn = _conn()
        rows = conn.execute("SELECT * FROM messages").fetchall()
        conn.close()
        return {r["transaction_id"]: dict(r) for r in rows}


def reset_db() -> None:
    with _lock:
        conn = _conn()
        conn.execute("DELETE FROM audit_log")
        for t in ("events", "classifications", "decisions", "messages",
                  "retry_attempts", "outcomes", "review_queue"):
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
        conn.close()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def count_rows(sql: str, params: tuple = ()) -> int:
    with _lock:
        conn = _conn()
        row = conn.execute(sql, params).fetchone()
        conn.close()
        return row[0] if row else 0
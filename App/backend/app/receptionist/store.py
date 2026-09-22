"""Persistence layer for voice receptionist calls and turns."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from .. import database as db
from ..guardrails import redact_pii_text

logger = logging.getLogger(__name__)


def init_receptionist_schema() -> None:
    """Create the voice_calls and voice_call_turns tables if they do not exist."""
    try:
        db.execute_script(
            """
            CREATE TABLE IF NOT EXISTS voice_calls (
                call_id         TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL DEFAULT '',
                user_id         TEXT NOT NULL DEFAULT '',
                tenant_id       TEXT NOT NULL DEFAULT 'default',
                channel         TEXT NOT NULL DEFAULT 'browser_sim',
                locale          TEXT NOT NULL DEFAULT 'en',
                status          TEXT NOT NULL DEFAULT 'ai',
                started_at      DOUBLE PRECISION NOT NULL,
                ended_at        DOUBLE PRECISION,
                end_reason      TEXT NOT NULL DEFAULT '',
                transferred     INTEGER NOT NULL DEFAULT 0,
                transfer_reason TEXT NOT NULL DEFAULT '',
                ticket_id       TEXT,
                officer_id      TEXT,
                summary_json    TEXT,
                metrics_json    TEXT,
                officer_rating  INTEGER,
                officer_note    TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_voice_calls_status ON voice_calls(status);
            CREATE INDEX IF NOT EXISTS idx_voice_calls_started ON voice_calls(started_at);
            CREATE INDEX IF NOT EXISTS idx_voice_calls_ticket ON voice_calls(ticket_id);
            CREATE INDEX IF NOT EXISTS idx_voice_calls_user ON voice_calls(user_id);

            CREATE TABLE IF NOT EXISTS voice_call_turns (
                id              TEXT PRIMARY KEY,
                call_id         TEXT NOT NULL,
                seq             INTEGER NOT NULL,
                speaker         TEXT NOT NULL,
                kind            TEXT NOT NULL,
                text            TEXT NOT NULL DEFAULT '',
                low_conf_json   TEXT DEFAULT '[]',
                mean_word_prob  DOUBLE PRECISION,
                faithfulness    DOUBLE PRECISION,
                latency_json    TEXT DEFAULT '{}',
                created_at      DOUBLE PRECISION NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_voice_call_turns_call ON voice_call_turns(call_id, seq);
            """
        )
        logger.info("Voice receptionist schema initialized")
    except Exception:
        logger.exception("Failed to initialize voice receptionist schema")


def create_call(
    call_id: str,
    conversation_id: str = "",
    user_id: str = "",
    tenant_id: str = "default",
    channel: str = "browser_sim",
    locale: str = "en",
    status: str = "ai",
    started_at: float | None = None,
) -> dict[str, Any]:
    """Create a new call record."""
    t_start = started_at if started_at is not None else time.time()
    db.execute(
        """
        INSERT INTO voice_calls (
            call_id, conversation_id, user_id, tenant_id, channel, locale,
            status, started_at, transferred
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (call_id, conversation_id or call_id, user_id, tenant_id, channel, locale, status, t_start),
    )
    return get_call(call_id) or {}


def get_call(call_id: str) -> dict[str, Any] | None:
    """Retrieve a call record by call_id."""
    rows = db.query_all("SELECT * FROM voice_calls WHERE call_id = ?", (call_id,))
    if not rows:
        return None
    row = dict(rows[0])
    # Parse json columns if present
    if row.get("summary_json"):
        try:
            row["summary"] = json.loads(row["summary_json"])
        except Exception:
            row["summary"] = None
    if row.get("metrics_json"):
        try:
            row["metrics"] = json.loads(row["metrics_json"])
        except Exception:
            row["metrics"] = None
    return row


def update_call(call_id: str, **kwargs: Any) -> bool:
    """Update fields on a call record."""
    if not kwargs:
        return False

    allowed_cols = {
        "status", "ended_at", "end_reason", "transferred", "transfer_reason",
        "ticket_id", "officer_id", "summary_json", "metrics_json",
        "officer_rating", "officer_note", "locale",
    }
    updates: list[str] = []
    params: list[Any] = []
    for k, v in kwargs.items():
        if k in allowed_cols:
            updates.append(f"{k} = ?")
            if k in ("summary_json", "metrics_json") and isinstance(v, (dict, list)):
                params.append(json.dumps(v))
            elif k == "transferred" and isinstance(v, bool):
                params.append(1 if v else 0)
            else:
                params.append(v)

    if not updates:
        return False

    params.append(call_id)
    query = f"UPDATE voice_calls SET {', '.join(updates)} WHERE call_id = ?"
    db.execute(query, tuple(params))
    return True


def list_calls(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List calls ordered by started_at DESC."""
    query = "SELECT * FROM voice_calls"
    params: list[Any] = []
    if status and status != "all":
        if status == "live":
            query += " WHERE status IN ('ai', 'transferring', 'bridged')"
        elif status == "ended":
            query += " WHERE status = 'ended'"
        else:
            query += " WHERE status = ?"
            params.append(status)

    query += " ORDER BY started_at DESC LIMIT ? OFFSET ?"
    params.extend([max(1, limit), max(0, offset)])
    rows = db.query_all(query, tuple(params))
    calls: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        if d.get("summary_json"):
            try:
                d["summary"] = json.loads(d["summary_json"])
            except Exception:
                d["summary"] = None
        if d.get("metrics_json"):
            try:
                d["metrics"] = json.loads(d["metrics_json"])
            except Exception:
                d["metrics"] = None
        calls.append(d)
    return calls


def create_turn(
    call_id: str,
    seq: int,
    speaker: str,
    kind: str,
    text: str,
    low_conf_words: list[dict[str, Any]] | None = None,
    mean_word_prob: float | None = None,
    faithfulness: float | None = None,
    latencies: dict[str, Any] | None = None,
    created_at: float | None = None,
) -> dict[str, Any]:
    """Create a turn record for a voice call, redacting PII in text."""
    turn_id = f"turn_{uuid.uuid4().hex[:12]}"
    t_now = created_at if created_at is not None else time.time()
    redacted_text = redact_pii_text(text)
    low_conf_str = json.dumps(low_conf_words or [])
    latency_str = json.dumps(latencies or {})

    db.execute(
        """
        INSERT INTO voice_call_turns (
            id, call_id, seq, speaker, kind, text,
            low_conf_json, mean_word_prob, faithfulness,
            latency_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            turn_id, call_id, seq, speaker, kind, redacted_text,
            low_conf_str, mean_word_prob, faithfulness,
            latency_str, t_now,
        ),
    )
    return {
        "id": turn_id,
        "call_id": call_id,
        "seq": seq,
        "speaker": speaker,
        "kind": kind,
        "text": redacted_text,
        "low_conf_words": low_conf_words or [],
        "mean_word_prob": mean_word_prob,
        "faithfulness": faithfulness,
        "latencies": latencies or {},
        "created_at": t_now,
    }


def list_turns(call_id: str) -> list[dict[str, Any]]:
    """List turns for a call ordered by sequence."""
    rows = db.query_all(
        "SELECT * FROM voice_call_turns WHERE call_id = ? ORDER BY seq ASC",
        (call_id,),
    )
    turns: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        try:
            d["low_conf_words"] = json.loads(d.get("low_conf_json") or "[]")
        except Exception:
            d["low_conf_words"] = []
        try:
            d["latencies"] = json.loads(d.get("latency_json") or "{}")
        except Exception:
            d["latencies"] = {}
        turns.append(d)
    return turns


def save_call_review(call_id: str, rating: int, note: str = "") -> bool:
    """Save an officer review (1-5 rating and note) for a call."""
    clamped_rating = max(1, min(5, rating))
    cleaned_note = redact_pii_text(note[:1000]) if note else ""
    return update_call(
        call_id,
        officer_rating=clamped_rating,
        officer_note=cleaned_note,
    )


def get_call_with_turns(call_id: str) -> dict[str, Any] | None:
    """Retrieve full call details including turns, summary, metrics, and linked ticket."""
    call = get_call(call_id)
    if not call:
        return None

    turns = list_turns(call_id)
    call["turns"] = turns

    ticket_id = call.get("ticket_id")
    if ticket_id:
        try:
            call["ticket"] = db.get_ticket(ticket_id)
        except Exception:
            call["ticket"] = None
    else:
        call["ticket"] = None

    return call

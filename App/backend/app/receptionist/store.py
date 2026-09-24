"""Persistence layer for voice receptionist calls and turns."""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from .. import database as db
from ..guardrails import redact_pii_text

logger = logging.getLogger(__name__)

#: Columns added to ``voice_calls`` after it first shipped, for the officer's
#: Call Desk (docs/plans/officer-call-desk-plan.md §5). Added by
#: :func:`_ensure_columns` on startup, so an existing database keeps its rows.
_CALL_DESK_COLUMNS: dict[str, str] = {
    "topic": "TEXT NOT NULL DEFAULT ''",
    "priority": "TEXT NOT NULL DEFAULT 'normal'",
    "transfer_requested_at": "DOUBLE PRECISION",
    "target_team": "TEXT NOT NULL DEFAULT ''",
    "claimed_by": "TEXT NOT NULL DEFAULT ''",
    "claimed_at": "DOUBLE PRECISION",
    "bridged_at": "DOUBLE PRECISION",
    "hold_started_at": "DOUBLE PRECISION",
    "hold_total_s": "DOUBLE PRECISION NOT NULL DEFAULT 0",
    "outcome": "TEXT NOT NULL DEFAULT ''",
    "wrapup_note": "TEXT",
    "wrapup_at": "DOUBLE PRECISION",
    "needs_callback": "INTEGER NOT NULL DEFAULT 0",
    "callback_reason": "TEXT NOT NULL DEFAULT ''",
    "callback_done_at": "DOUBLE PRECISION",
    "callback_done_by": "TEXT",
    "brief_json": "TEXT",
    "brief_updated_at": "DOUBLE PRECISION",
    "risk_json": "TEXT",
}


def _ensure_columns(table: str, columns: dict[str, str]) -> None:
    """Add any of *columns* (name → DDL) that *table* lacks, on either backend.

    Postgres has ``ADD COLUMN IF NOT EXISTS``; SQLite does not, so there the
    existing columns are read first, and a "duplicate column" error from a
    concurrent starter is ignored. *table* and the column names come from this
    module, never from input.
    """
    if db._pg_module() is not None:
        for name, ddl in columns.items():
            db.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {ddl}")  # noqa: S608 - fixed identifiers
        return
    existing = {row["name"] for row in db.query_all(f"PRAGMA table_info({table})")}  # noqa: S608 - fixed identifier
    for name, ddl in columns.items():
        if name in existing:
            continue
        try:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")  # noqa: S608 - fixed identifiers
        except Exception as exc:
            if "duplicate column" not in str(exc).lower():
                raise


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
        _ensure_columns("voice_calls", _CALL_DESK_COLUMNS)
        db.execute_script(
            """
            CREATE INDEX IF NOT EXISTS idx_voice_calls_callback ON voice_calls(needs_callback, callback_done_at);
            CREATE INDEX IF NOT EXISTS idx_voice_calls_claimed ON voice_calls(claimed_by);
            CREATE INDEX IF NOT EXISTS idx_voice_calls_outcome ON voice_calls(outcome);
            """
        )
        logger.info("Voice receptionist schema initialized")
    except Exception:
        logger.exception("Failed to initialize voice receptionist schema")


# Who wants to hear about a call's new turns (the officer brief's scheduler).
# Turns are written from several places — the brain, the Gemini transcript
# taps, the router, the officer leg — and all of them come through
# create_turn, so observing it here needs no change at any call site.
TurnObserver = Callable[[dict[str, Any]], None]
_turn_observers: dict[str, list[TurnObserver]] = {}
_observers_lock = threading.Lock()


def register_turn_observer(call_id: str, observer: TurnObserver) -> None:
    with _observers_lock:
        _turn_observers.setdefault(call_id, []).append(observer)


def unregister_turn_observer(call_id: str, observer: TurnObserver) -> None:
    with _observers_lock:
        # ==, not "is": each ``scheduler.observe`` access is a new bound method.
        remaining = [o for o in _turn_observers.get(call_id, []) if o != observer]
        if remaining:
            _turn_observers[call_id] = remaining
        else:
            _turn_observers.pop(call_id, None)


def _notify_turn_observers(turn: dict[str, Any]) -> None:
    with _observers_lock:
        observers = list(_turn_observers.get(turn["call_id"], []))
    for observer in observers:
        try:
            observer(turn)
        except Exception:
            logger.debug("Turn observer failed for %s", turn["call_id"], exc_info=True)


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
    if row.get("brief_json"):
        try:
            row["brief"] = json.loads(row["brief_json"])
        except Exception:
            row["brief"] = None
    return row


def claim_call(call_id: str, officer_id: str, now: float, expired_before: float) -> bool:
    """Give a waiting call to *officer_id*, atomically: first claim wins.

    One conditional UPDATE, so two officers pressing "Take call" at once —
    on this replica or through the database — cannot both succeed. A claim
    older than *expired_before* (audio never connected) no longer counts, and
    a call already with an officer cannot be claimed at all.
    """
    affected = db.execute(
        """
        UPDATE voice_calls SET claimed_by = ?, claimed_at = ?
        WHERE call_id = ? AND status IN ('ai', 'transferring')
          AND (claimed_by = '' OR claimed_by IS NULL OR claimed_by = ? OR claimed_at < ?)
        """,
        (officer_id, now, call_id, officer_id, expired_before),
    )
    return affected > 0


def release_claim(call_id: str, officer_id: str | None = None) -> bool:
    """Drop the claim on *call_id* — only *officer_id*'s, when given."""
    if officer_id is None:
        affected = db.execute(
            "UPDATE voice_calls SET claimed_by = '', claimed_at = NULL WHERE call_id = ?", (call_id,)
        )
    else:
        affected = db.execute(
            "UPDATE voice_calls SET claimed_by = '', claimed_at = NULL WHERE call_id = ? AND claimed_by = ?",
            (call_id, officer_id),
        )
    return affected > 0


def update_call(call_id: str, **kwargs: Any) -> bool:
    """Update fields on a call record."""
    if not kwargs:
        return False

    allowed_cols = {
        "status", "ended_at", "end_reason", "transferred", "transfer_reason",
        "ticket_id", "officer_id", "summary_json", "metrics_json",
        "officer_rating", "officer_note", "locale",
        *_CALL_DESK_COLUMNS,
    }
    updates: list[str] = []
    params: list[Any] = []
    for k, v in kwargs.items():
        if k in allowed_cols:
            updates.append(f"{k} = ?")
            if k.endswith("_json") and isinstance(v, (dict, list)):
                params.append(json.dumps(v))
            elif k in ("transferred", "needs_callback") and isinstance(v, bool):
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
    turn = {
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
    _notify_turn_observers(turn)
    return turn


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

"""Analytics persistence layer.

Two backends are available; selected via ``ANALYTICS_BACKEND``:

- **sqlite** (default) — thread-safe via thread-local connections with
  WAL journaling.  Fine for single-node / single-worker deploys; will
  lock-contend across replicas.
- **postgres** — see :mod:`postgres`.  Correct choice for multi-replica
  deploys.  Enable with ``ANALYTICS_BACKEND=postgres`` and ``POSTGRES_DSN``.

All write operations are wrapped in try/except to prevent data loss.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
import uuid
import json
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Backend selection — import early so the dispatch module aliases below pick
# up the right implementation.  Falls back to SQLite if psycopg missing.
# ---------------------------------------------------------------------------
ANALYTICS_BACKEND = os.getenv("ANALYTICS_BACKEND", "sqlite").lower()

from ._root import PROJECT_ROOT as _PROJECT_ROOT
_DB_DIR = Path(os.getenv("ANALYTICS_DB_DIR", str(_PROJECT_ROOT / "data_store")))
_DB_PATH = _DB_DIR / "analytics.db"

# Retention TTLs (days) — enforced by cleanup_expired_data()
_CONVERSATION_TTL_DAYS = int(os.getenv("CONVERSATION_TTL_DAYS", "7"))
_ANALYTICS_TTL_DAYS = int(os.getenv("ANALYTICS_TTL_DAYS", "365"))
_FEEDBACK_TTL_DAYS = int(os.getenv("FEEDBACK_TTL_DAYS", "90"))
_SESSION_TTL_DAYS = int(os.getenv("SESSION_TTL_DAYS", "30"))

# Thread-local storage for connections with a lock for init safety
_local = threading.local()
_init_lock = threading.Lock()


def _get_connection() -> sqlite3.Connection:
    """Return a thread-local SQLite connection (safe per-thread singleton)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        with _init_lock:
            # Double-check after acquiring lock
            conn = getattr(_local, "conn", None)
            if conn is None:
                _DB_DIR.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(str(_DB_PATH), timeout=10)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=5000")
                _local.conn = conn
    return conn


def _ensure_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    ddl: str,
) -> None:
    """Add *column* to *table* if it is missing.

    SQLite only gained ``ADD COLUMN IF NOT EXISTS`` recently, so we do
    the compatibility check ourselves to support older runtimes.
    """
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()  # noqa: S608 - fixed table name
    names = {row["name"] if isinstance(row, sqlite3.Row) else row[1] for row in rows}
    if column in names:
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")  # noqa: S608 - fixed identifiers
    logger.info("Added missing column %s.%s", table, column)


def init_db() -> None:
    """Create tables if they don't exist."""
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = _get_connection()

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS feedback (
            id          TEXT PRIMARY KEY,
            message_id  TEXT NOT NULL,
            session_id  TEXT,
            rating      TEXT NOT NULL CHECK(rating IN ('up', 'down')),
            comment     TEXT DEFAULT '',
            user_query  TEXT DEFAULT '',
            bot_reply   TEXT DEFAULT '',
            created_at  REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS analytics_events (
            id          TEXT PRIMARY KEY,
            session_id  TEXT,
            event_type  TEXT NOT NULL,
            event_data  TEXT DEFAULT '{}',
            created_at  REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id              TEXT PRIMARY KEY,
            started_at      REAL NOT NULL,
            last_active_at  REAL NOT NULL,
            message_count   INTEGER DEFAULT 0,
            user_agent      TEXT DEFAULT '',
            platform        TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id              TEXT PRIMARY KEY,
            conversation_id TEXT,
            session_id      TEXT,
            user_message    TEXT NOT NULL,
            bot_reply       TEXT NOT NULL,
            sources         TEXT DEFAULT '[]',
            response_time_ms REAL DEFAULT 0,
            confidence      REAL DEFAULT 0,
            topic_tag       TEXT DEFAULT '',
            created_at      REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS workflow_sessions (
            conversation_id TEXT PRIMARY KEY,
            workflow_id     TEXT NOT NULL,
            status          TEXT NOT NULL
                            CHECK(status IN ('active','completed','cancelled'))
                            DEFAULT 'active',
            current_step_idx INTEGER NOT NULL DEFAULT 0,
            slots_json      TEXT DEFAULT '{}',
            last_prompt     TEXT DEFAULT '',
            created_at      REAL NOT NULL,
            updated_at      REAL NOT NULL
        );

        -- Phase 14 (2026) — identity, tenancy, and consent.
        -- Tenants are a first-class concept for RLS and
        -- multi-tenant isolation.  One tenant per URA deployment
        -- or partner agency (KCCA, NSSF, etc).
        CREATE TABLE IF NOT EXISTS tenants (
            id            TEXT PRIMARY KEY,
            display_name  TEXT NOT NULL,
            created_at    REAL NOT NULL
        );

        -- Users map from OIDC `sub` claim to an internal id.
        -- One row per (tenant_id, external_id).  Role is the
        -- primary RBAC key.
        CREATE TABLE IF NOT EXISTS users (
            id            TEXT PRIMARY KEY,
            tenant_id     TEXT NOT NULL DEFAULT 'default',
            external_id   TEXT NOT NULL,
            email         TEXT DEFAULT '',
            role          TEXT NOT NULL DEFAULT 'public'
                          CHECK(role IN ('public','verified_taxpayer','ura_staff','ura_admin','ura_auditor')),
            created_at    REAL NOT NULL,
            last_seen_at  REAL NOT NULL,
            UNIQUE(tenant_id, external_id)
        );

        -- User profile — JSON blob for easy evolution, indexed via
        -- SQL only on fields we actually filter on.  Stored as TEXT
        -- in SQLite; JSONB in postgres.py mirror.
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id            TEXT PRIMARY KEY
                               REFERENCES users(id) ON DELETE CASCADE,
            taxpayer_type      TEXT DEFAULT 'unknown',
            industry           TEXT DEFAULT '',
            primary_language   TEXT DEFAULT 'en',
            detail_level       TEXT DEFAULT 'intermediate',
            registered_tax_types TEXT DEFAULT '[]',
            fiscal_year        TEXT DEFAULT 'FY2025-26',
            display_name       TEXT DEFAULT '',
            updated_at         REAL NOT NULL
        );

        -- Consent receipts — append-only.  Withdrawal is a new row
        -- with withdrawn_at set.  One active row per (user_id, purpose, version).
        CREATE TABLE IF NOT EXISTS consent_receipts (
            receipt_id    TEXT PRIMARY KEY,
            user_id       TEXT NOT NULL
                          REFERENCES users(id) ON DELETE CASCADE,
            purpose       TEXT NOT NULL
                          CHECK(purpose IN ('personalization','analytics','ticket_escalation','long_term_storage','ura_account_access','ura_actions','voice_recording','voice_analytics')),
            version       TEXT NOT NULL,
            granted_at    REAL NOT NULL,
            withdrawn_at  REAL,
            legal_basis   TEXT NOT NULL DEFAULT 'consent'
                          CHECK(legal_basis IN ('consent','public_task','legal_obligation'))
        );

        -- Phase 14-D — ticket queue for escalations.  Each ticket is
        -- one human-required conversation the supervisor routed out
        -- of the automated pipeline.  Staff work them via the admin
        -- endpoints in main.py (/v1/admin/tickets).
        CREATE TABLE IF NOT EXISTS tickets (
            id             TEXT PRIMARY KEY,
            conversation_id TEXT,
            session_id     TEXT,
            status         TEXT NOT NULL
                           CHECK(status IN ('open','assigned','resolved','wontfix'))
                           DEFAULT 'open',
            priority       TEXT NOT NULL
                           CHECK(priority IN ('low','normal','high','urgent'))
                           DEFAULT 'normal',
            reason         TEXT DEFAULT '',
            user_query     TEXT DEFAULT '',
            bot_reply      TEXT DEFAULT '',
            handoff_json   TEXT DEFAULT '{}',
            response_judge_json TEXT DEFAULT '{}',
            assignee       TEXT DEFAULT '',
            staff_note     TEXT DEFAULT '',
            created_at     REAL NOT NULL,
            updated_at     REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_feedback_message ON feedback(message_id);
        CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback(created_at);
        CREATE INDEX IF NOT EXISTS idx_events_type ON analytics_events(event_type);
        CREATE INDEX IF NOT EXISTS idx_events_session ON analytics_events(session_id);
        CREATE INDEX IF NOT EXISTS idx_events_created ON analytics_events(created_at);
        CREATE INDEX IF NOT EXISTS idx_sessions_active ON sessions(last_active_at);
        CREATE INDEX IF NOT EXISTS idx_conversations_session ON conversations(session_id);
        CREATE INDEX IF NOT EXISTS idx_conversations_created ON conversations(created_at);
        CREATE INDEX IF NOT EXISTS idx_workflow_status ON workflow_sessions(status);
        CREATE INDEX IF NOT EXISTS idx_workflow_updated ON workflow_sessions(updated_at);
        CREATE INDEX IF NOT EXISTS idx_tickets_status    ON tickets(status);
        CREATE INDEX IF NOT EXISTS idx_tickets_priority  ON tickets(priority);
        CREATE INDEX IF NOT EXISTS idx_tickets_created   ON tickets(created_at);
        CREATE INDEX IF NOT EXISTS idx_users_tenant      ON users(tenant_id);
        CREATE INDEX IF NOT EXISTS idx_users_last_seen   ON users(last_seen_at);
        CREATE INDEX IF NOT EXISTS idx_consent_user      ON consent_receipts(user_id);
        CREATE INDEX IF NOT EXISTS idx_consent_active    ON consent_receipts(user_id, purpose, withdrawn_at);
    """)

    # Forward-compatible schema migrations for existing DBs.
    _ensure_column(conn, "conversations", "conversation_id", "TEXT")
    conn.execute(
        "UPDATE conversations SET conversation_id = id WHERE conversation_id IS NULL OR conversation_id = ''"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversations_thread ON conversations(conversation_id)")
    _ensure_column(conn, "tickets", "handoff_json", "TEXT DEFAULT '{}'")
    _ensure_column(conn, "tickets", "response_judge_json", "TEXT DEFAULT '{}'")

    # Seed the default tenant if missing
    conn.execute(
        "INSERT OR IGNORE INTO tenants (id, display_name, created_at) VALUES (?, ?, ?)",
        ("default", "URA Default Tenant", time.time()),
    )
    conn.commit()
    logger.info("Analytics database initialised at %s", _DB_PATH)

    # Run cleanup on startup
    cleanup_expired_data()


# ---------------------------------------------------------------------------
# Retention TTL enforcement
# ---------------------------------------------------------------------------
def cleanup_expired_data() -> dict[str, int]:
    """Delete rows older than configured TTLs.  Returns counts deleted."""
    conn = _get_connection()
    now = time.time()
    deleted: dict[str, int] = {}

    ttls = [
        ("conversations", _CONVERSATION_TTL_DAYS),
        ("analytics_events", _ANALYTICS_TTL_DAYS),
        ("feedback", _FEEDBACK_TTL_DAYS),
        ("sessions", _SESSION_TTL_DAYS),
        ("workflow_sessions", _CONVERSATION_TTL_DAYS),
    ]
    ts_col = {
        "conversations": "created_at",
        "analytics_events": "created_at",
        "feedback": "created_at",
        "sessions": "last_active_at",
        "workflow_sessions": "updated_at",
    }

    for table, ttl_days in ttls:
        cutoff = now - (ttl_days * 86400)
        col = ts_col[table]
        try:
            cursor = conn.execute(
                f"DELETE FROM {table} WHERE {col} < ?",  # noqa: S608 — table/col are hardcoded above
                (cutoff,),
            )
            conn.commit()
            deleted[table] = cursor.rowcount
            if cursor.rowcount > 0:
                logger.info(
                    "TTL cleanup: deleted %d rows from %s (>%dd)", cursor.rowcount, table, ttl_days
                )
        except Exception:
            logger.exception("TTL cleanup failed for %s", table)
            conn.rollback()
            deleted[table] = 0

    return deleted


# ---------------------------------------------------------------------------
# Feedback CRUD
# ---------------------------------------------------------------------------
def save_feedback(
    message_id: str,
    rating: str,
    comment: str = "",
    session_id: str | None = None,
    user_query: str = "",
    bot_reply: str = "",
) -> dict[str, Any]:
    """Persist a feedback entry and return it."""
    conn = _get_connection()
    fb_id = str(uuid.uuid4())
    now = time.time()
    try:
        conn.execute(
            """INSERT INTO feedback (id, message_id, session_id, rating, comment,
               user_query, bot_reply, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (fb_id, message_id, session_id, rating, comment, user_query, bot_reply, now),
        )
        conn.commit()
    except Exception:
        logger.exception("Failed to save feedback for message_id=%s", message_id)
        conn.rollback()
        raise
    return {"id": fb_id, "message_id": message_id, "rating": rating, "created_at": now}


def update_feedback_comment(message_id: str, comment: str) -> bool:
    """Update the comment on an existing feedback entry (for follow-up comments)."""
    conn = _get_connection()
    try:
        cursor = conn.execute(
            """UPDATE feedback SET comment = ?
               WHERE id = (
                 SELECT id FROM feedback
                 WHERE message_id = ? AND comment = ''
                 ORDER BY created_at DESC LIMIT 1
               )""",
            (comment, message_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        logger.exception("Failed to update feedback comment for message_id=%s", message_id)
        conn.rollback()
        return False


def get_feedback_summary(days: int = 30) -> dict[str, Any]:
    """Aggregate feedback stats for the last N days."""
    conn = _get_connection()
    cutoff = time.time() - (days * 86400)
    row = conn.execute(
        """SELECT
             COUNT(*) as total,
             COALESCE(SUM(CASE WHEN rating='up' THEN 1 ELSE 0 END), 0) as thumbs_up,
             COALESCE(SUM(CASE WHEN rating='down' THEN 1 ELSE 0 END), 0) as thumbs_down
           FROM feedback WHERE created_at >= ?""",
        (cutoff,),
    ).fetchone()

    total = row["total"] or 0
    up = row["thumbs_up"] or 0
    down = row["thumbs_down"] or 0
    satisfaction = round(up / total * 100, 1) if total > 0 else 0.0

    recent = conn.execute(
        """SELECT id, message_id, rating, comment, created_at
           FROM feedback WHERE created_at >= ? ORDER BY created_at DESC LIMIT 20""",
        (cutoff,),
    ).fetchall()

    return {
        "period_days": days,
        "total": total,
        "thumbs_up": up,
        "thumbs_down": down,
        "satisfaction_pct": satisfaction,
        "recent": [dict(r) for r in recent],
    }


# ---------------------------------------------------------------------------
# Analytics events
# ---------------------------------------------------------------------------
def track_event(
    event_type: str,
    event_data: str = "{}",
    session_id: str | None = None,
) -> None:
    """Record an analytics event."""
    conn = _get_connection()
    try:
        conn.execute(
            """INSERT INTO analytics_events (id, session_id, event_type, event_data, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), session_id, event_type, event_data, time.time()),
        )
        conn.commit()
    except Exception:
        logger.exception("Failed to track event type=%s", event_type)
        conn.rollback()


def get_event_counts(days: int = 30) -> dict[str, int]:
    """Count events by type for the last N days."""
    conn = _get_connection()
    cutoff = time.time() - (days * 86400)
    rows = conn.execute(
        """SELECT event_type, COUNT(*) as cnt
           FROM analytics_events WHERE created_at >= ?
           GROUP BY event_type ORDER BY cnt DESC""",
        (cutoff,),
    ).fetchall()
    return {r["event_type"]: r["cnt"] for r in rows}


# ---------------------------------------------------------------------------
# Session tracking
# ---------------------------------------------------------------------------
def upsert_session(
    session_id: str,
    user_agent: str = "",
    platform: str = "",
) -> None:
    """Create or update a session record."""
    conn = _get_connection()
    now = time.time()
    try:
        conn.execute(
            """INSERT INTO sessions (id, started_at, last_active_at, message_count, user_agent, platform)
               VALUES (?, ?, ?, 1, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 last_active_at = excluded.last_active_at,
                 message_count = sessions.message_count + 1""",
            (session_id, now, now, user_agent, platform),
        )
        conn.commit()
    except Exception:
        logger.exception("Failed to upsert session id=%s", session_id)
        conn.rollback()


def get_session_stats(days: int = 30) -> dict[str, Any]:
    """Session analytics for the last N days."""
    conn = _get_connection()
    cutoff = time.time() - (days * 86400)
    row = conn.execute(
        """SELECT
             COUNT(*) as total_sessions,
             COALESCE(AVG(message_count), 0) as avg_messages,
             COALESCE(MAX(message_count), 0) as max_messages
           FROM sessions WHERE last_active_at >= ?""",
        (cutoff,),
    ).fetchone()
    return {
        "period_days": days,
        "total_sessions": row["total_sessions"] or 0,
        "avg_messages_per_session": round(row["avg_messages"] or 0, 1),
        "max_messages_in_session": row["max_messages"] or 0,
    }


# ---------------------------------------------------------------------------
# Conversation logging
# ---------------------------------------------------------------------------
def log_conversation(
    session_id: str | None,
    conversation_id: str | None,
    user_message: str,
    bot_reply: str,
    sources: str = "[]",
    response_time_ms: float = 0,
    confidence: float = 0,
    topic_tag: str = "",
) -> str:
    """Log a conversation turn and return the stable thread id."""
    conn = _get_connection()
    row_id = str(uuid.uuid4())
    thread_id = conversation_id or row_id
    try:
        conn.execute(
            """INSERT INTO conversations
               (id, conversation_id, session_id, user_message, bot_reply, sources,
                response_time_ms, confidence, topic_tag, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row_id,
                thread_id,
                session_id,
                user_message,
                bot_reply,
                sources,
                response_time_ms,
                confidence,
                topic_tag,
                time.time(),
            ),
        )
        conn.commit()
    except Exception:
        logger.exception("Failed to log conversation")
        conn.rollback()
        raise
    return thread_id


def get_recent_turns(
    session_id: str | None = None,
    conversation_id: str | None = None,
    limit: int = 5,
) -> list[dict[str, str]]:
    """Retrieve the most recent conversation turns for a session (multi-turn memory).

    Returns a list of dicts with ``user_message`` and ``bot_reply`` keys,
    ordered oldest-first (chronological) for prompt injection.
    """
    if conversation_id:
        sql = """SELECT user_message, bot_reply FROM conversations
                 WHERE conversation_id = ?
                 ORDER BY created_at DESC LIMIT ?"""
        args: tuple[str, int] = (conversation_id, limit)
    elif session_id:
        sql = """SELECT user_message, bot_reply FROM conversations
                 WHERE session_id = ?
                 ORDER BY created_at DESC LIMIT ?"""
        args = (session_id, limit)
    else:
        return []

    conn = _get_connection()
    rows = conn.execute(sql, args).fetchall()
    # Reverse to chronological order
    return [
        {"user_message": r["user_message"], "bot_reply": r["bot_reply"]} for r in reversed(rows)
    ]


def get_workflow_session(conversation_id: str) -> dict[str, Any] | None:
    """Return the persisted workflow session for a conversation, if any."""
    if not conversation_id:
        return None
    conn = _get_connection()
    row = conn.execute(
        """SELECT conversation_id, workflow_id, status, current_step_idx,
                  slots_json, last_prompt, created_at, updated_at
           FROM workflow_sessions WHERE conversation_id = ?""",
        (conversation_id,),
    ).fetchone()
    if not row:
        return None
    try:
        slots = json.loads(row["slots_json"] or "{}")
        if not isinstance(slots, dict):
            slots = {}
    except Exception:
        slots = {}
    return {
        "conversation_id": row["conversation_id"],
        "workflow_id": row["workflow_id"],
        "status": row["status"],
        "current_step_idx": int(row["current_step_idx"] or 0),
        "slots": slots,
        "last_prompt": row["last_prompt"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def upsert_workflow_session(
    conversation_id: str,
    workflow_id: str,
    current_step_idx: int,
    slots: dict[str, Any] | None = None,
    *,
    status: str = "active",
    last_prompt: str = "",
) -> None:
    """Create or update a durable workflow session."""
    if not conversation_id or not workflow_id:
        return
    if status not in {"active", "completed", "cancelled"}:
        status = "active"
    conn = _get_connection()
    now = time.time()
    slots_json = json.dumps(slots or {}, ensure_ascii=True)
    try:
        conn.execute(
            """INSERT INTO workflow_sessions
               (conversation_id, workflow_id, status, current_step_idx,
                slots_json, last_prompt, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(conversation_id) DO UPDATE SET
                 workflow_id = excluded.workflow_id,
                 status = excluded.status,
                 current_step_idx = excluded.current_step_idx,
                 slots_json = excluded.slots_json,
                 last_prompt = excluded.last_prompt,
                 updated_at = excluded.updated_at""",
            (
                conversation_id,
                workflow_id,
                status,
                max(0, int(current_step_idx)),
                slots_json,
                last_prompt[:2000],
                now,
                now,
            ),
        )
        conn.commit()
    except Exception:
        logger.exception("Failed to upsert workflow session")
        conn.rollback()
        raise


def complete_workflow_session(
    conversation_id: str,
    *,
    status: str = "completed",
) -> bool:
    """Mark a workflow session as completed or cancelled."""
    if not conversation_id:
        return False
    if status not in {"completed", "cancelled"}:
        status = "completed"
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "UPDATE workflow_sessions SET status = ?, updated_at = ? WHERE conversation_id = ?",
            (status, time.time(), conversation_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        logger.exception("Failed to complete workflow session")
        conn.rollback()
        return False


def get_conversation_stats(days: int = 30) -> dict[str, Any]:
    """Conversation analytics for the last N days."""
    conn = _get_connection()
    cutoff = time.time() - (days * 86400)
    row = conn.execute(
        """SELECT
             COUNT(*) as total,
             COALESCE(AVG(response_time_ms), 0) as avg_response_ms,
             COALESCE(AVG(confidence), 0) as avg_confidence
           FROM conversations WHERE created_at >= ?""",
        (cutoff,),
    ).fetchone()

    top_topics = conn.execute(
        """SELECT topic_tag, COUNT(*) as cnt
           FROM conversations WHERE created_at >= ? AND topic_tag != ''
           GROUP BY topic_tag ORDER BY cnt DESC LIMIT 10""",
        (cutoff,),
    ).fetchall()

    return {
        "period_days": days,
        "total_conversations": row["total"] or 0,
        "avg_response_time_ms": round(row["avg_response_ms"] or 0, 1),
        "avg_confidence": round(row["avg_confidence"] or 0, 3),
        "top_topics": [{"tag": r["topic_tag"], "count": r["cnt"]} for r in top_topics],
    }


# ---------------------------------------------------------------------------
# Feedback review export (feedback loop)
# ---------------------------------------------------------------------------
def export_review_feedback(days: int = 30) -> list[dict[str, Any]]:
    """Export negative feedback and low-confidence conversations for review.

    Returns entries suitable for retriever/reranker tuning and regression tests.
    """
    conn = _get_connection()
    cutoff = time.time() - (days * 86400)

    # Thumbs-down feedback
    down_rows = conn.execute(
        """SELECT f.message_id, f.user_query, f.bot_reply, f.comment, f.created_at,
                  'thumbs_down' as review_reason
           FROM feedback f
           WHERE f.rating = 'down' AND f.created_at >= ?
           ORDER BY f.created_at DESC""",
        (cutoff,),
    ).fetchall()

    # Low-confidence conversations
    low_conf_rows = conn.execute(
        """SELECT id as message_id, user_message as user_query, bot_reply,
                  '' as comment, created_at, 'low_confidence' as review_reason
           FROM conversations
           WHERE confidence < 0.3 AND confidence > 0 AND created_at >= ?
           ORDER BY created_at DESC LIMIT 200""",
        (cutoff,),
    ).fetchall()

    return [dict(r) for r in down_rows] + [dict(r) for r in low_conf_rows]


# ---------------------------------------------------------------------------
# Ticket queue (Phase 14-D)
# ---------------------------------------------------------------------------
def _json_dumps(value: Any, default: str) -> str:
    try:
        return json.dumps(value if value is not None else json.loads(default))
    except Exception:
        return default


def _json_loads(value: Any, default: Any) -> Any:
    if value in ("", None):
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _hydrate_ticket(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    ticket = dict(row)
    ticket["handoff"] = _json_loads(ticket.pop("handoff_json", "{}"), {})
    ticket["response_judge"] = _json_loads(ticket.pop("response_judge_json", "{}"), {})
    return ticket


def create_ticket(
    reason: str,
    user_query: str = "",
    bot_reply: str = "",
    session_id: str | None = None,
    conversation_id: str | None = None,
    priority: str = "normal",
    handoff: dict[str, Any] | None = None,
    response_judge: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a new escalation ticket and return it.

    Called from the ``escalate_to_human`` tool and from the
    supervisor's ESCALATE route.  Priority must be one of
    'low', 'normal', 'high', 'urgent' — invalid values are coerced
    to 'normal' with a warning.
    """
    if priority not in ("low", "normal", "high", "urgent"):
        logger.warning("create_ticket: invalid priority %r → 'normal'", priority)
        priority = "normal"
    conn = _get_connection()
    ticket_id = str(uuid.uuid4())
    now = time.time()
    try:
        conn.execute(
            """INSERT INTO tickets (id, conversation_id, session_id, status, priority,
                                    reason, user_query, bot_reply,
                                    handoff_json, response_judge_json,
                                    assignee, staff_note, created_at, updated_at)
               VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, '', '', ?, ?)""",
            (
                ticket_id,
                conversation_id,
                session_id,
                priority,
                reason,
                user_query,
                bot_reply,
                _json_dumps(handoff, "{}"),
                _json_dumps(response_judge, "{}"),
                now,
                now,
            ),
        )
        conn.commit()
        logger.info("ticket %s created (priority=%s reason=%s)", ticket_id, priority, reason[:60])
    except Exception:
        logger.exception("Failed to create ticket")
        conn.rollback()
        raise
    return {
        "id": ticket_id,
        "status": "open",
        "priority": priority,
        "reason": reason,
        "handoff": handoff or {},
        "response_judge": response_judge or {},
        "created_at": now,
    }


def list_tickets(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List tickets, optionally filtered by status.

    Ordered newest-first.  Used by the admin endpoint so staff can
    pull the current open queue.
    """
    conn = _get_connection()
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    sql = (
        "SELECT id, conversation_id, session_id, status, priority, reason, "
        "       user_query, bot_reply, handoff_json, response_judge_json, "
        "       assignee, staff_note, created_at, updated_at "
        "FROM tickets"
    )
    params: list[Any] = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = conn.execute(sql, params).fetchall()
    return [_hydrate_ticket(r) for r in rows]


def get_ticket(ticket_id: str) -> dict[str, Any] | None:
    conn = _get_connection()
    row = conn.execute(
        "SELECT * FROM tickets WHERE id = ?",
        (ticket_id,),
    ).fetchone()
    return _hydrate_ticket(row) if row else None


def update_ticket(
    ticket_id: str,
    status: str | None = None,
    assignee: str | None = None,
    staff_note: str | None = None,
    priority: str | None = None,
) -> bool:
    """Update mutable ticket fields.  Returns True if a row was touched."""
    conn = _get_connection()
    sets: list[str] = []
    params: list[Any] = []
    if status is not None:
        if status not in ("open", "assigned", "resolved", "wontfix"):
            return False
        sets.append("status = ?")
        params.append(status)
    if assignee is not None:
        sets.append("assignee = ?")
        params.append(assignee[:128])
    if staff_note is not None:
        sets.append("staff_note = ?")
        params.append(staff_note[:2000])
    if priority is not None:
        if priority not in ("low", "normal", "high", "urgent"):
            return False
        sets.append("priority = ?")
        params.append(priority)
    if not sets:
        return False
    sets.append("updated_at = ?")
    params.append(time.time())
    params.append(ticket_id)
    try:
        cursor = conn.execute(
            f"UPDATE tickets SET {', '.join(sets)} WHERE id = ?",  # noqa: S608
            params,
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        logger.exception("update_ticket failed")
        conn.rollback()
        return False


# ---------------------------------------------------------------------------
# Phase 14 (2026) — identity / profile / consent CRUD
# ---------------------------------------------------------------------------
def upsert_user(
    external_id: str,
    tenant_id: str = "default",
    email: str = "",
    role: str = "public",
) -> dict[str, Any]:
    """Create or refresh a user row from verified JWT claims.

    Returns the full row as a dict (with the internal ``id``).
    Idempotent — calling repeatedly with the same (tenant_id,
    external_id) updates ``last_seen_at`` only.
    """
    conn = _get_connection()
    now = time.time()
    row = conn.execute(
        "SELECT * FROM users WHERE tenant_id = ? AND external_id = ?",
        (tenant_id, external_id),
    ).fetchone()

    if row is not None:
        try:
            conn.execute(
                "UPDATE users SET last_seen_at = ?, email = ?, role = ? WHERE id = ?",
                (now, email or row["email"], role or row["role"], row["id"]),
            )
            conn.commit()
        except Exception:
            logger.exception("upsert_user update failed")
            conn.rollback()
        return {
            "id": row["id"],
            "tenant_id": row["tenant_id"],
            "external_id": row["external_id"],
            "email": email or row["email"],
            "role": role or row["role"],
            "created_at": row["created_at"],
            "last_seen_at": now,
        }

    user_id = str(uuid.uuid4())
    try:
        conn.execute(
            """INSERT INTO users (id, tenant_id, external_id, email, role, created_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, tenant_id, external_id, email, role, now, now),
        )
        conn.commit()
    except Exception:
        logger.exception("upsert_user insert failed")
        conn.rollback()
        raise
    return {
        "id": user_id,
        "tenant_id": tenant_id,
        "external_id": external_id,
        "email": email,
        "role": role,
        "created_at": now,
        "last_seen_at": now,
    }


def get_user(user_id: str) -> dict[str, Any] | None:
    conn = _get_connection()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_profile(user_id: str) -> dict[str, Any] | None:
    conn = _get_connection()
    row = conn.execute(
        "SELECT * FROM user_profiles WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    try:
        import json as _json

        d["registered_tax_types"] = _json.loads(d.get("registered_tax_types", "[]"))
    except Exception:
        d["registered_tax_types"] = []
    return d


def upsert_user_profile(user_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    """Create or patch a profile row.

    Unknown keys in *updates* are silently dropped — the Pydantic
    model above is the source of truth for allowed fields.
    """
    import json as _json

    allowed = {
        "taxpayer_type",
        "industry",
        "primary_language",
        "detail_level",
        "registered_tax_types",
        "fiscal_year",
        "display_name",
    }
    updates = {k: v for k, v in updates.items() if k in allowed}
    if "registered_tax_types" in updates and isinstance(updates["registered_tax_types"], list):
        updates["registered_tax_types"] = _json.dumps(updates["registered_tax_types"])

    conn = _get_connection()
    existing = conn.execute(
        "SELECT user_id FROM user_profiles WHERE user_id = ?", (user_id,)
    ).fetchone()
    now = time.time()

    if existing is None:
        # First-time insert — fall back to defaults for any missing field
        defaults = {
            "taxpayer_type": "unknown",
            "industry": "",
            "primary_language": "en",
            "detail_level": "intermediate",
            "registered_tax_types": "[]",
            "fiscal_year": "FY2025-26",
            "display_name": "",
        }
        defaults.update(updates)
        try:
            conn.execute(
                """INSERT INTO user_profiles
                   (user_id, taxpayer_type, industry, primary_language,
                    detail_level, registered_tax_types, fiscal_year,
                    display_name, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    defaults["taxpayer_type"],
                    defaults["industry"],
                    defaults["primary_language"],
                    defaults["detail_level"],
                    defaults["registered_tax_types"],
                    defaults["fiscal_year"],
                    defaults["display_name"],
                    now,
                ),
            )
            conn.commit()
        except Exception:
            logger.exception("upsert_user_profile insert failed")
            conn.rollback()
            raise
    else:
        if not updates:
            return get_user_profile(user_id) or {}
        sets = ", ".join(f"{k} = ?" for k in updates) + ", updated_at = ?"
        params = list(updates.values()) + [now, user_id]
        try:
            conn.execute(
                f"UPDATE user_profiles SET {sets} WHERE user_id = ?",  # noqa: S608
                params,
            )
            conn.commit()
        except Exception:
            logger.exception("upsert_user_profile update failed")
            conn.rollback()
            raise

    return get_user_profile(user_id) or {}


def grant_consent(
    user_id: str,
    purpose: str,
    version: str,
    legal_basis: str = "consent",
) -> dict[str, Any]:
    """Issue a new consent receipt for (user, purpose, version).

    If an active (not-withdrawn) row already exists for this
    (user, purpose, version) we return it unchanged — consents are
    idempotent.  Withdrawal of an older version is caller's job.
    """
    conn = _get_connection()
    existing = conn.execute(
        """SELECT * FROM consent_receipts
           WHERE user_id = ? AND purpose = ? AND version = ? AND withdrawn_at IS NULL""",
        (user_id, purpose, version),
    ).fetchone()
    if existing is not None:
        return dict(existing)

    receipt_id = str(uuid.uuid4())
    now = time.time()
    try:
        conn.execute(
            """INSERT INTO consent_receipts
               (receipt_id, user_id, purpose, version, granted_at, withdrawn_at, legal_basis)
               VALUES (?, ?, ?, ?, ?, NULL, ?)""",
            (receipt_id, user_id, purpose, version, now, legal_basis),
        )
        conn.commit()
    except Exception:
        logger.exception("grant_consent failed")
        conn.rollback()
        raise

    return {
        "receipt_id": receipt_id,
        "user_id": user_id,
        "purpose": purpose,
        "version": version,
        "granted_at": now,
        "withdrawn_at": None,
        "legal_basis": legal_basis,
    }


def withdraw_consent(user_id: str, purpose: str) -> int:
    """Mark all active consents for (user, purpose) as withdrawn.

    Returns the number of rows touched.  The caller is responsible
    for cascading cleanup (memory purge, etc.).
    """
    conn = _get_connection()
    now = time.time()
    try:
        cursor = conn.execute(
            """UPDATE consent_receipts
               SET withdrawn_at = ?
               WHERE user_id = ? AND purpose = ? AND withdrawn_at IS NULL""",
            (now, user_id, purpose),
        )
        conn.commit()
        return cursor.rowcount
    except Exception:
        logger.exception("withdraw_consent failed")
        conn.rollback()
        return 0


def get_active_consents(user_id: str) -> list[dict[str, Any]]:
    conn = _get_connection()
    rows = conn.execute(
        """SELECT * FROM consent_receipts
           WHERE user_id = ? AND withdrawn_at IS NULL
           ORDER BY granted_at DESC""",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def has_active_consent(user_id: str, purpose: str) -> bool:
    conn = _get_connection()
    row = conn.execute(
        """SELECT 1 FROM consent_receipts
           WHERE user_id = ? AND purpose = ? AND withdrawn_at IS NULL LIMIT 1""",
        (user_id, purpose),
    ).fetchone()
    return row is not None


def export_user_data(user_id: str) -> dict[str, Any]:
    """GET /v1/me/export — subject right to data portability (UDPA 2019)."""
    return {
        "user": get_user(user_id),
        "profile": get_user_profile(user_id),
        "consents": get_active_consents(user_id),
        "conversations": [],  # filled in by service.py (tenant + user filter)
        "tickets": [],  # filled in by service.py
        "facts": [],  # filled by Phase 16 memory module
    }


def delete_user_cascade(user_id: str) -> dict[str, int]:
    """DELETE /v1/me — right to erasure.

    Cascades through every table that holds user data.  The
    audit ledger is INTENTIONALLY not touched — erasure must be
    cryptographically marked, not the log rewritten (per UDPA +
    EU precedent for audit integrity).
    """
    conn = _get_connection()
    counts: dict[str, int] = {}
    # (table, fk_column)
    cascade = [
        ("consent_receipts", "user_id"),
        ("user_profiles", "user_id"),
        ("users", "id"),
    ]
    for table, col in cascade:
        try:
            cursor = conn.execute(
                f"DELETE FROM {table} WHERE {col} = ?",  # noqa: S608 — hardcoded list
                (user_id,),
            )
            counts[table] = cursor.rowcount
        except Exception:
            logger.exception("delete_user_cascade: %s", table)
            counts[table] = -1
    conn.commit()
    return counts


def ticket_stats(days: int = 30) -> dict[str, Any]:
    """Aggregate ticket statistics for the admin dashboard."""
    conn = _get_connection()
    cutoff = time.time() - (days * 86400)
    row = conn.execute(
        """SELECT
             COUNT(*) as total,
             COALESCE(SUM(CASE WHEN status='open'     THEN 1 ELSE 0 END), 0) as open_count,
             COALESCE(SUM(CASE WHEN status='assigned' THEN 1 ELSE 0 END), 0) as assigned_count,
             COALESCE(SUM(CASE WHEN status='resolved' THEN 1 ELSE 0 END), 0) as resolved_count,
             COALESCE(SUM(CASE WHEN status='wontfix'  THEN 1 ELSE 0 END), 0) as wontfix_count
           FROM tickets
           WHERE created_at >= ?""",
        (cutoff,),
    ).fetchone()
    by_priority = conn.execute(
        """SELECT priority, COUNT(*) as cnt FROM tickets
           WHERE created_at >= ?
           GROUP BY priority ORDER BY cnt DESC""",
        (cutoff,),
    ).fetchall()
    return {
        "period_days": days,
        "total": row["total"] or 0,
        "open": row["open_count"] or 0,
        "assigned": row["assigned_count"] or 0,
        "resolved": row["resolved_count"] or 0,
        "wontfix": row["wontfix_count"] or 0,
        "by_priority": {r["priority"]: r["cnt"] for r in by_priority},
    }


# ---------------------------------------------------------------------------
# Backend dispatch — when ANALYTICS_BACKEND=postgres, re-bind the module's
# public names to the postgres implementations so callers can continue to
# `from .database import log_conversation, ...` unchanged.
# ---------------------------------------------------------------------------
if ANALYTICS_BACKEND == "postgres":
    try:
        from . import postgres as _pg

        init_db = _pg.init_db  # type: ignore
        cleanup_expired_data = _pg.cleanup_expired_data  # type: ignore
        save_feedback = _pg.save_feedback  # type: ignore
        update_feedback_comment = _pg.update_feedback_comment  # type: ignore
        get_feedback_summary = _pg.get_feedback_summary  # type: ignore
        track_event = _pg.track_event  # type: ignore
        get_event_counts = _pg.get_event_counts  # type: ignore
        upsert_session = _pg.upsert_session  # type: ignore
        get_session_stats = _pg.get_session_stats  # type: ignore
        log_conversation = _pg.log_conversation  # type: ignore
        get_recent_turns = _pg.get_recent_turns  # type: ignore
        get_conversation_stats = _pg.get_conversation_stats  # type: ignore
        export_review_feedback = _pg.export_review_feedback  # type: ignore
        logger.info("Analytics backend: postgres")
    except Exception:
        logger.exception("Postgres backend requested but import failed; falling back to sqlite")
else:
    logger.info("Analytics backend: sqlite")

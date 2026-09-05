"""PostgreSQL analytics backend (opt-in via ``ANALYTICS_BACKEND=postgres``).

This module mirrors the public surface of :mod:`database` so it can be
drop-in substituted without the rest of the codebase knowing or caring
which backend is active.  Postgres is the correct choice for any
deployment with more than one API replica — SQLite with thread-local
connections cannot coordinate writes across processes.

Schema is intentionally identical to :mod:`database` (same columns,
same indexes) so you can migrate data with a one-shot export.

Environment variables:
    ANALYTICS_BACKEND       – "sqlite" (default) or "postgres"
    POSTGRES_DSN            – psycopg PostgreSQL connection string
    POSTGRES_POOL_MIN       – min pool size (default: 1)
    POSTGRES_POOL_MAX       – max pool size (default: 10)

Install the optional dependency when enabling this backend::

    uv pip install "psycopg[binary]>=3.2"
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

POSTGRES_DSN = os.getenv("POSTGRES_DSN", "")
POOL_MIN = int(os.getenv("POSTGRES_POOL_MIN", "1"))
POOL_MAX = int(os.getenv("POSTGRES_POOL_MAX", "10"))

# Retention TTLs — reused from database.py defaults so migrated deploys
# behave identically.
_CONVERSATION_TTL_DAYS = int(os.getenv("CONVERSATION_TTL_DAYS", "7"))
_ANALYTICS_TTL_DAYS = int(os.getenv("ANALYTICS_TTL_DAYS", "365"))
_FEEDBACK_TTL_DAYS = int(os.getenv("FEEDBACK_TTL_DAYS", "90"))
_SESSION_TTL_DAYS = int(os.getenv("SESSION_TTL_DAYS", "30"))
_TICKET_TTL_DAYS = int(os.getenv("TICKET_TTL_DAYS", "90"))

_pool: Any = None


def _get_pool() -> Any:
    """Lazy-init a psycopg ConnectionPool.  Returns None if unavailable."""
    global _pool
    if _pool is not None:
        return _pool
    if not POSTGRES_DSN:
        logger.warning("POSTGRES_DSN is empty; postgres backend disabled")
        return None
    try:
        from psycopg_pool import ConnectionPool  # type: ignore

        _pool = ConnectionPool(
            conninfo=POSTGRES_DSN,
            min_size=POOL_MIN,
            max_size=POOL_MAX,
            open=True,
            kwargs={"autocommit": False},
        )
        logger.info("Postgres pool ready (min=%d max=%d)", POOL_MIN, POOL_MAX)
        return _pool
    except ImportError:
        logger.warning("psycopg / psycopg_pool not installed; postgres disabled")
        return None
    except Exception:
        logger.exception("Postgres pool init failed")
        return None


def init_db() -> None:
    """Create tables and indexes if they don't exist."""
    pool = _get_pool()
    if pool is None:
        raise RuntimeError("Postgres backend requested but pool unavailable")

    ddl = """
    CREATE TABLE IF NOT EXISTS feedback (
        id          TEXT PRIMARY KEY,
        message_id  TEXT NOT NULL,
        session_id  TEXT,
        user_id     TEXT DEFAULT '',
        rating      TEXT NOT NULL CHECK (rating IN ('up','down')),
        comment     TEXT DEFAULT '',
        user_query  TEXT DEFAULT '',
        bot_reply   TEXT DEFAULT '',
        created_at  DOUBLE PRECISION NOT NULL
    );

    CREATE TABLE IF NOT EXISTS analytics_events (
        id          TEXT PRIMARY KEY,
        session_id  TEXT,
        user_id     TEXT DEFAULT '',
        event_type  TEXT NOT NULL,
        event_data  TEXT DEFAULT '{}',
        created_at  DOUBLE PRECISION NOT NULL
    );

    CREATE TABLE IF NOT EXISTS sessions (
        id              TEXT PRIMARY KEY,
        user_id         TEXT DEFAULT '',
        started_at      DOUBLE PRECISION NOT NULL,
        last_active_at  DOUBLE PRECISION NOT NULL,
        message_count   INTEGER DEFAULT 0,
        user_agent      TEXT DEFAULT '',
        platform        TEXT DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS conversations (
        id               TEXT PRIMARY KEY,
        conversation_id  TEXT,
        session_id       TEXT,
        user_message     TEXT NOT NULL,
        bot_reply        TEXT NOT NULL,
        sources          TEXT DEFAULT '[]',
        response_time_ms DOUBLE PRECISION DEFAULT 0,
        confidence       DOUBLE PRECISION DEFAULT 0,
        topic_tag        TEXT DEFAULT '',
        contexts         TEXT DEFAULT '[]',
        user_id          TEXT DEFAULT '',
        created_at       DOUBLE PRECISION NOT NULL
    );

    -- Escalation tickets.  These MUST live in the same store as
    -- `conversations`: a ticket references a conversation_id, and the
    -- officer view joins the two to show the transcript.  Left on
    -- SQLite they would be a per-replica file — invisible to officers
    -- hitting another pod, lost on restart, and pointing at a
    -- conversation in a different database.
    CREATE TABLE IF NOT EXISTS tickets (
        id                  TEXT PRIMARY KEY,
        conversation_id     TEXT,
        session_id          TEXT,
        status              TEXT NOT NULL DEFAULT 'open'
                            CHECK(status IN ('open','assigned','resolved','wontfix')),
        priority            TEXT NOT NULL DEFAULT 'normal'
                            CHECK(priority IN ('low','normal','high','urgent')),
        reason              TEXT DEFAULT '',
        user_query          TEXT DEFAULT '',
        bot_reply           TEXT DEFAULT '',
        handoff_json        TEXT DEFAULT '{}',
        response_judge_json TEXT DEFAULT '{}',
        transcript_json     TEXT DEFAULT '[]',
        user_id             TEXT DEFAULT '',
        team                TEXT DEFAULT '',
        officer_reply       TEXT DEFAULT '',
        reply_at            DOUBLE PRECISION DEFAULT 0,
        reply_delivered_at  DOUBLE PRECISION DEFAULT 0,
        first_response_at   DOUBLE PRECISION DEFAULT 0,
        resolved_at         DOUBLE PRECISION DEFAULT 0,
        assignee            TEXT DEFAULT '',
        staff_note          TEXT DEFAULT '',
        created_at          DOUBLE PRECISION NOT NULL,
        updated_at          DOUBLE PRECISION NOT NULL
    );


    -- Identity, tenancy and consent.  These were absent entirely, so on
    -- the backend production mandates every one of them resolved to a
    -- per-replica SQLite file: a user who withdrew consent on one pod
    -- was still processed as consenting on every other, erasure reached
    -- one pod's rows, and subject-access returned a fraction of the data.
    CREATE TABLE IF NOT EXISTS tenants (
        id            TEXT PRIMARY KEY,
        display_name  TEXT NOT NULL,
        created_at    DOUBLE PRECISION NOT NULL
    );

    CREATE TABLE IF NOT EXISTS users (
        id            TEXT PRIMARY KEY,
        tenant_id     TEXT NOT NULL DEFAULT 'default',
        external_id   TEXT NOT NULL,
        email         TEXT DEFAULT '',
        role          TEXT NOT NULL DEFAULT 'public'
                      CHECK(role IN ('public','verified_taxpayer','ura_staff','ura_admin','ura_auditor')),
        created_at    DOUBLE PRECISION NOT NULL,
        last_seen_at  DOUBLE PRECISION NOT NULL,
        UNIQUE(tenant_id, external_id)
    );

    CREATE TABLE IF NOT EXISTS user_profiles (
        user_id              TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        taxpayer_type        TEXT DEFAULT 'unknown',
        industry             TEXT DEFAULT '',
        primary_language     TEXT DEFAULT 'en',
        detail_level         TEXT DEFAULT 'intermediate',
        registered_tax_types TEXT DEFAULT '[]',
        fiscal_year          TEXT DEFAULT 'FY2025-26',
        display_name         TEXT DEFAULT '',
        updated_at           DOUBLE PRECISION NOT NULL
    );

    CREATE TABLE IF NOT EXISTS consent_receipts (
        receipt_id    TEXT PRIMARY KEY,
        user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        purpose       TEXT NOT NULL,
        version       TEXT NOT NULL,
        granted_at    DOUBLE PRECISION NOT NULL,
        withdrawn_at  DOUBLE PRECISION,
        legal_basis   TEXT DEFAULT 'consent'
    );

    CREATE TABLE IF NOT EXISTS ticket_presence (
        ticket_id  TEXT NOT NULL,
        viewer     TEXT NOT NULL,
        updated_at DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (ticket_id, viewer)
    );

    CREATE TABLE IF NOT EXISTS flag_overrides (
        name       TEXT PRIMARY KEY,
        enabled    BOOLEAN NOT NULL,
        updated_at DOUBLE PRECISION NOT NULL
    );

    CREATE TABLE IF NOT EXISTS reminder_inbox (
        id            TEXT PRIMARY KEY,
        user_id       TEXT NOT NULL,
        deadline_name TEXT NOT NULL,
        due_date      TEXT NOT NULL,
        message       TEXT NOT NULL,
        created_at    DOUBLE PRECISION NOT NULL,
        read_at       DOUBLE PRECISION DEFAULT 0,
        UNIQUE(user_id, deadline_name, due_date)
    );

    CREATE TABLE IF NOT EXISTS notification_outbox (
        id         TEXT PRIMARY KEY,
        user_id    TEXT NOT NULL,
        channel    TEXT NOT NULL,
        provider   TEXT NOT NULL DEFAULT 'mock',
        payload    TEXT NOT NULL,
        status     TEXT NOT NULL DEFAULT 'queued',
        created_at DOUBLE PRECISION NOT NULL
    );

    CREATE TABLE IF NOT EXISTS answer_overrides (
        id           TEXT PRIMARY KEY,
        match_query  TEXT NOT NULL UNIQUE,
        reply        TEXT NOT NULL,
        source_url   TEXT DEFAULT '',
        created_by   TEXT DEFAULT '',
        enabled      BOOLEAN NOT NULL DEFAULT TRUE,
        updated_at   DOUBLE PRECISION NOT NULL
    );

    CREATE TABLE IF NOT EXISTS conversation_topics (
        conversation_id TEXT PRIMARY KEY,
        topic_id        TEXT NOT NULL,
        label           TEXT NOT NULL,
        tax_type        TEXT DEFAULT '',
        confidence      DOUBLE PRECISION DEFAULT 0,
        updated_at      DOUBLE PRECISION NOT NULL
    );

    CREATE TABLE IF NOT EXISTS workflow_sessions (
        conversation_id  TEXT PRIMARY KEY,
        workflow_id      TEXT NOT NULL,
        status           TEXT NOT NULL DEFAULT 'active'
                         CHECK(status IN ('active','completed','cancelled')),
        current_step_idx INTEGER NOT NULL DEFAULT 0,
        slots_json       TEXT DEFAULT '{}',
        last_prompt      TEXT DEFAULT '',
        created_at       DOUBLE PRECISION NOT NULL,
        updated_at       DOUBLE PRECISION NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_feedback_message      ON feedback(message_id);
    CREATE INDEX IF NOT EXISTS idx_feedback_created      ON feedback(created_at);
    CREATE INDEX IF NOT EXISTS idx_events_type           ON analytics_events(event_type);
    CREATE INDEX IF NOT EXISTS idx_events_session        ON analytics_events(session_id);
    CREATE INDEX IF NOT EXISTS idx_events_created        ON analytics_events(created_at);
    CREATE INDEX IF NOT EXISTS idx_sessions_active       ON sessions(last_active_at);
    CREATE INDEX IF NOT EXISTS idx_conversations_session ON conversations(session_id);
    CREATE INDEX IF NOT EXISTS idx_conversations_thread  ON conversations(conversation_id);
    CREATE INDEX IF NOT EXISTS idx_conversations_created ON conversations(created_at);
    CREATE INDEX IF NOT EXISTS idx_tickets_status        ON tickets(status);
    CREATE INDEX IF NOT EXISTS idx_tickets_created       ON tickets(created_at);
    CREATE INDEX IF NOT EXISTS idx_tickets_conversation  ON tickets(conversation_id);
    CREATE INDEX IF NOT EXISTS idx_users_external        ON users(tenant_id, external_id);
    CREATE INDEX IF NOT EXISTS idx_consent_user          ON consent_receipts(user_id, purpose);
    CREATE INDEX IF NOT EXISTS idx_consent_active        ON consent_receipts(user_id, withdrawn_at);
    """

    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
            cur.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS conversation_id TEXT")
            cur.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS contexts TEXT DEFAULT '[]'")
            cur.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS user_id TEXT DEFAULT ''")
            cur.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS flag_variants TEXT DEFAULT '{}'")
            cur.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS locale TEXT DEFAULT ''")
            cur.execute("ALTER TABLE feedback ADD COLUMN IF NOT EXISTS user_id TEXT DEFAULT ''")
            cur.execute("ALTER TABLE analytics_events ADD COLUMN IF NOT EXISTS user_id TEXT DEFAULT ''")
            cur.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS user_id TEXT DEFAULT ''")
            cur.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS transcript_json TEXT DEFAULT '[]'")
            cur.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS user_id TEXT DEFAULT ''")
            for _col, _ddl in (
                ("officer_reply", "TEXT DEFAULT ''"),
                ("reply_at", "DOUBLE PRECISION DEFAULT 0"),
                ("reply_delivered_at", "DOUBLE PRECISION DEFAULT 0"),
                ("first_response_at", "DOUBLE PRECISION DEFAULT 0"),
                ("resolved_at", "DOUBLE PRECISION DEFAULT 0"),
                ("team", "TEXT DEFAULT ''"),
            ):
                cur.execute(f"ALTER TABLE tickets ADD COLUMN IF NOT EXISTS {_col} {_ddl}")
            cur.execute(
                "UPDATE conversations SET conversation_id = id WHERE conversation_id IS NULL OR conversation_id = ''"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversations_thread ON conversations(conversation_id)"
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_feedback_user ON feedback(user_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_events_user ON analytics_events(user_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")
        conn.commit()
    logger.info("Postgres analytics schema ready")
    cleanup_expired_data()


def cleanup_expired_data() -> dict[str, int]:
    pool = _get_pool()
    if pool is None:
        return {}
    now = time.time()
    ttls = [
        ("conversations", _CONVERSATION_TTL_DAYS, "created_at"),
        ("analytics_events", _ANALYTICS_TTL_DAYS, "created_at"),
        ("feedback", _FEEDBACK_TTL_DAYS, "created_at"),
        ("sessions", _SESSION_TTL_DAYS, "last_active_at"),
        ("conversation_topics", _CONVERSATION_TTL_DAYS, "updated_at"),
        ("ticket_presence", 1, "updated_at"),
    ]
    deleted: dict[str, int] = {}
    with pool.connection() as conn:
        for table, ttl_days, col in ttls:
            cutoff = now - (ttl_days * 86400)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"DELETE FROM {table} WHERE {col} < %s",  # noqa: S608 whitelist
                        (cutoff,),
                    )
                    deleted[table] = cur.rowcount
                conn.commit()
            except Exception:
                conn.rollback()
                logger.exception("TTL cleanup failed for %s", table)
                deleted[table] = 0
        ticket_cutoff = now - (_TICKET_TTL_DAYS * 86400)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM tickets WHERE status IN ('resolved', 'wontfix') "
                    "AND resolved_at > 0 AND resolved_at < %s",
                    (ticket_cutoff,),
                )
                deleted["tickets"] = cur.rowcount
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception("TTL cleanup failed for tickets")
            deleted["tickets"] = 0
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
    user_id: str = "",
) -> dict[str, Any]:
    pool = _get_pool()
    if pool is None:
        raise RuntimeError("postgres unavailable")
    from .guardrails import redact_pii_text

    comment = redact_pii_text(comment)
    user_query = redact_pii_text(user_query)
    bot_reply = redact_pii_text(bot_reply)
    fb_id = str(uuid.uuid4())
    now = time.time()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO feedback (id, message_id, session_id, user_id, rating,
                    comment, user_query, bot_reply, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (fb_id, message_id, session_id, user_id or "", rating, comment, user_query, bot_reply, now),
            )
        conn.commit()
    return {"id": fb_id, "message_id": message_id, "rating": rating, "created_at": now}


def update_feedback_comment(message_id: str, comment: str, user_id: str = "") -> bool:
    pool = _get_pool()
    if pool is None:
        return False
    from .guardrails import redact_pii_text

    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE feedback SET comment = %s
                   WHERE id = (
                     SELECT id FROM feedback
                     WHERE message_id = %s AND user_id = %s AND comment = ''
                     ORDER BY created_at DESC LIMIT 1
                   )""",
                (redact_pii_text(comment), message_id, user_id or ""),
            )
            rowcount = cur.rowcount
        conn.commit()
    return rowcount > 0


def get_feedback_summary(days: int = 30) -> dict[str, Any]:
    pool = _get_pool()
    if pool is None:
        return {
            "period_days": days,
            "total": 0,
            "thumbs_up": 0,
            "thumbs_down": 0,
            "satisfaction_pct": 0.0,
            "recent": [],
        }
    cutoff = time.time() - (days * 86400)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT COUNT(*),
                          COALESCE(SUM(CASE WHEN rating='up'  THEN 1 ELSE 0 END), 0),
                          COALESCE(SUM(CASE WHEN rating='down' THEN 1 ELSE 0 END), 0)
                   FROM feedback WHERE created_at >= %s""",
            (cutoff,),
        )
        row = cur.fetchone()
        total = row[0] or 0
        up = row[1] or 0
        down = row[2] or 0

        cur.execute(
            """SELECT id, message_id, rating, comment, user_query, created_at
                   FROM feedback WHERE created_at >= %s
                   ORDER BY created_at DESC LIMIT 20""",
            (cutoff,),
        )
        recent = [
            {
                "id": r[0],
                "message_id": r[1],
                "rating": r[2],
                "comment": r[3],
                "user_query": r[4],
                "created_at": r[5],
            }
            for r in cur.fetchall()
        ]
    satisfaction = round(up / total * 100, 1) if total > 0 else 0.0
    return {
        "period_days": days,
        "total": total,
        "thumbs_up": up,
        "thumbs_down": down,
        "satisfaction_pct": satisfaction,
        "recent": recent,
    }


# ---------------------------------------------------------------------------
# Analytics events
# ---------------------------------------------------------------------------
def track_event(
    event_type: str,
    event_data: str = "{}",
    session_id: str | None = None,
    user_id: str = "",
) -> None:
    pool = _get_pool()
    if pool is None:
        return
    from .guardrails import redact_pii_text

    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO analytics_events (id, session_id, user_id, event_type, event_data, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (
                    str(uuid.uuid4()),
                    session_id,
                    user_id or "",
                    event_type,
                    redact_pii_text(event_data),
                    time.time(),
                ),
            )
        conn.commit()


def get_event_counts(days: int = 30) -> dict[str, int]:
    pool = _get_pool()
    if pool is None:
        return {}
    cutoff = time.time() - (days * 86400)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT event_type, COUNT(*) FROM analytics_events
                   WHERE created_at >= %s
                   GROUP BY event_type ORDER BY COUNT(*) DESC""",
            (cutoff,),
        )
        return {r[0]: r[1] for r in cur.fetchall()}


# ---------------------------------------------------------------------------
# Session tracking
# ---------------------------------------------------------------------------
def upsert_session(
    session_id: str,
    user_agent: str = "",
    platform: str = "",
    user_id: str = "",
) -> None:
    pool = _get_pool()
    if pool is None:
        return
    now = time.time()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO sessions (id, user_id, started_at, last_active_at,
                       message_count, user_agent, platform)
                   VALUES (%s,%s,%s,%s,1,%s,%s)
                   ON CONFLICT (id) DO UPDATE SET
                     user_id = CASE WHEN EXCLUDED.user_id <> '' THEN EXCLUDED.user_id ELSE sessions.user_id END,
                     last_active_at = EXCLUDED.last_active_at,
                     message_count  = sessions.message_count + 1""",
                (session_id, user_id or "", now, now, user_agent, platform),
            )
        conn.commit()


def get_session_stats(days: int = 30) -> dict[str, Any]:
    pool = _get_pool()
    if pool is None:
        return {
            "period_days": days,
            "total_sessions": 0,
            "avg_messages_per_session": 0.0,
            "max_messages_in_session": 0,
        }
    cutoff = time.time() - (days * 86400)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT COUNT(*), COALESCE(AVG(message_count),0),
                          COALESCE(MAX(message_count),0)
                   FROM sessions WHERE last_active_at >= %s""",
            (cutoff,),
        )
        row = cur.fetchone()
    return {
        "period_days": days,
        "total_sessions": row[0] or 0,
        "avg_messages_per_session": round(row[1] or 0, 1),
        "max_messages_in_session": row[2] or 0,
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
    contexts: str = "[]",
    response_time_ms: float = 0,
    confidence: float = 0,
    topic_tag: str = "",
    user_id: str = "",
    flag_variants: str = "{}",
    locale: str = "",
) -> str:
    """Mirrors :func:`database.log_conversation` exactly.

    ``contexts`` and ``user_id`` were missing here while every caller
    passed them, so this raised ``TypeError`` on the Postgres backend —
    swallowed by the callers' ``except Exception``.  The effect was that
    the backend production mandates logged **no conversations at all**:
    no multi-turn memory, no transcript to attach to an escalation, and
    nothing for :func:`database.delete_user_cascade` to erase.
    """
    pool = _get_pool()
    if pool is None:
        raise RuntimeError("postgres unavailable")
    from .guardrails import redact_pii_text

    user_message = redact_pii_text(user_message)
    bot_reply = redact_pii_text(bot_reply)
    row_id = str(uuid.uuid4())
    thread_id = conversation_id or row_id
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO conversations (id, conversation_id, session_id, user_message, bot_reply,
                       sources, contexts, response_time_ms, confidence, topic_tag,
                       user_id, flag_variants, locale, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    row_id,
                    thread_id,
                    session_id,
                    user_message,
                    bot_reply,
                    sources,
                    contexts,
                    response_time_ms,
                    confidence,
                    topic_tag,
                    user_id,
                    flag_variants or "{}",
                    locale or "",
                    time.time(),
                ),
            )
        conn.commit()
    return thread_id


def get_recent_turns(
    session_id: str | None = None,
    conversation_id: str | None = None,
    limit: int = 5,
    user_id: str | None = None,
) -> list[dict[str, str]]:
    pool = _get_pool()
    if pool is None:
        return []
    if conversation_id:
        if user_id:
            sql = """SELECT user_message, bot_reply FROM conversations
                       WHERE conversation_id = %s AND user_id = %s
                       ORDER BY created_at DESC LIMIT %s"""
            args: tuple[Any, ...] = (conversation_id, user_id, limit)
        else:
            sql = """SELECT user_message, bot_reply FROM conversations
                       WHERE conversation_id = %s
                       ORDER BY created_at DESC LIMIT %s"""
            args = (conversation_id, limit)
    elif session_id:
        if user_id:
            sql = """SELECT user_message, bot_reply FROM conversations
                       WHERE session_id = %s AND user_id = %s
                       ORDER BY created_at DESC LIMIT %s"""
            args = (session_id, user_id, limit)
        else:
            sql = """SELECT user_message, bot_reply FROM conversations
                       WHERE session_id = %s
                       ORDER BY created_at DESC LIMIT %s"""
            args = (session_id, limit)
    else:
        return []
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        rows = cur.fetchall()
    return [{"user_message": r[0], "bot_reply": r[1]} for r in reversed(rows)]


def get_conversation_context(
    session_id: str | None = None,
    conversation_id: str | None = None,
    recent_limit: int = 6,
    max_history: int = 25,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Retrieve multi-turn conversation history and build rolling context & summary."""
    from .context_manager import RollingContextManager, context_manager

    turns = get_recent_turns(
        session_id=session_id,
        conversation_id=conversation_id,
        limit=max_history,
        user_id=user_id,
    )
    mgr = RollingContextManager(recent_limit=recent_limit) if recent_limit != context_manager.recent_limit else context_manager
    ctx = mgr.build_context(
        turns,
        conversation_id=conversation_id or session_id or "",
    )
    return {
        "recent_turns": ctx.recent_turns,
        "context_summary": ctx.context_summary,
        "active_entities": ctx.active_entities,
        "total_turns": ctx.total_turns,
        "all_turns": ctx.all_turns,
    }


def get_conversation_topic(conversation_id: str) -> dict[str, Any] | None:
    pool = _get_pool()
    if pool is None:
        return None
    cid = (conversation_id or "").strip()
    if not cid:
        return None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT conversation_id, topic_id, label, tax_type, confidence, updated_at
               FROM conversation_topics WHERE conversation_id = %s""",
            (cid,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "conversation_id": row[0],
        "topic_id": row[1],
        "label": row[2],
        "tax_type": row[3] or "",
        "confidence": float(row[4] or 0),
        "updated_at": float(row[5] or 0),
    }


def upsert_conversation_topic(
    conversation_id: str,
    *,
    topic_id: str,
    label: str,
    tax_type: str = "",
    confidence: float = 0.0,
) -> None:
    pool = _get_pool()
    if pool is None:
        return
    cid = (conversation_id or "").strip()
    if not cid or not topic_id:
        return
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO conversation_topics
               (conversation_id, topic_id, label, tax_type, confidence, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (conversation_id) DO UPDATE SET
                 topic_id = EXCLUDED.topic_id,
                 label = EXCLUDED.label,
                 tax_type = EXCLUDED.tax_type,
                 confidence = EXCLUDED.confidence,
                 updated_at = EXCLUDED.updated_at""",
            (cid, topic_id, label, tax_type or "", float(confidence), time.time()),
        )
        conn.commit()


def clear_conversation_topic(conversation_id: str) -> None:
    pool = _get_pool()
    if pool is None:
        return
    cid = (conversation_id or "").strip()
    if not cid:
        return
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM conversation_topics WHERE conversation_id = %s", (cid,))
        conn.commit()


def get_conversation_stats(days: int = 30) -> dict[str, Any]:
    pool = _get_pool()
    if pool is None:
        return {
            "period_days": days,
            "total_conversations": 0,
            "avg_response_time_ms": 0.0,
            "avg_confidence": 0.0,
            "top_topics": [],
        }
    cutoff = time.time() - (days * 86400)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT COUNT(*), COALESCE(AVG(response_time_ms),0),
                          COALESCE(AVG(confidence),0)
                   FROM conversations WHERE created_at >= %s""",
            (cutoff,),
        )
        row = cur.fetchone()

        cur.execute(
            """SELECT topic_tag, COUNT(*) FROM conversations
                   WHERE created_at >= %s AND topic_tag <> ''
                   GROUP BY topic_tag ORDER BY COUNT(*) DESC LIMIT 10""",
            (cutoff,),
        )
        top = [{"tag": r[0], "count": r[1]} for r in cur.fetchall()]

    return {
        "period_days": days,
        "total_conversations": row[0] or 0,
        "avg_response_time_ms": round(row[1] or 0, 1),
        "avg_confidence": round(row[2] or 0, 3),
        "top_topics": top,
    }


def export_review_feedback(days: int = 30) -> list[dict[str, Any]]:
    pool = _get_pool()
    if pool is None:
        return []
    cutoff = time.time() - (days * 86400)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT message_id, user_query, bot_reply, comment, created_at,
                          'thumbs_down'
                   FROM feedback
                   WHERE rating='down' AND created_at >= %s
                   ORDER BY created_at DESC""",
            (cutoff,),
        )
        down = [
            {
                "message_id": r[0],
                "user_query": r[1],
                "bot_reply": r[2],
                "comment": r[3],
                "created_at": r[4],
                "review_reason": r[5],
            }
            for r in cur.fetchall()
        ]
        cur.execute(
            """SELECT id, user_message, bot_reply, '', created_at, 'low_confidence'
                   FROM conversations
                   WHERE confidence < 0.3 AND confidence > 0 AND created_at >= %s
                   ORDER BY created_at DESC LIMIT 200""",
            (cutoff,),
        )
        low = [
            {
                "message_id": r[0],
                "user_query": r[1],
                "bot_reply": r[2],
                "comment": r[3],
                "created_at": r[4],
                "review_reason": r[5],
            }
            for r in cur.fetchall()
        ]
    return down + low


# ---------------------------------------------------------------------------
# Escalation tickets
#
# Mirrors the SQLite implementations in :mod:`database`.  These exist
# because production mandates ``ANALYTICS_BACKEND=postgres``: without a
# Postgres mirror the dispatch block leaves tickets on a per-replica
# SQLite file, so an officer polling the admin API sees whichever pod
# the load balancer picked, tickets vanish on restart, and the
# conversation a ticket points at lives in a different database.
# ---------------------------------------------------------------------------
#: Queue view — deliberately excludes transcript_json.  A list of 50
#: tickets should not ship 50 full conversations; the detail view has it.
_TICKET_COLUMNS = (
    "id, conversation_id, session_id, status, priority, reason, "
    "user_query, bot_reply, handoff_json, response_judge_json, "
    "assignee, staff_note, created_at, updated_at, user_id, team, "
    "officer_reply, reply_at, reply_delivered_at, first_response_at, resolved_at"
)
#: Detail view — the transcript is the point of the ticket.
_TICKET_COLUMNS_FULL = _TICKET_COLUMNS + ", transcript_json"


def _row_to_ticket(row: tuple[Any, ...], columns: str = _TICKET_COLUMNS) -> dict[str, Any]:
    """Map a row onto the same dict shape :mod:`database` returns."""
    ticket = dict(zip(columns.replace(" ", "").split(","), row, strict=True))
    from .database import _redact_ticket_value

    for field in ("reason", "user_query", "bot_reply", "staff_note", "officer_reply"):
        if field in ticket:
            ticket[field] = _redact_ticket_value(ticket[field])
    ticket["handoff"] = _redact_ticket_value(_loads(ticket.pop("handoff_json", "{}"), {}))
    ticket["response_judge"] = _redact_ticket_value(
        _loads(ticket.pop("response_judge_json", "{}"), {})
    )
    ticket["transcript"] = _redact_ticket_value(_loads(ticket.pop("transcript_json", "[]"), []))
    return ticket


def _loads(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def create_ticket(
    reason: str,
    user_query: str = "",
    bot_reply: str = "",
    session_id: str | None = None,
    conversation_id: str | None = None,
    priority: str = "normal",
    handoff: dict[str, Any] | None = None,
    response_judge: dict[str, Any] | None = None,
    transcript: list[dict[str, Any]] | None = None,
    user_id: str = "",
    team: str = "",
) -> dict[str, Any]:
    if priority not in ("low", "normal", "high", "urgent"):
        logger.warning("create_ticket: invalid priority %r -> 'normal'", priority)
        priority = "normal"
    from .database import _redact_ticket_value

    reason = _redact_ticket_value(reason)
    user_query = _redact_ticket_value(user_query)
    bot_reply = _redact_ticket_value(bot_reply)
    handoff = _redact_ticket_value(handoff or {})
    response_judge = _redact_ticket_value(response_judge or {})
    transcript = _redact_ticket_value(transcript or [])
    pool = _get_pool()
    if pool is None:
        raise RuntimeError("postgres unavailable")
    ticket_id = str(uuid.uuid4())
    now = time.time()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO tickets (id, conversation_id, session_id, status, priority,
                                        reason, user_query, bot_reply,
                                        handoff_json, response_judge_json, transcript_json,
                                        user_id, team, assignee, staff_note,
                                        created_at, updated_at)
                   VALUES (%s,%s,%s,'open',%s,%s,%s,%s,%s,%s,%s,%s,%s,'','',%s,%s)""",
                (
                    ticket_id,
                    conversation_id,
                    session_id,
                    priority,
                    reason,
                    user_query,
                    bot_reply,
                    json.dumps(handoff),
                    json.dumps(response_judge),
                    json.dumps(transcript),
                    user_id,
                    team,
                    now,
                    now,
                ),
            )
        conn.commit()
    logger.info("ticket %s created (priority=%s)", ticket_id, priority)
    return {
        "id": ticket_id,
        "status": "open",
        "priority": priority,
        "reason": reason,
        "handoff": handoff,
        "response_judge": response_judge,
        "transcript": transcript,
        "team": team,
        "created_at": now,
    }


def list_tickets(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    priority: str | None = None,
    team: str | None = None,
) -> list[dict[str, Any]]:
    pool = _get_pool()
    if pool is None:
        return []
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    sql = f"SELECT {_TICKET_COLUMNS} FROM tickets"
    params: list[Any] = []
    if status:
        sql += " WHERE status = %s"
        params.append(status)
    if priority:
        sql += " AND priority = %s" if (status or params) else " WHERE priority = %s"
        params.append(priority)
    if team:
        sql += " AND team = %s" if (status or params) else " WHERE team = %s"
        params.append(team)
    sql += (
        " ORDER BY CASE priority"
        "   WHEN 'urgent' THEN 0 WHEN 'high' THEN 1"
        "   WHEN 'normal' THEN 2 ELSE 3 END,"
        " created_at ASC LIMIT %s OFFSET %s"
    )
    params.extend([limit, offset])
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [_row_to_ticket(r) for r in cur.fetchall()]


def find_open_ticket(conversation_id: str) -> dict[str, Any] | None:
    """Postgres mirror of :func:`database.find_open_ticket`."""
    pool = _get_pool()
    if pool is None or not conversation_id:
        return None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT {_TICKET_COLUMNS_FULL} FROM tickets
                WHERE conversation_id = %s AND status IN ('open','assigned')
                ORDER BY created_at DESC LIMIT 1""",
            (conversation_id,),
        )
        row = cur.fetchone()
    return _row_to_ticket(row, _TICKET_COLUMNS_FULL) if row else None


def get_ticket(ticket_id: str) -> dict[str, Any] | None:
    pool = _get_pool()
    if pool is None:
        return None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_TICKET_COLUMNS_FULL} FROM tickets WHERE id = %s", (ticket_id,))
        row = cur.fetchone()
    return _row_to_ticket(row, _TICKET_COLUMNS_FULL) if row else None


def update_ticket(
    ticket_id: str,
    status: str | None = None,
    assignee: str | None = None,
    staff_note: str | None = None,
    priority: str | None = None,
    officer_reply: str | None = None,
) -> bool:
    pool = _get_pool()
    if pool is None:
        return False
    sets: list[str] = []
    params: list[Any] = []
    now = time.time()
    existing = get_ticket(ticket_id)
    if existing is None:
        return False
    is_touch = any(v is not None for v in (assignee, staff_note, officer_reply)) or (
        status is not None and status != "open"
    )
    if is_touch and not existing.get("first_response_at"):
        sets.append("first_response_at = %s")
        params.append(now)
    if status in ("resolved", "wontfix") and not existing.get("resolved_at"):
        sets.append("resolved_at = %s")
        params.append(now)
    if officer_reply is not None:
        from .database import _redact_ticket_value

        sets.append("officer_reply = %s")
        params.append(_redact_ticket_value(officer_reply)[:4000])
        sets.append("reply_at = %s")
        params.append(now)
    if status is not None:
        sets.append("status = %s")
        params.append(status)
    if assignee is not None:
        sets.append("assignee = %s")
        params.append(assignee)
    if staff_note is not None:
        sets.append("staff_note = %s")
        from .database import _redact_ticket_value

        params.append(_redact_ticket_value(staff_note))
    if priority is not None:
        sets.append("priority = %s")
        params.append(priority)
    if not sets:
        return False
    sets.append("updated_at = %s")
    params.extend([now, ticket_id])
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE tickets SET {', '.join(sets)} WHERE id = %s", params)
            touched = cur.rowcount > 0
        conn.commit()
    return touched


def ticket_stats(days: int = 30) -> dict[str, Any]:
    """Same shape and signature as :func:`database.ticket_stats`."""
    pool = _get_pool()
    empty = {
        "period_days": days,
        "total": 0,
        "open": 0,
        "assigned": 0,
        "resolved": 0,
        "wontfix": 0,
        "by_priority": {},
    }
    if pool is None:
        return empty
    cutoff = time.time() - (days * 86400)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT status, COUNT(*) FROM tickets
               WHERE created_at >= %s GROUP BY status""",
            (cutoff,),
        )
        by_status = {r[0]: r[1] for r in cur.fetchall()}
        cur.execute(
            """SELECT priority, COUNT(*) FROM tickets
               WHERE created_at >= %s GROUP BY priority ORDER BY COUNT(*) DESC""",
            (cutoff,),
        )
        by_priority = {r[0]: r[1] for r in cur.fetchall()}
    return {
        "period_days": days,
        "total": sum(by_status.values()),
        "open": by_status.get("open", 0),
        "assigned": by_status.get("assigned", 0),
        "resolved": by_status.get("resolved", 0),
        "wontfix": by_status.get("wontfix", 0),
        "by_priority": by_priority,
    }


def get_conversation_transcript(
    conversation_id: str | None = None,
    session_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Postgres mirror of :func:`database.get_conversation_transcript`."""
    pool = _get_pool()
    if pool is None:
        return []
    if conversation_id:
        where, key = "conversation_id = %s", conversation_id
    elif session_id:
        where, key = "session_id = %s", session_id
    else:
        return []
    limit = max(1, min(int(limit), 1000))
    sql = (
        "SELECT user_message, bot_reply, created_at, sources, topic_tag "
        f"FROM conversations WHERE {where} ORDER BY created_at DESC LIMIT %s"
    )
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (key, limit))
        rows = cur.fetchall()
    return [
        {
            "user_message": r[0],
            "bot_reply": r[1],
            "created_at": r[2],
            "sources": _loads(r[3], []),
            "topic_tag": r[4] or "",
        }
        for r in reversed(rows)
    ]


def pending_officer_reply(conversation_id: str) -> dict[str, Any] | None:
    """Postgres mirror of :func:`database.pending_officer_reply`."""
    pool = _get_pool()
    if pool is None or not conversation_id:
        return None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, officer_reply, reply_at, assignee, status FROM tickets
               WHERE conversation_id = %s AND officer_reply != ''
                 AND reply_delivered_at = 0
               ORDER BY reply_at ASC LIMIT 1""",
            (conversation_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return dict(
        zip(("id", "officer_reply", "reply_at", "assignee", "status"), row, strict=True)
    )


def mark_reply_delivered(ticket_id: str) -> bool:
    """Postgres mirror of :func:`database.mark_reply_delivered`."""
    pool = _get_pool()
    if pool is None or not ticket_id:
        return False
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tickets SET reply_delivered_at = %s "
                "WHERE id = %s AND reply_delivered_at = 0",
                (time.time(), ticket_id),
            )
            touched = cur.rowcount > 0
        conn.commit()
    return touched


def sla_stats(days: int = 30) -> dict[str, Any]:
    """Postgres mirror of :func:`database.sla_stats`."""
    from .database import compose_sla_stats

    pool = _get_pool()
    now = time.time()
    if pool is None:
        return compose_sla_stats(period_rows=[], open_rows=[], days=days, now=now)
    cutoff = now - (days * 86400)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT created_at, first_response_at, resolved_at, reply_at "
            "FROM tickets WHERE created_at >= %s",
            (cutoff,),
        )
        period = cur.fetchall()
        cur.execute(
            # assignee last — compose_sla_stats reads these tuples
            # positionally, so a new column has to go on the end.
            "SELECT created_at, first_response_at, reply_at, status, assignee "
            "FROM tickets WHERE status IN ('open', 'assigned')",
        )
        opened = cur.fetchall()
    return compose_sla_stats(period_rows=period, open_rows=opened, days=days, now=now)


def heartbeat_ticket_presence(ticket_id: str, viewer: str) -> None:
    cid = (ticket_id or "").strip()
    who = (viewer or "").strip()[:128]
    if not cid or not who:
        return
    pool = _get_pool()
    if pool is None:
        return
    now = time.time()
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO ticket_presence (ticket_id, viewer, updated_at)
               VALUES (%s, %s, %s)
               ON CONFLICT (ticket_id, viewer) DO UPDATE SET updated_at = EXCLUDED.updated_at""",
            (cid, who, now),
        )
        conn.commit()


def list_ticket_viewers(ticket_id: str, max_age: float | None = None) -> list[str]:
    from .database import PRESENCE_TTL_SECONDS

    if max_age is None:
        max_age = PRESENCE_TTL_SECONDS
    cid = (ticket_id or "").strip()
    if not cid:
        return []
    pool = _get_pool()
    if pool is None:
        return []
    cutoff = time.time() - max_age
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT viewer FROM ticket_presence
               WHERE ticket_id = %s AND updated_at >= %s
               ORDER BY updated_at DESC""",
            (cid, cutoff),
        )
        return [str(r[0]) for r in cur.fetchall()]


def load_flag_overrides() -> dict[str, bool]:
    pool = _get_pool()
    if pool is None:
        return {}
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT name, enabled FROM flag_overrides")
        return {str(r[0]): bool(r[1]) for r in cur.fetchall()}


def save_flag_override(name: str, enabled: bool) -> None:
    key = (name or "").strip()
    if not key:
        return
    pool = _get_pool()
    if pool is None:
        return
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO flag_overrides (name, enabled, updated_at)
               VALUES (%s, %s, %s)
               ON CONFLICT (name) DO UPDATE SET enabled = EXCLUDED.enabled, updated_at = EXCLUDED.updated_at""",
            (key, enabled, time.time()),
        )
        conn.commit()


def clear_flag_override(name: str) -> None:
    key = (name or "").strip()
    if not key:
        return
    pool = _get_pool()
    if pool is None:
        return
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM flag_overrides WHERE name = %s", (key,))
        conn.commit()


def upsert_reminder_inbox(
    user_id: str,
    deadline_name: str,
    due_date: str,
    message: str,
) -> dict[str, Any]:
    import uuid as _uuid

    uid = (user_id or "").strip()
    if not uid or not deadline_name or not due_date:
        return {}
    pool = _get_pool()
    if pool is None:
        return {}
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id FROM reminder_inbox
               WHERE user_id = %s AND deadline_name = %s AND due_date = %s""",
            (uid, deadline_name, due_date),
        )
        row = cur.fetchone()
        if row:
            rid = str(row[0])
            cur.execute("UPDATE reminder_inbox SET message = %s WHERE id = %s", (message, rid))
            conn.commit()
            return {
                "id": rid,
                "user_id": uid,
                "deadline_name": deadline_name,
                "due_date": due_date,
                "message": message,
            }
        rid = str(_uuid.uuid4())
        cur.execute(
            """INSERT INTO reminder_inbox
               (id, user_id, deadline_name, due_date, message, created_at, read_at)
               VALUES (%s, %s, %s, %s, %s, %s, 0)""",
            (rid, uid, deadline_name, due_date, message, time.time()),
        )
        conn.commit()
        return {
            "id": rid,
            "user_id": uid,
            "deadline_name": deadline_name,
            "due_date": due_date,
            "message": message,
        }


def list_reminder_inbox(user_id: str) -> list[dict[str, Any]]:
    uid = (user_id or "").strip()
    if not uid:
        return []
    pool = _get_pool()
    if pool is None:
        return []
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, user_id, deadline_name, due_date, message, created_at, read_at
               FROM reminder_inbox WHERE user_id = %s ORDER BY due_date ASC""",
            (uid,),
        )
        return [
            {
                "id": r[0],
                "user_id": r[1],
                "deadline_name": r[2],
                "due_date": r[3],
                "message": r[4],
                "created_at": r[5],
                "read_at": r[6],
            }
            for r in cur.fetchall()
        ]


def enqueue_notification(
    user_id: str,
    channel: str,
    payload: dict[str, Any],
    *,
    provider: str = "mock",
) -> dict[str, Any]:
    import json as _json
    import uuid as _uuid

    uid = (user_id or "").strip()
    ch = (channel or "").strip().lower()
    pool = _get_pool()
    if pool is None or not uid or not ch:
        return {}
    nid = str(_uuid.uuid4())
    now = time.time()
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO notification_outbox
               (id, user_id, channel, provider, payload, status, created_at)
               VALUES (%s, %s, %s, %s, %s, 'queued', %s)""",
            (nid, uid, ch, provider, _json.dumps(payload or {}), now),
        )
        conn.commit()
    return {
        "id": nid,
        "user_id": uid,
        "channel": ch,
        "provider": provider,
        "status": "queued",
        "live": False,
    }


def list_notification_outbox(user_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
    import json as _json

    pool = _get_pool()
    if pool is None:
        return []
    cap = max(1, min(limit, 200))
    with pool.connection() as conn, conn.cursor() as cur:
        if user_id:
            cur.execute(
                """SELECT id, user_id, channel, provider, payload, status, created_at
                   FROM notification_outbox WHERE user_id = %s
                   ORDER BY created_at DESC LIMIT %s""",
                (user_id, cap),
            )
        else:
            cur.execute(
                """SELECT id, user_id, channel, provider, payload, status, created_at
                   FROM notification_outbox ORDER BY created_at DESC LIMIT %s""",
                (cap,),
            )
        rows = cur.fetchall()
    out = []
    for r in rows:
        raw = r[4]
        if isinstance(raw, str):
            try:
                raw = _json.loads(raw)
            except _json.JSONDecodeError:
                pass
        out.append(
            {
                "id": r[0],
                "user_id": r[1],
                "channel": r[2],
                "provider": r[3],
                "payload": raw,
                "status": r[5],
                "created_at": r[6],
            }
        )
    return out


def get_answer_override(match_query: str) -> dict[str, Any] | None:
    key = (match_query or "").strip()
    pool = _get_pool()
    if pool is None or not key:
        return None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, match_query, reply, source_url, created_by, enabled, updated_at
               FROM answer_overrides WHERE match_query = %s AND enabled IS TRUE""",
            (key,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "match_query": row[1],
        "reply": row[2],
        "source_url": row[3],
        "created_by": row[4],
        "enabled": bool(row[5]),
        "updated_at": row[6],
    }


def upsert_answer_override(
    match_query: str,
    reply: str,
    *,
    source_url: str = "",
    created_by: str = "",
    enabled: bool = True,
) -> dict[str, Any]:
    import uuid as _uuid

    key = (match_query or "").strip()
    body = (reply or "").strip()
    pool = _get_pool()
    if pool is None or not key:
        return {}
    now = time.time()
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM answer_overrides WHERE match_query = %s", (key,))
        existing = cur.fetchone()
        if existing:
            oid = str(existing[0])
            cur.execute(
                """UPDATE answer_overrides
                   SET reply = %s, source_url = %s, created_by = %s,
                       enabled = %s, updated_at = %s
                   WHERE id = %s""",
                (body, source_url, created_by, enabled, now, oid),
            )
        else:
            oid = str(_uuid.uuid4())
            cur.execute(
                """INSERT INTO answer_overrides
                   (id, match_query, reply, source_url, created_by, enabled, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (oid, key, body, source_url, created_by, enabled, now),
            )
        conn.commit()
    return {
        "id": oid,
        "match_query": key,
        "reply": body,
        "source_url": source_url,
        "created_by": created_by,
        "enabled": enabled,
    }


def list_answer_overrides(limit: int = 100) -> list[dict[str, Any]]:
    pool = _get_pool()
    if pool is None:
        return []
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, match_query, reply, source_url, created_by, enabled, updated_at
               FROM answer_overrides ORDER BY updated_at DESC LIMIT %s""",
            (max(1, min(limit, 200)),),
        )
        return [
            {
                "id": r[0],
                "match_query": r[1],
                "reply": r[2],
                "source_url": r[3],
                "created_by": r[4],
                "enabled": bool(r[5]),
                "updated_at": r[6],
            }
            for r in cur.fetchall()
        ]


def delete_answer_override(override_id: str) -> bool:
    oid = (override_id or "").strip()
    pool = _get_pool()
    if pool is None or not oid:
        return False
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM answer_overrides WHERE id = %s", (oid,))
        touched = cur.rowcount > 0
        conn.commit()
    return touched


# ---------------------------------------------------------------------------
# Identity, profiles and consent
#
# Absent from this module entirely until now, so the dispatch block left
# them on SQLite while production runs Postgres.  The consent functions
# are the serious ones: `has_active_consent` gates memory injection and
# voice recording, and `withdraw_consent` is a legal instruction. On a
# per-replica store a withdrawal reaches one pod and every other keeps
# processing the taxpayer as consenting.
# ---------------------------------------------------------------------------
_USER_COLUMNS = "id, tenant_id, external_id, email, role, created_at, last_seen_at"
_PROFILE_COLUMNS = (
    "user_id, taxpayer_type, industry, primary_language, detail_level, "
    "registered_tax_types, fiscal_year, display_name, updated_at"
)
_CONSENT_COLUMNS = (
    "receipt_id, user_id, purpose, version, granted_at, withdrawn_at, legal_basis"
)
_WORKFLOW_COLUMNS = (
    "conversation_id, workflow_id, status, current_step_idx, slots_json, "
    "last_prompt, created_at, updated_at"
)


def _as_dict(columns: str, row: tuple[Any, ...] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(zip(columns.replace(" ", "").split(","), row, strict=True))


def upsert_user(
    external_id: str,
    tenant_id: str = "default",
    email: str = "",
    role: str = "public",
) -> dict[str, Any]:
    pool = _get_pool()
    if pool is None:
        raise RuntimeError("postgres unavailable")
    now = time.time()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_USER_COLUMNS} FROM users "
                "WHERE tenant_id = %s AND external_id = %s",
                (tenant_id, external_id),
            )
            existing = _as_dict(_USER_COLUMNS, cur.fetchone())
            if existing is not None:
                merged_email = email or existing["email"]
                merged_role = role or existing["role"]
                cur.execute(
                    "UPDATE users SET last_seen_at = %s, email = %s, role = %s WHERE id = %s",
                    (now, merged_email, merged_role, existing["id"]),
                )
                conn.commit()
                return {**existing, "email": merged_email, "role": merged_role,
                        "last_seen_at": now}
            user_id = str(uuid.uuid4())
            cur.execute(
                f"INSERT INTO users ({_USER_COLUMNS}) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (user_id, tenant_id, external_id, email, role, now, now),
            )
        conn.commit()
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
    pool = _get_pool()
    if pool is None:
        return None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_USER_COLUMNS} FROM users WHERE id = %s", (user_id,))
        return _as_dict(_USER_COLUMNS, cur.fetchone())


def get_user_profile(user_id: str) -> dict[str, Any] | None:
    pool = _get_pool()
    if pool is None:
        return None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {_PROFILE_COLUMNS} FROM user_profiles WHERE user_id = %s",
            (user_id,),
        )
        profile = _as_dict(_PROFILE_COLUMNS, cur.fetchone())
    if profile is None:
        return None
    profile["registered_tax_types"] = _loads(profile.get("registered_tax_types"), [])
    return profile


def upsert_user_profile(user_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    """Create or patch a profile row.  Mirrors the SQLite allow-list."""
    pool = _get_pool()
    if pool is None:
        raise RuntimeError("postgres unavailable")
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
    if isinstance(updates.get("registered_tax_types"), list):
        updates["registered_tax_types"] = json.dumps(updates["registered_tax_types"])
    now = time.time()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM user_profiles WHERE user_id = %s", (user_id,))
            exists = cur.fetchone() is not None
            if not exists:
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
                cur.execute(
                    f"INSERT INTO user_profiles ({_PROFILE_COLUMNS}) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
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
            elif updates:
                sets = ", ".join(f"{k} = %s" for k in updates) + ", updated_at = %s"
                cur.execute(
                    f"UPDATE user_profiles SET {sets} WHERE user_id = %s",  # noqa: S608
                    [*updates.values(), now, user_id],
                )
        conn.commit()
    return get_user_profile(user_id) or {}


def grant_consent(
    user_id: str,
    purpose: str,
    version: str,
    legal_basis: str = "consent",
) -> dict[str, Any]:
    pool = _get_pool()
    if pool is None:
        raise RuntimeError("postgres unavailable")
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_CONSENT_COLUMNS} FROM consent_receipts "
                "WHERE user_id = %s AND purpose = %s AND version = %s "
                "AND withdrawn_at IS NULL",
                (user_id, purpose, version),
            )
            existing = _as_dict(_CONSENT_COLUMNS, cur.fetchone())
            if existing is not None:
                return existing
            receipt_id = str(uuid.uuid4())
            now = time.time()
            cur.execute(
                f"INSERT INTO consent_receipts ({_CONSENT_COLUMNS}) "
                "VALUES (%s,%s,%s,%s,%s,NULL,%s)",
                (receipt_id, user_id, purpose, version, now, legal_basis),
            )
        conn.commit()
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
    pool = _get_pool()
    if pool is None:
        return 0
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE consent_receipts SET withdrawn_at = %s "
                "WHERE user_id = %s AND purpose = %s AND withdrawn_at IS NULL",
                (time.time(), user_id, purpose),
            )
            touched = cur.rowcount
        conn.commit()
    return touched


def get_active_consents(user_id: str) -> list[dict[str, Any]]:
    pool = _get_pool()
    if pool is None:
        return []
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {_CONSENT_COLUMNS} FROM consent_receipts "
            "WHERE user_id = %s AND withdrawn_at IS NULL ORDER BY granted_at DESC",
            (user_id,),
        )
        rows = cur.fetchall()
    return [d for r in rows if (d := _as_dict(_CONSENT_COLUMNS, r)) is not None]


def _resolve_internal_user_id(external_id: str, tenant_id: str = "default") -> str | None:
    pool = _get_pool()
    if pool is None or not external_id:
        return None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM users WHERE tenant_id = %s AND external_id = %s",
            (tenant_id, external_id),
        )
        row = cur.fetchone()
    return row[0] if row else None


def has_active_consent(user_id: str, purpose: str, tenant_id: str = "default") -> bool:
    """Mirrors the SQLite bridge: accepts the internal UUID or the OIDC ``sub``.

    Receipts are keyed by internal id while the chat/voice runtime holds
    only ``sub``; without the second lookup every authenticated user
    reads as having refused consent.
    """
    if not user_id:
        return False
    pool = _get_pool()
    if pool is None:
        return False
    sql = (
        "SELECT 1 FROM consent_receipts "
        "WHERE user_id = %s AND purpose = %s AND withdrawn_at IS NULL LIMIT 1"
    )
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (user_id, purpose))
        if cur.fetchone() is not None:
            return True
    internal_id = _resolve_internal_user_id(user_id, tenant_id)
    if internal_id and internal_id != user_id:
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (internal_id, purpose))
            return cur.fetchone() is not None
    return False


# ---------------------------------------------------------------------------
# Workflow sessions
#
# Multi-turn slot filling (TIN registration, VAT filing) keyed by
# conversation.  On a per-replica store a taxpayer half-way through a
# registration hits a different pod and the flow restarts from nothing.
# ---------------------------------------------------------------------------
def get_workflow_session(conversation_id: str) -> dict[str, Any] | None:
    pool = _get_pool()
    if pool is None or not conversation_id:
        return None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {_WORKFLOW_COLUMNS} FROM workflow_sessions WHERE conversation_id = %s",
            (conversation_id,),
        )
        row = _as_dict(_WORKFLOW_COLUMNS, cur.fetchone())
    if row is None:
        return None
    slots = _loads(row.pop("slots_json", "{}"), {})
    return {
        "conversation_id": row["conversation_id"],
        "workflow_id": row["workflow_id"],
        "status": row["status"],
        "current_step_idx": int(row["current_step_idx"] or 0),
        "slots": slots if isinstance(slots, dict) else {},
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
    pool = _get_pool()
    if pool is None or not conversation_id or not workflow_id:
        return
    if status not in {"active", "completed", "cancelled"}:
        status = "active"
    now = time.time()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""INSERT INTO workflow_sessions ({_WORKFLOW_COLUMNS})
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (conversation_id) DO UPDATE SET
                      workflow_id = EXCLUDED.workflow_id,
                      status = EXCLUDED.status,
                      current_step_idx = EXCLUDED.current_step_idx,
                      slots_json = EXCLUDED.slots_json,
                      last_prompt = EXCLUDED.last_prompt,
                      updated_at = EXCLUDED.updated_at""",
                (
                    conversation_id,
                    workflow_id,
                    status,
                    max(0, int(current_step_idx)),
                    json.dumps(slots or {}, ensure_ascii=True),
                    last_prompt[:2000],
                    now,
                    now,
                ),
            )
        conn.commit()


def complete_workflow_session(conversation_id: str, *, status: str = "completed") -> bool:
    pool = _get_pool()
    if pool is None or not conversation_id:
        return False
    if status not in {"completed", "cancelled"}:
        status = "completed"
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE workflow_sessions SET status = %s, updated_at = %s "
                "WHERE conversation_id = %s",
                (status, time.time(), conversation_id),
            )
            touched = cur.rowcount > 0
        conn.commit()
    return touched

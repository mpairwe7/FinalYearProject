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
    POSTGRES_DSN            – psycopg DSN, e.g. "postgresql://user:pw@host/db"
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
        rating      TEXT NOT NULL CHECK (rating IN ('up','down')),
        comment     TEXT DEFAULT '',
        user_query  TEXT DEFAULT '',
        bot_reply   TEXT DEFAULT '',
        created_at  DOUBLE PRECISION NOT NULL
    );

    CREATE TABLE IF NOT EXISTS analytics_events (
        id          TEXT PRIMARY KEY,
        session_id  TEXT,
        event_type  TEXT NOT NULL,
        event_data  TEXT DEFAULT '{}',
        created_at  DOUBLE PRECISION NOT NULL
    );

    CREATE TABLE IF NOT EXISTS sessions (
        id              TEXT PRIMARY KEY,
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
    """

    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
            cur.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS conversation_id TEXT")
            cur.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS contexts TEXT DEFAULT '[]'")
            cur.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS user_id TEXT DEFAULT ''")
            cur.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS transcript_json TEXT DEFAULT '[]'")
            cur.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS user_id TEXT DEFAULT ''")
            for _col, _ddl in (
                ("officer_reply", "TEXT DEFAULT ''"),
                ("reply_at", "DOUBLE PRECISION DEFAULT 0"),
                ("reply_delivered_at", "DOUBLE PRECISION DEFAULT 0"),
                ("first_response_at", "DOUBLE PRECISION DEFAULT 0"),
                ("resolved_at", "DOUBLE PRECISION DEFAULT 0"),
            ):
                cur.execute(f"ALTER TABLE tickets ADD COLUMN IF NOT EXISTS {_col} {_ddl}")
            cur.execute(
                "UPDATE conversations SET conversation_id = id WHERE conversation_id IS NULL OR conversation_id = ''"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversations_thread ON conversations(conversation_id)"
            )
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
    pool = _get_pool()
    if pool is None:
        raise RuntimeError("postgres unavailable")
    fb_id = str(uuid.uuid4())
    now = time.time()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO feedback (id, message_id, session_id, rating,
                    comment, user_query, bot_reply, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (fb_id, message_id, session_id, rating, comment, user_query, bot_reply, now),
            )
        conn.commit()
    return {"id": fb_id, "message_id": message_id, "rating": rating, "created_at": now}


def update_feedback_comment(message_id: str, comment: str) -> bool:
    pool = _get_pool()
    if pool is None:
        return False
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE feedback SET comment = %s
                   WHERE id = (
                     SELECT id FROM feedback
                     WHERE message_id = %s AND comment = ''
                     ORDER BY created_at DESC LIMIT 1
                   )""",
                (comment, message_id),
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
            """SELECT id, message_id, rating, comment, created_at
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
                "created_at": r[4],
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
def track_event(event_type: str, event_data: str = "{}", session_id: str | None = None) -> None:
    pool = _get_pool()
    if pool is None:
        return
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO analytics_events (id, session_id, event_type, event_data, created_at)
                   VALUES (%s,%s,%s,%s,%s)""",
                (str(uuid.uuid4()), session_id, event_type, event_data, time.time()),
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
def upsert_session(session_id: str, user_agent: str = "", platform: str = "") -> None:
    pool = _get_pool()
    if pool is None:
        return
    now = time.time()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO sessions (id, started_at, last_active_at,
                       message_count, user_agent, platform)
                   VALUES (%s,%s,%s,1,%s,%s)
                   ON CONFLICT (id) DO UPDATE SET
                     last_active_at = EXCLUDED.last_active_at,
                     message_count  = sessions.message_count + 1""",
                (session_id, now, now, user_agent, platform),
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
    row_id = str(uuid.uuid4())
    thread_id = conversation_id or row_id
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO conversations (id, conversation_id, session_id, user_message, bot_reply,
                       sources, contexts, response_time_ms, confidence, topic_tag,
                       user_id, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
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
                    time.time(),
                ),
            )
        conn.commit()
    return thread_id


def get_recent_turns(
    session_id: str | None = None,
    conversation_id: str | None = None,
    limit: int = 5,
) -> list[dict[str, str]]:
    pool = _get_pool()
    if pool is None:
        return []
    if conversation_id:
        sql = """SELECT user_message, bot_reply FROM conversations
                   WHERE conversation_id = %s
                   ORDER BY created_at DESC LIMIT %s"""
        args: tuple[str, int] = (conversation_id, limit)
    elif session_id:
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
    "assignee, staff_note, created_at, updated_at, "
    "officer_reply, reply_at, reply_delivered_at, first_response_at, resolved_at"
)
#: Detail view — the transcript is the point of the ticket.
_TICKET_COLUMNS_FULL = _TICKET_COLUMNS + ", transcript_json"


def _row_to_ticket(row: tuple[Any, ...], columns: str = _TICKET_COLUMNS) -> dict[str, Any]:
    """Map a row onto the same dict shape :mod:`database` returns."""
    ticket = dict(zip(columns.replace(" ", "").split(","), row, strict=True))
    ticket["handoff"] = _loads(ticket.pop("handoff_json", "{}"), {})
    ticket["response_judge"] = _loads(ticket.pop("response_judge_json", "{}"), {})
    ticket["transcript"] = _loads(ticket.pop("transcript_json", "[]"), [])
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
) -> dict[str, Any]:
    if priority not in ("low", "normal", "high", "urgent"):
        logger.warning("create_ticket: invalid priority %r -> 'normal'", priority)
        priority = "normal"
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
                                        user_id, assignee, staff_note, created_at, updated_at)
                   VALUES (%s,%s,%s,'open',%s,%s,%s,%s,%s,%s,%s,%s,'','',%s,%s)""",
                (
                    ticket_id,
                    conversation_id,
                    session_id,
                    priority,
                    reason,
                    user_query,
                    bot_reply,
                    json.dumps(handoff or {}),
                    json.dumps(response_judge or {}),
                    json.dumps(transcript or []),
                    user_id,
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
        "handoff": handoff or {},
        "response_judge": response_judge or {},
        "transcript": transcript or [],
        "created_at": now,
    }


def list_tickets(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    priority: str | None = None,
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
        sql += " AND priority = %s" if status else " WHERE priority = %s"
        params.append(priority)
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
        sets.append("officer_reply = %s")
        params.append(officer_reply[:4000])
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
        params.append(staff_note)
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
    pool = _get_pool()
    empty = {
        "period_days": days,
        "tickets": 0,
        "responded": 0,
        "resolved": 0,
        "awaiting_first_response": 0,
        "median_response_seconds": None,
        "median_resolution_seconds": None,
    }
    if pool is None:
        return empty
    cutoff = time.time() - (days * 86400)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT created_at, first_response_at, resolved_at FROM tickets "
            "WHERE created_at >= %s",
            (cutoff,),
        )
        rows = cur.fetchall()

    def _median(values: list[float]) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            return round(ordered[mid], 1)
        return round((ordered[mid - 1] + ordered[mid]) / 2, 1)

    response = [r[1] - r[0] for r in rows if r[1]]
    resolution = [r[2] - r[0] for r in rows if r[2]]
    return {
        "period_days": days,
        "tickets": len(rows),
        "responded": len(response),
        "resolved": len(resolution),
        "awaiting_first_response": sum(1 for r in rows if not r[1]),
        "median_response_seconds": _median(response),
        "median_resolution_seconds": _median(resolution),
    }

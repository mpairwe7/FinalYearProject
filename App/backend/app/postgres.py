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
        session_id       TEXT,
        user_message     TEXT NOT NULL,
        bot_reply        TEXT NOT NULL,
        sources          TEXT DEFAULT '[]',
        response_time_ms DOUBLE PRECISION DEFAULT 0,
        confidence       DOUBLE PRECISION DEFAULT 0,
        topic_tag        TEXT DEFAULT '',
        created_at       DOUBLE PRECISION NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_feedback_message      ON feedback(message_id);
    CREATE INDEX IF NOT EXISTS idx_feedback_created      ON feedback(created_at);
    CREATE INDEX IF NOT EXISTS idx_events_type           ON analytics_events(event_type);
    CREATE INDEX IF NOT EXISTS idx_events_session        ON analytics_events(session_id);
    CREATE INDEX IF NOT EXISTS idx_events_created        ON analytics_events(created_at);
    CREATE INDEX IF NOT EXISTS idx_sessions_active       ON sessions(last_active_at);
    CREATE INDEX IF NOT EXISTS idx_conversations_session ON conversations(session_id);
    CREATE INDEX IF NOT EXISTS idx_conversations_created ON conversations(created_at);
    """

    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
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
        return {"period_days": days, "total": 0, "thumbs_up": 0, "thumbs_down": 0,
                "satisfaction_pct": 0.0, "recent": []}
    cutoff = time.time() - (days * 86400)
    with pool.connection() as conn:
        with conn.cursor() as cur:
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
                {"id": r[0], "message_id": r[1], "rating": r[2],
                 "comment": r[3], "created_at": r[4]}
                for r in cur.fetchall()
            ]
    satisfaction = round(up / total * 100, 1) if total > 0 else 0.0
    return {
        "period_days": days, "total": total,
        "thumbs_up": up, "thumbs_down": down,
        "satisfaction_pct": satisfaction, "recent": recent,
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
    with pool.connection() as conn:
        with conn.cursor() as cur:
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
        return {"period_days": days, "total_sessions": 0,
                "avg_messages_per_session": 0.0, "max_messages_in_session": 0}
    cutoff = time.time() - (days * 86400)
    with pool.connection() as conn:
        with conn.cursor() as cur:
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
    user_message: str,
    bot_reply: str,
    sources: str = "[]",
    response_time_ms: float = 0,
    confidence: float = 0,
    topic_tag: str = "",
) -> str:
    pool = _get_pool()
    if pool is None:
        raise RuntimeError("postgres unavailable")
    conv_id = str(uuid.uuid4())
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO conversations (id, session_id, user_message, bot_reply,
                       sources, response_time_ms, confidence, topic_tag, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (conv_id, session_id, user_message, bot_reply, sources,
                 response_time_ms, confidence, topic_tag, time.time()),
            )
        conn.commit()
    return conv_id


def get_recent_turns(session_id: str, limit: int = 5) -> list[dict[str, str]]:
    pool = _get_pool()
    if pool is None:
        return []
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT user_message, bot_reply FROM conversations
                   WHERE session_id = %s
                   ORDER BY created_at DESC LIMIT %s""",
                (session_id, limit),
            )
            rows = cur.fetchall()
    return [{"user_message": r[0], "bot_reply": r[1]} for r in reversed(rows)]


def get_conversation_stats(days: int = 30) -> dict[str, Any]:
    pool = _get_pool()
    if pool is None:
        return {"period_days": days, "total_conversations": 0,
                "avg_response_time_ms": 0.0, "avg_confidence": 0.0, "top_topics": []}
    cutoff = time.time() - (days * 86400)
    with pool.connection() as conn:
        with conn.cursor() as cur:
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
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT message_id, user_query, bot_reply, comment, created_at,
                          'thumbs_down'
                   FROM feedback
                   WHERE rating='down' AND created_at >= %s
                   ORDER BY created_at DESC""",
                (cutoff,),
            )
            down = [
                {"message_id": r[0], "user_query": r[1], "bot_reply": r[2],
                 "comment": r[3], "created_at": r[4], "review_reason": r[5]}
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
                {"message_id": r[0], "user_query": r[1], "bot_reply": r[2],
                 "comment": r[3], "created_at": r[4], "review_reason": r[5]}
                for r in cur.fetchall()
            ]
    return down + low

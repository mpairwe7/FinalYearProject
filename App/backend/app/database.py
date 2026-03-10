"""SQLite persistence layer for analytics and feedback.

Thread-safe via thread-local connections with WAL journaling.
All write operations are wrapped in try/except to prevent data loss.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DB_DIR = Path(os.getenv("ANALYTICS_DB_DIR", str(_PROJECT_ROOT / "data_store")))
_DB_PATH = _DB_DIR / "analytics.db"

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
            session_id      TEXT,
            user_message    TEXT NOT NULL,
            bot_reply       TEXT NOT NULL,
            sources         TEXT DEFAULT '[]',
            response_time_ms REAL DEFAULT 0,
            confidence      REAL DEFAULT 0,
            topic_tag       TEXT DEFAULT '',
            created_at      REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_feedback_message ON feedback(message_id);
        CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback(created_at);
        CREATE INDEX IF NOT EXISTS idx_events_type ON analytics_events(event_type);
        CREATE INDEX IF NOT EXISTS idx_events_session ON analytics_events(session_id);
        CREATE INDEX IF NOT EXISTS idx_events_created ON analytics_events(created_at);
        CREATE INDEX IF NOT EXISTS idx_sessions_active ON sessions(last_active_at);
        CREATE INDEX IF NOT EXISTS idx_conversations_session ON conversations(session_id);
        CREATE INDEX IF NOT EXISTS idx_conversations_created ON conversations(created_at);
    """)
    conn.commit()
    logger.info("Analytics database initialised at %s", _DB_PATH)


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
               VALUES (?, ?, ?, 0, ?, ?)
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
    user_message: str,
    bot_reply: str,
    sources: str = "[]",
    response_time_ms: float = 0,
    confidence: float = 0,
    topic_tag: str = "",
) -> str:
    """Log a conversation turn and return its ID."""
    conn = _get_connection()
    conv_id = str(uuid.uuid4())
    try:
        conn.execute(
            """INSERT INTO conversations
               (id, session_id, user_message, bot_reply, sources, response_time_ms,
                confidence, topic_tag, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (conv_id, session_id, user_message, bot_reply, sources,
             response_time_ms, confidence, topic_tag, time.time()),
        )
        conn.commit()
    except Exception:
        logger.exception("Failed to log conversation")
        conn.rollback()
        raise
    return conv_id


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

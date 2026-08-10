"""Episodic memory — conversation summaries with time-filtered retrieval.

Stores one row per completed conversation in
``episodic_summaries``.  Each row has:

- ``summary`` — 1-3 sentence LLM-generated summary
- ``topic_tag`` — primary tag (e.g. "vat_registration")
- ``sentiment`` — rough "positive"/"neutral"/"negative"
- ``user_id``, ``tenant_id``, ``conversation_id``
- ``turn_count`` — how many turns it spanned
- ``created_at`` — when the summary was written

Retention defaults to 90 days (configurable via ``EPISODIC_TTL_DAYS``).
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

EPISODIC_TTL_DAYS = int(os.getenv("EPISODIC_TTL_DAYS", "90"))


@dataclass
class EpisodicSummary:
    summary_id: str
    user_id: str
    tenant_id: str
    conversation_id: str
    summary: str
    topic_tag: str = ""
    sentiment: str = "neutral"
    turn_count: int = 0
    created_at: float = field(default_factory=time.time)

    def to_row(self) -> tuple[Any, ...]:
        return (
            self.summary_id,
            self.user_id,
            self.tenant_id,
            self.conversation_id,
            self.summary,
            self.topic_tag,
            self.sentiment,
            self.turn_count,
            self.created_at,
        )


class EpisodicMemory:
    """SQLite/Postgres-backed episodic summary store.

    Runs against the shared analytics DB via :mod:`database`.  In a
    multi-tenant deploy every query is filtered by ``tenant_id``.
    """

    def __init__(self) -> None:
        self._init_schema()

    def _init_schema(self) -> None:
        from .. import database as db

        db.execute_script(
            """
            CREATE TABLE IF NOT EXISTS episodic_summaries (
                summary_id      TEXT PRIMARY KEY,
                user_id         TEXT NOT NULL,
                tenant_id       TEXT NOT NULL DEFAULT 'default',
                conversation_id TEXT NOT NULL,
                summary         TEXT NOT NULL,
                topic_tag       TEXT DEFAULT '',
                sentiment       TEXT DEFAULT 'neutral',
                turn_count      INTEGER DEFAULT 0,
                created_at      DOUBLE PRECISION NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_episodic_user
                ON episodic_summaries(user_id);
            CREATE INDEX IF NOT EXISTS idx_episodic_created
                ON episodic_summaries(created_at);
            CREATE INDEX IF NOT EXISTS idx_episodic_topic
                ON episodic_summaries(user_id, topic_tag);
            """
        )

    def write(self, summary: EpisodicSummary) -> str:
        from .. import database as db

        if not summary.summary_id:
            summary.summary_id = str(uuid.uuid4())
        try:
            db.execute(
                """INSERT INTO episodic_summaries
                   (summary_id, user_id, tenant_id, conversation_id,
                    summary, topic_tag, sentiment, turn_count, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                tuple(summary.to_row()),
            )
        except Exception:
            logger.exception("episodic write failed")
            raise
        return summary.summary_id

    def list_for_user(
        self,
        user_id: str,
        limit: int = 20,
        topic_tag: str | None = None,
        since_days: int = 90,
    ) -> list[dict[str, Any]]:
        from .. import database as db

        cutoff = time.time() - (since_days * 86400)
        sql = "SELECT * FROM episodic_summaries " "WHERE user_id = ? AND created_at >= ?"
        params: list[Any] = [user_id, cutoff]
        if topic_tag:
            sql += " AND topic_tag = ?"
            params.append(topic_tag)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return db.query_all(sql, tuple(params))

    def delete_for_user(self, user_id: str) -> int:
        """Forget cascade — delete every summary for this user."""
        from .. import database as db

        try:
            return db.execute(
                "DELETE FROM episodic_summaries WHERE user_id = ?", (user_id,)
            )
        except Exception:
            logger.exception("episodic delete failed")
            return 0

    def cleanup_expired(self) -> int:
        """TTL enforcement — called from the nightly worker."""
        from .. import database as db

        cutoff = time.time() - (EPISODIC_TTL_DAYS * 86400)
        try:
            return db.execute(
                "DELETE FROM episodic_summaries WHERE created_at < ?", (cutoff,)
            )
        except Exception:
            logger.exception("episodic cleanup failed")
            return 0

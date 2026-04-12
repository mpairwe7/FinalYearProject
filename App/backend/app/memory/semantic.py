"""Semantic memory — extracted user facts with provenance.

Each fact is one (subject, predicate, object) triple with:

- ``confidence`` — extractor's original confidence in [0, 1]
- ``category`` — e.g. "taxpayer_type", "industry" (drives decay half-life)
- ``provenance`` — ``conversation_id`` + ``turn_id`` + ``extractor_model``
- ``extracted_at`` — unix epoch; feeds decay
- ``superseded_by`` — nullable, set when a newer fact overrides this one

Retrieval applies consent gating + temporal decay at query time;
facts below ``decay_floor`` are excluded from results but preserved
in the DB for audit replay and "which fact led to this answer?"
lookups.

Phase 15 full replaces this flat table with a Neo4j / Kùzu personal
knowledge graph.  The public API (`write`, `read`, `forget`) stays
the same; callers don't know the backend changed.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from .decay import compute_decayed_confidence

logger = logging.getLogger(__name__)


@dataclass
class UserFact:
    fact_id: str
    user_id: str
    tenant_id: str
    category: str          # e.g. "taxpayer_type"
    subject: str           # usually "user"
    predicate: str         # e.g. "is_a"
    object_value: str      # e.g. "sole_trader"
    confidence: float      # 0..1 at extraction time
    extracted_at: float
    conversation_id: str = ""
    turn_id: str = ""
    extractor_model: str = ""
    superseded_by: str | None = None

    def to_row(self) -> tuple[Any, ...]:
        return (
            self.fact_id, self.user_id, self.tenant_id, self.category,
            self.subject, self.predicate, self.object_value,
            self.confidence, self.extracted_at,
            self.conversation_id, self.turn_id, self.extractor_model,
            self.superseded_by,
        )


class SemanticMemory:
    """SQLite-backed semantic fact store.

    Schema auto-created on first use.  Table is part of the shared
    analytics DB so Postgres migration inherits from :mod:`database`.
    """

    def __init__(self) -> None:
        self._init_schema()

    def _init_schema(self) -> None:
        from .. import database as db

        conn = db._get_connection()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS user_facts (
                fact_id          TEXT PRIMARY KEY,
                user_id          TEXT NOT NULL,
                tenant_id        TEXT NOT NULL DEFAULT 'default',
                category         TEXT NOT NULL,
                subject          TEXT NOT NULL DEFAULT 'user',
                predicate        TEXT NOT NULL,
                object_value     TEXT NOT NULL,
                confidence       REAL NOT NULL DEFAULT 0.5,
                extracted_at     REAL NOT NULL,
                conversation_id  TEXT DEFAULT '',
                turn_id          TEXT DEFAULT '',
                extractor_model  TEXT DEFAULT '',
                superseded_by    TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_facts_user
                ON user_facts(user_id);
            CREATE INDEX IF NOT EXISTS idx_facts_user_category
                ON user_facts(user_id, category);
            CREATE INDEX IF NOT EXISTS idx_facts_extracted
                ON user_facts(extracted_at);
            """
        )
        conn.commit()

    # -- Writes --------------------------------------------------------
    def write(self, fact: UserFact) -> str:
        from .. import database as db

        if not fact.fact_id:
            fact.fact_id = str(uuid.uuid4())
        if fact.extracted_at == 0:
            fact.extracted_at = time.time()
        conn = db._get_connection()
        try:
            conn.execute(
                """INSERT INTO user_facts
                   (fact_id, user_id, tenant_id, category, subject,
                    predicate, object_value, confidence, extracted_at,
                    conversation_id, turn_id, extractor_model, superseded_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                fact.to_row(),
            )
            conn.commit()
        except Exception:
            logger.exception("semantic write failed")
            conn.rollback()
            raise
        return fact.fact_id

    def write_batch(self, facts: list[UserFact]) -> list[str]:
        return [self.write(f) for f in facts]

    def supersede(self, old_fact_id: str, new_fact_id: str) -> bool:
        """Mark *old_fact_id* as superseded by *new_fact_id*."""
        from .. import database as db

        conn = db._get_connection()
        try:
            cursor = conn.execute(
                "UPDATE user_facts SET superseded_by = ? WHERE fact_id = ?",
                (new_fact_id, old_fact_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception:
            logger.exception("semantic supersede failed")
            conn.rollback()
            return False

    # -- Reads ---------------------------------------------------------
    def read(
        self,
        user_id: str,
        category: str | None = None,
        min_confidence: float = 0.5,
        decay_floor: float = 0.3,
        include_superseded: bool = False,
        limit: int = 20,
    ) -> list[UserFact]:
        """Return active facts above the decay floor."""
        from .. import database as db

        conn = db._get_connection()
        sql = "SELECT * FROM user_facts WHERE user_id = ?"
        params: list[Any] = [user_id]
        if category:
            sql += " AND category = ?"
            params.append(category)
        if not include_superseded:
            sql += " AND superseded_by IS NULL"
        sql += " ORDER BY extracted_at DESC LIMIT ?"
        params.append(max(1, limit * 3))   # overfetch, we'll filter by decay

        rows = conn.execute(sql, params).fetchall()
        now = time.time()
        results: list[UserFact] = []
        for r in rows:
            row = dict(r)
            decayed = compute_decayed_confidence(
                original_confidence=row["confidence"],
                category=row["category"],
                extracted_at=row["extracted_at"],
                now=now,
            )
            if decayed < decay_floor:
                continue
            if row["confidence"] < min_confidence:
                continue
            results.append(UserFact(
                fact_id=row["fact_id"],
                user_id=row["user_id"],
                tenant_id=row["tenant_id"],
                category=row["category"],
                subject=row["subject"],
                predicate=row["predicate"],
                object_value=row["object_value"],
                confidence=decayed,   # decay-adjusted for caller convenience
                extracted_at=row["extracted_at"],
                conversation_id=row["conversation_id"],
                turn_id=row["turn_id"],
                extractor_model=row["extractor_model"],
                superseded_by=row["superseded_by"],
            ))
            if len(results) >= limit:
                break
        return results

    # -- Deletes / erasure --------------------------------------------
    def forget_user(self, user_id: str) -> int:
        """Delete every fact for this user (UDPA right-to-erasure)."""
        from .. import database as db

        conn = db._get_connection()
        try:
            cursor = conn.execute(
                "DELETE FROM user_facts WHERE user_id = ?",
                (user_id,),
            )
            conn.commit()
            return cursor.rowcount
        except Exception:
            logger.exception("semantic forget failed")
            conn.rollback()
            return 0

    def forget_fact(self, fact_id: str) -> bool:
        from .. import database as db

        conn = db._get_connection()
        try:
            cursor = conn.execute(
                "DELETE FROM user_facts WHERE fact_id = ?",
                (fact_id,),
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception:
            logger.exception("semantic forget_fact failed")
            conn.rollback()
            return False

"""Top-level memory service — the single entry point for agents.

Composes WorkingMemory + EpisodicMemory + SemanticMemory behind a
clean interface that the Phase 15 graph orchestrator and any
future tool can call:

    memsvc = get_memory_service()

    # Consent-gated read
    facts = memsvc.read_facts(user_id, purpose="personalization")

    # Write after a conversation ends
    memsvc.absorb_conversation(user_id, conversation_id, turns)

    # Erasure cascade (called from /v1/me DELETE)
    memsvc.forget_user(user_id)

Every read is gated on the user's active consent for the
``personalization`` purpose.  No consent → empty results.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from .episodic import EpisodicMemory, EpisodicSummary
from .extractor import FactExtractor
from .semantic import SemanticMemory, UserFact
from .working import WorkingMemory

logger = logging.getLogger(__name__)


MIN_FACT_CONFIDENCE = 0.7


@dataclass
class MemoryReadResult:
    facts: list[UserFact]
    episodic: list[dict[str, Any]]
    working: dict[str, Any] | None
    consent_granted: bool


class MemoryService:
    """Unified memory interface used by the graph orchestrator."""

    def __init__(self) -> None:
        self.working = WorkingMemory()
        self.episodic = EpisodicMemory()
        self.semantic = SemanticMemory()
        self.extractor = FactExtractor()

    # -- Consent-gated reads -------------------------------------------
    def _has_consent(self, user_id: str, purpose: str = "personalization") -> bool:
        if not user_id:
            return False
        try:
            from .. import database as db

            return db.has_active_consent(user_id, purpose)
        except Exception:
            logger.debug("consent check failed", exc_info=True)
            return False

    def read_facts(
        self,
        user_id: str,
        category: str | None = None,
        limit: int = 10,
        purpose: str = "personalization",
    ) -> list[UserFact]:
        """Return decay-adjusted facts for a user.

        Returns an empty list if the user hasn't granted consent
        for the requested purpose.  This is the primary guard that
        enforces UDPA 2019 purpose limitation at the retrieval boundary.
        """
        if not self._has_consent(user_id, purpose):
            return []
        return self.semantic.read(
            user_id=user_id,
            category=category,
            limit=limit,
        )

    def read_episodic(
        self,
        user_id: str,
        topic_tag: str | None = None,
        limit: int = 5,
        purpose: str = "personalization",
    ) -> list[dict[str, Any]]:
        if not self._has_consent(user_id, purpose):
            return []
        return self.episodic.list_for_user(
            user_id=user_id,
            limit=limit,
            topic_tag=topic_tag,
        )

    def read_all(self, user_id: str, purpose: str = "personalization") -> MemoryReadResult:
        """One-shot read — facts, episodic summaries, and working state."""
        granted = self._has_consent(user_id, purpose)
        return MemoryReadResult(
            facts=self.semantic.read(user_id=user_id) if granted else [],
            episodic=self.episodic.list_for_user(user_id=user_id, limit=5) if granted else [],
            working=self.working.get(user_id) if granted else None,
            consent_granted=granted,
        )

    # -- Writes --------------------------------------------------------
    def absorb_conversation(
        self,
        user_id: str,
        conversation_id: str,
        turns: list[dict[str, str]],
        tenant_id: str = "default",
        extractor_model: str = "rules-v1",
    ) -> dict[str, Any]:
        """Extract facts and write an episodic summary from a conversation.

        Called by the offline worker after a conversation ends.
        Consent is NOT checked here — writes happen regardless
        (so the audit trail is complete) but reads are gated.

        Returns a dict with counts of writes performed.
        """
        if not user_id or not turns:
            return {"facts_written": 0, "episodic_written": False}

        # 1. Extract facts (rule-based in Phase 16 Lite)
        candidates = self.extractor.extract(turns)
        written_fact_ids: list[str] = []
        for c in candidates:
            if c.confidence < MIN_FACT_CONFIDENCE:
                continue
            fact = UserFact(
                fact_id=str(uuid.uuid4()),
                user_id=user_id,
                tenant_id=tenant_id,
                category=c.category,
                subject=c.subject,
                predicate=c.predicate,
                object_value=c.object_value,
                confidence=c.confidence,
                extracted_at=time.time(),
                conversation_id=conversation_id,
                turn_id=str(c.source_turn),
                extractor_model=extractor_model,
            )
            try:
                written_fact_ids.append(self.semantic.write(fact))
            except Exception:
                logger.exception("semantic write failed")

        # 2. Write an episodic summary (naive: first user turn truncated)
        first_user = next(
            (
                str(t.get("content") or t.get("user_message", ""))
                for t in turns
                if t.get("role") in ("user", "user_message")
            ),
            "",
        )
        summary_text = first_user[:240] if first_user else "(empty conversation)"
        episodic = EpisodicSummary(
            summary_id=str(uuid.uuid4()),
            user_id=user_id,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            summary=summary_text,
            topic_tag=_guess_topic_tag(first_user),
            sentiment="neutral",
            turn_count=len(turns),
        )
        episodic_id = ""
        try:
            episodic_id = self.episodic.write(episodic)
        except Exception:
            logger.exception("episodic write failed")

        return {
            "facts_written": len(written_fact_ids),
            "fact_ids": written_fact_ids,
            "episodic_id": episodic_id,
            "episodic_written": bool(episodic_id),
        }

    # -- Working memory -----------------------------------------------
    def update_working(self, user_id: str, **fields: Any) -> None:
        """Set/merge short-term working state (no consent gate — ephemeral)."""
        self.working.update(user_id, **fields)

    # -- Subject-access export ----------------------------------------
    def export_user(self, user_id: str) -> dict[str, Any]:
        """Ungated subject-access export of stored memory (UDPA data portability).

        Unlike :meth:`read_facts`, this is NOT consent-gated — a data subject is
        entitled to a copy of their stored data regardless of current consent.
        """
        import dataclasses

        facts = self.semantic.read(user_id=user_id, limit=1000)
        return {
            "facts": [
                dataclasses.asdict(f) if dataclasses.is_dataclass(f) else dict(f) for f in facts
            ],
            "episodic": self.episodic.list_for_user(user_id=user_id, limit=1000),
        }

    # -- Erasure cascade ----------------------------------------------
    def forget_user(self, user_id: str) -> dict[str, int]:
        """Cascade delete across all three tiers (UDPA right to erasure).

        The audit ledger is intentionally not touched — erasure is
        cryptographically noted but the hash chain is immutable.
        """
        self.working.clear(user_id)
        episodic_deleted = self.episodic.delete_for_user(user_id)
        facts_deleted = self.semantic.forget_user(user_id)
        return {
            "working": 1,
            "episodic": episodic_deleted,
            "semantic": facts_deleted,
        }


# ---------------------------------------------------------------------------
# Topic-tag heuristic (keeps the dependency count low)
# ---------------------------------------------------------------------------
_TOPIC_KEYWORDS = {
    "vat": ["vat", "value added"],
    "paye": ["paye", "take-home", "salary tax"],
    "cit": ["corporation tax", "corporate tax", "cit"],
    "customs": ["import", "customs", "cif", "tariff"],
    "registration": ["register", "tin", "sign up"],
    "withholding": ["withholding", "wht"],
    "capital_gains": ["capital gains", "cgt", "sold"],
    "escalation": ["human", "officer", "dispute", "appeal"],
}


def _guess_topic_tag(text: str) -> str:
    t = (text or "").lower()
    for tag, keywords in _TOPIC_KEYWORDS.items():
        if any(k in t for k in keywords):
            return tag
    return "general"


# ---------------------------------------------------------------------------
# Module singleton
# ---------------------------------------------------------------------------
_service: MemoryService | None = None


def get_memory_service() -> MemoryService:
    global _service
    if _service is None:
        _service = MemoryService()
    return _service


def reset_memory_service() -> None:
    global _service
    _service = None

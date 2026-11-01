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
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

from .episodic import EpisodicMemory, EpisodicSummary
from .extractor import FactExtractor
from .handoff import (
    EvidenceCard,
    HandoffEnvelope,
    HandoffGoal,
    RecommendedAction,
    SLAContext,
    ToolExecutionReceipt,
)
from .procedural import ProceduralMemory
from .resolution_ledger import ResolutionCase, ResolutionLedger
from .semantic import SemanticMemory, UserFact
from .working import WORKING_TTL_SECONDS

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
        self.episodic = EpisodicMemory()
        self.semantic = SemanticMemory()
        self.extractor = FactExtractor()
        self.procedural = ProceduralMemory()
        self.resolution_ledger = ResolutionLedger()

    # -- Consent-gated reads -------------------------------------------
    def _has_consent(
        self, user_id: str, purpose: str = "personalization", tenant_id: str = "default"
    ) -> bool:
        if not user_id:
            return False
        try:
            from .. import database as db

            return db.has_active_consent(user_id, purpose, tenant_id=tenant_id or "default")
        except Exception:
            logger.debug("consent check failed", exc_info=True)
            return False

    def read_facts(
        self,
        user_id: str,
        category: str | None = None,
        limit: int = 10,
        purpose: str = "personalization",
        tenant_id: str = "default",
    ) -> list[UserFact]:
        """Return decay-adjusted facts for a user.

        Returns an empty list if the user hasn't granted consent
        for the requested purpose.  This is the primary guard that
        enforces UDPA 2019 purpose limitation at the retrieval boundary.
        """
        if not self._has_consent(user_id, purpose, tenant_id):
            return []
        return self.semantic.read(
            user_id=user_id,
            tenant_id=tenant_id,
            category=category,
            limit=limit,
        )

    def read_episodic(
        self,
        user_id: str,
        topic_tag: str | None = None,
        limit: int = 5,
        purpose: str = "personalization",
        tenant_id: str = "default",
    ) -> list[dict[str, Any]]:
        if not self._has_consent(user_id, purpose, tenant_id):
            return []
        return self.episodic.list_for_user(
            user_id=user_id,
            tenant_id=tenant_id,
            limit=limit,
            topic_tag=topic_tag,
        )

    def read_all(
        self, user_id: str, purpose: str = "personalization", tenant_id: str = "default"
    ) -> MemoryReadResult:
        """One-shot read — facts, episodic summaries, and working state."""
        granted = self._has_consent(user_id, purpose, tenant_id)
        from .. import database as db

        return MemoryReadResult(
            facts=self.semantic.read(user_id=user_id, tenant_id=tenant_id) if granted else [],
            episodic=self.episodic.list_for_user(user_id=user_id, tenant_id=tenant_id, limit=5) if granted else [],
            working=db.get_working_memory(user_id, tenant_id=tenant_id) if granted else None,
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
        outcomes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Extract facts and write an episodic summary from a conversation.

        Called after a turn or by the sleep-time consolidation worker.
        Writes are consent-gated at the storage boundary. The caller also
        checks consent so revoked consent cannot be bypassed by another agent.

        Returns a dict with counts of writes performed.
        """
        if not user_id or not turns:
            return {"facts_written": 0, "episodic_written": False}
        if not self._has_consent(user_id, tenant_id=tenant_id):
            return {"facts_written": 0, "episodic_written": False, "skipped": "consent_required"}

        # 1. Extract facts (rule-based in Phase 16 Lite)
        candidates = self.extractor.extract(turns)
        written_fact_ids: list[str] = []
        for c in candidates:
            if c.confidence < MIN_FACT_CONFIDENCE:
                continue
            # Provenance: a fact read only from the machine-translated English
            # form of a Luganda or Swahili turn says so.
            model = f"{extractor_model}+en" if c.rule_id.endswith("+en") else extractor_model
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
                extractor_model=model,
            )
            try:
                written_fact_ids.append(self.semantic.write(fact))
            except Exception:
                logger.exception("semantic write failed")

        # 2. One episode per conversation, derived from the whole conversation
        # so far — its stored turns plus these — so every turn writes the same
        # row with more in it, rather than replacing it with its own topic.
        # Only coarse topic labels + sanitized outcome metadata (MS-1): raw user
        # text can contain identifiers, financial details, or prompt injection
        # and does not belong in long-lived personalization memory.
        new_user_texts = [text for text in (_user_text(t) for t in turns) if text]
        stored_turn_count = self._stored_turn_count(user_id, conversation_id, tenant_id)
        prior_user_texts = self._stored_user_texts(
            user_id, conversation_id, tenant_id, limit=max(25, stored_turn_count)
        )
        tags = _ordered_topic_tags(prior_user_texts + new_user_texts)
        if tags:
            summary_text = f"Discussed {_join_labels([_TOPIC_LABELS.get(t, t.replace('_', ' ')) for t in tags[:3]])}."
        elif new_user_texts or prior_user_texts:
            summary_text = "Discussed general tax questions."
        else:
            summary_text = "Conversation topic unavailable."
        outcome_clause = _format_episodic_outcomes(outcomes)
        if outcome_clause:
            summary_text = f"{summary_text[:-1]} ({outcome_clause})."
        episodic = EpisodicSummary(
            summary_id=str(uuid.uuid4()),
            user_id=user_id,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            summary=summary_text,
            topic_tag=tags[0] if tags else "general",
            # Do not persist inferred emotional or hardship labels.
            sentiment="neutral",
            turn_count=stored_turn_count + len(new_user_texts),
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

    def consolidate_user_memory(
        self,
        user_id: str,
        *,
        tenant_id: str = "default",
        decay_floor: float = 0.3,
    ) -> dict[str, Any]:
        """Sleep-time memory consolidation & contradiction reconciliation (MS-2 / Letta & Mem0).

        Reconciles active semantic facts by superseding older lower-confidence
        duplicates within single-valued/registration categories and pruning
        decayed facts below *decay_floor*.
        """
        if not user_id or not self._has_consent(user_id, tenant_id=tenant_id):
            return {"consolidated_facts": 0, "superseded_count": 0, "pruned_decayed": 0}
        from .. import database as db
        from .decay import compute_decayed_confidence
        from .semantic import SINGLE_VALUED_CATEGORIES

        rows = db.query_all(
            """SELECT * FROM user_facts
               WHERE tenant_id = ? AND user_id = ? AND superseded_by IS NULL
               ORDER BY extracted_at DESC""",
            (tenant_id or "default", user_id),
        )
        now = time.time()
        seen_single: dict[tuple[str, str, str], str] = {}
        superseded_count = 0
        pruned_decayed = 0
        active_count = 0
        for row in rows:
            fid = str(row["fact_id"])
            cat = str(row["category"] or "")
            subj = str(row["subject"] or "user")
            pred = str(row["predicate"] or "")
            decayed = compute_decayed_confidence(
                original_confidence=float(row["confidence"]),
                category=cat,
                extracted_at=float(row["extracted_at"]),
                now=now,
            )
            if decayed < decay_floor:
                db.execute(
                    "UPDATE user_facts SET invalidated_at = COALESCE(invalidated_at, ?) WHERE fact_id = ?",
                    (now, fid),
                )
                pruned_decayed += 1
                continue
            if cat in SINGLE_VALUED_CATEGORIES:
                slot_key = (cat, subj, pred)
                winner_id = seen_single.get(slot_key)
                if winner_id and winner_id != fid:
                    if self.semantic.supersede(fid, winner_id):
                        superseded_count += 1
                    continue
                seen_single[slot_key] = fid
            active_count += 1
        return {
            "consolidated_facts": active_count,
            "superseded_count": superseded_count,
            "pruned_decayed": pruned_decayed,
        }

    @staticmethod
    def _stored_user_texts(
        user_id: str, conversation_id: str, tenant_id: str, limit: int = 25
    ) -> list[str]:
        """The conversation's earlier taxpayer turns, in English where stored (G119)."""
        if not conversation_id:
            return []
        try:
            from .. import database as db
            from ..context_manager import english_view, normalize_history_turns

            turns = db.get_recent_turns(
                conversation_id=conversation_id, user_id=user_id, tenant_id=tenant_id or "default", limit=limit
            )
            return [t["user_message"] for t in english_view(normalize_history_turns(turns)) if t.get("user_message")]
        except Exception:
            logger.debug("episodic: earlier turns unavailable", exc_info=True)
            return []

    @staticmethod
    def _stored_turn_count(user_id: str, conversation_id: str, tenant_id: str) -> int:
        """Turns already logged for this conversation (the current one is logged after)."""
        if not conversation_id:
            return 0
        try:
            from .. import database as db

            row = db.query_one(
                """SELECT COUNT(*) AS n FROM conversations
                   WHERE tenant_id = ? AND conversation_id = ? AND user_id = ?""",
                (tenant_id or "default", conversation_id, user_id),
            )
            return int((row or {}).get("n") or 0)
        except Exception:
            logger.debug("episodic: turn count unavailable", exc_info=True)
            return 0

    def create_handoff_envelope(
        self,
        thread_id: str,
        user_id: str,
        objective: str,
        target_resolution: str,
        tenant_id: str = "default",
        evidence: list[EvidenceCard] | None = None,
        blockers: list[str] | None = None,
        next_action_type: str = "assist_taxpayer",
        suggested_message: str = "",
        *,
        locale: str = "en",
        source_channel: str = "web",
        caller_affect: str = "calm",
        priority: str = "normal",
        tool_receipts: list[ToolExecutionReceipt] | None = None,
        workflow_state_snapshot: dict[str, Any] | None = None,
        officer_macros: list[dict[str, str]] | None = None,
        eq_guidance: dict[str, Any] | None = None,
        required_inputs: list[str] | None = None,
        verified_slot_bank: dict[str, Any] | None = None,
        affect_trajectory: list[dict[str, Any]] | None = None,
        pending_background_tasks: list[str] | None = None,
    ) -> HandoffEnvelope:
        """Construct an immutable, structured handoff envelope for human or peer escalation."""
        import os

        from ..flags import flags

        eq_enabled = flags.is_enabled("eq_adaptive_retrieval", subject=user_id or None) or flags.is_enabled(
            "eq_multimodal_fusion", subject=user_id or None
        )
        effective_priority = priority or "normal"
        if eq_enabled and caller_affect in ("frustrated", "urgent_distress", "hardship") and effective_priority == "normal":
            effective_priority = "high"
        try:
            hardship_sla = max(15, int(os.getenv("EQ_HARDSHIP_SLA_TARGET_SECONDS", "45")))
        except ValueError:
            hardship_sla = 45
        target_s = (
            30
            if effective_priority == "urgent"
            else hardship_sla
            if (eq_enabled and caller_affect in ("urgent_distress", "hardship"))
            else 60
            if effective_priority == "high"
            else 120
        )
        effective_slots: dict[str, Any] = {}
        if user_id and flags.is_enabled("zero_effort_slot_prefill", subject=user_id):
            effective_slots.update(self.resolution_ledger.get_verified_slots(user_id, tenant_id=tenant_id))
        if verified_slot_bank:
            effective_slots.update(verified_slot_bank)
        tin_str = (
            user_id
            if user_id and user_id.isdigit() and len(user_id) == 10
            else str(effective_slots.get("tin") or "")
        )
        known_slot_keys = {str(k).strip().lower() for k, v in effective_slots.items() if v not in (None, "")}
        if tin_str:
            known_slot_keys.update({"tin", "taxpayer_tin", "taxpayer_id"})
        pruned_inputs = [
            req for req in (required_inputs or []) if str(req).strip().lower() not in known_slot_keys
        ]
        now = time.time()
        env = HandoffEnvelope(
            thread_id=thread_id,
            tenant_id=tenant_id,
            goal=HandoffGoal(primary_objective=objective, target_resolution=target_resolution),
            taxpayer_tin=tin_str,
            taxpayer_locale=locale or "en",
            verified_evidence=evidence or [],
            blockers_and_risks=blockers or [],
            next_action=RecommendedAction(
                action_type=next_action_type,
                suggested_message=suggested_message,
                required_inputs=pruned_inputs,
            ),
            source_channel=source_channel,
            caller_affect=caller_affect,
            workflow_state_snapshot=workflow_state_snapshot or {},
            tool_receipts=tool_receipts or [],
            sla=SLAContext(
                priority=effective_priority,
                target_response_seconds=target_s,
                deadline_utc=now + target_s,
            ),
            officer_macros=officer_macros or [],
            eq_guidance=dict(eq_guidance or {}),
            verified_slot_bank=effective_slots,
            affect_trajectory=list(affect_trajectory or []),
            pending_background_tasks=list(pending_background_tasks or []),
        )
        try:
            from ..analytics import metrics

            metrics.observe(
                "handoff_envelope_completeness_ratio",
                env.completeness_ratio(),
                labels={
                    "target_channel": env.target_channel,
                    "priority": priority or "normal",
                },
            )
        except Exception:
            logger.debug("handoff completeness metric skipped", exc_info=True)
        return env

    # -- Continuous Relationship Resolution Ledger --------------------
    def checkpoint_resolution_case(
        self,
        user_id: str,
        case: ResolutionCase,
        *,
        tenant_id: str = "default",
        asr_confidence: float | None = None,
    ) -> bool:
        """Persist a longitudinal ResolutionCase across channels (Voice/Web/Desk)."""
        return self.resolution_ledger.checkpoint(
            user_id,
            case,
            tenant_id=tenant_id,
            asr_confidence=asr_confidence,
        )

    # -- ExpRAG Procedural Trajectory Retrieval & Promotion (ADR-0012) --
    def retrieve_procedural_trajectories(
        self,
        query: str,
        *,
        top_k: int = 2,
        planned_tools: list[str] | None = None,
    ) -> str:
        """Return formatted ExpRAG procedural trajectory memory block (no PII, consent-independent)."""
        return self.procedural.format_experience_memory_block(
            query,
            top_k=top_k,
            planned_tools=planned_tools,
        )

    def record_verified_trajectory(
        self,
        *,
        intent: str,
        domain: str,
        user_query: str,
        plan: list[str],
        tool_sequence: list[str],
        faithfulness_score: float = 1.0,
        demonstration_turn: dict[str, Any] | None = None,
    ) -> Any:
        """Promote a high-faithfulness or officer-verified turn into ProceduralMemory."""
        return self.procedural.record_verified_trajectory(
            intent=intent,
            domain=domain,
            user_query=user_query,
            plan=plan,
            tool_sequence=tool_sequence,
            faithfulness_score=faithfulness_score,
            demonstration_turn=demonstration_turn,
        )

    def get_active_resolution_case(
        self,
        user_id: str,
        *,
        tenant_id: str = "default",
        current_channel: str = "",
    ) -> ResolutionCase | None:
        """Read an active cross-channel ResolutionCase for *user_id*."""
        return self.resolution_ledger.get_active_case(
            user_id,
            tenant_id=tenant_id,
            current_channel=current_channel,
        )

    # -- Working memory -----------------------------------------------
    def update_working(self, user_id: str, tenant_id: str = "default", **fields: Any) -> None:
        """Persist consented short-term state in the shared analytics backend."""
        if not self._has_consent(user_id, tenant_id=tenant_id):
            return
        from .. import database as db

        db.upsert_working_memory(
            user_id,
            tenant_id or "default",
            fields,
            ttl_seconds=WORKING_TTL_SECONDS,
        )

    # -- Subject-access export ----------------------------------------
    def export_user(self, user_id: str, tenant_id: str = "default") -> dict[str, Any]:
        """Ungated subject-access export of stored memory (UDPA data portability).

        Unlike :meth:`read_facts`, this is NOT consent-gated — a data subject is
        entitled to a copy of their stored data regardless of current consent.
        """
        # Export stored records directly, without retrieval decay, consent, or
        # a short result cap. Access/export must include historical facts even
        # when they are no longer selected for personalization.
        from .. import database as db

        facts = db.query_all(
            """SELECT * FROM user_facts WHERE tenant_id = ? AND user_id = ?
               ORDER BY extracted_at DESC""",
            (tenant_id or "default", user_id),
        )
        episodic = self.episodic.export_for_user(user_id, tenant_id=tenant_id)
        return {
            "facts": facts,
            "episodic": episodic,
            "working": db.get_working_memory(user_id, tenant_id=tenant_id),
        }

    # -- Erasure cascade ----------------------------------------------
    def forget_user(self, user_id: str, tenant_id: str = "default") -> dict[str, int]:
        """Cascade delete across all three tiers (UDPA right to erasure).

        The audit ledger is intentionally not touched — erasure is
        cryptographically noted but the hash chain is immutable.
        """
        from .. import database as db

        working_deleted = db.clear_working_memory(user_id, tenant_id=tenant_id)
        episodic_deleted = self.episodic.delete_for_user(user_id, tenant_id)
        facts_deleted = self.semantic.forget_user(user_id, tenant_id)
        return {
            "working": working_deleted,
            "episodic": episodic_deleted,
            "semantic": facts_deleted,
        }

    def cleanup_expired(self) -> dict[str, int]:
        """Run retention across every memory tier.

        Working memory uses the shared backend and a short expiry; persistent
        tiers are deleted explicitly.
        """
        from .. import database as db

        return {
            "working": db.cleanup_expired_working_memory(),
            "episodic": self.episodic.cleanup_expired(),
            "semantic": self.semantic.cleanup_expired(),
        }


# ---------------------------------------------------------------------------
# Topic tags (keeps the dependency count low)
# ---------------------------------------------------------------------------
# Whole words only. Substring matching tagged "I run a private company" as VAT
# ("pri-vat-e"), "the city council" as corporation tax and "getting started" as
# TIN registration. Patterns are English, read against a turn's English form
# (G119); the Luganda and Swahili words are a fallback for turns stored before
# English forms were.
_TOPIC_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("vat", (r"vat", r"value[\s-]added(?:\s+tax)?")),
    ("paye", (r"paye", r"take[\s-]home(?:\s+pay)?", r"salary\s+tax", r"pay\s+as\s+you\s+earn")),
    ("cit", (r"corporat(?:e|ion)\s+(?:income\s+)?tax", r"cit")),
    ("customs", (r"import(?:s|ed|ing|er)?", r"customs", r"cif", r"tariffs?", r"forodha")),
    ("registration", (r"regist(?:er|ers|ered|ering|ration)", r"tin", r"sign\s+up")),
    ("withholding", (r"withholding", r"wht", r"kodi\s+ya\s+zuio", r"zuio")),
    ("capital_gains", (r"capital\s+gains?", r"cgt")),
    ("rental", (r"rental", r"landlords?", r"tenancy", r"renting\s+out", r"kodi\s+ya\s+pango")),
    ("stamp", (r"stamp\s+duty", r"property\s+transfer", r"land\s+transfer")),
    ("motor_vehicle", (r"motor\s+vehicles?", r"logbooks?", r"number\s+plates?")),
    ("excise", (r"excise", r"dts", r"digital\s+tax\s+stamps?")),
    ("efris", (r"efris", r"fiscal\s+invoices?")),
    ("escalation", (r"officer", r"human", r"dispute", r"appeal", r"objection")),
    ("general_tax", (r"omusolo", r"kodi", r"ushuru")),
)
_TOPIC_RES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (tag, re.compile(r"\b(?:" + "|".join(patterns) + r")\b", re.I)) for tag, patterns in _TOPIC_PATTERNS
)
_TOPIC_LABELS: dict[str, str] = {
    "vat": "VAT",
    "paye": "PAYE",
    "cit": "corporation tax",
    "customs": "customs and imports",
    "registration": "TIN registration",
    "withholding": "withholding tax",
    "capital_gains": "capital gains tax",
    "rental": "rental income tax",
    "stamp": "stamp duty",
    "motor_vehicle": "motor vehicle registration",
    "excise": "excise duty and digital tax stamps",
    "efris": "EFRIS",
    "escalation": "speaking to an officer",
    "general_tax": "general tax questions",
}


def topic_label(text: str) -> str:
    """A readable label for the topic *text* names, or "" when it names none."""
    tag = _guess_topic_tag(text)
    return "" if tag == "general" else _TOPIC_LABELS.get(tag, tag.replace("_", " "))


def _guess_topic_tag(text: str) -> str:
    """The first topic *text* names, or ``general``."""
    for tag, pattern in _TOPIC_RES:
        if pattern.search(text or ""):
            return tag
    return "general"


def _ordered_topic_tags(texts: list[str]) -> list[str]:
    """Specific topics in the order the conversation first named them.

    ``general_tax`` (a bare "omusolo") counts only when nothing more specific
    was named.
    """
    tags: list[str] = []
    for text in texts:
        for tag, pattern in _TOPIC_RES:
            if tag not in tags and pattern.search(text or ""):
                tags.append(tag)
    specific = [t for t in tags if t != "general_tax"]
    return specific or tags


_SAFE_OUTCOME_TOKEN_RE = re.compile(r"^[a-zA-Z0-9_/-]{1,48}$")


def _format_episodic_outcomes(outcomes: dict[str, Any] | None) -> str:
    """Format sanitized tool/workflow/resolution outcomes for episodic memory (MS-1)."""
    if not outcomes or not isinstance(outcomes, dict):
        return ""
    parts: list[str] = []
    tools = outcomes.get("tools_used") or outcomes.get("tools") or []
    if isinstance(tools, (list, tuple)):
        safe_tools = [str(t) for t in tools[:3] if _SAFE_OUTCOME_TOKEN_RE.match(str(t))]
        if safe_tools:
            parts.append(f"verified tools: {', '.join(safe_tools)}")
    workflow = str(outcomes.get("workflow") or outcomes.get("workflow_id") or "").strip()
    if workflow and _SAFE_OUTCOME_TOKEN_RE.match(workflow):
        parts.append(f"workflow: {workflow}")
    status = str(outcomes.get("resolution_status") or outcomes.get("status") or "").strip()
    if status and _SAFE_OUTCOME_TOKEN_RE.match(status):
        parts.append(f"status: {status}")
    fy = str(outcomes.get("fiscal_year") or "").strip()
    if fy and _SAFE_OUTCOME_TOKEN_RE.match(fy):
        parts.append(f"FY: {fy}")
    return "; ".join(parts)


def _join_labels(labels: list[str]) -> str:
    if len(labels) <= 1:
        return "".join(labels)
    return f"{', '.join(labels[:-1])} and {labels[-1]}"


def _user_text(turn: dict[str, Any]) -> str:
    """A taxpayer turn's English form when stored, else its own words."""
    if "user_message" in turn:
        return str(turn.get("user_message_en") or turn.get("user_message") or "").strip()
    if turn.get("role") in ("user", "user_message"):
        return str(turn.get("content_en") or turn.get("content") or turn.get("message") or "").strip()
    return ""


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

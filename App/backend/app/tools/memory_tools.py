"""Agent memory operations exposed as standardized tools (2026).

Enables autonomous agents to recall, store, update, and discard facts across
the cognitive memory hierarchy within consent and tenant boundaries.
"""
from __future__ import annotations

import logging
from typing import Any

from . import Tool, ToolRegistry, ToolSchema

logger = logging.getLogger(__name__)


class MemoryRecallTool(Tool):
    """Semantic and episodic memory recall tool."""

    schema = ToolSchema(
        name="memory_recall",
        description="Search persistent semantic facts, episodic interaction summaries, or procedural exemplars.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Semantic query describing the fact, preference, or past topic to recall.",
                },
                "memory_type": {
                    "type": "string",
                    "enum": ["semantic", "episodic", "procedural"],
                    "description": "The memory tier to query: 'semantic' (facts/preferences), 'episodic' (past interactions), 'procedural' (skills/exemplars).",
                    "default": "semantic",
                },
                "confidence_floor": {
                    "type": "number",
                    "description": "Minimum confidence threshold (0.0 to 1.0).",
                    "default": 0.70,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "query": {"type": "string"},
                "memory_type": {"type": "string"},
                "count": {"type": "integer"},
                "results": {"type": "array"},
                "explanation": {"type": "string"},
                "error": {"type": "string"},
            },
            "required": ["ok"],
        },
        risk="low",
        namespace="core",
    )

    def execute(
        self,
        query: str,
        memory_type: str = "semantic",
        confidence_floor: float = 0.70,
        user_id: str = "",
        tenant_id: str = "default",
        **kwargs: Any,
    ) -> dict[str, Any]:
        from ..memory import get_memory_service

        memsvc = get_memory_service()
        results: list[dict[str, Any]] = []

        if memory_type == "semantic":
            if user_id:
                facts = memsvc.read_facts(user_id=user_id, purpose="personalization", tenant_id=tenant_id)
                q_lower = query.lower()
                for f in facts:
                    if f.confidence >= confidence_floor and (q_lower in f.key.lower() or q_lower in str(f.value).lower()):
                        results.append({
                            "fact_id": f.fact_id,
                            "category": f.category,
                            "key": f.key,
                            "value": f.value,
                            "confidence": f.confidence,
                            "source": f.source,
                        })
        elif memory_type == "episodic":
            if user_id:
                episodes = memsvc.read_episodic(user_id=user_id, purpose="personalization", tenant_id=tenant_id)
                q_lower = query.lower()
                for ep in episodes:
                    summary = ep.get("summary", "")
                    if q_lower in summary.lower() or q_lower in ep.get("topic_tag", "").lower():
                        results.append(ep)
        elif memory_type == "procedural":
            exemplar = memsvc.procedural.find_exemplar(query)
            if exemplar:
                results.append(exemplar.to_dict())
            else:
                skills = memsvc.procedural.list_skills()
                q_lower = query.lower()
                for s in skills:
                    if q_lower in s.name.lower() or q_lower in s.description.lower():
                        results.append(s.to_dict())

        return {
            "ok": True,
            "query": query,
            "memory_type": memory_type,
            "count": len(results),
            "results": results,
            "explanation": f"Recalled {len(results)} {memory_type} memory records." if results else f"No {memory_type} memory records matched query.",
        }


class MemoryStoreTool(Tool):
    """Tool to persist high-confidence facts into semantic memory."""

    schema = ToolSchema(
        name="memory_store",
        description="Persist verified facts or taxpayer profile attributes into semantic memory with confidence and source.",
        parameters={
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Unique semantic descriptor for the fact (e.g. 'taxpayer_kind', 'business_turnover').",
                },
                "value": {
                    "type": "string",
                    "description": "The factual value to store.",
                },
                "confidence": {
                    "type": "number",
                    "description": "Confidence score between 0.0 and 1.0 (minimum 0.70 required for long-term retention).",
                    "default": 1.0,
                },
                "source_citation": {
                    "type": "string",
                    "description": "Statutory citation, document name, or user declaration provenance.",
                },
            },
            "required": ["key", "value"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "fact_id": {"type": "string"},
                "key": {"type": "string"},
                "value": {"type": "string"},
                "status": {"type": "string"},
                "explanation": {"type": "string"},
                "error": {"type": "string"},
            },
            "required": ["ok"],
        },
        risk="medium",
        namespace="core",
        required_scopes=("personalization",),
        scope_exempt_roles=("verified_taxpayer", "ura_staff", "ura_admin"),
        read_only=False,
        destructive=False,
        idempotent=False,
        open_world=False,
    )

    def execute(
        self,
        key: str,
        value: str,
        confidence: float = 1.0,
        source_citation: str = "user_input",
        user_id: str = "",
        tenant_id: str = "default",
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not user_id:
            return {"ok": False, "error": "user_id required to persist facts"}
        if confidence < 0.70:
            return {"ok": False, "error": "confidence below minimum threshold of 0.70"}

        import time
        import uuid

        from ..memory import UserFact, get_memory_service

        memsvc = get_memory_service()
        fact = UserFact(
            fact_id=str(uuid.uuid4()),
            user_id=user_id,
            tenant_id=tenant_id or "default",
            category="taxpayer_fact",
            subject="user",
            predicate=key,
            object_value=str(value),
            confidence=confidence,
            extracted_at=time.time(),
            extractor_model=source_citation,
        )
        fact_id = memsvc.semantic.write(fact)
        return {
            "ok": bool(fact_id),
            "fact_id": fact_id,
            "key": key,
            "value": value,
            "status": "stored",
            "explanation": f"Persisted factual attribute '{key}' in semantic memory.",
        }


class MemoryDiscardTool(Tool):
    """Tool to remove or tombstone outdated facts from memory."""

    schema = ToolSchema(
        name="memory_discard",
        description="Discard or tombstone superseded or user-retracted facts from memory.",
        parameters={
            "type": "object",
            "properties": {
                "fact_id": {
                    "type": "string",
                    "description": "The unique ID of the fact to remove.",
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for discarding the fact (e.g. 'superseded_by_taxpayer', 'statutory_change').",
                },
            },
            "required": ["fact_id"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "fact_id": {"type": "string"},
                "status": {"type": "string"},
                "reason": {"type": "string"},
                "explanation": {"type": "string"},
                "error": {"type": "string"},
            },
            "required": ["ok"],
        },
        risk="medium",
        namespace="core",
        required_scopes=("personalization",),
        scope_exempt_roles=("verified_taxpayer", "ura_staff", "ura_admin"),
        read_only=False,
        destructive=True,
        idempotent=True,
        open_world=False,
    )

    def execute(
        self,
        fact_id: str,
        reason: str = "superseded",
        user_id: str = "",
        tenant_id: str = "default",
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not user_id:
            return {"ok": False, "error": "user_id required to discard facts"}

        import time

        from .. import database as db

        try:
            db.execute(
                "UPDATE user_facts SET superseded_by = 'discarded', invalidated_at = ? WHERE fact_id = ? AND user_id = ? AND tenant_id = ?",
                (time.time(), fact_id, user_id, tenant_id or "default"),
            )
        except Exception as exc:
            logger.debug("memory_discard db error: %s", exc)

        return {
            "ok": True,
            "fact_id": fact_id,
            "status": "discarded",
            "reason": reason,
            "explanation": f"Fact {fact_id} discarded from memory (reason: {reason}).",
        }


# Register memory tools in ToolRegistry
ToolRegistry.register(MemoryRecallTool())
ToolRegistry.register(MemoryStoreTool())
ToolRegistry.register(MemoryDiscardTool())

"""Typed state container for the graph orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class GraphOutcome(str, Enum):
    """Terminal outcomes the graph can produce."""

    ANSWERED = "answered"
    CLARIFY = "clarify"
    ESCALATED = "escalated"
    BLOCKED = "blocked"
    ABSTAINED = "abstained"
    TRUNCATED = "truncated"     # max iterations / depth reached
    ERRORED = "errored"


@dataclass
class AgentGraphState:
    """State dict threaded through every node in the graph.

    Unlike LangGraph's TypedDict approach, this is a mutable
    dataclass so nodes can mutate fields in place.  Every mutation
    is snapshotted into the audit ledger if ``FLAG_AUDIT_LEDGER``
    is on.

    Keep the field set small — it's logged on every transition.
    """

    # -- Request --
    query: str = ""
    rewritten_query: str = ""
    locale: str = "en"
    top_k: int = 4

    # -- Auth --
    tenant_id: str = "default"
    user_id: str = ""
    role: str = "public"
    granted_purposes: list[str] = field(default_factory=list)

    # -- Plan --
    plan: list[str] = field(default_factory=list)          # ordered step descriptions
    plan_reason: str = ""

    # -- Act / Observe --
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    max_iterations: int = 3

    # -- Retrieval --
    hits: list[dict[str, Any]] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    retrieval_mode: str = "keyword"

    # -- Reflect --
    faithfulness: float | None = None
    reflections: list[str] = field(default_factory=list)
    reflect_count: int = 0
    max_reflections: int = 1

    # -- Response --
    reply: str = ""
    outcome: GraphOutcome = GraphOutcome.ANSWERED
    error: str = ""
    clarification_question: str = ""
    escalation_reason: str = ""
    ticket_id: str = ""

    # -- Telemetry --
    trace: list[dict[str, Any]] = field(default_factory=list)  # (node, duration_ms)

    def record(self, node: str, duration_ms: float, **extras: Any) -> None:
        self.trace.append({"node": node, "duration_ms": round(duration_ms, 2), **extras})

    def to_summary(self) -> dict[str, Any]:
        """Short dict for logging — excludes verbose fields."""
        return {
            "query_len": len(self.query),
            "user_id": self.user_id or "anon",
            "tenant_id": self.tenant_id,
            "role": self.role,
            "plan_steps": len(self.plan),
            "tool_calls": len(self.tool_calls),
            "hits": len(self.hits),
            "iterations": self.iterations,
            "reflect_count": self.reflect_count,
            "outcome": self.outcome.value,
            "faithfulness": self.faithfulness,
            "reply_len": len(self.reply),
        }

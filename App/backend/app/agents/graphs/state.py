"""Typed state container for the graph orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..loop_control import ToolCallBudget


class GraphOutcome(str, Enum):
    """Terminal outcomes the graph can produce."""

    ANSWERED = "answered"
    CLARIFY = "clarify"
    ESCALATED = "escalated"
    BLOCKED = "blocked"
    ABSTAINED = "abstained"
    TRUNCATED = "truncated"  # max iterations / depth reached
    ERRORED = "errored"


@dataclass
class AgentGraphState:
    """State dict threaded through every node in the graph.

    This mutable dataclass is request-scoped and is not checkpointed or
    snapshotted into the audit ledger. ``trace`` records only node names,
    durations, and bounded status metadata; the surrounding service writes a
    separate privacy-minimized turn audit record.

    Keep the field set small — it's logged on every transition.
    """

    # -- Request --
    query: str = ""
    rewritten_query: str = ""
    locale: str = "en"
    top_k: int = 4
    conversation_history: list[dict[str, Any]] = field(default_factory=list)
    context_summary: str = ""

    # -- Auth --
    tenant_id: str = "default"
    user_id: str = ""
    role: str = "public"
    granted_purposes: list[str] = field(default_factory=list)

    # -- Plan --
    plan: list[str] = field(default_factory=list)  # ordered step descriptions
    plan_reason: str = ""

    # -- Act / Observe --
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    #: Tools the plan named but ``node_act`` did not dispatch, with why —
    #: unfillable arguments, or a spend ceiling.
    skipped_tools: list[dict[str, Any]] = field(default_factory=list)
    #: Per-turn tool spend ceilings and duplicate-call memo.  Excluded
    #: from ``repr`` because state is logged on every graph transition.
    budget: ToolCallBudget = field(default_factory=ToolCallBudget, repr=False)
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
    #: One specialist → retrieve hop when tools produced no evidence (G21/G23).
    handoff_count: int = 0
    max_handoffs: int = 1
    handoff_from: str = ""
    handoff_reason: str = ""

    # -- Response --
    reply: str = ""
    outcome: GraphOutcome = GraphOutcome.ANSWERED
    error: str = ""
    clarification_question: str = ""
    escalation_reason: str = ""
    ticket_id: str = ""
    agent_role: str = "graph_agent"

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
            "skipped_tools": len(self.skipped_tools),
            "hits": len(self.hits),
            "iterations": self.iterations,
            "reflect_count": self.reflect_count,
            "outcome": self.outcome.value,
            "faithfulness": self.faithfulness,
            "reply_len": len(self.reply),
            "budget": self.budget.stats(),
        }

    def to_dict(self) -> dict[str, Any]:
        """Serializable dict of graph state for durable checkpointing."""
        return {
            "query": self.query,
            "rewritten_query": self.rewritten_query,
            "locale": self.locale,
            "top_k": self.top_k,
            "context_summary": self.context_summary,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "role": self.role,
            "granted_purposes": list(self.granted_purposes),
            "plan": list(self.plan),
            "plan_reason": self.plan_reason,
            "tool_calls": list(self.tool_calls),
            "observations": list(self.observations),
            "skipped_tools": list(self.skipped_tools),
            "iterations": self.iterations,
            "max_iterations": self.max_iterations,
            "retrieval_mode": self.retrieval_mode,
            "faithfulness": self.faithfulness,
            "reflect_count": self.reflect_count,
            "handoff_count": self.handoff_count,
            "reply": self.reply,
            "outcome": self.outcome.value,
            "error": self.error,
            "clarification_question": self.clarification_question,
            "escalation_reason": self.escalation_reason,
            "ticket_id": self.ticket_id,
            "agent_role": self.agent_role,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentGraphState:
        """Reconstruct an AgentGraphState from a stored checkpoint dict."""
        outcome_val = data.get("outcome", GraphOutcome.ANSWERED.value)
        try:
            outcome = GraphOutcome(outcome_val)
        except ValueError:
            outcome = GraphOutcome.ANSWERED

        state = cls(
            query=str(data.get("query", "")),
            rewritten_query=str(data.get("rewritten_query", "")),
            locale=str(data.get("locale", "en")),
            top_k=int(data.get("top_k", 4)),
            context_summary=str(data.get("context_summary", "")),
            tenant_id=str(data.get("tenant_id", "default")),
            user_id=str(data.get("user_id", "")),
            role=str(data.get("role", "public")),
            granted_purposes=list(data.get("granted_purposes", [])),
            plan=list(data.get("plan", [])),
            plan_reason=str(data.get("plan_reason", "")),
            tool_calls=list(data.get("tool_calls", [])),
            observations=list(data.get("observations", [])),
            skipped_tools=list(data.get("skipped_tools", [])),
            iterations=int(data.get("iterations", 0)),
            max_iterations=int(data.get("max_iterations", 3)),
            retrieval_mode=str(data.get("retrieval_mode", "keyword")),
            faithfulness=float(data["faithfulness"]) if data.get("faithfulness") is not None else None,
            reflect_count=int(data.get("reflect_count", 0)),
            handoff_count=int(data.get("handoff_count", 0)),
            reply=str(data.get("reply", "")),
            outcome=outcome,
            error=str(data.get("error", "")),
            clarification_question=str(data.get("clarification_question", "")),
            escalation_reason=str(data.get("escalation_reason", "")),
            ticket_id=str(data.get("ticket_id", "")),
            agent_role=str(data.get("agent_role", "graph_agent")),
        )
        return state

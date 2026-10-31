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
    WAITING_USER = "waiting_user"  # human-in-the-loop confirmation required
    TRUNCATED = "truncated"  # max iterations / depth reached
    ERRORED = "errored"


@dataclass
class GoalSpec:
    """Long-horizon goal specification for multi-step workflows."""

    goal_id: str = ""
    description: str = ""
    status: str = "pending"  # "pending", "in_progress", "completed", "failed"
    created_at_s: float = 0.0
    updated_at_s: float = 0.0
    sub_tasks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "description": self.description,
            "status": self.status,
            "created_at_s": self.created_at_s,
            "updated_at_s": self.updated_at_s,
            "sub_tasks": list(self.sub_tasks),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoalSpec:
        return cls(
            goal_id=str(data.get("goal_id", "")),
            description=str(data.get("description", "")),
            status=str(data.get("status", "pending")),
            created_at_s=float(data.get("created_at_s", 0.0)),
            updated_at_s=float(data.get("updated_at_s", 0.0)),
            sub_tasks=list(data.get("sub_tasks", [])),
        )


@dataclass
class TaxpayerActionRequest:
    """Human-in-the-loop action requiring explicit user confirmation."""

    action_type: str = ""
    description: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    requires_consent: bool = True
    authorized: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "description": self.description,
            "payload": dict(self.payload),
            "requires_consent": self.requires_consent,
            "authorized": self.authorized,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaxpayerActionRequest:
        return cls(
            action_type=str(data.get("action_type", "")),
            description=str(data.get("description", "")),
            payload=dict(data.get("payload", {})),
            requires_consent=bool(data.get("requires_consent", True)),
            authorized=bool(data.get("authorized", False)),
        )


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

    # -- Long-Horizon Goal & HITL State --
    schema_version: int = 2
    active_goals: dict[str, GoalSpec] = field(default_factory=dict)
    current_goal_id: str = ""
    pending_action: TaxpayerActionRequest | None = None
    replan_count: int = 0
    max_replans: int = 2

    # -- Response --
    reply: str = ""
    outcome: GraphOutcome = GraphOutcome.ANSWERED
    error: str = ""
    clarification_question: str = ""
    escalation_reason: str = ""
    ticket_id: str = ""
    agent_role: str = "graph_agent"
    sub_routes: list[str] = field(default_factory=list)
    specialist_handoffs: list[dict[str, Any]] = field(default_factory=list)
    # -- Live check (once per turn, however often reflection retrieves) --
    live_checked: bool = False
    live_check_notice: str = ""
    live_check_disclosure: str = ""
    live_web_hits: list[dict[str, Any]] = field(default_factory=list)
    live_mode: str = ""

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
            "active_goals": len(self.active_goals),
            "current_goal": self.current_goal_id,
            "has_pending_action": self.pending_action is not None,
            "replan_count": self.replan_count,
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
            "schema_version": self.schema_version,
            "query": self.query,
            "rewritten_query": self.rewritten_query,
            "locale": self.locale,
            "top_k": self.top_k,
            "conversation_history": list(self.conversation_history),
            "context_summary": self.context_summary,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "role": self.role,
            "granted_purposes": list(self.granted_purposes),
            "plan": list(self.plan),
            "plan_reason": self.plan_reason,
            "active_goals": {gid: g.to_dict() for gid, g in self.active_goals.items()},
            "current_goal_id": self.current_goal_id,
            "pending_action": self.pending_action.to_dict() if self.pending_action else None,
            "replan_count": self.replan_count,
            "max_replans": self.max_replans,
            "tool_calls": list(self.tool_calls),
            "observations": list(self.observations),
            "skipped_tools": list(self.skipped_tools),
            "iterations": self.iterations,
            "max_iterations": self.max_iterations,
            "hits": list(self.hits),
            "sources": list(self.sources),
            "citations": list(self.citations),
            "retrieval_mode": self.retrieval_mode,
            "faithfulness": self.faithfulness,
            "reflections": list(self.reflections),
            "reflect_count": self.reflect_count,
            "max_reflections": self.max_reflections,
            "handoff_count": self.handoff_count,
            "max_handoffs": self.max_handoffs,
            "reply": self.reply,
            "outcome": self.outcome.value,
            "error": self.error,
            "clarification_question": self.clarification_question,
            "escalation_reason": self.escalation_reason,
            "ticket_id": self.ticket_id,
            "agent_role": self.agent_role,
            "sub_routes": list(self.sub_routes),
            "specialist_handoffs": list(self.specialist_handoffs),
            "live_checked": self.live_checked,
            "live_check_notice": self.live_check_notice,
            "live_check_disclosure": self.live_check_disclosure,
            "live_web_hits": list(self.live_web_hits),
            "live_mode": self.live_mode,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentGraphState:
        """Reconstruct an AgentGraphState from a stored checkpoint dict."""
        outcome_val = data.get("outcome", GraphOutcome.ANSWERED.value)
        try:
            outcome = GraphOutcome(outcome_val)
        except ValueError:
            outcome = GraphOutcome.ANSWERED

        goals_raw = data.get("active_goals", {})
        active_goals = {
            str(gid): GoalSpec.from_dict(g) if isinstance(g, dict) else g
            for gid, g in goals_raw.items()
        } if isinstance(goals_raw, dict) else {}

        pending_raw = data.get("pending_action")
        pending_action = (
            TaxpayerActionRequest.from_dict(pending_raw)
            if isinstance(pending_raw, dict)
            else None
        )

        state = cls(
            schema_version=int(data.get("schema_version", 1)),
            query=str(data.get("query", "")),
            rewritten_query=str(data.get("rewritten_query", "")),
            locale=str(data.get("locale", "en")),
            top_k=int(data.get("top_k", 4)),
            conversation_history=list(data.get("conversation_history", [])),
            context_summary=str(data.get("context_summary", "")),
            tenant_id=str(data.get("tenant_id", "default")),
            user_id=str(data.get("user_id", "")),
            role=str(data.get("role", "public")),
            granted_purposes=list(data.get("granted_purposes", [])),
            plan=list(data.get("plan", [])),
            plan_reason=str(data.get("plan_reason", "")),
            active_goals=active_goals,
            current_goal_id=str(data.get("current_goal_id", "")),
            pending_action=pending_action,
            replan_count=int(data.get("replan_count", 0)),
            max_replans=int(data.get("max_replans", 2)),
            tool_calls=list(data.get("tool_calls", [])),
            observations=list(data.get("observations", [])),
            skipped_tools=list(data.get("skipped_tools", [])),
            iterations=int(data.get("iterations", 0)),
            max_iterations=int(data.get("max_iterations", 3)),
            hits=list(data.get("hits", [])),
            sources=list(data.get("sources", [])),
            citations=list(data.get("citations", [])),
            retrieval_mode=str(data.get("retrieval_mode", "keyword")),
            faithfulness=float(data["faithfulness"]) if data.get("faithfulness") is not None else None,
            reflections=list(data.get("reflections", [])),
            reflect_count=int(data.get("reflect_count", 0)),
            max_reflections=int(data.get("max_reflections", 1)),
            handoff_count=int(data.get("handoff_count", 0)),
            max_handoffs=int(data.get("max_handoffs", 1)),
            reply=str(data.get("reply", "")),
            outcome=outcome,
            error=str(data.get("error", "")),
            clarification_question=str(data.get("clarification_question", "")),
            escalation_reason=str(data.get("escalation_reason", "")),
            ticket_id=str(data.get("ticket_id", "")),
            agent_role=str(data.get("agent_role", "graph_agent")),
            sub_routes=list(data.get("sub_routes", [])),
            specialist_handoffs=list(data.get("specialist_handoffs", [])),
            live_checked=bool(data.get("live_checked", False)),
            live_check_notice=str(data.get("live_check_notice", "")),
            live_check_disclosure=str(data.get("live_check_disclosure", "")),
            live_web_hits=list(data.get("live_web_hits", [])),
            live_mode=str(data.get("live_mode", "")),
        )
        return state

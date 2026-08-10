"""Agent state + routing enums.

The ``AgentState`` TypedDict is the single piece of mutable state that
flows through the supervisor → specialist → response pipeline.  It's
serialisable (all fields are plain Python / dict / list) so it can be
logged to the audit trail or persisted for resume-later workflows.

Keep this module free of heavy imports — it's loaded by the
supervisor on every request, and should stay cheap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypedDict


class AgentRoute(str, Enum):
    """Possible routing decisions the supervisor can make.

    The string values are stable identifiers — they're logged to
    analytics and referenced by specialist prompts, so renaming
    requires a migration.
    """

    # Route to the existing RAG pipeline (retrieval + LLM synthesis).
    # Default path for factual questions about URA policy.
    RAG = "rag"

    # Route to the tool-calling loop with access to calculators,
    # calendar, rates, and the RAG tool.  For numeric/computational
    # questions where deterministic tools beat the LLM.
    TOOLS = "tools"

    # Route to the tax specialist agent (prompt + narrowed tool set).
    # Used when the query is clearly tax-computation focused.
    TAX_SPECIALIST = "tax_specialist"

    # Route to the customs specialist agent.
    CUSTOMS_SPECIALIST = "customs_specialist"

    # Query is ambiguous or too short to act on — ask for clarification.
    CLARIFY = "clarify"

    # Query is out-of-scope, sensitive, or requires human review.
    # The response goes to the ticket queue (Phase D).
    ESCALATE = "escalate"

    # User sent a greeting — respond warmly without retrieval.
    GREET = "greet"

    # Input was blocked by the InputGuard (prompt injection etc).
    BLOCKED = "blocked"


@dataclass(frozen=True)
class RouteDecision:
    """Result of a single routing classification.

    Immutable so passing it through the pipeline is safe.  The
    ``confidence`` field is used by the supervisor to decide whether
    to stick with the rule-based decision or ask the LLM for a
    second opinion.
    """

    route: AgentRoute
    reason: str
    confidence: float  # 0.0 – 1.0
    suggested_tools: list[str] = field(default_factory=list)
    clarification_question: str = ""


class AgentState(TypedDict, total=False):
    """State container threaded through every agent hop.

    Fields are *optional* (``total=False``) — the supervisor fills
    in what it knows, the specialist enriches, and the response
    builder consumes what's present.  This lets new agents be added
    without breaking existing ones.
    """

    # -- Request metadata ----------------------------------------------
    query: str
    rewritten_query: str
    session_id: str
    conversation_id: str
    request_id: str
    locale: str
    top_k: int

    # -- Conversation context ------------------------------------------
    conversation_history: list[dict[str, str]]

    # -- Routing -------------------------------------------------------
    route: AgentRoute
    route_reason: str
    route_confidence: float

    # -- Retrieval -----------------------------------------------------
    hits: list[dict[str, Any]]
    retrieval_mode: str
    sources: list[str]
    citations: list[dict[str, str]]

    # -- Tool use ------------------------------------------------------
    tool_calls: list[dict[str, Any]]
    tool_iterations: int

    # -- Response ------------------------------------------------------
    reply: str
    faithfulness_score: float | None
    escalation_required: bool
    escalation_reason: str
    clarification_question: str

    # -- Telemetry -----------------------------------------------------
    timings: dict[str, float]  # stage name → ms
    specialist: str  # which specialist handled it, if any

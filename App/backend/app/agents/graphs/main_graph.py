"""Concrete nodes + factory for the main agent graph.

Structure:

    entry (route)
      ├─ rag_path   → retrieve → synth → reflect → respond
      ├─ tools_path → plan → act → observe → (reflect) → respond
      ├─ clarify    → respond (early return)
      └─ escalate   → respond (early return + ticket)

Every node is a pure function of ``AgentGraphState`` — this is
required for LangGraph migration compatibility and makes replay
from the audit ledger trivial.
"""

from __future__ import annotations

import logging

from ...mcp import get_client
from ...mcp.tool_rag import ToolRAGSelector
from ..state import AgentRoute
from .runtime import END, GraphNode, GraphRuntime, NodeResult
from .state import AgentGraphState, GraphOutcome

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------
def node_route(state: AgentGraphState) -> NodeResult:
    """Delegate routing to the existing Phase C supervisor."""
    from ..supervisor import supervisor as _supervisor

    decision = _supervisor.classify(
        state.rewritten_query or state.query,
        has_conversation_history=False,
    )

    if decision.route == AgentRoute.CLARIFY:
        state.clarification_question = decision.clarification_question
        state.outcome = GraphOutcome.CLARIFY
        return NodeResult(next_node="respond", outcome=GraphOutcome.CLARIFY)

    if decision.route == AgentRoute.ESCALATE:
        state.escalation_reason = decision.reason
        state.outcome = GraphOutcome.ESCALATED
        return NodeResult(next_node="respond", outcome=GraphOutcome.ESCALATED)

    if decision.route in (
        AgentRoute.TOOLS,
        AgentRoute.TAX_SPECIALIST,
        AgentRoute.CUSTOMS_SPECIALIST,
    ):
        # The supervisor already picked a whitelist — seed the plan
        state.plan = list(decision.suggested_tools)
        state.plan_reason = decision.reason
        return NodeResult(next_node="tool_rag_select")

    # Default: factual retrieval
    return NodeResult(next_node="retrieve")


def node_tool_rag_select(state: AgentGraphState) -> NodeResult:
    """Run Tool RAG on the query to narrow the tool whitelist.

    The supervisor's suggested_tools come in as state.plan.  We
    take the *intersection* of (supervisor suggestion) and (Tool
    RAG top-k) — the supervisor's hard picks stay, and Tool RAG
    adds any missed but relevant tools.
    """
    client = get_client()
    eligible = client.available_for(
        user_role=state.role,
        granted_purposes=state.granted_purposes,
    )

    import os

    if os.getenv("FLAG_TOOL_RAG", "false").lower() != "true":
        # Flag off → accept the supervisor's raw whitelist
        state.plan = [t for t in state.plan if t in eligible]
        return NodeResult(next_node="act")

    selector = ToolRAGSelector()
    selection = selector.select(
        query=state.rewritten_query or state.query,
        eligible_names=eligible,
        k=5,
    )
    # Union of supervisor plan and Tool RAG selection, preserving order
    seen: set[str] = set()
    merged: list[str] = []
    for name in list(state.plan) + list(selection.tool_names):
        if name in seen or name not in eligible:
            continue
        seen.add(name)
        merged.append(name)
    state.plan = merged
    return NodeResult(next_node="act")


def node_retrieve(state: AgentGraphState) -> NodeResult:
    """Phase 1-13 hybrid retrieval — kept as the RAG-path node."""
    # Deferred import to avoid the heavy retriever at module load
    from ...retriever import HybridRetriever

    retriever = HybridRetriever()
    if retriever.initialize():
        hits = retriever.search(state.rewritten_query or state.query, top_k=state.top_k)
        if hits:
            state.hits = hits
            state.retrieval_mode = "hybrid"
            state.sources = list({h.get("source", "") for h in hits if h.get("source")})
            state.citations = HybridRetriever.build_citations(hits)
    return NodeResult(next_node="synthesize")


def node_act(state: AgentGraphState) -> NodeResult:
    """Dispatch the tool calls in state.plan via the MCP client.

    This node is **Phase 15 Lite** — it just calls every tool the
    supervisor suggested.  Phase 15 full replaces it with a real
    ReAct loop where the LLM picks tools mid-generation.
    """
    client = get_client()
    state.iterations += 1
    for tool_name in state.plan:
        # Empty args — Phase 15 full fills these from the LLM tool_call
        # parser.  In Phase 15 Lite we rely on tool default kwargs.
        result = client.call_tool(
            tool_name,
            {},
            tenant_id=state.tenant_id,
            user_id=state.user_id,
            iteration=state.iterations,
        )
        state.tool_calls.append(
            {
                "call_id": result.call_id,
                "name": tool_name,
                "ok": result.ok,
                "duration_ms": result.duration_ms,
            }
        )
        state.observations.append(result.result)

    return NodeResult(next_node="synthesize")


def node_synthesize(state: AgentGraphState) -> NodeResult:
    """Produce the reply text.

    Phase 15 Lite: falls through to the existing service.py LLM
    call path (non-agentic) or the tool-calling loop when
    FLAG_TOOL_USE is on.  The graph here is a *control-flow
    scaffold* — the actual LLM call still lives in service.py
    for now.
    """
    if not state.hits and not state.observations:
        state.reply = (
            "I don't have enough information to answer this question reliably. "
            "Please contact URA directly at https://ura.go.ug."
        )
        state.outcome = GraphOutcome.ABSTAINED
        return NodeResult(next_node="respond", outcome=GraphOutcome.ABSTAINED)

    # Very lightweight synthesis — Phase 15 full replaces this with
    # the actual LLM call + structured output.
    if state.hits:
        best = state.hits[0]
        state.reply = best.get("answer") or best.get("text", "")
    elif state.observations:
        # Stitch tool observations into a brief summary — placeholder
        parts = []
        for obs in state.observations[:3]:
            expl = obs.get("explanation") or obs.get("message") or str(obs)[:200]
            parts.append(expl)
        state.reply = "\n".join(parts)

    return NodeResult(next_node="reflect")


def node_reflect(state: AgentGraphState) -> NodeResult:
    """Grounding check + one-shot Reflexion loop.

    Simplified for Phase 15 Lite — Phase 15 full adds LLM-based
    self-critique and regenerate-with-hint logic.
    """
    from ...retriever import HybridRetriever

    if state.hits and state.reply:
        contexts = [h.get("text") or h.get("answer", "") for h in state.hits]
        state.faithfulness = HybridRetriever.compute_faithfulness(state.reply, contexts)

    state.reflect_count += 1
    return NodeResult(next_node="respond")


def node_respond(state: AgentGraphState) -> NodeResult:
    """Terminal node — marks the outcome and ends the graph."""
    if state.outcome == GraphOutcome.ANSWERED and not state.reply:
        state.outcome = GraphOutcome.ABSTAINED
    return NodeResult(next_node=END, outcome=state.outcome)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def build_main_graph() -> GraphRuntime:
    """Return the compiled main agent graph.

    LangGraph migration note: every node above maps 1:1 to a
    ``StateGraph.add_node(name, callable)``; the conditional
    edges in NodeResult.next_node become
    ``graph.add_conditional_edges(...)``.
    """
    nodes: dict[str, GraphNode] = {
        "route": node_route,
        "tool_rag_select": node_tool_rag_select,
        "act": node_act,
        "retrieve": node_retrieve,
        "synthesize": node_synthesize,
        "reflect": node_reflect,
        "respond": node_respond,
    }
    return GraphRuntime(nodes=nodes, entry="route", max_steps=10)

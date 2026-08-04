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
from ...text_signals import ABSTENTION_REPLY
from ..state import AgentRoute
from .runtime import END, GraphNode, GraphRuntime, NodeResult
from .state import AgentGraphState, GraphOutcome

logger = logging.getLogger(__name__)

#: Faithfulness below this sends the RAG path back through retrieval once.
#: Matches ``CORRECTIVE_RAG_THRESHOLD_NORM`` — the same [0,1] scale, and
#: the same judgement about what "weakly grounded" means.
REFLECT_FAITHFULNESS_FLOOR = 0.50


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


#: Parameter names a free-text query can legitimately be bound to.
_QUERY_ARG_NAMES = frozenset({"query", "question", "text", "message"})


def bind_arguments(tool_name: str, state: AgentGraphState) -> dict[str, object] | None:
    """Fill a tool's required arguments from graph state, or ``None``.

    Driven by the tool's own JSON Schema rather than a hardcoded name
    list, so a new tool needs no change here.  Only two bindings are
    honest at this layer: a tool with no required parameters can be
    called as-is, and a required free-text parameter is the user's
    query.  Anything else — ``amount``, ``tax_type`` — is a value the
    graph would have to invent, so it returns ``None`` and the caller
    skips the tool.
    """
    from ...tools import ToolRegistry

    tool = ToolRegistry.get(tool_name)
    if tool is None:
        return None
    required = list((tool.schema.parameters or {}).get("required", []))
    if not required:
        return {}
    query = state.rewritten_query or state.query
    bound: dict[str, object] = {}
    for param in required:
        if param in _QUERY_ARG_NAMES and query:
            bound[param] = query
        else:
            return None
    return bound


def node_act(state: AgentGraphState) -> NodeResult:
    """Dispatch the plan's tool calls under the turn's spend budget.

    Two things this node must not do.  It must not call a tool with
    arguments it does not have — ``calculate_vat({})`` fails schema
    validation, and an error dict in ``observations`` is what
    ``node_synthesize`` would otherwise hand the user as an answer.  And
    it must not let a long plan turn into a tool storm: every dispatch
    goes through :class:`ToolCallBudget`, which caps the turn, caps any
    one tool, and serves a repeat from its memo instead of re-executing.
    """
    client = get_client()
    state.iterations += 1
    for tool_name in state.plan:
        arguments = bind_arguments(tool_name, state)
        if arguments is None:
            state.skipped_tools.append(
                {"name": tool_name, "reason": "required arguments unavailable"}
            )
            logger.info("graph: skipped %s — required arguments unavailable", tool_name)
            continue

        decision = state.budget.admit(tool_name, arguments, iteration=state.iterations)
        if not decision.should_dispatch:
            state.skipped_tools.append({"name": tool_name, "reason": decision.reason})
            if decision.result is not None:
                state.observations.append(decision.result)
            continue

        result = client.call_tool(
            tool_name,
            arguments,
            tenant_id=state.tenant_id,
            user_id=state.user_id,
            user_role=state.role,
            granted_purposes=state.granted_purposes,
            iteration=state.iterations,
        )
        state.budget.record(tool_name, arguments, result.result)
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
    # A failed tool call is not evidence.  Synthesising over the whole
    # observation list would put "amount: required property is missing"
    # in front of the user as if it were an answer.
    usable = [obs for obs in state.observations if isinstance(obs, dict) and obs.get("ok", True)]

    if not state.hits and not usable:
        state.reply = ABSTENTION_REPLY
        state.outcome = GraphOutcome.ABSTAINED
        return NodeResult(next_node="respond", outcome=GraphOutcome.ABSTAINED)

    # Very lightweight synthesis — Phase 15 full replaces this with
    # the actual LLM call + structured output.
    if state.hits:
        best = state.hits[0]
        state.reply = best.get("answer") or best.get("text", "")
    elif usable:
        # Stitch tool observations into a brief summary — placeholder.
        # Only prose keys are used: a raw dict repr is not an answer, and
        # showing one is worse than abstaining.
        parts = []
        for obs in usable[:3]:
            for key in ("explanation", "summary", "message", "human_readable", "answer"):
                value = obs.get(key)
                if isinstance(value, str) and value.strip():
                    parts.append(value)
                    break
        state.reply = "\n".join(parts)

    if not state.reply:
        state.reply = ABSTENTION_REPLY
        state.outcome = GraphOutcome.ABSTAINED
        return NodeResult(next_node="respond", outcome=GraphOutcome.ABSTAINED)

    return NodeResult(next_node="reflect")


def node_reflect(state: AgentGraphState) -> NodeResult:
    """Grounding check plus a bounded re-retrieval loop.

    Computing faithfulness and then responding regardless is a metric,
    not a reflection.  A weakly-grounded reply goes back through
    retrieval once with an expanded query; ``max_reflections`` bounds
    that, so the loop cannot become the retrieval thrash it exists to
    correct.  Re-retrieval only helps the RAG path — a tool answer is
    grounded in its own computation, not in passages.
    """
    from ...retriever import HybridRetriever

    if state.hits and state.reply:
        contexts = [h.get("text") or h.get("answer", "") for h in state.hits]
        state.faithfulness = HybridRetriever.compute_faithfulness(state.reply, contexts)

    state.reflect_count += 1

    if (
        state.faithfulness is not None
        and state.faithfulness < REFLECT_FAITHFULNESS_FLOOR
        and state.reflect_count <= state.max_reflections
    ):
        from ...corrective_rag import _expand_query

        expanded = _expand_query(state.rewritten_query or state.query)
        state.reflections.append(
            f"faithfulness={state.faithfulness:.2f} below "
            f"{REFLECT_FAITHFULNESS_FLOOR:.2f}; re-retrieving with an expanded query"
        )
        if expanded != (state.rewritten_query or state.query):
            state.rewritten_query = expanded
            return NodeResult(next_node="retrieve", note="reflexion re-retrieval")

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

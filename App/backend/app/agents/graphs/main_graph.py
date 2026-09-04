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
from typing import Any

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

    has_history = bool(state.conversation_history)
    decision = _supervisor.classify(
        state.rewritten_query or state.query,
        has_conversation_history=has_history,
        locale=state.locale,
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
        role_map = {
            AgentRoute.TOOLS: "tool_specialist",
            AgentRoute.TAX_SPECIALIST: "tax_specialist",
            AgentRoute.CUSTOMS_SPECIALIST: "customs_specialist",
        }
        state.agent_role = role_map.get(decision.route, "graph_agent")
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

    from ...flags import flags

    if not flags.is_enabled("tool_rag"):
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
    """Hybrid retrieval with the same plan/corrective gates as REST."""
    # Deferred import to avoid the heavy retriever at module load
    from ...corrective_rag import corrective_retrieve
    from ...retriever import HybridRetriever

    retriever = HybridRetriever()
    query = state.rewritten_query or state.query
    if retriever.initialize():
        search = getattr(retriever, "search_planned", retriever.search)
        subject = state.user_id or None
        try:
            hits = search(query, top_k=state.top_k, locale=state.locale, subject=subject)
        except TypeError:
            try:
                hits = search(query, top_k=state.top_k, locale=state.locale)
            except TypeError:
                hits = search(query, top_k=state.top_k)
        if hits:
            hits, was_corrected = corrective_retrieve(
                query, retriever, hits, top_k=state.top_k, subject=subject
            )
            hits = _fuse_graph_leg(query, hits)
            hits = _apply_faq_gates(query, hits)
            state.hits = hits
            state.retrieval_mode = "hybrid_corrected" if was_corrected else "hybrid"
            state.sources = list({h.get("source", "") for h in hits if h.get("source")})
            state.citations = HybridRetriever.build_citations(hits)
    return NodeResult(next_node="synthesize")


def _apply_faq_gates(query: str, hits: list) -> list:
    """Same unbound-FAQ filter + exact-FAQ promote REST uses after blend."""
    try:
        from ...service import _filter_unbound_faq_hits, _promote_equivalent_faq_hits
    except Exception:
        logger.debug("graph: FAQ gates unavailable", exc_info=True)
        return hits
    hits = _filter_unbound_faq_hits(query, hits)
    return _promote_equivalent_faq_hits(query, hits)


def _fuse_graph_leg(query: str, hits: list) -> list:
    """Same rank-level RRF fuse REST uses; no-op when flags are off."""
    from ...flags import flags
    from ...retriever import rrf_fuse_ranked_lists

    if not (flags.is_enabled("graph_fusion") and flags.is_enabled("tax_graph")):
        return hits
    try:
        from ...graph.shadow import graph_hit_for

        hit = graph_hit_for(query)
    except Exception:
        logger.debug("graph: fusion skipped", exc_info=True)
        return hits
    if not hit:
        return hits
    return rrf_fuse_ranked_lists(hits, [hit])


#: Parameter names a free-text query can legitimately be bound to.
_QUERY_ARG_NAMES = frozenset({"query", "question", "text", "message"})


def bind_arguments(tool_name: str, state: AgentGraphState) -> dict[str, object] | None:
    """Fill a tool's required arguments from graph state, or ``None``.

    Driven by structured parameter extraction and the tool's JSON Schema.
    A tool with no required parameters is called as-is (e.g. get_current_date).
    A tool with free-text parameter (e.g. search_ura_knowledge_base) takes the query.
    Calculation and rate tools extract parameters via deterministic parsing.
    If required arguments cannot be extracted from the query, returns None to
    skip unfillable tools.
    """
    from ...tools import ToolRegistry

    tool = ToolRegistry.get(tool_name)
    if tool is None:
        return None
    required = list((tool.schema.parameters or {}).get("required", []))
    if not required:
        return {}
    query = state.rewritten_query or state.query

    # 1. Free-text query parameter binding (e.g. search_ura_knowledge_base)
    if all(param in _QUERY_ARG_NAMES for param in required) and query:
        return {param: query for param in required}

    # 2. Structured calculation parameter extraction
    try:
        from ...calculator_router import plan_calculation

        calc_plan = plan_calculation(query)
        if (calc_plan is None or calc_plan.missing) and state.query and state.query != query:
            raw_plan = plan_calculation(state.query)
            if raw_plan and not raw_plan.missing:
                calc_plan = raw_plan
        if calc_plan and calc_plan.tool == tool_name:
            if not calc_plan.missing and all(param in calc_plan.params for param in required):
                return dict(calc_plan.params)
    except Exception:
        logger.debug("graph: plan_calculation binding failed for %s", tool_name, exc_info=True)

    # 3. Structured rate lookup parameter extraction
    if tool_name == "lookup_rate":
        try:
            from ...calculator_router import plan_rate_lookup

            rate_plan = plan_rate_lookup(query)
            if (rate_plan is None or not rate_plan.tax_type) and state.query and state.query != query:
                raw_rate = plan_rate_lookup(state.query)
                if raw_rate and raw_rate.tax_type:
                    rate_plan = raw_rate
            if rate_plan and rate_plan.tax_type:
                return {"tax_type": rate_plan.tax_type}
        except Exception:
            logger.debug("graph: plan_rate_lookup binding failed", exc_info=True)

    # 4. Authenticated taxpayer account parameter binding
    if set(required) == {"taxpayer_id"} and state.user_id:
        return {"taxpayer_id": state.user_id}

    return None



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

    return NodeResult(next_node="observe")


def _usable_observations(state: AgentGraphState) -> list:
    return [
        obs
        for obs in state.observations
        if isinstance(obs, dict) and obs.get("ok", True)
    ]


def node_observe(state: AgentGraphState) -> NodeResult:
    """ReAct observe: keep, retrieve once, or synthesise an empty plan.

    Industry 2026 practice caps this at one hop (``max_handoffs``). Failed
    or unfillable tools are not evidence — they hand off to retrieve.
    """
    if _usable_observations(state) or state.hits:
        return NodeResult(next_node="synthesize")
    if state.handoff_count < state.max_handoffs:
        state.handoff_count += 1
        state.handoff_from = "observe"
        state.handoff_reason = "tools produced no usable evidence"
        logger.info("graph: observe handoff to retrieve (%s)", state.handoff_reason)
        return NodeResult(next_node="retrieve", note="observe handoff to retrieve")
    return NodeResult(next_node="synthesize")


def _format_observation_prose(obs: dict[str, Any]) -> str:
    """Format a tool observation dict into human-readable prose."""
    for key in ("explanation", "summary", "message", "human_readable", "answer"):
        val = obs.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()

    # Rate lookup tool: {"tax_type": ..., "display_name": ..., "formatted": ..., "fiscal_year": ...}
    if "tax_type" in obs and "formatted" in obs:
        name = obs.get("display_name") or obs.get("tax_type")
        fy = f" for {obs['fiscal_year']}" if obs.get("fiscal_year") else ""
        return f"The official {name} rate{fy} is {obs['formatted']}."

    # Calendar date tool: {"today": ..., "day_of_week": ..., "fiscal_year": ...}
    if "today" in obs and "day_of_week" in obs:
        fy = f" ({obs['fiscal_year']})" if obs.get("fiscal_year") else ""
        return f"Today is {obs['day_of_week']}, {obs['today']}{fy}."

    # Deadlines tool: {"deadlines": [...]}
    if "deadlines" in obs and isinstance(obs["deadlines"], list):
        items = [
            f"- {d.get('name', 'Deadline')}: {d.get('date', '')} ({d.get('description', '')})"
            for d in obs["deadlines"][:3]
        ]
        if items:
            return "Upcoming statutory deadlines:\n" + "\n".join(items)

    return ""


def node_synthesize(state: AgentGraphState) -> NodeResult:
    """Produce the reply text from retrieved passages or tool observations."""
    usable = [obs for obs in state.observations if isinstance(obs, dict) and obs.get("ok", True)]

    if not state.hits and not usable:
        state.reply = ABSTENTION_REPLY
        state.outcome = GraphOutcome.ABSTAINED
        return NodeResult(next_node="respond", outcome=GraphOutcome.ABSTAINED)

    if state.hits:
        from ... import llm as _llm_module
        if _llm_module.is_available():
            try:
                llm_reply = _llm_module.generate(
                    query=state.rewritten_query or state.query,
                    passages=state.hits,
                    conversation_history=state.conversation_history or None,
                    locale=state.locale,
                )
                if llm_reply and llm_reply.strip():
                    state.reply = llm_reply.strip()
            except Exception:
                logger.debug("graph: LLM synthesis failed, using best hit", exc_info=True)
        if not state.reply:
            best = state.hits[0]
            state.reply = best.get("answer") or best.get("text", "")
    elif usable:
        parts = []
        for obs in usable[:3]:
            prose = _format_observation_prose(obs)
            if prose:
                parts.append(prose)
        state.reply = "\n\n".join(parts)

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

    weak_grounding = (
        state.faithfulness is not None
        and state.faithfulness < REFLECT_FAITHFULNESS_FLOOR
    )
    reasoning_miss = _is_reasoning_miss(state)
    if (
        state.hits
        and state.reflect_count <= state.max_reflections
        and (weak_grounding or reasoning_miss)
    ):
        from ...corrective_rag import _expand_query

        query = state.rewritten_query or state.query
        expanded = _expand_query(query)
        if expanded != query:
            state.rewritten_query = expanded
        why = (
            f"faithfulness={state.faithfulness:.2f}"
            if weak_grounding
            else "reply shares too few question terms"
        )
        state.reflections.append(f"{why}; re-retrieving once")
        return NodeResult(next_node="retrieve", note="reflexion re-retrieval")

    return NodeResult(next_node="respond")


def _is_reasoning_miss(state: AgentGraphState) -> bool:
    """True when the reply ignores the question's content terms (G21)."""
    from ...text_signals import content_tokens

    asked = content_tokens(state.rewritten_query or state.query)
    answered = content_tokens(state.reply)
    if not asked or not answered:
        return False
    return (len(asked & answered) / len(asked)) < 0.20


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
        "observe": node_observe,
        "retrieve": node_retrieve,
        "synthesize": node_synthesize,
        "reflect": node_reflect,
        "respond": node_respond,
    }
    return GraphRuntime(nodes=nodes, entry="route", max_steps=12)

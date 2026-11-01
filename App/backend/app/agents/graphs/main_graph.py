"""Concrete nodes + factory for the main agent graph.

Structure:

    entry (route)
      ├─ rag_path   → retrieve → synth → reflect → respond
      ├─ tools_path → plan → act → observe → (reflect) → respond
      ├─ clarify    → respond (early return)
      └─ escalate   → respond (early return + ticket)

Nodes mutate a request-scoped ``AgentGraphState``. Retrieval and synthesis
are bounded; ``node_act`` may invoke external tools and is therefore a side
effect boundary. The graph drafts an answer and the service checks it: the reply
goes through ``_apply_output_guards`` like any other. This runtime does not replay
nodes. A future durable LangGraph migration must make those tool effects
idempotent before enabling retries or resume.
"""

from __future__ import annotations

import logging
from typing import Any

from ...mcp import get_client
from ...mcp.tool_rag import ToolRAGSelector
from ...text_signals import ABSTENTION_REPLY, CLARIFICATION_PROMPT, NO_HITS_REPLY
from ..state import AgentRoute
from .compactor import compact_tool_observation, node_context_compactor
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
    from ...flags import flags
    from ..supervisor import supervisor as _supervisor

    if not flags.is_enabled("agentic_mode"):
        return NodeResult(next_node="retrieve")

    has_history = bool(state.conversation_history)
    if flags.is_enabled("multi_agent_dag", subject=state.user_id or None):
        decision = _supervisor.plan_multi_agent_dag(
            state.rewritten_query or state.query,
            has_conversation_history=has_history,
            locale=state.locale,
            subject=state.user_id or None,
        )
    else:
        decision = _supervisor.classify(
            state.rewritten_query or state.query,
            has_conversation_history=has_history,
            locale=state.locale,
        )
    if getattr(decision, "requires_multi_agent", False) and getattr(decision, "sub_routes", ()):
        state.sub_routes = [r.value for r in decision.sub_routes]

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
        role_map = {
            AgentRoute.TOOLS: "tool_specialist",
            AgentRoute.TAX_SPECIALIST: "tax_specialist",
            AgentRoute.CUSTOMS_SPECIALIST: "customs_specialist",
        }
        state.agent_role = role_map.get(decision.route, "graph_agent")
        state.plan_reason = decision.reason
        # The graph flag selects orchestration; it must not implicitly turn on
        # registered-tool execution. Keep the independent side-effect gate.
        if not flags.is_enabled("tool_use"):
            return NodeResult(next_node="retrieve")
        # The supervisor already picked a whitelist — seed the plan
        state.plan = list(decision.suggested_tools)
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
        return NodeResult(next_node="human_gate")

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
    return NodeResult(next_node="human_gate")


def node_human_gate(state: AgentGraphState) -> NodeResult:
    """Pause when ``pending_action`` needs the taxpayer's consent.

    Nothing sets ``pending_action`` in production. Confirmation is enforced in the
    MCP client, which answers a ``requires_confirmation`` tool with a proposal
    until it is told ``confirmed``, on every path including this one.
    """
    if state.pending_action and state.pending_action.requires_consent and not state.pending_action.authorized:
        state.outcome = GraphOutcome.WAITING_USER
        state.reply = (
            f"Action requires taxpayer authorization: {state.pending_action.description}. "
            "Please confirm to proceed."
        )
        return NodeResult(next_node="act", outcome=GraphOutcome.WAITING_USER, note="awaiting taxpayer approval")
    return NodeResult(next_node="act")


def node_retrieve(state: AgentGraphState) -> NodeResult:
    """Hybrid retrieval with the same plan/corrective gates as REST."""
    # Deferred import to avoid the heavy retriever at module load
    from ...corrective_rag import corrective_retrieve
    from ...model_welfare import build_welfare_grounding_hits, match_welfare_categories
    from ...retriever import HybridRetriever, get_shared_retriever

    query = state.rewritten_query or state.query
    welfare_cats = match_welfare_categories(query)
    if welfare_cats:
        hits = build_welfare_grounding_hits(query, welfare_cats)
        state.hits = hits
        state.retrieval_mode = "model_welfare"
        state.agent_role = "conversational_agent"
        state.sources = list(dict.fromkeys(h.get("source", "") for h in hits if h.get("source")))
        state.citations = HybridRetriever.build_citations(hits)
        return NodeResult(next_node="synthesize")

    retriever = get_shared_retriever(auto_init=False)
    hits: list[dict[str, Any]] = []
    was_corrected = False
    if getattr(retriever, "is_ready", False) or retriever.initialize():
        search = getattr(retriever, "search_planned", retriever.search)
        subject = state.user_id or None
        user_hist = [
            str(m.get("user_message") or m.get("content") or "").strip()
            for m in (state.conversation_history or [])
            if isinstance(m, dict) and (m.get("user_message") or m.get("role") == "user")
        ]
        user_hist = [h for h in user_hist if h]
        try:
            hits = search(
                query,
                top_k=state.top_k,
                locale=state.locale,
                subject=subject,
                history=user_hist or None,
            )
        except TypeError:
            try:
                hits = search(query, top_k=state.top_k, locale=state.locale, subject=subject)
            except TypeError:
                try:
                    hits = search(query, top_k=state.top_k, locale=state.locale)
                except TypeError:
                    hits = search(query, top_k=state.top_k)
        if hits:
            hits, was_corrected = corrective_retrieve(
                query, retriever, hits, top_k=state.top_k, subject=subject, locale=state.locale
            )

    # The statutory leg can answer a rate query even when dense/keyword
    # retrieval is down or empty, so always consult it while its flags are on.
    hits = _fuse_graph_leg(query, hits)
    graph_fused = any(hit.get("doc_type") == "graph" for hit in hits)
    hits, live_mode = _apply_live_research(state, query, hits, retriever)
    if hits:
        hits = _apply_faq_gates(query, hits)
        state.hits = hits
        state.retrieval_mode = live_mode or (
            "graph_corrected" if graph_fused and was_corrected
            else "graph" if graph_fused
            else "hybrid_corrected" if was_corrected
            else "hybrid"
        )
        state.sources = list(dict.fromkeys(h.get("source", "") for h in hits if h.get("source")))
        state.citations = HybridRetriever.build_citations(hits)
    return NodeResult(next_node="synthesize")


def _apply_live_research(
    state: AgentGraphState,
    query: str,
    hits: list,
    retriever: Any,
) -> tuple[list, str]:
    """The research step REST and SSE run, once per turn on this path too.

    The graph answered without it, so turning ``langgraph`` on quietly removed
    the live check and its disclosure. Reflection re-enters retrieval, hence the
    once-per-turn guard: a second pass would pay for the same searches. Its
    evidence is kept and merged back, or the retry would drop what the notice
    and citations still describe.
    """
    from ...flags import flags
    from ...web_research import merge_web_hits

    if state.live_checked:
        return merge_web_hits(state.live_web_hits, hits), state.live_mode
    if not flags.is_enabled("live_web_research", subject=state.user_id or None):
        return hits, ""
    from ...service import _live_research_for_turn

    state.live_checked = True
    merged, mode, outcome = _live_research_for_turn(
        query,
        hits=hits,
        retriever=retriever if getattr(retriever, "is_ready", False) else None,
        turn_id=state.current_goal_id or "graph",
    )
    state.live_check_notice = outcome.live_check_notice
    state.live_check_disclosure = outcome.disclosure
    state.live_web_hits = list(outcome.web_hits)
    state.live_mode = mode
    return merged, mode


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

    # 4. Heuristic parameter recovery for calculation tools
    if tool_name in (
        "calculate_vat",
        "calculate_paye",
        "calculate_withholding_tax",
        "calculate_rental_tax",
        "calculate_presumptive_tax",
    ):
        try:
            from ...calculator_router import extract_amounts

            # Only an unambiguous figure binds. The first number in the question
            # was once taken, so "In 2026, VAT on 500,000" computed VAT on 2026.
            amounts = extract_amounts(query)
            if len(amounts) == 1:
                parsed = amounts[0][0]
                if parsed > 0:
                    params: dict[str, Any] = {}
                    for p in required:
                        if p in ("amount", "net_amount", "gross_amount", "value", "payment"):
                            params[p] = parsed
                        elif p in ("monthly_salary", "salary", "gross_salary"):
                            params[p] = parsed
                        elif p in ("gross_turnover", "turnover", "annual_turnover"):
                            params[p] = parsed
                        elif p in ("gross_rent", "rent", "rental_income"):
                            params[p] = parsed
                    if params and all(p in params for p in required):
                        return params
        except Exception:
            logger.debug("graph: heuristic parameter extraction failed for %s", tool_name, exc_info=True)

    # 5. Authenticated taxpayer account parameter binding
    if set(required) == {"taxpayer_id"} and state.user_id:
        return {"taxpayer_id": state.user_id}

    # 6. Memory-backed & DAG-receipt argument binding (Phase 35)
    try:
        from ...flags import flags

        uid = state.user_id or None
        if flags.is_enabled("autonomous_outcome_verification", subject=uid) or flags.is_enabled(
            "zero_effort_slot_prefill", subject=uid
        ):
            bound = _bind_from_memory_or_dag(tool_name, required, state)
            if bound is not None:
                return bound
    except Exception:
        logger.debug("graph: memory/DAG parameter binding failed for %s", tool_name, exc_info=True)

    return None


#: Keys that mean the same thing as a slot, and nothing else. Tax turns on the
#: difference: chargeable income is not turnover, a landed cost already contains
#: VAT, and a rent may be monthly or annual. A loose alias here computes a confident
#: figure on the wrong base, so a slot with no exact match stays unfilled and the
#: taxpayer is asked.
_SLOT_CANDIDATE_KEYS: dict[str, tuple[str, ...]] = {
    "amount": ("amount", "taxable_amount", "invoice_amount", "payment_amount"),
    "net_amount": ("net_amount", "net", "taxable_amount"),
    "gross_amount": ("gross_amount", "gross"),
    "value": ("value",),
    "payment": ("payment", "payment_amount"),
    "monthly_gross": ("monthly_gross", "monthly_salary", "gross_salary", "salary"),
    "monthly_salary": ("monthly_salary", "gross_salary", "salary", "monthly_gross"),
    "salary": ("salary", "monthly_salary", "gross_salary", "monthly_gross"),
    "gross_salary": ("gross_salary", "monthly_salary", "salary", "monthly_gross"),
    "gross_turnover": ("gross_turnover", "annual_turnover", "turnover"),
    "turnover": ("turnover", "annual_turnover", "gross_turnover"),
    "annual_turnover": ("annual_turnover", "gross_turnover", "turnover"),
    "annual_gross_rent": ("annual_gross_rent",),
    "gross_rent": ("gross_rent",),
    "rent": ("rent",),
    "rental_income": ("rental_income",),
    "chargeable_income": ("chargeable_income", "taxable_income"),
    "cif_value": ("cif_value", "cif", "customs_value"),
    "taxpayer_id": ("tin", "taxpayer_id"),
    "tin": ("tin", "taxpayer_id"),
}

#: Slots that decide *whose* record a tool reads. These never come from a tool's
#: output: that would let one tool's result choose the subject of the next call,
#: and connector results are not trusted input.
_IDENTITY_SLOTS = frozenset({"taxpayer_id", "tin", "nin", "brn"})


def _bind_from_memory_or_dag(
    tool_name: str,
    required: list[str],
    state: AgentGraphState,
) -> dict[str, object] | None:
    """Resolve missing tool parameters from prior DAG observations, verified_slot_bank, or SemanticMemory."""
    params: dict[str, object] = {}
    bound_source = ""

    # A. Prior specialist observations in the same DAG turn
    for obs in reversed(state.observations):
        if not isinstance(obs, dict) or not obs.get("ok", True):
            continue
        for req in required:
            if req in params or req in _IDENTITY_SLOTS:
                continue
            for cand in _SLOT_CANDIDATE_KEYS.get(req, (req,)):
                val = obs.get(cand)
                if val not in (None, "") and isinstance(val, (int, float, str)):
                    params[req] = val
                    bound_source = bound_source or "dag_receipt"
                    break

    # B. Consented verified_slot_bank & SemanticMemory
    if state.user_id and not all(p in params for p in required):
        from ...memory.service import get_memory_service

        memsvc = get_memory_service()
        slot_bank = memsvc.resolution_ledger.get_verified_slots(
            state.user_id, tenant_id=state.tenant_id or "default"
        )
        for req in required:
            if req in params:
                continue
            for cand in _SLOT_CANDIDATE_KEYS.get(req, (req,)):
                val = slot_bank.get(cand)
                if val not in (None, ""):
                    params[req] = val
                    bound_source = bound_source or "verified_slot_bank"
                    break

        if not all(p in params for p in required):
            facts = memsvc.read_facts(state.user_id, tenant_id=state.tenant_id or "default")
            fact_map = {str(f.predicate or f.category).lower(): f.object_value for f in facts}
            for req in required:
                if req in params:
                    continue
                for cand in _SLOT_CANDIDATE_KEYS.get(req, (req,)):
                    val = fact_map.get(cand.lower())
                    if val not in (None, ""):
                        try:
                            from ...tax.money import parse_money_amount

                            parsed = parse_money_amount(str(val))
                            params[req] = parsed if parsed is not None and parsed > 0 else val
                        except Exception:
                            params[req] = val
                        bound_source = bound_source or "semantic_memory"
                        break

    if params and all(p in params for p in required):
        if bound_source:
            assumed = {"tool": tool_name, "inputs": sorted(params), "source": bound_source}
            if assumed not in state.assumed_inputs:
                state.assumed_inputs.append(assumed)
            try:
                from ...analytics import metrics

                metrics.inc(
                    "slot_repetition_prevented_total",
                    labels={"source": bound_source},
                )
            except Exception:
                logger.debug("slot_repetition_prevented_total metric skipped", exc_info=True)
        return params
    return None



def _is_parallel_safe_tool(tool_name: str) -> bool:
    """True when *tool_name* is declared both read_only and idempotent (MO-3)."""
    from ...tools import ToolRegistry

    tool = ToolRegistry.get(tool_name)
    if tool is None:
        return False
    return bool(getattr(tool.schema, "read_only", False) and getattr(tool.schema, "idempotent", False))


def node_act(state: AgentGraphState) -> NodeResult:
    """Dispatch the plan's tool calls under the turn's spend budget.

    Two things this node must not do.  It must not call a tool with
    arguments it does not have — ``calculate_vat({})`` fails schema
    validation, and an error dict in ``observations`` is what
    ``node_synthesize`` would otherwise hand the user as an answer.  And
    it must not let a long plan turn into a tool storm: every dispatch
    goes through :class:`ToolCallBudget`, which caps the turn, caps any
    one tool, and serves a repeat from its memo instead of re-executing.

    When multiple admitted tools in ``state.plan`` are all ``read_only`` and
    ``idempotent``, they execute concurrently via ``ThreadPoolExecutor`` while
    preserving deterministic plan order (MO-3 / Kim et al. 2026).
    """
    from concurrent.futures import ThreadPoolExecutor

    from .state import TaxpayerActionRequest

    client = get_client()
    state.iterations += 1
    admitted_calls: list[tuple[str, dict[str, object]]] = []
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

        admitted_calls.append((tool_name, dict(arguments)))

    def _invoke(item: tuple[str, dict[str, object]]) -> Any:
        t_name, t_args = item
        return client.call_tool(
            t_name,
            t_args,
            tenant_id=state.tenant_id,
            user_id=state.user_id,
            user_role=state.role,
            granted_purposes=state.granted_purposes,
            iteration=state.iterations,
        )

    if len(admitted_calls) >= 2 and all(_is_parallel_safe_tool(t) for t, _ in admitted_calls):
        import contextvars

        contexts = [contextvars.copy_context() for _ in admitted_calls]
        with ThreadPoolExecutor(max_workers=min(len(admitted_calls), 4)) as pool:
            results = list(
                pool.map(
                    lambda pair: pair[0].run(_invoke, pair[1]),
                    zip(contexts, admitted_calls, strict=True),
                )
            )
    else:
        results = [_invoke(item) for item in admitted_calls]

    for (tool_name, arguments), result in zip(admitted_calls, results, strict=True):
        state.budget.record(tool_name, arguments, result.result)
        verified_ok = bool(
            result.ok and isinstance(result.result, dict) and result.result.get("ok", True)
        )
        state.tool_calls.append(
            {
                "call_id": result.call_id,
                "name": tool_name,
                "ok": result.ok,
                "verified_outcome": verified_ok,
                "bound_arguments": dict(arguments),
                "duration_ms": result.duration_ms,
                "result": result.result,
            }
        )
        state.observations.append(result.result)
        if isinstance(result.result, dict) and (
            result.result.get("requires_confirmation") or result.result.get("input_required")
        ):
            desc = str(
                result.result.get("message")
                or result.result.get("description")
                or f"Confirm execution of {tool_name}"
            )
            state.pending_action = TaxpayerActionRequest(
                action_type=tool_name,
                description=desc,
                payload=dict(arguments),
                requires_consent=True,
                authorized=False,
            )
            state.outcome = GraphOutcome.WAITING_USER
            state.reply = (
                f"Action requires taxpayer authorization: {desc}. Please confirm to proceed."
                if not result.result.get("message")
                else str(result.result["message"])
            )
            return NodeResult(
                next_node="respond",
                outcome=GraphOutcome.WAITING_USER,
                note="awaiting taxpayer confirmation",
            )

    return NodeResult(next_node="observe")


def _usable_observations(state: AgentGraphState) -> list:
    return [
        obs
        for obs in state.observations
        if isinstance(obs, dict) and obs.get("ok", True)
    ]


_SPECIALIST_DEFAULT_TOOLS: dict[str, tuple[str, ...]] = {
    "customs_specialist": ("calculate_customs_duty", "lookup_rate"),
    "tax_specialist": (
        "calculate_vat",
        "calculate_paye",
        "calculate_withholding_tax",
        "calculate_rental_tax",
        "lookup_rate",
    ),
    "tools": ("lookup_rate", "get_filing_deadlines", "get_current_date"),
}


def _build_aligned_tool_receipts(state: AgentGraphState) -> list[Any]:
    """Build ToolExecutionReceipt list aligned strictly with each successful tool_call."""
    from ...memory.handoff import ToolExecutionReceipt

    receipts: list[ToolExecutionReceipt] = []
    for idx, tc in enumerate(state.tool_calls):
        if not isinstance(tc, dict) or not tc.get("ok"):
            continue
        obs = tc.get("result")
        if obs is None and idx < len(state.observations):
            obs = state.observations[idx]
        if isinstance(obs, dict) and obs.get("ok", True):
            receipts.append(
                ToolExecutionReceipt(
                    tool_name=str(tc.get("name", "tool")),
                    inputs_summary=state.rewritten_query or state.query,
                    deterministic_output=obs,
                )
            )
    return receipts


def node_observe(state: AgentGraphState) -> NodeResult:
    """ReAct observe: keep, retrieve once, or synthesise an empty plan.

    Industry 2026 practice caps this at one hop (``max_handoffs``). Failed
    or unfillable tools are not evidence — they hand off to retrieve.
    When ``multi_agent_dag`` is active and multiple specialist sub_routes
    were planned, records a structured inter-specialist ``HandoffEnvelope``,
    advances the route cursor, and dispatches the next specialist's tools
    (or statutory retrieval) before final synthesis.
    """
    usable = _usable_observations(state)
    from ...flags import flags

    if (
        len(state.sub_routes) >= 2
        and not state.hits
        and state.handoff_count < max(2, state.max_handoffs + 1)
        and flags.is_enabled("multi_agent_dag", subject=state.user_id or None)
    ):
        state.handoff_count += 1
        state.handoff_from = state.sub_routes[0]
        target_spec = state.sub_routes[1]
        state.sub_routes = list(state.sub_routes[1:])
        state.agent_role = target_spec
        state.handoff_reason = f"multi_agent_dag_chain:{state.handoff_from}->{target_spec}"
        # Isolate downstream specialist state from failed upstream observations (MO-3).
        state.observations = list(usable)
        try:
            from ...memory.handoff import HandoffEnvelope, HandoffGoal

            receipts = _build_aligned_tool_receipts(state)
            env = HandoffEnvelope(
                thread_id=state.current_goal_id or "dag-turn",
                tenant_id=state.tenant_id,
                source_agent=state.handoff_from,
                target_channel=target_spec,
                goal=HandoffGoal(
                    primary_objective=state.rewritten_query or state.query,
                    target_resolution=f"Combine {state.handoff_from} tool outputs with {target_spec} statutory rules",
                ),
                taxpayer_locale=state.locale,
                tool_receipts=receipts,
            )
            state.specialist_handoffs.append(env.to_dict())
        except Exception:
            logger.debug("graph: specialist handoff envelope creation skipped", exc_info=True)

        # Dispatch target_spec's bindable specialist tools before retrieval when available
        if flags.is_enabled("tool_use") and state.iterations < state.max_iterations:
            already_called = {str(tc.get("name", "")) for tc in state.tool_calls if isinstance(tc, dict)}
            already_skipped = {str(sk.get("name", "")) for sk in state.skipped_tools if isinstance(sk, dict)}
            candidate_tools = _SPECIALIST_DEFAULT_TOOLS.get(target_spec, ())
            next_tools = [
                t
                for t in candidate_tools
                if t not in already_called
                and t not in already_skipped
                and bind_arguments(t, state) is not None
            ]
            if next_tools:
                state.plan = next_tools
                return NodeResult(
                    next_node="tool_rag_select",
                    note=f"multi_agent_dag dispatch to {target_spec}",
                )

        return NodeResult(next_node="retrieve", note=f"multi_agent_dag specialist handoff to {target_spec} retrieve")

    if usable or state.hits:
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
        from ...query import english_retrieval_query

        if _llm_module.is_available():
            try:
                raw_q = state.rewritten_query or state.query
                synth_query = (
                    english_retrieval_query(raw_q, state.locale)
                    if (
                        state.locale not in ("", "en")
                        and not _llm_module.can_generate_in_locale(state.locale)
                    )
                    else raw_q
                )
                llm_reply = _llm_module.generate(
                    query=synth_query or raw_q,
                    passages=state.hits,
                    conversation_history=state.conversation_history or None,
                    locale=state.locale,
                    personalization_context=state.live_check_notice,
                )
                if llm_reply and llm_reply.strip():
                    from ...service import _endpoint_model, _note_generation

                    state.reply = llm_reply.strip()
                    # No endpoint was passed above, so this is the configured model.
                    _note_generation(backend="local", model=_endpoint_model(None))
                    if state.locale in ("", "en") or not _llm_module.can_generate_in_locale(state.locale):
                        from ...service import _answer_in_english

                        state.reply = _answer_in_english(state.reply)
            except Exception:
                logger.warning("graph: LLM synthesis failed, using best hit", exc_info=True)
        if not state.reply:
            from ...service import _note_passage_served

            _note_passage_served("generation_unavailable")
            best = state.hits[0]
            state.reply = best.get("answer") or best.get("text", "")
        if usable and state.specialist_handoffs:
            tool_parts = [p for p in (_format_observation_prose(obs) for obs in usable[:2]) if p]
            if tool_parts:
                state.reply = "\n\n".join([*tool_parts, state.reply]).strip()
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
        from ...corrective_rag import build_reflection_query

        query = state.rewritten_query or state.query
        expanded = build_reflection_query(
            query,
            draft_reply=state.reply,
            hits=state.hits,
            locale=state.locale,
        )
        if expanded != query:
            state.rewritten_query = expanded
        why = (
            f"faithfulness={state.faithfulness:.2f}"
            if weak_grounding
            else "reply shares too few question terms"
        )
        state.reflections.append(f"{why}; re-retrieving once")
        return NodeResult(next_node="retrieve", note="reflexion re-retrieval")

    if state.hits and weak_grounding:
        # Grounding safety net: reconcile against top verified passage text
        best = state.hits[0]
        verified_text = best.get("answer") or best.get("text", "")
        if verified_text:
            from ...service import _note_passage_served

            state.reply = verified_text.strip()
            state.reflections.append("grounded revision: aligned synthesis with verified statutory passage")
            state.faithfulness = 1.0
            _note_passage_served("draft_unsupported")

    return NodeResult(next_node="respond")


def _is_reasoning_miss(state: AgentGraphState) -> bool:
    """True when the reply ignores the question's content terms (G21)."""
    from ... import mt
    from ...receptionist.lexicon import normalize_codeswitched_query
    from ...text_signals import content_tokens

    raw_q = (state.rewritten_query or state.query or "").strip()
    q_for_tokens = raw_q
    loc = (state.locale or "").strip().lower()
    if loc not in ("", "en") and raw_q:
        cached = mt.cache.get(loc, "en", raw_q)
        q_for_tokens = cached if cached else normalize_codeswitched_query(raw_q, language=loc)
    asked = content_tokens(q_for_tokens or raw_q)
    answered = content_tokens(state.reply)
    if not asked or not answered:
        return False
    return (len(asked & answered) / len(asked)) < 0.20


def assumed_inputs_notice(state: AgentGraphState) -> str:
    """The sentence owed when a figure rests on something the taxpayer did not say this turn.

    Names the inputs, never their values: the taxpayer can correct "monthly salary"
    without the reply repeating it.
    """
    names = sorted({name.replace("_", " ") for item in state.assumed_inputs for name in item.get("inputs", ())})
    if not names:
        return ""
    listed = ", ".join(names)
    return f"This uses the {listed} from earlier in your case. Please tell me if that has changed."


def node_respond(state: AgentGraphState) -> NodeResult:
    """Ensure every controlled terminal route has a safe, non-empty reply."""
    if not (state.reply and state.reply.strip()):
        _fill_terminal_reply(state)
    notice = assumed_inputs_notice(state)
    if notice and state.outcome == GraphOutcome.ANSWERED and notice not in state.reply:
        state.reply = f"{state.reply.rstrip()}\n\n{notice}"
    if state.live_check_disclosure and state.outcome in (GraphOutcome.ANSWERED, GraphOutcome.ABSTAINED):
        from ...service import _with_live_check_disclosure

        state.reply = _with_live_check_disclosure(state.reply, state.live_check_disclosure)
    return NodeResult(next_node=END, outcome=state.outcome)


def _fill_terminal_reply(state: AgentGraphState) -> None:
    """The safe reply for a route that reached the end without one."""
    if state.outcome == GraphOutcome.CLARIFY:
        state.reply = state.clarification_question.strip() or CLARIFICATION_PROMPT
    elif state.outcome == GraphOutcome.ESCALATED:
        # Do not imply that a human handoff happened. The service boundary
        # may create a ticket, subject to the ticket_queue feature flag.
        state.reply = NO_HITS_REPLY
    elif state.outcome == GraphOutcome.ANSWERED:
        state.outcome = GraphOutcome.ABSTAINED
        state.reply = ABSTENTION_REPLY
    else:
        state.reply = ABSTENTION_REPLY


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def build_main_graph(checkpointer: Any = None) -> GraphRuntime:
    """Construct the bounded main agent graph with optional durable checkpointer."""
    from .runtime import DurableCheckpointStore

    actual_checkpointer = DurableCheckpointStore() if checkpointer is None else checkpointer
    nodes: dict[str, GraphNode] = {
        "compactor": node_context_compactor,
        "route": node_route,
        "tool_rag_select": node_tool_rag_select,
        "human_gate": node_human_gate,
        "act": node_act,
        "observe": node_observe,
        "retrieve": node_retrieve,
        "synthesize": node_synthesize,
        "reflect": node_reflect,
        "respond": node_respond,
    }
    return GraphRuntime(nodes=nodes, entry="compactor", max_steps=14, checkpointer=actual_checkpointer)

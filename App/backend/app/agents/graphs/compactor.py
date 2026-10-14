"""Context Compaction Middleware for LangGraph Agent Execution.

Implements token-bounded hierarchical compression, pruning tool schema bloat
and compressing conversation histories beyond the verbatim window.
"""
from __future__ import annotations

import logging
from typing import Any

from .runtime import NodeResult
from .state import AgentGraphState

logger = logging.getLogger(__name__)

MAX_VERBATIM_TURNS = 4
MAX_OBSERVATION_TOKENS = 512

# Statutory keys that must always be preserved during tool output compaction
_ESSENTIAL_STATUTORY_KEYS = frozenset({
    "ok",
    "error",
    "tax_type",
    "display_name",
    "formatted",
    "fiscal_year",
    "today",
    "day_of_week",
    "tax_payable",
    "payable",
    "gross",
    "net",
    "effective_rate",
    "band",
    "nssf",
    "cif",
    "duty",
    "vat",
    "landed_cost",
    "gain",
    "cost_base",
    "sale_price",
    "wht_rate",
    "assessment_id",
    "status",
    "explanation",
    "summary",
    "message",
    "human_readable",
    "answer",
})


def compact_tool_observation(obs: Any, max_tokens: int = MAX_OBSERVATION_TOKENS) -> dict[str, Any]:
    """Compress verbose tool payloads into bounded statutory fact structures."""
    if not isinstance(obs, dict):
        return {"data": str(obs)[:max_tokens * 3], "ok": True}

    if not obs.get("ok", True) or obs.get("error"):
        err_msg = str(obs.get("error", "tool failed"))[:300]
        return {"ok": False, "error": err_msg}

    compacted: dict[str, Any] = {"ok": True}

    # Extract primary statutory fields
    for key in _ESSENTIAL_STATUTORY_KEYS:
        if key in obs and obs[key] is not None:
            val = obs[key]
            if isinstance(val, (int, float, bool)):
                compacted[key] = val
            elif isinstance(val, str):
                compacted[key] = val[:400]
            elif isinstance(val, list):
                compacted[key] = val[:3]
            elif isinstance(val, dict):
                compacted[key] = {k: v for k, v in list(val.items())[:5]}

    # Retain lists and sub-dicts with bounded slices (e.g. deadlines, line items)
    for key, val in obs.items():
        if key not in compacted:
            if isinstance(val, list):
                compacted[key] = val[:3]
            elif isinstance(val, dict):
                compacted[key] = {k: v for k, v in list(val.items())[:5]}
            elif isinstance(val, (int, float, str, bool)):
                compacted[key] = val if not isinstance(val, str) else val[:200]

    return compacted


def compact_messages(
    messages: list[dict[str, Any]],
    max_verbatim_turns: int = MAX_VERBATIM_TURNS,
) -> tuple[list[dict[str, Any]], str]:
    """Split and summarize historical dialogue exceeding the verbatim window."""
    if not messages:
        return [], ""

    turn_count = len(messages)
    max_messages = max_verbatim_turns * 2

    if turn_count <= max_messages:
        return list(messages), ""

    older = messages[:-max_messages]
    recent = list(messages[-max_messages:])

    summary_chunks: list[str] = []
    for msg in older:
        role = str(msg.get("role", "")).lower()
        content = str(msg.get("content") or msg.get("user_message") or msg.get("bot_reply") or "").strip()
        if not content:
            continue
        if role in ("user", "") and "user_message" in msg:
            summary_chunks.append(f"Taxpayer: {content[:120]}")
        elif role in ("assistant", "") and "bot_reply" in msg:
            summary_chunks.append(f"Assistant: {content[:120]}")
        elif role == "user":
            summary_chunks.append(f"Taxpayer: {content[:120]}")
        elif role in ("assistant", "bot", "model"):
            summary_chunks.append(f"Assistant: {content[:120]}")

    return recent, " | ".join(summary_chunks)


def analyze_context_window(state: AgentGraphState) -> dict[str, Any]:
    """Analyze token footprint, context utilization, and attention rot risk."""
    from ...llm import LLM_CONTEXT_WINDOW, _estimate_tokens, _served_context_window

    query_tokens = _estimate_tokens(state.rewritten_query or state.query)
    summary_tokens = _estimate_tokens(state.context_summary)
    history_tokens = sum(
        _estimate_tokens(str(m.get("content") or m.get("user_message") or m.get("bot_reply") or ""))
        for m in state.conversation_history
    )
    obs_tokens = sum(_estimate_tokens(str(obs)) for obs in state.observations)
    total_estimated = query_tokens + summary_tokens + history_tokens + obs_tokens

    served = _served_context_window()
    window = served if served > 0 else LLM_CONTEXT_WINDOW
    utilization_pct = round((total_estimated / max(window, 1)) * 100, 2)

    # Detect context rot / attention dilution risk
    if utilization_pct > 75.0 or obs_tokens > 2000:
        rot_risk = "high"
    elif utilization_pct > 50.0 or obs_tokens > 1000:
        rot_risk = "medium"
    else:
        rot_risk = "low"

    return {
        "estimated_tokens": total_estimated,
        "context_window": window,
        "utilization_pct": utilization_pct,
        "rot_risk": rot_risk,
        "token_breakdown": {
            "query": query_tokens,
            "summary": summary_tokens,
            "history": history_tokens,
            "observations": obs_tokens,
        },
    }


def node_context_compactor(state: AgentGraphState) -> NodeResult:
    """LangGraph node: prunes messages and observations within budget."""
    # 1. Compact observations in place
    if state.observations:
        state.observations = [compact_tool_observation(obs) for obs in state.observations]

    # 2. Compact history if exceeding verbatim limit
    if len(state.conversation_history) > (MAX_VERBATIM_TURNS * 2):
        recent, summary = compact_messages(state.conversation_history, MAX_VERBATIM_TURNS)
        state.conversation_history = recent
        if summary:
            if state.context_summary:
                state.context_summary = f"{state.context_summary} [Earlier: {summary}]"
            else:
                state.context_summary = f"[Earlier: {summary}]"

    # 3. Analyze token utilization & context rot risk
    analytics = analyze_context_window(state)
    state.record("compactor", 0.0, **analytics)

    # Default forward edge to route node
    return NodeResult(next_node="route")


def compact_prompt_context(
    query: str,
    conversation_history: list[dict[str, str]] | None,
    context_summary: str = "",
    max_verbatim_turns: int = MAX_VERBATIM_TURNS,
) -> tuple[list[dict[str, str]], str, dict[str, Any]]:
    """Compact conversation history and compute rot analytics for prompt construction."""
    history = list(conversation_history or [])
    updated_summary = context_summary
    if len(history) > max_verbatim_turns:
        recent = history[-max_verbatim_turns:]
        older = history[:-max_verbatim_turns]
        from ...context_manager import summarize_older_turns

        older_summary = summarize_older_turns(older)
        if older_summary:
            updated_summary = (
                f"{updated_summary} [Earlier: {older_summary}]".strip()
                if updated_summary
                else older_summary
            )
        history = recent

    from ...llm import LLM_CONTEXT_WINDOW, _estimate_tokens, _served_context_window

    query_tokens = _estimate_tokens(query)
    summary_tokens = _estimate_tokens(updated_summary)
    history_tokens = sum(
        _estimate_tokens(str(m.get("user_message") or m.get("content") or "") + " " + str(m.get("bot_reply") or ""))
        for m in history
    )
    total_estimated = query_tokens + summary_tokens + history_tokens
    served = _served_context_window()
    window = served if served > 0 else LLM_CONTEXT_WINDOW
    utilization_pct = round((total_estimated / max(window, 1)) * 100, 2)
    rot_risk = "high" if utilization_pct > 75.0 else ("medium" if utilization_pct > 50.0 else "low")
    analytics = {
        "estimated_tokens": total_estimated,
        "context_window": window,
        "utilization_pct": utilization_pct,
        "rot_risk": rot_risk,
    }
    return history, updated_summary, analytics

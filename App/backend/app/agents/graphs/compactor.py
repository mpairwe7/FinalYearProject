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

    # Default forward edge to route node
    return NodeResult(next_node="route")

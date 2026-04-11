"""Agentic AI layer — supervisor + specialist pattern.

This package implements the agent runtime described in
``docs/GAPS_AND_AGENTIC_ROADMAP.md`` §3:

    User query
      → Supervisor (classify + route)
          ├── calculator / rate / calendar question → tool specialist
          ├── factual URA question                  → RAG specialist
          ├── ambiguous (< 2 words, missing context) → clarify
          └── unsupported / sensitive / escalation   → human handoff

The supervisor routes via a fast rule-based classifier first (<1ms)
and only falls back to an LLM classifier when the rules are
inconclusive — this keeps the common case latency-free while still
handling edge cases gracefully.

Everything is gated behind ``FLAG_AGENTIC_MODE`` so the default
request path (Phase 1-13 RAG) is unchanged.
"""

from __future__ import annotations

from .state import AgentState, AgentRoute, RouteDecision
from .supervisor import Supervisor, supervisor

__all__ = [
    "AgentState",
    "AgentRoute",
    "RouteDecision",
    "Supervisor",
    "supervisor",
]

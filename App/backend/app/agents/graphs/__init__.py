"""LangGraph-inspired request orchestration (Phase 15 Lite).

This package implements a plan → act → observe → reflect → respond
state machine without a runtime dependency on LangGraph itself. It is a
bounded, synchronous, in-process dispatcher for one chat turn, not a drop-in
LangGraph implementation or a durable workflow engine.

Why not just depend on LangGraph directly?
- Keeps the runtime footprint small for sovereign / air-gapped
  deploys where pulling langgraph + its transitive deps is heavy.
- Keeps control flow inspectable in plain Python; only compact request
  metadata is audited by the surrounding service, not a replayable state log.
- Avoids Pydantic v1 / v2 version compat issues that some LangGraph
  releases carry.

Adopting upstream LangGraph later requires an explicit state-schema and
transition migration, production checkpointer/retention design, and
idempotency for tools before retries or resume. It is not a mechanical swap:

    # before
    runtime = GraphRuntime([plan, act, observe, reflect, respond])

    # after
    graph = StateGraph(AgentState)
    graph.add_node("plan", plan)  # same callables
    ...
    runtime = graph.compile(checkpointer=PostgresCheckpointer(pg))

Feature flag: ``FLAG_LANGGRAPH`` default false.
"""

from __future__ import annotations

from .runtime import GraphNode, GraphRuntime, NodeResult
from .state import AgentGraphState, GraphOutcome

__all__ = [
    "AgentGraphState",
    "GraphNode",
    "GraphOutcome",
    "GraphRuntime",
    "NodeResult",
]

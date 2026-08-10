"""LangGraph-style agent orchestration (Phase 15 Lite).

This package implements a plan → act → observe → reflect → respond
state machine without a runtime dependency on LangGraph itself.
The pattern is chosen to be **upgrade-compatible** — when LangGraph
is installed, each node here maps 1:1 to a LangGraph node and the
``GraphRuntime`` can be swapped for ``langgraph.Graph``.

Why not just depend on LangGraph directly?
- Keeps the runtime footprint small for sovereign / air-gapped
  deploys where pulling langgraph + its transitive deps is heavy.
- Makes the control flow inspectable in plain Python (good for
  audit replay).
- Avoids Pydantic v1 / v2 version compat issues that some LangGraph
  releases carry.

When we do install LangGraph in Phase 15 full, the migration is:

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

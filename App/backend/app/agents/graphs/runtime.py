"""Minimal state-machine runtime — LangGraph-compatible node shape.

A graph is just a list of (name, callable) pairs plus a small set
of edges.  Each node receives the current ``AgentGraphState``,
may mutate it, and returns a :class:`NodeResult` naming the next
node (or ``END`` to terminate).  This covers ReAct / Plan-and-
Execute / Reflexion without pulling in LangGraph.

When Phase 15 full lands, every node becomes a LangGraph
``@node`` and the runtime is replaced with ``StateGraph.compile()``.
The public API of a node — ``(state) -> NodeResult`` — is
intentionally the same so the migration is mechanical.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from .state import AgentGraphState, GraphOutcome

logger = logging.getLogger(__name__)

END = "__end__"

GraphNode = Callable[[AgentGraphState], "NodeResult"]


@dataclass
class NodeResult:
    """What a node returns to the runtime.

    ``next_node`` is the name of the next node to run, or
    :data:`END` to terminate the graph.  ``outcome`` is only set
    when terminating and overrides state.outcome.
    """

    next_node: str
    outcome: GraphOutcome | None = None
    note: str = ""


class GraphRuntime:
    """Dispatch loop for a list of named nodes.

    Features:
    - Bounded execution (max_steps cap as a safety net)
    - Per-node duration tracking into state.trace
    - Structured exception capture — a failing node terminates
      with ``outcome=ERRORED`` rather than unwinding
    """

    def __init__(
        self,
        nodes: dict[str, GraphNode],
        entry: str,
        max_steps: int = 12,
    ) -> None:
        if entry not in nodes:
            raise ValueError(f"entry node {entry!r} not in {sorted(nodes)}")
        self._nodes = nodes
        self._entry = entry
        self._max_steps = max_steps

    def run(self, state: AgentGraphState) -> AgentGraphState:
        current = self._entry
        steps = 0
        while current != END:
            if steps >= self._max_steps:
                logger.warning("GraphRuntime: max_steps=%d reached", self._max_steps)
                state.outcome = GraphOutcome.TRUNCATED
                break

            node = self._nodes.get(current)
            if node is None:
                logger.error("GraphRuntime: unknown node %r", current)
                state.outcome = GraphOutcome.ERRORED
                state.error = f"unknown node: {current}"
                break

            t0 = time.perf_counter()
            try:
                result = node(state)
            except Exception as e:  # noqa: BLE001
                logger.exception("GraphRuntime: node %s raised", current)
                state.outcome = GraphOutcome.ERRORED
                state.error = f"{current}: {type(e).__name__}: {e}"
                break
            elapsed_ms = (time.perf_counter() - t0) * 1000
            state.record(current, elapsed_ms)

            if result.outcome is not None:
                state.outcome = result.outcome
            if result.next_node == END:
                break
            current = result.next_node
            steps += 1

        return state

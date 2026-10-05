"""Minimal request-scoped state-machine runtime with LangGraph-style nodes.

A graph is just a list of (name, callable) pairs plus a small set
of edges.  Each node receives the current ``AgentGraphState``,
may mutate it, and returns a :class:`NodeResult` naming the next
node (or ``END`` to terminate).  This covers ReAct / Plan-and-
Execute / Reflexion without pulling in LangGraph.

This deliberately small dispatcher is not upstream LangGraph: it has no
checkpoint store, retries, interrupts, or durable resume. It is suitable for
bounded, single-request turns only. A durable workflow migration must account
for tool side effects and idempotency before enabling node replay.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

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


class CheckpointStore:
    """Base class for durable graph state checkpointers."""

    def save_checkpoint(self, thread_id: str, step: int, node_name: str, state_dict: dict[str, Any]) -> None:
        raise NotImplementedError

    def load_checkpoint(self, thread_id: str) -> dict[str, Any] | None:
        raise NotImplementedError


class InMemoryCheckpointStore(CheckpointStore):
    """Process-local checkpoint store for tests and lightweight executions."""

    def __init__(self) -> None:
        self._checkpoints: dict[str, list[dict[str, Any]]] = {}

    def save_checkpoint(self, thread_id: str, step: int, node_name: str, state_dict: dict[str, Any]) -> None:
        entry = {"thread_id": thread_id, "step": step, "node": node_name, "state": dict(state_dict), "ts": time.time()}
        self._checkpoints.setdefault(thread_id, []).append(entry)

    def load_checkpoint(self, thread_id: str) -> dict[str, Any] | None:
        entries = self._checkpoints.get(thread_id)
        return entries[-1]["state"] if entries else None


class GraphRuntime:
    """Dispatch loop for a list of named nodes.

    Features:
    - Bounded execution (max_steps cap as a safety net)
    - Per-node duration tracking into state.trace
    - Structured exception capture — a failing node terminates
      with ``outcome=ERRORED`` rather than unwinding
    - Optional durable checkpointing after each node transition
    """

    def __init__(
        self,
        nodes: Mapping[str, GraphNode],
        entry: str,
        max_steps: int = 12,
        checkpointer: CheckpointStore | None = None,
    ) -> None:
        if not isinstance(nodes, Mapping) or not nodes:
            raise ValueError("nodes must be a non-empty mapping")
        if any(not isinstance(name, str) or not name or name == END for name in nodes):
            raise ValueError("node names must be non-empty strings other than END")
        if any(not callable(node) for node in nodes.values()):
            raise ValueError("every graph node must be callable")
        if not isinstance(entry, str) or entry not in nodes:
            raise ValueError(f"entry node {entry!r} not in {sorted(nodes)}")
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
            raise ValueError("max_steps must be a positive integer")
        self._nodes = dict(nodes)
        self._entry = entry
        self._max_steps = max_steps
        self._checkpointer = checkpointer

    def run(self, state: AgentGraphState, thread_id: str = "") -> AgentGraphState:
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
                elapsed_ms = (time.perf_counter() - t0) * 1000
                state.record(current, elapsed_ms, status="error", error_type=type(e).__name__)
                logger.exception("GraphRuntime: node %s raised", current)
                state.outcome = GraphOutcome.ERRORED
                state.error = f"{current}: {type(e).__name__}: {e}"
                break
            elapsed_ms = (time.perf_counter() - t0) * 1000

            if not isinstance(result, NodeResult):
                state.record(current, elapsed_ms, status="error", error_type="InvalidNodeResult")
                logger.error("GraphRuntime: node %s returned %s, expected NodeResult", current, type(result).__name__)
                state.outcome = GraphOutcome.ERRORED
                state.error = f"{current}: node returned {type(result).__name__}, expected NodeResult"
                break
            if not isinstance(result.next_node, str) or (
                result.next_node != END and result.next_node not in self._nodes
            ):
                state.record(current, elapsed_ms, status="error", error_type="InvalidTransition")
                logger.error("GraphRuntime: node %s selected unknown node %r", current, result.next_node)
                state.outcome = GraphOutcome.ERRORED
                state.error = f"unknown node: {result.next_node}"
                break
            if result.outcome is not None and not isinstance(result.outcome, GraphOutcome):
                state.record(current, elapsed_ms, status="error", error_type="InvalidOutcome")
                logger.error("GraphRuntime: node %s returned invalid outcome %r", current, result.outcome)
                state.outcome = GraphOutcome.ERRORED
                state.error = f"{current}: invalid graph outcome"
                break

            state.record(current, elapsed_ms, status="ok")

            if self._checkpointer and thread_id:
                try:
                    self._checkpointer.save_checkpoint(thread_id, steps, current, state.to_dict())
                except Exception as exc:  # noqa: BLE001
                    logger.debug("GraphRuntime: checkpoint save skipped: %s", exc)

            if result.outcome is not None:
                state.outcome = result.outcome
            if result.next_node == END:
                break
            current = result.next_node
            steps += 1

        return state

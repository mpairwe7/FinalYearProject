"""Minimal request-scoped state-machine runtime with LangGraph-style nodes.

A graph is just a list of (name, callable) pairs plus a small set
of edges.  Each node receives the current ``AgentGraphState``,
may mutate it, and returns a :class:`NodeResult` naming the next
node (or ``END`` to terminate).  This covers ReAct / Plan-and-
Execute / Reflexion without pulling in LangGraph.

This deliberately small dispatcher is not upstream LangGraph and does not replay
nodes. Checkpoints and ``resume`` exist for a suspended approval, and the service
passes no ``thread_id``, so production turns are bounded and single-request. A
durable workflow migration must account for tool side effects and idempotency
before enabling node replay.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ...resilience import remaining_turn_seconds
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


class DurableCheckpointStore(CheckpointStore):
    """Database-backed durable checkpointer for agent graph execution states.

    A failed save is raised, not swallowed: only the runtime knows whether this
    was a routine step or a suspend that nothing else can resume.
    """

    def save_checkpoint(self, thread_id: str, step: int, node_name: str, state_dict: dict[str, Any]) -> None:
        from ...database import save_agent_checkpoint

        save_agent_checkpoint(thread_id, step, node_name, state_dict)

    async def async_save_checkpoint(self, thread_id: str, step: int, node_name: str, state_dict: dict[str, Any]) -> None:
        import asyncio

        await asyncio.to_thread(self.save_checkpoint, thread_id, step, node_name, state_dict)

    def load_checkpoint(self, thread_id: str) -> dict[str, Any] | None:
        try:
            from ...database import get_agent_checkpoint

            return get_agent_checkpoint(thread_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Checkpoint could not be read: %s", type(exc).__name__)
            return None

    async def async_load_checkpoint(self, thread_id: str) -> dict[str, Any] | None:
        import asyncio

        return await asyncio.to_thread(self.load_checkpoint, thread_id)


class GraphRuntime:
    """Dispatch loop for a list of named nodes.

    Features:
    - Bounded execution: ``max_steps``, and the turn's deadline between nodes
    - Per-node duration tracking into state.trace
    - Structured exception capture — a failing node terminates
      with ``outcome=ERRORED`` rather than unwinding
    - Optional durable checkpointing after each node transition. A lost routine
      checkpoint costs nothing this turn; a lost suspend ends it, because a thread
      that says it is waiting with nothing saved can never be resumed.

    :meth:`run` and :meth:`arun` differ only in how they call a node and save a
    checkpoint; everything they decide is shared, so the two cannot drift.
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

    def _next_node(self, state: AgentGraphState, current: str, steps: int) -> GraphNode | None:
        """The node to run, or ``None`` with *state* marked if the graph must stop."""
        if steps >= self._max_steps:
            logger.warning("GraphRuntime: max_steps=%d reached", self._max_steps)
            state.outcome = GraphOutcome.TRUNCATED
            return None
        if steps and remaining_turn_seconds(1.0) <= 0:
            logger.warning("GraphRuntime: turn deadline reached before %s", current)
            state.outcome = GraphOutcome.TRUNCATED
            state.error = "turn deadline reached"
            return None
        node = self._nodes.get(current)
        if node is None:
            logger.error("GraphRuntime: unknown node %r", current)
            state.outcome = GraphOutcome.ERRORED
            state.error = f"unknown node: {current}"
        return node

    @staticmethod
    def _node_raised(state: AgentGraphState, current: str, exc: Exception, started: float) -> None:
        elapsed_ms = (time.perf_counter() - started) * 1000
        state.record(current, elapsed_ms, status="error", error_type=type(exc).__name__)
        logger.error("GraphRuntime: node %s raised", current, exc_info=exc)
        state.outcome = GraphOutcome.ERRORED
        state.error = f"{current}: {type(exc).__name__}: {exc}"

    def _accepted(self, state: AgentGraphState, current: str, result: Any, elapsed_ms: float) -> bool:
        """Validate what a node returned, recording the step; False stops the graph."""
        if not isinstance(result, NodeResult):
            state.record(current, elapsed_ms, status="error", error_type="InvalidNodeResult")
            logger.error("GraphRuntime: node %s returned %s, expected NodeResult", current, type(result).__name__)
            state.outcome = GraphOutcome.ERRORED
            state.error = f"{current}: node returned {type(result).__name__}, expected NodeResult"
            return False
        if not isinstance(result.next_node, str) or (
            result.next_node != END and result.next_node not in self._nodes
        ):
            state.record(current, elapsed_ms, status="error", error_type="InvalidTransition")
            logger.error("GraphRuntime: node %s selected unknown node %r", current, result.next_node)
            state.outcome = GraphOutcome.ERRORED
            state.error = f"unknown node: {result.next_node}"
            return False
        if result.outcome is not None and not isinstance(result.outcome, GraphOutcome):
            state.record(current, elapsed_ms, status="error", error_type="InvalidOutcome")
            logger.error("GraphRuntime: node %s returned invalid outcome %r", current, result.outcome)
            state.outcome = GraphOutcome.ERRORED
            state.error = f"{current}: invalid graph outcome"
            return False
        state.record(current, elapsed_ms, status="ok")
        return True

    @staticmethod
    def _checkpoint_failed(state: AgentGraphState, result: NodeResult, exc: Exception) -> None:
        """A suspend that could not be saved is not a suspend."""
        suspending = result.outcome == GraphOutcome.WAITING_USER or state.outcome == GraphOutcome.WAITING_USER
        logger.warning("GraphRuntime: checkpoint not saved: %s", type(exc).__name__)
        if suspending:
            state.outcome = GraphOutcome.ERRORED
            state.error = "suspended state could not be saved"
            result.outcome = GraphOutcome.ERRORED
            result.next_node = END

    @staticmethod
    def _advance(state: AgentGraphState, result: NodeResult) -> str:
        """Apply *result*, returning the next node or END to stop."""
        if result.outcome is not None:
            state.outcome = result.outcome
        if result.outcome == GraphOutcome.WAITING_USER:
            return END
        return result.next_node

    def _checkpoint_state(self, state: AgentGraphState, result: NodeResult) -> dict[str, Any]:
        """State as of this transition. A node's outcome is applied after the save,
        so without this a checkpoint could record the outcome before it."""
        snapshot = state.to_dict()
        if result.outcome is not None:
            snapshot["outcome"] = result.outcome.value
        return snapshot

    def run(self, state: AgentGraphState, thread_id: str = "", entry: str | None = None) -> AgentGraphState:
        current = entry or self._entry
        steps = 0
        while current != END:
            node = self._next_node(state, current, steps)
            if node is None:
                break
            t0 = time.perf_counter()
            try:
                result = node(state)
            except Exception as e:  # noqa: BLE001
                self._node_raised(state, current, e, t0)
                break
            if not self._accepted(state, current, result, (time.perf_counter() - t0) * 1000):
                break
            if self._checkpointer and thread_id:
                try:
                    self._checkpointer.save_checkpoint(
                        thread_id, steps, current, self._checkpoint_state(state, result)
                    )
                except Exception as exc:  # noqa: BLE001
                    self._checkpoint_failed(state, result, exc)
            current = self._advance(state, result)
            steps += 1

        return state

    async def arun(self, state: AgentGraphState, thread_id: str = "", entry: str | None = None) -> AgentGraphState:
        """Asynchronously dispatch nodes without blocking the asyncio event loop."""
        import asyncio
        import inspect

        current = entry or self._entry
        steps = 0
        while current != END:
            node = self._next_node(state, current, steps)
            if node is None:
                break
            t0 = time.perf_counter()
            try:
                outcome = node(state)
                result = await outcome if inspect.isawaitable(outcome) else outcome
            except Exception as e:  # noqa: BLE001
                self._node_raised(state, current, e, t0)
                break
            if not self._accepted(state, current, result, (time.perf_counter() - t0) * 1000):
                break
            if self._checkpointer and thread_id:
                try:
                    await asyncio.to_thread(
                        self._checkpointer.save_checkpoint,
                        thread_id,
                        steps,
                        current,
                        self._checkpoint_state(state, result),
                    )
                except Exception as exc:  # noqa: BLE001
                    self._checkpoint_failed(state, result, exc)
            current = self._advance(state, result)
            steps += 1

        return state

    def _prepare_resume(
        self,
        data: dict[str, Any] | None,
        thread_id: str,
        resume_payload: dict[str, Any] | None,
        start_node: str | None,
        expected_user_id: str,
        expected_tenant_id: str,
    ) -> tuple[AgentGraphState, str]:
        """Rebuild a suspended thread, or refuse. Shared by both resume paths.

        The approval is the taxpayer's, so it must come from the taxpayer: the
        thread id was the only thing asked for, and anyone holding it could approve
        an action on someone else's account. Naming a start node cannot step past
        the gate either, and a thread with an owner refuses a caller who names none.
        """
        if not data:
            raise ValueError(f"No checkpoint found for thread {thread_id!r}")

        state = AgentGraphState.from_dict(data)
        if state.outcome != GraphOutcome.WAITING_USER:
            raise ValueError(f"Thread {thread_id!r} is not waiting for user consent (current outcome: {state.outcome})")
        if state.user_id and state.user_id != expected_user_id:
            raise PermissionError(f"Thread {thread_id!r} belongs to another taxpayer")
        if state.user_id and expected_tenant_id and state.tenant_id != expected_tenant_id:
            raise PermissionError(f"Thread {thread_id!r} belongs to another tenant")

        if resume_payload and resume_payload.get("approved"):
            if state.pending_action:
                state.pending_action.authorized = True
            state.outcome = GraphOutcome.ANSWERED
        elif resume_payload:
            if state.pending_action:
                state.pending_action.authorized = False
            state.outcome = GraphOutcome.ESCALATED
            state.escalation_reason = str(resume_payload.get("reason", "Action declined by taxpayer"))
        else:
            state.outcome = GraphOutcome.ANSWERED

        unapproved = state.pending_action is not None and not state.pending_action.authorized
        if start_node == "act" and unapproved:
            raise PermissionError(f"Thread {thread_id!r} has no approval to act on")
        entry = start_node or ("act" if state.pending_action and state.pending_action.authorized else "respond")
        if entry not in self._nodes:
            entry = self._entry
        return state, entry

    def resume(
        self,
        thread_id: str,
        resume_payload: dict[str, Any] | None = None,
        start_node: str | None = None,
        *,
        expected_user_id: str = "",
        expected_tenant_id: str = "",
    ) -> AgentGraphState:
        """Resume a suspended graph execution for the taxpayer who owns it."""
        if not self._checkpointer:
            raise RuntimeError("Cannot resume execution without a configured checkpointer")
        data = self._checkpointer.load_checkpoint(thread_id)
        state, entry = self._prepare_resume(
            data, thread_id, resume_payload, start_node, expected_user_id, expected_tenant_id
        )
        return self.run(state, thread_id=thread_id, entry=entry)

    async def aresume(
        self,
        thread_id: str,
        resume_payload: dict[str, Any] | None = None,
        start_node: str | None = None,
        *,
        expected_user_id: str = "",
        expected_tenant_id: str = "",
    ) -> AgentGraphState:
        """Asynchronously resume a suspended execution for the taxpayer who owns it."""
        if not self._checkpointer:
            raise RuntimeError("Cannot resume execution without a configured checkpointer")
        import asyncio

        data = await asyncio.to_thread(self._checkpointer.load_checkpoint, thread_id)
        state, entry = self._prepare_resume(
            data, thread_id, resume_payload, start_node, expected_user_id, expected_tenant_id
        )
        return await self.arun(state, thread_id=thread_id, entry=entry)

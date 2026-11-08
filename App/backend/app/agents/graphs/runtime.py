"""Minimal request-scoped state-machine runtime with LangGraph-style nodes.

A graph is just a list of (name, callable) pairs plus a small set
of edges.  Each node receives the current ``AgentGraphState``,
may mutate it, and returns a :class:`NodeResult` naming the next
node (or ``END`` to terminate).  This covers ReAct / Plan-and-
Execute / Reflexion without pulling in LangGraph.

This deliberately small dispatcher is not upstream LangGraph and does not replay
nodes. The service passes the conversation id as ``thread_id``, so every graph
turn of a conversation shares one thread. What a checkpoint is for here is a
suspended approval and its resume (ADR-0030):

* Step numbers continue across runs on a thread, so the latest row is the
  latest state. A resume that restarted at 0 left its own suspend as "latest",
  and the same approval could be replayed (G468).
* A resume claims the suspend by inserting the next step. The primary key is
  the lock: of two concurrent resumes one inserts and the other is refused,
  so the action runs once (G469; LangGraph's durable-execution guidance asks
  for the same exactly-once property from side effects).
* The approval carries the digest of the action the taxpayer was shown, and
  the suspended row carries an HMAC over that action, its owner and its time,
  so neither a different action nor an edited row can be approved (G470, G472;
  OWASP ASI09). An approval older than ``GRAPH_APPROVAL_TTL_S`` is refused (G471).
* Routine node transitions are persisted only when the store asks for it.
  Nothing reads them, and each held the question, history and passages (G473).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ...resilience import remaining_turn_seconds
from .state import AgentGraphState, GraphOutcome

logger = logging.getLogger(__name__)

END = "__end__"

GraphNode = Callable[[AgentGraphState], "NodeResult"]

#: How long a suspended approval may be acted on.
APPROVAL_TTL_SECONDS = float(os.getenv("GRAPH_APPROVAL_TTL_S", "600"))
#: The node name recorded for a resume's claim on a suspend.
RESUME_CLAIM_NODE = "__resume__"
_RESUMED = "resumed"


def _approval_key() -> bytes:
    """The HMAC key for suspended approvals: the confirmation-token secret."""
    from ...confirm_tokens import _secret

    return _secret()


def _approval_mac(thread_id: str, step: int, snapshot: Mapping[str, Any]) -> str:
    """HMAC over everything a resume trusts: the whole state, its thread and its step.

    The whole state, because a resumed run acts with its role, purposes and
    plan as well as its pending action. The step, because a signed suspend
    copied to a higher step would otherwise be resumable a second time.
    Computed on the JSON form, so it survives the store's round trip.
    """
    key = _approval_key()
    if not key:
        return ""
    state = {k: v for k, v in snapshot.items() if k != "approval_mac"}
    canonical = json.loads(json.dumps(state, default=str))
    bound = {"thread_id": thread_id, "step": int(step), "state": canonical}
    message = json.dumps(bound, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


#: Marker rows. None carries the question or history: only who, and what happened.
SUPERSEDED_NODE = "__superseded__"
RESUME_DONE_NODE = "__resume_done__"
RESUME_FAILED_NODE = "__resume_failed__"


def resume_status(store: CheckpointStore, thread_id: str) -> str:
    """Where a thread's approval stands, for reconciliation (ADR-0030).

    ``awaiting_approval``; ``claimed_incomplete`` — the latest resume claimed the
    suspend and no end marker names that claim, so the process stopped and the
    action may or may not have run (reconcile with the downstream system; never
    re-run it); ``completed``; ``failed``; ``superseded`` by a later turn; or
    ``none``. An end marker names its claim's step, so a late marker from an
    older resume cannot answer for a newer one.
    """
    rows = sorted(store.history(thread_id))
    if not rows:
        return "none"
    latest = store.latest_checkpoint(thread_id)
    if latest is not None and latest[1].get("outcome") == GraphOutcome.WAITING_USER.value:
        return "awaiting_approval"
    claims = [step for step, name in rows if name == RESUME_CLAIM_NODE]
    if rows[-1][1] == SUPERSEDED_NODE and (not claims or claims[-1] < rows[-1][0]):
        return "superseded"
    if not claims:
        return "none"
    names = {name for _, name in rows}
    if f"{RESUME_DONE_NODE}:{claims[-1]}" in names:
        return "completed"
    if f"{RESUME_FAILED_NODE}:{claims[-1]}" in names:
        return "failed"
    return "claimed_incomplete"


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
    """Base class for durable graph state checkpointers.

    ``persist_routine`` says whether a node transition that neither suspends
    nor resumes is written. A suspend and a resume claim always are.
    """

    persist_routine: bool = True

    def save_checkpoint(self, thread_id: str, step: int, node_name: str, state_dict: dict[str, Any]) -> None:
        raise NotImplementedError

    def load_checkpoint(self, thread_id: str) -> dict[str, Any] | None:
        latest = self.latest_checkpoint(thread_id)
        return latest[1] if latest else None

    def latest_checkpoint(self, thread_id: str) -> tuple[int, dict[str, Any]] | None:
        """``(step, state)`` of the highest step on *thread_id*, or ``None``."""
        raise NotImplementedError

    def claim(self, thread_id: str, step: int, node_name: str, state_dict: dict[str, Any]) -> bool:
        """Write *step* only if no row holds it. False means another run got there first."""
        raise NotImplementedError

    def history(self, thread_id: str) -> list[tuple[int, str]]:
        """``(step, node)`` for every row on *thread_id*."""
        raise NotImplementedError


class InMemoryCheckpointStore(CheckpointStore):
    """Process-local checkpoint store for tests and lightweight executions."""

    def __init__(self) -> None:
        import threading

        self._checkpoints: dict[str, dict[int, dict[str, Any]]] = {}
        self._lock = threading.RLock()

    def save_checkpoint(self, thread_id: str, step: int, node_name: str, state_dict: dict[str, Any]) -> None:
        entry = {"thread_id": thread_id, "step": step, "node": node_name, "state": dict(state_dict), "ts": time.time()}
        with self._lock:
            self._checkpoints.setdefault(thread_id, {})[step] = entry

    def latest_checkpoint(self, thread_id: str) -> tuple[int, dict[str, Any]] | None:
        with self._lock:
            entries = self._checkpoints.get(thread_id)
            if not entries:
                return None
            step = max(entries)
            return step, entries[step]["state"]

    def claim(self, thread_id: str, step: int, node_name: str, state_dict: dict[str, Any]) -> bool:
        # Through save_checkpoint, so a store that overrides only the save still
        # fails a claim the same way.
        with self._lock:
            if step in self._checkpoints.get(thread_id, {}):
                return False
            self.save_checkpoint(thread_id, step, node_name, state_dict)
            return True

    def history(self, thread_id: str) -> list[tuple[int, str]]:
        with self._lock:
            return [(step, entry["node"]) for step, entry in self._checkpoints.get(thread_id, {}).items()]


class DurableCheckpointStore(CheckpointStore):
    """Database-backed durable checkpointer for agent graph execution states.

    A failed save is raised, not swallowed: only the runtime knows whether this
    was a routine step or a suspend that nothing else can resume. Routine
    transitions are written only with the ``graph_checkpoint_routine`` flag: nothing
    reads them, each held the question, history and retrieved passages (DPPA
    2019 data minimisation), and each cost a synchronous write per node.
    """

    def __init__(self) -> None:
        self._persist_routine: bool | None = None

    @property
    def persist_routine(self) -> bool:
        """The ``graph_checkpoint_routine`` flag, unless set on this store."""
        if self._persist_routine is not None:
            return self._persist_routine
        from ...flags import flags

        return flags.is_enabled("graph_checkpoint_routine")

    @persist_routine.setter
    def persist_routine(self, value: bool) -> None:
        self._persist_routine = bool(value)

    def save_checkpoint(self, thread_id: str, step: int, node_name: str, state_dict: dict[str, Any]) -> None:
        from ...database import save_agent_checkpoint

        save_agent_checkpoint(thread_id, step, node_name, state_dict)

    async def async_save_checkpoint(self, thread_id: str, step: int, node_name: str, state_dict: dict[str, Any]) -> None:
        import asyncio

        await asyncio.to_thread(self.save_checkpoint, thread_id, step, node_name, state_dict)

    def latest_checkpoint(self, thread_id: str) -> tuple[int, dict[str, Any]] | None:
        """Raises when the store cannot be read: "unreadable" is not "empty".

        Read as empty, a run would start at step 0 and could write a suspend
        beneath the thread's latest row, where no resume would ever find it.
        """
        from ...database import get_latest_agent_checkpoint

        return get_latest_agent_checkpoint(thread_id)

    def claim(self, thread_id: str, step: int, node_name: str, state_dict: dict[str, Any]) -> bool:
        from ...database import insert_agent_checkpoint_if_absent

        return insert_agent_checkpoint_if_absent(thread_id, step, node_name, state_dict)

    def history(self, thread_id: str) -> list[tuple[int, str]]:
        from ...database import list_agent_checkpoints

        return [(int(row["step"]), str(row["node_name"])) for row in list_agent_checkpoints(thread_id)]

    async def async_load_checkpoint(self, thread_id: str) -> dict[str, Any] | None:
        import asyncio

        return await asyncio.to_thread(self.load_checkpoint, thread_id)


def _current_kernel_version() -> str:
    try:
        from ...kernel.release import current

        return current().version
    except Exception:  # noqa: BLE001 - a missing kernel must not break a resume path that predates it
        logger.warning("GraphRuntime: kernel version unavailable", exc_info=True)
        return ""


def _stamp_kernel(state: AgentGraphState) -> None:
    """Pin a fresh run to the turn's kernel release (ADR-0033). A resumed state keeps its own."""
    if state.kernel_version:
        return
    try:
        from ...kernel.pin import active_pin, pin_for_turn

        pin = active_pin() or pin_for_turn().to_dict()
    except Exception:  # noqa: BLE001
        logger.warning("GraphRuntime: kernel pin unavailable", exc_info=True)
        return
    state.kernel_version = str(pin.get("kernel_version") or "")
    state.ruleset_version = str(pin.get("ruleset_version") or "")
    state.as_of = str(pin.get("as_of") or "")


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

    def _checkpoint_state(
        self, state: AgentGraphState, result: NodeResult, thread_id: str = "", step: int = 0
    ) -> dict[str, Any]:
        """State as of this transition. A node's outcome is applied after the save,
        so without this a checkpoint could record the outcome before it. A
        suspend is stamped with its time and sealed with an HMAC over the whole
        state, its thread and its step."""
        snapshot = state.to_dict()
        if result.outcome is not None:
            snapshot["outcome"] = result.outcome.value
        if snapshot.get("outcome") == GraphOutcome.WAITING_USER.value:
            snapshot["suspended_at"] = time.time()
            mac = _approval_mac(thread_id, step, snapshot)
            if not mac:
                raise RuntimeError("no approval signing key is configured")
            snapshot["approval_mac"] = mac
        return snapshot

    def _open(self, state: AgentGraphState, thread_id: str) -> list[int | None] | None:
        """This run's write cursor, or ``None`` when the run must not proceed.

        Writes are dense: each goes one past the last row the writer saw, so any
        two writers that saw the same thread contend for the same primary key
        and exactly one wins (CodeRabbit on #702). A cursor of ``[None]`` means
        the position is unknown: nothing is written and a suspend fails closed.

        A pending approval as the latest row means the conversation moved on
        without it, so this run retires it first, at the very step a resume
        would claim. A run that can neither retire it nor see it retired does
        not proceed, because the approval would stay live behind it.
        """
        if not (self._checkpointer and thread_id):
            return [0]
        try:
            latest = self._checkpointer.latest_checkpoint(thread_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("GraphRuntime: latest checkpoint unreadable: %s", type(exc).__name__)
            return [None]
        for _ in range(3):
            if latest is None:
                return [0]
            step, data = latest
            if data.get("outcome") != GraphOutcome.WAITING_USER.value:
                return [step + 1]
            if self._write_marker(state, thread_id, step + 1, SUPERSEDED_NODE, "superseded"):
                return [step + 2]
            try:
                latest = self._checkpointer.latest_checkpoint(thread_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("GraphRuntime: latest checkpoint unreadable: %s", type(exc).__name__)
                break
        state.outcome = GraphOutcome.ERRORED
        state.error = "a pending approval on this conversation could not be retired"
        return None

    def _should_save(self, state: AgentGraphState, result: NodeResult) -> bool:
        suspending = result.outcome == GraphOutcome.WAITING_USER or state.outcome == GraphOutcome.WAITING_USER
        return suspending or bool(getattr(self._checkpointer, "persist_routine", True))

    def _save(
        self, state: AgentGraphState, result: NodeResult, thread_id: str, cursor: list[int | None], node: str
    ) -> None:
        """Persist this transition when it must be, at the cursor, and advance it.

        Insert-if-absent at the cursor: a row there means another run wrote since
        this one looked, so this run's position is stale; it stops writing, and a
        suspend it was saving fails closed.
        """
        if not (self._checkpointer and thread_id) or not self._should_save(state, result):
            return
        try:
            step = cursor[0]
            if step is None:
                raise RuntimeError("thread position unknown")
            snapshot = self._checkpoint_state(state, result, thread_id, step)
            if not self._checkpointer.claim(thread_id, step, node, snapshot):
                cursor[0] = None
                raise RuntimeError(f"another run wrote step {step} on this thread")
            cursor[0] = step + 1
        except Exception as exc:  # noqa: BLE001
            self._checkpoint_failed(state, result, exc)

    def _write_marker(self, state: AgentGraphState, thread_id: str, step: int, node: str, outcome: str) -> bool:
        """Insert a minimal row (who, and what happened) at *step*; True when written."""
        marker = {
            "outcome": outcome,
            "final_outcome": state.outcome.value if state.outcome else "",
            "user_id": state.user_id,
            "tenant_id": state.tenant_id,
            "at": time.time(),
        }
        try:
            return bool(self._checkpointer.claim(thread_id, step, node, marker))
        except Exception as exc:  # noqa: BLE001
            logger.warning("GraphRuntime: %s marker not saved: %s", node, type(exc).__name__)
            return False

    def _dispatch(self, state: AgentGraphState, thread_id: str, entry: str | None, cursor: list[int | None]) -> None:
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
            self._save(state, result, thread_id, cursor, current)
            current = self._advance(state, result)
            steps += 1

    async def _adispatch(
        self, state: AgentGraphState, thread_id: str, entry: str | None, cursor: list[int | None]
    ) -> None:
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
                # A synchronous node (retrieval, translation, a model call) runs in a
                # worker thread so it cannot stall the caller's event loop; the thread
                # copies the context, and no node sets a ContextVar (CodeRabbit on #705).
                if inspect.iscoroutinefunction(node):
                    outcome = node(state)
                else:
                    outcome = await asyncio.to_thread(node, state)
                result = await outcome if inspect.isawaitable(outcome) else outcome
            except Exception as e:  # noqa: BLE001
                self._node_raised(state, current, e, t0)
                break
            if not self._accepted(state, current, result, (time.perf_counter() - t0) * 1000):
                break
            await asyncio.to_thread(self._save, state, result, thread_id, cursor, current)
            current = self._advance(state, result)
            steps += 1

    def run(self, state: AgentGraphState, thread_id: str = "", entry: str | None = None) -> AgentGraphState:
        _stamp_kernel(state)
        cursor = self._open(state, thread_id)
        if cursor is not None:
            self._dispatch(state, thread_id, entry, cursor)
        return state

    async def arun(self, state: AgentGraphState, thread_id: str = "", entry: str | None = None) -> AgentGraphState:
        """Asynchronously dispatch nodes without blocking the asyncio event loop."""
        import asyncio

        _stamp_kernel(state)
        cursor = await asyncio.to_thread(self._open, state, thread_id)
        if cursor is not None:
            await self._adispatch(state, thread_id, entry, cursor)
        return state

    def _prepare_resume(
        self,
        latest: tuple[int, dict[str, Any]] | None,
        thread_id: str,
        resume_payload: dict[str, Any] | None,
        start_node: str | None,
        expected_user_id: str,
        expected_tenant_id: str,
    ) -> tuple[AgentGraphState, str, int]:
        """Rebuild a suspended thread, or refuse. Shared by both resume paths.

        The approval is the taxpayer's, so it must come from the taxpayer: the
        thread id was the only thing asked for, and anyone holding it could approve
        an action on someone else's account. Naming a start node cannot step past
        the gate either, and a thread with an owner refuses a caller who names none.

        It must also be for this action, now, from this row: an approval names the
        digest of the action it was shown, the suspended row's HMAC must match its
        whole state, thread and step, and the suspend must be younger than
        ``APPROVAL_TTL_SECONDS``. Returns the state, the entry node and the step.
        """
        if not latest:
            raise ValueError(f"No checkpoint found for thread {thread_id!r}")
        step, data = latest
        if data.get("outcome") != GraphOutcome.WAITING_USER.value:
            raise ValueError(
                f"Thread {thread_id!r} is not waiting for user consent (current outcome: {data.get('outcome')})"
            )

        state = AgentGraphState.from_dict(data)
        if state.user_id and state.user_id != expected_user_id:
            raise PermissionError(f"Thread {thread_id!r} belongs to another taxpayer")
        if state.user_id and expected_tenant_id and state.tenant_id != expected_tenant_id:
            raise PermissionError(f"Thread {thread_id!r} belongs to another tenant")

        expected_mac = _approval_mac(thread_id, step, data)
        if not expected_mac or not hmac.compare_digest(str(data.get("approval_mac") or ""), expected_mac):
            raise PermissionError(f"Thread {thread_id!r} failed its integrity check")
        suspended_at = float(data.get("suspended_at") or 0.0)
        if time.time() - suspended_at > APPROVAL_TTL_SECONDS:
            raise PermissionError(f"Thread {thread_id!r}: the approval window has closed")
        # The figures the taxpayer approved came from one kernel release; a
        # resume under another would act on figures nobody showed them (ADR-0033).
        now_version = _current_kernel_version()
        if state.kernel_version and now_version and state.kernel_version != now_version:
            raise PermissionError(
                f"Thread {thread_id!r}: the kernel release changed since the approval was asked "
                f"({state.kernel_version} -> {now_version}); ask again"
            )

        if resume_payload and resume_payload.get("approved"):
            if state.pending_action:
                presented = str(resume_payload.get("action_digest") or "")
                if not hmac.compare_digest(presented, state.pending_action.digest()):
                    raise PermissionError(f"Thread {thread_id!r}: the approval does not match the action shown")
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
        return state, entry, step

    def _claim(self, thread_id: str, suspended_step: int, state: AgentGraphState) -> int:
        """Take the suspend for this resume, or refuse; returns the claim's step.

        The claim is the row one past the suspend, the same step a later turn
        writes to retire it. Its primary key is the lock: a second resume, or a
        turn that moved the conversation on, has taken it, and this resume stops.
        """
        claim = {
            "outcome": _RESUMED,
            "user_id": state.user_id,
            "tenant_id": state.tenant_id,
            "resumed_at": time.time(),
        }
        if not self._checkpointer.claim(thread_id, suspended_step + 1, RESUME_CLAIM_NODE, claim):
            raise PermissionError(f"Thread {thread_id!r} has already been resumed or moved on")
        return suspended_step + 1

    def _resume_done(self, state: AgentGraphState, thread_id: str, claim_step: int, cursor: list[int | None]) -> None:
        """Record how this claimed resume ended, naming the claim. A claim with no end is a resume to reconcile.

        A resumed run that asked for another approval keeps its own suspend as
        the latest row. The marker goes one past the latest row it can see;
        another writer there means it reads again rather than losing the record.
        """
        if state.outcome == GraphOutcome.WAITING_USER:
            return
        failed = state.outcome in (GraphOutcome.ERRORED, GraphOutcome.TRUNCATED)
        node = f"{RESUME_FAILED_NODE if failed else RESUME_DONE_NODE}:{claim_step}"
        outcome = "resume_failed" if failed else "resume_completed"
        step = cursor[0]
        for _ in range(5):
            if step is None:
                try:
                    latest = self._checkpointer.latest_checkpoint(thread_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("GraphRuntime: resume end not recorded: %s", type(exc).__name__)
                    return
                step = 0 if latest is None else latest[0] + 1
            if self._write_marker(state, thread_id, step, node, outcome):
                return
            step = None
        logger.warning("GraphRuntime: resume end not recorded for claim %s", claim_step)

    def resume(
        self,
        thread_id: str,
        resume_payload: dict[str, Any] | None = None,
        start_node: str | None = None,
        *,
        expected_user_id: str = "",
        expected_tenant_id: str = "",
    ) -> AgentGraphState:
        """Resume a suspended graph execution for the taxpayer who owns it, once."""
        if not self._checkpointer:
            raise RuntimeError("Cannot resume execution without a configured checkpointer")
        latest = self._checkpointer.latest_checkpoint(thread_id)
        state, entry, step = self._prepare_resume(
            latest, thread_id, resume_payload, start_node, expected_user_id, expected_tenant_id
        )
        claim_step = self._claim(thread_id, step, state)
        cursor: list[int | None] = [claim_step + 1]
        self._dispatch(state, thread_id, entry, cursor)
        self._resume_done(state, thread_id, claim_step, cursor)
        return state

    async def aresume(
        self,
        thread_id: str,
        resume_payload: dict[str, Any] | None = None,
        start_node: str | None = None,
        *,
        expected_user_id: str = "",
        expected_tenant_id: str = "",
    ) -> AgentGraphState:
        """Asynchronously resume a suspended execution for the taxpayer who owns it, once."""
        if not self._checkpointer:
            raise RuntimeError("Cannot resume execution without a configured checkpointer")
        import asyncio

        latest = await asyncio.to_thread(self._checkpointer.latest_checkpoint, thread_id)
        state, entry, step = self._prepare_resume(
            latest, thread_id, resume_payload, start_node, expected_user_id, expected_tenant_id
        )
        claim_step = await asyncio.to_thread(self._claim, thread_id, step, state)
        cursor: list[int | None] = [claim_step + 1]
        await self._adispatch(state, thread_id, entry, cursor)
        await asyncio.to_thread(self._resume_done, state, thread_id, claim_step, cursor)
        return state

"""Checkpoint and resume integrity for the agent graph (ADR-0030, G468–G474).

Measured on ``dev`` on 2026-11-07 against the real SQLite store:

* G468 — a resumed run restarted its step numbers at 0 while "latest" meant
  the highest step, so the suspended row outlived its own resume: resuming a
  completed approval a second time ran the action again (a PRN for
  UGX 500,000 generated twice).
* G469 — two concurrent resumes of one suspend both ran the action.
* G470 — the approval was ``{"approved": true}``; nothing tied it to the action
  the taxpayer was shown.
* G471 — a suspended approval never expired.
* G472 — the checkpoint a resume trusts carried no integrity check.
* G473 — checkpoints were written with SQLite-only ``INSERT OR REPLACE``,
  which Postgres rejects, and every graph turn persisted its full state on
  every node although nothing reads a routine checkpoint.
"""

from __future__ import annotations

import threading
import time

import pytest
from app.agents.graphs.runtime import (
    END,
    DurableCheckpointStore,
    GraphRuntime,
    InMemoryCheckpointStore,
    NodeResult,
)
from app.agents.graphs.state import AgentGraphState, GraphOutcome, TaxpayerActionRequest
from app.database import init_db, list_agent_checkpoints


@pytest.fixture(autouse=True)
def _db() -> None:
    init_db()


def _graph(store, effects: list[int]) -> GraphRuntime:
    def gate(s: AgentGraphState) -> NodeResult:
        if s.pending_action and s.pending_action.requires_consent and not s.pending_action.authorized:
            s.outcome = GraphOutcome.WAITING_USER
            s.reply = f"Please confirm: {s.pending_action.description}"
            return NodeResult(next_node="act", outcome=GraphOutcome.WAITING_USER)
        return NodeResult(next_node="act")

    def prepare(s: AgentGraphState) -> NodeResult:
        return NodeResult(next_node="gate")

    def act(s: AgentGraphState) -> NodeResult:
        effects.append(int(s.pending_action.payload["amount"]))
        return NodeResult(next_node="respond")

    def respond(s: AgentGraphState) -> NodeResult:
        s.reply = "Done."
        return NodeResult(next_node=END, outcome=GraphOutcome.ANSWERED)

    return GraphRuntime(
        nodes={"prepare": prepare, "gate": gate, "act": act, "respond": respond},
        entry="prepare",
        checkpointer=store,
    )


def _suspend(runtime: GraphRuntime, thread_id: str, amount: int = 500_000) -> AgentGraphState:
    state = AgentGraphState(
        query="Generate a PRN",
        user_id="u1",
        tenant_id="t1",
        pending_action=TaxpayerActionRequest(
            action_type="generate_prn",
            description=f"Generate a PRN for UGX {amount:,}",
            payload={"amount": amount},
        ),
    )
    paused = runtime.run(state, thread_id=thread_id)
    assert paused.outcome == GraphOutcome.WAITING_USER
    return paused


def _approval(paused: AgentGraphState) -> dict:
    return {"approved": True, "action_digest": paused.pending_action.digest()}


@pytest.fixture(params=["memory", "durable"])
def store(request):
    return InMemoryCheckpointStore() if request.param == "memory" else DurableCheckpointStore()


def _thread(name: str) -> str:
    return f"{name}-{time.time_ns()}"


# --------------------------------------------------------------------------- G468


def test_a_completed_approval_cannot_be_resumed_again(store) -> None:
    effects: list[int] = []
    runtime = _graph(store, effects)
    thread_id = _thread("replay")
    paused = _suspend(runtime, thread_id)

    done = runtime.resume(thread_id, _approval(paused), expected_user_id="u1", expected_tenant_id="t1")
    assert done.outcome == GraphOutcome.ANSWERED
    with pytest.raises((ValueError, PermissionError)):
        runtime.resume(thread_id, _approval(paused), expected_user_id="u1", expected_tenant_id="t1")

    assert effects == [500_000]


def test_a_later_shorter_turn_on_the_thread_supersedes_an_earlier_one() -> None:
    """A turn that restarted at step 0 overwrote only as many rows as it had."""
    store = DurableCheckpointStore()
    store.persist_routine = True
    effects: list[int] = []
    thread_id = _thread("turns")
    _suspend(_graph(store, effects), thread_id)  # two rows: prepare, gate

    def reply(s: AgentGraphState) -> NodeResult:
        s.reply = "You're welcome."
        return NodeResult(next_node=END, outcome=GraphOutcome.ANSWERED)

    one_node = GraphRuntime(nodes={"reply": reply}, entry="reply", checkpointer=store)
    one_node.run(AgentGraphState(query="thanks", user_id="u1", tenant_id="t1"), thread_id=thread_id)

    assert [row["step"] for row in list_agent_checkpoints(thread_id)] == [0, 1, 2]
    assert store.load_checkpoint(thread_id)["query"] == "thanks"


# --------------------------------------------------------------------------- G469


def test_concurrent_resumes_run_the_action_once(store) -> None:
    effects: list[int] = []
    runtime = _graph(store, effects)
    thread_id = _thread("race")
    paused = _suspend(runtime, thread_id, amount=700_000)
    barrier = threading.Barrier(2)
    refused: list[str] = []

    def resume() -> None:
        barrier.wait()
        try:
            runtime.resume(thread_id, _approval(paused), expected_user_id="u1", expected_tenant_id="t1")
        except (ValueError, PermissionError) as exc:
            refused.append(type(exc).__name__)

    workers = [threading.Thread(target=resume) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert effects == [700_000]
    assert len(refused) == 1


# --------------------------------------------------------------------------- G470


def test_an_approval_must_name_the_action_it_approves(store) -> None:
    effects: list[int] = []
    runtime = _graph(store, effects)
    thread_id = _thread("binding")
    _suspend(runtime, thread_id)

    with pytest.raises(PermissionError):
        runtime.resume(thread_id, {"approved": True}, expected_user_id="u1", expected_tenant_id="t1")
    with pytest.raises(PermissionError):
        runtime.resume(
            thread_id, {"approved": True, "action_digest": "0" * 64}, expected_user_id="u1", expected_tenant_id="t1"
        )
    assert effects == []


def test_the_digest_is_offered_with_the_pending_action() -> None:
    action = TaxpayerActionRequest(action_type="generate_prn", description="PRN", payload={"amount": 1})

    assert action.to_dict()["action_digest"] == action.digest()
    assert len(action.digest()) == 64
    assert TaxpayerActionRequest.from_dict(action.to_dict()).digest() == action.digest()


def test_a_decline_needs_no_digest(store) -> None:
    effects: list[int] = []
    runtime = _graph(store, effects)
    thread_id = _thread("decline")
    _suspend(runtime, thread_id)

    declined = runtime.resume(thread_id, {"approved": False}, expected_user_id="u1", expected_tenant_id="t1")

    assert declined.outcome in (GraphOutcome.ESCALATED, GraphOutcome.ANSWERED)
    assert effects == []


# --------------------------------------------------------------------------- G471


def test_an_approval_expires(store, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.agents.graphs.runtime as runtime_module

    effects: list[int] = []
    runtime = _graph(store, effects)
    thread_id = _thread("expiry")
    paused = _suspend(runtime, thread_id)
    later = time.time() + runtime_module.APPROVAL_TTL_SECONDS + 1
    monkeypatch.setattr(runtime_module.time, "time", lambda: later)

    with pytest.raises(PermissionError):
        runtime.resume(thread_id, _approval(paused), expected_user_id="u1", expected_tenant_id="t1")
    assert effects == []


# --------------------------------------------------------------------------- G472


def test_a_tampered_suspend_is_refused() -> None:
    import json

    from app.database import execute, query_one

    store = DurableCheckpointStore()
    effects: list[int] = []
    runtime = _graph(store, effects)
    thread_id = _thread("tamper")
    paused = _suspend(runtime, thread_id)
    row = query_one(
        "SELECT step, state_json FROM agent_checkpoints WHERE thread_id = ? ORDER BY step DESC LIMIT 1",
        (thread_id,),
    )
    data = json.loads(row["state_json"])
    data["pending_action"]["payload"]["amount"] = 50_000_000
    data["pending_action"]["description"] = "Generate a PRN for UGX 50,000,000"
    execute(
        "UPDATE agent_checkpoints SET state_json = ? WHERE thread_id = ? AND step = ?",
        (json.dumps(data), thread_id, row["step"]),
    )
    tampered = TaxpayerActionRequest.from_dict(data["pending_action"])

    with pytest.raises(PermissionError):
        runtime.resume(
            thread_id,
            {"approved": True, "action_digest": tampered.digest()},
            expected_user_id="u1",
            expected_tenant_id="t1",
        )
    assert effects == []
    assert paused.pending_action.payload["amount"] == 500_000


# --------------------------------------------------------------------------- G473


def test_checkpoint_sql_is_portable() -> None:
    import inspect

    import app.database as database

    source = inspect.getsource(database.save_agent_checkpoint) + inspect.getsource(
        database.insert_agent_checkpoint_if_absent
    )
    assert "INSERT OR REPLACE" not in source
    assert "INSERT OR IGNORE" not in source
    assert "ON CONFLICT (thread_id, step)" in source


def test_routine_transitions_are_not_persisted_by_default() -> None:
    store = DurableCheckpointStore()
    effects: list[int] = []
    runtime = _graph(store, effects)
    thread_id = _thread("routine")

    runtime.run(AgentGraphState(query="What is VAT?", user_id="u1"), thread_id=thread_id)

    assert list_agent_checkpoints(thread_id) == []
    assert not store.persist_routine


def test_a_suspend_is_always_persisted() -> None:
    store = DurableCheckpointStore()
    effects: list[int] = []
    runtime = _graph(store, effects)
    thread_id = _thread("suspend")

    _suspend(runtime, thread_id)

    rows = list_agent_checkpoints(thread_id)
    assert [row["node_name"] for row in rows] == ["gate"]

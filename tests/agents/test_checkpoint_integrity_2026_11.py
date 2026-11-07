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
        if s.pending_action is not None:
            if s.pending_action.payload.get("fail"):
                raise RuntimeError("downstream refused")
            effects.append(int(s.pending_action.payload["amount"]))
        return NodeResult(next_node="respond")

    def respond(s: AgentGraphState) -> NodeResult:
        if s.outcome == GraphOutcome.ESCALATED:
            s.reply = s.escalation_reason
            return NodeResult(next_node=END, outcome=s.outcome)
        s.reply = "Done."
        return NodeResult(next_node=END, outcome=GraphOutcome.ANSWERED)

    return GraphRuntime(
        nodes={"prepare": prepare, "gate": gate, "act": act, "respond": respond},
        entry="prepare",
        checkpointer=store,
    )


def _suspend(runtime: GraphRuntime, thread_id: str, amount: int = 500_000, **payload) -> AgentGraphState:
    state = AgentGraphState(
        query="Generate a PRN",
        user_id="u1",
        tenant_id="t1",
        pending_action=TaxpayerActionRequest(
            action_type="generate_prn",
            description=f"Generate a PRN for UGX {amount:,}",
            payload={"amount": amount, **payload},
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
    reads = threading.Barrier(2)
    refused: list[str] = []
    real_latest = store.latest_checkpoint

    def latest_then_wait(tid: str):
        latest = real_latest(tid)
        reads.wait(timeout=5)  # both have read the suspend before either claims it
        return latest

    store.latest_checkpoint = latest_then_wait

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

    assert declined.outcome == GraphOutcome.ESCALATED
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

    result = runtime.run(AgentGraphState(query="What is VAT?", user_id="u1"), thread_id=thread_id)

    assert result.outcome == GraphOutcome.ANSWERED
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



# --------------------------------------------------------------------------- PR #702 review


def _rewrite_latest(thread_id: str, mutate) -> None:
    """Edit the latest row in place, as someone with write access to the store could."""
    import json

    from app.database import execute, query_one

    row = query_one(
        "SELECT step, state_json FROM agent_checkpoints WHERE thread_id = ? ORDER BY step DESC LIMIT 1",
        (thread_id,),
    )
    data = json.loads(row["state_json"])
    mutate(data)
    execute(
        "UPDATE agent_checkpoints SET state_json = ? WHERE thread_id = ? AND step = ?",
        (json.dumps(data), thread_id, row["step"]),
    )


def test_a_suspend_copied_to_a_higher_step_cannot_be_resumed_again() -> None:
    store = DurableCheckpointStore()
    effects: list[int] = []
    runtime = _graph(store, effects)
    thread_id = _thread("copied")
    paused = _suspend(runtime, thread_id)
    runtime.resume(thread_id, _approval(paused), expected_user_id="u1", expected_tenant_id="t1")
    import json

    from app.database import execute, query_one

    suspend = query_one(
        "SELECT step, node_name, state_json FROM agent_checkpoints WHERE thread_id = ? AND node_name = 'gate'",
        (thread_id,),
    )
    execute(
        "INSERT INTO agent_checkpoints (thread_id, step, node_name, state_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (thread_id, 50, "gate", suspend["state_json"], time.time()),
    )
    assert json.loads(suspend["state_json"])["outcome"] == "waiting_user"

    with pytest.raises(PermissionError):
        runtime.resume(thread_id, _approval(paused), expected_user_id="u1", expected_tenant_id="t1")
    assert effects == [500_000]


@pytest.mark.parametrize(
    "field_change",
    [
        lambda d: d.__setitem__("role", "ura_admin"),
        lambda d: d.__setitem__("granted_purposes", ["account_actions"]),
        lambda d: d.__setitem__("plan", ["transfer the refund to another account"]),
    ],
    ids=["role", "purposes", "plan"],
)
def test_any_edit_to_the_resumed_state_is_refused(field_change) -> None:
    store = DurableCheckpointStore()
    effects: list[int] = []
    runtime = _graph(store, effects)
    thread_id = _thread("state-edit")
    paused = _suspend(runtime, thread_id)
    _rewrite_latest(thread_id, field_change)

    with pytest.raises(PermissionError):
        runtime.resume(thread_id, _approval(paused), expected_user_id="u1", expected_tenant_id="t1")
    assert effects == []


def test_an_unreadable_store_fails_a_suspend_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.database as database

    def broken(_thread_id):
        raise RuntimeError("database down")

    monkeypatch.setattr(database, "get_latest_agent_checkpoint", broken)
    store = DurableCheckpointStore()
    effects: list[int] = []
    runtime = _graph(store, effects)
    state = AgentGraphState(
        query="pay",
        user_id="u1",
        tenant_id="t1",
        pending_action=TaxpayerActionRequest(action_type="generate_prn", description="PRN", payload={"amount": 1}),
    )

    result = runtime.run(state, thread_id=_thread("unreadable"))

    assert result.outcome == GraphOutcome.ERRORED


def test_a_concurrent_run_cannot_overwrite_a_suspend(store) -> None:
    effects: list[int] = []
    runtime = _graph(store, effects)
    thread_id = _thread("overlap")
    first = _suspend(runtime, thread_id, amount=100)
    # A second turn that read the thread before the first suspend landed.
    real = runtime._start_position
    runtime._start_position = lambda tid: (0, False)
    second = runtime.run(
        AgentGraphState(
            query="pay",
            user_id="u1",
            tenant_id="t1",
            pending_action=TaxpayerActionRequest(action_type="generate_prn", description="other", payload={"amount": 9}),
        ),
        thread_id=thread_id,
    )
    runtime._start_position = real

    assert second.outcome == GraphOutcome.ERRORED
    runtime.resume(thread_id, _approval(first), expected_user_id="u1", expected_tenant_id="t1")
    assert effects == [100]


def test_a_later_completed_turn_retires_an_earlier_approval(store) -> None:
    effects: list[int] = []
    runtime = _graph(store, effects)
    thread_id = _thread("moved-on")
    paused = _suspend(runtime, thread_id)
    later = runtime.run(AgentGraphState(query="What is VAT?", user_id="u1", tenant_id="t1"), thread_id=thread_id)
    assert later.outcome == GraphOutcome.ANSWERED

    with pytest.raises(ValueError, match="not waiting"):
        runtime.resume(thread_id, _approval(paused), expected_user_id="u1", expected_tenant_id="t1")
    assert effects == []


def test_how_a_resume_ended_is_recorded(store) -> None:
    from app.agents.graphs.runtime import resume_status

    effects: list[int] = []
    runtime = _graph(store, effects)
    ok_thread, bad_thread = _thread("done"), _thread("failed")
    ok = _suspend(runtime, ok_thread)
    bad = _suspend(runtime, bad_thread, fail=True)
    assert resume_status(store, ok_thread) == "awaiting_approval"

    runtime.resume(ok_thread, _approval(ok), expected_user_id="u1", expected_tenant_id="t1")
    runtime.resume(bad_thread, _approval(bad), expected_user_id="u1", expected_tenant_id="t1")

    assert resume_status(store, ok_thread) == "completed"
    assert resume_status(store, bad_thread) == "failed"


def test_a_claim_with_no_recorded_end_is_reported_for_reconciliation(store) -> None:
    from app.agents.graphs.runtime import resume_status

    effects: list[int] = []
    runtime = _graph(store, effects)
    thread_id = _thread("crashed")
    paused = _suspend(runtime, thread_id)
    runtime._resume_done = lambda *_args, **_kwargs: None  # the process stopped before recording an end

    runtime.resume(thread_id, _approval(paused), expected_user_id="u1", expected_tenant_id="t1")

    assert resume_status(store, thread_id) == "claimed_incomplete"
    with pytest.raises(ValueError, match="not waiting"):
        runtime.resume(thread_id, _approval(paused), expected_user_id="u1", expected_tenant_id="t1")


def test_configured_but_unavailable_postgres_refuses_checkpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.database as database

    monkeypatch.setattr(database, "ANALYTICS_BACKEND", "postgres")
    monkeypatch.setattr(database, "_pg_module", lambda: None)

    for call in (
        lambda: database.save_agent_checkpoint("t", 0, "n", {}),
        lambda: database.insert_agent_checkpoint_if_absent("t", 0, "n", {}),
        lambda: database.get_latest_agent_checkpoint("t"),
    ):
        with pytest.raises(RuntimeError):
            call()


def test_routine_persistence_is_a_registry_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    store = DurableCheckpointStore()
    assert store.persist_routine is False
    monkeypatch.setenv("FLAG_GRAPH_CHECKPOINT_ROUTINE", "true")
    assert store.persist_routine is True

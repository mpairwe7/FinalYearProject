"""Session binding and data minimisation for guided workflows (post-#619 audit).

* A signed-in caller adopts only the anonymous workflow its own request can
  prove (same session id, or the same unsessioned conversation) — never one
  reachable through someone else's conversation id alone.
* Adoption re-keys the row in place, as a compare-and-swap, so the journey
  funnel sees one journey and no copy of the answers is left behind.
* "Signed in" is decided per request, not by a marker stored on the session.
* Only the case facts a prerequisite reads are ever persisted on the session.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from app import database
from app.service import _ACTIVE_CASE_FACTS, _ACTIVE_CONVERSATION_STATE, _ACTIVE_TENANT_ID, ChatModel
from app.workflows.journey_copilot import prerequisite_case_facts
from app.workflows.loader import load_workflow
from app.workflows.registry import WorkflowRegistry, WorkflowSession

FLOWS_DIR = Path(__file__).resolve().parents[2] / "App" / "backend" / "app" / "workflows" / "flows"
TENANT = "default"


@pytest.fixture(autouse=True)
def _workflows_loaded() -> None:
    if WorkflowRegistry.get("return_filing") is None:
        for path in sorted(FLOWS_DIR.glob("*.yaml")):
            WorkflowRegistry.register(load_workflow(path))
    database.init_db()


@contextmanager
def _as_caller(thread_id: str, *, session_id: str | None, user_id: str | None) -> Iterator[str]:
    """Bind the request-scoped state key the way ``_scoped_conversation_state`` does."""
    state = database.conversation_state_key(
        thread_id, session_id=session_id, user_id=user_id, tenant_id=TENANT
    )
    tok_state = _ACTIVE_CONVERSATION_STATE.set(state)
    tok_tenant = _ACTIVE_TENANT_ID.set(TENANT)
    try:
        yield state
    finally:
        _ACTIVE_CONVERSATION_STATE.reset(tok_state)
        _ACTIVE_TENANT_ID.reset(tok_tenant)


def _turn(
    model: ChatModel, thread_id: str, message: str, *, session_id: str | None, user_id: str | None
) -> dict | None:
    with _as_caller(thread_id, session_id=session_id, user_id=user_id):
        return model._maybe_handle_workflow(
            message=message,
            rewritten=message,
            thread_id=thread_id,
            locale="en",
            user_id=user_id,
            session_id=session_id,
        )


def _start_anonymous_return_filing(model: ChatModel, *, session_id: str | None) -> tuple[str, str]:
    thread_id = f"conv-bind-{uuid.uuid4().hex[:10]}"
    first = _turn(model, thread_id, "Help me file my return", session_id=session_id, user_id=None)
    assert first is not None and first["workflow"]["id"] == "return_filing"
    second = _turn(model, thread_id, "company", session_id=session_id, user_id=None)
    assert second is not None and second["workflow"]["step_index"] == 2
    anon_key = database.conversation_state_key(
        thread_id, session_id=session_id, user_id=None, tenant_id=TENANT
    )
    return thread_id, anon_key


@pytest.mark.parametrize("victim_session", ["sess-victim", None], ids=["sessioned", "unsessioned"])
def test_signed_in_caller_cannot_adopt_another_sessions_anonymous_workflow(victim_session):
    model = ChatModel()
    thread_id, anon_key = _start_anonymous_return_filing(model, session_id=victim_session)
    before = database.get_workflow_session(anon_key, tenant_id=TENANT)
    assert before is not None and before["slots"]["taxpayer_type"] == "company"

    # An attacker who learned the conversation id signs in on their own session.
    hijack = _turn(model, thread_id, "vat", session_id="sess-attacker", user_id="usr-attacker")

    assert hijack is None or hijack["workflow"]["id"] != "return_filing"
    after = database.get_workflow_session(anon_key, tenant_id=TENANT)
    assert after is not None
    assert after["user_id"] == ""
    assert after["status"] == "active"
    assert after["slots"] == before["slots"]
    attacker_key = database.conversation_state_key(
        thread_id, session_id="sess-attacker", user_id="usr-attacker", tenant_id=TENANT
    )
    assert database.get_workflow_session(attacker_key, tenant_id=TENANT) is None


@pytest.mark.parametrize("owner_session", ["sess-owner", None], ids=["sessioned", "unsessioned"])
def test_owner_signing_in_keeps_the_same_journey_with_no_copy_left(owner_session):
    model = ChatModel()
    thread_id, anon_key = _start_anonymous_return_filing(model, session_id=owner_session)
    created_at = database.get_workflow_session(anon_key, tenant_id=TENANT)["created_at"]

    resumed = _turn(model, thread_id, "vat", session_id=owner_session, user_id="usr-owner")

    assert resumed is not None
    assert resumed["workflow"]["id"] == "return_filing"
    assert {"taxpayer_type", "tax_type"} <= set(resumed["workflow"]["filled_slots"])
    assert database.get_workflow_session(anon_key, tenant_id=TENANT) is None
    owned = database.get_workflow_session(
        database.conversation_state_key(
            thread_id, session_id=owner_session, user_id="usr-owner", tenant_id=TENANT
        ),
        tenant_id=TENANT,
    )
    assert owned is not None
    assert owned["user_id"] == "usr-owner"
    assert owned["status"] == "active"
    assert owned["created_at"] == created_at


def test_adopt_workflow_session_is_a_guarded_compare_and_swap():
    src = f"state_src_{uuid.uuid4().hex}"
    dst = f"state_dst_{uuid.uuid4().hex}"
    database.upsert_workflow_session(src, "return_filing", 1, {"taxpayer_type": "company"}, tenant_id=TENANT)

    assert database.adopt_workflow_session(src, src, user_id="u1", tenant_id=TENANT) is False
    assert database.adopt_workflow_session(src, dst, user_id="", tenant_id=TENANT) is False
    assert database.adopt_workflow_session(src, dst, user_id="u1", tenant_id="other-tenant") is False
    assert (
        database.adopt_workflow_session(src, dst, user_id="u1", tenant_id=TENANT, min_updated_at=time.time() + 60)
        is False
    ), "a row past retention must never be adopted"

    assert database.adopt_workflow_session(src, dst, user_id="u1", tenant_id=TENANT) is True
    assert database.adopt_workflow_session(dst, f"{dst}_again", user_id="u2", tenant_id=TENANT) is False, (
        "an owned row cannot be adopted a second time"
    )

    taken = f"state_taken_{uuid.uuid4().hex}"
    other = f"state_other_{uuid.uuid4().hex}"
    database.upsert_workflow_session(taken, "payment_assistance", 0, {}, tenant_id=TENANT)
    database.upsert_workflow_session(other, "return_filing", 0, {}, tenant_id=TENANT)
    assert database.adopt_workflow_session(other, taken, user_id="u3", tenant_id=TENANT) is False
    assert database.get_workflow_session(taken, tenant_id=TENANT)["workflow_id"] == "payment_assistance"

    finished = f"state_done_{uuid.uuid4().hex}"
    database.upsert_workflow_session(finished, "return_filing", 0, {}, tenant_id=TENANT)
    database.complete_workflow_session(finished, status="completed", tenant_id=TENANT)
    assert database.adopt_workflow_session(finished, f"{finished}_x", user_id="u4", tenant_id=TENANT) is False


def test_signed_in_marker_is_bound_to_the_current_request():
    session = WorkflowSession(workflow_id="return_filing", current_step_idx=0, slots={})
    ChatModel._apply_personalization_to_workflow(session, None, user_id="usr-1")
    assert session.slots["_authenticated_user_id"] == "usr-1"

    ChatModel._apply_personalization_to_workflow(session, None, user_id=None)
    assert "_authenticated_user_id" not in session.slots


def test_only_prerequisite_case_facts_are_persisted_on_the_session():
    class _Facts:
        def to_dict(self) -> dict:
            return {
                "monthly_salary": {"amount": "4500000", "currency": "UGX"},
                "annual_turnover": {"amount": "180000000", "currency": "UGX"},
                "days_in_uganda": 200,
                "has_tin": True,
            }

    assert prerequisite_case_facts(_Facts()) == {"has_tin": True}
    assert prerequisite_case_facts({"days_in_uganda": 10}) == {}
    assert prerequisite_case_facts(None) == {}

    session = WorkflowSession(
        workflow_id="return_filing",
        current_step_idx=0,
        # A row written before the rule: the whole CaseFacts was stored.
        slots={"_case_facts": {"monthly_salary": {"amount": "4500000"}, "days_in_uganda": 200}},
    )
    ChatModel._apply_personalization_to_workflow(session, None, user_id=None)
    assert "_case_facts" not in session.slots

    token = _ACTIVE_CASE_FACTS.set(_Facts())
    try:
        ChatModel._apply_personalization_to_workflow(session, None, user_id=None)
    finally:
        _ACTIVE_CASE_FACTS.reset(token)
    assert session.slots["_case_facts"] == {"has_tin": True}

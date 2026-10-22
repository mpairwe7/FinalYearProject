"""Per-conversation state is read back only by the caller who owns it (ADR-0013).

Holding a conversation id proves nothing: conversation history already
follows that rule (``database.get_recent_turns``). These tests pin it for
every other store a turn reads back:

* the facts a taxpayer states (salary, turnover, days in Uganda) and the
  calculator they last used stay with their own session;
* a workflow row under the bare conversation id — written before owner-scoped
  keys, and left there by the startup migration because it could not tell
  whose it was — is never resumed or offered;
* the "Continue <flow>" action is decided by the same key and retention rule
  as the resume it leads to.

Found live through ``/v1/chat`` on the #627 head: a second anonymous session
holding only the conversation id got the first session's salary back in its
PAYE answer, and a stranger inherited the answers of a bare-id workflow row.
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from app import database
from app.memory.resolution_ledger import ResolutionLedger
from app.service import _ACTIVE_CONVERSATION_STATE, _ACTIVE_TENANT_ID, ChatModel
from app.workflows.loader import load_workflow
from app.workflows.registry import WorkflowRegistry

if TYPE_CHECKING:
    from collections.abc import Iterator

FLOWS_DIR = Path(__file__).resolve().parents[2] / "App" / "backend" / "app" / "workflows" / "flows"
TENANT = "default"
SALARY = "4,750,000"

STRANGERS = pytest.mark.parametrize(
    "stranger",
    [
        {"session_id": "sess-stranger"},
        {"session_id": None},
        {"session_id": "sess-stranger", "user_id": "usr-stranger"},
    ],
    ids=["other-session", "header-less", "signed-in"],
)


@pytest.fixture
def model(tmp_db) -> ChatModel:
    if WorkflowRegistry.get("return_filing") is None:
        for path in sorted(FLOWS_DIR.glob("*.yaml")):
            WorkflowRegistry.register(load_workflow(path))
    return ChatModel()


def _conv(prefix: str) -> str:
    return f"conv-{prefix}-{uuid.uuid4().hex[:10]}"


def _ask(
    model: ChatModel,
    message: str,
    *,
    conv: str,
    session_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    return model.generate(  # nosemgrep: ura-llm01-raw-user-input-to-llm -- ChatModel.generate applies InputGuard before inference.
        message, conversation_id=conv, session_id=session_id, user_id=user_id, locale="en"
    )


def _says(result: dict[str, Any], figure: str) -> bool:
    return figure.replace(",", "") in str(result.get("reply") or "").replace(",", "")


@contextmanager
def _as_caller(conv: str, *, session_id: str | None, user_id: str | None) -> Iterator[str]:
    """Bind the request-scoped state key the way ``_scoped_conversation_state`` does."""
    state = database.conversation_state_key(
        conv, session_id=session_id, user_id=user_id, tenant_id=TENANT
    )
    tok_state = _ACTIVE_CONVERSATION_STATE.set(state)
    tok_tenant = _ACTIVE_TENANT_ID.set(TENANT)
    try:
        yield state
    finally:
        _ACTIVE_CONVERSATION_STATE.reset(tok_state)
        _ACTIVE_TENANT_ID.reset(tok_tenant)


@STRANGERS
def test_a_stated_salary_stays_with_the_session_that_stated_it(model, stranger):
    conv = _conv("facts")
    stated = _ask(
        model,
        f"My monthly salary is UGX {SALARY}. How much PAYE do I pay?",
        conv=conv,
        session_id="sess-owner",
    )
    assert _says(stated, SALARY)
    recalled = _ask(
        model, "How much PAYE do I pay on my salary?", conv=conv, session_id="sess-owner"
    )
    assert _says(recalled, SALARY), "the owner's next turn must still have the salary they gave"

    other = _ask(model, "How much PAYE do I pay on my salary?", conv=conv, **stranger)

    assert not _says(other, SALARY), (
        "another caller holding only the conversation id was told the owner's salary"
    )


@STRANGERS
def test_a_calculator_follow_up_replays_only_the_callers_own_calculator(model, stranger):
    conv = _conv("calc")
    _ask(model, "What is the VAT on UGX 10,000,000?", conv=conv, session_id="sess-owner")
    mine = _ask(model, "what about 2 million?", conv=conv, session_id="sess-owner")
    assert _says(mine, "360,000"), "the owner's follow-up stays on VAT"

    other = _ask(model, "what about 2 million?", conv=conv, **stranger)

    assert not _says(other, "360,000"), "another caller replayed the owner's calculator"


def test_a_rejected_calculator_follow_up_is_not_rendered_as_a_figure(model, monkeypatch):
    """The ``ok`` check sat after ``return None`` inside the ``except`` and
    never ran: a rejected follow-up reached ``format_calc_reply`` and raised."""
    conv = _conv("reject")
    _ask(model, "What is the VAT on UGX 10,000,000?", conv=conv, session_id="sess-owner")

    class _Rejecting:
        def call_tool(self, *_args: Any, **_kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(result={"ok": False, "error": "amount out of range"})

    monkeypatch.setattr("app.mcp.get_client", lambda: _Rejecting())
    with _as_caller(conv, session_id="sess-owner", user_id=None):
        follow_up = model._maybe_handle_calc_followup(
            message="what about 2 million?",
            rewritten="what about 2 million?",
            thread_id=conv,
            locale="en",
        )
    assert follow_up is None


@STRANGERS
def test_a_workflow_row_under_the_bare_conversation_id_is_never_resumed(model, stranger):
    conv = _conv("legacy")
    database.upsert_workflow_session(
        conv, "return_filing", 1, {"taxpayer_type": "company"}, tenant_id=TENANT
    )

    result = _ask(model, "vat", conv=conv, **stranger)

    assert (result.get("workflow") or {}).get("id") != "return_filing"
    own_key = database.conversation_state_key(
        conv,
        session_id=stranger.get("session_id"),
        user_id=stranger.get("user_id"),
        tenant_id=TENANT,
    )
    row = database.get_workflow_session(own_key, tenant_id=TENANT)
    assert row is None or "taxpayer_type" not in row["slots"], (
        "the legacy row's answers were copied to the caller"
    )
    legacy = database.get_workflow_session(conv, tenant_id=TENANT)
    assert legacy is not None
    assert legacy["slots"] == {"taxpayer_type": "company"}


def test_continue_action_is_offered_only_for_a_flow_the_caller_can_resume(model):
    name = WorkflowRegistry.get("return_filing").name

    # The caller's own active flow: offered inside a chat turn, and outside one
    # (the localized wrapper) when the same owner is given explicitly.
    mine = _conv("own")
    with _as_caller(mine, session_id="sess-owner", user_id=None) as key:
        database.upsert_workflow_session(
            key, "return_filing", 1, {"taxpayer_type": "company"}, tenant_id=TENANT
        )
        assert model._get_suspended_workflow_name(mine) == name
    assert (
        model._get_suspended_workflow_name(mine, session_id="sess-owner", tenant_id=TENANT) == name
    )
    assert (
        model._get_suspended_workflow_name(mine, session_id="sess-other", tenant_id=TENANT) is None
    )

    # A header-less taxpayer's flow is not offered to a caller on a session,
    # who could not resume it.
    open_conv = _conv("open")
    unsessioned = database.conversation_state_key(open_conv, tenant_id=TENANT)
    database.upsert_workflow_session(unsessioned, "return_filing", 1, {}, tenant_id=TENANT)
    with _as_caller(open_conv, session_id="sess-stranger", user_id="usr-stranger"):
        assert model._get_suspended_workflow_name(open_conv) is None

    # Nor is a bare-id legacy row, to anyone.
    legacy = _conv("legacy")
    database.upsert_workflow_session(legacy, "return_filing", 1, {}, tenant_id=TENANT)
    with _as_caller(legacy, session_id=None, user_id=None):
        assert model._get_suspended_workflow_name(legacy) is None

    # Past retention the answers are gone and the resume refuses it, so the
    # action must not promise it.
    stale = _conv("stale")
    with _as_caller(stale, session_id="sess-owner", user_id=None) as key:
        database.upsert_workflow_session(key, "return_filing", 1, {}, tenant_id=TENANT)
        database.execute(
            "UPDATE workflow_sessions SET updated_at = ? WHERE conversation_id = ?",
            (time.time() - database.conversation_ttl_seconds() - 60, key),
        )
        assert model._get_suspended_workflow_name(stale) is None


def test_promoting_an_anonymous_session_moves_its_workflow_once(tmp_db):
    owner = tmp_db.upsert_user(external_id="1000555111", tenant_id=TENANT)["id"]
    other = tmp_db.upsert_user(external_id="1000555222", tenant_id=TENANT)["id"]
    for uid in (owner, other):
        tmp_db.grant_consent(uid, "personalization", "2026-04")
    conv, sess = _conv("promote"), "sess-anon"
    anon_key = database.conversation_state_key(conv, session_id=sess, tenant_id=TENANT)
    database.upsert_workflow_session(
        anon_key, "return_filing", 2, {"tax_type": "VAT"}, tenant_id=TENANT
    )
    created_at = database.get_workflow_session(anon_key, tenant_id=TENANT)["created_at"]
    ledger = ResolutionLedger()

    first = ledger.promote_anonymous_session(sess, owner, conversation_id=conv, tenant_id=TENANT)
    second = ledger.promote_anonymous_session(sess, other, conversation_id=conv, tenant_id=TENANT)

    assert first["workflow_migrated"] is True
    assert second["workflow_migrated"] is False, (
        "a second sign-in took a journey that was already adopted"
    )
    assert database.get_workflow_session(anon_key, tenant_id=TENANT) is None, (
        "a copy was left under the anonymous key"
    )
    moved = database.get_workflow_session(
        database.conversation_state_key(conv, user_id=owner, tenant_id=TENANT), tenant_id=TENANT
    )
    assert moved is not None
    assert moved["user_id"] == owner
    assert moved["created_at"] == created_at
    assert (
        database.get_workflow_session(
            database.conversation_state_key(conv, user_id=other, tenant_id=TENANT), tenant_id=TENANT
        )
        is None
    )

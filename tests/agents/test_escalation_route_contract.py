"""A promise of a human is only true if an officer can find the taxpayer.

The ``escalate_to_human`` tool wrote its own ticket: no conversation, no user, no
tenant, no transcript, nobody notified. The model then told the taxpayer that an
officer would review it. Separately, every ticket from any path was labelled with
the ``default`` tenant whoever raised it, and an open ticket could be reused across
tenants, so a tenant's retention, erasure and reporting could not trust the column.
"""

from __future__ import annotations

import uuid
from unittest import mock

import pytest
from app import tools as tools_pkg
from app.audit import governance_v3 as gov
from app.flags import flags
from app.service import ChatModel
from app.tools import Tool, ToolRegistry, ToolSchema

# The module, not the name: ``fresh_registry`` reloads it, which makes a new ContextVar,
# so a name bound at collection would set one the tool no longer reads.
from app.tools import escalate as escalate_tool


@pytest.fixture
def queue(clean_flags, tmp_db):
    flags.set("ticket_queue", True)
    return tmp_db


def _model() -> ChatModel:
    return ChatModel.__new__(ChatModel)


def _route(model: ChatModel, conversation: str, *, tenant: str = "tenant-a", user: str = "u1"):
    return model._tool_escalation_route(
        message="I want a person to review this",
        session_id="sess-1",
        conversation_id=conversation,
        user_id=user,
        tenant_id=tenant,
        locale="en",
    )


class TestTheToolReachesAnOfficer:
    def test_the_ticket_can_be_traced_back_to_the_taxpayer(self, queue) -> None:
        conversation = f"c-{uuid.uuid4().hex[:8]}"
        token = escalate_tool.ESCALATION_ROUTE.set(_route(_model(), conversation))
        try:
            result = ToolRegistry.call("escalate_to_human", {"reason": "needs review"}, user_id="u1")
        finally:
            escalate_tool.ESCALATION_ROUTE.reset(token)
        assert result["ok"] is True
        ticket = queue.get_ticket(result["ticket_id"])
        assert ticket["conversation_id"] == conversation
        assert ticket["session_id"] == "sess-1"
        assert ticket["user_id"] == "u1"
        assert ticket["tenant_id"] == "tenant-a"
        assert ticket["transcript"]

    def test_one_conversation_gets_one_officer(self, queue) -> None:
        conversation = f"c-{uuid.uuid4().hex[:8]}"
        token = escalate_tool.ESCALATION_ROUTE.set(_route(_model(), conversation))
        try:
            first = ToolRegistry.call("escalate_to_human", {"reason": "one"}, user_id="u1")
            second = ToolRegistry.call("escalate_to_human", {"reason": "two"}, user_id="u1")
        finally:
            escalate_tool.ESCALATION_ROUTE.reset(token)
        assert first["ticket_id"] == second["ticket_id"]

    def test_staff_are_told(self, queue) -> None:
        conversation = f"c-{uuid.uuid4().hex[:8]}"
        token = escalate_tool.ESCALATION_ROUTE.set(_route(_model(), conversation))
        try:
            with mock.patch("app.service.notify_ticket_created") as notify:
                ToolRegistry.call("escalate_to_human", {"reason": "needs review"}, user_id="u1")
        finally:
            escalate_tool.ESCALATION_ROUTE.reset(token)
        notify.assert_called_once()

    def test_a_failed_handoff_is_not_reported_as_a_handoff(self, queue) -> None:
        token = escalate_tool.ESCALATION_ROUTE.set(lambda **_kwargs: "")
        try:
            result = ToolRegistry.call("escalate_to_human", {"reason": "needs review"}, user_id="u1")
        finally:
            escalate_tool.ESCALATION_ROUTE.reset(token)
        assert result["ok"] is False
        assert "ticket_id" not in result

    def test_a_route_that_raises_is_not_reported_as_a_handoff(self, queue) -> None:
        def _down(**_kwargs):
            raise ConnectionError("queue unreachable")

        token = escalate_tool.ESCALATION_ROUTE.set(_down)
        try:
            result = ToolRegistry.call("escalate_to_human", {"reason": "needs review"}, user_id="u1")
        finally:
            escalate_tool.ESCALATION_ROUTE.reset(token)
        assert result["ok"] is False

    def test_outside_a_turn_the_ticket_still_names_its_owner(self, queue) -> None:
        # An MCP server has no turn to route through, but it does know who asked.
        result = ToolRegistry.call("escalate_to_human", {"reason": "help"}, user_id="u7", tenant_id="tenant-b")
        ticket = queue.get_ticket(result["ticket_id"])
        assert ticket["user_id"] == "u7"
        assert ticket["tenant_id"] == "tenant-b"


class TestEveryTicketKeepsItsTenant:
    def test_a_ticket_is_written_to_the_tenant_that_raised_it(self, queue) -> None:
        ticket_id = _model()._maybe_create_ticket(
            reason="review", user_query="q", bot_reply="a", session_id="s",
            conversation_id=f"c-{uuid.uuid4().hex[:8]}", user_id="u1", tenant_id="tenant-a", locale="en",
        )
        assert queue.get_ticket(ticket_id)["tenant_id"] == "tenant-a"

    def test_an_open_ticket_is_not_shared_across_tenants(self, queue) -> None:
        conversation = f"c-{uuid.uuid4().hex[:8]}"
        model = _model()
        first = model._maybe_create_ticket(
            reason="review", user_query="q", bot_reply="a", session_id="s",
            conversation_id=conversation, user_id="u1", tenant_id="tenant-a", locale="en",
        )
        second = model._maybe_create_ticket(
            reason="review", user_query="q", bot_reply="a", session_id="s",
            conversation_id=conversation, user_id="u2", tenant_id="tenant-b", locale="en",
        )
        assert first != second
        assert queue.get_ticket(second)["tenant_id"] == "tenant-b"


class TestAwaitingAnOfficerIsTrue:
    """``proposed_awaiting_officer`` used to go nowhere: no queue, no person."""

    @pytest.fixture
    def write_tool(self):
        name = f"probe_{uuid.uuid4().hex[:8]}"

        class _Write(Tool):
            @property
            def schema(self) -> ToolSchema:
                return ToolSchema(name=name, description="w", risk="high", read_only=False, idempotent=False)

            def execute(self, **_kwargs):
                raise AssertionError("a held action must not run")

        tools_pkg.ToolRegistry.register(_Write())
        yield name
        tools_pkg.ToolRegistry.unregister(name)

    def test_a_held_action_opens_a_ticket(self, queue, write_tool) -> None:
        flags.set("risk_tiered_autonomy_gates", True)
        gov.quality_circuit_breaker.reset()
        conversation = f"c-{uuid.uuid4().hex[:8]}"
        token = escalate_tool.ESCALATION_ROUTE.set(_route(_model(), conversation))
        try:
            result = ToolRegistry.call(write_tool, {}, user_id="u1", caller_confirmed=True)
        finally:
            escalate_tool.ESCALATION_ROUTE.reset(token)
        assert result["ok"] is False
        assert result["autonomy_outcome"] == "proposed_awaiting_officer"
        ticket = queue.get_ticket(result["ticket_id"])
        assert ticket["conversation_id"] == conversation
        assert write_tool in ticket["reason"]

    def test_with_no_route_it_does_not_claim_an_officer_has_it(self, queue, write_tool) -> None:
        flags.set("risk_tiered_autonomy_gates", True)
        gov.quality_circuit_breaker.reset()
        result = ToolRegistry.call(write_tool, {}, user_id="u1", caller_confirmed=True)
        assert result["ok"] is False
        assert "ticket_id" not in result

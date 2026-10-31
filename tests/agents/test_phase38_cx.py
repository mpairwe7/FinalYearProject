"""Phase 38: the relationship, EQ and handoff primitives reach the text channel.

Through the real service code and a real (in-memory) analytics database:

* a text turn that *does* something opens the taxpayer's case for its domain
  (web chat used to only read the ledger), one case per domain, with a fixed
  objective rather than the taxpayer's words;
* every ticket — an auto-escalation or the taxpayer's own "Talk to an officer"
  — is prioritised from affect and stakes and carries a HandoffEnvelope with
  what was already given as names only;
* in the conversation, a person is offered first when it is urgent.

Flags are set through the environment, which is read on every call.
"""

from __future__ import annotations

import json
import uuid

import pytest

USER = "u-phase38"


@pytest.fixture
def consented(tmp_db, monkeypatch: pytest.MonkeyPatch):
    """A signed-in taxpayer who granted personalization; yields (db, internal user id)."""
    user = tmp_db.upsert_user(external_id="phase38-taxpayer", tenant_id="default")
    tmp_db.grant_consent(user["id"], "personalization", "v1")
    monkeypatch.setenv("FLAG_CONTINUOUS_RELATIONSHIP_MEMORY", "true")
    return tmp_db, user["id"]


def _cases(db, user_id: str) -> dict:
    working = db.get_working_memory(user_id, tenant_id="default") or {}
    return working.get("active_resolution_cases") or {}


class TestTextTurnsKeepTheCase:
    def test_a_workflow_turn_opens_one_case_and_the_next_moves_it(self, consented) -> None:
        from app.service import _record_turn_case

        db, user_id = consented
        started = {
            "conversation_id": "c-1",
            "retrieval_mode": "workflow",
            "workflow": {"id": "calc_vat_registration", "name": "VAT Registration Check", "status": "active"},
        }
        _record_turn_case("My TIN is 1000000001, do I need VAT?", started, user_id=user_id, tenant_id="default", channel="sse")
        done = {**started, "workflow": {**started["workflow"], "status": "completed", "completed": True}}
        _record_turn_case("yes", done, user_id=user_id, tenant_id="default", channel="rest")

        cases = _cases(db, user_id)
        assert len(cases) == 1
        case = next(iter(cases.values()))
        assert case["domain_tag"] == "vat"
        assert case["primary_objective"] == "VAT question or registration"
        assert case["source_channel"] == "web"
        assert case["status"] == "awaiting_user_action"
        assert case["completed_steps"] == [
            "Started guided steps: VAT Registration Check",
            "Completed guided steps: VAT Registration Check",
        ]
        # The taxpayer's words — here a TIN — are never the objective.
        assert "1000000001" not in json.dumps(cases)

    def test_a_plain_answer_opens_nothing(self, consented) -> None:
        from app.service import _record_turn_case

        db, user_id = consented
        _record_turn_case("What is VAT?", {"retrieval_mode": "hybrid"}, user_id=user_id, tenant_id="default", channel="sse")
        assert _cases(db, user_id) == {}

    def test_without_the_flag_nothing_is_written(self, consented, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.service import _record_turn_case

        db, user_id = consented
        monkeypatch.setenv("FLAG_CONTINUOUS_RELATIONSHIP_MEMORY", "false")
        _record_turn_case(
            "PAYE on 2,000,000", {"retrieval_mode": "calculator"}, user_id=user_id, tenant_id="default", channel="rest"
        )
        assert _cases(db, user_id) == {}

    def test_a_rate_read_out_is_not_progress_but_a_computation_is(self, consented) -> None:
        from app.service import _record_turn_case

        db, user_id = consented
        # Both come back as retrieval_mode "calculator"; only the figure makes it a case.
        _record_turn_case("What is the VAT rate in Uganda?", {"retrieval_mode": "calculator"}, user_id=user_id, tenant_id="default", channel="rest")
        assert _cases(db, user_id) == {}
        _record_turn_case("PAYE on UGX 2,500,000 a month", {"retrieval_mode": "calculator"}, user_id=user_id, tenant_id="default", channel="rest")
        _record_turn_case("and on UGX 3,000,000?", {"retrieval_mode": "calculator", "current_topic": "paye"}, user_id=user_id, tenant_id="default", channel="sse")
        cases = _cases(db, user_id)
        # The bare follow-up names no tax; the conversation's topic put it on the PAYE case.
        assert [c["domain_tag"] for c in cases.values()] == ["paye"]


def _ticket(db, *, query: str, prior: list[str] = (), handoff: dict | None = None) -> dict:
    from app.service import ChatModel

    conversation = f"c-{uuid.uuid4().hex[:8]}"
    for message in prior:
        db.log_conversation(session_id="s", conversation_id=conversation, user_message=message, bot_reply="...")
    model = ChatModel.__new__(ChatModel)
    ticket_id = ChatModel._maybe_create_ticket(
        model,
        reason="The taxpayer asked to speak to a URA officer.",
        user_query=query,
        bot_reply="",
        session_id="s",
        conversation_id=conversation,
        handoff=handoff if handoff is not None else {"topic": "account_specific", "priority": "normal",
                                                     "required_details": ["tax type and return period",
                                                                          "TIN or registered taxpayer email"]},
        locale="en",
    )
    assert ticket_id
    return db.get_ticket(ticket_id)


class TestEveryPathToAnOfficer:
    @pytest.fixture(autouse=True)
    def _flags(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FLAG_TICKET_QUEUE", "true")
        monkeypatch.setenv("FLAG_EQ_STAKES_ESCALATION", "true")
        monkeypatch.setenv("FLAG_STRUCTURED_TEXT_HANDOFF", "true")

    def test_hardship_and_enforcement_said_before_the_button_make_the_ticket_urgent(self, tmp_db) -> None:
        ticket = _ticket(
            tmp_db,
            query="Talk to an officer",
            prior=["What is PAYE?", "I can't afford this, URA froze my bank account and my staff are unpaid"],
        )
        assert ticket["priority"] == "urgent"
        handoff = ticket["handoff"]
        assert handoff["escalation"]["tier"] == "priority"
        assert {"affect:hardship", "stakes:enforcement"} <= set(handoff["escalation"]["reasons"])
        envelope = handoff["envelope"]
        assert envelope["sla"]["priority"] == "urgent"
        assert envelope["caller_affect"] == "hardship"
        assert 0 < handoff["completeness"] <= 1

    def test_what_was_already_given_is_not_asked_again_and_never_copied(self, tmp_db) -> None:
        conversation = f"c-{uuid.uuid4().hex[:8]}"
        # The workflow engine stores a session under the owner-scoped state key
        # (tenant, owner, conversation hashed) - never the id the client sent.
        state_key = tmp_db.conversation_state_key(conversation, session_id="s", tenant_id="default")
        tmp_db.upsert_workflow_session(
            state_key, "return_filing", 1, {"tax_type": "vat", "filing_period": "2026-09", "tin": "1000000001"}
        )
        from app.service import ChatModel

        model = ChatModel.__new__(ChatModel)
        handoff = {
            "topic": "account_specific",
            "priority": "normal",
            "required_details": ["tax type and return period", "TIN or registered taxpayer email", "any reference number shown in the URA portal"],
        }
        ticket_id = ChatModel._maybe_create_ticket(
            model, reason="r", user_query="I need help with my VAT return", bot_reply="", session_id="s",
            conversation_id=conversation, handoff=handoff, locale="en",
        )
        stored = tmp_db.get_ticket(ticket_id)["handoff"]
        assert stored["required_details"] == ["any reference number shown in the URA portal"]
        assert stored["already_given"] == ["filing period", "tax type", "tin"]
        assert "1000000001" not in json.dumps(stored)
        assert stored["envelope"]["verified_slot_bank"] == {"filing_period": "provided", "tax_type": "provided", "tin": "provided"}

    def test_another_owners_workflow_under_the_bare_id_is_not_read(self, tmp_db) -> None:
        from app.service import ChatModel

        conversation = f"c-{uuid.uuid4().hex[:8]}"
        tmp_db.upsert_workflow_session(conversation, "return_filing", 1, {"tax_type": "vat", "filing_period": "2026-09"})
        model = ChatModel.__new__(ChatModel)
        ticket_id = ChatModel._maybe_create_ticket(
            model, reason="r", user_query="I need help", bot_reply="", session_id="s",
            conversation_id=conversation, handoff={"topic": "account_specific", "priority": "normal",
                                                   "required_details": ["tax type and return period"]},
            locale="en",
        )
        stored = tmp_db.get_ticket(ticket_id)["handoff"]
        assert stored["already_given"] == []
        assert stored["required_details"] == ["tax type and return period"]

    def test_the_button_takes_its_topic_and_details_from_the_conversation(self, tmp_db) -> None:
        # /v1/escalate builds a placeholder hand-off: topic "general", nothing to ask for.
        placeholder = {"topic": "general", "summary": "asked for an officer", "priority": "normal", "requested_by": "taxpayer"}
        ticket = _ticket(
            tmp_db,
            query="Talk to an officer",
            prior=["What is VAT?", "My container is held at Malaba and I need the customs entry released"],
            handoff=placeholder,
        )
        handoff = ticket["handoff"]
        assert handoff["topic"] == "customs"
        assert "entry or declaration number" in handoff["required_details"]
        assert ticket["team"] == "customs"

    def test_a_real_topic_is_never_replaced(self, tmp_db) -> None:
        ticket = _ticket(
            tmp_db,
            query="I want to lodge an objection",
            handoff={"topic": "objection_or_dispute", "priority": "normal", "required_details": ["assessment or objection reference number"]},
        )
        assert ticket["handoff"]["topic"] == "objection_or_dispute"
        assert ticket["handoff"]["required_details"] == ["assessment or objection reference number"]

    def test_flags_off_leave_the_ticket_as_it_was(self, tmp_db, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FLAG_EQ_STAKES_ESCALATION", "false")
        monkeypatch.setenv("FLAG_STRUCTURED_TEXT_HANDOFF", "false")
        ticket = _ticket(tmp_db, query="URA froze my bank account and I can't afford anything")
        assert ticket["priority"] == "normal"
        assert "escalation" not in ticket["handoff"]
        assert "envelope" not in ticket["handoff"]


class TestTheOfferInTheConversation:
    def test_an_urgent_case_is_offered_a_person_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.turn_guidance import HANDOFF_ACTION, apply_turn_guidance

        monkeypatch.setenv("FLAG_EQ_STAKES_ESCALATION", "true")
        result = {"retrieval_mode": "hybrid", "reply": "Here is how agency notices work.", "next_actions": ["Check balance"]}
        apply_turn_guidance("I can't afford this, they issued an agency notice to my bank", result, subject=USER)
        assert result["next_actions"][0] == HANDOFF_ACTION

    def test_stakes_without_distress_are_offered_after_the_answer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.turn_guidance import HANDOFF_ACTION, apply_turn_guidance

        monkeypatch.setenv("FLAG_EQ_STAKES_ESCALATION", "true")
        result = {"retrieval_mode": "hybrid", "reply": "Here is the procedure.", "next_actions": ["Check release status"]}
        apply_turn_guidance("Customs seized my goods at Malaba. What is the procedure?", result, subject=USER)
        assert result["next_actions"][-1] == HANDOFF_ACTION
        assert result["next_actions"][0] != HANDOFF_ACTION

    def test_an_ordinary_question_is_not_offered_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.turn_guidance import HANDOFF_ACTION, apply_turn_guidance

        monkeypatch.setenv("FLAG_EQ_STAKES_ESCALATION", "true")
        result = {"retrieval_mode": "hybrid", "reply": "18%.", "next_actions": []}
        apply_turn_guidance("What is the VAT rate?", result, subject=USER)
        assert HANDOFF_ACTION not in result["next_actions"]

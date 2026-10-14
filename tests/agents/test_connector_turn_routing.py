"""A taxpayer who chose a service and asked it for a record gets that service.

Found live, against the real model on the GPU stack: with EFRIS selected,
"Is the taxpayer with TIN … registered? Check EFRIS" was answered by a
keyword template that rendered an invented *EFRIS Invoice Audit*; "status of
consignment IM7-0001" was answered from passages with a date and a port that
appear nowhere. Both paths pre-empted the agent, and when the agent did run
the model, under ``tool_choice=auto``, declined to call.

These tests pin the routing — where the request goes — with the agent stubbed;
``test_chat_connectors.py`` pins what the agent loop then does.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest import mock

import pytest
from app import service as service_module
from app.mcp import MCPClient
from app.mcp.transport import InProcessTransport
from app.text_signals import CONNECTOR_UNAVAILABLE_REPLY
from app.verified_resources import resources_for_turn

LOOKUP = "Is the taxpayer with TIN 1000000002 registered for VAT? Check EFRIS."
PROCEDURE = "How do I register for a TIN?"


class _Remote:
    name = "static-remote"
    namespace = "efris"

    def __init__(self) -> None:
        self.descriptors = [
            {
                "name": "efris_taxpayer_status",
                "description": "Read a sample taxpayer profile.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"tin": {"type": "string"}},
                    "required": ["tin"],
                    "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": True, "destructiveHint": False},
                "_meta": {
                    "ug.go.ura.chatbot/risk": "low",
                    "ug.go.ura.chatbot/requiredScopes": [],
                    "ug.go.ura.chatbot/requiresConfirmation": False,
                },
            }
        ]

    def list_tools(self) -> list[dict[str, Any]]:
        return self.descriptors

    def describe(self, tool_name: str) -> dict[str, Any] | None:
        return next((d for d in self.descriptors if d["name"] == tool_name), None)

    def call(self, tool_name: str, arguments: dict[str, Any], **_: Any) -> dict[str, Any]:
        return {"ok": True}


@pytest.fixture
def model(monkeypatch, tmp_db):
    monkeypatch.setenv("FLAG_ENTERPRISE_CONNECTORS", "true")
    client = MCPClient({"core": InProcessTransport(), "efris": _Remote()})
    monkeypatch.setattr("app.mcp.get_client", lambda: client)
    chat = service_module.ChatModel()
    chat._llm_available = True
    return chat


def _agent_answering(text: str, *, used_tool: bool = True):
    """A stand-in for the agent loop that records how it was called."""
    calls: list[dict[str, Any]] = []

    def agent(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "text": text,
            "tool_calls": (
                [{"name": "efris_taxpayer_status", "arguments": {"tin": "1000000002"}, "result": {"ok": True}}]
                if used_tool
                else []
            ),
            "iterations": 2,
            "truncated": False,
        }

    agent.calls = calls  # type: ignore[attr-defined]
    return agent


def _must_not_run(*_args: Any, **_kwargs: Any):
    raise AssertionError("a keyword template answered a lookup the taxpayer sent to a chosen service")


class TestSyncPath:
    def test_a_lookup_goes_to_the_service_and_not_to_a_template(self, model, monkeypatch):
        agent = _agent_answering("The sample EFRIS record lists this taxpayer as registered (demo data).")
        monkeypatch.setattr(service_module, "_call_llm_agentic", agent)
        monkeypatch.setattr(model, "_maybe_handle_fast_paths", _must_not_run)
        monkeypatch.setattr(model, "_deterministic_procedure_reply", _must_not_run)

        result = model.generate(LOOKUP, connector_namespaces=["efris"], locale="en")  # nosemgrep: ura-llm01-raw-user-input-to-llm -- ChatModel.generate applies InputGuard before inference.

        assert "demo data" in result["reply"]
        assert "Invoice Audit" not in result["reply"]
        assert result["retrieval_mode"] == "agentic"
        assert len(agent.calls) == 1
        assert agent.calls[0]["forced_operations"] == ["efris_taxpayer_status"]
        assert agent.calls[0]["passages"] == [], "a lookup is answered from the service, not from passages"
        assert agent.calls[0]["connector_namespaces"] == ["efris"]

    def test_a_service_s_record_does_not_carry_the_passages_or_form_links_it_was_not_made_from(
        self, model, monkeypatch
    ):
        monkeypatch.setattr(
            service_module, "_call_llm_agentic", _agent_answering("Registered (demo data).")
        )

        result = model.generate(LOOKUP, connector_namespaces=["efris"], locale="en")  # nosemgrep: ura-llm01-raw-user-input-to-llm -- ChatModel.generate applies InputGuard before inference.

        assert result["sources"] == []
        assert result["citations"] == []
        # resources_for_turn matched EFRIS in the question and offered the
        # portal and registration forms under a record that came from a service.
        assert resources_for_turn(LOOKUP, {"connector_lookup": True}) == []
        assert resources_for_turn(LOOKUP, {}) != []

    def test_nothing_about_the_passages_can_pre_empt_the_lookup(self, model, monkeypatch):
        # Found in review: the clarification, false-premise and abstention gates
        # sat in front of the service, so a vague-looking or poorly-matched
        # lookup never reached it (and could open a ticket). None of them may
        # answer a turn the taxpayer sent to a chosen service.
        agent = _agent_answering("The sample EFRIS record lists this taxpayer as registered (demo data).")
        monkeypatch.setattr(service_module, "_call_llm_agentic", agent)
        monkeypatch.setattr(service_module, "needs_clarification", lambda *_a, **_k: "Which tax do you mean?")
        monkeypatch.setattr(
            service_module,
            "check_false_premise",
            lambda *_a, **_k: mock.Mock(is_false_premise=True, reply="That tax does not exist."),
        )
        monkeypatch.setattr(model._output_guard, "should_abstain", lambda *_a, **_k: True)

        result = model.generate(LOOKUP, connector_namespaces=["efris"], locale="en")  # nosemgrep: ura-llm01-raw-user-input-to-llm -- ChatModel.generate applies InputGuard before inference.

        assert result["retrieval_mode"] == "agentic"
        assert "demo data" in result["reply"]
        assert agent.calls

    def test_the_streaming_prelude_does_not_abstain_or_ask_back_before_the_service_is_called(
        self, model, monkeypatch
    ):
        monkeypatch.setattr(service_module, "needs_clarification", lambda *_a, **_k: "Which tax do you mean?")
        monkeypatch.setattr(
            service_module,
            "check_false_premise",
            lambda *_a, **_k: mock.Mock(is_false_premise=True, reply="That tax does not exist."),
        )
        monkeypatch.setattr(model._output_guard, "should_abstain", lambda *_a, **_k: True)

        result = model.generate_retrieval_only(
            message=LOOKUP, connector_namespaces=["efris"], locale="en", top_k=4
        )

        assert result["retrieval_mode"] not in ("abstained", "clarification", "false_premise_rejected")
        assert result["_connector_forced"] == ["efris_taxpayer_status"]

    def test_a_connector_answer_is_not_escalated_for_missing_passages(self, model, monkeypatch):
        monkeypatch.setattr(service_module, "_call_llm_agentic", _agent_answering("Registered (sample data)."))

        result = model.generate(LOOKUP, connector_namespaces=["efris"], locale="en")  # nosemgrep: ura-llm01-raw-user-input-to-llm -- ChatModel.generate applies InputGuard before inference.

        assert result.get("escalation_required") in (False, None)

    def test_a_procedural_question_still_gets_the_template_that_answers_it(self, model, monkeypatch):
        agent = _agent_answering("should not be needed", used_tool=False)
        monkeypatch.setattr(service_module, "_call_llm_agentic", agent)
        templates = mock.Mock(wraps=model._maybe_handle_fast_paths)
        monkeypatch.setattr(model, "_maybe_handle_fast_paths", templates)

        result = model.generate(PROCEDURE, connector_namespaces=["efris"], locale="en")  # nosemgrep: ura-llm01-raw-user-input-to-llm -- ChatModel.generate applies InputGuard before inference.

        assert templates.call_count >= 1, "the deterministic paths must still run for a how-to"
        assert all(not call.get("forced_operations") for call in agent.calls)
        assert result["reply"]

    def test_a_service_that_cannot_be_asked_is_reported_not_guessed_around(self, model, monkeypatch):
        monkeypatch.setattr(
            service_module, "_call_llm_agentic", _agent_answering("", used_tool=False)
        )

        result = model.generate(LOOKUP, connector_namespaces=["efris"], locale="en")  # nosemgrep: ura-llm01-raw-user-input-to-llm -- ChatModel.generate applies InputGuard before inference.

        assert "couldn't reach the service you selected" in result["reply"]
        assert "won't guess" in result["reply"]
        assert result["retrieval_mode"] != "hybrid"
        # An unreachable service is not a reason to open a ticket for a person
        # (the streaming path already said so; the synchronous one escalated
        # whenever the weak passages or an "account query" phrase said to).
        assert result["escalation_required"] is False
        assert result["response_judge"]["decision"] == "approve"

    def test_nothing_changes_without_a_selected_connector(self, model, monkeypatch):
        agent = _agent_answering("unused", used_tool=False)
        monkeypatch.setattr(service_module, "_call_llm_agentic", agent)
        templates = mock.Mock(wraps=model._maybe_handle_fast_paths)
        monkeypatch.setattr(model, "_maybe_handle_fast_paths", templates)

        model.generate(LOOKUP, locale="en")  # nosemgrep: ura-llm01-raw-user-input-to-llm -- ChatModel.generate applies InputGuard before inference.

        assert templates.call_count >= 1
        assert all(not call.get("forced_operations") for call in agent.calls)


class TestStreamingPrelude:
    def _prelude(self, model, message, namespaces):
        return model.generate_retrieval_only(
            message=message, connector_namespaces=namespaces, locale="en", top_k=4
        )

    def test_a_lookup_is_flagged_for_a_forced_call_and_skips_the_templates(self, model, monkeypatch):
        monkeypatch.setattr(model, "_maybe_handle_fast_paths", _must_not_run)
        monkeypatch.setattr(model, "_deterministic_procedure_reply", _must_not_run)

        result = self._prelude(model, LOOKUP, ["efris"])

        assert result["_connector_forced"] == ["efris_taxpayer_status"]
        assert not result.get("_short_circuit")

    def test_the_first_frame_does_not_offer_passages_as_the_answers_sources(self, monkeypatch):
        monkeypatch.setenv("FLAG_ENTERPRISE_CONNECTORS", "true")
        monkeypatch.setattr(service_module.llm_module, "is_available", lambda: True)
        monkeypatch.setattr(service_module, "_call_llm_agentic", _agent_answering("Registered (demo data)."))

        model_double = mock.MagicMock()
        model_double.generate_retrieval_only.return_value = {
            "reply": "",
            "retrieval_mode": "hybrid",
            "locale": "en",
            "sources": [{"id": "faq-1"}],
            "citations": [{"id": 1, "source": "faq-1"}],
            "_hits": [{"text": "x", "source": "faq-1"}],
            "_history": [],
            "_rewritten": LOOKUP,
            "conversation_id": "c-1",
            "_connector_forced": ["efris_taxpayer_status"],
        }

        async def run():
            return [
                event
                async for event in service_module.run_chat_turn(
                    model_double,
                    message=LOOKUP,
                    conversation_id=None,
                    top_k=4,
                    locale="en",
                    session_id=None,
                    request_id=None,
                    user_id=None,
                    tenant_id="default",
                    connector_namespaces=["efris"],
                )
            ]

        events = asyncio.run(run())
        metadata = next(payload for kind, payload in events if kind == "metadata")

        assert metadata["sources"] == []
        assert metadata["citations"] == []
        assert metadata["resources"] == []

    def test_a_how_to_is_not_flagged(self, model):
        result = self._prelude(model, PROCEDURE, ["efris"])

        # The template answered it, so the turn short-circuits: nothing is forced.
        assert not result.get("_connector_forced")

    def test_no_selection_means_no_forced_call(self, model):
        assert not self._prelude(model, LOOKUP, None).get("_connector_forced")


class TestStreamedTurn:
    def _events(self, **result_fields):
        result = {
            "reply": "",
            "retrieval_mode": "hybrid",
            "locale": "en",
            "_hits": [],
            "_history": [],
            "_rewritten": LOOKUP,
            "conversation_id": "c-1",
            **result_fields,
        }
        model_double = mock.MagicMock()
        model_double.generate_retrieval_only.return_value = result

        async def run():
            return [
                event
                async for event in service_module.run_chat_turn(
                    model_double,
                    message=LOOKUP,
                    conversation_id=None,
                    top_k=4,
                    locale="en",
                    session_id=None,
                    request_id=None,
                    user_id=None,
                    tenant_id="default",
                    connector_namespaces=["efris"],
                )
            ]

        return asyncio.run(run()), model_double

    def test_the_forced_operations_reach_the_agent_and_the_answer_is_not_escalated(self, monkeypatch):
        agent = _agent_answering("The sample EFRIS record lists this taxpayer as registered (demo data).")
        monkeypatch.setattr(service_module, "_call_llm_agentic", agent)
        monkeypatch.setenv("FLAG_ENTERPRISE_CONNECTORS", "true")
        monkeypatch.setattr(service_module.llm_module, "is_available", lambda: True)

        events, _ = self._events(_connector_forced=["efris_taxpayer_status"])

        assert agent.calls
        assert agent.calls[0]["forced_operations"] == ["efris_taxpayer_status"]
        assert agent.calls[0]["passages"] == []
        tokens = "".join(payload for kind, payload in events if kind == "token")
        assert "demo data" in tokens
        grounding = next(payload for kind, payload in events if kind == "grounding")
        assert grounding["escalation_required"] is False
        assert grounding["response_judge"]["decision"] == "approve"

    def test_an_unreachable_service_is_reported_in_the_stream(self, monkeypatch):
        monkeypatch.setattr(
            service_module, "_call_llm_agentic", _agent_answering("", used_tool=False)
        )
        monkeypatch.setenv("FLAG_ENTERPRISE_CONNECTORS", "true")
        monkeypatch.setattr(service_module.llm_module, "is_available", lambda: True)

        events, _ = self._events(_connector_forced=["efris_taxpayer_status"])

        tokens = "".join(payload for kind, payload in events if kind == "token")
        assert CONNECTOR_UNAVAILABLE_REPLY.split(".")[0] in tokens
        grounding = next(payload for kind, payload in events if kind == "grounding")
        assert grounding["escalation_required"] is False

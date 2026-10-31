"""The supervisor's decision must bind every transport, not merely be logged.

Regression for G309. ``elif tiering_enabled:`` was inserted above the route
handling on the REST path in #599, so with ``agentic_mode`` on (the default)
the decision was computed, traced and then ignored: an explicit request for a
person got a retrieval answer and no ticket. With ``agentic_mode`` off and
``model_tiering`` on the same block dereferenced ``None``.

These tests retrieve a passage on purpose. With an empty index the turn
abstained and opened a ticket by another route, which is why the regression
survived the suite for twelve days.
"""

from __future__ import annotations

import pytest


class _RetrieverWithHits:
    """A ready retriever that always finds a plausible, high-confidence passage."""

    is_ready = True
    _ready = True
    _reranker = None

    def initialize(self) -> bool:
        return True

    @staticmethod
    def _hit() -> dict:
        return {
            "text": "URA officers can be reached through the contact centre on 0800 117 000.",
            "answer": "URA officers can be reached through the contact centre on 0800 117 000.",
            "question": "How do I talk to URA?",
            "source": "ura_faq.csv",
            "chunk_id": "c1",
            "page": "",
            "section": "Contact",
            "doc_type": "csv",
            "score_norm": 0.92,
            "score_rrf": 0.03,
        }

    def search(self, *_args, **_kwargs) -> list[dict]:
        return [self._hit()]

    def search_planned(self, *_args, **_kwargs) -> list[dict]:
        return [self._hit()]


@pytest.fixture
def model(tmp_db, fresh_registry, monkeypatch):
    # Flags go through the environment: test_integration.py reloads app.flags,
    # so flags.set() can land on a different object from the one service.py holds.
    from app.service import ChatModel

    monkeypatch.setenv("FLAG_TICKET_QUEUE", "true")
    instance = ChatModel()
    instance._retriever = _RetrieverWithHits()
    instance._retriever_ready = True
    return instance


@pytest.mark.parametrize(
    "message",
    [
        "I want to speak to a human officer",
        "I need to talk to someone at URA about my case",
    ],
)
def test_rest_honours_the_escalate_route_when_passages_exist(model, message):
    result = model.generate(message)  # nosemgrep: ura-llm01-raw-user-input-to-llm

    assert result["retrieval_mode"] == "escalated"
    assert result["escalation_required"] is True
    assert result["agent_role"] == "escalation_triage"
    assert result["ticket_id"], "a taxpayer who asked for a person must get a ticket"


def test_tiering_without_agentic_mode_does_not_dereference_a_missing_route(model, monkeypatch):
    from app.service import flags as service_flags

    monkeypatch.setenv("FLAG_AGENTIC_MODE", "false")
    monkeypatch.setenv("FLAG_MODEL_TIERING", "true")
    assert service_flags.is_enabled("agentic_mode") is False

    result = model.generate("What should a new importer know?")  # nosemgrep: ura-llm01-raw-user-input-to-llm

    assert result["reply"]


def test_a_tool_route_reaches_the_agentic_path_with_its_whitelist(model, monkeypatch):
    from app import service
    from app.agents.state import AgentRoute, RouteDecision
    from app.agents.supervisor import supervisor

    route = RouteDecision(
        route=AgentRoute.CUSTOMS_SPECIALIST,
        reason="customs vocabulary (1 match(es))",
        confidence=0.78,
        suggested_tools=["calculate_customs_duty", "search_ura_knowledge_base"],
    )
    monkeypatch.setattr(supervisor, "classify", lambda *_a, **_k: route)
    model._llm_available = True
    seen: dict = {}

    def spy(**kwargs):
        seen.update(kwargs)
        return {"text": "", "tool_calls": [], "iterations": 0}

    monkeypatch.setattr(service, "_call_llm_agentic", spy)

    model.generate("What are the customs clearance requirements for export?")  # nosemgrep: ura-llm01-raw-user-input-to-llm

    assert seen, "the specialist route must force the agentic path on REST"
    assert seen["tool_names"] == ["calculate_customs_duty", "search_ura_knowledge_base"]
    assert seen["agent_role"] == "customs_specialist"


@pytest.mark.parametrize(
    "message",
    [
        "This is unacceptable, connect me to a supervisor now",
        "transfer me to a person please",
        "Please put me through to an officer",
        "connect me with a representative",
        "can I talk to a manager",
    ],
)
def test_handoff_phrasing_escalates_rather_than_routing_on_a_stray_word(message):
    # "now" used to win: the temporal table fired and the taxpayer was offered
    # today's date. An explicit handoff outranks every other reading.
    from app.agents.state import AgentRoute
    from app.agents.supervisor import supervisor

    assert supervisor.classify(message).route == AgentRoute.ESCALATE


@pytest.mark.parametrize(
    "message",
    [
        "Who is the contact person for customs bonds?",
        "How do I connect my EFRIS device to the portal?",
        "Can a clearing agent transfer goods to a bonded warehouse?",
    ],
)
def test_handoff_vocabulary_does_not_capture_ordinary_questions(message):
    from app.agents.state import AgentRoute
    from app.agents.supervisor import supervisor

    assert supervisor.classify(message).route != AgentRoute.ESCALATE

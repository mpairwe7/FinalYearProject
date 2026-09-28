"""Pipeline-level cover for the guided-journey and emotional-intelligence fixes.

Sibling of ``test_answer_integrity_integration.py``, here for the same reason:
CI runs this directory but measures ``App/backend``. Each case reproduces a
finding from the journey probes run against the local GPU stack on 2026-09-29
(``docs/runbooks/guided-journey-probes.md``) through the real FastAPI app:

* "How do I file my return?" opened an officer ticket instead of answering;
* no answer ever offered the step-by-step guide that already existed;
* every "Help me …" request opened with "I understand this can feel stressful";
* a message about self-harm had no safety net;
* Tax Clearance and vehicle registration had no guided journey at all.
"""

from __future__ import annotations

import asyncio
import os
import sys
import types
import uuid
from pathlib import Path
from unittest import mock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "App" / "backend"))

os.environ.setdefault("LLM_ENABLED", "false")
os.environ.setdefault("CACHE_BACKEND", "memory")
os.environ.setdefault("ANALYTICS_BACKEND", "sqlite")
os.environ.setdefault("OTEL_ENABLED", "false")
os.environ.setdefault("QDRANT_URL", "http://127.0.0.1:1")
os.environ.setdefault("QDRANT_ENABLED", "false")
os.environ.setdefault("SPEECH_ENABLED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from App.backend.app import database  # noqa: E402
from App.backend.app import service as service_module  # noqa: E402
from App.backend.app.main import app  # noqa: E402

GUIDE_RETURN_FILING = "Guide me step by step through Return Filing"


@pytest.fixture(scope="module")
def client():
    database.init_db()
    app.state.model = service_module.ChatModel()
    app.state.speech = types.SimpleNamespace(enabled=False)
    return TestClient(app)


def _chat(client, message: str) -> dict:
    # A fresh conversation per case: the analytics DB persists between runs.
    response = client.post(
        "/v1/chat", json={"message": message, "conversation_id": f"conv-gj-{uuid.uuid4().hex[:12]}"}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_a_crisis_message_gets_crisis_lines_and_no_tax_answer(client):
    body = _chat(client, "I can't pay this tax, I want to kill myself")
    assert body["retrieval_mode"] == "crisis_support"
    assert "999" in body["reply"]
    assert "0800 21 21 21" in body["reply"]
    assert "Talk to an officer" in body["next_actions"]


def test_a_how_to_question_about_my_return_is_answered_not_ticketed(client):
    body = _chat(client, "How do I file my return?")
    assert body["retrieval_mode"] != "escalated"
    assert not body.get("escalation_required")
    assert GUIDE_RETURN_FILING in body["next_actions"]


def test_the_guide_offer_starts_the_flow_when_clicked(client):
    body = _chat(client, GUIDE_RETURN_FILING)
    assert body["retrieval_mode"] == "workflow"
    assert body["workflow"]["name"] == "Return Filing"


def test_a_help_me_request_gets_no_stress_opener(client):
    body = _chat(client, "Help me register for a TIN")
    assert body["retrieval_mode"] == "workflow"
    assert "stressful" not in body["reply"]


@pytest.mark.parametrize(
    ("message", "flow"),
    [
        ("Walk me through filing my VAT return", "Return Filing"),
        ("Guide me through getting a tax clearance certificate", "Tax Clearance Certificate"),
        ("Guide me through registering my imported car", "Motor Vehicle Registration"),
    ],
)
def test_task_requests_reach_their_journey(client, message, flow):
    body = _chat(client, message)
    assert body["retrieval_mode"] == "workflow", body["reply"][:200]
    assert body["workflow"]["name"] == flow


def test_a_stated_need_is_answered_with_the_journey_one_tap_away(client):
    # "I need …" reads as a question at the flow entrance (G39), so it is
    # answered rather than captured — and the answer now offers the journey.
    body = _chat(client, "I need a tax clearance certificate")
    assert body["retrieval_mode"] != "workflow"
    assert "Guide me step by step through Tax Clearance Certificate" in body["next_actions"]


def test_a_feeling_only_turn_is_repaired_not_retrieved(client):
    # On the local stack "It still does not work" retrieved a passage about
    # URA's own funding problems. It now gets a clarifying question, and the
    # second time the officer comes first.
    conversation_id = f"conv-gj-{uuid.uuid4().hex[:12]}"
    first = client.post("/v1/chat", json={"message": "This is useless", "conversation_id": conversation_id}).json()
    assert first["retrieval_mode"] == "clarification"
    assert first["agent_role"] == "conversation_repair"
    assert "File a return" in first["next_actions"]
    second = client.post(
        "/v1/chat", json={"message": "It still does not work", "conversation_id": conversation_id}
    ).json()
    assert second["retrieval_mode"] == "clarification"
    assert second["next_actions"][0] == "Talk to an officer"


def test_journey_funnel_events_are_counted(client):
    # The funnel the CX team reads on /metrics: where journeys start, which
    # step each one reached, and where they were abandoned.
    def key(event: str, step: str) -> str:
        return f'journey_events_total{{event="{event}",step="{step}",workflow="return_filing"}}'

    before = dict(service_module.metrics.snapshot()["counters"])
    conversation_id = f"conv-gj-{uuid.uuid4().hex[:12]}"
    client.post("/v1/chat", json={"message": "Help me file my return", "conversation_id": conversation_id})
    client.post("/v1/chat", json={"message": "cancel", "conversation_id": conversation_id})
    after = service_module.metrics.snapshot()["counters"]

    def delta(counter: str) -> int:
        return after.get(counter, 0) - before.get(counter, 0)

    assert delta(key("started", "")) == 1
    assert delta(key("step_entered", "collect_taxpayer_type")) == 1
    assert delta(key("cancelled", "collect_taxpayer_type")) == 1


def test_asking_for_another_flow_inside_one_switches_to_it(client):
    # The Tax Clearance checklist tells the taxpayer to say "help me file my
    # return"; inside the flow that used to be validated as a yes/no answer.
    conversation_id = f"conv-gj-{uuid.uuid4().hex[:12]}"
    first = client.post(
        "/v1/chat",
        json={"message": "Guide me through getting a tax clearance certificate", "conversation_id": conversation_id},
    ).json()
    assert first["workflow"]["name"] == "Tax Clearance Certificate"
    switched = client.post(
        "/v1/chat", json={"message": "help me file my return", "conversation_id": conversation_id}
    ).json()
    assert switched["retrieval_mode"] == "workflow"
    assert switched["workflow"]["name"] == "Return Filing"


def test_i_dont_understand_after_a_real_question_is_not_restarted(client):
    conversation_id = f"conv-gj-{uuid.uuid4().hex[:12]}"
    client.post("/v1/chat", json={"message": "What is chargeable income?", "conversation_id": conversation_id})
    follow_up = client.post(
        "/v1/chat", json={"message": "I don't understand", "conversation_id": conversation_id}
    ).json()
    assert follow_up.get("agent_role") != "conversation_repair"


def test_resume_does_not_count_a_step_twice(client):
    step_key = 'journey_events_total{event="step_entered",step="collect_taxpayer_type",workflow="return_filing"}'
    before = service_module.metrics.snapshot()["counters"].get(step_key, 0)
    conversation_id = f"conv-gj-{uuid.uuid4().hex[:12]}"
    client.post("/v1/chat", json={"message": "Help me file my return", "conversation_id": conversation_id})
    client.post("/v1/chat", json={"message": "resume", "conversation_id": conversation_id})
    after = service_module.metrics.snapshot()["counters"].get(step_key, 0)
    assert after - before == 1


def test_the_streaming_abstention_neither_repeats_the_opener_nor_competes_with_a_handoff(client):
    from App.backend.app.text_signals import empathy_ack  # noqa: PLC0415
    from App.backend.app.turn_guidance import GUIDE_PREFIX, HANDOFF_ACTION  # noqa: PLC0415

    ack = empathy_ack("anxiety")
    model = mock.MagicMock()
    model.generate_retrieval_only.return_value = {
        "reply": "",
        "retrieval_mode": "hybrid",
        "locale": "en",
        "next_actions": [],
        "_hits": [],
        "_distress": "anxiety",
        "_history": [{"user_message": "I'm worried about my return", "bot_reply": f"{ack}\n\nFirst answer."}],
    }
    model._maybe_create_ticket.return_value = ""
    model._build_handoff_packet.return_value = None

    async def _run():
        return [
            event
            async for event in service_module.run_chat_turn(
                model,
                message="I'm still worried, how do I file my return?",
                conversation_id=None,
                top_k=4,
                locale="en",
                session_id=None,
                request_id=None,
                user_id=None,
                tenant_id="default",
            )
        ]

    events = asyncio.run(_run())
    reply = "".join(payload for kind, payload in events if kind == "token")
    grounding = next(payload for kind, payload in events if kind == "grounding")
    assert not reply.startswith(ack)
    if grounding["escalation_required"]:
        assert not any(a.startswith(GUIDE_PREFIX) or a == HANDOFF_ACTION for a in grounding["next_actions"])


def test_the_streaming_core_applies_the_same_guidance(client):
    # ``client`` builds the real ChatModel, which loads the workflow flows the
    # guided-mode offer is matched against.
    model = mock.MagicMock()
    model.generate_retrieval_only.return_value = {
        "reply": "Log in to eTax, open e-Returns and upload the validated file.",
        "retrieval_mode": "faq_priority",
        "locale": "en",
        "next_actions": [],
        "_hits": [],
        "_short_circuit": True,
    }

    async def _run():
        return [
            event
            async for event in service_module.run_chat_turn(
                model,
                message="How do I file my return?",
                conversation_id=None,
                top_k=4,
                locale="en",
                session_id=None,
                request_id=None,
                user_id=None,
                tenant_id="default",
            )
        ]

    events = asyncio.run(_run())
    grounding = next(payload for kind, payload in events if kind == "grounding")
    assert GUIDE_RETURN_FILING in grounding["next_actions"]

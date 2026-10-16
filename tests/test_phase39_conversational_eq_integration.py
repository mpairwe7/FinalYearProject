"""Phase 39: conversational and emotional robustness, through the real pipeline.

Each case reproduces a gap from the 2026-10-16 QA audit
(``docs/plans/conversational-eq-phase39-plan.md``) through the FastAPI app with
the real ChatModel (no LLM), so the wiring is covered, not only the helpers:

* a taxpayer who wrote that URA froze their account, in the middle of a guided
  return, was told "Please choose one of: …" and asked the same step again;
* a step answered wrongly again and again looped with no way out;
* "I can't cope anymore", said after "I can't afford the penalty", was
  answered as a tax question — the indirect disclosure crisis studies find
  chatbots miss;
* a crisis reply to "…because of my VAT penalty" carried VAT form cards and a
  filing-deadline nudge.

Flags are set through the environment, which is read on every call.
"""

from __future__ import annotations

import json
import os
import sys
import types
import uuid
from pathlib import Path

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
from App.backend.app.main import app, limiter  # noqa: E402

HANDOFF = "Talk to an officer"


@pytest.fixture(autouse=True)
def _fresh_rate_window():
    """Every case starts a fresh limiter window: the suite's earlier /v1/chat
    calls share the test client's address and its 30/minute budget."""
    limiter.reset()


@pytest.fixture(scope="module")
def client():
    database.init_db()
    app.state.model = service_module.ChatModel()
    app.state.speech = types.SimpleNamespace(enabled=False)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _phase39(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both Phase 39 flags on; a test that needs one off sets it after this runs."""
    monkeypatch.setenv("FLAG_WORKFLOWS", "true")
    monkeypatch.setenv("FLAG_WORKFLOW_EMOTIONAL_OFFRAMP", "true")
    monkeypatch.setenv("FLAG_WELLBEING_CHECKIN", "true")


class _Conversation:
    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.id = f"conv-p39-{uuid.uuid4().hex[:12]}"

    def say(self, message: str) -> dict:
        response = self.client.post("/v1/chat", json={"message": message, "conversation_id": self.id})
        assert response.status_code == 200, response.text
        return response.json()


def _at_tax_type_step(client: TestClient) -> _Conversation:
    conversation = _Conversation(client)
    started = conversation.say("Help me file my return")
    assert started["workflow"]["name"] == "Return Filing"
    asked = conversation.say("company")
    assert "Which return are you filing" in asked["reply"]
    return conversation


class TestGuidedFlowsListen:
    def test_enforcement_news_pauses_the_flow_instead_of_a_validation_error(self, client) -> None:
        conversation = _at_tax_type_step(client)
        # Not a question, so it is not diverted to retrieval as a change of subject
        # ("…issued an agency notice" is, and is answered from the rate table).
        paused = conversation.say("URA closed my shop and I cannot pay my workers")
        # The reply formatter bolds a lead sentence; the words are what matter.
        assert paused["reply"].replace("**", "").startswith("That matters more than this form.")
        assert "Please choose one of" not in paused["reply"]
        assert paused["next_actions"][0] == HANDOFF
        assert "Continue the steps" in paused["next_actions"]
        assert paused["workflow"]["paused"] is True
        assert paused["workflow"]["options"] == []
        # Nothing was stored and nothing was lost: the same step comes back.
        resumed = conversation.say("Continue the steps")
        assert "Which return are you filing" in resumed["reply"]

    def test_a_real_answer_is_never_paused(self, client) -> None:
        conversation = _at_tax_type_step(client)
        answered = conversation.say("VAT")
        assert "Which filing period" in answered["reply"]
        assert not answered["workflow"].get("paused")

    def test_three_wrong_answers_offer_a_way_out(self, client) -> None:
        conversation = _at_tax_type_step(client)
        first = conversation.say("banana")
        assert first["reply"].replace("**", "").startswith("Please choose one of")
        second = conversation.say("pineapple")
        assert second["next_actions"] == [HANDOFF, "Cancel"]
        third = conversation.say("mango")
        assert third["reply"].replace("**", "").startswith("This step may not fit your situation.")
        assert third["next_actions"] == [HANDOFF, "Cancel"]
        # A right answer clears the count: the next step starts fresh.
        recovered = conversation.say("VAT")
        assert "Which filing period" in recovered["reply"]

    def test_without_the_flag_the_flow_behaves_as_before(self, client, monkeypatch) -> None:
        monkeypatch.setenv("FLAG_WORKFLOWS", "true")
        monkeypatch.setenv("FLAG_WORKFLOW_EMOTIONAL_OFFRAMP", "false")
        conversation = _at_tax_type_step(client)
        reply = conversation.say("URA closed my shop and I cannot pay my workers")
        # On dev this statement leaves the flow for retrieval (the change-of-subject rule).
        assert not (reply.get("workflow") or {}).get("paused")


class TestCheckInBeforeTheTaxAnswer:
    def test_hopelessness_after_hardship_gets_one_check_in(self, client) -> None:
        conversation = _Conversation(client)
        conversation.say("I can't afford the penalty URA charged me")
        checked = conversation.say("I can't cope anymore")
        assert checked["retrieval_mode"] == "wellbeing_checkin"
        assert "0800 21 21 21" in checked["reply"]
        assert "999" in checked["reply"]
        assert checked["next_actions"][0] == HANDOFF
        assert "UGX" not in checked["reply"]
        # Once per conversation: the second time it would read as a script.
        again = conversation.say("I can't cope anymore")
        assert again["retrieval_mode"] != "wellbeing_checkin"

    def test_hopelessness_about_a_portal_is_not_a_check_in(self, client) -> None:
        reply = _Conversation(client).say("This portal is hopeless, I give up")
        assert reply["retrieval_mode"] not in ("wellbeing_checkin", "crisis_support")

    def test_the_streaming_path_checks_in_too(self, client) -> None:
        conversation = _Conversation(client)
        conversation.say("URA froze my bank account")
        modes = []
        with client.stream(
            "POST",
            "/v1/chat/stream",
            json={"message": "I give up, there is no way out", "conversation_id": conversation.id},
        ) as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                if line.startswith("data:"):
                    try:
                        payload = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if isinstance(payload, dict) and payload.get("retrieval_mode"):
                        modes.append(payload["retrieval_mode"])
        assert "wellbeing_checkin" in modes


class TestSafetyRepliesCarryNoTaxContent:
    def test_passive_ideation_reaches_the_crisis_path(self, client) -> None:
        reply = _Conversation(client).say("Since URA closed my shop I have nothing to live for")
        assert reply["retrieval_mode"] == "crisis_support"

    def test_a_crisis_reply_carries_no_forms_and_no_deadline_nudge(self, client) -> None:
        reply = _Conversation(client).say("I want to die because of my VAT penalty")
        assert reply["retrieval_mode"] == "crisis_support"
        assert reply.get("resources") in (None, [])
        assert not any("deadline" in str(action).lower() for action in reply["next_actions"])

"""The officer's brief: parsing, evidence, redaction, model routing, and keeping it rolling."""

from __future__ import annotations

import asyncio
import json
import unittest
import uuid
from unittest.mock import patch

from starlette.testclient import TestClient

from app import database as db
from app.auth.jwt_auth import make_dev_token
from app.receptionist import brief as call_brief
from app.receptionist.hub import hub
from app.receptionist.state import registry
from app.receptionist.store import create_call, create_turn, get_call, init_receptionist_schema, update_call

VALID = {1, 2, 3}

MODEL_BRIEF = {
    "why_officer": {"text": "Needs account access the AI does not have", "turn_seqs": [3, 99]},
    "caller_goal": {"text": "Check an August VAT refund", "turn_seqs": [1]},
    "ai_already_said": [{"text": "Refunds take up to 30 days", "turn_seqs": [2]}],
    "still_open": ["Refund not visible on the portal"],
    "details_given": [{"label": "TIN", "value": "1000123456", "turn_seqs": [1]}],
    "sentiment": "Frustrated",
    "topic": "VAT refund",
    "priority": "HIGH",
    "suggested_opener": "I can see you've been waiting on your August VAT refund.",
}


class ParseBriefTests(unittest.TestCase):
    def test_a_fenced_brief_is_read(self):
        brief = call_brief.parse_brief("```json\n" + json.dumps(MODEL_BRIEF) + "\n```", VALID)
        self.assertEqual(brief["caller_goal"], {"text": "Check an August VAT refund", "turn_seqs": [1]})
        self.assertEqual((brief["sentiment"], brief["priority"], brief["topic"]), ("frustrated", "high", "VAT refund"))

    def test_evidence_pointing_nowhere_is_dropped(self):
        brief = call_brief.parse_brief(json.dumps(MODEL_BRIEF), VALID)
        self.assertEqual(brief["why_officer"]["turn_seqs"], [3])  # 99 is not a turn
        self.assertEqual(brief["still_open"], [{"text": "Refund not visible on the portal", "turn_seqs": []}])

    def test_a_detail_s_value_is_never_repeated(self):
        brief = call_brief.parse_brief(json.dumps(MODEL_BRIEF), VALID)
        self.assertEqual(brief["details_given"], [{"label": "TIN", "value": "given (redacted)", "turn_seqs": [1]}])
        self.assertNotIn("1000123456", json.dumps(brief))

    def test_identifiers_the_model_writes_in_prose_are_redacted(self):
        leaky = {**MODEL_BRIEF, "caller_goal": {"text": "Caller 0772123456 wants a refund", "turn_seqs": [1]}}
        brief = call_brief.parse_brief(json.dumps(leaky), VALID)
        self.assertNotIn("0772123456", brief["caller_goal"]["text"])

    def test_no_caller_goal_is_no_brief(self):
        self.assertIsNone(call_brief.parse_brief(json.dumps({"topic": "VAT"}), VALID))
        self.assertIsNone(call_brief.parse_brief("I cannot help with that.", VALID))


class BuildBriefTests(unittest.TestCase):
    def setUp(self) -> None:
        db.init_db()
        init_receptionist_schema()
        self.call_id = f"call_brief_{uuid.uuid4().hex[:8]}"
        create_call(self.call_id, conversation_id=f"conv_{self.call_id}")
        create_turn(self.call_id, 1, "caller", "utterance", "My VAT refund from August has not come")
        create_turn(self.call_id, 2, "assistant", "answer", "Refunds take up to 30 days.")
        create_turn(self.call_id, 3, "caller", "utterance", "It has been 45 days, I need a person")

    def reply(self, calls: list[str], raw: str = json.dumps(MODEL_BRIEF)):
        def generate(prompt: str, language: str):
            calls.append(prompt)
            return {"raw": raw}, "gemini-2.5-flash-lite"
        return generate

    def test_a_full_brief_is_built_and_stored(self):
        prompts: list[str] = []
        with patch.object(call_brief, "_generate", self.reply(prompts)):
            brief = call_brief.build_brief(self.call_id)
        self.assertEqual((brief["turns_covered"], brief["fallback"], brief["model"]), (3, False, "gemini-2.5-flash-lite"))
        self.assertIn("Transcript of the call so far", prompts[0])
        self.assertEqual(get_call(self.call_id)["brief"]["caller_goal"]["text"], "Check an August VAT refund")

    def test_new_turns_update_the_previous_brief(self):
        prompts: list[str] = []
        with patch.object(call_brief, "_generate", self.reply(prompts)):
            call_brief.build_brief(self.call_id)
            create_turn(self.call_id, 4, "caller", "utterance", "Can you check it now?")
            brief = call_brief.build_brief(self.call_id)
        self.assertIn("Your brief so far, covering the call up to turn 3", prompts[1])
        self.assertIn("4 caller: Can you check it now?", prompts[1])
        self.assertNotIn("1 caller:", prompts[1])  # only what is new
        self.assertEqual(brief["turns_covered"], 4)

    def test_nothing_new_costs_no_model_call_unless_forced(self):
        prompts: list[str] = []
        with patch.object(call_brief, "_generate", self.reply(prompts)):
            call_brief.build_brief(self.call_id)
            call_brief.build_brief(self.call_id)
            self.assertEqual(len(prompts), 1)
            call_brief.build_brief(self.call_id, force=True)
        self.assertEqual(len(prompts), 2)
        self.assertIn("Transcript of the call so far", prompts[1])

    def test_no_model_answering_gives_a_deterministic_brief(self):
        update_call(self.call_id, status="transferring", transferred=True, transfer_reason="caller_requested",
                    topic="general_tax_support", priority="high")
        with patch.object(call_brief, "_generate", return_value=(None, "")):
            brief = call_brief.build_brief(self.call_id)
        self.assertTrue(brief["fallback"])
        self.assertEqual(brief["model"], "fallback")
        self.assertEqual(brief["caller_goal"], {"text": "My VAT refund from August has not come", "turn_seqs": [1]})
        self.assertEqual(brief["why_officer"]["turn_seqs"], [3])
        self.assertEqual((brief["topic"], brief["priority"]), ("General tax support", "high"))

    def test_luganda_goes_to_sunflower_first_and_the_rest_to_gemini(self):
        order: list[str] = []

        def gemini(*_a, **_kw):
            order.append("gemini")
            return json.dumps(MODEL_BRIEF)

        def sunflower(messages, **_kw):
            order.append("sunflower")
            self.assertEqual(messages[0]["role"], "system")  # chat messages, not a string
            return json.dumps(MODEL_BRIEF)

        with patch("app.providers.gateway.gemini_generate", gemini), patch("app.llm._vllm_generate", sunflower):
            _, model = call_brief._generate("prompt", "lg")
            self.assertEqual((order, model), (["sunflower"], "sunflower"))
            order.clear()
            _, model = call_brief._generate("prompt", "sw")
            self.assertEqual((order, model), (["gemini"], "gemini-2.5-flash-lite"))

    def test_a_failing_first_model_falls_to_the_second(self):
        def gemini(*_a, **_kw):
            raise RuntimeError("gateway down")

        with patch("app.providers.gateway.gemini_generate", gemini), \
                patch("app.llm._vllm_generate", return_value=json.dumps(MODEL_BRIEF)):
            reply, model = call_brief._generate("prompt", "en")
        self.assertEqual(model, "sunflower")
        self.assertIn("caller_goal", reply["raw"])


class RollingBriefTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        db.init_db()
        init_receptionist_schema()
        self.call_id = f"call_roll_{uuid.uuid4().hex[:8]}"
        create_call(self.call_id, conversation_id=f"conv_{self.call_id}")
        self.room = await registry.create(self.call_id, f"conv_{self.call_id}")
        self.built: list[str] = []

        def build(call_id: str, *, force: bool = False):
            self.built.append(call_id)
            return {"caller_goal": {"text": "x", "turn_seqs": []}, "topic": "VAT refund", "priority": "high"}

        patch.object(call_brief, "build_brief", build).start()
        self.lobby = patch.object(hub, "publish_lobby").start()
        self.per_call = patch.object(hub, "publish_call").start()
        self.addCleanup(patch.stopall)
        call_brief.start(self.call_id)

    async def asyncTearDown(self) -> None:
        call_brief.stop(self.call_id)
        await registry.remove(self.call_id)

    async def settle(self) -> None:
        for _ in range(5):
            await asyncio.sleep(0.02)
        scheduler = call_brief._schedulers.get(self.call_id)
        if scheduler and scheduler._task:
            await scheduler._task

    async def test_every_third_caller_turn_rebuilds_it(self):
        for seq, speaker in enumerate(["caller", "assistant", "caller", "assistant", "caller"], start=1):
            create_turn(self.call_id, seq, speaker, "utterance", "words")
        await self.settle()
        self.assertEqual(self.built, [self.call_id])
        events = [c.args[1] for c in self.per_call.call_args_list]
        self.assertEqual(events, ["brief"])

    async def test_the_lobby_hears_brief_ready_once_per_transfer_and_nothing_else(self):
        self.room.state.mode = "transferring"
        self.room.state.transfer_attempts = 1
        call_brief.build_now(self.call_id)
        await self.settle()
        call_brief.build_now(self.call_id)
        await self.settle()
        ready = [c.args[1] for c in self.lobby.call_args_list if c.args[0] == "call.brief_ready"]
        self.assertEqual(ready, [{"call_id": self.call_id, "topic": "VAT refund", "priority": "high"}])

    async def test_a_stopped_call_is_no_longer_watched(self):
        call_brief.stop(self.call_id)
        for seq in range(1, 7):
            create_turn(self.call_id, seq, "caller", "utterance", "words")
        await self.settle()
        self.assertEqual(self.built, [])


class BriefRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from app.main import app

        db.init_db()
        init_receptionist_schema()
        cls.client = TestClient(app)

    def headers(self, role: str = "ura_staff") -> dict[str, str]:
        return {"Authorization": f"Bearer {make_dev_token(f'{role}_brief', role=role)}"}

    def test_the_brief_is_served_once_written_and_building_before(self):
        call_id = f"call_br_{uuid.uuid4().hex[:8]}"
        create_call(call_id)
        r = self.client.get(f"/v1/admin/calls/{call_id}/brief", headers=self.headers())
        self.assertEqual((r.status_code, r.json()), (202, {"status": "building"}))
        update_call(call_id, brief_json={"caller_goal": {"text": "VAT refund", "turn_seqs": []}})
        r = self.client.get(f"/v1/admin/calls/{call_id}/brief", headers=self.headers("ura_auditor"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["caller_goal"]["text"], "VAT refund")

    def test_refresh_rebuilds_it(self):
        call_id = f"call_br_{uuid.uuid4().hex[:8]}"
        create_call(call_id)
        fresh = {"caller_goal": {"text": "fresh", "turn_seqs": []}}
        with patch.object(call_brief, "build_brief", return_value=fresh) as build:
            r = self.client.get(f"/v1/admin/calls/{call_id}/brief?refresh=1", headers=self.headers())
        self.assertEqual(r.json(), fresh)
        self.assertTrue(build.call_args.kwargs["force"])

    def test_unknown_and_malformed_calls(self):
        self.assertEqual(self.client.get("/v1/admin/calls/nope_nope/brief", headers=self.headers()).status_code, 404)
        self.assertEqual(self.client.get("/v1/admin/calls/bad%20id/brief", headers=self.headers()).status_code, 400)


if __name__ == "__main__":
    unittest.main()

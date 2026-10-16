"""Receptionist audit R1: safety and integrity (docs/plans/receptionist-cx-audit-2026-10-plan.md).

Each class pins one gap register row (G175–G182, G192). The defects they describe were all
reached through the receptionist's real entry points — a caller's turn, the "Talk to an
officer" button, the officer desk — so the tests go through those too.
"""

from __future__ import annotations

import asyncio
import os
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app import database as db
from app.flags import flags
from app.receptionist import desk, presence
from app.receptionist import transfer as transfer_mod
from app.receptionist.brain import UraReceptionistBrain
from app.receptionist.phrases import phrase
from app.receptionist.serializer import RequestOfficerFrame
from app.receptionist.state import CallRoom, CallState, registry
from app.receptionist.store import (
    create_call,
    get_call,
    init_receptionist_schema,
    list_turns,
    update_call,
)


def queue_on(name: str, **_kw: object) -> bool:
    return name == "ticket_queue"


def _words(text: str, prob: float = 0.95) -> list[dict]:
    return [{"word": w, "prob": prob} for w in text.split()]


class BrainCase(unittest.IsolatedAsyncioTestCase):
    """A call answered by the AI, with the ticket queue on."""

    async def asyncSetUp(self) -> None:
        # An officer is on shift: these tests are about the wait, not about nobody being
        # there to take the call (that is test_receptionist_queue_experience, G180).
        staffed = patch("app.receptionist.transfer.is_staff_available", return_value=True)
        staffed.start()
        self.addCleanup(staffed.stop)
        db.init_db()
        init_receptionist_schema()
        self.call_id = f"call_r1_{uuid.uuid4().hex[:8]}"
        create_call(self.call_id, conversation_id=f"conv_{self.call_id}", user_id="taxpayer_r1")
        self.state = CallState(call_id=self.call_id, conversation_id=f"conv_{self.call_id}",
                               user_id="taxpayer_r1", mode="ai")
        self.room = CallRoom(call_id=self.call_id, state=self.state)
        self.chat_model = MagicMock()
        self.chat_model.generate.return_value = {
            "reply": "You can register for a TIN on the URA portal.",
            "sources": ["URA Portal Guide"],
            "faithfulness_score": 0.9,
        }
        self.chat_model._build_handoff_packet.return_value = {"topic": "registration", "priority": "high"}
        self.chat_model._maybe_create_ticket.return_value = "TICK-R1-0001"
        self.brain = UraReceptionistBrain(room=self.room, chat_model=self.chat_model)
        self.brain.push_frame = AsyncMock()
        self.lobby = patch.object(transfer_mod.hub, "publish_lobby").start()
        self.call_events = patch.object(transfer_mod.hub, "publish_call").start()
        self.flags = patch.object(flags, "is_enabled", side_effect=queue_on).start()
        self.addCleanup(patch.stopall)

    async def asyncTearDown(self) -> None:
        for task in (self.state.transfer_timer_task, self.state.review_timer_task):
            if task is not None and not task.done():
                task.cancel()

    def texts(self) -> list[str]:
        return [t["text"] for t in list_turns(self.call_id)]

    def voiced(self) -> list[str]:
        return [
            getattr(c.args[0], "text", "")
            for c in self.brain.push_frame.call_args_list
            if c.args[0].__class__.__name__ == "LLMTextFrame"
        ]

    def lobby_events(self, event: str) -> list[dict]:
        return [c.args[1] for c in self.lobby.call_args_list if c.args[0] == event]

    async def say(self, text: str, prob: float = 0.95) -> None:
        await self.brain.handle_external_question(text, _words(text, prob))


class CrisisWhileWaitingTests(BrainCase):
    """G175: a caller waiting for an officer is still heard when they are in danger."""

    async def test_a_crisis_while_waiting_is_answered_and_raises_the_call_to_urgent(self):
        await self.brain.process_frame(RequestOfficerFrame())
        self.assertEqual(self.state.mode, "transferring")
        waiting_since = self.state.transfer_requested_at

        with patch.object(transfer_mod, "_update_ticket_priority") as ticket_priority:
            await self.say("I can't take this anymore, I want to end my life.")

        spoken = f"{phrase('crisis_support', 'en')} {phrase('crisis_waiting', 'en')}"
        self.assertIn(spoken, self.texts())
        self.assertIn(spoken, self.voiced())
        self.assertIn(phrase("crisis_screen", "en"), self.texts())
        self.assertNotIn(phrase("crisis_screen", "en"), self.voiced())  # numbers on screen only
        self.assertEqual(self.state.mode, "transferring")  # still on its way to an officer
        self.assertEqual(self.state.priority, "urgent")
        self.assertEqual(get_call(self.call_id)["priority"], "urgent")
        ticket_priority.assert_called_once_with("TICK-R1-0001", "urgent")
        self.assertEqual(
            self.lobby_events("call.priority_raised"),
            [{"call_id": self.call_id, "priority": "urgent", "reason": "safety_concern"}],
        )
        # Not a new wait: re-sending transfer_requested would reset the row's claim and wait.
        self.assertEqual(len(self.lobby_events("call.transfer_requested")), 1)
        self.assertEqual(self.state.transfer_requested_at, waiting_since)
        self.call_events.assert_any_call(self.call_id, "priority", {"priority": "urgent", "reason": "safety_concern"})
        self.chat_model.generate.assert_not_called()  # never a tax answer

    async def test_other_speech_while_waiting_still_waits_for_the_officer(self):
        await self.brain.process_frame(RequestOfficerFrame())
        said_before = len(self.voiced())
        await self.say("Hello? Is anyone there?")
        self.assertEqual(len(self.voiced()), said_before)
        self.assertEqual(self.state.priority, "high")
        self.chat_model.generate.assert_not_called()

    async def test_a_second_crisis_line_is_answered_without_a_second_alert(self):
        await self.brain.process_frame(RequestOfficerFrame())
        with patch.object(transfer_mod, "_update_ticket_priority"):
            await self.say("I want to end my life.")
            await self.say("I really want to kill myself.")
        self.assertEqual(self.texts().count(phrase("crisis_screen", "en")), 2)
        self.assertEqual(len(self.lobby_events("call.priority_raised")), 1)

    async def test_a_claimed_call_keeps_its_claim_when_raised(self):
        await self.brain.process_frame(RequestOfficerFrame())
        self.state.claimed_by = "officer_okello"  # an officer is joining
        with patch.object(transfer_mod, "_update_ticket_priority"):
            await self.say("I want to end my life.")
        self.assertEqual(self.state.claimed_by, "officer_okello")
        self.assertEqual(len(self.lobby_events("call.transfer_requested")), 1)  # only the original
        self.assertEqual(self.lobby_events("call.priority_raised")[-1]["priority"], "urgent")


class OfficerRequestReentryTests(BrainCase):
    """G178: "Talk to an officer" acts once, and never under an officer who has the call."""

    async def test_a_second_request_while_waiting_changes_nothing(self):
        await self.brain.process_frame(RequestOfficerFrame())
        timer = self.state.transfer_timer_task
        await self.brain.process_frame(RequestOfficerFrame())
        self.chat_model._maybe_create_ticket.assert_called_once()
        self.assertEqual(self.state.transfer_attempts, 1)
        self.assertEqual(len(self.lobby_events("call.transfer_requested")), 1)
        self.assertIs(self.state.transfer_timer_task, timer)
        self.assertFalse(timer.done())

    async def test_a_request_while_an_officer_has_the_call_changes_nothing(self):
        self.state.mode = "bridged"
        self.state.officer_id = "officer_okello"
        await self.brain.process_frame(RequestOfficerFrame())
        self.assertEqual(self.state.mode, "bridged")
        self.chat_model._maybe_create_ticket.assert_not_called()
        self.assertEqual(self.lobby_events("call.transfer_requested"), [])
        self.assertIsNone(self.state.transfer_timer_task)


class QueueEpochTests(BrainCase):
    """G179: a countdown acts only on the wait it was started for."""

    async def test_a_stale_countdown_does_not_time_out_a_later_wait(self):
        await self.brain.process_frame(RequestOfficerFrame())
        stale = self.state.queue_epoch
        transfer_mod.begin_wait(self.room)  # an officer passed the call on: a new wait
        await self.brain._transfer_timeout_countdown(0, "TICK-R1-0001", epoch=stale)
        self.assertEqual(self.state.mode, "transferring")
        self.assertNotIn(phrase("officers_busy", "en"), self.texts())

        await self.brain._transfer_timeout_countdown(0, "TICK-R1-0001")  # the current wait
        self.assertEqual(self.state.mode, "ai")
        self.assertIn(phrase("officers_busy", "en"), self.texts())

    async def test_bridging_ends_the_ai_transfer_countdown(self):
        await self.brain.process_frame(RequestOfficerFrame())
        timer = self.state.transfer_timer_task
        self.state.claimed_by = "officer_okello"
        await desk.bridge(self.room, None, "officer_okello", None)
        await asyncio.sleep(0)
        self.assertTrue(timer.cancelled())

    async def test_start_queue_countdown_replaces_an_earlier_one(self):
        await self.brain.process_frame(RequestOfficerFrame())
        first = self.state.transfer_timer_task
        self.brain.start_queue_countdown("TICK-R1-0001")
        await asyncio.sleep(0)
        self.assertTrue(first.cancelled())
        self.assertIsNot(self.state.transfer_timer_task, first)


class RequeueKeepsTopicAndPriorityTests(BrainCase):
    """G192: a call an officer passes on keeps its topic and priority in the queue."""

    async def test_an_officer_transfer_republishes_the_calls_topic_and_priority(self):
        await self.brain.process_frame(RequestOfficerFrame())
        self.assertEqual((self.state.topic, self.state.priority), ("registration", "high"))
        registry._rooms[self.call_id] = self.room
        self.addCleanup(registry._rooms.pop, self.call_id, None)
        self.state.claimed_by = "officer_okello"
        await desk.bridge(self.room, None, "officer_okello", None)
        self.room.brain = MagicMock()
        with patch.object(desk.hub, "publish_lobby") as desk_lobby:
            await desk.transfer(self.call_id, "officer_okello", team="disputes")
        event = next(c.args[1] for c in desk_lobby.call_args_list if c.args[0] == "call.transfer_requested")
        self.assertEqual((event["topic"], event["priority"]), ("registration", "high"))
        self.room.brain.start_queue_countdown.assert_called_once_with("TICK-R1-0001")


class SpokenConfirmationFailsClosedTests(BrainCase):
    """G177: a confirmed action is said to be done only when something did it."""

    def _arm(self, **action: object) -> None:
        self.flags.side_effect = lambda name, **_kw: name in ("ticket_queue", "voice_agentic_workflows")
        self.brain._take_offer()  # the confirmation is the one open question
        self.state.pending_confirm_challenge = "chal-r1"
        self.state.pending_confirm_action = {"description": "TIN registration submission", **action}

    async def test_without_an_executor_nothing_is_said_to_be_completed(self):
        self._arm()
        await self.say("Yes, confirm")
        self.assertEqual(self.texts()[-1], phrase("confirm_not_done", "en"))
        self.assertEqual(self.state.pending_offer, "officer")
        self.assertEqual(self.state.pending_confirm_challenge, "")
        self.assertFalse(any("completed" in t.lower() for t in self.texts()))

    async def test_an_executor_that_succeeds_completes_it(self):
        execute = MagicMock(return_value={"ok": True})
        self._arm(execute=execute)
        await self.say("Yes, confirm")
        execute.assert_called_once_with()
        self.assertEqual(self.texts()[-1], phrase("confirm_done", "en"))
        self.assertEqual(self.state.pending_offer, "")

    async def test_an_executor_that_fails_offers_an_officer(self):
        failures = (
            MagicMock(return_value={"ok": False}),
            MagicMock(side_effect=RuntimeError("down")),
            MagicMock(return_value=None),
            MagicMock(return_value="done"),  # truthy, but not a success the executor reported
        )
        for execute in failures:
            self._arm(execute=execute)
            await self.say("Yes, confirm")
            self.assertEqual(self.texts()[-1], phrase("confirm_not_done", "en"), execute)
            self.assertEqual(self.state.pending_offer, "officer", execute)

    async def test_a_doubtful_yes_is_asked_again_in_the_call_language(self):
        self._arm()
        self.state.locale = "sw"
        await self.say("Ndiyo", prob=0.5)
        self.assertEqual(self.texts()[-1], phrase("confirm_reprompt", "sw"))
        self.assertEqual(self.state.pending_confirm_challenge, "chal-r1")

    def test_no_confirmation_line_carries_an_aside(self):
        for language in ("en", "lg", "sw", "nyn", "ach"):
            for key in ("confirm_reprompt", "confirm_done", "confirm_not_done", "crisis_waiting"):
                self.assertNotIn("(", phrase(key, language), (language, key))


class GroundingNotHearingTests(BrainCase):
    """G181: whether to offer an officer depends on the answer's grounding, not on how well the caller was heard."""

    async def test_a_poorly_heard_caller_with_a_grounded_answer_is_not_offered_an_officer(self):
        # Function words heard poorly, the content words clearly: the clarify gate has nothing
        # to ask, and the turn's mean word probability (0.4) falls in the old "uncertain" band.
        heard = [("How", 0.2), ("do", 0.2), ("I", 0.2), ("register", 0.9), ("for", 0.2), ("a", 0.2), ("TIN?", 0.9)]
        await self.brain.handle_external_question(
            "How do I register for a TIN?", [{"word": w, "prob": p} for w, p in heard]
        )
        self.assertIn("register for a TIN", self.texts()[-1])
        self.assertNotIn(phrase("officer_offer", "en"), self.texts()[-1])
        self.assertNotEqual(self.state.pending_offer, "officer")
        self.assertEqual(self.state.mode, "ai")

    async def test_a_weakly_grounded_answer_is_offered_an_officer(self):
        self.chat_model.generate.return_value = {
            **self.chat_model.generate.return_value,
            "response_judge": {"confidence_band": "low"},
        }
        await self.say("How do I register for a TIN?")
        self.assertEqual(self.state.pending_offer, "officer")
        self.assertTrue(self.texts()[-1].endswith(phrase("officer_offer", "en")))

    async def test_a_zero_confidence_answer_without_sources_goes_to_an_officer(self):
        self.chat_model.generate.return_value = {"reply": "Perhaps.", "sources": [], "confidence": 0.0}
        await self.say("What is the levy on imported spoons?")
        self.assertEqual((self.state.mode, self.state.transfer_reason), ("transferring", "no_knowledge_match"))

    async def test_an_answer_without_sources_or_a_confidence_is_spoken(self):
        # Courtesy, calculator and workflow replies carry no passages and no score.
        self.chat_model.generate.return_value = {"reply": "You're welcome.", "sources": []}
        await self.say("Thank you very much")
        self.assertEqual(self.state.mode, "ai")
        self.assertIn("You're welcome.", self.texts())

    async def test_a_luganda_answer_passes_the_chat_s_grounding_through(self):
        self.chat_model._maybe_handle_fast_paths.return_value = None
        self.chat_model.generate.return_value = {
            "reply": "The VAT rate is 18 percent.", "sources": ["VAT Act"],
            "response_judge": {"confidence_band": "medium"}, "faithfulness_score": 0.7,
        }
        result = await self.brain._generate_luganda_answer("VAT ebitundu bimeka?")
        self.assertIsNone(result["confidence"])
        self.assertEqual(result["response_judge"], {"confidence_band": "medium"})
        self.assertEqual(result["faithfulness_score"], 0.7)


class LanguageChoiceParityTests(unittest.TestCase):
    """G182: the caller can pick any call language on every transport."""

    def test_every_call_language_can_be_chosen(self):
        from app.receptionist.config import call_language

        for language in ("en", "sw", "lg", "nyn", "ach"):
            self.assertEqual(call_language(f" {language.upper()} "), language)
        self.assertIsNone(call_language("fr"))
        self.assertIsNone(call_language(None))
        with patch.dict(os.environ, {"RECEPTIONIST_LANGUAGES": "en,lg"}):
            self.assertIsNone(call_language("ach"))

    async def _deserialize(self, language: str):
        from app.receptionist.serializer import BrowserCallSerializer

        return await BrowserCallSerializer().deserialize(f'{{"type": "set_language", "language": "{language}"}}')

    def test_the_websocket_serializer_and_livekit_loop_share_the_rule(self):
        frame = asyncio.run(self._deserialize("ach"))
        self.assertEqual(getattr(frame, "language", None), "ach")
        with patch.dict(os.environ, {"RECEPTIONIST_LANGUAGES": "en,lg"}):
            self.assertIsNone(asyncio.run(self._deserialize("ach")))
        from app.receptionist.ws import _forward_control

        task = MagicMock(queue_frame=AsyncMock())
        asyncio.run(_forward_control(task, {"type": "set_language", "language": "nyn"}))
        self.assertEqual(task.queue_frame.await_args.args[0].language, "nyn")
        task.queue_frame.reset_mock()
        asyncio.run(_forward_control(task, {"type": "set_language", "language": "xx"}))
        task.queue_frame.assert_not_awaited()


class OfficerTerminatesWaitingCallTests(unittest.IsolatedAsyncioTestCase):
    """G176: ending a waiting call from the desk ends it properly, whoever held it before."""

    async def asyncSetUp(self) -> None:
        db.init_db()
        init_receptionist_schema()
        self.call_id = f"call_r1_end_{uuid.uuid4().hex[:8]}"
        create_call(self.call_id, conversation_id=self.call_id, status="bridged", user_id="taxpayer_r1")
        self.state = CallState(call_id=self.call_id, conversation_id=self.call_id, mode="bridged",
                               officer_id="officer_okello", officer_name="Officer Okello")
        self.room = CallRoom(call_id=self.call_id, state=self.state)
        registry._rooms[self.call_id] = self.room
        self.addCleanup(registry._rooms.pop, self.call_id, None)
        presence.set_on_call("officer_okello", self.call_id)
        update_call(self.call_id, status="bridged", officer_id="officer_okello")

    async def _officer_drops_and_lapses(self) -> None:
        await desk.handle_officer_disconnect(self.room, "officer_okello", None)
        desk._cancel_reconnect_timer(self.room)
        await desk._expire_reconnect_grace(self.room, "officer_okello", 0)

    async def test_a_lapsed_officer_is_released_and_the_call_waits_with_a_countdown(self):
        self.room.brain = MagicMock()
        await self._officer_drops_and_lapses()
        self.assertEqual(self.state.mode, "transferring")
        self.assertIsNone(self.state.officer_id)
        self.assertNotEqual(presence.get_presence("officer_okello")["status"], "on_call")
        self.room.brain.start_queue_countdown.assert_called_once_with("")

    async def test_terminating_the_re_queued_call_ends_it_cleanly(self):
        await self._officer_drops_and_lapses()
        result = await desk.end(self.call_id, "officer_nakato", None)
        self.assertEqual(result, {"ended": True, "call_id": self.call_id})
        self.assertTrue(self.room.end_event.is_set())  # CallRoom.end() ran its teardown
        self.assertEqual(self.state.end_reason, "officer_terminated_waiting")
        row = get_call(self.call_id)
        self.assertEqual((row["status"], row["end_reason"]), ("ended", "officer_terminated_waiting"))

    async def test_terminating_a_waiting_call_closes_its_media_room(self):
        self.state.mode = "transferring"
        self.state.officer_id = None
        self.state.livekit_room = "ura-r1-room"
        with patch("app.receptionist.livekit.close_room", new=AsyncMock()) as close_room:
            await desk.end(self.call_id, "officer_nakato", None)
        close_room.assert_awaited_once_with("ura-r1-room")


if __name__ == "__main__":
    unittest.main()

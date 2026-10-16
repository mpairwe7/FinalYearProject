"""Receptionist audit R2: the waiting caller's experience (docs/plans/receptionist-cx-audit-2026-10-plan.md).

G180 (G91 as actually built): nobody signed in means a callback at once, not a hold for nobody.
G186: a waiting caller sees their place in the queue and can ask to be called back instead.
G187: an officer's hold is spoken, and the caller is reassured while held.
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
from app.receptionist.serializer import RequestCallbackFrame, RequestOfficerFrame
from app.receptionist.state import CallRoom, CallState, registry
from app.receptionist.store import create_call, get_call, init_receptionist_schema, list_turns, update_call


def queue_on(name: str, **_kw: object) -> bool:
    return name == "ticket_queue"


def _words(text: str) -> list[dict]:
    return [{"word": w, "prob": 0.95} for w in text.split()]


class QueueCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        db.init_db()
        init_receptionist_schema()
        self.call_id = f"call_r2_{uuid.uuid4().hex[:8]}"
        create_call(self.call_id, conversation_id=f"conv_{self.call_id}", user_id="taxpayer_r2")
        self.state = CallState(call_id=self.call_id, conversation_id=f"conv_{self.call_id}",
                               user_id="taxpayer_r2", mode="ai")
        self.room = CallRoom(call_id=self.call_id, state=self.state)
        registry._rooms[self.call_id] = self.room
        self.chat_model = MagicMock()
        self.chat_model._build_handoff_packet.return_value = {"topic": "registration", "priority": "normal"}
        self.chat_model._maybe_create_ticket.return_value = "TICK-R2-0001"
        self.brain = UraReceptionistBrain(room=self.room, chat_model=self.chat_model)
        self.brain.push_frame = AsyncMock()
        self.room.brain = self.brain
        self.lobby = patch.object(transfer_mod.hub, "publish_lobby").start()
        patch.object(flags, "is_enabled", side_effect=queue_on).start()
        self.staffed = patch.object(transfer_mod, "is_staff_available", return_value=True).start()
        self.addCleanup(patch.stopall)
        self.addCleanup(registry._rooms.pop, self.call_id, None)

    async def asyncTearDown(self) -> None:
        for room in list(registry._rooms.values()):
            for task in (room.state.transfer_timer_task, room.hold_timer):
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

    def statuses(self) -> list[str]:
        return [
            c.args[0].message.get("status")
            for c in self.brain.push_frame.call_args_list
            if c.args[0].__class__.__name__ == "OutputTransportMessageFrame"
            and isinstance(c.args[0].message, dict)
            and c.args[0].message.get("type") == "status"
        ]

    def positions(self) -> list[int]:
        """The queue places sent to the caller's screen, in order."""
        return [
            c.args[0].message["position"]
            for c in self.brain.push_frame.call_args_list
            if c.args[0].__class__.__name__ == "OutputTransportMessageFrame"
            and isinstance(c.args[0].message, dict)
            and c.args[0].message.get("type") == "queue"
        ]

    def waiting_room(self, *, priority: str = "normal", since: float = 1.0, tenant: str = "default") -> CallRoom:
        call_id = f"call_r2_other_{uuid.uuid4().hex[:8]}"
        state = CallState(call_id=call_id, conversation_id=call_id, mode="transferring", tenant_id=tenant,
                          priority=priority, transfer_requested_at=since)
        room = CallRoom(call_id=call_id, state=state)
        registry._rooms[call_id] = room
        self.addCleanup(registry._rooms.pop, call_id, None)
        return room


class NobodyOnlineTests(QueueCase):
    """G180: with no officer signed in, the caller is told so and owed a callback at once."""

    async def test_with_nobody_online_the_callback_is_offered_at_once(self):
        self.staffed.return_value = False
        with patch("app.officer_hours.on_duty", return_value=True):  # within working hours
            await self.brain.process_frame(RequestOfficerFrame())

        self.assertEqual(self.state.mode, "ai")
        self.assertIn(phrase("officers_unavailable", "en"), self.voiced())
        self.assertIn(phrase("reference_screen", "en", ref="TIC-TICK-R2-"), self.texts())
        self.assertNotIn(phrase("transfer", "en"), self.voiced())  # no promise of a connection
        self.chat_model._maybe_create_ticket.assert_called_once()  # officers still get the case
        row = get_call(self.call_id)
        self.assertEqual((row["needs_callback"], row["callback_reason"]), (1, "no_officer_online"))
        self.assertNotIn("transferring", self.statuses())  # the screen never shows a wait for nobody
        self.assertEqual(self.statuses()[-1], "ai")
        self.assertTrue(self.state.transfer_timer_task is None or self.state.transfer_timer_task.done())

    async def test_outside_working_hours_the_caller_hears_officers_are_off_duty(self):
        """G195: after the desk closes (11 PM by default) nobody can take the call."""
        self.staffed.return_value = False
        with patch("app.officer_hours.on_duty", return_value=False):
            await self.brain.process_frame(RequestOfficerFrame())
        self.assertEqual(self.state.mode, "ai")
        self.assertIn(phrase("officers_off_duty", "en"), self.voiced())
        hours_line = next(t for t in self.texts() if t.startswith("Officers' hours: "))
        self.assertIn(":00", hours_line)  # digits on screen
        self.assertNotIn(hours_line, self.voiced())  # never read out
        row = get_call(self.call_id)
        self.assertEqual((row["needs_callback"], row["callback_reason"]), (1, "outside_working_hours"))

    async def test_an_officer_working_late_still_takes_the_call(self):
        with patch("app.officer_hours.on_duty", return_value=False):
            await self.brain.process_frame(RequestOfficerFrame())
        self.assertEqual(self.state.mode, "transferring")  # someone is signed in: queue

    async def test_an_officer_online_means_the_caller_waits(self):
        await self.brain.process_frame(RequestOfficerFrame())
        self.assertEqual(self.state.mode, "transferring")
        self.assertIn(phrase("transfer", "en"), self.voiced())

    def test_presence_lookup_errors_fail_open(self):
        patch.stopall()
        with patch("app.receptionist.presence.count_active_officers", side_effect=RuntimeError("db down")):
            self.assertTrue(transfer_mod.is_staff_available())
        with patch("app.receptionist.presence.count_active_officers", return_value=0):
            self.assertFalse(transfer_mod.is_staff_available())


class QueuePositionTests(QueueCase):
    """G186: a waiting caller sees their place in the queue; first in line is told so."""

    async def test_the_only_caller_waiting_sees_their_place_and_hears_they_are_next(self):
        with patch.dict(os.environ, {"RECEPTIONIST_HOLD_UPDATE_S": "0.05"}):
            await self.brain.process_frame(RequestOfficerFrame())
            self.assertEqual(self.positions(), [1])  # at once, on screen, not in the transcript
            self.assertFalse(any("queue" in t.lower() for t in self.texts()))
            self.assertEqual(self.voiced()[-1], phrase("transfer", "en"))  # the handoff line stands alone
            await asyncio.sleep(0.17)
        self.assertEqual(self.voiced().count(phrase("queue_next", "en")), 1)  # said once, with a hold line

    async def test_a_caller_behind_another_sees_their_place_and_moves_up(self):
        ahead = self.waiting_room(since=1.0)
        with patch.dict(os.environ, {"RECEPTIONIST_HOLD_UPDATE_S": "0.05"}):
            await self.brain.process_frame(RequestOfficerFrame())
            self.assertEqual(self.positions(), [2])
            await asyncio.sleep(0.08)
            self.assertNotIn(phrase("queue_next", "en"), self.voiced())
            ahead.state.mode = "bridged"  # an officer took the caller ahead
            await asyncio.sleep(0.08)
        self.assertEqual(self.positions(), [2, 1])  # moved up, shown once
        self.assertEqual(self.voiced().count(phrase("queue_next", "en")), 1)

    def test_urgent_callers_go_first_and_other_tenants_and_taken_calls_do_not_count(self):
        self.state.mode = "transferring"
        self.state.transfer_requested_at = 10.0
        self.waiting_room(priority="urgent", since=20.0)  # later, but urgent: ahead
        self.waiting_room(since=1.0, tenant="other")  # another tenant's queue
        taken = self.waiting_room(since=2.0)
        taken.state.claimed_by = "officer_okello"  # an officer is joining it
        self.waiting_room(priority="low", since=5.0)  # earlier, but lower priority
        self.assertEqual(transfer_mod.queue_position(self.room), 2)


class CallbackInsteadTests(QueueCase):
    """G186: a waiting caller can ask to be called back instead, by voice or on screen."""

    async def _wait(self) -> None:
        await self.brain.process_frame(RequestOfficerFrame())
        self.assertEqual(self.state.mode, "transferring")

    async def _assert_callback(self) -> None:
        self.assertEqual(self.state.mode, "ai")
        self.assertIn(phrase("callback_confirmed", self.state.locale), self.voiced())
        self.assertIn(phrase("reference_screen", self.state.locale, ref="TIC-TICK-R2-"), self.texts())
        row = get_call(self.call_id)
        self.assertEqual((row["needs_callback"], row["callback_reason"]), (1, "caller_requested_callback"))
        await asyncio.sleep(0)
        self.assertTrue(self.state.transfer_timer_task is None or self.state.transfer_timer_task.done())

    async def test_asking_to_be_called_back_while_waiting(self):
        await self._wait()
        await self.brain.handle_external_question("Can you just call me back instead?", _words("x"))
        await self._assert_callback()

    async def test_the_call_me_back_button(self):
        await self._wait()
        await self.brain.process_frame(RequestCallbackFrame())
        await self._assert_callback()

    async def test_callers_in_every_call_language_can_ask_too(self):
        for locale, said in (
            ("lg", "Munkubire essimu"),
            ("sw", "Tafadhali nipigie simu baadaye"),
            ("nyn", "Onteerere esimu"),
            ("ach", "Goy cim bota"),
        ):
            self.state.mode, self.state.locale = "ai", locale
            await self._wait()
            await self.brain.handle_external_question(said, _words(said))
            await self._assert_callback()

    async def test_the_button_outside_a_wait_changes_nothing(self):
        await self.brain.process_frame(RequestCallbackFrame())
        self.assertEqual(self.state.mode, "ai")
        self.assertEqual(self.voiced(), [])
        self.chat_model._maybe_create_ticket.assert_not_called()


class OfficerHoldTests(unittest.IsolatedAsyncioTestCase):
    """G187: the caller hears that they are on hold, and is reassured while held."""

    async def asyncSetUp(self) -> None:
        db.init_db()
        init_receptionist_schema()
        self.call_id = f"call_r2_hold_{uuid.uuid4().hex[:8]}"
        create_call(self.call_id, conversation_id=self.call_id, status="bridged")
        self.state = CallState(call_id=self.call_id, conversation_id=self.call_id, mode="bridged",
                               officer_id="officer_okello", officer_name="Officer Okello")
        self.room = CallRoom(call_id=self.call_id, state=self.state)
        self.room.officer = MagicMock(on_hold=False, close=AsyncMock())
        registry._rooms[self.call_id] = self.room
        self.addCleanup(registry._rooms.pop, self.call_id, None)
        update_call(self.call_id, status="bridged", officer_id="officer_okello")
        presence.set_on_call("officer_okello", self.call_id)

    async def asyncTearDown(self) -> None:
        if self.room.hold_timer is not None and not self.room.hold_timer.done():
            self.room.hold_timer.cancel()

    def texts(self) -> list[str]:
        return [t["text"] for t in list_turns(self.call_id)]

    async def test_the_caller_hears_they_are_on_hold_and_is_reassured(self):
        with patch.dict(os.environ, {"RECEPTIONIST_HOLD_UPDATE_S": "0.05"}):
            await desk.hold(self.call_id, "officer_okello", on=True)
            self.assertEqual(self.texts()[-1], phrase("on_hold", "en"))
            await asyncio.sleep(0.13)
        self.assertIn(phrase("officer_hold_update", "en"), self.texts())

    async def test_resuming_stops_the_reassurance(self):
        with patch.dict(os.environ, {"RECEPTIONIST_HOLD_UPDATE_S": "0.05"}):
            await desk.hold(self.call_id, "officer_okello", on=True)
            timer = self.room.hold_timer
            await desk.hold(self.call_id, "officer_okello", on=False)
            await asyncio.sleep(0)
        self.assertTrue(timer.cancelled())
        updates = self.texts().count(phrase("officer_hold_update", "en"))
        await asyncio.sleep(0.12)
        self.assertEqual(self.texts().count(phrase("officer_hold_update", "en")), updates)

    async def test_ending_the_call_stops_the_reassurance(self):
        await desk.hold(self.call_id, "officer_okello", on=True)
        timer = self.room.hold_timer
        await self.room.end("caller_hangup")
        await asyncio.sleep(0)
        self.assertTrue(timer.cancelled())


class ControlMessageTests(unittest.TestCase):
    def test_request_callback_reaches_the_pipeline_on_both_transports(self):
        from app.receptionist.serializer import BrowserCallSerializer
        from app.receptionist.ws import _forward_control

        frame = asyncio.run(BrowserCallSerializer().deserialize('{"type": "request_callback"}'))
        self.assertIsInstance(frame, RequestCallbackFrame)
        task = MagicMock(queue_frame=AsyncMock())
        asyncio.run(_forward_control(task, {"type": "request_callback"}))
        self.assertIsInstance(task.queue_frame.await_args.args[0], RequestCallbackFrame)


if __name__ == "__main__":
    unittest.main()

"""Handoff and caller safety: a caller is never stranded, misrouted or left unheard.

Regressions from the November 2026 call-receptionist audit
(docs/plans/receptionist-assurance-2026-11-11.md, ADR-0039). Each test names
the failure a taxpayer or an officer would have met.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from app import database as db
from app.receptionist import desk, livekit, transfer
from app.receptionist.hub import hub
from app.receptionist.state import registry
from app.receptionist.store import create_call, get_call, init_receptionist_schema


class FakeCallerSocket:
    """The caller's browser: records what the server sends it."""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.audio: list[bytes] = []

    async def send_text(self, text: str) -> None:
        self.messages.append(json.loads(text))

    async def send_bytes(self, data: bytes) -> None:
        self.audio.append(data)

    def captions(self) -> list[str]:
        return [m["text"] for m in self.messages if m.get("type") == "caption"]

    def statuses(self) -> list[str]:
        return [m["status"] for m in self.messages if m.get("type") == "status"]


class WaitingCallCase(unittest.IsolatedAsyncioTestCase):
    """A call the AI handed over, waiting in the officers' queue, on the real store."""

    tenant_id = "default"

    async def asyncSetUp(self) -> None:
        db.init_db()
        init_receptionist_schema()
        self.call_id = f"call_safe_{uuid.uuid4().hex[:8]}"
        create_call(
            self.call_id,
            conversation_id=f"conv_{self.call_id}",
            status="transferring",
            tenant_id=self.tenant_id,
        )
        self.caller = FakeCallerSocket()
        self.room = await registry.create(
            self.call_id, f"conv_{self.call_id}", tenant_id=self.tenant_id, caller_ws=self.caller
        )
        self.room.state.mode = "transferring"
        self.lobby = patch.object(hub, "publish_lobby").start()
        self.call_events = patch.object(hub, "publish_call").start()
        self.addCleanup(patch.stopall)

    async def asyncTearDown(self) -> None:
        desk._cancel_claim_timer(self.room)
        desk._cancel_reconnect_timer(self.room)
        await registry.remove(self.call_id)

    def lobby_events(self, event: str) -> list[dict]:
        return [c.args[1] for c in self.lobby.call_args_list if c.args[0] == event]

    def call_channel(self, event: str) -> list[dict]:
        return [c.args[2] for c in self.call_events.call_args_list if c.args[1] == event]

    async def bridge(self, officer: str = "okello") -> None:
        await desk.claim(self.call_id, officer)
        await desk.bridge(self.room, None, officer, speech_model=None)

    async def drop_officer_and_let_the_grace_lapse(self, officer: str = "okello") -> None:
        with patch.object(desk, "get_officer_reconnect_grace_s", return_value=0.02):
            await desk.handle_officer_disconnect(self.room, officer, speech_model=None)
            await asyncio.sleep(0.15)


class OfficerDropTests(WaitingCallCase):
    """An officer's connection drops and they do not come back."""

    async def test_the_next_officer_can_take_the_call(self):
        await self.bridge("okello")
        await self.drop_officer_and_let_the_grace_lapse("okello")

        result = await desk.claim(self.call_id, "nakato")

        self.assertTrue(result["claimed"])
        self.assertEqual(self.room.state.claimed_by, "nakato")

    async def test_the_row_shows_the_caller_waiting_again(self):
        await self.bridge("okello")
        await self.drop_officer_and_let_the_grace_lapse("okello")

        row = get_call(self.call_id)

        self.assertEqual(row["status"], "transferring")
        self.assertIn(row["officer_id"], (None, ""))
        self.assertEqual(row["transfer_reason"], "officer_disconnected")

    async def test_the_next_officer_can_take_it_even_when_the_requeue_was_not_recorded(self):
        await self.bridge("okello")
        with patch.object(desk, "update_call", side_effect=RuntimeError("database is locked")):
            await self.drop_officer_and_let_the_grace_lapse("okello")
        self.assertEqual(get_call(self.call_id)["status"], "bridged")  # the row missed the requeue

        result = await desk.claim(self.call_id, "nakato")

        self.assertTrue(result["claimed"])
        row = get_call(self.call_id)
        self.assertEqual((row["status"], row["claimed_by"]), ("transferring", "nakato"))

    async def test_a_claim_another_officer_holds_is_still_refused(self):
        await self.bridge("okello")
        await self.drop_officer_and_let_the_grace_lapse("okello")
        await desk.claim(self.call_id, "nakato")

        with self.assertRaises(desk.DeskError) as refused:
            await desk.claim(self.call_id, "mukasa")

        self.assertEqual(refused.exception.status, 409)
        self.assertEqual(get_call(self.call_id)["claimed_by"], "nakato")

    async def test_the_caller_is_requeued_even_when_the_notice_cannot_be_recorded(self):
        await self.bridge("okello")
        self.room.brain = types.SimpleNamespace(start_queue_countdown=unittest.mock.Mock())
        with (
            patch.object(desk, "create_turn", side_effect=RuntimeError("database is locked")),
            patch.object(desk, "update_call", side_effect=RuntimeError("database is locked")),
        ):
            await self.drop_officer_and_let_the_grace_lapse("okello")

        self.assertEqual(len(self.lobby_events("call.transfer_requested")), 1)
        self.room.brain.start_queue_countdown.assert_called_once()
        self.assertIn("transferring", self.caller.statuses())


class OfficerEndTests(WaitingCallCase):
    """The officer presses End: the call ends, whatever else fails."""

    async def test_the_call_ends_when_the_closing_line_cannot_be_spoken(self):
        await self.bridge("okello")
        with patch.object(desk, "say_to_caller", new=AsyncMock(side_effect=RuntimeError("speech backend down"))):
            result = await desk.end(self.call_id, "okello", speech_model=None)

        self.assertTrue(result["ended"])
        self.assertEqual(self.room.state.mode, "ended")
        self.assertEqual(self.room.state.end_reason, "officer_ended")
        self.assertEqual(get_call(self.call_id)["status"], "ended")

    async def test_the_call_ends_when_presence_cannot_be_written(self):
        await self.bridge("okello")
        with patch.object(desk.presence, "set_wrap_up", side_effect=RuntimeError("database is locked")):
            await desk.end(self.call_id, "okello", speech_model=None)

        self.assertEqual(self.room.state.mode, "ended")

    async def test_pressing_end_twice_says_goodbye_once(self):
        await self.bridge("okello")
        before = len(self.caller.captions())

        results = await asyncio.gather(
            desk.end(self.call_id, "okello", speech_model=None),
            desk.end(self.call_id, "okello", speech_model=None),
        )

        self.assertEqual([r["ended"] for r in results], [True, True])
        self.assertEqual(len(self.caller.captions()) - before, 1)

    async def test_another_officer_still_cannot_end_it(self):
        await self.bridge("okello")

        with self.assertRaises(desk.DeskError) as refused:
            await desk.end(self.call_id, "nakato", speech_model=None)

        self.assertEqual(refused.exception.status, 403)
        self.assertEqual(self.room.state.mode, "bridged")


class DirectedTransferTests(WaitingCallCase):
    """A call passed to a named officer is theirs to take first, then anyone's."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        desk.presence.upsert_presence("nakato", display_name="Officer Nakato", status="available")
        desk.presence.upsert_presence("mukasa", display_name="Officer Mukasa", status="available")
        self.addCleanup(desk.presence.upsert_presence, "nakato", status="offline")
        self.addCleanup(desk.presence.upsert_presence, "mukasa", status="offline")
        await self.bridge("okello")

    async def asyncTearDown(self) -> None:
        desk._cancel_reservation(self.room)
        await super().asyncTearDown()

    async def test_only_the_named_officer_can_take_it_at_first(self):
        await desk.transfer(self.call_id, "okello", target_officer_id="nakato")

        with self.assertRaises(desk.DeskError) as refused:
            await desk.claim(self.call_id, "mukasa")
        self.assertEqual(refused.exception.status, 409)
        self.assertEqual(refused.exception.extra.get("reserved_for"), "nakato")

        taken = await desk.claim(self.call_id, "nakato")
        self.assertTrue(taken["claimed"])
        self.assertEqual(self.room.state.reserved_for, "")

    async def test_officers_are_told_who_it_is_reserved_for(self):
        await desk.transfer(self.call_id, "okello", target_officer_id="nakato")

        event = self.lobby_events("call.transfer_requested")[-1]
        self.assertEqual((event["reserved_for"], event["reserved_name"]), ("nakato", "Officer Nakato"))
        self.assertGreater(event["reserved_until"], event["waiting_since"])
        row = get_call(self.call_id)
        self.assertEqual(row["reserved_for"], "nakato")

    async def test_a_reservation_nobody_takes_opens_the_call_to_everyone(self):
        with patch.object(desk, "get_claim_timeout_s", return_value=0.05):
            await desk.transfer(self.call_id, "okello", target_officer_id="nakato")
            await asyncio.sleep(0.2)

        self.assertEqual(self.lobby_events("call.unreserved"), [{"call_id": self.call_id, "reason": "not_taken"}])
        self.assertEqual(get_call(self.call_id)["reserved_for"], "")
        taken = await desk.claim(self.call_id, "mukasa")
        self.assertTrue(taken["claimed"])

    async def test_a_call_is_not_passed_to_an_officer_who_is_not_available(self):
        desk.presence.upsert_presence("nakato", status="away")

        with self.assertRaises(desk.DeskError) as refused:
            await desk.transfer(self.call_id, "okello", target_officer_id="nakato")

        self.assertEqual(refused.exception.status, 409)
        self.assertEqual((self.room.state.mode, self.room.state.officer_id), ("bridged", "okello"))
        self.assertEqual(self.lobby_events("call.transfer_requested"), [])

    async def test_an_officer_cannot_pass_a_call_to_themselves(self):
        with self.assertRaises(desk.DeskError) as refused:
            await desk.transfer(self.call_id, "okello", target_officer_id="okello")

        self.assertEqual(refused.exception.status, 400)
        self.assertEqual(self.room.state.mode, "bridged")

    async def test_the_database_refuses_another_officer_while_it_is_reserved(self):
        from app.receptionist.store import claim_call

        await desk.transfer(self.call_id, "okello", target_officer_id="nakato")
        now = self.room.state.transfer_requested_at

        self.assertFalse(claim_call(self.call_id, "mukasa", now, now - 20))
        self.assertTrue(claim_call(self.call_id, "nakato", now, now - 20))

    async def test_a_team_transfer_reserves_nothing(self):
        await desk.transfer(self.call_id, "okello", team="disputes")

        event = self.lobby_events("call.transfer_requested")[-1]
        self.assertEqual(event.get("reserved_for", ""), "")
        taken = await desk.claim(self.call_id, "mukasa")
        self.assertTrue(taken["claimed"])

    async def test_a_passed_on_call_reaches_the_queue_when_the_row_cannot_be_written(self):
        self.room.brain = types.SimpleNamespace(start_queue_countdown=unittest.mock.Mock())
        with (
            patch.object(desk, "update_call", side_effect=RuntimeError("database is locked")),
            patch.object(desk, "create_turn", side_effect=RuntimeError("database is locked")),
        ):
            await desk.transfer(self.call_id, "okello", team="disputes", note="Needs the disputes desk")

        self.assertEqual(len(self.lobby_events("call.transfer_requested")), 1)
        self.room.brain.start_queue_countdown.assert_called_once()
        self.assertIn("transferring", self.caller.statuses())


class FakeOfficerSocket:
    """An officer's browser socket: records the frames it is sent and that it was closed."""

    def __init__(self, log: list | None = None) -> None:
        self.log: list = log if log is not None else []

    async def send_text(self, text: str) -> None:
        self.log.append(json.loads(text))

    async def send_bytes(self, _data: bytes) -> None:
        return None

    async def close(self, code: int = 1000) -> None:
        self.log.append("closed")


class CallEndedSignalTests(WaitingCallCase):
    """When the caller hangs up, the officer's screens are told the call ended, not just cut off."""

    ENDED = {"type": "status", "status": "ended", "reason": "caller_hangup"}

    async def test_the_officers_audio_socket_hears_it_before_it_closes(self):
        from app.receptionist.officer import OfficerLeg

        socket = FakeOfficerSocket()
        leg = OfficerLeg(ws=socket, officer_id="okello", officer_name="Officer Okello", room=self.room, speech_model=None)
        await desk.claim(self.call_id, "okello")
        await desk.bridge(self.room, leg, "okello", speech_model=None)

        await self.room.end("caller_hangup")

        self.assertEqual(socket.log, [self.ENDED, "closed"])

    async def test_a_livekit_officer_hears_it_before_the_media_room_is_closed(self):
        order: list = []
        self.room.officer_control = FakeOfficerSocket(order)
        self.room.state.livekit_room = f"ura-{self.call_id}"
        await self.bridge("okello")

        async def close_room(_name: str) -> None:
            order.append("room_closed")

        with patch.object(livekit, "close_room", new=close_room):
            await self.room.end("caller_hangup")

        self.assertEqual(order, [self.ENDED, "room_closed"])

    async def test_officers_watching_the_transcript_see_it_end(self):
        await self.bridge("okello")

        await self.room.end("caller_hangup")

        self.assertIn(self.ENDED, self.call_channel("status"))

    async def test_an_officer_who_cannot_be_told_does_not_stop_the_call_ending(self):
        class DeadSocket(FakeOfficerSocket):
            async def send_text(self, text: str) -> None:
                raise RuntimeError("socket is gone")

        self.room.officer_control = DeadSocket()
        await self.bridge("okello")

        await self.room.end("caller_hangup")

        self.assertEqual(get_call(self.call_id)["status"], "ended")

    async def test_a_wrap_up_saved_while_the_caller_still_waits_says_the_call_is_live(self):
        await self.bridge("okello")
        await desk.transfer(self.call_id, "okello", team="disputes")

        await desk.wrapup(self.call_id, "okello", outcome="referred", note="Passed to disputes")

        event = self.lobby_events("call.wrapped_up")[-1]
        self.assertIs(event["live"], True)

    async def test_a_wrap_up_after_the_call_ended_says_it_is_over(self):
        await self.bridge("okello")
        await self.room.end("caller_hangup")

        await desk.wrapup(self.call_id, "okello", outcome="resolved", note="Explained the VAT return")

        event = self.lobby_events("call.wrapped_up")[-1]
        self.assertIs(event["live"], False)


class IdleCallerSocketTests(unittest.IsolatedAsyncioTestCase):
    """A call that ends is finished at once, even when the caller's socket says nothing more."""

    async def test_the_lobby_hears_a_call_ended_while_the_callers_socket_is_idle(self):
        from types import SimpleNamespace

        from app import ws_concurrency
        from app.receptionist import ws

        db.init_db()
        init_receptionist_schema()
        ws_concurrency.reset()
        self.addCleanup(ws_concurrency.reset)

        class IdleCaller:
            """Sends call_start, then never says or closes anything."""

            headers = {"origin": "http://localhost:3300"}
            client = SimpleNamespace(host="127.0.0.1")
            query_params: dict = {}

            def __init__(self) -> None:
                self.sent: list[dict] = []

            async def accept(self) -> None:
                return None

            async def receive_text(self) -> str:
                return json.dumps({"type": "call_start", "locale": "en", "voice_consent_accepted": True})

            async def receive(self) -> dict:
                await asyncio.sleep(3600)
                return {"type": "websocket.disconnect"}

            async def send_text(self, text: str) -> None:
                self.sent.append(json.loads(text))

            async def send_bytes(self, _data: bytes) -> None:
                return None

            async def close(self, code: int = 1000) -> None:
                return None

        caller = IdleCaller()
        lobby = patch.object(hub, "publish_lobby").start()
        self.addCleanup(patch.stopall)
        with (
            patch.object(ws, "_voice_receptionist_enabled", return_value=True),
            patch.object(ws.livekit, "enabled", return_value=False),
            patch.object(ws, "build_call_pipeline", side_effect=ImportError("pipecat is not installed")),
            patch.object(ws, "generate_call_summary"),
        ):
            call = asyncio.create_task(ws.call_stream_endpoint(caller))
            for _ in range(100):
                await asyncio.sleep(0.02)
                if any(m.get("type") == "call_ready" for m in caller.sent):
                    break
            call_id = next(m["call_id"] for m in caller.sent if m.get("type") == "call_ready")
            room = registry.get(call_id)

            await room.end("officer_hangup")  # ended from the officer's side
            done, _pending = await asyncio.wait({call}, timeout=3.0)
            if not done:
                call.cancel()
                await asyncio.gather(call, return_exceptions=True)

        self.assertTrue(done, "the caller's handler is still waiting on an idle socket")
        ended = [c.args[1] for c in lobby.call_args_list if c.args[0] == "call.ended"]
        self.assertEqual([(e["call_id"], e["reason"]) for e in ended], [(call_id, "officer_hangup")])
        self.assertIsNone(registry.get(call_id))


class CrisisWhileWaitingTests(WaitingCallCase):
    """A waiting caller in crisis is raised to urgent on the officers' screens."""

    async def test_officers_are_alerted_when_the_row_cannot_be_written(self):
        with patch.object(transfer, "update_call", side_effect=RuntimeError("database is locked")):
            raised = transfer.raise_waiting_priority(self.room, reason="safety_concern")

        self.assertTrue(raised)
        self.assertEqual(self.room.state.priority, "urgent")
        self.assertEqual(
            self.lobby_events("call.priority_raised"),
            [{"call_id": self.call_id, "priority": "urgent", "reason": "safety_concern"}],
        )
        self.assertEqual(self.call_channel("priority"), [{"priority": "urgent", "reason": "safety_concern"}])

    async def test_the_row_and_ticket_are_still_raised_when_the_database_is_up(self):
        raised = transfer.raise_waiting_priority(self.room, reason="safety_concern")

        self.assertTrue(raised)
        row = get_call(self.call_id)
        self.assertEqual((row["priority"], row["transfer_reason"]), ("urgent", "safety_concern"))


class TransferTenantTests(WaitingCallCase):
    """The ticket a transfer opens belongs to the caller's tenant, so its officers see it."""

    tenant_id = "tenant_kla"

    async def test_the_transfer_ticket_is_in_the_callers_tenant(self):
        from app.service import ChatModel

        self.room.state.mode = "ai"
        self.room.state.user_id = "taxpayer-1"
        with patch("app.service.flags.is_enabled", side_effect=lambda name, **_kw: name == "ticket_queue"):
            ticket_id, _status = transfer.open_transfer(
                self.room,
                ChatModel(),
                "caller_requested",
                question="I need to talk to someone about my VAT assessment",
            )

        self.assertTrue(ticket_id)
        self.assertEqual(db.get_ticket(ticket_id)["tenant_id"], "tenant_kla")


class LiveKitDeadlineTests(unittest.IsolatedAsyncioTestCase):
    """An unreachable media service cannot hold a call's desk lock open."""

    def install_hanging_livekit(self) -> None:
        class HangingClient:
            def __init__(self) -> None:
                self.room = self

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def update_participant(self, _request):
                await asyncio.sleep(3600)

            async def remove_participant(self, _request):
                await asyncio.sleep(3600)

            async def delete_room(self, _request):
                await asyncio.sleep(3600)

        api = types.SimpleNamespace(
            LiveKitAPI=HangingClient,
            UpdateParticipantRequest=lambda **kwargs: kwargs,
            ParticipantPermission=lambda **kwargs: kwargs,
            RoomParticipantIdentity=lambda **kwargs: kwargs,
            DeleteRoomRequest=lambda **kwargs: kwargs,
        )
        fake = types.ModuleType("livekit")
        fake.api = api
        patch.dict(sys.modules, {"livekit": fake}).start()
        patch.dict(
            "os.environ",
            {
                "LIVEKIT_URL": "wss://rtc.example.test",
                "LIVEKIT_API_KEY": "server-key",
                "LIVEKIT_API_SECRET": "server-secret",
            },
        ).start()
        self.addCleanup(patch.stopall)

    async def test_revoking_a_participant_gives_up_at_its_deadline(self):
        self.install_hanging_livekit()
        # The outer wait is the test's own backstop: without a deadline inside
        # the helper it is what fires, as a TimeoutError rather than the
        # RuntimeError callers already handle.
        with patch.object(livekit, "ADMIN_DEADLINE_S", 0.05, create=True):
            with self.assertRaises(RuntimeError):
                await asyncio.wait_for(livekit.revoke_participant("ura-call_1", "officer-old"), timeout=1.0)

    async def test_closing_a_room_gives_up_at_its_deadline(self):
        self.install_hanging_livekit()
        with patch.object(livekit, "ADMIN_DEADLINE_S", 0.05, create=True):
            with self.assertRaises(RuntimeError):
                await asyncio.wait_for(livekit.close_room("ura-call_1"), timeout=1.0)


# ---------------------------------------------------------------------------
# What the AI says around a handoff
# ---------------------------------------------------------------------------


def only(*enabled: str):
    """A flag reader with just *enabled* on."""
    return lambda name, **_kw: name in enabled


class BrainCase(unittest.IsolatedAsyncioTestCase):
    """One call in the AI's hands, with a chat model that answers a TIN question."""

    async def asyncSetUp(self) -> None:
        from unittest.mock import MagicMock

        from app.receptionist.brain import UraReceptionistBrain
        from app.receptionist.state import CallRoom, CallState

        staffed = patch("app.receptionist.transfer.is_staff_available", return_value=True)
        staffed.start()
        self.addCleanup(staffed.stop)
        db.init_db()
        init_receptionist_schema()
        self.call_id = f"call_say_{uuid.uuid4().hex[:8]}"
        create_call(self.call_id, conversation_id=f"conv_{self.call_id}")
        self.state = CallState(
            call_id=self.call_id, conversation_id=f"conv_{self.call_id}", user_id="taxpayer_1", mode="ai"
        )
        self.room = CallRoom(call_id=self.call_id, state=self.state)
        self.chat_model = MagicMock()
        self.chat_model.generate.return_value = {
            "reply": "You can register for a TIN using the URA portal.",
            "sources": ["URA Portal Guide"],
            "faithfulness_score": 0.95,
        }
        self.chat_model._build_handoff_packet.return_value = {"priority": "normal"}
        self.chat_model._maybe_create_ticket.return_value = "TICK-TEST-123"
        self.brain = UraReceptionistBrain(room=self.room, chat_model=self.chat_model)
        self.brain.push_frame = AsyncMock()

    async def asyncTearDown(self) -> None:
        for task in (self.state.transfer_timer_task, self.state.review_timer_task):
            if task is not None and not task.done():
                task.cancel()

    def voiced(self) -> list[str]:
        return [
            getattr(c.args[0], "text", "")
            for c in self.brain.push_frame.call_args_list
            if c.args[0].__class__.__name__ == "LLMTextFrame"
        ]

    def turns(self, kind: str | None = None) -> list[dict]:
        from app.receptionist.store import list_turns

        return [t for t in list_turns(self.call_id) if kind is None or t["kind"] == kind]

    async def hear(self, text: str) -> None:
        from app.receptionist.brain import LLMContextFrame

        await self.brain.process_frame(LLMContextFrame(context=text))


PROMISES = ("connect you to an officer", "connecting you to a ura officer")


class OfficerPromiseTests(BrainCase):
    """The AI never says an officer is coming before it knows one can."""

    async def test_a_failed_lookup_does_not_promise_an_officer_when_handoffs_are_off(self):
        self.chat_model.generate.side_effect = RuntimeError("retriever down")
        with patch("app.receptionist.brain.flags.is_enabled", side_effect=only()):
            await self.hear("How do I register for a TIN?")

        said = " ".join(self.voiced()).lower()
        for promise in PROMISES:
            self.assertNotIn(promise, said)
        self.assertIn("can't transfer you right now", said)
        self.assertEqual(self.state.mode, "ai")

    async def test_a_failed_lookup_still_reaches_an_officer_when_handoffs_are_on(self):
        self.chat_model.generate.side_effect = RuntimeError("retriever down")
        with patch("app.receptionist.brain.flags.is_enabled", side_effect=only("ticket_queue")):
            await self.hear("How do I register for a TIN?")

        said = " ".join(self.voiced()).lower()
        self.assertIn("i'm connecting you to a ura officer", said)
        self.assertEqual(self.state.mode, "transferring")

    async def test_the_apology_is_not_what_repeat_that_replays(self):
        await self.hear("How do I register for a TIN?")
        answered = self.state.last_assistant_answer
        self.chat_model.generate.side_effect = RuntimeError("retriever down")
        with patch("app.receptionist.brain.flags.is_enabled", side_effect=only()):
            await self.hear("What is the VAT rate for imports?")

        self.assertEqual(self.state.last_assistant_answer, answered)
        self.assertEqual([t["kind"] for t in self.turns() if "error" in t["text"].lower()], ["notice"])

    async def test_an_uncertain_answer_offers_no_officer_when_handoffs_are_off(self):
        self.chat_model.generate.return_value = {
            "reply": "The levy may apply to your business.",
            "sources": ["Finance Act"],
            "confidence": 0.4,
        }
        with patch("app.receptionist.brain.flags.is_enabled", side_effect=only()):
            await self.hear("Does the new levy apply to my shop?")

        self.assertEqual(self.state.pending_offer, "")
        self.assertNotIn("speak to an officer", " ".join(self.voiced()).lower())

    async def test_a_crisis_gets_the_helplines_but_no_officer_offer_when_handoffs_are_off(self):
        from app.receptionist.phrases import phrase

        with patch("app.receptionist.brain.flags.is_enabled", side_effect=only()):
            await self.hear("I want to kill myself")

        said = " ".join(self.voiced())
        self.assertIn(phrase("crisis_support", "en"), said)
        self.assertNotIn(phrase("crisis_offer", "en"), said)
        self.assertEqual(self.state.pending_offer, "")


class UnwantedHangUpTests(BrainCase):
    """A call ends when the caller ends it, not when a question sounds like a goodbye."""

    QUESTIONS = (
        "How do I disconnect my EFRIS device?",
        "Can I end the call centre contract tax?",
        "My supplier said goodbye to VAT registration",
        "disconnect my EFRIS device please",
        "What if I hang up my business licence?",
        # A question by its punctuation, whatever it opens with.
        "So I just disconnect?",
        "Okay, so goodbye?",
        "Then I end the call?",
    )
    GOODBYES = (
        "goodbye",
        "Okay, good bye.",
        "Thank you, goodbye!",
        "please hang up now",
        "I want to end the call",
        "end the call please",
        "kwaheri",
        "Kale, weeraba",
        "that's all, thanks",
        "bye",
        # Asking the assistant to do it is a request, question mark or not.
        "Can you hang up?",
        "Could you end the call please?",
        "Please hang up?",
    )

    def test_a_question_is_not_a_goodbye(self):
        from app.receptionist.brain import _is_goodbye

        for text in self.QUESTIONS:
            with self.subTest(text=text):
                self.assertFalse(_is_goodbye(text))

    def test_a_goodbye_is_still_a_goodbye(self):
        from app.receptionist.brain import _is_goodbye

        for text in self.GOODBYES:
            with self.subTest(text=text):
                self.assertTrue(_is_goodbye(text))

    async def test_a_question_about_disconnecting_a_device_is_answered(self):
        with patch("app.receptionist.brain.flags.is_enabled", side_effect=only("ticket_queue")):
            await self.hear("How do I disconnect my EFRIS device?")

        self.chat_model.generate.assert_called_once()
        self.assertEqual(self.state.mode, "ai")

    async def test_a_caller_who_speaks_during_the_goodbye_is_not_hung_up_on(self):
        async def goodbye_line(_room, _text, _speech):
            # The caller says something while the closing line is playing.
            self.state.caller_turns_count += 1
            return 0.0

        with patch("app.receptionist.desk.say_to_caller", new=goodbye_line):
            await self.brain._end_call_after("Thank you for calling URA.", "caller_voice_hangup")

        self.assertEqual(self.state.mode, "ai")

    async def test_a_silent_caller_is_hung_up_on_after_the_goodbye(self):
        with patch("app.receptionist.desk.say_to_caller", new=AsyncMock(return_value=0.0)):
            await self.brain._end_call_after("Thank you for calling URA.", "caller_voice_hangup")

        self.assertEqual(self.state.mode, "ended")

    async def test_asking_for_an_officer_at_the_review_question_is_a_transfer(self):
        self.state.review_state = "asking_end"
        self.state.ai_answers_count = 1
        with patch("app.receptionist.brain.flags.is_enabled", side_effect=only("ticket_queue")):
            await self.hear("Officer please")

        self.assertEqual(self.state.mode, "transferring")
        self.assertEqual(self.turns("review"), [])


class LateAnswerTests(BrainCase):
    """An answer that finishes after the caller went on hold is not spoken over the wait."""

    async def test_an_answer_that_arrives_after_the_transfer_is_dropped(self):
        from app.receptionist.serializer import RequestOfficerFrame

        release = asyncio.Event()
        loop = asyncio.get_running_loop()

        def slow_generate(**_kwargs):
            asyncio.run_coroutine_threadsafe(release.wait(), loop).result(timeout=5)
            return {"reply": "The VAT rate is 18 percent.", "sources": ["VAT Act"], "confidence": 0.4}

        self.chat_model.generate.side_effect = slow_generate
        with patch("app.receptionist.brain.flags.is_enabled", side_effect=only("ticket_queue")):
            answering = asyncio.create_task(self.hear("What is the VAT rate?"))
            await asyncio.sleep(0.05)
            await self.brain.process_frame(RequestOfficerFrame())
            self.assertEqual(self.state.mode, "transferring")
            release.set()
            await answering

        self.assertNotIn("18 percent", " ".join(self.voiced()))
        self.assertEqual(self.state.pending_offer, "")
        self.assertEqual(self.state.ai_answers_count, 0)


class WithheldFigureTests(BrainCase):
    """When the spoken-figure guard withholds an answer, the caller is told where to read it."""

    def refuse_all(self):
        import types as _types

        return patch(
            "app.receptionist.spoken_figure_guard.verify_spoken_figures",
            return_value=_types.SimpleNamespace(ok=False),
        )

    async def test_the_caller_is_pointed_to_the_screen(self):
        self.chat_model.generate.return_value = {
            "reply": "The rate is 0.5% for turnover between UGX 10 million and UGX 30 million.",
            "sources": ["Income Tax Act"],
            "faithfulness_score": 0.95,
        }
        with (
            patch("app.receptionist.brain.flags.is_enabled", side_effect=only("ticket_queue", "spoken_figure_guard")),
            self.refuse_all(),
        ):
            await self.hear("What is the presumptive tax rate?")

        said = " ".join(self.voiced())
        self.assertIn("exact figures on your screen", said)
        self.assertNotIn("0.5", said)
        answer = self.turns("answer")
        self.assertEqual(len(answer), 1)
        self.assertIn("UGX 10 million", answer[0]["text"])

    async def test_repeat_that_does_not_voice_the_withheld_answer(self):
        self.chat_model.generate.return_value = {
            "reply": "The rate is 0.5% for turnover between UGX 10 million and UGX 30 million.",
            "sources": ["Income Tax Act"],
            "faithfulness_score": 0.95,
        }
        with (
            patch("app.receptionist.brain.flags.is_enabled", side_effect=only("ticket_queue", "spoken_figure_guard")),
            self.refuse_all(),
        ):
            await self.hear("What is the presumptive tax rate?")
            await self.hear("repeat that")

        self.assertNotIn("0.5", " ".join(self.voiced()))

    async def test_more_detail_is_checked_before_it_is_spoken(self):
        with (
            patch("app.receptionist.brain.flags.is_enabled", side_effect=only("ticket_queue", "spoken_figure_guard")),
            self.refuse_all(),
        ):
            await self.brain._say_more("The penalty is UGX 200,000 for each month the return is late.")

        self.assertNotIn("200,000", " ".join(self.voiced()))
        self.assertIn("exact figures on your screen", " ".join(self.voiced()))


class EventLoopTests(BrainCase):
    """Nothing slow runs on the loop that carries every live call's audio."""

    async def test_a_transfer_writes_its_ticket_off_the_event_loop(self):
        import threading

        loop_thread = threading.current_thread()
        seen: list[bool] = []

        def create_ticket(**_kwargs):
            seen.append(threading.current_thread() is loop_thread)
            return "TICK-OFF-LOOP"

        self.chat_model._maybe_create_ticket.side_effect = create_ticket
        with patch("app.receptionist.brain.flags.is_enabled", side_effect=only("ticket_queue")):
            await self.brain._transfer("caller_requested")

        self.assertEqual(seen, [False])
        self.assertEqual(self.state.ticket_id, "TICK-OFF-LOOP")
        self.assertEqual(self.state.mode, "transferring")

    async def test_two_transfer_requests_at_once_open_one_ticket(self):
        import time as _time

        def create_ticket(**_kwargs):
            _time.sleep(0.1)
            return "TICK-ONCE"

        self.chat_model._maybe_create_ticket.side_effect = create_ticket
        lobby = patch.object(hub, "publish_lobby").start()
        self.addCleanup(patch.stopall)
        with patch("app.receptionist.brain.flags.is_enabled", side_effect=only("ticket_queue")):
            await asyncio.gather(
                self.brain._transfer("caller_requested"),
                self.brain._transfer("caller_requested"),
            )

        self.assertEqual(self.chat_model._maybe_create_ticket.call_count, 1)
        self.assertEqual(len([c for c in lobby.call_args_list if c.args[0] == "call.transfer_requested"]), 1)

    async def test_a_luganda_answer_is_put_into_luganda_off_the_event_loop(self):
        import threading

        loop_thread = threading.current_thread()
        seen: list[bool] = []

        def localize(text: str, _locale: str) -> str:
            seen.append(threading.current_thread() is loop_thread)
            return "Osobola okwewandiisa ku mukutu gwa URA."

        self.state.locale = "lg"
        self.chat_model._maybe_handle_fast_paths.return_value = None
        self.chat_model._localize_reply.side_effect = localize
        with patch.object(self.brain, "_synthesize_luganda_reply", return_value=""):
            result = await self.brain._generate_luganda_answer("Nyinza ntya okwewandiisa okufuna TIN?")

        self.assertEqual(seen, [False])
        self.assertEqual(result.get("reply"), "Osobola okwewandiisa ku mukutu gwa URA.")


class RiskMonitorTests(unittest.IsolatedAsyncioTestCase):
    """The at-risk watch costs a long call nothing on the event loop and speaks only on change."""

    async def asyncSetUp(self) -> None:
        from app.receptionist import risk

        db.init_db()
        init_receptionist_schema()
        self.risk = risk
        self.call_id = f"call_risk_{uuid.uuid4().hex[:8]}"
        create_call(self.call_id, conversation_id=f"conv_{self.call_id}")
        self.lobby = patch.object(hub, "publish_lobby").start()
        self.addCleanup(patch.stopall)
        risk.start(self.call_id)
        self.addCleanup(risk.stop, self.call_id)

    def write(self, seq: int, text: str, speaker: str = "caller") -> None:
        from app.receptionist.store import create_turn

        create_turn(call_id=self.call_id, seq=seq, speaker=speaker, kind="utterance", text=text)

    def risk_events(self) -> list[dict]:
        return [c.args[1] for c in self.lobby.call_args_list if c.args[0] == "call.risk"]

    async def settle(self) -> None:
        for _ in range(40):
            await asyncio.sleep(0.02)
            if not self.risk.pending(self.call_id):
                return

    async def test_writing_a_turn_does_not_evaluate_risk_on_the_event_loop(self):
        import threading

        loop_thread = threading.current_thread()
        seen: list[bool] = []
        real = self.risk.evaluate_call_risk

        def spy(call_id: str):
            seen.append(threading.current_thread() is loop_thread)
            return real(call_id)

        with patch.object(self.risk, "evaluate_call_risk", side_effect=lambda call_id, *_a: spy(call_id)):
            self.write(1, "How do I register my business for VAT this year?")
            await self.settle()

        self.assertEqual(seen, [False])

    async def test_an_uneventful_call_publishes_no_risk_events(self):
        questions = (
            "How do I register my shop for VAT?",
            "My landlord wants a receipt, who pays rental income tax?",
            "Can my son get a TIN before he turns eighteen?",
            "Stamp duty on a land transfer in Wakiso, what is it?",
            "Which PAYE band applies to a salary of two million shillings?",
            "The EFRIS device stopped printing this morning.",
        )
        for seq, question in enumerate(questions, start=1):
            self.write(seq, question)
            await self.settle()

        self.assertEqual(self.risk_events(), [])

    async def test_a_repeated_question_still_raises_the_call_once(self):
        self.write(1, "How do I register my business for VAT this year?")
        await self.settle()
        self.write(2, "How do I register my business for VAT this year?")
        await self.settle()
        self.write(3, "And what about rental income for a landlord?")
        await self.settle()

        events = self.risk_events()
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["level"], events[0]["signals"]), ("watch", ["repeated_question"]))

    @staticmethod
    def unrelated_turns(count: int) -> list[str]:
        """*count* caller turns no two of which read alike."""
        import random

        words = (
            "vat return filing deadline penalty interest refund assessment objection tribunal customs duty "
            "import export excise stamp rental landlord tenant salary paye employer withholding dividend "
            "presumptive turnover invoice efris receipt device portal password tin registration company "
            "partnership farmer exemption waiver instalment arrears agent bank mobile money payment slip"
        ).split()
        rng = random.Random(2026)
        return [" ".join(rng.sample(words, 12)) for _ in range(count)]

    def test_a_new_turn_on_a_long_call_is_compared_with_a_bounded_number_of_turns(self):
        texts = self.unrelated_turns(400)
        compared: list[str] = []

        class Counting(self.risk.difflib.SequenceMatcher):
            def set_seq1(self, a):
                if a:  # the constructor sets two empty sequences first
                    compared.append(a)
                super().set_seq1(a)

        scan = self.risk.RepeatScan()
        scan.check(texts[:399])
        with patch.object(self.risk.difflib, "SequenceMatcher", Counting):
            repeated = scan.check(texts)

        self.assertFalse(repeated)
        self.assertEqual(len(compared), self.risk._REPEAT_LOOKBACK)

    def test_a_repeat_is_found_however_long_the_call_has_run(self):
        question = "How do I register my business for VAT this year?"
        earlier = self.unrelated_turns(60)
        scan = self.risk.RepeatScan()

        self.assertFalse(scan.check(earlier + [question]))
        self.assertTrue(scan.check(earlier + [question, question]))

if __name__ == "__main__":
    unittest.main()

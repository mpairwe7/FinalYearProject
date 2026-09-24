"""Officers taking calls: one winner per call, claims that lapse, joining, ending, and a quiet AI."""

from __future__ import annotations

import asyncio
import json
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import database as db
from app.auth.jwt_auth import make_dev_token
from app.flags import flags
from app.receptionist import desk
from app.receptionist.hub import hub
from app.receptionist.state import registry
from app.receptionist.store import claim_call, create_call, get_call, init_receptionist_schema, list_turns


def queue_on(name: str, **_kw) -> bool:
    return name == "ticket_queue"


class FakeCallerSocket:
    """The caller's browser: records what the server sends it."""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.audio: list[bytes] = []

    async def send_text(self, text: str) -> None:
        self.messages.append(json.loads(text))

    async def send_bytes(self, data: bytes) -> None:
        self.audio.append(data)

    def types(self) -> list[str]:
        return [m.get("status") or m["type"] for m in self.messages]


class DeskTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        db.init_db()
        init_receptionist_schema()
        self.call_id = f"call_desk_{uuid.uuid4().hex[:8]}"
        create_call(self.call_id, conversation_id=f"conv_{self.call_id}", status="transferring")
        self.caller = FakeCallerSocket()
        self.room = await registry.create(self.call_id, f"conv_{self.call_id}", caller_ws=self.caller)
        self.room.state.mode = "transferring"
        self.lobby = patch.object(hub, "publish_lobby").start()
        self.addCleanup(patch.stopall)

    async def asyncTearDown(self) -> None:
        desk._cancel_claim_timer(self.room)
        await registry.remove(self.call_id)

    def lobby_events(self, event: str) -> list[dict]:
        return [c.args[1] for c in self.lobby.call_args_list if c.args[0] == event]


class ClaimTests(DeskTestCase):
    async def test_two_officers_at_once_one_wins(self):
        results = await asyncio.gather(
            desk.claim(self.call_id, "okello", officer_name="Officer Okello"),
            desk.claim(self.call_id, "nakato", officer_name="Officer Nakato"),
            return_exceptions=True,
        )
        wins = [r for r in results if isinstance(r, dict)]
        losses = [r for r in results if isinstance(r, desk.DeskError)]
        self.assertEqual((len(wins), len(losses)), (1, 1))
        self.assertEqual(losses[0].status, 409)
        self.assertEqual(losses[0].extra["claimed_by"], self.room.state.claimed_by)
        self.assertEqual(losses[0].extra["officer_name"], wins[0]["officer_name"])
        self.assertEqual(get_call(self.call_id)["claimed_by"], self.room.state.claimed_by)
        self.assertEqual(len(self.lobby_events("call.claimed")), 1)

    def test_the_database_lets_only_the_first_claim_through(self):
        self.assertTrue(claim_call(self.call_id, "okello", 100.0, 80.0))
        self.assertFalse(claim_call(self.call_id, "nakato", 101.0, 81.0))
        self.assertTrue(claim_call(self.call_id, "nakato", 130.0, 110.0))  # Okello's claim lapsed at 120

    async def test_a_claim_nobody_joins_lapses(self):
        with patch.object(desk, "get_claim_timeout_s", return_value=0.05):
            await desk.claim(self.call_id, "okello")
            await asyncio.sleep(0.15)
        self.assertEqual((self.room.state.claimed_by, get_call(self.call_id)["claimed_by"]), ("", ""))
        self.assertEqual(self.lobby_events("call.unclaimed"), [{"call_id": self.call_id, "reason": "claim_expired"}])
        self.assertEqual(self.room.state.mode, "transferring")  # back to waiting

    async def test_the_claimant_can_give_it_back_and_nobody_else_can(self):
        await desk.claim(self.call_id, "okello")
        with self.assertRaises(desk.DeskError) as other:
            await desk.release(self.call_id, "nakato")
        self.assertEqual(other.exception.status, 403)
        await desk.release(self.call_id, "okello")
        self.assertEqual(self.lobby_events("call.unclaimed"), [{"call_id": self.call_id, "reason": "released"}])

    async def test_taking_over_from_the_ai_goes_through_the_queue_and_back(self):
        self.room.state.mode = "ai"
        with patch.object(flags, "is_enabled", side_effect=queue_on):
            await desk.claim(self.call_id, "okello")
        self.assertEqual((self.room.state.mode, self.room.state.transfer_reason), ("transferring", "officer_takeover"))
        self.assertEqual(len(self.lobby_events("call.transfer_requested")), 1)
        await desk.release(self.call_id, "okello")
        self.assertEqual(self.room.state.mode, "ai")  # the caller never asked for a person
        self.assertEqual(get_call(self.call_id)["status"], "ai")
        self.assertEqual(self.caller.types()[-1], "ai")

    async def test_no_take_over_without_the_ticket_queue(self):
        self.room.state.mode = "ai"
        with patch.object(flags, "is_enabled", return_value=False), self.assertRaises(desk.DeskError) as err:
            await desk.claim(self.call_id, "okello")
        self.assertEqual(err.exception.status, 409)
        self.assertEqual(self.room.state.mode, "ai")

    async def test_a_call_with_an_officer_cannot_be_claimed(self):
        self.room.state.mode = "bridged"
        self.room.state.officer_id, self.room.state.officer_name = "okello", "Officer Okello"
        with self.assertRaises(desk.DeskError) as err:
            await desk.claim(self.call_id, "nakato")
        self.assertEqual((err.exception.status, err.exception.extra["officer_name"]), (409, "Officer Okello"))


class JoinAndEndTests(DeskTestCase):
    async def joined(self) -> None:
        await desk.claim(self.call_id, "okello", officer_name="Officer Okello")
        self.leg = SimpleNamespace(close=AsyncMock())
        await desk.bridge(self.room, self.leg, "okello", speech_model=None)

    async def test_joining_quiets_the_ai_and_introduces_the_officer(self):
        self.room.state.locale = "sw"
        await self.joined()
        self.assertEqual(self.room.state.mode, "bridged")
        self.assertIsNone(self.room.claim_timer)
        self.assertEqual(self.caller.types(), ["interrupt", "caption", "bridged"])
        caption = self.caller.messages[-2]
        self.assertEqual(caption["text"], "Sasa umeunganishwa na Officer Okello.")  # the call's language
        row = get_call(self.call_id)
        self.assertEqual((row["status"], row["officer_id"], row["needs_callback"]), ("bridged", "okello", 0))
        self.assertIsNotNone(row["bridged_at"])
        self.assertEqual(self.lobby_events("call.bridged")[0]["officer_name"], "Officer Okello")
        self.assertIn("Sasa umeunganishwa", [t["text"] for t in list_turns(self.call_id)][-1])

    async def test_the_officer_ends_it_with_a_closing_line(self):
        await self.joined()
        result = await desk.end(self.call_id, "okello", speech_model=None)
        self.assertTrue(result["ended"])
        self.assertEqual(self.caller.messages[-2]["text"], "Thank you for calling URA. Goodbye.")
        self.assertEqual(self.caller.types()[-1], "ended")
        self.assertEqual(self.room.state.mode, "ended")
        self.assertEqual(get_call(self.call_id)["end_reason"], "officer_ended")

    async def test_only_the_officer_on_the_call_ends_it(self):
        await self.joined()
        with self.assertRaises(desk.DeskError) as err:
            await desk.end(self.call_id, "nakato", speech_model=None)
        self.assertEqual(err.exception.status, 403)
        with self.assertRaises(desk.DeskError):
            await desk.release(self.call_id, "okello")  # on the call: end it, don't release it

    async def test_the_joining_line_is_played_as_pcm(self):
        speech = MagicMock()
        with patch.object(desk, "synthesize_pcm16", return_value=b"\x01\x00" * 24000):
            await desk.claim(self.call_id, "okello")
            await desk.bridge(self.room, SimpleNamespace(close=AsyncMock()), "okello", speech_model=speech)
        self.assertEqual(sum(len(chunk) for chunk in self.caller.audio), 48000)
        self.assertTrue(all(len(chunk) <= 16000 for chunk in self.caller.audio))


class QuietAITests(unittest.IsolatedAsyncioTestCase):
    """While an officer has the call, the AI neither hears the caller nor speaks."""

    async def test_caller_audio_goes_to_the_officer_only(self):
        pytest.importorskip("pipecat")
        from pipecat.frames.frames import InputAudioRawFrame
        from pipecat.processors.frame_processor import FrameDirection

        from app.receptionist.taps import CallerAudioTap

        officer = MagicMock()
        room = SimpleNamespace(state=SimpleNamespace(mode="bridged"), officer=officer)
        tap = CallerAudioTap(room=room)
        tap.push_frame = AsyncMock()
        frame = InputAudioRawFrame(audio=b"\x00\x01" * 160, sample_rate=16000, num_channels=1)
        await tap.process_frame(frame, FrameDirection.DOWNSTREAM)
        officer.send_caller_audio.assert_called_once_with(frame.audio)
        tap.push_frame.assert_not_awaited()
        room.state.mode = "ai"
        await tap.process_frame(frame, FrameDirection.DOWNSTREAM)
        tap.push_frame.assert_awaited_once()

    async def test_the_ai_s_reply_is_dropped_but_nothing_else(self):
        pytest.importorskip("pipecat")
        from pipecat.frames.frames import EndFrame, OutputTransportMessageUrgentFrame, TTSAudioRawFrame
        from pipecat.processors.frame_processor import FrameDirection

        from app.receptionist.hold_gate import OfficerOutputGate

        room = SimpleNamespace(state=SimpleNamespace(mode="bridged"))
        gate = OfficerOutputGate(room)
        pushed: list = []

        async def push_frame(frame, direction=FrameDirection.DOWNSTREAM):
            pushed.append(frame)

        gate.push_frame = push_frame
        reply = TTSAudioRawFrame(audio=b"\x00" * 640, sample_rate=16000, num_channels=1)
        caption = OutputTransportMessageUrgentFrame(message={"type": "caption", "speaker": "assistant"})
        status = OutputTransportMessageUrgentFrame(message={"type": "status", "status": "bridged"})
        end = EndFrame()
        for frame in (reply, caption, status, end):
            await gate.process_frame(frame, FrameDirection.DOWNSTREAM)
        self.assertEqual(pushed, [status, end])
        room.state.mode = "ai"
        await gate.process_frame(reply, FrameDirection.DOWNSTREAM)
        self.assertIs(pushed[-1], reply)


class ClaimRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from app.main import app

        db.init_db()
        init_receptionist_schema()
        cls.client = TestClient(app)

    def setUp(self) -> None:
        self.call_id = f"call_route_{uuid.uuid4().hex[:8]}"
        create_call(self.call_id, conversation_id=f"conv_{self.call_id}", status="transferring")
        self.room = asyncio.run(registry.create(self.call_id, f"conv_{self.call_id}"))
        self.room.state.mode = "transferring"
        # The claim timer is an asyncio task; the test client's loop ends with each request.
        patch.object(desk, "_restart_claim_timer").start()
        self.addCleanup(patch.stopall)
        self.addCleanup(lambda: asyncio.run(registry.remove(self.call_id)))

    def token(self, user: str, role: str = "ura_staff") -> str:
        return make_dev_token(user, role=role)

    def post(self, action: str, user: str, role: str = "ura_staff"):
        return self.client.post(f"/v1/admin/calls/{self.call_id}/{action}",
                                headers={"Authorization": f"Bearer {self.token(user, role)}"})

    def test_first_claim_wins_and_the_second_learns_who(self):
        first = self.post("claim", "okello")
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()["claimed"])
        second = self.post("claim", "nakato")
        self.assertEqual(second.status_code, 409)
        self.assertEqual((second.json()["claimed_by"], second.json()["officer_name"]), ("okello", "Officer Okello"))
        self.assertEqual(self.post("release", "okello").status_code, 200)

    def test_auditors_take_nothing(self):
        for action in ("claim", "release", "end"):
            self.assertEqual(self.post(action, "watcher", role="ura_auditor").status_code, 403)

    def test_ending_a_call_nobody_joined_is_refused(self):
        self.assertEqual(self.post("end", "okello").status_code, 409)

    def test_the_console_hides_calls_on_a_deployment_without_them(self):
        with patch.object(flags, "is_enabled", return_value=False), self.assertRaises(WebSocketDisconnect) as closed:
            with self.client.websocket_connect(f"/v1/admin/calls/stream?token={self.token('okello')}") as ws:
                ws.receive_text()
        self.assertEqual(closed.exception.code, 1001)

    def test_only_the_claimant_s_audio_may_join(self):
        self.post("claim", "okello")
        with self.assertRaises(WebSocketDisconnect) as closed:
            with self.client.websocket_connect(
                f"/v1/admin/calls/{self.call_id}/audio?token={self.token('nakato')}"
            ) as ws:
                ws.receive_text()
        self.assertEqual(closed.exception.code, 4409)


if __name__ == "__main__":
    unittest.main()

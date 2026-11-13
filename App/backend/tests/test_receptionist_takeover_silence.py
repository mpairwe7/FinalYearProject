"""G657 (ADR-0055): once an officer has the call, the caller hears and sees the officer only.

Found live on the Space (2026-11-13). The AI's speech is made faster than it
plays: Pipecat queues it in the output transport and plays it out in real time,
and the transfer's "transferring" status and queue place wait in that queue
behind it. An officer joining switched the AI off upstream of that queue (the
output gate) and told the caller's player to drop what it held, so the caller
heard the rest of the AI's greeting over the officer for 15 s, then "I'm
connecting you to an officer", and then their screen showed them back in the
queue, for the rest of the call. An officer's own hang-up on the audio socket
did not tell the caller at all, and on the WebSocket transport an ended call
kept its pipeline, and the caller's socket, open.

The fixes covered here:

* the bridge clears the AI's queued output on the server, in the output
  transport itself and before the joining line, waiting (bounded) until it is
  done (``desk._interrupt_ai_output``);
* the caller's socket writer and the output gate hold back the AI's replies once
  an officer has the call or it ended, and any queue place or "transferring"
  status from a wait that is over;
* the brain sends the waiting status only if the call is still waiting after
  the transfer notice;
* asking for an officer cuts the AI off, as a call control does;
* the WebSocket transport's pipeline runs as a task the room holds, so
  ``CallRoom.end()`` stops it, and the caller's socket with it.

The officer's socket hang-up (it now ends as End does) is in
``test_receptionist_frame_limits.py``; the revocation id in
``test_session_revocation.py``.
"""

from __future__ import annotations

import asyncio
import json
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app import database as db
from app.flags import flags
from app.receptionist import desk
from app.receptionist import transfer as transfer_mod
from app.receptionist import ws as ws_mod
from app.receptionist.brain import UraReceptionistBrain
from app.receptionist.hold_gate import is_stale_wait_message
from app.receptionist.hub import hub
from app.receptionist.phrases import phrase
from app.receptionist.serializer import (
    BrowserCallSerializer,
    InterruptionFrame,
    OutputAudioRawFrame,
    OutputTransportMessageFrame,
    RequestOfficerFrame,
)
from app.receptionist.state import CallRoom, CallState, registry
from app.receptionist.store import create_call, init_receptionist_schema

try:  # The output transport itself is only there with the receptionist stack (CI's receptionist job).
    import pipecat  # noqa: F401

    PIPECAT = True
except ImportError:  # pragma: no cover - depends on the environment
    PIPECAT = False

PCM_40MS = b"\x01\x00" * 640  # 40 ms of 16 kHz mono PCM16


def _status(status: str) -> OutputTransportMessageFrame:
    return OutputTransportMessageFrame(message={"type": "status", "status": status})


def _queue(position: int = 1) -> OutputTransportMessageFrame:
    return OutputTransportMessageFrame(message={"type": "queue", "position": position})


def _caption(text: str = "Hello") -> OutputTransportMessageFrame:
    return OutputTransportMessageFrame(message={"type": "caption", "speaker": "assistant", "text": text})


class FakeCaller:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.messages.append(json.loads(text))

    async def send_bytes(self, _data: bytes) -> None:
        return None


class FakeOutput:
    """The pipeline's output transport, as the bridge reaches it.

    Pipecat calls a queued frame's callback once the processor has handled it;
    *delay* is how long that takes here, ``None`` for never.
    """

    def __init__(self, log: list[str], delay: float | None = 0.0, fail: bool = False) -> None:
        self.log = log
        self.delay = delay
        self.fail = fail
        self.queued: list = []
        self._pending: set[asyncio.Task] = set()

    async def queue_frame(self, frame, direction=None, callback=None) -> None:
        if self.fail:
            raise RuntimeError("the output transport is gone")
        self.queued.append(frame)
        self.log.append("AI output interrupted")
        if callback is None or self.delay is None:
            return

        async def handled() -> None:
            await asyncio.sleep(self.delay)
            self.log.append("AI output cleared")
            await callback(self, frame, direction)

        task = asyncio.ensure_future(handled())
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)


class WaitingCall(unittest.IsolatedAsyncioTestCase):
    """A caller waiting for an officer, with a pipeline whose output the bridge can reach."""

    async def asyncSetUp(self) -> None:
        db.init_db()
        init_receptionist_schema()
        self.call_id = f"call_g657_{uuid.uuid4().hex[:8]}"
        create_call(self.call_id, conversation_id=f"conv_{self.call_id}", status="transferring", tenant_id="default")
        self.caller = FakeCaller()
        self.room = await registry.create(
            self.call_id, f"conv_{self.call_id}", tenant_id="default", caller_ws=self.caller
        )
        self.room.state.mode = "transferring"
        self.log: list[str] = []
        patch.object(hub, "publish_lobby").start()
        patch.object(hub, "publish_call").start()

        async def joining_line(room, text, _speech_model):
            self.log.append("joining line")
            return 0.0

        self.say = patch.object(desk, "say_to_caller", side_effect=joining_line).start()
        self.addCleanup(patch.stopall)

    async def asyncTearDown(self) -> None:
        desk._cancel_claim_timer(self.room)
        desk._cancel_reconnect_timer(self.room)
        await registry.remove(self.call_id)

    def with_output(self, **kwargs) -> FakeOutput:
        output = FakeOutput(self.log, **kwargs)
        self.room.transport = SimpleNamespace(output=lambda: output)
        return output

    async def bridge(self) -> None:
        await desk.claim(self.call_id, "okello")
        await desk.bridge(self.room, None, "okello", speech_model=None)


class BridgeClearsTheAiOutputTests(WaitingCall):
    async def test_the_ai_output_is_interrupted_and_cleared_before_the_joining_line(self):
        output = self.with_output(delay=0.05)

        await self.bridge()

        self.assertEqual(self.log, ["AI output interrupted", "AI output cleared", "joining line"])
        self.assertEqual(len(output.queued), 1)
        self.assertIsInstance(output.queued[0], InterruptionFrame)
        self.assertEqual(self.room.state.mode, "bridged")
        # The caller's player still drops what it held, as before.
        self.assertIn({"type": "interrupt"}, self.caller.messages)

    async def test_an_output_that_never_answers_holds_the_officer_up_only_briefly(self):
        self.with_output(delay=None)

        with patch.object(desk, "_AI_SILENCE_TIMEOUT_S", 0.05), self.assertLogs(desk.logger, "WARNING") as logs:
            await asyncio.wait_for(self.bridge(), timeout=2.0)

        self.assertEqual(self.log, ["AI output interrupted", "joining line"])
        self.assertIn("did not confirm", "\n".join(logs.output))
        self.assertEqual(self.room.state.mode, "bridged")

    async def test_an_output_that_fails_does_not_stop_the_officer_joining(self):
        self.with_output(fail=True)

        await self.bridge()

        self.assertEqual(self.log, ["joining line"])
        self.assertEqual(self.room.state.mode, "bridged")


class SocketWriterTests(unittest.IsolatedAsyncioTestCase):
    """What the caller's socket writer lets out, decided as each frame is written."""

    def writer(self, mode: str) -> BrowserCallSerializer:
        room = SimpleNamespace(state=SimpleNamespace(mode=mode, caption_only_degraded=False, audio_codec="pcm16_16k"))
        return BrowserCallSerializer(room=room)

    async def sent(self, mode: str, frame) -> object:
        return await self.writer(mode).serialize(frame)

    async def test_the_ais_speech_and_captions_are_held_back_once_an_officer_has_the_call(self):
        for mode in ("bridged", "ended"):
            with self.subTest(mode=mode):
                self.assertIsNone(await self.sent(mode, OutputAudioRawFrame(PCM_40MS, 16000, 1)))
                self.assertIsNone(await self.sent(mode, _caption()))

    async def test_a_wait_that_is_over_sends_no_queue_place_or_transferring_status(self):
        for mode in ("bridged", "ended", "ai"):
            with self.subTest(mode=mode):
                self.assertIsNone(await self.sent(mode, _status("transferring")))
                self.assertIsNone(await self.sent(mode, _queue()))

    async def test_a_waiting_caller_still_hears_the_ai_and_sees_their_place(self):
        self.assertEqual(await self.sent("transferring", OutputAudioRawFrame(PCM_40MS, 16000, 1)), PCM_40MS)
        self.assertEqual(json.loads(await self.sent("transferring", _queue(2))), {"type": "queue", "position": 2})
        self.assertEqual(json.loads(await self.sent("transferring", _status("transferring")))["status"], "transferring")
        self.assertEqual(await self.sent("ai", OutputAudioRawFrame(PCM_40MS, 16000, 1)), PCM_40MS)
        self.assertEqual(json.loads(await self.sent("ai", _caption("Hi")))["text"], "Hi")

    async def test_the_interrupt_and_other_messages_still_reach_the_caller(self):
        self.assertEqual(json.loads(await self.sent("bridged", InterruptionFrame())), {"type": "interrupt"})
        self.assertEqual(json.loads(await self.sent("bridged", _status("ended")))["status"], "ended")
        error = OutputTransportMessageFrame(message={"type": "error", "code": "unavailable"})
        self.assertEqual(json.loads(await self.sent("bridged", error))["code"], "unavailable")

    async def test_a_room_without_a_mode_changes_nothing(self):
        serializer = BrowserCallSerializer(room=None)
        self.assertEqual(await serializer.serialize(OutputAudioRawFrame(PCM_40MS, 16000, 1)), PCM_40MS)
        self.assertEqual(json.loads(await serializer.serialize(_queue()))["type"], "queue")


class StaleWaitMessageTests(unittest.TestCase):
    def test_only_queue_places_and_transferring_statuses_outside_a_wait_are_stale(self):
        self.assertTrue(is_stale_wait_message(_queue(), "bridged"))
        self.assertTrue(is_stale_wait_message(_status("transferring"), "ai"))
        self.assertFalse(is_stale_wait_message(_queue(), "transferring"))
        self.assertFalse(is_stale_wait_message(_status("bridged"), "bridged"))
        self.assertFalse(is_stale_wait_message(_caption(), "bridged"))
        self.assertFalse(is_stale_wait_message(OutputAudioRawFrame(PCM_40MS, 16000, 1), "bridged"))
        self.assertFalse(is_stale_wait_message(OutputTransportMessageFrame(message="queue"), "bridged"))


def _queue_on(name: str, **_kw: object) -> bool:
    return name == "ticket_queue"


class BrainCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        db.init_db()
        init_receptionist_schema()
        self.call_id = f"call_g657b_{uuid.uuid4().hex[:8]}"
        create_call(self.call_id, conversation_id=f"conv_{self.call_id}", user_id="taxpayer_g657")
        self.state = CallState(call_id=self.call_id, conversation_id=f"conv_{self.call_id}",
                               user_id="taxpayer_g657", mode="ai")
        self.room = CallRoom(call_id=self.call_id, state=self.state)
        registry._rooms[self.call_id] = self.room
        self.addCleanup(registry._rooms.pop, self.call_id, None)
        chat_model = MagicMock()
        chat_model._build_handoff_packet.return_value = {"topic": "registration", "priority": "normal"}
        chat_model._maybe_create_ticket.return_value = "TICK-G657-0001"
        self.brain = UraReceptionistBrain(room=self.room, chat_model=chat_model)
        self.brain.push_frame = AsyncMock()
        self.room.brain = self.brain
        patch.object(transfer_mod.hub, "publish_lobby").start()
        patch.object(flags, "is_enabled", side_effect=_queue_on).start()
        patch.object(transfer_mod, "is_staff_available", return_value=True).start()
        self.addCleanup(patch.stopall)

    async def asyncTearDown(self) -> None:
        for task in (self.state.transfer_timer_task, self.room.hold_timer):
            if task is not None and not task.done():
                task.cancel()

    def pushed(self) -> list:
        return [c.args[0] for c in self.brain.push_frame.call_args_list]

    def messages(self, kind: str) -> list[dict]:
        return [
            f.message for f in self.pushed()
            if f.__class__.__name__ == "OutputTransportMessageFrame"
            and isinstance(f.message, dict) and f.message.get("type") == kind
        ]


class TransferNoticeOvertakenTests(BrainCase):
    async def test_an_officer_who_joins_during_the_notice_gets_no_waiting_screen_sent_after_them(self):
        say = self.brain._say_and_record

        async def officer_joins_while_it_is_said(text, *args, **kwargs):
            await say(text, *args, **kwargs)
            if text == phrase("transfer", "en"):
                self.state.mode = "bridged"

        with patch.object(self.brain, "_say_and_record", side_effect=officer_joins_while_it_is_said):
            await self.brain.process_frame(RequestOfficerFrame())

        self.assertEqual([m for m in self.messages("status") if m.get("status") == "transferring"], [])
        self.assertEqual(self.messages("queue"), [])
        self.assertIsNone(self.state.transfer_timer_task)

    async def test_a_caller_still_waiting_after_the_notice_sees_the_waiting_screen(self):
        await self.brain.process_frame(RequestOfficerFrame())

        self.assertEqual(self.state.mode, "transferring")
        self.assertIn("transferring", [m.get("status") for m in self.messages("status")])
        self.assertEqual([m["position"] for m in self.messages("queue")], [1])


class AskingForAnOfficerInterruptsTests(BrainCase):
    async def test_asking_for_an_officer_cuts_the_ai_off_before_anything_else(self):
        generation = self.state.generation_id
        answering = asyncio.ensure_future(asyncio.sleep(3600))
        self.brain._active_gen_task = answering

        await self.brain.process_frame(RequestOfficerFrame())
        await asyncio.sleep(0)

        pushed = self.pushed()
        self.assertIsInstance(pushed[0], InterruptionFrame)
        self.assertEqual(sum(isinstance(f, InterruptionFrame) for f in pushed), 1)
        self.assertGreater(self.state.generation_id, generation)
        self.assertTrue(answering.cancelled())
        self.assertEqual(self.state.mode, "transferring")

    async def test_asking_again_while_waiting_does_not_cut_off_the_hold(self):
        await self.brain.process_frame(RequestOfficerFrame())
        self.brain.push_frame.reset_mock()
        generation = self.state.generation_id

        await self.brain.process_frame(RequestOfficerFrame())

        self.assertEqual(self.pushed(), [])
        self.assertEqual(self.state.generation_id, generation)

    async def test_asking_while_an_officer_has_the_call_changes_nothing(self):
        self.state.mode = "bridged"

        await self.brain.process_frame(RequestOfficerFrame())

        self.assertEqual(self.pushed(), [])


class FakeTask:
    """A Pipecat task: cancel() only queues the cancel, which the runner then sees."""

    def __init__(self) -> None:
        self.cancelled = asyncio.Event()

    async def cancel(self) -> None:
        self.cancelled.set()


class FakeRunner:
    async def run(self, task: FakeTask) -> None:
        await task.cancelled.wait()


class WebSocketPipelineEndsWithTheCallTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        db.init_db()
        init_receptionist_schema()
        self.call_id = f"call_g657c_{uuid.uuid4().hex[:8]}"
        create_call(self.call_id, conversation_id=f"conv_{self.call_id}", status="bridged", tenant_id="default")
        self.room = CallRoom(call_id=self.call_id, state=CallState(call_id=self.call_id, conversation_id="c", mode="bridged"))
        patch.object(hub, "publish_lobby").start()
        patch.object(hub, "publish_call").start()
        self.addCleanup(patch.stopall)

    async def test_ending_the_call_stops_the_pipeline_the_caller_socket_runs(self):
        task = FakeTask()
        self.room.pipeline_task = task
        running = asyncio.create_task(ws_mod._run_pipeline(self.room, FakeRunner(), task))
        await asyncio.sleep(0)
        self.assertIsNotNone(self.room.pipeline_runner_task)

        await self.room.end("officer_ended")

        await asyncio.wait_for(running, timeout=1.0)
        self.assertTrue(task.cancelled.is_set())

    async def test_a_pipeline_that_finishes_on_its_own_returns(self):
        task = FakeTask()
        self.room.pipeline_task = task
        running = asyncio.create_task(ws_mod._run_pipeline(self.room, FakeRunner(), task))
        await asyncio.sleep(0)
        await task.cancel()  # the caller hung up: the transport cancelled it

        await asyncio.wait_for(running, timeout=1.0)
        self.assertTrue(self.room.pipeline_runner_task.done())


# ---------------------------------------------------------------------------
# With Pipecat itself (the receptionist stack): the real output transport
# ---------------------------------------------------------------------------


class StarletteLikeSocket:
    """The caller's socket as Pipecat's FastAPI transport sees it."""

    def __init__(self) -> None:
        from starlette.websockets import WebSocketState

        self.client_state = WebSocketState.CONNECTED
        self.application_state = WebSocketState.CONNECTED
        self.audio: list[bytes] = []
        self.messages: list[dict] = []
        self.closed: int | None = None

    async def send_bytes(self, data: bytes) -> None:
        self.audio.append(data)

    async def send_text(self, text: str) -> None:
        self.messages.append(json.loads(text))

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        from starlette.websockets import WebSocketState

        if self.closed is None:
            self.closed = code
        self.client_state = WebSocketState.DISCONNECTED
        self.application_state = WebSocketState.DISCONNECTED

    def iter_bytes(self):  # pragma: no cover - the input side is not built here
        raise RuntimeError("not used")


@unittest.skipUnless(PIPECAT, "needs the receptionist stack (pipecat-ai); CI's receptionist job runs it")
class RealOutputTransportTests(unittest.IsolatedAsyncioTestCase):
    """The Space's transport: the AI's queued speech, then a bridge, as it happened live."""

    async def asyncSetUp(self) -> None:
        from app.receptionist.hold_gate import OfficerOutputGate
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.runner import PipelineRunner
        from pipecat.pipeline.task import PipelineParams, PipelineTask
        from pipecat.transports.websocket.fastapi import (
            FastAPIWebsocketParams,
            FastAPIWebsocketTransport,
        )

        db.init_db()
        init_receptionist_schema()
        self.call_id = f"call_g657p_{uuid.uuid4().hex[:8]}"
        create_call(self.call_id, conversation_id=f"conv_{self.call_id}", status="transferring", tenant_id="default")
        self.room = CallRoom(
            call_id=self.call_id,
            state=CallState(call_id=self.call_id, conversation_id="c", mode="transferring"),
        )
        patch.object(hub, "publish_lobby").start()
        patch.object(hub, "publish_call").start()
        self.addCleanup(patch.stopall)
        self.socket = StarletteLikeSocket()
        self.transport = FastAPIWebsocketTransport(
            self.socket,
            FastAPIWebsocketParams(
                audio_out_enabled=True,
                add_wav_header=False,
                serializer=BrowserCallSerializer(room=self.room),
            ),
        )
        self.room.transport = self.transport
        self.task = PipelineTask(
            Pipeline([OfficerOutputGate(self.room), self.transport.output()]),
            params=PipelineParams(audio_out_sample_rate=16000),
            cancel_on_idle_timeout=False,
        )
        self.room.pipeline_task = self.task
        self.running = asyncio.create_task(
            ws_mod._run_pipeline(self.room, PipelineRunner(handle_sigint=False), self.task)
        )
        self.addAsyncCleanup(self.stop)

    async def stop(self) -> None:
        if not self.running.done():
            await self.task.cancel()
            await asyncio.wait_for(asyncio.gather(self.running, return_exceptions=True), timeout=5.0)

    async def ai_says(self, seconds: float) -> None:
        """The AI's line, made at once and queued to play out in real time, then the waiting screen."""
        frames = [OutputAudioRawFrame(PCM_40MS, 16000, 1) for _ in range(int(seconds / 0.04))]
        await self.task.queue_frames([*frames, _status("transferring"), _queue(1)])

    async def until(self, condition, timeout: float = 5.0) -> None:
        async with asyncio.timeout(timeout):
            while not condition():
                await asyncio.sleep(0.01)

    def waiting_screen(self) -> list[dict]:
        return [m for m in self.socket.messages if m.get("type") == "queue" or m.get("status") == "transferring"]

    async def test_a_waiting_caller_hears_the_line_and_then_sees_the_waiting_screen(self):
        await self.ai_says(0.4)

        await self.until(lambda: len(self.waiting_screen()) == 2)

        self.assertEqual(len(self.socket.audio), 10)

    async def test_once_the_officer_joins_the_queued_speech_and_waiting_screen_never_arrive(self):
        await self.ai_says(4.0)
        await self.until(lambda: len(self.socket.audio) >= 5)

        self.room.state.mode = "bridged"
        await asyncio.wait_for(desk._interrupt_ai_output(self.room), timeout=2.0)
        heard = len(self.socket.audio)
        await asyncio.sleep(0.5)

        self.assertLessEqual(len(self.socket.audio), heard + 1)
        self.assertLess(len(self.socket.audio), 50)
        self.assertEqual(self.waiting_screen(), [])
        self.assertIn({"type": "interrupt"}, self.socket.messages)

    async def test_the_output_is_cleared_even_before_the_mode_says_so(self):
        # The output transport's own clearing, without the socket writer's
        # mode check behind it (the LiveKit transport has no such writer).
        await self.ai_says(4.0)
        await self.until(lambda: len(self.socket.audio) >= 5)

        await asyncio.wait_for(desk._interrupt_ai_output(self.room), timeout=2.0)
        heard = len(self.socket.audio)
        await asyncio.sleep(0.5)

        self.assertLessEqual(len(self.socket.audio), heard + 1)
        self.assertEqual(self.waiting_screen(), [])

    async def test_frames_the_ai_still_had_in_hand_are_held_back_at_the_gate(self):
        self.room.state.mode = "bridged"

        await self.ai_says(0.4)
        await asyncio.sleep(0.3)

        self.assertEqual(self.socket.audio, [])
        self.assertEqual(self.waiting_screen(), [])

    async def test_ending_the_call_closes_the_callers_socket_from_the_server(self):
        await asyncio.sleep(0.05)

        await self.room.end("officer_ended")

        await asyncio.wait_for(self.running, timeout=5.0)
        self.assertIsNotNone(self.socket.closed)


if __name__ == "__main__":
    unittest.main()

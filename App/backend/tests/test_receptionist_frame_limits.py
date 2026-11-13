"""Frame limits on the call sockets: size, rate and a sustained flood (G304, ADR-0045).

Neither the officer's audio socket nor the caller's call socket limited the
size or the rate of what it accepted. An odd-length frame from the officer made
``np.frombuffer`` raise and dropped them into the reconnect grace; a flood grew
the officer's utterance buffer without a byte bound. Every test here names what
a taxpayer, an officer or the service would have met.

A fake clock stands in for ``frame_budget._now`` so a five-second flood runs in
milliseconds. The fake sockets advance it as each frame is received.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import database as db
from app.receptionist import desk, frame_budget, telemetry, ws
from app.receptionist.frame_budget import FrameGate, Verdict
from app.receptionist.hub import hub
from app.receptionist.serializer import BrowserCallSerializer
from app.receptionist.state import registry
from app.receptionist.store import create_call, init_receptionist_schema

#: One browser frame: PCM16 at 16 kHz, 20 ms.
FRAME = b"\x00\x01" * 320
assert len(FRAME) == 640


def counter(name: str, **labels: str) -> float:
    """The current value of one exported counter series (0 when it does not exist yet)."""
    from app.analytics import metrics

    pairs = ",".join(f'{key}="{value}"' for key, value in sorted(labels.items()))
    key = f"{name}{{{pairs}}}" if pairs else name
    return float(metrics.snapshot()["counters"].get(key, 0))


def dropped(side: str, reason: str) -> float:
    return counter("receptionist_frames_dropped_total", side=side, reason=reason)


class FakeClock:
    """A monotonic clock the test moves by hand. It starts away from zero."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ---------------------------------------------------------------------------
# The budget itself
# ---------------------------------------------------------------------------


class FrameGateTests(unittest.TestCase):
    """The per-socket budget: what passes, what is dropped, and when a socket is closed."""

    def setUp(self) -> None:
        self.clock = FakeClock()
        self.before = {
            (side, reason): dropped(side, reason)
            for side in ("caller", "officer")
            for reason in ("rate", "size", "malformed")
        }

    def gate(self, side: str = "officer") -> FrameGate:
        return FrameGate(side, call_id="call_gate", clock=self.clock)

    def counted(self, side: str, reason: str) -> float:
        return dropped(side, reason) - self.before[(side, reason)]

    def test_a_browsers_normal_stream_is_never_dropped(self):
        gate = self.gate()
        for _ in range(50 * 30):  # 30 s of 640 B every 20 ms: 50 frames/s, 32 KB/s
            self.clock.advance(0.02)
            self.assertIs(gate.audio(FRAME), Verdict.PASS)
        self.assertEqual(self.counted("officer", "rate"), 0)

    def test_the_script_processor_fallback_is_never_dropped(self):
        # About 90 frames/s, still 32 KB/s: more frames, smaller ones.
        gate = self.gate()
        small = b"\x00\x01" * 178
        for _ in range(90 * 30):
            self.clock.advance(1 / 90)
            self.assertIs(gate.audio(small), Verdict.PASS)

    def test_a_throttled_tab_releasing_a_burst_is_let_through(self):
        gate = self.gate()
        for _ in range(3):
            for _ in range(100):  # two seconds of audio at once, then a pause
                self.assertIs(gate.audio(FRAME), Verdict.PASS)
            self.clock.advance(2.0)

    def test_a_burst_passes_then_the_next_frame_is_dropped(self):
        gate = self.gate()
        verdicts = [gate.audio(FRAME) for _ in range(201)]  # 200 x 640 B is exactly the 128 000 B burst
        self.assertEqual(verdicts[:200], [Verdict.PASS] * 200)
        self.assertIs(verdicts[200], Verdict.DROP)
        self.assertEqual(self.counted("officer", "rate"), 1)

    def test_the_byte_budget_refills_at_64_kb_a_second(self):
        gate = self.gate()
        for _ in range(200):
            gate.audio(FRAME)
        self.clock.advance(0.1)  # 6 400 B back: ten frames
        verdicts = [gate.audio(FRAME) for _ in range(11)]
        self.assertEqual(verdicts[:10], [Verdict.PASS] * 10)
        self.assertIs(verdicts[10], Verdict.DROP)

    def test_the_frame_budget_refills_at_200_frames_a_second(self):
        gate = self.gate()
        tiny = b"\x00\x01"
        self.assertEqual([gate.audio(tiny) for _ in range(200)], [Verdict.PASS] * 200)
        self.assertIs(gate.audio(tiny), Verdict.DROP)
        self.clock.advance(0.05)  # ten frames back
        verdicts = [gate.audio(tiny) for _ in range(11)]
        self.assertEqual(verdicts[:10], [Verdict.PASS] * 10)
        self.assertIs(verdicts[10], Verdict.DROP)
        self.assertEqual(self.counted("officer", "rate"), 2)

    def test_a_dropped_frame_spends_nothing(self):
        gate = self.gate()
        for _ in range(200):
            gate.audio(FRAME)
        self.clock.advance(0.01)  # 640 B back: one frame
        self.assertIs(gate.audio(b"\x00" * 20000), Verdict.DROP)  # oversize, refused first
        self.assertIs(gate.audio(FRAME), Verdict.PASS)

    def test_the_byte_budget_can_be_set_from_the_environment(self):
        with patch.dict("os.environ", {"RECEPTIONIST_AUDIO_BYTES_PER_S": "32000"}):
            gate = self.gate()
        verdicts = [gate.audio(FRAME) for _ in range(101)]  # the burst is twice the rate: 64 000 B
        self.assertEqual(verdicts[:100], [Verdict.PASS] * 100)
        self.assertIs(verdicts[100], Verdict.DROP)

    def test_a_budget_too_small_for_one_stream_is_raised_to_a_real_time_stream(self):
        from app.receptionist import config

        with patch.dict("os.environ", {"RECEPTIONIST_AUDIO_BYTES_PER_S": "10"}):
            self.assertEqual(config.get_audio_bytes_per_s(), 32000)
        with patch.dict("os.environ", {"RECEPTIONIST_AUDIO_BYTES_PER_S": "plenty"}):
            self.assertEqual(config.get_audio_bytes_per_s(), 64000)
        with patch.dict("os.environ", clear=False):
            import os

            os.environ.pop("RECEPTIONIST_AUDIO_BYTES_PER_S", None)
            self.assertEqual(config.get_audio_bytes_per_s(), 64000)

    def test_a_frame_over_16_kib_is_dropped_as_oversize(self):
        gate = self.gate("caller")
        self.assertIs(gate.audio(b"\x00" * 16384), Verdict.PASS)
        self.assertIs(gate.audio(b"\x00" * 16386), Verdict.DROP)
        self.assertEqual(self.counted("caller", "size"), 1)
        self.assertEqual(self.counted("caller", "rate"), 0)

    def test_an_odd_length_frame_is_dropped_as_malformed(self):
        gate = self.gate()
        self.assertIs(gate.audio(b"\x00" * 641), Verdict.DROP)
        self.assertEqual(self.counted("officer", "malformed"), 1)

    def test_mu_law_is_one_byte_a_sample_so_an_odd_length_is_fine(self):
        gate = self.gate("caller")
        self.assertIs(gate.audio(b"\xff" * 161, one_byte_samples=True), Verdict.PASS)
        self.assertEqual(self.counted("caller", "malformed"), 0)
        self.assertIs(gate.audio(b"\xff" * 16386, one_byte_samples=True), Verdict.DROP)  # still capped
        self.assertEqual(self.counted("caller", "size"), 1)

    def test_a_text_frame_is_capped_in_size(self):
        gate = self.gate()
        self.assertIs(gate.text('{"type":"hangup"}'), Verdict.PASS)
        self.assertIs(gate.text("x" * 20000), Verdict.DROP)
        self.assertEqual(self.counted("officer", "size"), 1)

    def test_a_text_frame_is_measured_in_utf8_bytes_not_characters(self):
        # What arrives on the socket is UTF-8: 5 000 emoji are 20 000 B, and
        # 16 383 two-byte letters are 32 766 B, both under 16 384 characters.
        gate = self.gate()
        self.assertIs(gate.text("\U0001f600" * 5000), Verdict.DROP)
        self.assertIs(gate.text("é" * (frame_budget.MAX_FRAME_BYTES - 1)), Verdict.DROP)
        self.assertEqual(self.counted("officer", "size"), 2)
        # The boundary in ASCII, one byte a character: 16 KiB passes, one more does not.
        self.assertIs(gate.text("x" * frame_budget.MAX_FRAME_BYTES), Verdict.PASS)
        self.assertIs(gate.text("x" * (frame_budget.MAX_FRAME_BYTES + 1)), Verdict.DROP)
        self.assertEqual(self.counted("officer", "size"), 3)

    def test_text_frames_are_limited_to_15_in_10_seconds(self):
        gate = self.gate()
        verdicts = [gate.text('{"type":"ping"}') for _ in range(20)]
        self.assertEqual(verdicts[:15], [Verdict.PASS] * 15)
        self.assertEqual(verdicts[15:], [Verdict.DROP] * 5)
        self.assertEqual(self.counted("officer", "rate"), 5)
        self.clock.advance(10.0)
        self.assertEqual([gate.text('{"type":"ping"}') for _ in range(15)], [Verdict.PASS] * 15)

    def test_a_frame_the_socket_never_takes_is_dropped_as_malformed(self):
        # A binary frame on a LiveKit control socket (ADR-0047): one is
        # dropped and counted; a stream of them that lasts 5 s is a flood.
        gate = self.gate("caller")
        self.assertIs(gate.unexpected(), Verdict.DROP)
        self.assertEqual(self.counted("caller", "malformed"), 1)
        self.assertIs(gate.text('{"type":"hangup"}'), Verdict.PASS)  # spends nothing of the text budget
        verdicts = []
        for _ in range(600):
            self.clock.advance(0.01)
            verdicts.append(gate.unexpected())
        self.assertIn(Verdict.CLOSE, verdicts)

    def test_text_and_audio_do_not_spend_each_others_budget(self):
        gate = self.gate()
        for _ in range(15):
            gate.text('{"type":"ping"}')
        self.assertIs(gate.audio(FRAME), Verdict.PASS)
        for _ in range(200):
            gate.audio(FRAME)
        self.clock.advance(10.0)
        self.assertIs(gate.text('{"type":"hangup"}'), Verdict.PASS)

    def test_drops_for_five_seconds_close_the_socket(self):
        gate = self.gate()
        verdicts = []
        for _ in range(700):  # 7 s of oversize frames, one every 10 ms
            self.clock.advance(0.01)
            verdicts.append(gate.audio(b"\x00" * 20000))
        first_close = verdicts.index(Verdict.CLOSE)
        self.assertEqual(set(verdicts[:first_close]), {Verdict.DROP})
        # The first drop was at 0.01 s: the close is the first drop at or after 5.01 s.
        self.assertAlmostEqual((first_close + 1) * 0.01, 5.01, delta=0.02)
        # Told once: whatever arrives while the socket is closing is dropped, not counted again.
        self.assertEqual(set(verdicts[first_close + 1 :]), {Verdict.DROP})

    def test_a_flood_the_budget_half_absorbs_still_closes(self):
        # A flood at 1 000 frames/s against a 200 frames/s budget still gets
        # some frames through: those must not reset the clock.
        gate = self.gate()
        verdicts = []
        for _ in range(8000):
            self.clock.advance(0.001)
            verdicts.append(gate.audio(FRAME))
        self.assertIn(Verdict.PASS, verdicts[300:])
        self.assertIn(Verdict.CLOSE, verdicts)
        self.assertLess(verdicts.index(Verdict.CLOSE), 6000)

    def test_a_short_flood_followed_by_quiet_does_not_close_later(self):
        gate = self.gate()
        for _ in range(300):  # 300 frames at once: 100 are dropped
            gate.audio(FRAME)
        self.clock.advance(1.5)  # the budget refills, the socket is quiet
        self.assertIs(gate.audio(FRAME), Verdict.PASS)
        self.clock.advance(60.0)
        # A drop a minute later is a new flood, not the end of the old one.
        verdicts = [gate.audio(FRAME) for _ in range(250)]
        self.assertNotIn(Verdict.CLOSE, verdicts)
        self.assertIn(Verdict.DROP, verdicts)

    def test_drops_with_gaps_longer_than_a_second_never_add_up(self):
        gate = self.gate()
        for _ in range(20):  # one oversize frame every 2 s for 40 s
            self.assertIs(gate.audio(b"\x00" * 20000), Verdict.DROP)
            self.clock.advance(2.0)

    def test_a_flood_is_logged_once_not_per_frame(self):
        gate = self.gate()
        with self.assertLogs("app.receptionist.frame_budget", level=logging.WARNING) as logs:
            for _ in range(100):
                self.clock.advance(0.01)
                gate.audio(b"\x00" * 20000)
        self.assertEqual(len(logs.records), 1)
        self.assertIn("call_gate", logs.output[0])
        self.assertIn("officer", logs.output[0])

    def test_a_second_flood_after_a_quiet_spell_is_logged_again(self):
        gate = self.gate()
        with self.assertLogs("app.receptionist.frame_budget", level=logging.WARNING) as logs:
            gate.audio(b"\x00" * 20000)
            gate.audio(b"\x00" * 20000)
            self.clock.advance(3.0)
            gate.audio(b"\x00" * 20000)
        self.assertEqual(len(logs.records), 2)


class FrameCounterTests(unittest.TestCase):
    """The counter exists, at zero, for every label before the first drop."""

    def test_every_side_and_reason_is_declared_at_zero(self):
        from app.analytics import metrics

        with patch.object(metrics, "inc", wraps=metrics.inc) as inc:
            telemetry.declare()

        zeroed = {
            (c.kwargs["labels"]["side"], c.kwargs["labels"]["reason"])
            for c in inc.call_args_list
            if c.args[0] == "receptionist_frames_dropped_total" and c.args[1] == 0
        }
        for side in ("caller", "officer"):
            for reason in ("rate", "size", "malformed"):
                self.assertIn((side, reason), zeroed)
                self.assertIn(
                    f'receptionist_frames_dropped_total{{reason="{reason}",side="{side}"}}',
                    metrics.snapshot()["counters"],
                )

    def test_a_value_outside_the_vocabulary_is_counted_as_other(self):
        before = dropped("other", "other")
        telemetry.frame_dropped("a-socket-the-code-never-names", "a-reason-nobody-declared")
        self.assertEqual(dropped("other", "other") - before, 1)


# ---------------------------------------------------------------------------
# The officer's audio socket
# ---------------------------------------------------------------------------


class RecordingCaller:
    """The caller's browser, as the officer's leg sees it: keeps the audio it is sent."""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.audio: list[bytes] = []

    async def send_text(self, text: str) -> None:
        self.messages.append(json.loads(text))

    async def send_bytes(self, data: bytes) -> None:
        self.audio.append(data)


class ScriptedSocket:
    """A staff browser socket that plays a script of frames, moving the fake clock as each arrives."""

    def __init__(self, frames: list, clock: FakeClock, step: float, first: list[str] | None = None) -> None:
        self._first = list(first if first is not None else [json.dumps({"type": "authenticate", "access_token": "t"})])
        self.frames = list(frames)
        self.clock = clock
        self.step = step
        self.drained = asyncio.Event()
        self.sent: list = []
        self.closed: int | None = None
        self.close_reason: str | None = None
        self.headers = {"origin": "http://localhost:3300"}
        self.query_params: dict = {}
        self.client = SimpleNamespace(host="127.0.0.1")

    async def accept(self) -> None:
        return None

    async def receive_text(self) -> str:
        if self._first:
            return self._first.pop(0)
        await asyncio.sleep(3600)
        return ""

    async def receive(self) -> dict:
        if not self.frames:
            self.drained.set()
            await asyncio.sleep(3600)
        frame = self.frames.pop(0)
        step = self.step
        if isinstance(frame, tuple):  # (seconds since the last frame, frame)
            step, frame = frame
        self.clock.advance(step)
        if isinstance(frame, bytes):
            return {"type": "websocket.receive", "bytes": frame}
        return {"type": "websocket.receive", "text": frame}

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        if self.closed is None:
            self.closed = code
            self.close_reason = reason


async def stop(task: asyncio.Task) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


class OfficerSocketTests(unittest.IsolatedAsyncioTestCase):
    """The officer's audio socket takes frames within a budget and survives a bad one."""

    async def asyncSetUp(self) -> None:
        from app import ws_concurrency

        db.init_db()
        init_receptionist_schema()
        ws_concurrency.reset()
        self.addCleanup(ws_concurrency.reset)
        self.call_id = f"call_frames_{uuid.uuid4().hex[:8]}"
        create_call(self.call_id, conversation_id=f"conv_{self.call_id}", status="transferring", tenant_id="default")
        self.caller = RecordingCaller()
        self.room = await registry.create(
            self.call_id, f"conv_{self.call_id}", tenant_id="default", caller_ws=self.caller
        )
        self.room.state.mode = "transferring"
        self.clock = FakeClock()
        patch.object(hub, "publish_lobby").start()
        patch.object(hub, "publish_call").start()
        patch.object(frame_budget, "_now", self.clock).start()
        self.addCleanup(patch.stopall)
        self.before = {
            (side, reason): dropped(side, reason)
            for side in ("caller", "officer")
            for reason in ("rate", "size", "malformed")
        }

    async def asyncTearDown(self) -> None:
        desk._cancel_claim_timer(self.room)
        desk._cancel_reconnect_timer(self.room)
        desk._cancel_reservation(self.room)
        await registry.remove(self.call_id)

    def counted(self, side: str, reason: str) -> float:
        return dropped(side, reason) - self.before[(side, reason)]

    async def officer_sends(self, frames: list, step: float = 0.02) -> tuple[asyncio.Task, ScriptedSocket]:
        """The officer signs in, takes the call, then sends *frames*, *step* seconds apart."""
        await desk.claim(self.call_id, "okello")
        socket = ScriptedSocket(frames, self.clock, step)
        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(ws, "_voice_receptionist_enabled", return_value=True))
        stack.enter_context(
            patch.object(ws, "_staff_session", return_value=("okello", "default", "ura_staff", None))
        )
        stack.enter_context(patch.object(ws.livekit, "enabled", return_value=False))
        self.addCleanup(stack.close)
        task = asyncio.create_task(ws.officer_audio_endpoint(socket, self.call_id))
        self.addAsyncCleanup(stop, task)
        return task, socket

    async def played_out(self, socket: ScriptedSocket) -> None:
        await asyncio.wait_for(socket.drained.wait(), timeout=10.0)

    def assert_officer_still_on_the_call(self, task: asyncio.Task, socket: ScriptedSocket) -> None:
        self.assertFalse(task.done(), "the officer's socket ended")
        self.assertIsNone(socket.closed)
        self.assertEqual(self.room.state.mode, "bridged")
        self.assertFalse(self.room.state.reconnecting_officer, "the officer was put into the reconnect grace")

    async def test_a_normal_stream_is_passed_to_the_caller_untouched(self):
        task, socket = await self.officer_sends([FRAME] * 250)  # 5 s at 50 frames/s

        await self.played_out(socket)

        self.assert_officer_still_on_the_call(task, socket)
        self.assertEqual(len(self.caller.audio), 250)
        for reason in ("rate", "size", "malformed"):
            self.assertEqual(self.counted("officer", reason), 0)

    async def test_an_oversize_frame_is_dropped_and_the_officer_stays_on_the_call(self):
        task, socket = await self.officer_sends([FRAME, b"\x00" * 16386, FRAME])

        await self.played_out(socket)

        self.assert_officer_still_on_the_call(task, socket)
        self.assertEqual(len(self.caller.audio), 2)
        self.assertEqual(self.counted("officer", "size"), 1)

    async def test_an_odd_length_frame_is_dropped_instead_of_ending_the_leg(self):
        # np.frombuffer raised on it and the officer fell into the reconnect grace.
        task, socket = await self.officer_sends([FRAME, b"\x00" * 641, FRAME])

        await self.played_out(socket)

        self.assert_officer_still_on_the_call(task, socket)
        self.assertEqual(len(self.caller.audio), 2)
        self.assertEqual(self.counted("officer", "malformed"), 1)

    async def test_a_flood_is_dropped_and_counted_but_not_forwarded(self):
        # 400 frames in the same instant: the 200-frame burst passes, the rest do not.
        task, socket = await self.officer_sends([FRAME] * 400, step=0.0)

        await self.played_out(socket)

        self.assert_officer_still_on_the_call(task, socket)
        self.assertEqual(len(self.caller.audio), 200)
        self.assertEqual(self.counted("officer", "rate"), 200)

    async def test_a_flood_that_lasts_five_seconds_closes_the_socket_with_1008(self):
        task, socket = await self.officer_sends([FRAME] * 8000, step=0.001)

        done, _pending = await asyncio.wait({task}, timeout=10.0)

        self.assertTrue(done, "a sustained flood did not end the officer's socket")
        self.assertEqual((socket.closed, socket.close_reason), (1008, "too many frames"))
        self.assertGreater(len(socket.frames), 0, "the socket kept reading after it was closed")
        self.assertGreater(self.counted("officer", "rate"), 0)
        # As for any close: no crash, and the officer has the reconnect grace to come back.
        self.assertIsNone(task.exception())
        self.assertEqual(
            (self.room.state.mode, self.room.state.reconnecting_officer), ("transferring", "okello")
        )
        self.assertIsNone(self.room.officer)

    async def test_a_burst_that_ends_within_five_seconds_leaves_the_socket_open(self):
        # 2 s at 1 000 frames/s, then the normal 50 frames/s.
        script = [(0.001, FRAME)] * 2000 + [(0.02, FRAME)] * 150
        task, socket = await self.officer_sends(script)

        await self.played_out(socket)

        self.assert_officer_still_on_the_call(task, socket)
        self.assertGreater(self.counted("officer", "rate"), 0)
        # Once the burst is over the normal stream is passed again.
        self.assertEqual(self.caller.audio[-100:], [FRAME] * 100)

    async def test_text_frames_from_the_officer_are_limited_to_15_in_10_seconds(self):
        ping = json.dumps({"type": "ping"})
        task, socket = await self.officer_sends([ping] * 20, step=0.0)

        await self.played_out(socket)

        self.assert_officer_still_on_the_call(task, socket)
        self.assertEqual(self.counted("officer", "rate"), 5)

    async def test_hanging_up_within_the_budget_still_ends_the_call(self):
        task, socket = await self.officer_sends([FRAME, json.dumps({"type": "hangup"})])

        done, _pending = await asyncio.wait({task}, timeout=5.0)

        self.assertTrue(done)
        self.assertEqual((self.room.state.mode, self.room.state.end_reason), ("ended", "officer_hangup"))
        self.assertNotEqual(socket.closed, 1008)


# ---------------------------------------------------------------------------
# The caller's socket
# ---------------------------------------------------------------------------


class CloseRecorder:
    """The caller's socket as the serializer sees it: only close matters here."""

    def __init__(self) -> None:
        self.closed: list[tuple[int, str | None]] = []

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed.append((code, reason))


class CallerSerializerTests(unittest.IsolatedAsyncioTestCase):
    """The Pipecat transport reads the caller's audio through the serializer: the budget is there."""

    async def asyncSetUp(self) -> None:
        self.clock = FakeClock()
        patch.object(frame_budget, "_now", self.clock).start()
        self.addCleanup(patch.stopall)
        self.socket = CloseRecorder()
        self.state = SimpleNamespace(audio_codec="pcm16_16k")
        self.room = SimpleNamespace(call_id="call_ser", state=self.state, caller_ws=self.socket)
        self.serializer = BrowserCallSerializer(room=self.room)
        self.before = {
            (side, reason): dropped(side, reason)
            for side in ("caller", "officer")
            for reason in ("rate", "size", "malformed")
        }

    def counted(self, side: str, reason: str) -> float:
        return dropped(side, reason) - self.before[(side, reason)]

    async def test_a_normal_stream_reaches_the_pipeline(self):
        for _ in range(250):  # 5 s at 50 frames/s
            self.clock.advance(0.02)
            frame = await self.serializer.deserialize(FRAME)
            self.assertIsNotNone(frame)
            self.assertEqual(frame.audio, FRAME)
        self.assertEqual(self.socket.closed, [])
        for reason in ("rate", "size", "malformed"):
            self.assertEqual(self.counted("caller", reason), 0)

    async def test_an_oversize_frame_is_dropped_and_counted(self):
        self.assertIsNone(await self.serializer.deserialize(b"\x00" * 16386))
        self.assertIsNotNone(await self.serializer.deserialize(FRAME))
        self.assertEqual(self.counted("caller", "size"), 1)
        self.assertEqual(self.socket.closed, [])

    async def test_an_odd_length_frame_is_dropped_as_malformed(self):
        self.assertIsNone(await self.serializer.deserialize(b"\x00" * 641))
        self.assertEqual(self.counted("caller", "malformed"), 1)

    async def test_mu_law_audio_may_be_odd_in_length(self):
        self.state.audio_codec = "mulaw_8k"
        self.assertIsNotNone(await self.serializer.deserialize(b"\xff" * 161))
        self.assertEqual(self.counted("caller", "malformed"), 0)

    async def test_a_flood_is_dropped_and_counted(self):
        passed = 0
        for _ in range(400):
            if await self.serializer.deserialize(FRAME) is not None:
                passed += 1
        self.assertEqual(passed, 200)
        self.assertEqual(self.counted("caller", "rate"), 200)
        self.assertEqual(self.socket.closed, [])

    async def test_a_flood_that_lasts_five_seconds_closes_the_socket_with_1008(self):
        for _ in range(8000):
            self.clock.advance(0.001)
            await self.serializer.deserialize(FRAME)
            if self.socket.closed:
                break

        self.assertEqual(self.socket.closed, [(1008, "too many frames")])

    async def test_the_socket_is_closed_once_however_long_the_flood_runs(self):
        for _ in range(8000):
            self.clock.advance(0.001)
            await self.serializer.deserialize(b"\x00" * 20000)

        self.assertEqual(self.socket.closed, [(1008, "too many frames")])

    async def test_a_socket_that_cannot_be_closed_does_not_raise_into_the_transport(self):
        class Broken:
            async def close(self, code: int = 1000, reason: str | None = None) -> None:
                raise RuntimeError("already closed")

        self.room.caller_ws = Broken()
        for _ in range(1000):
            self.clock.advance(0.01)
            await self.serializer.deserialize(b"\x00" * 20000)

    async def test_control_messages_keep_their_own_limiter(self):
        for _ in range(200):
            await self.serializer.deserialize(b"\x00" * 20000)  # a flood of audio
        frame = await self.serializer.deserialize(json.dumps({"type": "call_control", "action": "repeat"}))
        self.assertIsNotNone(frame)
        self.assertEqual(self.socket.closed, [])


class ScriptedCaller:
    """The caller's browser on the fallback loop: sends call_start, then plays a script of frames."""

    headers = {"origin": "http://localhost:3300"}
    client = SimpleNamespace(host="127.0.0.1")
    query_params: dict = {}

    def __init__(self, frames: list, clock: FakeClock, step: float) -> None:
        self.frames = list(frames)
        self.clock = clock
        self.step = step
        self.drained = asyncio.Event()
        self.sent: list[dict] = []
        self.closed: int | None = None
        self.close_reason: str | None = None

    async def accept(self) -> None:
        return None

    async def receive_text(self) -> str:
        return json.dumps({"type": "call_start", "locale": "en", "voice_consent_accepted": True})

    async def receive(self) -> dict:
        if not self.frames:
            self.drained.set()
            await asyncio.sleep(3600)
        self.clock.advance(self.step)
        return {"type": "websocket.receive", "bytes": self.frames.pop(0)}

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def send_bytes(self, _data: bytes) -> None:
        return None

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        if self.closed is None:
            self.closed = code
            self.close_reason = reason


class CallerSocketTests(unittest.IsolatedAsyncioTestCase):
    """The same budget on the caller's endpoint when Pipecat is not there to read the socket."""

    async def asyncSetUp(self) -> None:
        from app import ws_concurrency

        db.init_db()
        init_receptionist_schema()
        ws_concurrency.reset()
        self.addCleanup(ws_concurrency.reset)
        self.clock = FakeClock()
        patch.object(hub, "publish_lobby").start()
        patch.object(frame_budget, "_now", self.clock).start()
        patch.object(ws, "_voice_receptionist_enabled", return_value=True).start()
        patch.object(ws.livekit, "enabled", return_value=False).start()
        patch.object(ws, "build_call_pipeline", side_effect=ImportError("pipecat is not installed")).start()
        patch.object(ws, "generate_call_summary").start()
        self.addCleanup(patch.stopall)
        self.before = {
            (side, reason): dropped(side, reason)
            for side in ("caller", "officer")
            for reason in ("rate", "size", "malformed")
        }

    def counted(self, side: str, reason: str) -> float:
        return dropped(side, reason) - self.before[(side, reason)]

    async def call(self, frames: list, step: float) -> tuple[asyncio.Task, ScriptedCaller]:
        caller = ScriptedCaller(frames, self.clock, step)
        task = asyncio.create_task(ws.call_stream_endpoint(caller))
        self.addAsyncCleanup(stop, task)
        return task, caller

    async def played_out(self, caller: ScriptedCaller) -> None:
        await asyncio.wait_for(caller.drained.wait(), timeout=10.0)

    async def test_a_normal_stream_leaves_the_call_open(self):
        task, caller = await self.call([FRAME] * 250, step=0.02)

        await self.played_out(caller)

        self.assertFalse(task.done())
        self.assertIsNone(caller.closed)
        for reason in ("rate", "size", "malformed"):
            self.assertEqual(self.counted("caller", reason), 0)

    async def test_oversize_and_odd_frames_are_dropped_and_the_call_goes_on(self):
        task, caller = await self.call([FRAME, b"\x00" * 16386, b"\x00" * 641, FRAME], step=0.02)

        await self.played_out(caller)

        self.assertFalse(task.done())
        self.assertIsNone(caller.closed)
        self.assertEqual(self.counted("caller", "size"), 1)
        self.assertEqual(self.counted("caller", "malformed"), 1)

    async def test_a_flood_is_dropped_and_counted_without_ending_the_call(self):
        task, caller = await self.call([FRAME] * 400, step=0.0)

        await self.played_out(caller)

        self.assertFalse(task.done())
        self.assertIsNone(caller.closed)
        self.assertEqual(self.counted("caller", "rate"), 200)

    async def test_a_flood_that_lasts_five_seconds_closes_the_caller_with_1008(self):
        # Oversize frames every 50 ms: the loop makes tasks per frame, which the
        # test runner's debug mode makes slow, so this flood is slow and long
        # rather than fast (the fast one is the serializer's, above).
        task, caller = await self.call([b"\x00" * 16386] * 200, step=0.05)

        done, _pending = await asyncio.wait({task}, timeout=10.0)

        self.assertTrue(done, "a sustained flood did not end the caller's socket")
        self.assertEqual((caller.closed, caller.close_reason), (1008, "too many frames"))
        self.assertIsNone(task.exception())
        self.assertGreater(len(caller.frames), 0, "the socket kept reading after it was closed")


# ---------------------------------------------------------------------------
# The control sockets of a LiveKit call
# ---------------------------------------------------------------------------
#
# On a LiveKit call the audio goes over WebRTC, and the browser keeps a text
# socket for control: the caller's call socket, the officer's audio socket and
# a listener's. None of them limited what it took in (G304, ADR-0047). Every
# text frame the caller sent was handed to the pipeline (a `text_turn` queues
# a model turn), and a binary frame made `receive_text()` raise: it ended the
# caller's call, put the officer into the reconnect grace and cut a listener
# off.


class StarletteSocket(ScriptedSocket):
    """A scripted socket whose ``receive_text`` behaves like Starlette's: the next frame's text.

    A binary frame has no text, and Starlette's ``receive_text`` raises on it.
    """

    async def receive_text(self) -> str:
        if self._first:
            return self._first.pop(0)
        message = await self.receive()
        return message["text"]


async def played_out_or_ended(task: asyncio.Task, socket: StarletteSocket) -> None:
    """Until the socket has played its script, or its endpoint has returned (the call ended under it)."""
    drained = asyncio.ensure_future(socket.drained.wait())
    try:
        await asyncio.wait({task, drained}, timeout=10.0, return_when=asyncio.FIRST_COMPLETED)
    finally:
        drained.cancel()


def counts_before() -> dict[tuple[str, str], float]:
    return {
        (side, reason): dropped(side, reason)
        for side in ("caller", "officer")
        for reason in ("rate", "size", "malformed")
    }


class LiveKitCallerControlTests(unittest.IsolatedAsyncioTestCase):
    """The caller's control socket on a LiveKit call takes control messages within the text budget."""

    async def asyncSetUp(self) -> None:
        import os
        import sys

        from app import ws_concurrency

        db.init_db()
        init_receptionist_schema()
        ws_concurrency.reset()
        self.addCleanup(ws_concurrency.reset)
        self.clock = FakeClock()
        seen = self.seen = SimpleNamespace(frames=[], cancelled=asyncio.Event())

        class FakeTask:
            stop: asyncio.Event | None = None

            async def cancel(self) -> None:
                seen.cancelled.set()
                if self.stop is not None:
                    self.stop.set()

            async def queue_frame(self, frame) -> None:
                seen.frames.append(frame)

        class FakeRunner:
            async def run(self, task: FakeTask) -> None:
                task.stop = asyncio.Event()
                await task.stop.wait()

        class FakeTransport:
            def event_handler(self, _name):
                return lambda fn: fn

            def get_participants(self):
                return []

        # The runner is the only part of Pipecat this loop touches; a stand-in
        # module lets it run where Pipecat is not installed.
        runner_module = SimpleNamespace(PipelineRunner=FakeRunner)
        brain = SimpleNamespace(say_greeting=AsyncMock())
        for patcher in (
            patch.dict(sys.modules, {"pipecat.pipeline.runner": runner_module}),
            patch.dict(os.environ, {"LIVEKIT_URL": "wss://rtc.example.test"}),
            patch.object(frame_budget, "_now", self.clock),
            patch.object(hub, "publish_lobby"),
            patch.object(ws, "_voice_receptionist_enabled", return_value=True),
            patch.object(ws.livekit, "enabled", return_value=True),
            patch.object(ws.livekit, "mint_token", return_value="room-jwt"),
            patch.object(ws.livekit, "close_room", AsyncMock()),
            patch.object(ws, "build_call_pipeline", return_value=(FakeTask(), FakeTransport(), brain)),
            patch.object(ws.call_brief, "start"),
            patch.object(ws.call_brief, "stop"),
            patch.object(ws.risk, "start"),
            patch.object(ws.risk, "stop"),
            patch.object(ws, "generate_call_summary"),
        ):
            patcher.start()
        self.addCleanup(patch.stopall)
        self.before = counts_before()

    def counted(self, side: str, reason: str) -> float:
        return dropped(side, reason) - self.before[(side, reason)]

    async def call(self, frames: list, step: float) -> tuple[asyncio.Task, StarletteSocket]:
        """The caller starts a LiveKit call, then sends *frames* on its control socket, *step* seconds apart."""
        start = json.dumps({"type": "call_start", "locale": "en", "voice_consent_accepted": True})
        caller = StarletteSocket(frames, self.clock, step, first=[start])
        task = asyncio.create_task(ws.call_stream_endpoint(caller))
        self.addAsyncCleanup(stop, task)
        return task, caller

    async def played_out(self, task: asyncio.Task, caller: StarletteSocket) -> None:
        await played_out_or_ended(task, caller)

    def assert_call_goes_on(self, task: asyncio.Task, caller: StarletteSocket) -> None:
        self.assertFalse(task.done(), "the caller's call ended")
        self.assertIsNone(caller.closed)
        self.assertFalse(self.seen.cancelled.is_set(), "the media pipeline was stopped")

    async def test_the_sixteenth_control_message_in_ten_seconds_is_dropped_and_not_forwarded(self):
        # Each `text_turn` is a model turn: 20 at once were 20 turns.
        turn = json.dumps({"type": "text_turn", "text": "What is my TIN?"})
        task, caller = await self.call([turn] * 20, step=0.0)

        await self.played_out(task, caller)

        self.assert_call_goes_on(task, caller)
        self.assertEqual(len(self.seen.frames), 15)
        self.assertEqual(self.counted("caller", "rate"), 5)

    async def test_a_binary_frame_is_dropped_and_the_call_goes_on(self):
        language = json.dumps({"type": "set_language", "language": "sw"})
        task, caller = await self.call([b"\x00\x01" * 320, language], step=0.02)

        await self.played_out(task, caller)

        self.assert_call_goes_on(task, caller)
        self.assertEqual([frame.language for frame in self.seen.frames], ["sw"])
        self.assertEqual(self.counted("caller", "malformed"), 1)

    async def test_a_flood_that_lasts_five_seconds_closes_the_socket_with_1008_and_stops_the_pipeline(self):
        ping = json.dumps({"type": "ping"})
        task, caller = await self.call([ping] * 800, step=0.01)

        done, _pending = await asyncio.wait({task}, timeout=10.0)

        self.assertTrue(done, "a sustained flood did not end the caller's control socket")
        self.assertIsNone(task.exception())
        self.assertEqual((caller.closed, caller.close_reason), (1008, "too many frames"))
        self.assertGreater(len(caller.frames), 0, "the socket kept reading after it was closed")
        self.assertGreater(self.counted("caller", "rate"), 0)
        # As for any close: the pipeline is stopped and the call torn down.
        self.assertTrue(self.seen.cancelled.is_set(), "the media pipeline outlived the call")
        ready = next(m for m in caller.sent if isinstance(m, dict) and m.get("type") == "call_ready")
        self.assertIsNone(registry.get(ready["call_id"]))


class LiveKitStaffControlTests(unittest.IsolatedAsyncioTestCase):
    """The officer's and a listener's control sockets on a LiveKit call take frames within the text budget."""

    async def asyncSetUp(self) -> None:
        import os

        from app import ws_concurrency

        db.init_db()
        init_receptionist_schema()
        ws_concurrency.reset()
        self.addCleanup(ws_concurrency.reset)
        self.call_id = f"call_lkctl_{uuid.uuid4().hex[:8]}"
        create_call(self.call_id, conversation_id=f"conv_{self.call_id}", status="transferring", tenant_id="default")
        self.room = await registry.create(self.call_id, f"conv_{self.call_id}", tenant_id="default")
        self.room.state.mode = "transferring"
        self.room.state.livekit_room = f"ura-{self.call_id}"
        self.clock = FakeClock()
        for patcher in (
            patch.dict(os.environ, {"LIVEKIT_URL": "wss://rtc.example.test"}),
            patch.object(frame_budget, "_now", self.clock),
            patch.object(hub, "publish_lobby"),
            patch.object(hub, "publish_call"),
            patch.object(ws, "_voice_receptionist_enabled", return_value=True),
            patch.object(ws.livekit, "enabled", return_value=True),
            patch.object(ws.livekit, "mint_token", return_value="room-jwt"),
            patch.object(ws.livekit, "close_room", AsyncMock()),
            patch.object(ws.livekit, "revoke_participant", AsyncMock()),
            # The officer's media is in the room as soon as it is asked for.
            patch.object(self.room, "wait_for_participant", AsyncMock(return_value=True)),
        ):
            patcher.start()
        self.addCleanup(patch.stopall)
        self.before = counts_before()

    async def asyncTearDown(self) -> None:
        desk._cancel_claim_timer(self.room)
        desk._cancel_reconnect_timer(self.room)
        desk._cancel_reservation(self.room)
        await registry.remove(self.call_id)

    def counted(self, side: str, reason: str) -> float:
        return dropped(side, reason) - self.before[(side, reason)]

    def connect(self, socket: StarletteSocket, user: str, role: str) -> asyncio.Task:
        session = patch.object(ws, "_staff_session", return_value=(user, "default", role, None))
        session.start()
        self.addCleanup(session.stop)
        task = asyncio.create_task(ws.officer_audio_endpoint(socket, self.call_id))
        self.addAsyncCleanup(stop, task)
        return task

    async def officer_sends(self, frames: list, step: float = 0.02) -> tuple[asyncio.Task, StarletteSocket]:
        """Okello takes the call, his media joins, then his control socket sends *frames*."""
        await desk.claim(self.call_id, "okello")
        socket = StarletteSocket(frames, self.clock, step)
        return self.connect(socket, "okello", "ura_staff"), socket

    async def listener_sends(self, frames: list, step: float = 0.02) -> tuple[asyncio.Task, StarletteSocket]:
        """An auditor listens to the bridged call, and their console sends *frames*."""
        await desk.claim(self.call_id, "okello")
        await desk.bridge(self.room, None, "okello", speech_model=None)
        socket = StarletteSocket(frames, self.clock, step)
        socket.query_params = {"listen": "true"}
        return self.connect(socket, "auditor1", "ura_auditor"), socket

    async def played_out(self, task: asyncio.Task, socket: StarletteSocket) -> None:
        await played_out_or_ended(task, socket)

    def assert_officer_still_on_the_call(self, task: asyncio.Task, socket: StarletteSocket) -> None:
        self.assertFalse(task.done(), "the officer's control socket ended")
        self.assertIsNone(socket.closed)
        self.assertEqual((self.room.state.mode, self.room.state.officer_id), ("bridged", "okello"))
        self.assertFalse(self.room.state.reconnecting_officer, "the officer was put into the reconnect grace")

    async def test_text_frames_from_the_officer_are_limited_to_15_in_10_seconds(self):
        ping = json.dumps({"type": "ping"})
        task, socket = await self.officer_sends([ping] * 20, step=0.0)

        await self.played_out(task, socket)

        self.assert_officer_still_on_the_call(task, socket)
        self.assertEqual(self.counted("officer", "rate"), 5)

    async def test_a_binary_frame_from_the_officer_is_dropped_without_the_reconnect_grace(self):
        task, socket = await self.officer_sends([b"\x00\x01" * 320])

        await self.played_out(task, socket)

        self.assert_officer_still_on_the_call(task, socket)
        self.assertEqual(self.counted("officer", "malformed"), 1)

    async def test_a_flood_from_the_officer_that_lasts_five_seconds_closes_with_1008_and_starts_the_grace(self):
        ping = json.dumps({"type": "ping"})
        task, socket = await self.officer_sends([ping] * 800, step=0.01)

        done, _pending = await asyncio.wait({task}, timeout=10.0)

        self.assertTrue(done, "a sustained flood did not end the officer's control socket")
        self.assertIsNone(task.exception())
        self.assertEqual((socket.closed, socket.close_reason), (1008, "too many frames"))
        self.assertGreater(len(socket.frames), 0, "the socket kept reading after it was closed")
        self.assertGreater(self.counted("officer", "rate"), 0)
        # As for any close: the caller is kept, and the officer has the grace to come back.
        self.assertEqual(
            (self.room.state.mode, self.room.state.reconnecting_officer), ("transferring", "okello")
        )

    async def test_the_officer_hanging_up_within_the_budget_still_ends_the_call(self):
        task, socket = await self.officer_sends([json.dumps({"type": "ping"}), json.dumps({"type": "hangup"})])

        done, _pending = await asyncio.wait({task}, timeout=5.0)

        self.assertTrue(done)
        self.assertEqual((self.room.state.mode, self.room.state.end_reason), ("ended", "officer_ended"))
        self.assertNotEqual(socket.closed, 1008)

    async def test_text_frames_from_a_listener_are_limited_and_the_listener_stays(self):
        ping = json.dumps({"type": "ping"})
        task, socket = await self.listener_sends([ping] * 20, step=0.0)

        await self.played_out(task, socket)

        self.assertFalse(task.done(), "the listener was cut off")
        self.assertIsNone(socket.closed)
        self.assertEqual(len(self.room.state.livekit_observer_identities), 1)
        self.assertEqual(self.counted("officer", "rate"), 5)

    async def test_a_binary_frame_from_a_listener_is_dropped_and_the_listener_stays(self):
        task, socket = await self.listener_sends([b"\x00\x01" * 320])

        await self.played_out(task, socket)

        self.assertFalse(task.done(), "the listener was cut off")
        self.assertIsNone(socket.closed)
        self.assertEqual(self.counted("officer", "malformed"), 1)

    async def test_a_flood_from_a_listener_that_lasts_five_seconds_closes_with_1008(self):
        ping = json.dumps({"type": "ping"})
        task, socket = await self.listener_sends([ping] * 800, step=0.01)

        done, _pending = await asyncio.wait({task}, timeout=10.0)

        self.assertTrue(done, "a sustained flood did not end the listener's socket")
        self.assertEqual((socket.closed, socket.close_reason), (1008, "too many frames"))
        # Out of the room as for any close; the call itself goes on.
        self.assertEqual(self.room.state.livekit_observer_identities, set())
        self.assertEqual(self.room.state.mode, "bridged")

"""OutputHoldGate: Gemini's first reply waits for the language vote, and no longer."""

from __future__ import annotations

import asyncio
import unittest

import pytest

pytest.importorskip("pipecat")

from app.receptionist.hold_gate import InterruptedReplyMute, OutputHoldGate  # noqa: E402
from pipecat.frames.frames import (  # noqa: E402
    EndFrame,
    FunctionCallInProgressFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    OutputTransportMessageUrgentFrame,
    TextFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection  # noqa: E402


def audio(n: int) -> TTSAudioRawFrame:
    return TTSAudioRawFrame(audio=bytes([n]) * 640, sample_rate=16000, num_channels=1)


class HoldGateTests(unittest.IsolatedAsyncioTestCase):
    def build(self, timeout_ms: int = 600) -> tuple[OutputHoldGate, list[object], list[float]]:
        held: list[float] = []
        gate = OutputHoldGate(timeout_ms=timeout_ms, on_held=held.append)
        pushed: list[object] = []

        async def push_frame(frame, direction=FrameDirection.DOWNSTREAM):
            pushed.append(frame)

        gate.push_frame = push_frame  # type: ignore[method-assign]
        return gate, pushed, held

    async def test_passes_everything_when_not_holding(self):
        gate, pushed, _ = self.build()
        frame = audio(1)
        await gate.process_frame(frame, FrameDirection.DOWNSTREAM)
        self.assertEqual(pushed, [frame])

    async def test_buffers_while_holding_and_releases_in_order(self):
        gate, pushed, held = self.build()
        gate.hold()
        frames = [TextFrame(text="Hello"), audio(1), audio(2),
                  OutputTransportMessageUrgentFrame(message={"type": "caption"})]
        for f in frames:
            await gate.process_frame(f, FrameDirection.DOWNSTREAM)
        self.assertEqual(pushed, [])
        self.assertEqual(gate.buffered, 4)
        await gate.release()
        self.assertEqual(pushed, frames)
        self.assertFalse(gate.holding)
        self.assertEqual(len(held), 1)

    async def test_discard_drops_the_reply(self):
        gate, pushed, held = self.build()
        gate.hold()
        await gate.process_frame(audio(1), FrameDirection.DOWNSTREAM)
        await gate.discard()
        self.assertEqual(pushed, [])
        self.assertEqual(gate.buffered, 0)
        self.assertFalse(gate.holding)
        # After a discard the gate is open again.
        frame = audio(2)
        await gate.process_frame(frame, FrameDirection.DOWNSTREAM)
        self.assertEqual(pushed, [frame])

    async def test_lifecycle_and_interruptions_are_never_held(self):
        gate, pushed, _ = self.build()
        gate.hold()
        await gate.process_frame(audio(1), FrameDirection.DOWNSTREAM)
        interruption = InterruptionFrame()
        end = EndFrame()
        await gate.process_frame(interruption, FrameDirection.DOWNSTREAM)
        await gate.process_frame(end, FrameDirection.DOWNSTREAM)
        self.assertEqual(pushed, [interruption, end])
        self.assertEqual(gate.buffered, 0)  # the interrupted reply is gone too

    async def test_notices_that_are_not_the_reply_are_not_held(self):
        gate, pushed, held = self.build()
        gate.hold()
        notice = OutputTransportMessageUrgentFrame(message={"type": "user_speaking", "speaking": True})
        await gate.process_frame(notice, FrameDirection.DOWNSTREAM)
        self.assertEqual(pushed, [notice])
        self.assertEqual(gate.buffered, 0)  # and the hold's clock has not started

    async def test_upstream_frames_pass(self):
        gate, pushed, _ = self.build()
        gate.hold()
        frame = audio(1)
        await gate.process_frame(frame, FrameDirection.UPSTREAM)
        self.assertEqual(pushed, [frame])

    async def test_a_late_vote_costs_at_most_the_timeout(self):
        gate, pushed, held = self.build(timeout_ms=50)
        gate.hold()
        await gate.process_frame(audio(1), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.15)
        self.assertEqual(len(pushed), 1)
        self.assertFalse(gate.holding)
        self.assertEqual(len(held), 1)
        self.assertGreaterEqual(held[0], 40)

    async def test_holding_with_nothing_buffered_costs_nothing(self):
        gate, _, held = self.build()
        gate.hold()
        await gate.release()
        self.assertEqual(held, [])  # nothing was waiting, so nothing was delayed


def barge_in() -> InterruptionFrame:
    frame = InterruptionFrame()
    frame.metadata["local_barge_in"] = True
    return frame


class InterruptedReplyMuteTests(unittest.IsolatedAsyncioTestCase):
    """Gemini keeps streaming a reply its own VAD did not see interrupted."""

    def build(self) -> tuple[InterruptedReplyMute, list[object]]:
        mute = InterruptedReplyMute()
        pushed: list[object] = []

        async def push_frame(frame, direction=FrameDirection.DOWNSTREAM):
            pushed.append(frame)

        mute.push_frame = push_frame  # type: ignore[method-assign]
        return mute, pushed

    async def send(self, mute, *frames) -> None:
        for frame in frames:
            await mute.process_frame(frame, FrameDirection.DOWNSTREAM)

    async def test_the_rest_of_a_reply_talked_over_is_dropped(self):
        mute, pushed = self.build()
        await self.send(mute, LLMFullResponseStartFrame(), audio(1))
        cut = barge_in()
        # Pipecat's Gemini service closes its side on the interruption, then the
        # same reply carries on as if new — start marker, audio, text, end.
        rest = [TTSStartedFrame(), LLMFullResponseStartFrame(), audio(2), TextFrame(text="and"),
                TTSStoppedFrame(), LLMFullResponseEndFrame()]
        await self.send(mute, cut, *rest)
        self.assertEqual(pushed[2:], [cut])
        self.assertFalse(mute.muted)
        # The answer to what the caller said next plays.
        after = [LLMFullResponseStartFrame(), audio(3)]
        await self.send(mute, *after)
        self.assertEqual(pushed[3:], after)

    async def test_a_reply_already_received_needs_no_mute(self):
        mute, pushed = self.build()
        await self.send(mute, LLMFullResponseStartFrame(), audio(1), LLMFullResponseEndFrame(), barge_in())
        self.assertFalse(mute.muted)
        await self.send(mute, audio(2))
        self.assertIs(pushed[-1].__class__, TTSAudioRawFrame)

    async def test_gemini_stopping_by_itself_ends_the_mute(self):
        mute, pushed = self.build()
        await self.send(mute, LLMFullResponseStartFrame(), barge_in())
        self.assertTrue(mute.muted)
        await self.send(mute, InterruptionFrame())  # Gemini's own "interrupted"
        frame = audio(4)
        await self.send(mute, frame)
        self.assertIs(pushed[-1], frame)

    async def test_tool_calls_are_never_dropped(self):
        mute, pushed = self.build()
        await self.send(mute, LLMFullResponseStartFrame(), barge_in())
        call = FunctionCallInProgressFrame(function_name="query_ura_tax_knowledge", tool_call_id="1", arguments={})
        await self.send(mute, call)
        self.assertIs(pushed[-1], call)

    async def test_a_mute_with_no_end_in_sight_gives_up(self):
        mute, pushed = self.build()
        mute.MAX_MUTE_S = 0.0
        await self.send(mute, LLMFullResponseStartFrame(), barge_in())
        await asyncio.sleep(0.01)
        frame = audio(5)
        await self.send(mute, frame)
        self.assertIs(pushed[-1], frame)


if __name__ == "__main__":
    unittest.main()

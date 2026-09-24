"""OutputHoldGate: Gemini's first reply waits for the language vote, and no longer."""

from __future__ import annotations

import asyncio
import unittest

import pytest

pytest.importorskip("pipecat")

from app.receptionist.hold_gate import OutputHoldGate  # noqa: E402
from pipecat.frames.frames import (  # noqa: E402
    EndFrame,
    InterruptionFrame,
    OutputTransportMessageUrgentFrame,
    TextFrame,
    TTSAudioRawFrame,
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


if __name__ == "__main__":
    unittest.main()

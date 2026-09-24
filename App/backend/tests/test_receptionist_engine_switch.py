"""EngineGate in a real Pipecat ParallelPipeline: one engine answers at a time.

Stand-in "engines" answer each TextFrame with a tagged TextFrame, so the
output shows which branch spoke. The property that ruled out Pipecat's own
ServiceSwitcher is the second test: an engine that answers *late*, after the
call has moved away from it, must not be heard.
"""

from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass

import pytest

pytest.importorskip("pipecat")

from app.receptionist.multilingual import ClientEventOutlet, EngineGate  # noqa: E402
from app.receptionist.router import EngineSelector  # noqa: E402
from pipecat.frames.frames import (  # noqa: E402
    DataFrame,
    InterruptionFrame,
    LLMRunFrame,
    TextFrame,
)
from pipecat.pipeline.parallel_pipeline import ParallelPipeline  # noqa: E402
from pipecat.pipeline.pipeline import Pipeline  # noqa: E402
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor  # noqa: E402
from pipecat.tests.utils import SleepFrame, run_test  # noqa: E402


@dataclass
class FlipFrame(DataFrame):
    """In order with the text frames around it (a SystemFrame would overtake them)."""

    engine: str = ""


class Flipper(FrameProcessor):
    """Test stand-in for the router: flips the selector when a FlipFrame passes."""

    def __init__(self, selector: EngineSelector) -> None:
        super().__init__()
        self.selector = selector

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, FlipFrame):
            self.selector.active = frame.engine
            return
        await self.push_frame(frame, direction)


class Engine(FrameProcessor):
    """Answers each TextFrame, optionally after a delay; records what reached it."""

    def __init__(self, name: str, delay: float = 0.0) -> None:
        super().__init__()
        self.tag = name
        self.delay = delay
        self.seen: list[type] = []

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        self.seen.append(type(frame))
        if isinstance(frame, TextFrame) and direction == FrameDirection.DOWNSTREAM:
            if self.delay:
                asyncio.get_running_loop().create_task(self._late(frame.text))
            else:
                await self.push_frame(TextFrame(text=f"{self.tag}:{frame.text}"))
            return
        await self.push_frame(frame, direction)

    async def _late(self, text: str) -> None:
        await asyncio.sleep(self.delay)
        await self.push_frame(TextFrame(text=f"{self.tag}:{text}"))


def build(selector: EngineSelector, gemini: Engine, cascaded: Engine) -> Pipeline:
    return Pipeline([
        Flipper(selector),
        ParallelPipeline(
            [EngineGate(selector, "gemini_live", head=True), gemini, EngineGate(selector, "gemini_live", head=False)],
            [EngineGate(selector, "cascaded", head=True), cascaded, EngineGate(selector, "cascaded", head=False)],
        ),
    ])


def texts(frames) -> list[str]:
    return [f.text for f in frames if isinstance(f, TextFrame)]


class EngineSwitchTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_the_active_engine_answers(self):
        selector = EngineSelector("gemini_live")
        gemini, cascaded = Engine("gemini"), Engine("cascaded")
        down, _ = await run_test(
            build(selector, gemini, cascaded),
            start_timeout=10.0,
            # The sleep lets "a" clear the gates first: a flip applies to frames
            # still queued ahead of them, exactly as the router's would.
            frames_to_send=[TextFrame(text="a"), SleepFrame(sleep=0.2), FlipFrame(engine="cascaded"), TextFrame(text="b")],
        )
        self.assertEqual(texts(down), ["gemini:a", "cascaded:b"])
        self.assertEqual(cascaded.seen.count(TextFrame), 1)  # only "b"

    async def test_a_late_answer_from_the_engine_left_behind_is_not_heard(self):
        selector = EngineSelector("gemini_live")
        gemini, cascaded = Engine("gemini", delay=0.15), Engine("cascaded")
        down, _ = await run_test(
            build(selector, gemini, cascaded),
            start_timeout=10.0,
            frames_to_send=[
                TextFrame(text="question"),
                SleepFrame(sleep=0.05),
                FlipFrame(engine="cascaded"),
                SleepFrame(sleep=0.4),
                TextFrame(text="next"),
                SleepFrame(sleep=0.1),
            ],
        )
        self.assertEqual(texts(down), ["cascaded:next"])

    async def test_priming_and_interruptions_reach_both_engines(self):
        selector = EngineSelector("gemini_live")
        gemini, cascaded = Engine("gemini"), Engine("cascaded")
        down, _ = await run_test(
            build(selector, gemini, cascaded),
            start_timeout=10.0,
            frames_to_send=[LLMRunFrame(), InterruptionFrame()],
        )
        self.assertIn(LLMRunFrame, cascaded.seen)
        self.assertIn(InterruptionFrame, cascaded.seen)
        self.assertIn(LLMRunFrame, gemini.seen)
        # Seen by both branches, but it leaves the switch once.
        self.assertEqual(sum(isinstance(f, LLMRunFrame) for f in down), 1)

    async def test_the_inactive_engine_hears_no_caller_input(self):
        selector = EngineSelector("cascaded")
        gemini, cascaded = Engine("gemini"), Engine("cascaded")
        await run_test(start_timeout=10.0, processor=build(selector, gemini, cascaded), frames_to_send=[TextFrame(text="a")])
        self.assertNotIn(TextFrame, gemini.seen)


class OutletTests(unittest.IsolatedAsyncioTestCase):
    async def test_router_messages_go_downstream_as_urgent_frames(self):
        from pipecat.frames.frames import OutputTransportMessageUrgentFrame

        outlet = ClientEventOutlet()
        pushed: list[object] = []

        async def push_frame(frame, direction=FrameDirection.DOWNSTREAM):
            pushed.append((frame, direction))

        outlet.push_frame = push_frame  # type: ignore[method-assign]
        await outlet.send({"type": "language", "language": "lg"})
        frame, direction = pushed[0]
        self.assertIsInstance(frame, OutputTransportMessageUrgentFrame)
        self.assertEqual(frame.message["language"], "lg")
        self.assertEqual(direction, FrameDirection.DOWNSTREAM)


if __name__ == "__main__":
    unittest.main()

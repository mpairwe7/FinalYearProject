"""ClientEventOutlet: the router's language events reach the caller's screen."""

from __future__ import annotations

import unittest

import pytest

pytest.importorskip("pipecat")

from app.receptionist.multilingual import ClientEventOutlet  # noqa: E402
from pipecat.processors.frame_processor import FrameDirection  # noqa: E402


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

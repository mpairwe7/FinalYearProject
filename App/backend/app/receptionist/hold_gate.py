"""Hold Gemini's reply back while the call's language is still being decided.

Before a call's language is locked, the first content question is heard by
Gemini Live (English/Swahili) *and* by the language sentinel. If the caller
spoke Luganda, Gemini's answer is wrong in the only way that matters — it is
not in Luganda — so it must not be played. This gate, at the tail of the
Gemini branch, buffers everything Gemini produces from the moment the router
calls :meth:`OutputHoldGate.hold` until the vote arrives:

* :meth:`release` — the vote kept the call on Gemini: flush, in order.
* :meth:`discard` — the call moved to another engine: drop it all.

The buffer is capped by time, not size: once the first held frame has waited
``timeout_ms`` (600 ms by default) it is released anyway, so a slow vote costs
an English caller at most that much. The sentinel usually votes before Gemini
has said anything at all, in which case holding costs nothing.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

try:
    from pipecat.frames.frames import (
        Frame,
        InterruptionFrame,
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        OutputAudioRawFrame,
        OutputTransportMessageFrame,
        OutputTransportMessageUrgentFrame,
        TextFrame,
        TTSStartedFrame,
        TTSStoppedFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    _REPLY_FRAMES: tuple[type, ...] = (
        OutputAudioRawFrame,  # TTSAudioRawFrame is one
        TextFrame,  # TTSTextFrame is one
        LLMFullResponseStartFrame,
        LLMFullResponseEndFrame,
        TTSStartedFrame,
        TTSStoppedFrame,
    )
except ImportError:  # pragma: no cover — the gate only exists inside a Pipecat pipeline
    FrameProcessor = object  # type: ignore[assignment,misc]


class OutputHoldGate(FrameProcessor):  # type: ignore[misc,valid-type]
    """Buffers downstream frames while held. See the module docstring."""

    def __init__(self, timeout_ms: int = 600, on_held: Any = None, **kwargs: Any) -> None:
        # Direct mode, like the taps: the gate keeps its own buffer, so a
        # second queue in front of it would only add a hop to every frame.
        super().__init__(enable_direct_mode=True, **kwargs)
        self.timeout_s = max(0, timeout_ms) / 1000.0
        self._on_held = on_held  # callback(held_ms) — the router records it
        self._holding = False
        # True while release() drains the buffer: frames that arrive mid-drain
        # queue behind it instead of overtaking the held ones.
        self._flushing = False
        self._buffer: list[tuple[Frame, FrameDirection]] = []
        self._first_held_at: float | None = None
        self._timer: asyncio.Task[None] | None = None
        # Pinned: a switch away from Gemini is decided but waits for the end
        # of the caller's turn — nothing buffered may time out and play.
        self._pinned = False

    @property
    def holding(self) -> bool:
        return self._holding

    @property
    def buffered(self) -> int:
        return len(self._buffer)

    def hold(self) -> None:
        """Start buffering. Idempotent."""
        self._holding = True

    def pin(self) -> None:
        """Hold with no timeout until release() or discard()."""
        self._holding = True
        self._pinned = True
        self._cancel_timer()

    async def release(self) -> None:
        """Stop holding and play what was buffered, in order."""
        self._holding = False
        self._pinned = False
        self._cancel_timer()
        self._report_held()
        if self._flushing:
            return
        self._flushing = True
        try:
            while self._buffer:
                frame, direction = self._buffer.pop(0)
                await self.push_frame(frame, direction)
        finally:
            self._flushing = False

    async def discard(self) -> None:
        """Stop holding and drop what was buffered — it is never played."""
        self._holding = False
        self._pinned = False
        self._cancel_timer()
        dropped = len(self._buffer)
        self._buffer = []
        self._report_held()
        if dropped:
            logger.info("Discarded %d held Gemini frames after a language switch", dropped)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, InterruptionFrame) and direction == FrameDirection.DOWNSTREAM:
            # The reply being held was interrupted anyway.
            self._buffer = []
            self._cancel_timer()
            self._first_held_at = None
        elif (self._holding or self._flushing) and direction == FrameDirection.DOWNSTREAM and self._holdable(frame):
            if self._holding and not self._buffer:
                self._first_held_at = time.monotonic()
                if not self._pinned:
                    self._timer = asyncio.create_task(self._release_after_timeout())
            self._buffer.append((frame, direction))
            return

        await self.push_frame(frame, direction)

    @staticmethod
    def _holdable(frame: Frame) -> bool:
        """The reply itself: its audio, its text, its start/end markers, its captions.

        Nothing else waits. In particular the transcript tap's "user is
        speaking" notice, which Gemini triggers the moment it hears the caller,
        used to start the hold's clock with no reply in sight.
        """
        if isinstance(frame, (OutputTransportMessageFrame, OutputTransportMessageUrgentFrame)):
            message = frame.message if isinstance(frame.message, dict) else {}
            return message.get("type") == "caption"
        return isinstance(frame, _REPLY_FRAMES)

    async def _release_after_timeout(self) -> None:
        try:
            await asyncio.sleep(self.timeout_s)
        except asyncio.CancelledError:
            return
        self._timer = None
        logger.info("Language vote late; releasing Gemini's reply after %.0f ms", self.timeout_s * 1000)
        await self.release()

    def _cancel_timer(self) -> None:
        timer, self._timer = self._timer, None
        if timer is not None and not timer.done() and timer is not asyncio.current_task():
            timer.cancel()

    def _report_held(self) -> None:
        if self._first_held_at is None:
            return
        held_ms = round((time.monotonic() - self._first_held_at) * 1000, 1)
        self._first_held_at = None
        if self._on_held is not None:
            self._on_held(held_ms)

    async def cleanup(self) -> None:
        self._cancel_timer()
        await super().cleanup()

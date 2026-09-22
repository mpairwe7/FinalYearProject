"""Pipeline taps for caller audio bridging and transcript capture."""

from __future__ import annotations

import logging
from typing import Any

from .hub import hub

logger = logging.getLogger(__name__)

try:
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
    from pipecat.frames.frames import (
        Frame,
        InputAudioRawFrame,
        OutputTransportMessageFrame,
        TranscriptionFrame,
    )
except ImportError:
    class FrameDirection:  # type: ignore[no-redef]
        DOWNSTREAM = 1
        UPSTREAM = 2

    class FrameProcessor:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            pass

        async def push_frame(self, frame: Any, direction: Any = None):
            pass

    class Frame:  # type: ignore[no-redef]
        pass

    class InputAudioRawFrame(Frame):  # type: ignore[no-redef]
        pass

    class OutputTransportMessageFrame(Frame):  # type: ignore[no-redef]
        def __init__(self, message: Any):
            self.message = message

    class TranscriptionFrame(Frame):  # type: ignore[no-redef]
        pass


class CallerAudioTap(FrameProcessor):
    """Taps caller input PCM audio and forwards to the officer browser when bridged."""

    def __init__(self, room: Any, **kwargs: Any) -> None:
        super().__init__(enable_direct_mode=True, **kwargs)
        self.room = room

    async def process_frame(self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM) -> None:
        if isinstance(frame, InputAudioRawFrame):
            if self.room.state.mode == "bridged" and self.room.officer is not None:
                self.room.officer.send_caller_audio(frame.audio)

        await self.push_frame(frame, direction)


class TranscriptTap(FrameProcessor):
    """Captures word confidences from TranscriptionFrame and emits live captions."""

    def __init__(self, room: Any, **kwargs: Any) -> None:
        super().__init__(enable_direct_mode=True, **kwargs)
        self.room = room

    async def process_frame(self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM) -> None:
        if isinstance(frame, TranscriptionFrame):
            logger.info("TranscriptTap: received TranscriptionFrame: text=%r", frame.text)
            res = getattr(frame, "result", None)
            words = getattr(res, "words", None) if res else None
            self.room.state.turn_words = words or []

            # Emit caption to caller and staff
            caption_data = {
                "type": "caption",
                "speaker": "caller",
                "text": frame.text,
                "final": True,
                "turn_id": self.room.state.turn_seq + 1,
            }
            # Send message to caller WebSocket
            await self.push_frame(OutputTransportMessageFrame(caption_data), direction)
            # Publish to staff live view
            hub.publish_call(self.room.call_id, "caption", caption_data)

        await self.push_frame(frame, direction)

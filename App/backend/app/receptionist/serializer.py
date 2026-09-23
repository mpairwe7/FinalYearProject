"""WebSocket message serializer for the browser call client and Pipecat transport."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    from pipecat.serializers.base_serializer import FrameSerializer
    from pipecat.frames.frames import (
        Frame,
        InputAudioRawFrame,
        OutputAudioRawFrame,
        InterruptionFrame,
        OutputTransportMessageFrame,
        OutputTransportMessageUrgentFrame,
    )
except ImportError:
    class Frame:  # type: ignore[no-redef]
        pass

    class FrameSerializer:  # type: ignore[no-redef]
        pass

    class InputAudioRawFrame(Frame):  # type: ignore[no-redef]
        def __init__(self, audio: bytes, sample_rate: int = 16000, num_channels: int = 1):
            self.audio = audio
            self.sample_rate = sample_rate
            self.num_channels = num_channels

    class OutputAudioRawFrame(Frame):  # type: ignore[no-redef]
        def __init__(self, audio: bytes, sample_rate: int = 16000, num_channels: int = 1):
            self.audio = audio
            self.sample_rate = sample_rate
            self.num_channels = num_channels

    class InterruptionFrame(Frame):  # type: ignore[no-redef]
        pass

    class OutputTransportMessageFrame(Frame):  # type: ignore[no-redef]
        def __init__(self, message: Any):
            self.message = message

    class OutputTransportMessageUrgentFrame(Frame):  # type: ignore[no-redef]
        def __init__(self, message: Any):
            self.message = message


class RequestOfficerFrame(Frame):
    """Custom frame fired when the caller clicks or requests 'talk to an officer'."""

    def __init__(self, reason: str = "caller_requested"):
        self.reason = reason


class BrowserCallSerializer(FrameSerializer):
    """Serializes frames to/from the taxpayer browser WebSocket connection."""

    def __init__(self, room: Any = None) -> None:
        self.room = room

    async def serialize(self, frame: Frame) -> bytes | str | None:
        """Convert a Pipecat frame into a WebSocket message."""
        if isinstance(frame, OutputAudioRawFrame):
            return frame.audio

        if isinstance(frame, InterruptionFrame):
            return json.dumps({"type": "interrupt"})

        # The urgent variant is a SystemFrame the transport writes immediately
        # instead of queueing behind pending bot audio — live captions use it so
        # the caller's words are not held back by the reply they precede. It is
        # a sibling class, not a subclass, so it needs naming here.
        if isinstance(frame, (OutputTransportMessageFrame, OutputTransportMessageUrgentFrame)):
            if isinstance(frame.message, (dict, list)):
                return json.dumps(frame.message)
            return str(frame.message)

        return None

    async def deserialize(self, data: bytes | str) -> Frame | None:
        """Convert a WebSocket message into a Pipecat frame."""
        if isinstance(data, bytes):
            return InputAudioRawFrame(audio=data, sample_rate=16000, num_channels=1)

        if isinstance(data, str):
            try:
                msg = json.loads(data)
            except Exception:
                return None

            msg_type = msg.get("type")
            if msg_type == "interrupt":
                return InterruptionFrame()

            if msg_type == "request_officer":
                return RequestOfficerFrame(reason=msg.get("reason", "caller_requested"))

            if msg_type == "hangup":
                if self.room and hasattr(self.room, "end"):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(self.room.end("caller_hangup"))
                    except RuntimeError:
                        pass
                return None

            if msg_type == "ping":
                return None

        return None

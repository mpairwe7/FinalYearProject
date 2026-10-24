"""WebSocket message serializer for the browser call client and Pipecat transport."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from .config import call_language

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
    import itertools
    _fallback_frame_id_seq = itertools.count(1)

    class Frame:  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.id = next(_fallback_frame_id_seq)
            self.name = self.__class__.__name__

        def __post_init__(self) -> None:
            if not hasattr(self, "id"):
                self.id = next(_fallback_frame_id_seq)
            if not hasattr(self, "name"):
                self.name = self.__class__.__name__

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


# Dataclasses, like every Pipecat frame: the base class's __post_init__ gives
# each frame the id and name the pipeline relies on (a ParallelPipeline
# de-duplicates by id). A hand-written __init__ skipped it.
@dataclass
class RequestOfficerFrame(Frame):
    """Custom frame fired when the caller clicks or requests 'talk to an officer'."""

    reason: str = "caller_requested"


@dataclass
class RequestCallbackFrame(Frame):
    """The waiting caller tapped "Call me back instead" (G186)."""


@dataclass
class SetLanguageFrame(Frame):
    """The caller picked the call's language on screen — an override for the rest of the call."""

    language: str = "en"


_ALLOWED_CALL_CONTROLS: frozenset[str] = frozenset(
    {
        "repeat",
        "slower",
        "normal_speed",
        "more_detail",
        "accept_offer",
        "decline_offer",
    }
)


@dataclass
class CallControlFrame(Frame):
    """The caller triggered an on-screen call control (repeat, slower, normal_speed, more_detail, accept_offer, decline_offer)."""

    action: str = ""


@dataclass
class CallTextTurnFrame(Frame):
    """The caller typed a question or identifier (TIN/PRN/NIN) during the call."""

    text: str = ""


_CONTROL_RATE_WINDOW_S = 10.0
_MAX_CONTROL_FRAMES_PER_WINDOW = 15


class BrowserCallSerializer(FrameSerializer):
    """Serializes frames to/from the taxpayer browser WebSocket connection."""

    def __init__(self, room: Any = None) -> None:
        self.room = room
        self._control_timestamps: list[float] = []

    def _allow_control_frame(self) -> bool:
        now = time.monotonic()
        cutoff = now - _CONTROL_RATE_WINDOW_S
        self._control_timestamps = [ts for ts in self._control_timestamps if ts >= cutoff]
        if len(self._control_timestamps) >= _MAX_CONTROL_FRAMES_PER_WINDOW:
            return False
        self._control_timestamps.append(now)
        return True

    async def serialize(self, frame: Frame) -> bytes | str | None:
        """Convert a Pipecat frame into a WebSocket message (with 2G/3G mu-law & caption-only support)."""
        if isinstance(frame, OutputAudioRawFrame):
            state = getattr(self.room, "state", None) if self.room is not None else None
            if state is not None:
                if getattr(state, "caption_only_degraded", False) is True:
                    return None
                if getattr(state, "audio_codec", "pcm16_16k") == "mulaw_8k":
                    from .low_bandwidth import pcm16_16k_to_mulaw_8k

                    return pcm16_16k_to_mulaw_8k(frame.audio)
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
            audio_bytes = data
            state = getattr(self.room, "state", None) if self.room is not None else None
            if state is not None and getattr(state, "audio_codec", "pcm16_16k") == "mulaw_8k":
                from .low_bandwidth import mulaw_8k_to_pcm16_16k

                audio_bytes = mulaw_8k_to_pcm16_16k(data)
            return InputAudioRawFrame(audio=audio_bytes, sample_rate=16000, num_channels=1)

        if isinstance(data, str):
            try:
                msg = json.loads(data)
            except Exception:
                return None
            if not isinstance(msg, dict):
                return None

            msg_type = msg.get("type")
            if msg_type in ("network_quality", "network_telemetry") and self.room is not None:
                from .low_bandwidth import apply_network_telemetry_to_room

                ack = apply_network_telemetry_to_room(self.room, msg)
                return OutputTransportMessageUrgentFrame(ack)

            if msg_type == "interrupt":
                return InterruptionFrame()

            if msg_type == "request_officer":
                return RequestOfficerFrame(reason=msg.get("reason", "caller_requested"))

            if msg_type == "request_callback":
                return RequestCallbackFrame()

            if msg_type == "set_language":
                language = call_language(msg.get("language"))
                return SetLanguageFrame(language=language) if language else None

            if msg_type == "call_control":
                action = str(msg.get("action") or "").strip().lower()
                if action in _ALLOWED_CALL_CONTROLS and self._allow_control_frame():
                    return CallControlFrame(action=action)
                return None

            if msg_type == "text_turn":
                text = " ".join(str(msg.get("text") or "").split()).strip()
                if text and len(text) <= 500 and self._allow_control_frame():
                    return CallTextTurnFrame(text=text)
                return None

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

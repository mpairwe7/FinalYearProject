"""Output gates at the tail of the call pipeline.

:class:`OfficerOutputGate` keeps the AI silent once an officer has the call:
anything it was still saying when the officer joined is dropped before it
reaches the caller. It sees only what comes after it: speech the AI made before
the officer joined is already queued in the output transport, which the bridge
clears itself (``desk._interrupt_ai_output``, G657).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    from pipecat.frames.frames import (
        Frame,
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
    class FrameProcessor:  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def process_frame(self, frame: Any, direction: Any) -> None:
            pass

        async def push_frame(self, frame: Any, direction: Any = None) -> None:
            pass

    class FrameDirection:  # type: ignore[no-redef]
        DOWNSTREAM = 1
        UPSTREAM = 2

    class Frame:  # type: ignore[no-redef]
        pass


def is_reply_frame(frame: Frame) -> bool:
    """The assistant's reply itself: its audio, its text, its start/end markers, its captions.

    Nothing else counts: lifecycle frames, interruptions and status messages
    still reach the caller while an officer has the call.
    """
    if isinstance(frame, (OutputTransportMessageFrame, OutputTransportMessageUrgentFrame)):
        message = frame.message if isinstance(frame.message, dict) else {}
        return message.get("type") == "caption"
    return isinstance(frame, _REPLY_FRAMES)


def is_stale_wait_message(frame: Any, mode: str) -> bool:
    """A queue place or "transferring" status for a wait that is over (G657, ADR-0055).

    The AI sends both when it opens a transfer, queued behind the transfer
    notice it is still saying. An officer can take the call before they play
    (live: 20 s later, while the caller talked to the officer), and a waiting
    screen then replaced the officer's. They mean something only while the
    call waits (*mode* ``transferring``). Read from the message alone, so it
    holds for Pipecat's frames and the stand-ins used without it.
    """
    if mode == "transferring":
        return False
    message = getattr(frame, "message", None)
    if not isinstance(message, dict):
        return False
    kind = message.get("type")
    return kind == "queue" or (kind == "status" and message.get("status") == "transferring")


class OfficerOutputGate(FrameProcessor):  # type: ignore[misc,valid-type]
    """Keeps the AI silent while an officer has the call.

    Sits just before the transport's output in every pipeline. Once an officer
    is bridged the caller talks to them — their audio and captions go straight
    to the caller's socket, not through here — and anything the AI still says
    (a reply it was halfway through when the officer joined) is dropped, as it
    is once the call has ended (``CallRoom.end()`` stops the pipeline only
    after closing the language router).
    So is a queue place or "transferring" status from a wait that is over
    (:func:`is_stale_wait_message`). Everything else passes: lifecycle,
    interruptions, other status.
    """

    def __init__(self, room: Any, **kwargs: Any) -> None:
        super().__init__(enable_direct_mode=True, **kwargs)
        self.room = room

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM:
            mode = self.room.state.mode
            if (mode in ("bridged", "ended") and is_reply_frame(frame)) or is_stale_wait_message(frame, mode):
                return
        await self.push_frame(frame, direction)

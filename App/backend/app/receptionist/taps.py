"""Pipeline taps for caller audio bridging and transcript capture."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ..speech_service import pcm16_to_wav
from .config import get_partial_transcript_interval_s, live_partial_transcripts_enabled
from .hub import hub

logger = logging.getLogger(__name__)

try:
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
    from pipecat.frames.frames import (
        Frame,
        InputAudioRawFrame,
        InterimTranscriptionFrame,
        OutputTransportMessageFrame,
        OutputTransportMessageUrgentFrame,
        TranscriptionFrame,
        UserStartedSpeakingFrame,
        UserStoppedSpeakingFrame,
        VADUserStartedSpeakingFrame,
        VADUserStoppedSpeakingFrame,
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
        def __init__(self, audio: bytes = b"", sample_rate: int = 16000, num_channels: int = 1, *args: Any, **kwargs: Any):
            self.audio = audio
            self.sample_rate = sample_rate
            self.num_channels = num_channels

    class OutputTransportMessageFrame(Frame):  # type: ignore[no-redef]
        def __init__(self, message: Any):
            self.message = message

    class OutputTransportMessageUrgentFrame(Frame):  # type: ignore[no-redef]
        def __init__(self, message: Any):
            self.message = message

    class TranscriptionFrame(Frame):  # type: ignore[no-redef]
        def __init__(self, text: str = "", user_id: str = "", timestamp: str = "", *args: Any, **kwargs: Any):
            self.text = text
            self.user_id = user_id
            self.timestamp = timestamp

    class InterimTranscriptionFrame(TranscriptionFrame):  # type: ignore[no-redef]
        pass

    class UserStartedSpeakingFrame(Frame):  # type: ignore[no-redef]
        pass

    class UserStoppedSpeakingFrame(Frame):  # type: ignore[no-redef]
        pass

    class VADUserStartedSpeakingFrame(Frame):  # type: ignore[no-redef]
        pass

    class VADUserStoppedSpeakingFrame(Frame):  # type: ignore[no-redef]
        pass


# 16 kHz mono int16 — the sample rate the call transport is pinned to.
_BYTES_PER_SECOND = 16000 * 2


class CallerAudioTap(FrameProcessor):
    """Taps caller input PCM audio and forwards to the officer browser when bridged."""

    def __init__(self, room: Any, **kwargs: Any) -> None:
        super().__init__(enable_direct_mode=True, **kwargs)
        self.room = room

    async def process_frame(self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM) -> None:
        if isinstance(frame, InputAudioRawFrame) and self.room.state.mode == "bridged":
            # The caller is talking to the officer now: their audio goes to the
            # officer only. Passed on, it reached Gemini, which answered the
            # caller over the officer.
            if self.room.officer is not None:
                self.room.officer.send_caller_audio(frame.audio)
            return

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

            # Emit caption to caller and staff. Urgent: a plain
            # OutputTransportMessageFrame is a DataFrame, so the output
            # transport queues it behind whatever bot audio is still draining
            # and the caller's own words land on screen seconds late — after
            # the reply they belong to. This one closes the live partials from
            # LivePartialTranscriptTap, so it has to overtake that queue.
            caption_data = {
                "type": "caption",
                "speaker": "caller",
                "text": frame.text,
                "final": True,
                "turn_id": self.room.state.turn_seq + 1,
            }
            # Send message to caller WebSocket
            await self.push_frame(OutputTransportMessageUrgentFrame(caption_data), direction)
            # Publish to staff live view
            hub.publish_call(self.room.call_id, "caption", caption_data)

        await self.push_frame(frame, direction)


class LivePartialTranscriptTap(FrameProcessor):
    """Streams the caller's own words back to them *while* they are still talking.

    Whisper-SALT is a segmented recognizer: :class:`UraWhisperSTT` only yields a
    ``TranscriptionFrame`` once VAD has closed the turn, so without this tap the
    caller first sees their own sentence after they stop speaking — and one
    caption box later it has already been replaced by the assistant's reply.

    So this tap keeps the utterance-so-far in a buffer and re-decodes it every
    ``interval_s`` on the same local model, emitting each hypothesis as a
    ``final: false`` caption that the browser renders in place. Re-decoding the
    whole utterance (rather than appending per-chunk decodes) is what keeps the
    text stable: Whisper rewrites earlier words as context arrives, and a
    1-second slice decoded alone is close to noise.

    Cost, measured on one RTX A6000 with Whisper-SALT resident (2026-09-23):
    0.22 s for 1 s of speech, 0.38 s for 2 s, 0.50 s for 3 s, 0.63 s for 4 s —
    so at the 0.8 s default cadence a decode has finished before the next one is
    due, and the in-flight guard drops the extra if it has not. Partials stop
    the moment VAD closes the turn, so they never contend with the turn's own
    STT call or with the answer generation behind it.
    """

    def __init__(
        self,
        room: Any,
        speech_model: Any,
        language: str = "en",
        interval_s: float | None = None,
        min_audio_s: float = 0.6,
        max_audio_s: float = 28.0,
        preroll_s: float = 0.5,
        **kwargs: Any,
    ) -> None:
        super().__init__(enable_direct_mode=True, **kwargs)
        self.room = room
        self.speech_model = speech_model
        self._language = language
        self.interval_s = get_partial_transcript_interval_s() if interval_s is None else interval_s
        self._min_bytes = int(min_audio_s * _BYTES_PER_SECOND)
        # Whisper's receptive field is 30 s; past that a re-decode silently
        # truncates, so stop emitting partials rather than showing the caller a
        # hypothesis that drops the tail of their own sentence.
        self._max_bytes = int(max_audio_s * _BYTES_PER_SECOND)
        # Silero only calls the turn started after `start_secs` of speech, so the
        # first syllable is already past by the time the buffer opens. Decoding
        # from there cost the caller the opening word of every partial
        # ("Bizinensi entonotono…" came back as "Tonotono…"), so the tap keeps a
        # rolling tail of pre-trigger audio and seeds each turn with it. The
        # final transcript is unaffected — the STT service does its own
        # pre-roll — this only fixes what the caller reads mid-sentence.
        self._preroll_bytes = int(preroll_s * _BYTES_PER_SECOND)
        self._preroll = bytearray()
        self._buffer = bytearray()
        self._speaking = False
        self._turn = 0
        self._last_started_at = 0.0
        self._last_text = ""
        self._pending: asyncio.Task[None] | None = None

    @property
    def language(self) -> str:
        """The call's language now; the constructor value only without a room state."""
        state = getattr(self.room, "state", None)
        return getattr(state, "locale", None) or self._language

    @language.setter
    def language(self, value: str) -> None:
        self._language = value

    @property
    def enabled(self) -> bool:
        """Partials need a local recognizer and the feature left on."""
        return self.speech_model is not None and live_partial_transcripts_enabled()

    async def process_frame(self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM) -> None:
        if self.enabled:
            # VADProcessor broadcasts the VAD* variants; the turn strategies in
            # the context aggregator emit the plain ones. Either may arrive
            # first, and _begin/_end are idempotent so whichever does wins.
            if isinstance(frame, (VADUserStartedSpeakingFrame, UserStartedSpeakingFrame)):
                self._begin_turn()
            elif isinstance(frame, (VADUserStoppedSpeakingFrame, UserStoppedSpeakingFrame)):
                self._end_turn()
            elif isinstance(frame, InputAudioRawFrame):
                if self._speaking:
                    self._buffer.extend(frame.audio)
                    self._maybe_decode()
                else:
                    self._preroll.extend(frame.audio)
                    if len(self._preroll) > self._preroll_bytes:
                        del self._preroll[: len(self._preroll) - self._preroll_bytes]

        await self.push_frame(frame, direction)

    def _begin_turn(self) -> None:
        if self._speaking:
            return
        self._speaking = True
        self._turn += 1
        self._buffer.clear()
        self._buffer.extend(self._preroll)
        self._last_text = ""
        self._last_started_at = time.monotonic()

    def _end_turn(self) -> None:
        # The buffer is dropped, not decoded once more: UraWhisperSTT is already
        # transcribing this same audio and TranscriptTap's final caption is what
        # replaces the partials. A late partial landing after it would overwrite
        # the finished sentence with a worse one, which the turn counter below
        # prevents.
        self._speaking = False
        self._turn += 1
        self._buffer.clear()

    def _maybe_decode(self) -> None:
        if self._pending is not None and not self._pending.done():
            return
        size = len(self._buffer)
        if size < self._min_bytes or size > self._max_bytes:
            return
        if time.monotonic() - self._last_started_at < self.interval_s:
            return
        self._last_started_at = time.monotonic()
        self._pending = asyncio.create_task(self._decode_and_emit(bytes(self._buffer), self._turn))

    async def _decode_and_emit(self, pcm: bytes, turn: int) -> None:
        try:
            result = await asyncio.to_thread(
                self.speech_model.transcribe, pcm16_to_wav(pcm, 16000), 16000, self.language, False
            )
        except Exception:
            logger.debug("Live partial transcription failed", exc_info=True)
            return

        text = (getattr(result, "text", "") or "").strip()
        # Stale-turn guard: the caller stopped talking (or started a new turn)
        # while this decode was running, so its text is no longer what is on
        # screen. Dropping it leaves the final caption in place.
        if not text or turn != self._turn or not self._speaking:
            return
        if text == self._last_text:
            return
        self._last_text = text

        caption_data = {
            "type": "caption",
            "speaker": "caller",
            "text": text,
            "final": False,
            "turn_id": self.room.state.turn_seq + 1,
        }
        await self.push_frame(
            OutputTransportMessageUrgentFrame(caption_data), FrameDirection.DOWNSTREAM
        )
        hub.publish_call(self.room.call_id, "caption", caption_data)
        # The same hypothesis, as a frame the turn strategies read: while the
        # assistant is talking, two words of it are what count as a barge-in
        # (see turns.py). The user aggregator consumes it; it never reaches
        # the brain as text.
        await self.push_frame(
            InterimTranscriptionFrame(
                text=text,
                user_id=getattr(self.room.state, "user_id", "") or "",
                timestamp=str(time.time()),
            ),
            FrameDirection.DOWNSTREAM,
        )

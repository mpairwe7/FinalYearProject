"""Pipecat STT service adapter for URA Whisper-SALT."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from ..speech_service import TranscribeResult, pcm16_to_wav

logger = logging.getLogger(__name__)

try:
    from pipecat.services.stt_service import SegmentedSTTService
    from pipecat.frames.frames import Frame, TranscriptionFrame
except ImportError:
    class SegmentedSTTService:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            pass

    class Frame:  # type: ignore[no-redef]
        pass

    class TranscriptionFrame(Frame):  # type: ignore[no-redef]
        def __init__(
            self,
            text: str,
            user_id: str = "",
            timestamp: str = "",
            language: str = "en",
            result: Any = None,
        ):
            self.text = text
            self.user_id = user_id
            self.timestamp = timestamp
            self.language = language
            self.result = result


try:
    from pipecat.services.settings import STTSettings
except ImportError:
    STTSettings = None


def _strip_wav_header(audio_bytes: bytes) -> bytes:
    """Strip WAV header if audio arrives containerized with RIFF header."""
    if audio_bytes.startswith(b"RIFF") and len(audio_bytes) > 44:
        # Search for 'data' marker
        idx = audio_bytes.find(b"data")
        if idx != -1 and len(audio_bytes) > idx + 8:
            return audio_bytes[idx + 8 :]
        return audio_bytes[44:]
    return audio_bytes


class UraWhisperSTT(SegmentedSTTService):
    """Segmented STT service running URA Whisper-SALT in an asyncio thread pool.

    With a ``room`` the Whisper-SALT language token is read from
    ``room.state.locale`` for every segment, so a call that changes language
    is transcribed in its new one from the next utterance; ``language`` is
    the value used without a room (and the initial one with).
    """

    def __init__(
        self,
        speech_model: Any,
        user_id: str = "",
        language: str = "en",
        room: Any = None,
        **kwargs: Any,
    ) -> None:
        settings = STTSettings(language=language) if STTSettings else None
        super().__init__(
            sample_rate=16000,
            audio_passthrough=False,
            ttfs_p99_latency=12.0,
            settings=settings,
            **kwargs,
        )
        self.speech_model = speech_model
        self.user_id = user_id
        self.room = room
        self._language = language
        self.last_stt_ms: float = 0.0

    @property
    def language(self) -> str:
        if self.room is not None:
            return self.room.state.locale or self._language
        return self._language

    @language.setter
    def language(self, value: str) -> None:
        self._language = value

    @property
    def wants_wav_segments(self) -> bool:
        """Local Whisper-SALT consumes raw 16-bit PCM directly."""
        return False

    async def start(self, frame: Any) -> None:
        """Start STT service and broadcast STTMetadataFrame to arm turn stop timeouts."""
        await super().start(frame)
        await self.broadcast_service_metadata()

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        """Transcribe segmented PCM audio with word-level confidence."""
        pcm = _strip_wav_header(audio)
        if not pcm or len(pcm) < 320:  # < 10ms of 16kHz audio
            return

        language = self.language
        logger.info("UraWhisperSTT.run_stt: transcribing %d bytes of PCM (%s)", len(pcm), language)
        t0 = time.perf_counter()
        try:
            res: TranscribeResult = await asyncio.to_thread(
                self.speech_model.transcribe,
                pcm16_to_wav(pcm, 16000),
                16000,
                language,
                with_words=True,
            )
        except Exception:
            logger.exception("UraWhisperSTT transcription failed")
            return

        self.last_stt_ms = round((time.perf_counter() - t0) * 1000, 1)
        logger.info("UraWhisperSTT.run_stt completed in %.1f ms: text=%r", self.last_stt_ms, getattr(res, "text", ""))

        if res and res.text:
            frame = TranscriptionFrame(
                text=res.text,
                user_id=self.user_id,
                timestamp=str(time.time()),
                language=language,
            )
            # Stash the full TranscribeResult on the frame
            frame.result = res
            yield frame

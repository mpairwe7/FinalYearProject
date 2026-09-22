"""Pipecat STT service adapter for URA Whisper-SALT."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from ..speech_service import TranscribeResult

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
    """Segmented STT service running URA Whisper-SALT in an asyncio thread pool."""

    def __init__(
        self,
        speech_model: Any,
        user_id: str = "",
        language: str = "en",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.speech_model = speech_model
        self.user_id = user_id
        self.language = language
        self.last_stt_ms: float = 0.0

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        """Transcribe segmented PCM audio with word-level confidence."""
        pcm = _strip_wav_header(audio)
        if not pcm or len(pcm) < 320:  # < 10ms of 16kHz audio
            return

        t0 = time.perf_counter()
        try:
            res: TranscribeResult = await asyncio.to_thread(
                self.speech_model.transcribe,
                pcm,
                16000,
                self.language,
                with_words=True,
            )
        except Exception:
            logger.exception("UraWhisperSTT transcription failed")
            return

        self.last_stt_ms = round((time.perf_counter() - t0) * 1000, 1)

        if res and res.text:
            frame = TranscriptionFrame(
                text=res.text,
                user_id=self.user_id,
                timestamp=str(time.time()),
                language=self.language,
            )
            # Stash the full TranscribeResult on the frame
            frame.result = res
            yield frame

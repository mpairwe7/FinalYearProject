"""Pipecat TTS service adapter for URA SpeechModel synthesis."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

import numpy as np

from .config import get_tts_voice

logger = logging.getLogger(__name__)

try:
    from pipecat.services.tts_service import TTSService
    from pipecat.frames.frames import Frame, TTSAudioRawFrame
except ImportError:
    class TTSService:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            pass

    class Frame:  # type: ignore[no-redef]
        pass

    class TTSAudioRawFrame(Frame):  # type: ignore[no-redef]
        def __init__(self, audio: bytes, sample_rate: int = 16000, num_channels: int = 1):
            self.audio = audio
            self.sample_rate = sample_rate
            self.num_channels = num_channels


class UraSpeechTTS(TTSService):
    """TTS service adapter that calls SpeechModel.synthesize in thread pool."""

    def __init__(
        self,
        speech_model: Any,
        voice: str | None = None,
        language: str = "en",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.speech_model = speech_model
        self.voice = voice or get_tts_voice()
        self.language = language
        self.last_first_chunk_ms: float = 0.0

    async def run_tts(self, text: str) -> AsyncGenerator[Frame, None]:
        """Synthesize text and stream chunked PCM16 LE frames."""
        clean_text = text.strip()
        if not clean_text:
            return

        t0 = time.perf_counter()
        try:
            res = await asyncio.to_thread(
                self.speech_model.synthesize,
                clean_text,
                self.language,
                self.voice,
            )
        except Exception:
            logger.exception("UraSpeechTTS synthesis failed for text: %s", clean_text[:60])
            return

        if not res or not res.audio:
            return

        # Decode audio bytes into float32 samples at 16000Hz
        try:
            samples = self.speech_model._decode_audio_bytes(res.audio, target_sr=16000)
            if len(samples) == 0:
                return
            pcm_i16 = (np.asarray(samples) * 32767).clip(-32768, 32767).astype(np.int16)
            pcm_bytes = pcm_i16.tobytes()
        except Exception:
            logger.exception("Failed decoding synthesized audio to PCM16")
            return

        self.last_first_chunk_ms = round((time.perf_counter() - t0) * 1000, 1)

        # Chunk into 20ms frames (640 bytes = 320 samples @ 16-bit mono 16kHz)
        chunk_size = 640
        for i in range(0, len(pcm_bytes), chunk_size):
            chunk = pcm_bytes[i : i + chunk_size]
            yield TTSAudioRawFrame(audio=chunk, sample_rate=16000, num_channels=1)
            # Yield control to event loop
            await asyncio.sleep(0.001)

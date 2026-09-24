"""Pipecat TTS service adapter for URA SpeechModel synthesis."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

import numpy as np

from .. import orpheus_tts
from .config import allow_edge_standin_lg, get_tts_voice

logger = logging.getLogger(__name__)

try:
    from pipecat.frames.frames import Frame, TTSAudioRawFrame
    from pipecat.services.tts_service import TTSService
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


try:
    from pipecat.services.settings import TTSSettings
except ImportError:
    TTSSettings = None


def decode_to_pcm16(speech_model: Any, audio: bytes) -> bytes:
    """Synthesised audio (any container) as 16 kHz mono PCM16 — what the caller's player takes."""
    samples = speech_model._decode_audio_bytes(audio, target_sr=16000)
    if len(samples) == 0:
        return b""
    return (np.asarray(samples) * 32767).clip(-32768, 32767).astype(np.int16).tobytes()


def synthesize_pcm16(speech_model: Any, text: str, language: str, voice: str | None = None) -> bytes:
    """A line spoken outside the pipeline (an officer joining or closing the call). Blocking.

    Empty when synthesis fails, or when the only voice for Luganda is the
    English stand-in and that is not allowed — the caption still goes out.
    """
    if speech_model is None or not text:
        return b""
    try:
        res = speech_model.synthesize(text, voice=voice or get_tts_voice(), language=language)
    except Exception:
        logger.exception("Synthesis failed for a line outside the pipeline")
        return b""
    if not res or not res.audio:
        return b""
    backend = str(getattr(res, "backend", "") or "")
    if language == "lg" and backend.startswith("edge") and not allow_edge_standin_lg():
        return b""
    try:
        return decode_to_pcm16(speech_model, res.audio)
    except Exception:
        logger.exception("Failed decoding a line outside the pipeline to PCM16")
        return b""


class UraSpeechTTS(TTSService):
    """TTS service adapter that calls SpeechModel.synthesize in thread pool.

    The language is read from ``room.state.locale`` on every sentence when a
    room is given — a multilingual call can change language between two
    sentences — and from the constructor argument otherwise.

    For a language Orpheus voices (Luganda by default, see
    :mod:`app.orpheus_tts`) the sentence is *streamed*: the caller hears the
    first frame ~350 ms after it is asked for instead of after the whole
    sentence renders. Pre-warmed lines come from the phrase cache instead,
    and a streamed sentence is added to it for next time.
    """

    def __init__(
        self,
        speech_model: Any,
        voice: str | None = None,
        language: str = "en",
        room: Any = None,
        **kwargs: Any,
    ) -> None:
        v = voice or get_tts_voice()
        settings = TTSSettings(language=language, voice=v) if TTSSettings else None
        super().__init__(sample_rate=16000, settings=settings, **kwargs)
        self.speech_model = speech_model
        self.voice = v
        self.room = room
        self._language = language
        self.last_first_chunk_ms: float = 0.0
        self.last_backend: str = ""

    @property
    def language(self) -> str:
        if self.room is not None:
            return self.room.state.locale or self._language
        return self._language

    @language.setter
    def language(self, value: str) -> None:
        self._language = value

    async def run_tts(self, text: str, context_id: str = "", *args: Any, **kwargs: Any) -> AsyncGenerator[Frame, None]:
        """Synthesize text and stream chunked PCM16 LE frames."""
        clean_text = text.strip()
        if not clean_text:
            return
        language = self.language

        t0 = time.perf_counter()
        if self._should_stream(clean_text, language):
            try:
                async for frame in self._stream_orpheus(clean_text, language, context_id, t0):
                    yield frame
                return
            except orpheus_tts.OrpheusUnavailable as exc:
                logger.info("Orpheus unavailable mid-call (%s); using the synthesis chain", exc)

        try:
            res = await asyncio.to_thread(
                self.speech_model.synthesize,
                clean_text,
                voice=self.voice,
                language=language,
            )
        except Exception:
            logger.exception("UraSpeechTTS synthesis failed for text: %s", clean_text[:60])
            return

        if not res or not res.audio:
            return
        backend = str(getattr(res, "backend", "") or "")
        self.last_backend = backend
        if language == "lg" and backend.startswith("edge") and not allow_edge_standin_lg():
            # edge-tts has no Luganda voice; what it returned is an English
            # speaker reading Luganda. The caption still reaches the screen.
            logger.warning("Dropping English stand-in voice for a Luganda line (%s)", backend)
            return

        # Decode audio bytes into PCM16 at 16000Hz
        try:
            pcm_bytes = decode_to_pcm16(self.speech_model, res.audio)
            if not pcm_bytes:
                return
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

    def _should_stream(self, text: str, language: str) -> bool:
        if not orpheus_tts.speaker_for(language):
            return False
        cache_get = getattr(self.speech_model, "tts_cache_get", None)
        # A pre-warmed or already-spoken line is instant from the cache.
        return not (callable(cache_get) and cache_get(text, self.voice, language) is not None)

    async def _stream_orpheus(
        self, text: str, language: str, context_id: str, t0: float
    ) -> AsyncGenerator[Frame, None]:
        pieces: list[bytes] = []

        async def source() -> AsyncIterator[bytes]:
            async for pcm in orpheus_tts.stream(text, language):
                if not pieces:
                    self.last_first_chunk_ms = round((time.perf_counter() - t0) * 1000, 1)
                pieces.append(pcm)
                yield pcm

        self.last_backend = "orpheus_salt"
        async for frame in self._stream_audio_frames_from_iterator(
            source(), in_sample_rate=orpheus_tts.SAMPLE_RATE, context_id=context_id
        ):
            yield frame

        cache_put = getattr(self.speech_model, "tts_cache_put", None)
        if pieces and callable(cache_put):
            self._remember(cache_put, text, language, b"".join(pieces))

    def _remember(self, cache_put: Any, text: str, language: str, pcm: bytes) -> None:
        from ..speech_service import SynthesizeResult

        n = len(pcm) // 2
        cache_put(text, self.voice, language, SynthesizeResult(
            audio=orpheus_tts.pcm16_to_wav(pcm),
            sample_rate=orpheus_tts.SAMPLE_RATE,
            num_samples=n,
            duration_s=round(n / orpheus_tts.SAMPLE_RATE, 3),
            latency_s=0.0,
            backend="orpheus_salt",
            voice=orpheus_tts.speaker_for(language) or "",
        ))


def prewarm_receptionist_phrases(speech_model: Any, languages: tuple[str, ...]) -> dict[str, dict[str, int]]:
    """Render the receptionist's fixed lines for *languages* into the phrase cache.

    Uses the same voice argument :class:`UraSpeechTTS` passes, so the cache
    keys match what a call will look up. Blocking — run it off the event loop.
    """
    from .phrases import prewarm_phrases

    outcome: dict[str, dict[str, int]] = {}
    for lang in languages:
        outcome[lang] = speech_model.prewarm_phrases(lang, prewarm_phrases(lang), voice=get_tts_voice())
    return outcome

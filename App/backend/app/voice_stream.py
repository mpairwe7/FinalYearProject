"""Streaming voice engine — VAD, barge-in, sentence-chunked TTS (2026).

This module provides the core streaming voice pipeline that powers the
WebSocket ``/v1/voice/chat/stream`` endpoint.  It extends the existing
batch :class:`SpeechModel` with real-time capabilities:

* **Energy-based VAD** with hysteresis (no silero-vad dependency)
* **Sentence-chunked TTS** for sub-second time-to-first-audio
* **Barge-in** via an asyncio Event that cancels TTS mid-stream
* **Per-session state** (``VoiceSession``) for multi-turn conversations

Architecture::

    Client PCM chunks  ──▶  VAD  ──▶  utterance buffer
                                          │
                                          ▼  (utterance complete)
                              ASR ──▶ [MT] ──▶ LLM ──▶ [MT] ──▶ TTS
                                                                  │
                              ◄── sentence chunks ◄───────────────┘
                              (cancellable via barge-in)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import re
import struct
import time
import uuid
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any, AsyncGenerator
from unittest.mock import Mock

import numpy as np

if TYPE_CHECKING:
    from .service import ChatModel
    from .speech_service import SpeechModel, SynthesizeResult, TranscribeResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# VAD thresholds — tunable via env vars
_VAD_ENERGY_THRESHOLD = float(os.getenv("VOICE_VAD_ENERGY_THRESHOLD", "0.015"))
_VAD_SILENCE_MS = int(os.getenv("VOICE_VAD_SILENCE_MS", "600"))
_VAD_MIN_SPEECH_MS = int(os.getenv("VOICE_VAD_MIN_SPEECH_MS", "250"))
_VAD_MAX_UTTERANCE_S = float(os.getenv("VOICE_VAD_MAX_UTTERANCE_S", "30.0"))

# Hard wall-clock ceiling for the LLM stage of a voice turn. The chat model
# has its own internal deadlines, but a stall anywhere else in generate()
# must not hang the WebSocket session forever.
_VOICE_LLM_DEADLINE_S = float(os.getenv("VOICE_LLM_DEADLINE_S", "45"))

# Sentence split regex — split on .!? followed by whitespace
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


async def _run_blocking(func, *args):
    """Run blocking speech/LLM calls off-loop, but keep unit-test mocks inline."""
    target = getattr(func, "func", func)
    if isinstance(target, Mock):
        return func(*args)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, func, *args)


async def _translate_with_retry(speech, text: str, source: str, target: str):
    """One transient-failure retry before declaring the MT stage degraded.

    Returns the TranslateResult on success, None when both attempts fail —
    the caller decides how to degrade (never silently swap languages).
    """
    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            result = await _run_blocking(speech.translate, text, source, target)
            if result.text and not result.error:
                return result
            logger.warning(
                "MT %s->%s attempt %d returned error: %s", source, target, attempt, result.error
            )
        except Exception as exc:  # noqa: BLE001 — degrade, don't crash the session
            last_exc = exc
            logger.warning("MT %s->%s attempt %d raised: %s", source, target, attempt, exc)
    if last_exc is not None:
        logger.error("MT %s->%s failed after retry: %s", source, target, last_exc)
    return None


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VADConfig:
    """Configurable VAD thresholds, env-overridable."""

    energy_threshold: float = _VAD_ENERGY_THRESHOLD
    silence_duration_ms: int = _VAD_SILENCE_MS
    min_speech_duration_ms: int = _VAD_MIN_SPEECH_MS
    max_utterance_s: float = _VAD_MAX_UTTERANCE_S
    sample_rate: int = 16_000

    @classmethod
    def from_sensitivity(cls, sensitivity: str = "medium", sample_rate: int = 16_000) -> VADConfig:
        """Create a VADConfig from a human-friendly sensitivity level."""
        presets = {
            "low": cls(energy_threshold=0.025, silence_duration_ms=800, sample_rate=sample_rate),
            "medium": cls(energy_threshold=0.015, silence_duration_ms=600, sample_rate=sample_rate),
            "high": cls(energy_threshold=0.008, silence_duration_ms=400, sample_rate=sample_rate),
        }
        return presets.get(sensitivity, presets["medium"])


@dataclass
class VoiceStreamEvent:
    """Wire-format event sent/received over WebSocket."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# VoiceSession
# ---------------------------------------------------------------------------


class VoiceSession:
    """One user's streaming voice conversation.

    Created per WebSocket connection.  Manages VAD state, routes audio
    through the existing :class:`SpeechModel` for ASR/TTS, and orchestrates
    the full RAG pipeline via :class:`ChatModel`.
    """

    def __init__(
        self,
        session_id: str,
        speech: SpeechModel,
        chat_model: ChatModel,
        vad_config: VADConfig | None = None,
        language: str = "en",
        voice: str | None = None,
        conversation_id: str | None = None,
        tts_enabled: bool = True,
        top_k: int = 4,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        self.session_id = session_id
        self._speech = speech
        self._chat_model = chat_model
        self.vad = vad_config or VADConfig()
        self.language = language
        self.voice = voice
        self.conversation_id = conversation_id or str(uuid.uuid4())
        self.tts_enabled = tts_enabled
        self.top_k = top_k
        self.user_id = user_id or ""
        self.tenant_id = tenant_id or "default"

        # VAD state
        self._audio_buffer = bytearray()
        self._is_speaking = False
        self._silence_samples = 0
        self._utterance_start: float | None = None

        # Barge-in
        self._cancelled = asyncio.Event()

        # Metrics
        self._turn_count = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def handle_audio_chunk(self, pcm_bytes: bytes) -> AsyncGenerator[VoiceStreamEvent, None]:
        """Feed raw PCM16 LE mono audio into the VAD pipeline.

        Yields ``vad_state`` events on transitions and ``transcript_final``
        + pipeline events when an utterance is complete.
        """
        was_speaking = self._is_speaking
        is_speech, utterance_complete = self._vad_detect(pcm_bytes)

        # Emit VAD state change
        if is_speech and not was_speaking:
            yield VoiceStreamEvent(type="vad_state", data={"speaking": True})
        elif not is_speech and was_speaking and not utterance_complete:
            # Still in hysteresis — don't emit yet
            pass

        if utterance_complete and len(self._audio_buffer) > 0:
            # VAD says utterance is done — process it
            utterance_audio = bytes(self._audio_buffer)
            self._audio_buffer.clear()
            self._is_speaking = False
            self._silence_samples = 0
            self._utterance_start = None

            yield VoiceStreamEvent(type="vad_state", data={"speaking": False})

            # Check minimum duration
            duration_ms = (len(utterance_audio) / 2) / self.vad.sample_rate * 1000
            if duration_ms < self.vad.min_speech_duration_ms:
                logger.debug(
                    "Utterance too short (%.0fms < %dms), discarding",
                    duration_ms,
                    self.vad.min_speech_duration_ms,
                )
                return

            # Reset barge-in for new turn
            self._cancelled.clear()
            self._turn_count += 1

            # Run the full pipeline
            async for event in self.process_utterance(utterance_audio):
                yield event

    async def process_utterance(self, audio: bytes) -> AsyncGenerator[VoiceStreamEvent, None]:
        """Full pipeline: ASR -> [MT] -> LLM -> [MT] -> TTS.

        Yields streaming events at each stage.  Checks ``_cancelled``
        between stages for barge-in support.
        """
        timings: dict[str, float] = {}
        audio_hash = hashlib.sha256(audio).hexdigest()[:16]

        # ── Stage 1: ASR ──────────────────────────────────────────────
        t0 = time.perf_counter()
        try:
            asr_result = await _run_blocking(
                self._speech.transcribe,
                audio,
                self.vad.sample_rate,
                self.language if self.language != "auto" else None,
            )
        except Exception as exc:
            logger.error("Streaming ASR failed: %s", exc)
            yield VoiceStreamEvent(
                type="error",
                data={"detail": f"ASR failed: {exc}", "recoverable": True, "stage": "asr"},
            )
            return

        timings["asr_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        if asr_result.error or not asr_result.text.strip():
            yield VoiceStreamEvent(
                type="transcript_final",
                data={
                    "text": "",
                    "language": asr_result.language,
                    "latency_s": timings["asr_ms"] / 1000,
                    "backend": asr_result.backend,
                    "audio_hash": audio_hash,
                    "error": asr_result.error or "No speech detected",
                },
            )
            return

        detected_lang = asr_result.language or self.language
        yield VoiceStreamEvent(
            type="transcript_final",
            data={
                "text": asr_result.text,
                "language": detected_lang,
                "latency_s": timings["asr_ms"] / 1000,
                "backend": asr_result.backend,
                "audio_hash": audio_hash,
            },
        )

        if self._cancelled.is_set():
            return

        # ── Stage 2: MT (lg->en) ─────────────────────────────────────
        query_text = asr_result.text
        llm_locale = "en"
        timings["mt_ms"] = 0.0
        mt_backend = ""
        mt_degraded: list[str] = []

        if detected_lang == "lg":
            t0 = time.perf_counter()
            mt_result = await _translate_with_retry(self._speech, asr_result.text, "lg", "en")
            if mt_result is not None:
                query_text = mt_result.text
                mt_backend = mt_result.backend
            else:
                # Degraded: send the original Luganda to the LLM and tell it
                # so (multilingual prompt rule) rather than mislabel it as
                # English; surface the degradation to the client.
                llm_locale = detected_lang
                mt_degraded.append("lg-en")
                yield VoiceStreamEvent(
                    type="mt_degraded",
                    data={"direction": "lg-en", "detail": "translation unavailable"},
                )
            timings["mt_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        if self._cancelled.is_set():
            return

        # ── Stage 3: LLM (hard deadline — a stall must not hang the WS) ──
        t0 = time.perf_counter()
        try:
            llm_result = await asyncio.wait_for(
                _run_blocking(
                    partial(
                        self._chat_model.generate,
                        message=query_text,
                        conversation_id=self.conversation_id,
                        top_k=self.top_k,
                        locale=llm_locale,
                        user_id=self.user_id or None,
                        tenant_id=self.tenant_id,
                    ),
                ),
                timeout=_VOICE_LLM_DEADLINE_S,
            )
            reply_text = llm_result.get("reply", "")
        except asyncio.TimeoutError:
            logger.error("LLM generation exceeded %.0fs voice deadline", _VOICE_LLM_DEADLINE_S)
            yield VoiceStreamEvent(
                type="error",
                data={
                    "detail": "The assistant took too long to answer. Please try again.",
                    "recoverable": True,
                    "stage": "llm",
                },
            )
            return
        except Exception as exc:
            logger.error("LLM generation failed: %s", exc)
            yield VoiceStreamEvent(
                type="error",
                data={"detail": f"LLM failed: {exc}", "recoverable": True, "stage": "llm"},
            )
            return

        timings["llm_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        if self._cancelled.is_set():
            return

        # ── Stage 4: MT (en->lg) ─────────────────────────────────────
        reply_for_tts = reply_text
        tts_lang = detected_lang
        if detected_lang == "lg" and "lg-en" not in mt_degraded:
            t0 = time.perf_counter()
            mt_back = await _translate_with_retry(self._speech, reply_text, "en", "lg")
            if mt_back is not None:
                reply_for_tts = mt_back.text
                mt_backend = mt_back.backend
            else:
                # Degraded: speak the English reply with an English voice —
                # intelligible English beats a Luganda voice mangling it.
                tts_lang = "en"
                mt_degraded.append("en-lg")
                yield VoiceStreamEvent(
                    type="mt_degraded",
                    data={"direction": "en-lg", "detail": "translation unavailable"},
                )
            timings["mt_ms"] += round((time.perf_counter() - t0) * 1000, 1)
        elif detected_lang == "lg":
            # Inbound MT already degraded — the reply is whatever language
            # the LLM answered in; keep the detected voice only if the reply
            # was generated for that locale.
            tts_lang = detected_lang

        if self._cancelled.is_set():
            return

        # ── Stage 5: Sentence-chunked TTS ─────────────────────────────
        tts_first_chunk_ms: float | None = None
        tts_t0 = time.perf_counter()
        chunk_idx = 0

        if self.tts_enabled and reply_for_tts.strip():
            sentences = _split_sentences(reply_for_tts)
            audio_started = False

            for sentence in sentences:
                if self._cancelled.is_set():
                    break

                # Emit text chunk
                yield VoiceStreamEvent(
                    type="reply_text",
                    data={"text": sentence, "chunk_index": chunk_idx},
                )

                # Synthesize this sentence
                try:
                    tts_result = await _run_blocking(
                        self._speech.synthesize,
                        sentence,
                        self.voice,
                        tts_lang,
                    )
                except Exception as exc:
                    logger.warning("TTS failed for chunk %d: %s", chunk_idx, exc)
                    chunk_idx += 1
                    continue

                if tts_first_chunk_ms is None:
                    tts_first_chunk_ms = round((time.perf_counter() - tts_t0) * 1000, 1)

                if tts_result.audio and not tts_result.error:
                    # Send audio_start before first successful audio chunk
                    if not audio_started:
                        yield VoiceStreamEvent(
                            type="audio_start",
                            data={"sample_rate": tts_result.sample_rate},
                        )
                        audio_started = True

                    yield VoiceStreamEvent(
                        type="audio_chunk",
                        data={
                            "audio": tts_result.audio,  # raw bytes, sent as binary WS frame
                            "sample_rate": tts_result.sample_rate,
                            "chunk_index": chunk_idx,
                        },
                    )
                chunk_idx += 1

            if not audio_started and not self._cancelled.is_set():
                # Every sentence failed to synthesize — tell the client the
                # turn is text-only instead of ending in silent confusion.
                yield VoiceStreamEvent(
                    type="tts_degraded",
                    data={"detail": "voice output unavailable for this reply"},
                )
            yield VoiceStreamEvent(type="audio_end", data={})
        else:
            # No TTS — just emit the reply text as a single chunk
            yield VoiceStreamEvent(
                type="reply_text",
                data={"text": reply_for_tts, "chunk_index": 0},
            )

        timings["tts_first_chunk_ms"] = tts_first_chunk_ms or 0.0
        timings["total_ms"] = round(
            timings["asr_ms"] + timings["mt_ms"] + timings["llm_ms"]
            + (tts_first_chunk_ms or 0.0),
            1,
        )

        # ── Metadata ──────────────────────────────────────────────────
        yield VoiceStreamEvent(
            type="reply_meta",
            data={
                "sources": llm_result.get("sources", []),
                "citations": llm_result.get("citations", []),
                "faithfulness_score": llm_result.get("faithfulness_score"),
                "retrieval_mode": llm_result.get("retrieval_mode", "keyword"),
                "conversation_id": self.conversation_id,
                "reply_language": tts_lang,
                "mt_degraded": mt_degraded,
            },
        )

        yield VoiceStreamEvent(
            type="latency_report",
            data={
                "asr_ms": timings["asr_ms"],
                "mt_ms": timings["mt_ms"],
                "llm_ms": timings["llm_ms"],
                "tts_first_chunk_ms": timings.get("tts_first_chunk_ms", 0.0),
                "total_ms": timings["total_ms"],
                "asr_backend": asr_result.backend,
                "tts_backend": self._speech._breakers["tts"].name if self._speech else "",
                "mt_backend": mt_backend,
            },
        )

    async def barge_in(self) -> None:
        """Interrupt current TTS playback.

        Sets the cancelled flag so the TTS generator stops between
        sentence chunks.
        """
        self._cancelled.set()
        self._audio_buffer.clear()
        self._is_speaking = False
        self._silence_samples = 0
        logger.info("Barge-in triggered (session=%s, turn=%d)", self.session_id, self._turn_count)

    def close(self) -> None:
        """Cleanup session resources."""
        self._audio_buffer.clear()
        self._cancelled.set()
        logger.info(
            "Voice session closed (session=%s, turns=%d)",
            self.session_id,
            self._turn_count,
        )

    # ------------------------------------------------------------------
    # VAD
    # ------------------------------------------------------------------

    def _vad_detect(self, pcm_chunk: bytes) -> tuple[bool, bool]:
        """Energy-based Voice Activity Detection with hysteresis.

        Args:
            pcm_chunk: Raw PCM16 LE mono audio bytes.

        Returns:
            (is_speech, utterance_complete) — True/True means the user
            finished speaking and the accumulated buffer is ready for ASR.
        """
        # Convert PCM16 LE to float32
        n_samples = len(pcm_chunk) // 2
        if n_samples == 0:
            return False, False

        samples = np.frombuffer(pcm_chunk, dtype=np.int16).astype(np.float32) / 32768.0

        # RMS energy
        rms = float(np.sqrt(np.mean(samples ** 2)))
        is_speech = rms > self.vad.energy_threshold

        if is_speech:
            if not self._is_speaking:
                self._is_speaking = True
                self._utterance_start = time.perf_counter()
            self._silence_samples = 0
            self._audio_buffer.extend(pcm_chunk)
        else:
            if self._is_speaking:
                self._silence_samples += n_samples
                # Still accumulate audio during silence hysteresis
                self._audio_buffer.extend(pcm_chunk)

                silence_ms = (self._silence_samples / self.vad.sample_rate) * 1000
                if silence_ms >= self.vad.silence_duration_ms:
                    # Utterance complete
                    return False, True

        # Check max utterance duration
        if self._utterance_start is not None:
            elapsed = time.perf_counter() - self._utterance_start
            if elapsed >= self.vad.max_utterance_s:
                return is_speech, True

        return is_speech, False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences for chunked TTS.

    Handles common abbreviations, numbers, and titles that contain
    periods but are not sentence boundaries.  Falls back to the
    full text as a single chunk if no boundaries are found.
    """
    # Protect common abbreviations / numbers from being split
    _ABBREVS = {
        "e.g.": "e\x00g\x00",
        "i.e.": "i\x00e\x00",
        "etc.": "etc\x00",
        "Mr.": "Mr\x00",
        "Mrs.": "Mrs\x00",
        "Dr.": "Dr\x00",
        "No.": "No\x00",
        "Shs.": "Shs\x00",
        "UGX.": "UGX\x00",
        "vs.": "vs\x00",
        "approx.": "approx\x00",
        "dept.": "dept\x00",
    }
    protected = text
    for abbr, placeholder in _ABBREVS.items():
        protected = protected.replace(abbr, placeholder)

    # Protect decimal numbers like "1,000,000.00"
    protected = re.sub(
        r"(\d)\.(\d)",
        lambda match: f"{match.group(1)}\x01{match.group(2)}",
        protected,
    )

    parts = _SENTENCE_RE.split(protected.strip())

    # Restore protected tokens
    result: list[str] = []
    for part in parts:
        part = part.replace("\x00", ".").replace("\x01", ".").strip()
        if not part:
            continue
        # Merge tiny non-sentence fragments, but keep short complete sentences.
        if result and len(part) < 15 and not re.search(r"[.!?]$", part):
            result[-1] = result[-1] + " " + part
        else:
            result.append(part)
    return result if result else [text.strip()]

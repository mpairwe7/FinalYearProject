"""V2 streaming voice engine — dual-path, token-level TTS, vision support (2026).

Extends the Phase 23 :mod:`voice_stream` with:

* **Streaming ASR** — partial hypotheses for speculative prefetch
* **Speculative retrieval** — start RAG search before utterance ends
* **Token-level TTS** — CosyVoice2 flow-matching (first audio in 150-250ms)
* **Dual-path routing** — fast path for greetings/cached, grounded for RAG
* **Parallel vision** — Qwen2-VL document encoding concurrent with ASR

Backward-compatible: V1 clients receive identical event types.  New
events (``vision_result``, ``partial_transcript``) are additive.

Feature flags: ``native_voice``, ``streaming_tts_v2``, ``voice_vision_v2``,
``speculative_prefetch``
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
import uuid
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, AsyncGenerator

from .flags import flags
from .voice_stream import VADConfig, VoiceStreamEvent, _run_blocking, _split_sentences

if TYPE_CHECKING:
    from .service import ChatModel
    from .speech_service import SpeechModel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# VoiceSessionV2
# ---------------------------------------------------------------------------


class VoiceSessionV2:
    """V2 voice session with dual-path routing and streaming TTS.

    Created per WebSocket connection (same lifecycle as V1).

    Key differences from V1 :class:`VoiceSession`:

    1. **Streaming ASR** emits partial hypotheses during the utterance.
    2. **Speculative prefetch** begins retrieval on the stable prefix.
    3. **Query planner** routes between fast (< 400ms) and grounded (< 800ms) paths.
    4. **Token-level TTS** via CosyVoice2 (falls back to sentence-chunked Piper).
    5. **Vision frames** processed in parallel with ASR.
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
        vision_enabled: bool = False,
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
        self.vision_enabled = vision_enabled

        # VAD state (identical to V1)
        self._audio_buffer = bytearray()
        self._is_speaking = False
        self._silence_samples = 0
        self._utterance_start: float | None = None

        # Barge-in
        self._cancelled = asyncio.Event()

        # Metrics
        self._turn_count = 0

        # V2 components (lazy-loaded behind flags)
        self._streaming_asr = None
        self._streaming_tts = None
        self._prefetcher = None
        self._query_planner = None
        self._vision_encoder = None
        self._pending_image: bytes | None = None

        self._init_v2_components()

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------

    def _init_v2_components(self) -> None:
        """Lazy-init V2 components behind feature flags."""
        if flags.is_enabled("native_voice"):
            try:
                from .native_voice.streaming_asr import StreamingASR
                from .native_voice.streaming_tts import StreamingTTS

                self._streaming_asr = StreamingASR(
                    self._speech,
                    sample_rate=self.vad.sample_rate,
                )
                self._streaming_tts = StreamingTTS(speech_fallback=self._speech)
            except Exception:
                logger.warning("V2 streaming components unavailable", exc_info=True)

            if flags.is_enabled("speculative_prefetch"):
                try:
                    from .native_voice.speculative_prefetch import SpeculativePrefetcher

                    retriever = getattr(self._chat_model, "_retriever", None)
                    if retriever is not None:
                        self._prefetcher = SpeculativePrefetcher(
                            retriever, top_k=self.top_k,
                        )
                except Exception:
                    logger.warning("Speculative prefetcher unavailable", exc_info=True)

            try:
                from .native_voice.query_planner import QueryPlanner

                supervisor = getattr(self._chat_model, "_supervisor", None)
                cache = getattr(self._chat_model, "_cache", None)
                if supervisor is not None:
                    self._query_planner = QueryPlanner(supervisor, cache)
            except Exception:
                logger.warning("Query planner unavailable", exc_info=True)

        if self.vision_enabled and flags.is_enabled("voice_vision_v2"):
            try:
                from .vision.encoder import VisionEncoder

                self._vision_encoder = VisionEncoder()
            except Exception:
                logger.warning("Vision encoder unavailable", exc_info=True)

    # ------------------------------------------------------------------
    # Public API: audio handling
    # ------------------------------------------------------------------

    async def handle_audio_chunk(self, pcm_bytes: bytes) -> AsyncGenerator[VoiceStreamEvent, None]:
        """Feed raw PCM16 LE mono audio into the VAD + streaming ASR.

        Yields VAD state events, partial transcripts (V2), and full
        pipeline events when an utterance is complete.
        """
        was_speaking = self._is_speaking
        is_speech, utterance_complete = self._vad_detect(pcm_bytes)

        # VAD state transitions
        if is_speech and not was_speaking:
            yield VoiceStreamEvent(type="vad_state", data={"speaking": True})
        elif not is_speech and was_speaking and not utterance_complete:
            pass  # hysteresis — don't emit yet

        # Feed streaming ASR during speech
        if is_speech and self._streaming_asr is not None:
            partial = await self._streaming_asr.feed_chunk(pcm_bytes)
            if partial is not None:
                yield VoiceStreamEvent(
                    type="partial_transcript",
                    data={
                        "text": partial.text,
                        "stable_prefix": partial.stable_prefix,
                        "language": partial.language,
                        "latency_ms": partial.latency_ms,
                    },
                )
                # Kick off speculative prefetch on stable prefix
                if self._prefetcher and partial.stable_token_count >= 4:
                    await self._prefetcher.maybe_prefetch(partial.stable_prefix)

        # Utterance complete — run full pipeline
        if utterance_complete and len(self._audio_buffer) > 0:
            utterance_audio = bytes(self._audio_buffer)
            self._audio_buffer.clear()
            self._is_speaking = False
            self._silence_samples = 0
            self._utterance_start = None

            yield VoiceStreamEvent(type="vad_state", data={"speaking": False})

            # Minimum duration check
            duration_ms = (len(utterance_audio) / 2) / self.vad.sample_rate * 1000
            if duration_ms < self.vad.min_speech_duration_ms:
                logger.debug(
                    "Utterance too short (%.0fms < %dms), discarding",
                    duration_ms,
                    self.vad.min_speech_duration_ms,
                )
                if self._streaming_asr:
                    self._streaming_asr.finalize()
                return

            self._cancelled.clear()
            self._turn_count += 1

            async for event in self.process_utterance(utterance_audio):
                yield event

            # Reset streaming ASR for next utterance
            if self._streaming_asr:
                self._streaming_asr.finalize()

    async def handle_image_frame(self, image_bytes: bytes) -> None:
        """Buffer an image frame for the next utterance."""
        self._pending_image = image_bytes
        logger.debug("Image frame buffered (%d bytes, session=%s)", len(image_bytes), self.session_id)

    # ------------------------------------------------------------------
    # Core pipeline
    # ------------------------------------------------------------------

    async def process_utterance(self, audio: bytes) -> AsyncGenerator[VoiceStreamEvent, None]:
        """V2 pipeline: parallel ASR+vision → MT → planner → LLM → MT → streaming TTS."""
        timings: dict[str, float] = {}
        audio_hash = hashlib.sha256(audio).hexdigest()[:16]

        # ── Stage 1: Parallel ASR + Vision ─────────────────────────
        asr_coro = self._do_asr(audio, timings)
        vision_coro = None
        if self._pending_image is not None and self._vision_encoder is not None:
            vision_coro = self._do_vision(self._pending_image, timings)
            self._pending_image = None

        if vision_coro is not None:
            asr_result, image_context = await asyncio.gather(
                asr_coro, vision_coro, return_exceptions=True,
            )
            if isinstance(asr_result, BaseException):
                logger.error("ASR failed in parallel: %s", asr_result)
                asr_result = None
            if isinstance(image_context, BaseException):
                logger.warning("Vision failed in parallel: %s", image_context)
                image_context = None
        else:
            asr_result = await asr_coro
            image_context = None

        if asr_result is None:
            yield VoiceStreamEvent(
                type="transcript_final",
                data={"text": "", "language": self.language, "audio_hash": audio_hash,
                      "error": "No speech detected"},
            )
            return

        detected_lang = asr_result["language"]
        yield VoiceStreamEvent(
            type="transcript_final",
            data={
                "text": asr_result["text"],
                "language": detected_lang,
                "latency_s": timings.get("asr_ms", 0) / 1000,
                "backend": asr_result["backend"],
                "audio_hash": audio_hash,
            },
        )

        if image_context:
            yield VoiceStreamEvent(
                type="vision_result",
                data={
                    "ocr_text": image_context.get("ocr_text", ""),
                    "doc_type": image_context.get("doc_type", "unknown"),
                    "summary": image_context.get("summary", ""),
                    "latency_ms": timings.get("vision_ms", 0),
                },
            )

        if self._cancelled.is_set():
            return

        # ── Stage 2: MT (lg → en) ─────────────────────────────────
        query_text = asr_result["text"]
        timings["mt_ms"] = 0.0
        mt_backend = ""

        if detected_lang == "lg":
            t0 = time.perf_counter()
            try:
                mt_result = await _run_blocking(
                    self._speech.translate, asr_result["text"], "lg", "en",
                )
                if mt_result.text and not mt_result.error:
                    query_text = mt_result.text
                    mt_backend = mt_result.backend
            except Exception:
                logger.warning("MT lg→en failed, using original text")
            timings["mt_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        if self._cancelled.is_set():
            return

        # ── Stage 3: Route decision ────────────────────────────────
        prefetch_hits = None
        if self._prefetcher:
            prefetch_hits = await self._prefetcher.resolve(query_text)
            self._prefetcher.reset()

        voice_path = "grounded"
        path_decision = None
        if self._query_planner:
            from .native_voice.query_planner import VoicePath

            path_decision = self._query_planner.plan(
                query_text,
                has_image=image_context is not None,
                has_conversation_history=self._turn_count > 1,
                prefetch_hits=prefetch_hits,
                locale=detected_lang,
            )
            voice_path = path_decision.path.value

        # ── Stage 4: LLM Generation ───────────────────────────────
        t0 = time.perf_counter()

        # Inject vision context if present
        message_for_llm = query_text
        if image_context:
            doc_type = image_context.get("doc_type", "document")
            ocr_text = image_context.get("ocr_text", "")
            summary = image_context.get("summary", "")
            message_for_llm = (
                f"{query_text}\n\n"
                f"[Scanned {doc_type}]\n"
                f"OCR text: {ocr_text}\n"
                f"Analysis: {summary}"
            )

        try:
            llm_result = await _run_blocking(
                partial(
                    self._chat_model.generate,
                    message=message_for_llm,
                    conversation_id=self.conversation_id,
                    top_k=self.top_k,
                    locale="en",
                    user_id=self.user_id or None,
                    tenant_id=self.tenant_id,
                ),
            )
            reply_text = llm_result.get("reply", "")
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

        # ── Stage 5: MT (en → lg) ─────────────────────────────────
        reply_for_tts = reply_text
        if detected_lang == "lg":
            t0 = time.perf_counter()
            try:
                mt_back = await _run_blocking(
                    self._speech.translate, reply_text, "en", "lg",
                )
                if mt_back.text and not mt_back.error:
                    reply_for_tts = mt_back.text
                    mt_backend = mt_back.backend
            except Exception:
                logger.warning("MT en→lg failed, using English reply")
            timings["mt_ms"] += round((time.perf_counter() - t0) * 1000, 1)

        if self._cancelled.is_set():
            return

        # ── Stage 6: TTS (streaming V2 or sentence-chunked V1) ────
        tts_first_chunk_ms: float | None = None
        tts_t0 = time.perf_counter()

        if self.tts_enabled and reply_for_tts.strip():
            use_streaming_tts = (
                self._streaming_tts is not None
                and flags.is_enabled("streaming_tts_v2")
            )

            if use_streaming_tts:
                async for event in self._tts_streaming_v2(reply_for_tts, detected_lang):
                    if tts_first_chunk_ms is None and event.type == "audio_chunk":
                        tts_first_chunk_ms = round((time.perf_counter() - tts_t0) * 1000, 1)
                    yield event
            else:
                async for event in self._tts_sentence_chunked_v1(reply_for_tts, detected_lang):
                    if tts_first_chunk_ms is None and event.type == "audio_chunk":
                        tts_first_chunk_ms = round((time.perf_counter() - tts_t0) * 1000, 1)
                    yield event
        else:
            yield VoiceStreamEvent(
                type="reply_text", data={"text": reply_for_tts, "chunk_index": 0},
            )

        timings["tts_first_chunk_ms"] = tts_first_chunk_ms or 0.0
        timings["total_ms"] = round(
            timings.get("asr_ms", 0) + timings.get("mt_ms", 0)
            + timings.get("llm_ms", 0) + (tts_first_chunk_ms or 0.0),
            1,
        )

        # ── Metadata + latency report ─────────────────────────────
        yield VoiceStreamEvent(
            type="reply_meta",
            data={
                "sources": llm_result.get("sources", []),
                "citations": llm_result.get("citations", []),
                "faithfulness_score": llm_result.get("faithfulness_score"),
                "retrieval_mode": llm_result.get("retrieval_mode", "keyword"),
                "conversation_id": self.conversation_id,
                "voice_path": voice_path,
                "vision_doc_type": image_context.get("doc_type") if image_context else None,
            },
        )

        yield VoiceStreamEvent(
            type="latency_report",
            data={
                "asr_ms": timings.get("asr_ms", 0),
                "mt_ms": timings.get("mt_ms", 0),
                "llm_ms": timings.get("llm_ms", 0),
                "tts_first_chunk_ms": timings.get("tts_first_chunk_ms", 0),
                "total_ms": timings.get("total_ms", 0),
                "vision_ms": timings.get("vision_ms", 0),
                "voice_path": voice_path,
                "speculative_prefetch_used": prefetch_hits is not None,
                "asr_backend": asr_result.get("backend", ""),
                "mt_backend": mt_backend,
            },
        )

    # ------------------------------------------------------------------
    # TTS backends
    # ------------------------------------------------------------------

    async def _tts_streaming_v2(
        self, text: str, language: str,
    ) -> AsyncGenerator[VoiceStreamEvent, None]:
        """V2 token-level streaming TTS via CosyVoice2."""
        audio_started = False
        chunk_idx = 0

        async for tts_chunk in self._streaming_tts.synthesize_text(
            text, language=language, voice=self.voice,
        ):
            if self._cancelled.is_set():
                break
            if tts_chunk.is_last:
                yield VoiceStreamEvent(type="audio_end", data={})
                return
            if tts_chunk.audio:
                if not audio_started:
                    yield VoiceStreamEvent(
                        type="audio_start",
                        data={"sample_rate": tts_chunk.sample_rate},
                    )
                    audio_started = True
                if tts_chunk.text_span:
                    yield VoiceStreamEvent(
                        type="reply_text",
                        data={"text": tts_chunk.text_span, "chunk_index": chunk_idx},
                    )
                yield VoiceStreamEvent(
                    type="audio_chunk",
                    data={
                        "audio": tts_chunk.audio,
                        "sample_rate": tts_chunk.sample_rate,
                        "chunk_index": chunk_idx,
                    },
                )
                chunk_idx += 1

        if not audio_started:
            yield VoiceStreamEvent(type="audio_end", data={})

    async def _tts_sentence_chunked_v1(
        self, text: str, language: str,
    ) -> AsyncGenerator[VoiceStreamEvent, None]:
        """V1 fallback: sentence-chunked TTS (Piper/edge-tts/Sunbird)."""
        sentences = _split_sentences(text)
        audio_started = False

        for idx, sentence in enumerate(sentences):
            if self._cancelled.is_set():
                break
            yield VoiceStreamEvent(
                type="reply_text", data={"text": sentence, "chunk_index": idx},
            )
            try:
                tts_result = await _run_blocking(
                    self._speech.synthesize, sentence, self.voice, language,
                )
            except Exception:
                logger.warning("V1 TTS failed for chunk %d", idx, exc_info=True)
                continue
            if tts_result.audio and not tts_result.error:
                if not audio_started:
                    yield VoiceStreamEvent(
                        type="audio_start",
                        data={"sample_rate": tts_result.sample_rate},
                    )
                    audio_started = True
                yield VoiceStreamEvent(
                    type="audio_chunk",
                    data={
                        "audio": tts_result.audio,
                        "sample_rate": tts_result.sample_rate,
                        "chunk_index": idx,
                    },
                )

        yield VoiceStreamEvent(type="audio_end", data={})

    # ------------------------------------------------------------------
    # ASR + Vision helpers
    # ------------------------------------------------------------------

    async def _do_asr(self, audio: bytes, timings: dict) -> dict | None:
        """Run batch ASR on the complete utterance (same as V1)."""
        t0 = time.perf_counter()
        try:
            result = await _run_blocking(
                self._speech.transcribe,
                audio,
                self.vad.sample_rate,
                self.language if self.language != "auto" else None,
            )
        except Exception as exc:
            logger.error("ASR failed: %s", exc)
            return None
        timings["asr_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        if result.error or not result.text.strip():
            return None

        return {
            "text": result.text,
            "language": result.language or self.language,
            "backend": result.backend,
        }

    async def _do_vision(self, image_bytes: bytes, timings: dict) -> dict | None:
        """Run vision encoding in the executor."""
        t0 = time.perf_counter()
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None, self._vision_encoder.encode, image_bytes,
            )
            timings["vision_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            return result
        except Exception:
            logger.warning("Vision encoding failed", exc_info=True)
            timings["vision_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            return None

    # ------------------------------------------------------------------
    # VAD (identical algorithm to V1 — reused to avoid circular import)
    # ------------------------------------------------------------------

    def _vad_detect(self, pcm_chunk: bytes) -> tuple[bool, bool]:
        """Energy-based VAD with hysteresis (same as V1)."""
        import numpy as np

        n_samples = len(pcm_chunk) // 2
        if n_samples == 0:
            return False, False

        samples = np.frombuffer(pcm_chunk, dtype=np.int16).astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(samples**2)))
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
                self._audio_buffer.extend(pcm_chunk)

                silence_ms = (self._silence_samples / self.vad.sample_rate) * 1000
                if silence_ms >= self.vad.silence_duration_ms:
                    return False, True

        if self._utterance_start is not None:
            elapsed = time.perf_counter() - self._utterance_start
            if elapsed >= self.vad.max_utterance_s:
                return is_speech, True

        return is_speech, False

    # ------------------------------------------------------------------
    # Barge-in + lifecycle
    # ------------------------------------------------------------------

    async def barge_in(self) -> None:
        """Interrupt current TTS playback and reset prefetcher."""
        self._cancelled.set()
        self._audio_buffer.clear()
        self._is_speaking = False
        self._silence_samples = 0
        if self._prefetcher:
            self._prefetcher.reset()
        if self._streaming_asr:
            self._streaming_asr.finalize()
        logger.info("V2 barge-in (session=%s, turn=%d)", self.session_id, self._turn_count)

    def close(self) -> None:
        """Cleanup session resources."""
        self._audio_buffer.clear()
        self._cancelled.set()
        if self._prefetcher:
            self._prefetcher.reset()
        if self._streaming_asr:
            self._streaming_asr.finalize()
        logger.info(
            "V2 session closed (session=%s, turns=%d)",
            self.session_id,
            self._turn_count,
        )

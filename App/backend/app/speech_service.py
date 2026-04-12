"""Speech service layer — production ASR + MT + TTS (2026).

Parallel singleton to :class:`ChatModel` (see ``service.py``). Loads speech
models lazily so the backend still boots when speech assets are missing —
this is critical for the dev / CI path where only the text pipeline is
exercised.

Architecture::

    /asr      audio  -> SpeechModel.transcribe  -> {text, language, rtf}
    /tts      text   -> SpeechModel.synthesize  -> audio/wav
    /translate text  -> SpeechModel.translate   -> {text, source_lang, target_lang}

The model singleton is created in ``main.py`` lifespan and stashed on
``app.state.speech``. Each method wraps the underlying inference in a
bounded executor + circuit breaker (same pattern as ``service._LLM_EXECUTOR``)
so a slow / failing speech model cannot exhaust API workers.

Environment flags:

* ``SPEECH_ENABLED``        — set to ``false`` to skip speech model init.
* ``SPEECH_ASR_BACKEND``    — ``auto|sherpa|transformers|mock``.
* ``SPEECH_TTS_BACKEND``    — ``auto|sherpa|piper|mock``.
* ``SPEECH_MT_BACKEND``     — ``auto|onnx|transformers|prompted|mock``.
* ``SPEECH_DEADLINE_S``     — hard wall-clock budget for one inference.

Commercial posture: all default models are MIT / Apache-2.0. No CC-BY-NC
paths are enabled by default.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .resilience import CircuitBreaker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


SPEECH_ENABLED = os.getenv("SPEECH_ENABLED", "true").lower() == "true"
SPEECH_ASR_BACKEND = os.getenv("SPEECH_ASR_BACKEND", "auto")
SPEECH_TTS_BACKEND = os.getenv("SPEECH_TTS_BACKEND", "auto")
SPEECH_MT_BACKEND = os.getenv("SPEECH_MT_BACKEND", "auto")
SPEECH_DEADLINE_S = float(os.getenv("SPEECH_DEADLINE_S", "20"))
SPEECH_MAX_CONCURRENCY = int(os.getenv("SPEECH_MAX_CONCURRENCY", "2"))

DEFAULT_EN_VOICE = os.getenv("SPEECH_EN_VOICE", "en_US-lessac-medium")
DEFAULT_LG_VOICE = os.getenv("SPEECH_LG_VOICE", "luganda-vits-v1")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SPEECH_ASR_SHERPA_DIR = Path(
    os.getenv(
        "SPEECH_ASR_SHERPA_DIR",
        str(PROJECT_ROOT / "artifacts" / "speech" / "asr" / "sherpa" / "whisper-small"),
    )
)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TranscribeResult:
    text: str
    language: Optional[str] = None
    duration_s: Optional[float] = None
    latency_s: Optional[float] = None
    rtf: Optional[float] = None
    backend: str = "unknown"
    error: Optional[str] = None


@dataclass
class SynthesizeResult:
    audio: bytes
    sample_rate: int
    num_samples: int
    duration_s: float
    latency_s: float
    backend: str
    voice: str
    error: Optional[str] = None


@dataclass
class TranslateResult:
    text: str
    source_lang: str
    target_lang: str
    latency_s: float
    backend: str
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class SpeechModel:
    """Lazy singleton for ASR + TTS + MT.

    All heavy imports are deferred so importing this module has zero
    runtime cost when SPEECH_ENABLED=false.
    """

    def __init__(self) -> None:
        self.enabled = SPEECH_ENABLED
        self._lock = threading.Lock()
        self._closed = False
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=SPEECH_MAX_CONCURRENCY,
            thread_name_prefix="speech",
        )
        self._breakers = {
            "asr": CircuitBreaker(
                name="speech.asr",
                failure_threshold=3,
                reset_timeout=15.0,
                max_timeout=120.0,
            ),
            "tts": CircuitBreaker(
                name="speech.tts",
                failure_threshold=3,
                reset_timeout=15.0,
                max_timeout=120.0,
            ),
            "mt": CircuitBreaker(
                name="speech.mt",
                failure_threshold=3,
                reset_timeout=15.0,
                max_timeout=120.0,
            ),
        }
        self._asr = None
        self._mt = None
        self._lang_det = None
        self._initialised = False
        if self.enabled:
            with self._lock:
                self._init_models()

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------

    def _init_models(self) -> None:
        try:
            from ml.scripts.asr.infer_asr import AsrTranscriber  # type: ignore

            self._asr = AsrTranscriber(
                sherpa_dir=SPEECH_ASR_SHERPA_DIR,
                backend=SPEECH_ASR_BACKEND,
            )
            logger.info("SpeechModel: ASR transcriber ready (backend=%s)", SPEECH_ASR_BACKEND)
        except Exception:
            logger.exception("SpeechModel: ASR init failed — transcription disabled")

        try:
            from ml.scripts.lang_id import LanguageDetector  # type: ignore

            self._lang_det = LanguageDetector()
            logger.info("SpeechModel: language detector ready")
        except Exception:
            logger.exception("SpeechModel: lang-ID init failed — fallback to 'en'")

        try:
            from ml.scripts.mt.infer_mt import MtTranslator  # type: ignore

            self._mt = MtTranslator(backend=SPEECH_MT_BACKEND)
            logger.info("SpeechModel: MT translator ready (backend=%s)", SPEECH_MT_BACKEND)
        except Exception:
            logger.exception("SpeechModel: MT init failed — translation disabled")

        self._initialised = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_ready(self) -> bool:
        return self.enabled and self._initialised

    def transcribe(
        self, audio_bytes: bytes, sample_rate: int = 16000, language: Optional[str] = None
    ) -> TranscribeResult:
        """Transcribe raw PCM bytes (int16 or float32)."""
        if not self.enabled:
            return TranscribeResult(text="", backend="disabled", error="SPEECH_ENABLED=false")
        if self._asr is None:
            return TranscribeResult(text="", backend="unavailable", error="ASR not initialised")
        if not self._breakers["asr"].allow_request():
            return TranscribeResult(text="", backend="circuit_open", error="ASR circuit open")

        future = self._executor.submit(self._do_transcribe, audio_bytes, sample_rate)
        try:
            result = future.result(timeout=SPEECH_DEADLINE_S)
            self._breakers["asr"].record_success()
            if language is None and self._lang_det is not None and result.text:
                try:
                    det = self._lang_det.detect(result.text)
                    result.language = det.lang
                except Exception:
                    pass
            return result
        except concurrent.futures.TimeoutError:
            future.cancel()
            self._breakers["asr"].record_failure()
            return TranscribeResult(text="", backend="timeout", error=f"deadline {SPEECH_DEADLINE_S}s exceeded")
        except Exception as exc:
            self._breakers["asr"].record_failure()
            logger.exception("ASR error")
            return TranscribeResult(text="", backend="error", error=str(exc))

    def synthesize(
        self,
        text: str,
        voice: Optional[str] = None,
        language: str = "en",
    ) -> SynthesizeResult:
        """Synthesize text to WAV bytes."""
        if not self.enabled:
            return SynthesizeResult(
                audio=b"",
                sample_rate=0,
                num_samples=0,
                duration_s=0.0,
                latency_s=0.0,
                backend="disabled",
                voice="",
                error="SPEECH_ENABLED=false",
            )
        voice = voice or (DEFAULT_LG_VOICE if language == "lg" else DEFAULT_EN_VOICE)
        if not self._breakers["tts"].allow_request():
            return SynthesizeResult(
                audio=b"",
                sample_rate=0,
                num_samples=0,
                duration_s=0.0,
                latency_s=0.0,
                backend="circuit_open",
                voice=voice,
                error="TTS circuit open",
            )
        future = self._executor.submit(self._do_synthesize, text, voice)
        try:
            result = future.result(timeout=SPEECH_DEADLINE_S)
            self._breakers["tts"].record_success()
            return result
        except concurrent.futures.TimeoutError:
            future.cancel()
            self._breakers["tts"].record_failure()
            return SynthesizeResult(
                audio=b"",
                sample_rate=0,
                num_samples=0,
                duration_s=0.0,
                latency_s=0.0,
                backend="timeout",
                voice=voice,
                error=f"deadline {SPEECH_DEADLINE_S}s exceeded",
            )
        except Exception as exc:
            self._breakers["tts"].record_failure()
            logger.exception("TTS error")
            return SynthesizeResult(
                audio=b"",
                sample_rate=0,
                num_samples=0,
                duration_s=0.0,
                latency_s=0.0,
                backend="error",
                voice=voice,
                error=str(exc),
            )

    def translate(
        self,
        text: str,
        source_lang: str = "en",
        target_lang: str = "lg",
    ) -> TranslateResult:
        if not self.enabled:
            return TranslateResult(
                text="",
                source_lang=source_lang,
                target_lang=target_lang,
                latency_s=0.0,
                backend="disabled",
                error="SPEECH_ENABLED=false",
            )
        if self._mt is None:
            return TranslateResult(
                text=text,
                source_lang=source_lang,
                target_lang=target_lang,
                latency_s=0.0,
                backend="unavailable",
                error="MT not initialised — passing through",
            )
        if not self._breakers["mt"].allow_request():
            return TranslateResult(
                text="",
                source_lang=source_lang,
                target_lang=target_lang,
                latency_s=0.0,
                backend="circuit_open",
                error="MT circuit open",
            )
        future = self._executor.submit(
            self._do_translate, text, source_lang, target_lang
        )
        try:
            result = future.result(timeout=SPEECH_DEADLINE_S)
            self._breakers["mt"].record_success()
            return result
        except concurrent.futures.TimeoutError:
            future.cancel()
            self._breakers["mt"].record_failure()
            return TranslateResult(
                text="",
                source_lang=source_lang,
                target_lang=target_lang,
                latency_s=0.0,
                backend="timeout",
                error=f"deadline {SPEECH_DEADLINE_S}s exceeded",
            )
        except Exception as exc:
            self._breakers["mt"].record_failure()
            logger.exception("MT error")
            return TranslateResult(
                text="",
                source_lang=source_lang,
                target_lang=target_lang,
                latency_s=0.0,
                backend="error",
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Internal helpers (run inside the executor)
    # ------------------------------------------------------------------

    def _pcm_bytes_to_float(self, audio_bytes: bytes):
        """Decode raw PCM bytes into a float32 numpy array in [-1, 1]."""
        import numpy as np

        # Assume 16-bit little-endian PCM unless bytes count suggests float32.
        if len(audio_bytes) % 4 == 0:
            # Try float32; fall back to int16 if values look wildly scaled.
            try:
                arr = np.frombuffer(audio_bytes, dtype=np.float32)
                if arr.size and -1.5 <= float(arr.max()) <= 1.5:
                    return arr.copy()
            except Exception:
                pass
        arr = np.frombuffer(audio_bytes, dtype=np.int16).astype("float32") / 32768.0
        return arr

    def _do_transcribe(self, audio_bytes: bytes, sample_rate: int) -> TranscribeResult:
        samples = self._pcm_bytes_to_float(audio_bytes)
        t0 = time.perf_counter()
        result = self._asr.transcribe_array(samples, sample_rate=sample_rate)  # type: ignore[union-attr]
        return TranscribeResult(
            text=result.text,
            language=result.language,
            duration_s=result.duration_s,
            latency_s=round(time.perf_counter() - t0, 3),
            rtf=result.rtf,
            backend=result.backend,
        )

    def _do_synthesize(self, text: str, voice: str) -> SynthesizeResult:
        from ml.scripts.tts.infer_tts import TtsSynthesizer  # type: ignore

        synth = TtsSynthesizer(voice_id=voice, backend=SPEECH_TTS_BACKEND)
        t0 = time.perf_counter()
        result = synth.synthesize(text)
        latency = time.perf_counter() - t0

        # Convert the samples (which the wrapper produces as float32) to WAV bytes.
        import io
        import wave

        import numpy as np

        # NOTE: the wrapper returned the metrics but not the samples. Re-run the
        # synth via the streaming path to grab bytes. This keeps the TtsResult
        # API narrow while still giving us audio for /tts.
        from ml.scripts.tts.infer_tts import (  # type: ignore
            _synth_mock,
            _synth_piper,
            _synth_sherpa,
        )

        samples = None
        sample_rate = 22050
        voice_dir = PROJECT_ROOT / "artifacts" / "speech" / "tts" / "sherpa" / voice
        try:
            r = _synth_sherpa(voice_dir, text, voice)
            if r is not None:
                samples, sample_rate, _, _ = r
        except Exception:
            logger.warning("Sherpa TTS failed for voice=%s, falling back", voice, exc_info=True)
            samples = None
        if samples is None:
            piper_dir = PROJECT_ROOT / "artifacts" / "speech" / "tts" / voice
            try:
                r = _synth_piper(piper_dir, text)
                if r is not None:
                    samples, sample_rate, _, _ = r
            except Exception:
                logger.warning("Piper TTS failed, falling back to mock", exc_info=True)
                samples = None
        if samples is None:
            try:
                r = _synth_mock(text, voice)
                samples, sample_rate, _, _ = r
            except Exception:
                logger.error("All TTS backends failed for voice=%s", voice, exc_info=True)
                return SynthesizeResult(
                    audio=b"",
                    sample_rate=0,
                    num_samples=0,
                    duration_s=0.0,
                    latency_s=round(latency, 3),
                    backend="error",
                    voice=voice,
                    error="All TTS backends failed",
                )

        float_samples = np.asarray(samples, dtype="float32")
        pcm16 = (float_samples * 32768).clip(-32768, 32767).astype("int16")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(pcm16.tobytes())
        return SynthesizeResult(
            audio=buf.getvalue(),
            sample_rate=int(sample_rate),
            num_samples=int(len(pcm16)),
            duration_s=round(len(pcm16) / max(sample_rate, 1), 3),
            latency_s=round(latency, 3),
            backend=result.backend,
            voice=voice,
        )

    def _do_translate(self, text: str, source_lang: str, target_lang: str) -> TranslateResult:
        t0 = time.perf_counter()
        result = self._mt.translate(  # type: ignore[union-attr]
            text, source_lang=source_lang, target_lang=target_lang
        )
        return TranslateResult(
            text=result.text,
            source_lang=result.source_lang,
            target_lang=result.target_lang,
            latency_s=round(time.perf_counter() - t0, 3),
            backend=result.backend,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)

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
* ``WHISPER_DEVICE``        — device for Whisper LoRA adapters (default: cpu).

Commercial posture: all default models are MIT / Apache-2.0. No CC-BY-NC
paths are enabled by default.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import logging
import os
import sys
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from pathlib import Path

# Ensure the project root is on sys.path so `ml.scripts.*` imports resolve.
from ._root import PROJECT_ROOT as _PROJECT_ROOT_P

_PROJECT_ROOT = str(_PROJECT_ROOT_P)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from .resilience import CircuitBreaker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


SPEECH_ENABLED = os.getenv("SPEECH_ENABLED", "true").lower() == "true"
SPEECH_ASR_BACKEND = os.getenv("SPEECH_ASR_BACKEND", "auto")
SPEECH_TTS_BACKEND = os.getenv("SPEECH_TTS_BACKEND", "auto")
SPEECH_MT_BACKEND = os.getenv("SPEECH_MT_BACKEND", "prompted")
SPEECH_DEADLINE_S = float(os.getenv("SPEECH_DEADLINE_S", "60"))
# LRU cache for repeated short phrases (greetings, empathy openers, workflow
# prompts). 0 disables. Keyed by (text, voice, language).
TTS_CACHE_SIZE = int(os.getenv("SPEECH_TTS_CACHE_SIZE", "64"))

# Hard ceiling for ONE cloud speech-tier call (Sunbird / edge-tts / Workers
# AI). Sunbird latency swings 25s→90s+; a hung upstream must fail the TIER
# (fall through to the next backend or a degraded JSON reply) — never the
# whole request via a deployment-gateway 504.
SPEECH_CLOUD_DEADLINE_S = float(os.getenv("SPEECH_CLOUD_DEADLINE_S", "40"))

# Dedicated pool so bounded cloud calls can never deadlock the main speech
# executor (transcribe_async already runs ON that executor).
_CLOUD_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=int(os.getenv("SPEECH_CLOUD_MAX_CONCURRENCY", "4")),
    thread_name_prefix="speech-cloud",
)


def _cloud_call(label: str, func, *args, **kwargs):
    """Run one cloud speech-tier call under :data:`SPEECH_CLOUD_DEADLINE_S`.

    Raises ``TimeoutError`` on breach. The abandoned worker thread finishes
    in the background (network I/O is uncancellable) but the caller moves
    on to the next tier immediately.
    """
    future = _CLOUD_EXECUTOR.submit(func, *args, **kwargs)
    try:
        return future.result(timeout=SPEECH_CLOUD_DEADLINE_S)
    except concurrent.futures.TimeoutError as exc:
        future.cancel()
        logger.warning("%s exceeded the %.0fs cloud deadline", label, SPEECH_CLOUD_DEADLINE_S)
        raise TimeoutError(f"{label} exceeded {SPEECH_CLOUD_DEADLINE_S:.0f}s") from exc

# Magic bytes for the audio containers our TTS tiers legitimately produce.
_AUDIO_MAGIC = (b"RIFF", b"ID3", b"OggS", b"fLaC")


def _looks_like_audio(data: bytes) -> bool:
    """Cheap container sniff so an HTML error page never reaches a client
    as 'audio' (RIFF/WAV, MP3, Ogg, FLAC, or a bare MPEG frame)."""
    if len(data) < 44:
        return False
    if data.startswith(_AUDIO_MAGIC):
        return True
    return data[0] == 0xFF and (data[1] & 0xE0) == 0xE0  # raw MPEG frame sync


# MPEG audio frame tables, for reading a duration off an MP3 without decoding
# it. Index by (version, layer) as encoded in the frame header.
_MPEG_BITRATES_V1_L3 = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0)
_MPEG_BITRATES_V2_L3 = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0)
_MPEG_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def _mp3_metadata(data: bytes) -> tuple[int, float] | None:
    """(sample_rate, duration_s) from an MP3's first frame header, or None.

    Duration assumes a constant bitrate, which is what edge-tts emits. A VBR
    file would come out approximate — still far closer than the byte-length
    guess this replaces, which assumed the bytes were raw PCM.
    """
    i = 0
    if data[:3] == b"ID3":  # skip the ID3v2 tag: 10-byte header + syncsafe size
        if len(data) < 10:
            return None
        size = 0
        for b in data[6:10]:
            size = (size << 7) | (b & 0x7F)
        i = 10 + size
    # Scan a bounded window for the frame sync rather than trusting alignment.
    limit = min(len(data) - 4, i + 8192)
    while i < limit:
        if data[i] == 0xFF and (data[i + 1] & 0xE0) == 0xE0:
            h1, h2 = data[i + 1], data[i + 2]
            version = (h1 >> 3) & 0x03  # 3=MPEG1, 2=MPEG2, 0=MPEG2.5
            layer = (h1 >> 1) & 0x03    # 1=Layer III
            br_idx = (h2 >> 4) & 0x0F
            sr_idx = (h2 >> 2) & 0x03
            if version != 1 and layer == 1 and sr_idx != 3 and br_idx not in (0, 15):
                table = _MPEG_BITRATES_V1_L3 if version == 3 else _MPEG_BITRATES_V2_L3
                bitrate = table[br_idx] * 1000
                rate = _MPEG_RATES[version][sr_idx]
                if bitrate:
                    return rate, round((len(data) - i) * 8 / bitrate, 3)
                return rate, 0.0
        i += 1
    return None


def _audio_metadata(data: bytes, default_rate: int) -> tuple[int, int, float]:
    """(sample_rate, num_samples, duration_s) read from *data*'s own header.

    Every cloud TTS tier used to report these as hardcoded constants and zeros,
    so `/v1/tts` advertised a sample rate the audio did not have and a duration
    of 0 for real speech. Reading the container is cheap — a WAV header parse or
    one MPEG frame header — and is the only way these fields can be true for a
    backend that hands us encoded bytes rather than samples.

    Falls back to (default_rate, 0, 0.0) for anything unrecognised, so an
    unknown container degrades to the old behaviour instead of raising.
    """
    if not data or len(data) < 4:
        return default_rate, 0, 0.0
    try:
        if data[:4] == b"RIFF":
            import io as _io
            import wave as _wave

            with _wave.open(_io.BytesIO(data), "rb") as w:
                rate = w.getframerate()
                frames = w.getnframes()
            return rate, frames, round(frames / rate, 3) if rate else 0.0
        if data[:3] == b"ID3" or (data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
            mp3 = _mp3_metadata(data)
            if mp3:
                rate, duration = mp3
                return rate, int(duration * rate), duration
    except Exception:  # noqa: BLE001 — metadata is advisory; never fail a response
        logger.debug("Could not read audio metadata from %d bytes", len(data), exc_info=True)
    return default_rate, 0, 0.0


def _input_duration_s(data: bytes, sample_rate: int) -> float:
    """Wall-clock length of posted audio, for ASR results that omit it.

    Prefers the container's own header; falls back to treating the bytes as
    raw PCM16 at *sample_rate*, which is what `/v1/asr` documents its body to
    be. Returns 0.0 rather than raising when the input makes no sense.
    """
    _, _, duration = _audio_metadata(data, sample_rate)
    if duration:
        return duration
    if not data or sample_rate <= 0:
        return 0.0
    return round(len(data) / (sample_rate * 2), 3)  # int16 mono


SPEECH_MAX_CONCURRENCY = int(os.getenv("SPEECH_MAX_CONCURRENCY", "2"))

DEFAULT_EN_VOICE = os.getenv("SPEECH_EN_VOICE", "en_US-lessac-medium")
DEFAULT_LG_VOICE = os.getenv("SPEECH_LG_VOICE", "luganda-vits-v1")

# edge-tts neural voices — used when the local Piper stack is absent (e.g. the
# slim Crane Cloud image). Override per deployment via env. en-US-AriaNeural is
# a natural English neural voice; Sunbird has no native English voice, so this
# is what keeps English TTS off the poor Sunbird-English fallback.
SPEECH_EN_EDGE_VOICE = os.getenv("SPEECH_EN_EDGE_VOICE", "en-US-AriaNeural")
# edge-tts has no Luganda voice, so this is an English voice reading Luganda
# text — a stopgap for when Sunbird's native Luganda voice is unreachable.
# It was en-UG-MaleNeural, which is not a real edge-tts voice: the service has
# no Uganda locale at all, so every call raised "No audio was received" and
# Luganda lost its only fallback. en-KE is the nearest available East African
# English (Kenya borders Uganda) and Chilemba is male, matching the intent of
# the previous value.
SPEECH_LG_EDGE_VOICE = os.getenv("SPEECH_LG_EDGE_VOICE", "en-KE-ChilembaNeural")

# Locales that ship a genuine local (Piper/Sherpa) voice model. Anything outside
# this set can only be synthesized locally by an *English* model reading foreign
# text — which the chain below must treat as "no voice", not as success.
LOCAL_TTS_VOICES: dict[str, str] = {
    "en": DEFAULT_EN_VOICE,
    "lg": DEFAULT_LG_VOICE,
}

# English speakers a caller may choose. edge-tts takes a voice name directly, so
# unlike the Sunbird catalog these need no lookup — but they must be REAL edge
# voice names. en-UG-MaleNeural was not (edge-tts has no Uganda locale at all)
# and every call raised "No audio was received", which is why this list is
# limited to voices already serving in production rather than plausible-looking
# additions. Verify with `edge-tts --list-voices` before extending it.
EN_EDGE_VOICE_CHOICES: tuple[str, ...] = (
    "en-US-AriaNeural",
    "en-US-GuyNeural",
    "en-GB-SoniaNeural",
)


def _sunbird_has_native_voice(locale: str) -> bool:
    """True when Sunbird ships a native speaker for *locale*."""
    try:
        from . import sunbird

        return bool(sunbird.TTS_VOICES.get(locale))
    except Exception:  # noqa: BLE001 — sunbird optional; absence means no voice
        return False

# Cloudflare Workers AI models for ENGLISH STT/TTS — configurable so the model
# can be swapped (e.g. to Deepgram nova-3/flux/aura-2) via env without code
# changes. Defaults are the free-tier-friendly combo; TTS_FALLBACK_MODEL_2 is a
# resilience fallback tried if the primary TTS model fails. Gated by
# FLAG_CLOUDFLARE_FALLBACK + STT_FALLBACK_BACKEND/TTS_FALLBACK_BACKEND=workers_ai.
STT_FALLBACK_MODEL = os.getenv("STT_FALLBACK_MODEL", "@cf/openai/whisper-large-v3-turbo")
TTS_FALLBACK_MODEL = os.getenv("TTS_FALLBACK_MODEL", "@cf/myshell-ai/melotts")
TTS_FALLBACK_MODEL_2 = os.getenv("TTS_FALLBACK_MODEL_2", "@cf/deepgram/aura-2-en")

# Whisper LoRA adapters — per-language fine-tuned for multilingual ASR.
# Set WHISPER_ADAPTER_PATH for single-language (backward-compat), or
# set WHISPER_ADAPTER_{LG,SW,NYN,ACH} for per-language routing.
# Every locale the UI can select needs a slot here; a missing key means the
# locale silently gets the un-adapted base model instead of its fine-tune.
WHISPER_ADAPTER_PATH = os.getenv("WHISPER_ADAPTER_PATH", "") or None
WHISPER_ADAPTERS: dict[str, str | None] = {
    "lg": os.getenv("WHISPER_ADAPTER_LG", "") or None,
    "sw": os.getenv("WHISPER_ADAPTER_SW", "") or None,
    "nyn": os.getenv("WHISPER_ADAPTER_NYN", "") or None,
    "ach": os.getenv("WHISPER_ADAPTER_ACH", "") or None,
    # Phase 23 — accent-specific adapters (populated by train_accent_asr.py)
    "en_ug_central": os.getenv("WHISPER_ADAPTER_EN_UG_CENTRAL", "") or None,
    "en_ug_eastern": os.getenv("WHISPER_ADAPTER_EN_UG_EASTERN", "") or None,
    "en_ug_western": os.getenv("WHISPER_ADAPTER_EN_UG_WESTERN", "") or None,
    "code_switch_en_lg": os.getenv("WHISPER_ADAPTER_CODE_SWITCH", "") or None,
}
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu").strip().lower() or "cpu"

PROJECT_ROOT = _PROJECT_ROOT_P
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
    language: str | None = None
    duration_s: float | None = None
    latency_s: float | None = None
    rtf: float | None = None
    backend: str = "unknown"
    error: str | None = None


@dataclass
class SynthesizeResult:
    audio: bytes
    sample_rate: int
    num_samples: int
    duration_s: float
    latency_s: float
    backend: str
    voice: str
    error: str | None = None


@dataclass
class TranslateResult:
    text: str
    source_lang: str
    target_lang: str
    latency_s: float
    backend: str
    error: str | None = None


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
        self._tts_cache: OrderedDict[tuple[str, str, str], SynthesizeResult] = OrderedDict()
        self._tts_cache_lock = threading.Lock()
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
        self._whisper_peft = None  # (model, processor) — legacy single adapter
        self._whisper_adapters: dict[str, tuple] = {}  # lang -> (model, processor)
        self._mt = None
        self._lang_det = None
        self._chat_model = None  # set externally for prompted MT
        self._faster_whisper = None  # cached faster-whisper model
        self._accent_detector = None  # accent detection for adapter routing
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

        # Whisper + LoRA adapters for multilingual ASR
        # Collect all configured adapter paths
        whisper_paths: dict[str, str] = {}
        for lang, path in WHISPER_ADAPTERS.items():
            if path and os.path.isdir(path):
                whisper_paths[lang] = path
        # Legacy single-path fallback
        if not whisper_paths and WHISPER_ADAPTER_PATH and os.path.isdir(WHISPER_ADAPTER_PATH):
            whisper_paths["lg"] = WHISPER_ADAPTER_PATH

        if whisper_paths:
            try:
                import torch
                from transformers import WhisperForConditionalGeneration, WhisperProcessor
                from peft import PeftModel

                for lang, adapter_path in whisper_paths.items():
                    try:
                        whisper_dtype = (
                            torch.float16 if WHISPER_DEVICE.startswith("cuda") else torch.float32
                        )
                        whisper_kwargs = {"torch_dtype": whisper_dtype}
                        if WHISPER_DEVICE == "auto":
                            whisper_kwargs = {
                                "torch_dtype": torch.float16,
                                "device_map": "auto",
                            }

                        logger.info(
                            "Loading Whisper+LoRA adapter '%s' from %s (device=%s)",
                            lang,
                            adapter_path,
                            WHISPER_DEVICE,
                        )
                        _processor = WhisperProcessor.from_pretrained("openai/whisper-small")
                        _whisper = WhisperForConditionalGeneration.from_pretrained(
                            "openai/whisper-small", **whisper_kwargs,
                        )
                        if WHISPER_DEVICE != "auto":
                            _whisper = _whisper.to(WHISPER_DEVICE)
                        _whisper.config.forced_decoder_ids = None
                        _whisper.config.suppress_tokens = []
                        _whisper = PeftModel.from_pretrained(_whisper, adapter_path)
                        _whisper = _whisper.merge_and_unload()
                        _whisper.eval()
                        self._whisper_adapters[lang] = (_whisper, _processor)
                        logger.info("SpeechModel: Whisper+LoRA '%s' merged and ready", lang)
                    except Exception:
                        logger.exception("Failed to load Whisper adapter '%s' from %s", lang, adapter_path)

                # Legacy compat: set _whisper_peft to first loaded adapter
                if self._whisper_adapters:
                    first_lang = next(iter(self._whisper_adapters))
                    self._whisper_peft = self._whisper_adapters[first_lang]
                    logger.info(
                        "SpeechModel: %d Whisper adapters loaded (%s)",
                        len(self._whisper_adapters),
                        ", ".join(self._whisper_adapters.keys()),
                    )
            except ImportError:
                logger.warning("peft or transformers not installed; skipping Whisper LoRA adapters")
            except Exception:
                logger.exception("Failed to initialize Whisper adapters")

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

        # Accent detector for Whisper adapter routing (Phase 23)
        try:
            from .accent_detector import AccentDetector

            self._accent_detector = AccentDetector()
            if self._accent_detector.is_ready:
                logger.info("SpeechModel: accent detector ready")
        except Exception:
            logger.debug("Accent detector init skipped", exc_info=True)

        self._initialised = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_ready(self) -> bool:
        return self.enabled and self._initialised

    def transcribe(
        self, audio_bytes: bytes, sample_rate: int = 16000, language: str | None = None
    ) -> TranscribeResult:
        """Transcribe raw PCM bytes, filling in any timing the backend omitted.

        The local backends report duration_s / latency_s / rtf themselves; the
        cloud ones reported none of them, so `/v1/asr` answered with nulls
        whenever it fell through to Sunbird or Workers AI — which is the common
        case for every non-English locale. Input duration does not depend on
        which backend ran: it is a property of the audio that was posted. So it
        is computed once here and used to fill the gaps, rather than at each
        `return` in the chain where the next branch added would miss it.
        """
        t_start = time.perf_counter()
        result = self._transcribe_chain(audio_bytes, sample_rate, language)
        if result.duration_s is None:
            result.duration_s = _input_duration_s(audio_bytes, sample_rate)
        if result.latency_s is None:
            result.latency_s = round(time.perf_counter() - t_start, 3)
        if result.rtf is None and result.duration_s:
            result.rtf = round(result.latency_s / max(result.duration_s, 0.01), 2)
        return result

    def _transcribe_chain(
        self, audio_bytes: bytes, sample_rate: int = 16000, language: str | None = None
    ) -> TranscribeResult:
        """The backend fallback chain itself.

            ① Whisper + LoRA     — fine-tuned, language-specific, offline
            ② Local Sherpa ASR   — offline, if model available
            ③ faster-whisper     — CTranslate2 int8, good multilingual
            ④ Sunbird API        — cloud fallback when all local backends fail
        """
        if not self.enabled:
            return TranscribeResult(text="", backend="disabled", error="SPEECH_ENABLED=false")

        # ⓪ Accent detection — route to the best Whisper adapter automatically
        if (
            self._accent_detector is not None
            and self._whisper_adapters
            and audio_bytes
            and len(audio_bytes) > 16000  # need at least ~0.5s of audio
        ):
            try:
                accent = self._accent_detector.detect(audio_bytes, sample_rate=sample_rate)
                # Map accent label to adapter key
                accent_to_adapter = {
                    "ug_english_central": "en_ug_central",
                    "ug_english_eastern": "en_ug_eastern",
                    "ug_english_western": "en_ug_western",
                    "luganda_kampala": "lg",
                    "code_switch_en_lg": "code_switch_en_lg",
                }
                adapter_key = accent_to_adapter.get(accent.label)
                if adapter_key and adapter_key in self._whisper_adapters:
                    logger.info(
                        "Accent detected: %s (%.0f%%) → adapter '%s'",
                        accent.label, accent.confidence * 100, adapter_key,
                    )
                    language = adapter_key  # override language for adapter selection
            except Exception:
                logger.debug("Accent detection failed — using default adapter", exc_info=True)

        # ① Whisper + LoRA (fine-tuned, language-specific, offline — primary)
        whisper_pair = self._whisper_adapters.get(language) if language else None
        if whisper_pair is None and self._whisper_peft is not None:
            whisper_pair = self._whisper_peft  # fallback to default adapter
        if whisper_pair is not None:
            try:
                result = self._transcribe_whisper_peft(
                    audio_bytes, sample_rate, language, whisper_pair=whisper_pair,
                )
                if result and result.text:
                    return result
            except Exception:
                logger.debug("Whisper+LoRA failed", exc_info=True)

        # ② Local Sherpa ASR (if initialised by ml.scripts.asr)
        if self._asr is not None and self._breakers["asr"].allow_request():
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
            except (concurrent.futures.TimeoutError, Exception) as exc:
                self._breakers["asr"].record_failure()
                logger.debug("Local ASR failed (%s), trying faster-whisper", exc)

        # ③ faster-whisper (CTranslate2 — int8 quantised, offline)
        try:
            result = self._transcribe_faster_whisper(audio_bytes, sample_rate, language)
            if result and result.text:
                return result
        except Exception:
            logger.debug("faster-whisper failed", exc_info=True)

        # ③.5 Cloudflare Workers AI — PRIMARY cloud STT for English (configurable
        # model). Sunbird is preferred for Luganda, so this tier is English-gated.
        if (language or "en") == "en":
            try:
                cf_text = _cloud_call(
                    "Workers AI STT", self._cf_whisper_transcribe, audio_bytes, sample_rate, language
                )
            except TimeoutError:
                cf_text = ""
            if cf_text:
                return TranscribeResult(text=cf_text, language="en", backend="cf_workers_ai")

        # ④ Sunbird cloud (fallback when all local backends unavailable)
        try:
            from . import sunbird
            if sunbird.is_available():
                import io as _io, wave as _wave
                samples = self._decode_audio_bytes(audio_bytes, target_sr=sample_rate)
                import numpy as np
                pcm16 = (samples * 32768).clip(-32768, 32767).astype("int16")
                wav_buf = _io.BytesIO()
                with _wave.open(wav_buf, "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(sample_rate)
                    w.writeframes(pcm16.tobytes())
                # Read the code from sunbird's own locale table rather than a
                # local copy. The inline map here only knew en/lg, so Swahili,
                # Runyankole and Acholi audio was submitted tagged as English —
                # Sunbird then transcribed it with an English model and returned
                # confident nonsense, which the RAG layer had no way to spot.
                lang_code = sunbird.LOCALE_TO_SUNBIRD.get(language or "en", "eng")
                stt_result = _cloud_call(
                    "Sunbird STT",
                    sunbird.speech_to_text,
                    wav_buf.getvalue(),
                    language=lang_code,
                    filename="audio.wav",
                )
                if stt_result and stt_result.get("text"):
                    return TranscribeResult(
                        text=stt_result["text"],
                        language=stt_result.get("language", language or "en"),
                        backend="sunbird_cloud",
                    )
        except Exception:
            logger.debug("Sunbird STT fallback also failed")

        # ⑤ Cloudflare Workers AI Whisper (final cloud net; flag/budget-gated)
        try:
            cf_text = _cloud_call(
                "Workers AI STT", self._cf_whisper_transcribe, audio_bytes, sample_rate, language
            )
        except TimeoutError:
            cf_text = ""
        if cf_text:
            return TranscribeResult(
                text=cf_text, language=language or "en", backend="cf_workers_ai"
            )

        return TranscribeResult(
            text="", backend="unavailable",
            error="All ASR backends failed (Whisper+LoRA, Sherpa, faster-whisper, Sunbird, Workers AI)",
        )

    def _cf_whisper_transcribe(
        self, audio_bytes: bytes, sample_rate: int, language: str | None
    ) -> str:
        """Cloud STT via Cloudflare Workers AI Whisper (flag/budget/breaker-gated)."""
        from .flags import flags

        if not flags.is_enabled("cloudflare_fallback"):
            return ""
        if os.getenv("STT_FALLBACK_BACKEND", "").strip().lower() != "workers_ai":
            return ""
        try:
            from .providers import breakers, budget
            from .providers import config as cfg
            from .providers import gateway as gw
        except Exception:
            return ""
        if not (
            cfg.is_cloudflare_configured()
            and breakers.CF_STT_BREAKER.allow_request()
            and budget.try_consume_neurons(5)
        ):
            return ""
        try:
            import io as _io
            import wave as _wave

            import numpy as np

            samples = self._decode_audio_bytes(audio_bytes, target_sr=sample_rate)
            pcm16 = (samples * 32768).clip(-32768, 32767).astype("int16")
            buf = _io.BytesIO()
            with _wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(sample_rate)
                w.writeframes(pcm16.tobytes())
            res = gw.workers_ai_stt(buf.getvalue(), model=STT_FALLBACK_MODEL)
            breakers.CF_STT_BREAKER.record_success()
            return (res.get("text") or "").strip()
        except Exception:
            breakers.CF_STT_BREAKER.record_failure()
            logger.warning("Workers AI Whisper STT failed", exc_info=True)
            return ""

    def _cf_workers_ai_tts(
        self, text: str, voice: str | None, language: str
    ) -> "SynthesizeResult | None":
        """Cloud TTS via Cloudflare Workers AI (flag/backend/config/breaker/budget
        gated). Tries TTS_FALLBACK_MODEL then TTS_FALLBACK_MODEL_2 for resilience.
        Returns None when disabled/unavailable so the caller falls through."""
        from .flags import flags

        if not flags.is_enabled("cloudflare_fallback"):
            return None
        if os.getenv("TTS_FALLBACK_BACKEND", "").strip().lower() != "workers_ai":
            return None
        try:
            from .providers import breakers, budget
            from .providers import config as cfg
            from .providers import gateway as gw
        except Exception:
            return None
        if not (cfg.is_cloudflare_configured() and breakers.CF_TTS_BREAKER.allow_request()):
            return None
        lang = "en" if (language or "en") == "en" else language
        for model in (TTS_FALLBACK_MODEL, TTS_FALLBACK_MODEL_2):
            if not model:
                continue
            if not budget.try_consume_neurons(10):
                break
            t0 = time.perf_counter()
            try:
                out = gw.workers_ai_tts(text, model=model, lang=lang)
                audio = out.get("audio") or b""
                if not audio:
                    continue
                breakers.CF_TTS_BREAKER.record_success()
                # Was a WAV-only header peek that still left num_samples and
                # duration at zero; _audio_metadata covers MP3 too and fills
                # all three, falling back to the provider's declared rate.
                sr, samples, duration = _audio_metadata(audio, out.get("sample_rate") or 24000)
                return SynthesizeResult(
                    audio=audio, sample_rate=sr, num_samples=samples, duration_s=duration,
                    latency_s=round(time.perf_counter() - t0, 3),
                    backend="cf_workers_ai", voice=model,
                )
            except Exception:
                breakers.CF_TTS_BREAKER.record_failure()
                logger.warning("Workers AI TTS failed (model=%s)", model, exc_info=True)
                continue
        return None

    def _transcribe_whisper_peft(
        self,
        audio_bytes: bytes,
        sample_rate: int,
        language: str | None,
        whisper_pair: tuple | None = None,
    ) -> TranscribeResult | None:
        """Offline STT via Whisper + LoRA adapter (language-specific)."""
        import numpy as np
        import torch

        pair = whisper_pair or self._whisper_peft
        if pair is None:
            return None
        model, processor = pair
        t0 = time.perf_counter()
        samples = self._decode_audio_bytes(audio_bytes, target_sr=16000)

        input_features = processor.feature_extractor(
            samples, sampling_rate=16000, return_tensors="pt",
        ).input_features.to(model.device, dtype=model.dtype)

        # Scoped to this line, not the rule. ura-llm01 guards prompt injection
        # (OWASP LLM01) by matching `.generate(` on user text; this is Whisper
        # ASR and the argument is `input_features`, a mel spectrogram built from
        # audio samples. There is no prompt for an instruction to be injected
        # into, and InputGuard.check() takes a string, so it cannot be applied
        # to a tensor. The transcript this produces IS guarded — downstream in
        # service.generate(), whose first step is InputGuard.check(message).
        #
        # The directive has to sit on the line immediately above the match;
        # anything between it and the code makes it inert.
        with torch.no_grad():
            # nosemgrep: ura-llm01-raw-user-input-to-llm
            predicted_ids = model.generate(input_features, max_new_tokens=225)
        text = processor.batch_decode(predicted_ids, skip_special_tokens=True)[0].strip()

        latency = time.perf_counter() - t0
        duration = len(samples) / max(sample_rate, 1)
        logger.info("Whisper+LoRA STT: '%s' (%.1fs)", text[:60], latency)
        return TranscribeResult(
            text=text,
            language=language or "lg",
            duration_s=round(duration, 2),
            latency_s=round(latency, 3),
            rtf=round(latency / max(duration, 0.01), 2),
            backend="whisper_peft",
        )

    def _transcribe_faster_whisper(
        self, audio_bytes: bytes, sample_rate: int, language: str | None
    ) -> TranscribeResult | None:
        """Offline STT via faster-whisper (CTranslate2 int8).

        The WhisperModel is loaded once and cached on self._faster_whisper
        to avoid the 2-5s model load penalty on every fallback call.
        """
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            return None

        import numpy as np
        import tempfile, io, wave

        t0 = time.perf_counter()
        samples = self._decode_audio_bytes(audio_bytes, target_sr=sample_rate)

        # Write WAV to temp file (faster-whisper needs file path)
        buf = io.BytesIO()
        pcm16 = (samples * 32768).clip(-32768, 32767).astype("int16")
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(pcm16.tobytes())
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(buf.getvalue())
            tmp_path = f.name

        try:
            # Cache model on first use — avoids 2-5s reload per request
            if self._faster_whisper is None:
                with self._lock:
                    if self._faster_whisper is None:
                        logger.info("Loading faster-whisper model (one-time, ~150MB)...")
                        self._faster_whisper = WhisperModel(
                            "base", device="cpu", compute_type="int8",
                        )
            whisper_lang = {"lg": None, "en": "en"}.get(language or "en", language)
            segments, info = self._faster_whisper.transcribe(
                tmp_path, language=whisper_lang, beam_size=3,
            )
            text = " ".join(seg.text.strip() for seg in segments)
            latency = time.perf_counter() - t0
            duration = len(samples) / max(sample_rate, 1)
            logger.info("faster-whisper STT: '%s' (%.1fs, lang=%s)", text[:60], latency, info.language)
            return TranscribeResult(
                text=text,
                language=info.language or language or "en",
                duration_s=round(duration, 2),
                latency_s=round(latency, 3),
                rtf=round(latency / max(duration, 0.01), 2),
                backend="faster_whisper",
            )
        finally:
            import os
            os.unlink(tmp_path)

    def synthesize(
        self,
        text: str,
        voice: str | None = None,
        language: str = "en",
    ) -> SynthesizeResult:
        """Synthesize text to WAV bytes.

        Fallback chain (local-first for production):
            ① Local Sherpa/Piper — offline TTS (primary)
            ② edge-tts           — Microsoft neural voices (needs internet)
            ③ Sunbird API        — cloud fallback for native Luganda voices
        """
        if not self.enabled:
            return SynthesizeResult(
                audio=b"", sample_rate=0, num_samples=0, duration_s=0.0,
                latency_s=0.0, backend="disabled", voice="", error="SPEECH_ENABLED=false",
            )
        voice = voice or LOCAL_TTS_VOICES.get(language, DEFAULT_EN_VOICE)

        # ⓪ Phrase cache — repeated short prompts (greetings, empathy openers,
        #    workflow questions) skip the whole backend chain.
        #
        #    usedforsecurity=False is not a suppression: this digest keys an
        #    in-process cache, so it needs to be fast and stable, not
        #    collision-resistant against an adversary. Saying so lets the hash
        #    keep working where a FIPS-restricted build would otherwise refuse
        #    SHA-1 outright, and tells Bandit (B324) the truth rather than
        #    hiding the finding behind a nosec.
        cache_key = (
            hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest(),
            voice,
            language,
        )
        if TTS_CACHE_SIZE > 0:
            with self._tts_cache_lock:
                cached = self._tts_cache.get(cache_key)
                if cached is not None:
                    self._tts_cache.move_to_end(cache_key)
                    return replace(cached, latency_s=0.0, backend=f"{cached.backend}+cache")

        result = self._synthesize_uncached(text, voice, language)
        if TTS_CACHE_SIZE > 0 and result.audio and not result.error:
            with self._tts_cache_lock:
                self._tts_cache[cache_key] = result
                self._tts_cache.move_to_end(cache_key)
                while len(self._tts_cache) > TTS_CACHE_SIZE:
                    self._tts_cache.popitem(last=False)
        return result

    def _synthesize_uncached(
        self,
        text: str,
        voice: str,
        language: str,
    ) -> SynthesizeResult:
        """The actual backend fallback chain behind :meth:`synthesize`."""
        # Local Piper and edge-tts both default to an English voice for any
        # language they have no model for, and then *succeed* — so left in
        # their natural order they answer a Runyankole question in an English
        # voice and the chain never reaches Sunbird's native speaker.
        #
        # So for languages whose only genuine voice is Sunbird's, try Sunbird
        # FIRST rather than removing the other backends from the chain.
        # Ordering, not exclusion: an earlier revision skipped local and edge
        # outright, which turned a Sunbird outage into silence. It did exactly
        # that in production — Sunbird's TTS endpoint had been answering 405
        # since it moved, so those locales returned no audio at all. Degraded
        # audio beats no audio; a wrong-sounding voice is still recoverable
        # information, silence is not.
        prefer_native_cloud = (
            language not in LOCAL_TTS_VOICES and _sunbird_has_native_voice(language)
        )

        def _try_sunbird() -> SynthesizeResult | None:
            """One attempt at Sunbird's native voice. None if unavailable."""
            t_sunbird = time.perf_counter()
            try:
                from . import sunbird

                if not sunbird.is_available():
                    return None

                def _fetch():
                    # Forward the requested speaker. `voice` here is whatever the
                    # caller asked for (or this locale's local default, filled in
                    # by synthesize); sunbird validates it against its own
                    # per-locale catalog and ignores anything foreign, so a Piper
                    # or edge-tts voice name arriving here cannot be forwarded.
                    tts_result = sunbird.text_to_speech(text, locale=language, voice=voice)
                    if not tts_result or not tts_result.get("audio_url"):
                        return None
                    import httpx

                    return httpx.get(tts_result["audio_url"], timeout=15)

                audio_resp = _cloud_call("Sunbird TTS", _fetch)
                if audio_resp is None:
                    return None
                if audio_resp.status_code == 200 and _looks_like_audio(audio_resp.content):
                    # These were 22050/0/0.0 regardless of what came back. The
                    # audio is actually 24 kHz, so the advertised rate was wrong
                    # for every native voice, and latency read 0.0 for a call
                    # that had just crossed the network twice.
                    rate, samples, duration = _audio_metadata(audio_resp.content, 24000)
                    return SynthesizeResult(
                        audio=audio_resp.content,
                        sample_rate=rate,
                        num_samples=samples,
                        duration_s=duration,
                        latency_s=round(time.perf_counter() - t_sunbird, 3),
                        backend="sunbird_cloud",
                        # The speaker actually used, not just the language. With
                        # a catalog of eight Luganda voices, "sunbird_lg" cannot
                        # answer "which one did I hear?" — the field a caller
                        # checks after choosing one.
                        voice=sunbird.resolve_tts_voice(language, voice) or f"sunbird_{language}",
                    )
                logger.warning(
                    "Sunbird TTS returned non-audio payload (status=%s, %d bytes)",
                    audio_resp.status_code,
                    len(audio_resp.content),
                )
            except Exception:
                logger.debug("Sunbird TTS attempt failed", exc_info=True)
            return None

        # ⓪ Native cloud voice first, for languages that have one there and
        #    nowhere else. Falls through on failure rather than giving up.
        if prefer_native_cloud:
            result = _try_sunbird()
            if result:
                return result
            logger.info(
                "Sunbird native voice unavailable for %s; degrading to the "
                "generic chain rather than returning silence",
                language,
            )

        # ① Local Sherpa/Piper TTS (primary — offline, low-latency)
        if self._breakers["tts"].allow_request():
            future = self._executor.submit(self._do_synthesize, text, voice)
            try:
                result = future.result(timeout=SPEECH_DEADLINE_S)
                self._breakers["tts"].record_success()
                return result
            except (concurrent.futures.TimeoutError, Exception) as exc:
                self._breakers["tts"].record_failure()
                logger.debug("Local TTS failed (%s), trying edge-tts", exc)

        # ①.5 Cloudflare Workers AI — PRIMARY cloud TTS for English (configurable
        # model + resilience fallback). Sunbird stays primary for Luganda.
        if (language or "en") == "en":
            try:
                result = _cloud_call("Workers AI TTS", self._cf_workers_ai_tts, text, voice, language)
                if result and result.audio:
                    return result
            except Exception:
                logger.debug("Workers AI TTS failed", exc_info=True)

        # ①.75 Native cloud voice for a locale that has a local voice on paper
        #      but whose local model may be absent from the running image —
        #      today that is Luganda, whose luganda-vits-v1 does not ship in the
        #      slim deploy image.
        #
        #      This MUST come before edge-tts. edge has no voice for any Ugandan
        #      language, so it answers Luganda with an English one; letting it
        #      run first means it always succeeds and Sunbird's native Luganda
        #      speaker at ③ is never reached. That is not hypothetical — while
        #      the edge voice was misconfigured, stage ② failed and the chain
        #      fell through to Sunbird; repairing the edge voice would have
        #      silently swapped Luganda's native speaker for an English one,
        #      leaving it the only local language without a native voice.
        if not prefer_native_cloud and (language or "en") != "en":
            result = _try_sunbird()
            if result:
                return result

        # ② edge-tts (Microsoft neural voices — needs internet, no API key)
        try:
            result = _cloud_call("edge-tts", self._synthesize_edge_tts, text, language)
            if result and result.audio:
                return result
        except Exception:
            logger.debug("edge-tts failed", exc_info=True)

        # ③ Sunbird cloud TTS — English only by this point; every other locale
        #    already tried it at ⓪ or ①.75, and the gates are mutually exclusive
        #    so Sunbird is still attempted at most once per request.
        if not prefer_native_cloud and (language or "en") == "en":
            result = _try_sunbird()
            if result:
                return result

        return SynthesizeResult(
            audio=b"", sample_rate=0, num_samples=0, duration_s=0.0,
            latency_s=0.0, backend="error", voice=voice, error="All TTS backends failed",
        )

    # ------------------------------------------------------------------
    # Streaming extensions (Phase 23)
    # ------------------------------------------------------------------

    def synthesize_sentences(
        self,
        text: str,
        voice: str | None = None,
        language: str = "en",
    ):
        """Sentence-level chunked TTS.

        Yields one :class:`SynthesizeResult` per sentence.  The caller
        can abort between yields for barge-in support.

        Reuses the existing ``synthesize()`` fallback chain internally.
        """
        import re

        sentences = re.split(r"(?<=[.!?])\s+", text.strip())
        sentences = [s.strip() for s in sentences if s.strip()]
        if not sentences:
            sentences = [text.strip()]

        for sentence in sentences:
            yield self.synthesize(sentence, voice=voice, language=language)

    async def transcribe_async(
        self,
        audio_bytes: bytes,
        sample_rate: int = 16000,
        language: str | None = None,
    ) -> "TranscribeResult":
        """Async wrapper around ``transcribe()`` — runs in executor.

        Intended for use from async WebSocket handlers.  Uses the
        existing multi-tier fallback chain.
        """
        import asyncio

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            self.transcribe,
            audio_bytes,
            sample_rate,
            language,
        )

    def _synthesize_edge_tts(self, text: str, language: str) -> SynthesizeResult | None:
        """TTS via edge-tts (Microsoft neural voices, free, needs internet).

        Runs the async edge_tts.Communicate in a dedicated event loop on a
        new thread to avoid the nested-loop antipattern (this method is
        called from a ThreadPoolExecutor worker, not the main async loop).
        """
        try:
            import edge_tts
            import io
        except ImportError:
            return None

        voice_map = {
            "lg": SPEECH_LG_EDGE_VOICE,
            "en": SPEECH_EN_EDGE_VOICE,
        }
        voice_id = voice_map.get(language, SPEECH_EN_EDGE_VOICE)
        t0 = time.perf_counter()

        def _generate_sync() -> bytes:
            """Run the async generator in an isolated event loop on this thread."""
            import asyncio

            loop = asyncio.new_event_loop()
            try:
                async def _stream() -> bytes:
                    communicate = edge_tts.Communicate(text[:3000], voice_id)
                    buf = io.BytesIO()
                    async for chunk in communicate.stream():
                        if chunk["type"] == "audio":
                            buf.write(chunk["data"])
                    return buf.getvalue()

                return loop.run_until_complete(_stream())
            finally:
                loop.close()

        try:
            # Run in a separate thread to guarantee no event loop conflict
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(_generate_sync)
                audio_bytes = future.result(timeout=SPEECH_DEADLINE_S)
        except Exception as e:
            logger.warning("edge-tts failed: %s", e)
            return None

        if not audio_bytes:
            return None

        latency = time.perf_counter() - t0
        logger.info("edge-tts: %d bytes in %.1fs (voice=%s)", len(audio_bytes), latency, voice_id)
        # edge-tts returns MP3, but duration was computed as len/(24000*2) —
        # the bytes-to-samples formula for raw PCM16. Against a ~48 kbps MP3
        # that understates a real phrase by roughly 5x. Read the frame header.
        rate, samples, duration = _audio_metadata(audio_bytes, 24000)
        return SynthesizeResult(
            audio=audio_bytes,
            sample_rate=rate,
            num_samples=samples,
            duration_s=duration,
            latency_s=round(latency, 3),
            backend="edge_tts",
            voice=voice_id,
        )

    def translate(
        self,
        text: str,
        source_lang: str = "en",
        target_lang: str = "lg",
    ) -> TranslateResult:
        """Translate text. Priority: Sunbird cloud → local MT → LLM prompted."""
        if not self.enabled:
            return TranslateResult(
                text="",
                source_lang=source_lang,
                target_lang=target_lang,
                latency_s=0.0,
                backend="disabled",
                error="SPEECH_ENABLED=false",
            )
        if self._mt is None and self._chat_model is None:
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
        future = self._executor.submit(self._do_translate, text, source_lang, target_lang)
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

    def _decode_audio_bytes(self, audio_bytes: bytes, target_sr: int = 16000):
        """Decode audio bytes of any supported format into float32 mono at target_sr.

        Supports: raw PCM (int16/float32), WAV, WebM/Opus, OGG, MP3.
        Falls back to raw PCM int16 if format detection fails.

        Returns:
            numpy float32 array in [-1, 1] at target_sr.
        """
        import numpy as np

        if not audio_bytes or len(audio_bytes) < 4:
            return np.zeros(0, dtype=np.float32)

        # --- WAV detection (starts with RIFF header) ---
        if audio_bytes[:4] == b"RIFF":
            return self._decode_wav(audio_bytes, target_sr)

        # --- WebM/Matroska detection (starts with 0x1A45DFA3) ---
        if audio_bytes[:4] in (b"\x1a\x45\xdf\xa3", b"OggS"):
            return self._decode_container(audio_bytes, target_sr)

        # --- MP3 detection (ID3 header or sync word 0xFFE0+) ---
        if audio_bytes[:3] == b"ID3" or (audio_bytes[0] == 0xFF and (audio_bytes[1] & 0xE0) == 0xE0):
            return self._decode_container(audio_bytes, target_sr)

        # --- Fallback: assume raw PCM ---
        return self._pcm_bytes_to_float(audio_bytes)

    def _decode_wav(self, audio_bytes: bytes, target_sr: int = 16000):
        """Decode WAV bytes to float32 numpy array."""
        import io
        import wave

        import numpy as np

        try:
            with wave.open(io.BytesIO(audio_bytes), "rb") as w:
                sr = w.getframerate()
                sw = w.getsampwidth()
                nc = w.getnchannels()
                frames = w.readframes(w.getnframes())

            if sw == 2:
                arr = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
            elif sw == 4:
                arr = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
            else:
                arr = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

            # Mix to mono
            if nc > 1:
                arr = arr.reshape(-1, nc).mean(axis=1)

            # Resample if needed
            if sr != target_sr:
                arr = self._resample(arr, sr, target_sr)

            return arr
        except Exception:
            logger.debug("WAV decode failed, falling back to raw PCM", exc_info=True)
            return self._pcm_bytes_to_float(audio_bytes)

    def _decode_container(self, audio_bytes: bytes, target_sr: int = 16000):
        """Decode WebM/OGG/MP3 via ffmpeg or pydub, falling back to raw PCM."""
        import numpy as np

        # Try ffmpeg (subprocess — most reliable for WebM/Opus)
        try:
            import subprocess
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
                f.write(audio_bytes)
                tmp_in = f.name
            tmp_out = tmp_in + ".wav"

            try:
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-i", tmp_in,
                        "-ar", str(target_sr), "-ac", "1", "-f", "wav",
                        tmp_out,
                    ],
                    check=True, capture_output=True, timeout=30,
                )
                return self._decode_wav(open(tmp_out, "rb").read(), target_sr)
            finally:
                import os
                for p in (tmp_in, tmp_out):
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
        except (FileNotFoundError, subprocess.CalledProcessError):
            logger.debug("ffmpeg not available for container decode")

        # Try pydub as fallback
        try:
            from pydub import AudioSegment
            import io

            seg = AudioSegment.from_file(io.BytesIO(audio_bytes))
            seg = seg.set_channels(1).set_frame_rate(target_sr).set_sample_width(2)
            arr = np.frombuffer(seg.raw_data, dtype=np.int16).astype(np.float32) / 32768.0
            return arr
        except ImportError:
            logger.debug("pydub not available for container decode")
        except Exception:
            logger.debug("pydub decode failed", exc_info=True)

        # Last resort: treat as raw PCM
        logger.warning("Could not decode container audio — treating as raw PCM")
        return self._pcm_bytes_to_float(audio_bytes)

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

    @staticmethod
    def _resample(audio, orig_sr: int, target_sr: int):
        """Simple linear resampling (no dependency on librosa)."""
        import numpy as np

        if orig_sr == target_sr:
            return audio
        ratio = target_sr / orig_sr
        new_len = int(len(audio) * ratio)
        indices = np.linspace(0, len(audio) - 1, new_len)
        return np.interp(indices, np.arange(len(audio)), audio).astype(np.float32)

    def _do_transcribe(self, audio_bytes: bytes, sample_rate: int) -> TranscribeResult:
        samples = self._decode_audio_bytes(audio_bytes, target_sr=sample_rate)
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
        """Translate via the model-routing policy, with a language-aware order.

        For **Luganda** (``lg↔en``) Sunbird's Luganda-native NLLB leads — it is the
        most accurate for this low-resource language — with Gemini 2.5 Flash as a
        strong fallback (then Cloudflare Llama → local MT → prompted Qwen3). For all
        other languages Gemini leads. Every cloud tier self-skips when its
        flags/keys are absent, so a local/GPU deploy still reaches the offline tiers.
        """
        from .providers import routing

        t0 = time.perf_counter()

        def _res(out: str, backend: str) -> TranslateResult:
            return TranslateResult(
                text=out, source_lang=source_lang, target_lang=target_lang,
                latency_s=round(time.perf_counter() - t0, 3), backend=backend,
            )

        def _gemini():
            out = self._gemini_translate(text, source_lang, target_lang)
            return _res(out, "gemini_flash") if out else None

        def _cf():
            out = self._cf_llama_translate(text, source_lang, target_lang)
            return _res(out, "cf_workers_ai") if out else None

        def _sunbird():
            try:
                from . import sunbird
                if sunbird.is_available():
                    # Same inline-map defect as the STT path had: this knew only
                    # en/lg and passed everything else through unmapped, so
                    # Swahili went out as "sw" rather than "swa". Runyankole and
                    # Acholi only worked because their locale code happens to
                    # equal their Sunbird code. Use the canonical table so that
                    # is intentional rather than luck.
                    #
                    # Swahili still won't translate here — Sunbird's translate
                    # endpoint covers English + five Ugandan languages only (see
                    # TRANSLATION_LANGUAGES) — but it now declines with the right
                    # code and falls through to Gemini/Workers AI/prompted.
                    src_code = sunbird.LOCALE_TO_SUNBIRD.get(source_lang, source_lang)
                    tgt_code = sunbird.LOCALE_TO_SUNBIRD.get(target_lang, target_lang)
                    result = sunbird.translate(text, src_code, tgt_code)
                    if result:
                        return _res(result, "sunbird_cloud")
            except Exception:
                logger.debug("Sunbird translate failed", exc_info=True)
            return None

        def _local():
            if self._mt is not None:
                try:
                    result = self._mt.translate(text, source_lang=source_lang, target_lang=target_lang)
                    if result and result.text:
                        return _res(result.text, result.backend or "local_mt")
                except Exception:
                    logger.debug("Local MT failed", exc_info=True)
            return None

        def _prompted():
            if self._chat_model is not None:
                try:
                    from . import llm as llm_module
                    reply = llm_module.translate_text(text, source_lang=source_lang, target_lang=target_lang)
                    if reply and reply.strip():
                        return _res(reply.strip(), "prompted_qwen3")
                except Exception:
                    logger.debug("Prompted MT via Qwen3 failed", exc_info=True)
            return None

        # Sunbird's NLLB leads for every pair its translate endpoint actually
        # covers — not just Luganda. The gate used to be `"lg" in (src, tgt)`,
        # which sent Runyankole, Acholi, Ateso and Lugbara to Gemini FIRST; and
        # because Gemini answers, Sunbird was never reached for the four
        # languages it is trained on. Measured in production: lg took 3.2s on
        # sunbird_cloud while nyn/ach/teo took 17-18s on gemini_flash, for
        # languages a general model approximates rather than speaks.
        #
        # Swahili deliberately still leads with Gemini: it has a Sunbird TTS
        # voice but their translate endpoint does not serve it (see
        # TRANSLATION_LANGUAGES), so leading with Sunbird there would just
        # spend a failed call before falling through.
        def _sunbird_translates(src: str, tgt: str) -> bool:
            try:
                from . import sunbird
            except Exception:  # noqa: BLE001 — optional dependency
                return False
            codes = {
                sunbird.LOCALE_TO_SUNBIRD.get(lang.lower(), lang.lower())
                for lang in (src, tgt)
            }
            return codes <= sunbird.TRANSLATION_LANGUAGES

        if _sunbird_translates(source_lang, target_lang):
            tiers = [
                ("sunbird_cloud", _sunbird), ("gemini_flash", _gemini),
                ("cf_workers_ai", _cf), ("local_mt", _local), ("prompted_qwen3", _prompted),
            ]
        else:
            tiers = [
                ("gemini_flash", _gemini), ("cf_workers_ai", _cf),
                ("sunbird_cloud", _sunbird), ("local_mt", _local), ("prompted_qwen3", _prompted),
            ]

        prev: str | None = None
        for name, tier in tiers:
            try:
                result = _cloud_call(f"translate:{name}", tier)
            except TimeoutError:
                result = None
            if result is not None:
                if prev is not None:
                    routing.log_fallback("translate", prev, result.backend, f"{prev}_unavailable")
                routing.log_model_use("translate", result.backend)
                return result
            prev = name

        return TranslateResult(
            text="", source_lang=source_lang, target_lang=target_lang,
            latency_s=round(time.perf_counter() - t0, 3), backend="error",
            error="No translation backend available (Sunbird, Gemini, CF Llama, local MT, Qwen3 all failed)",
        )

    @staticmethod
    def _gemini_translate(text: str, source_lang: str, target_lang: str) -> str:
        """Translate via Gemini 2.5 Flash through the AI Gateway (Luganda-strong).

        Flag/budget/breaker-gated; returns "" when disabled or unavailable so the
        Sunbird tier still runs.
        """
        from .flags import flags

        if not flags.is_enabled("cloudflare_fallback"):
            return ""
        if os.getenv("TRANSLATE_FALLBACK_BACKEND", "").strip().lower() != "gemini":
            return ""
        try:
            from .providers import breakers, budget
            from .providers import config as cfg
            from .providers import gateway as gw
        except Exception:
            return ""
        if not (
            cfg.is_gemini_configured()
            and breakers.GEMINI_BREAKER.allow_request()
            and budget.try_consume_gemini_call()
        ):
            return ""
        names = {
            "lg": "Luganda", "en": "English", "nyn": "Runyankole",
            "ach": "Acholi", "sw": "Swahili",
        }
        src, tgt = names.get(source_lang, source_lang), names.get(target_lang, target_lang)
        try:
            out = gw.gemini_generate(
                text,
                system=(
                    f"You are a professional translator. Translate the user's text "
                    f"from {src} to {tgt}. Output ONLY the translation — no notes, "
                    f"quotes, or transliteration."
                ),
                max_tokens=512,
                temperature=0.1,
            )
            breakers.GEMINI_BREAKER.record_success()
            return out.strip()
        except Exception:
            breakers.GEMINI_BREAKER.record_failure()
            logger.warning("Gemini translation failed", exc_info=True)
            return ""

    @staticmethod
    def _cf_llama_translate(text: str, source_lang: str, target_lang: str) -> str:
        """Translate via Cloudflare Workers AI Llama (prompted) — the model-routing
        fallback after Gemini. Flag/budget/breaker-gated; returns "" when disabled
        or unavailable so the Sunbird tier still runs."""
        from .flags import flags

        if not flags.is_enabled("cloudflare_fallback"):
            return ""
        if os.getenv("TRANSLATE_FALLBACK_BACKEND", "").strip().lower() not in ("gemini", "workers_ai"):
            return ""
        try:
            from .providers import breakers, budget
            from .providers import config as cfg
            from .providers import gateway as gw
            from .providers import routing
        except Exception:
            return ""
        if not (
            cfg.is_cloudflare_configured()
            and breakers.CF_LLM_BREAKER.allow_request()
            and budget.try_consume_neurons(5)
        ):
            return ""
        names = {
            "lg": "Luganda", "en": "English", "nyn": "Runyankole",
            "ach": "Acholi", "sw": "Swahili",
        }
        src, tgt = names.get(source_lang, source_lang), names.get(target_lang, target_lang)
        try:
            out = gw.workers_ai_chat(
                [
                    {"role": "system", "content": (
                        f"You are a professional translator. Translate the user's text "
                        f"from {src} to {tgt}. Output ONLY the translation — no notes, "
                        f"quotes, or transliteration."
                    )},
                    {"role": "user", "content": text},
                ],
                model=routing.CF_LLM_MODEL,
                max_tokens=512,
                temperature=0.1,
            )
            breakers.CF_LLM_BREAKER.record_success()
            return (out or "").strip()
        except Exception:
            breakers.CF_LLM_BREAKER.record_failure()
            logger.warning("CF Llama translation failed", exc_info=True)
            return ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)

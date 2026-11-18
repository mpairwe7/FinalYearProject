"""ASR inference — Whisper with optional sherpa-onnx backend."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class TranscribeResult:
    text: str
    language: str | None = None
    duration_s: float | None = None
    latency_s: float | None = None
    rtf: float | None = None
    backend: str = "unknown"
    error: str | None = None


class AsrTranscriber:
    """Lightweight ASR wrapper with sherpa-onnx -> transformers -> mock fallback."""

    def __init__(self, sherpa_dir: str | Path | None = None, backend: str = "auto"):
        self.backend = backend
        self._sherpa_dir = Path(sherpa_dir) if sherpa_dir else None
        self._pipeline = None

        if backend in ("auto", "sherpa") and self._sherpa_dir and self._sherpa_dir.exists():
            self._try_init_sherpa()

        if self._pipeline is None and backend in ("auto", "transformers"):
            self._try_init_transformers()

        if self._pipeline is None:
            logger.info("ASR: using mock backend (no sherpa/transformers available)")
            self.backend = "mock"

    def _try_init_sherpa(self) -> None:
        try:
            import sherpa_onnx

            encoder = self._sherpa_dir / "encoder.int8.onnx"
            decoder = self._sherpa_dir / "decoder.int8.onnx"
            tokens = self._sherpa_dir / "tokens.txt"
            if not all(p.exists() for p in (encoder, decoder, tokens)):
                logger.debug("Sherpa model files missing at %s", self._sherpa_dir)
                return
            self._pipeline = "sherpa"
            self.backend = "sherpa"
            logger.info("ASR: sherpa-onnx backend ready from %s", self._sherpa_dir)
        except ImportError:
            logger.debug("sherpa-onnx not installed")

    def _try_init_transformers(self) -> None:
        try:
            from transformers import pipeline

            self._hf_pipe = pipeline(
                "automatic-speech-recognition",
                model="openai/whisper-small",
                device="cpu",
            )
            self._pipeline = "transformers"
            self.backend = "transformers"
            logger.info("ASR: transformers whisper-small backend ready")
        except Exception:
            logger.debug("Transformers ASR init failed", exc_info=True)

    def transcribe_array(
        self, samples, sample_rate: int = 16000
    ) -> TranscribeResult:
        t0 = time.perf_counter()
        duration_s = len(samples) / sample_rate if hasattr(samples, "__len__") else None

        if self._pipeline == "transformers":
            try:
                result = self._hf_pipe(
                    {"raw": samples, "sampling_rate": sample_rate},
                    return_timestamps=False,
                )
                latency = time.perf_counter() - t0
                text = result.get("text", "") if isinstance(result, dict) else ""
                return TranscribeResult(
                    text=text.strip(),
                    duration_s=duration_s,
                    latency_s=round(latency, 3),
                    rtf=round(latency / duration_s, 2) if duration_s else None,
                    backend="transformers",
                )
            except Exception as e:
                return TranscribeResult(text="", backend="transformers", error=str(e))

        return TranscribeResult(
            text="", backend="mock",
            error="No ASR backend available",
        )

    def transcribe(
        self, audio_bytes: bytes, sample_rate: int = 16000, language: str | None = None
    ) -> TranscribeResult:
        import numpy as np

        samples = np.frombuffer(audio_bytes, dtype=np.int16).astype("float32") / 32768.0
        return self.transcribe_array(samples, sample_rate)

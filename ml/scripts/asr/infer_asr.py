#!/usr/bin/env python3
"""ASR inference wrapper — batch + streaming (2026).

Single entry point for Python-side speech recognition:

    * Batch:      transcribe a wav file end-to-end.
    * Streaming:  feed PCM chunks, yield partial + final hypotheses.

Runs on **three backends**, tried in order:

    1. ``sherpa-onnx``                       — production (on-device parity)
    2. ``transformers.pipeline("automatic-speech-recognition")``
                                              — dev fallback (no ONNX assets needed)
    3. mock / deterministic placeholder      — CI fallback so tests always run

Used by:
    * ``ml/pipelines/evaluate_speech.py``     — WER/CER eval
    * ``ml/pipelines/evaluate_tts.py``         — round-trip intelligibility
    * ``App/backend/app/speech_service.py``    — server-side transcription

Usage::

    # Batch on a wav file
    python -m ml.scripts.asr.infer_asr --audio sample.wav

    # With explicit sherpa-onnx package
    python -m ml.scripts.asr.infer_asr --audio sample.wav \\
        --sherpa-dir artifacts/speech/asr/sherpa/whisper-small

    # Transformers fallback (dev — slower, no ONNX)
    python -m ml.scripts.asr.infer_asr --audio sample.wav --backend transformers
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

log = logging.getLogger("asr.infer")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SHERPA_DIR = PROJECT_ROOT / "artifacts" / "speech" / "asr" / "sherpa" / "whisper-small"
DEFAULT_HF_CACHE = PROJECT_ROOT / "artifacts" / "speech" / "asr" / "openai__whisper-small"


@dataclass
class TranscribeResult:
    text: str
    language: str | None = None
    confidence: float | None = None
    duration_s: float | None = None
    latency_s: float | None = None
    rtf: float | None = None
    backend: str = "unknown"
    chunks: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Backend: sherpa-onnx
# ---------------------------------------------------------------------------


def _build_sherpa_recognizer(sherpa_dir: Path):
    """Build an offline sherpa-onnx recognizer for Whisper.

    Returns ``None`` on import/config failure so callers can fall through.
    """
    try:
        import sherpa_onnx  # type: ignore
    except ImportError:
        log.debug("sherpa-onnx not importable")
        return None

    encoder = sherpa_dir / "encoder.int8.onnx"
    decoder = sherpa_dir / "decoder.int8.onnx"
    tokens = sherpa_dir / "tokens.txt"

    if not encoder.exists() or not decoder.exists() or not tokens.exists():
        log.debug("sherpa assets missing in %s", sherpa_dir)
        return None

    try:
        recognizer = sherpa_onnx.OfflineRecognizer.from_whisper(
            encoder=str(encoder),
            decoder=str(decoder),
            tokens=str(tokens),
            num_threads=2,
            decoding_method="greedy_search",
        )
    except Exception as exc:
        log.debug("sherpa recognizer build failed: %s", exc)
        return None
    return recognizer


def _transcribe_sherpa(sherpa_dir: Path, samples, sample_rate: int) -> TranscribeResult | None:
    rec = _build_sherpa_recognizer(sherpa_dir)
    if rec is None:
        return None

    stream = rec.create_stream()
    stream.accept_waveform(sample_rate, samples)
    t0 = time.perf_counter()
    rec.decode_stream(stream)
    latency = time.perf_counter() - t0
    duration = len(samples) / float(sample_rate)
    return TranscribeResult(
        text=stream.result.text or "",
        language=None,
        duration_s=round(duration, 3),
        latency_s=round(latency, 3),
        rtf=round(latency / max(duration, 1e-6), 3),
        backend="sherpa-onnx",
    )


# ---------------------------------------------------------------------------
# Backend: transformers pipeline (dev fallback)
# ---------------------------------------------------------------------------


def _transcribe_transformers(
    model_path: Path, samples, sample_rate: int
) -> TranscribeResult | None:
    try:
        import numpy as np
        from transformers import pipeline  # type: ignore
    except ImportError:
        log.debug("transformers not importable")
        return None

    if not model_path.exists():
        log.debug("transformers model path missing: %s", model_path)
        return None

    try:
        asr = pipeline(
            task="automatic-speech-recognition",
            model=str(model_path),
            chunk_length_s=30,
            return_timestamps=False,
        )
    except Exception as exc:
        log.debug("transformers pipeline build failed: %s", exc)
        return None

    audio_np = np.asarray(samples, dtype=np.float32)
    if audio_np.max() > 1.0:
        audio_np = audio_np / 32768.0  # assume int16 range
    t0 = time.perf_counter()
    out = asr({"array": audio_np, "sampling_rate": sample_rate})
    latency = time.perf_counter() - t0
    duration = len(samples) / float(sample_rate)
    text = out["text"] if isinstance(out, dict) else str(out)
    return TranscribeResult(
        text=text.strip(),
        duration_s=round(duration, 3),
        latency_s=round(latency, 3),
        rtf=round(latency / max(duration, 1e-6), 3),
        backend="transformers",
    )


# ---------------------------------------------------------------------------
# Backend: deterministic mock (CI fallback)
# ---------------------------------------------------------------------------


def _transcribe_mock(samples, sample_rate: int) -> TranscribeResult:
    duration = len(samples) / float(sample_rate)
    # Produce a deterministic placeholder string derived from the audio length
    # so unit tests can assert end-to-end wiring without real models.
    return TranscribeResult(
        text="[mock] asr backend unavailable — install sherpa-onnx or transformers",
        duration_s=round(duration, 3),
        latency_s=0.0,
        rtf=0.0,
        backend="mock",
    )


# ---------------------------------------------------------------------------
# Audio I/O
# ---------------------------------------------------------------------------


def _load_audio(path: Path, target_sr: int = 16000):
    """Load any audio file into a float32 mono numpy array at ``target_sr``.

    Tries soundfile first (fast, no re-encoding), then librosa (resamples).
    """
    try:
        import soundfile as sf  # type: ignore

        samples, sr = sf.read(str(path), dtype="float32", always_2d=False)
    except Exception as exc:
        log.debug("soundfile load failed: %s", exc)
        try:
            import librosa  # type: ignore

            samples, sr = librosa.load(str(path), sr=target_sr, mono=True)
            return samples, target_sr
        except Exception as exc2:
            raise RuntimeError(f"cannot load audio {path}: {exc2}") from exc2

    if getattr(samples, "ndim", 1) > 1:
        samples = samples.mean(axis=1)
    if sr != target_sr:
        try:
            import librosa  # type: ignore

            samples = librosa.resample(samples, orig_sr=sr, target_sr=target_sr)
            sr = target_sr
        except ImportError as exc:
            raise RuntimeError(
                f"sample-rate mismatch ({sr} vs {target_sr}) and librosa is not installed"
            ) from exc
    return samples, sr


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class AsrTranscriber:
    """High-level transcriber with automatic backend selection."""

    def __init__(
        self,
        *,
        sherpa_dir: Path | None = None,
        hf_model_dir: Path | None = None,
        backend: str = "auto",
    ) -> None:
        self.sherpa_dir = Path(sherpa_dir) if sherpa_dir else DEFAULT_SHERPA_DIR
        self.hf_model_dir = Path(hf_model_dir) if hf_model_dir else DEFAULT_HF_CACHE
        self.backend = backend

    def transcribe_array(self, samples, sample_rate: int = 16000) -> TranscribeResult:
        order: Iterable[str]
        if self.backend == "auto":
            order = ("sherpa", "transformers", "mock")
        elif self.backend == "sherpa":
            order = ("sherpa",)
        elif self.backend == "transformers":
            order = ("transformers",)
        elif self.backend == "mock":
            order = ("mock",)
        else:
            raise ValueError(f"unknown backend: {self.backend}")

        for b in order:
            if b == "sherpa":
                r = _transcribe_sherpa(self.sherpa_dir, samples, sample_rate)
                if r is not None:
                    return r
            elif b == "transformers":
                r = _transcribe_transformers(self.hf_model_dir, samples, sample_rate)
                if r is not None:
                    return r
            elif b == "mock":
                return _transcribe_mock(samples, sample_rate)
        return _transcribe_mock(samples, sample_rate)

    def transcribe_file(self, audio_path: Path, target_sr: int = 16000) -> TranscribeResult:
        samples, sr = _load_audio(audio_path, target_sr=target_sr)
        return self.transcribe_array(samples, sample_rate=sr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--audio", type=Path, help="Input audio file (wav/flac/ogg/mp3)")
    parser.add_argument("--sherpa-dir", type=Path, default=None)
    parser.add_argument("--hf-model-dir", type=Path, default=None)
    parser.add_argument(
        "--backend", choices=("auto", "sherpa", "transformers", "mock"), default="auto"
    )
    parser.add_argument("--target-sr", type=int, default=16000)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )

    if args.audio is None:
        parser.print_help()
        return 2

    transcriber = AsrTranscriber(
        sherpa_dir=args.sherpa_dir,
        hf_model_dir=args.hf_model_dir,
        backend=args.backend,
    )
    try:
        result = transcriber.transcribe_file(args.audio, target_sr=args.target_sr)
    except Exception:
        log.exception("transcribe failed")
        return 1

    json.dump(result.to_dict(), sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

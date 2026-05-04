"""Accent detection for Ugandan English + Luganda (2026).

Lightweight accent classifier that runs on the first few seconds of
audio to select the optimal ASR adapter (LoRA) at inference time.

Architecture::

    audio (first 3-5s) ──▶ feature extraction ──▶ classifier ──▶ AccentResult
                                                                     │
                    SpeechModel.transcribe() ◄── adapter routing ◄───┘

The classifier uses prosodic features (pitch, energy contour, speaking
rate) rather than Whisper encoder embeddings to avoid loading heavy
models just for accent detection.  This keeps inference < 50ms.

Supported accent profiles:
  - ug_english_central  — Kampala / Central Uganda English
  - ug_english_eastern  — Eastern Uganda English
  - ug_english_western  — Western Uganda English
  - luganda_kampala     — Luganda (Kampala dialect)
  - code_switch_en_lg   — Mixed English-Luganda code-switching
  - generic_en          — Fallback general English

Feature-flagged behind ``voice_enabled``.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ._root import PROJECT_ROOT as _PROJECT_ROOT

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ACCENT_CONFIDENCE_THRESHOLD = float(os.getenv("ACCENT_CONFIDENCE_THRESHOLD", "0.7"))
ACCENT_SAMPLE_DURATION_S = float(os.getenv("ACCENT_SAMPLE_DURATION_S", "5.0"))
ACCENT_MODEL_DIR = Path(
    os.getenv(
        "ACCENT_MODEL_DIR",
        str(_PROJECT_ROOT / "artifacts" / "speech" / "accent"),
    )
)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AccentResult:
    """Result from accent detection."""

    label: str
    confidence: float
    latency_ms: float
    features: dict | None = None


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------


def _extract_prosodic_features(
    audio: np.ndarray,
    sample_rate: int = 16000,
) -> dict:
    """Extract prosodic features for accent classification.

    Returns a dict of features:
      - rms_mean, rms_std — energy statistics
      - zcr_mean, zcr_std — zero-crossing rate (correlates with fricatives)
      - spectral_centroid_mean — brightness
      - speaking_rate_proxy — syllable-like energy peaks per second
      - pitch_range_proxy — variance in spectral centroid (proxy for F0 range)
    """
    # Ensure float32
    if audio.dtype != np.float32:
        audio = audio.astype(np.float32) / 32768.0

    n = len(audio)
    if n == 0:
        return {}

    # Frame parameters
    frame_len = int(0.025 * sample_rate)  # 25ms frames
    hop_len = int(0.010 * sample_rate)  # 10ms hop

    # RMS energy per frame
    n_frames = max(1, (n - frame_len) // hop_len + 1)
    rms_values = []
    zcr_values = []
    centroid_values = []

    for i in range(n_frames):
        start = i * hop_len
        end = min(start + frame_len, n)
        frame = audio[start:end]

        if len(frame) < 2:
            continue

        # RMS
        rms = float(np.sqrt(np.mean(frame ** 2)))
        rms_values.append(rms)

        # Zero-crossing rate
        signs = np.sign(frame)
        zcr = float(np.sum(np.abs(np.diff(signs)) > 0)) / len(frame)
        zcr_values.append(zcr)

        # Spectral centroid (simplified)
        fft = np.abs(np.fft.rfft(frame))
        freqs = np.fft.rfftfreq(len(frame), d=1.0 / sample_rate)
        if fft.sum() > 0:
            centroid = float(np.sum(freqs * fft) / np.sum(fft))
        else:
            centroid = 0.0
        centroid_values.append(centroid)

    rms_arr = np.array(rms_values) if rms_values else np.array([0.0])
    zcr_arr = np.array(zcr_values) if zcr_values else np.array([0.0])
    centroid_arr = np.array(centroid_values) if centroid_values else np.array([0.0])

    # Speaking rate proxy: count energy peaks (syllable nuclei)
    duration_s = n / sample_rate
    if len(rms_arr) > 3:
        threshold = np.mean(rms_arr) * 0.6
        peaks = 0
        above = False
        for v in rms_arr:
            if v > threshold and not above:
                peaks += 1
                above = True
            elif v <= threshold:
                above = False
        speaking_rate = peaks / max(duration_s, 0.1)
    else:
        speaking_rate = 0.0

    return {
        "rms_mean": float(np.mean(rms_arr)),
        "rms_std": float(np.std(rms_arr)),
        "zcr_mean": float(np.mean(zcr_arr)),
        "zcr_std": float(np.std(zcr_arr)),
        "spectral_centroid_mean": float(np.mean(centroid_arr)),
        "pitch_range_proxy": float(np.std(centroid_arr)),
        "speaking_rate_proxy": speaking_rate,
        "duration_s": duration_s,
    }


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


class AccentDetector:
    """Lightweight accent classifier using prosodic features.

    If a trained model exists in ``ACCENT_MODEL_DIR``, loads it.
    Otherwise uses a heuristic rule-based classifier as a baseline.
    """

    def __init__(self, model_dir: Path | None = None) -> None:
        self._model_dir = model_dir or ACCENT_MODEL_DIR
        self._classifier = None
        self._ready = False

        if self._model_dir.exists() and (self._model_dir / "accent_model.pkl").exists():
            self._load_model()

    def _load_model(self) -> None:
        """Load a pre-trained sklearn classifier."""
        try:
            import pickle

            model_path = self._model_dir / "accent_model.pkl"
            with open(model_path, "rb") as f:
                self._classifier = pickle.load(f)
            self._ready = True
            logger.info("Accent classifier loaded from %s", model_path)
        except Exception:
            logger.warning("Failed to load accent classifier — using heuristic fallback")

    @property
    def is_ready(self) -> bool:
        return True  # Always ready — falls back to heuristics

    def detect(
        self,
        audio_bytes: bytes,
        sample_rate: int = 16000,
    ) -> AccentResult:
        """Detect accent from the first N seconds of audio.

        Args:
            audio_bytes: Raw PCM16 LE mono audio.
            sample_rate: Sample rate (default 16000).

        Returns:
            AccentResult with label, confidence, and latency.
        """
        t0 = time.perf_counter()

        # Trim to first N seconds
        max_samples = int(ACCENT_SAMPLE_DURATION_S * sample_rate)
        max_bytes = max_samples * 2  # 16-bit = 2 bytes per sample
        trimmed = audio_bytes[:max_bytes]

        # Convert to float32
        n_samples = len(trimmed) // 2
        if n_samples < sample_rate // 2:  # Less than 0.5s — not enough
            return AccentResult(
                label="generic_en",
                confidence=0.0,
                latency_ms=round((time.perf_counter() - t0) * 1000, 1),
            )

        audio = np.frombuffer(trimmed, dtype=np.int16).astype(np.float32) / 32768.0

        # Extract features
        features = _extract_prosodic_features(audio, sample_rate)

        if self._classifier is not None:
            result = self._classify_ml(features)
        else:
            result = self._classify_heuristic(features)

        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        return AccentResult(
            label=result[0],
            confidence=result[1],
            latency_ms=latency_ms,
            features=features,
        )

    def _classify_ml(self, features: dict) -> tuple[str, float]:
        """Classify using the trained ML model."""
        try:
            feature_vec = np.array([
                features.get("rms_mean", 0),
                features.get("rms_std", 0),
                features.get("zcr_mean", 0),
                features.get("zcr_std", 0),
                features.get("spectral_centroid_mean", 0),
                features.get("pitch_range_proxy", 0),
                features.get("speaking_rate_proxy", 0),
            ]).reshape(1, -1)

            label = self._classifier.predict(feature_vec)[0]
            proba = self._classifier.predict_proba(feature_vec)
            confidence = float(np.max(proba))
            return (label, confidence)
        except Exception:
            logger.debug("ML classification failed, using heuristic", exc_info=True)
            return self._classify_heuristic(features)

    def _classify_heuristic(self, features: dict) -> tuple[str, float]:
        """Rule-based accent classification using prosodic features.

        This is a baseline — should be replaced by a trained model
        once accent-labeled data is available.  The heuristics are based
        on observed patterns in Ugandan English vs. standard English:

        - Ugandan English tends to have a lower speaking rate
        - Luganda has more tonal variation (higher pitch range proxy)
        - Code-switching shows mixed features
        """
        speaking_rate = features.get("speaking_rate_proxy", 0)
        pitch_range = features.get("pitch_range_proxy", 0)
        zcr_mean = features.get("zcr_mean", 0)
        centroid = features.get("spectral_centroid_mean", 0)

        # High pitch variation + moderate speaking rate → likely Luganda
        if pitch_range > 800 and speaking_rate < 4.0:
            return ("luganda_kampala", 0.55)

        # Moderate pitch, low speaking rate → Ugandan English
        if speaking_rate < 3.5 and centroid < 2500:
            return ("ug_english_central", 0.50)

        # Higher speaking rate with variable pitch → code-switching
        if speaking_rate > 3.5 and pitch_range > 600:
            return ("code_switch_en_lg", 0.45)

        # Default
        return ("generic_en", 0.40)

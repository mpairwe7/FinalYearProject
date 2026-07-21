"""Audio codec utilities — PCM / Opus / WAV conversion (2026).

Provides bandwidth-efficient encoding for WebSocket audio transport
and format conversions between pipeline stages.

PCM16 LE mono 16 kHz is the canonical wire format (same as V1).
Opus encoding is available for bandwidth-constrained mobile clients
when ``VOICE_CODEC_OPUS=true`` is set.
"""

from __future__ import annotations

import io
import logging
import os
import struct
import wave
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

OPUS_ENABLED = os.getenv("VOICE_CODEC_OPUS", "false").lower() in ("1", "true", "yes")

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AudioFrame:
    """A single audio frame with metadata."""

    pcm: bytes  # PCM16 LE mono
    sample_rate: int
    channels: int = 1
    duration_ms: float = 0.0

    @property
    def num_samples(self) -> int:
        return len(self.pcm) // (2 * self.channels)


# ---------------------------------------------------------------------------
# PCM utilities
# ---------------------------------------------------------------------------


def pcm_to_wav(pcm: bytes, sample_rate: int = 16_000, channels: int = 1) -> bytes:
    """Wrap raw PCM16 LE bytes in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def wav_to_pcm(wav_bytes: bytes) -> tuple[bytes, int]:
    """Extract raw PCM16 LE and sample rate from a WAV file."""
    buf = io.BytesIO(wav_bytes)
    with wave.open(buf, "rb") as wf:
        pcm = wf.readframes(wf.getnframes())
        sr = wf.getframerate()
    return pcm, sr


def resample_pcm(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Resample PCM16 LE mono from *src_rate* to *dst_rate*."""
    if src_rate == dst_rate:
        return pcm
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    ratio = dst_rate / src_rate
    new_len = int(len(samples) * ratio)
    indices = np.linspace(0, len(samples) - 1, new_len)
    resampled = np.interp(indices, np.arange(len(samples)), samples)
    return np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()


def pcm_duration_ms(pcm: bytes, sample_rate: int = 16_000) -> float:
    """Calculate duration in milliseconds of PCM16 mono audio."""
    n_samples = len(pcm) // 2
    return (n_samples / sample_rate) * 1000


def generate_silence(duration_ms: float, sample_rate: int = 16_000) -> bytes:
    """Generate silent PCM16 LE mono audio."""
    n_samples = int(sample_rate * duration_ms / 1000)
    return b"\x00\x00" * n_samples


def pcm_rms_energy(pcm: bytes) -> float:
    """Compute RMS energy of PCM16 LE audio (0.0 = silence, 1.0 = max)."""
    if len(pcm) < 2:
        return 0.0
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(samples**2)))


def split_pcm_frames(pcm: bytes, frame_ms: float = 20.0, sample_rate: int = 16_000) -> list[bytes]:
    """Split PCM16 LE into fixed-duration frames for WebSocket streaming."""
    frame_bytes = int(sample_rate * frame_ms / 1000) * 2  # 2 bytes per sample
    frames = []
    for i in range(0, len(pcm), frame_bytes):
        frame = pcm[i : i + frame_bytes]
        if len(frame) == frame_bytes:
            frames.append(frame)
        elif frame:
            # Pad the last frame with silence
            frames.append(frame + b"\x00" * (frame_bytes - len(frame)))
    return frames


# ---------------------------------------------------------------------------
# Opus codec (optional, for bandwidth-constrained mobile)
# ---------------------------------------------------------------------------

_opus_encoder = None
_opus_decoder = None


def opus_available() -> bool:
    """Check if Opus codec is available and enabled."""
    if not OPUS_ENABLED:
        return False
    try:
        import opuslib  # type: ignore[import-untyped]

        return True
    except ImportError:
        return False


def opus_encode(pcm: bytes, sample_rate: int = 16_000, frame_ms: int = 20) -> list[bytes]:
    """Encode PCM16 LE to Opus frames.  Returns empty list if unavailable."""
    if not opus_available():
        return []
    try:
        import opuslib

        global _opus_encoder
        if _opus_encoder is None:
            _opus_encoder = opuslib.Encoder(sample_rate, 1, opuslib.APPLICATION_VOIP)

        frame_size = int(sample_rate * frame_ms / 1000)
        frame_bytes = frame_size * 2
        encoded: list[bytes] = []

        for i in range(0, len(pcm), frame_bytes):
            frame = pcm[i : i + frame_bytes]
            if len(frame) < frame_bytes:
                frame += b"\x00" * (frame_bytes - len(frame))
            encoded.append(_opus_encoder.encode(frame, frame_size))

        return encoded
    except Exception:
        logger.warning("Opus encoding failed", exc_info=True)
        return []


def opus_decode(opus_frames: list[bytes], sample_rate: int = 16_000, frame_ms: int = 20) -> bytes:
    """Decode Opus frames to PCM16 LE.  Returns empty bytes if unavailable."""
    if not opus_available():
        return b""
    try:
        import opuslib

        global _opus_decoder
        if _opus_decoder is None:
            _opus_decoder = opuslib.Decoder(sample_rate, 1)

        frame_size = int(sample_rate * frame_ms / 1000)
        pcm_parts: list[bytes] = []
        for frame in opus_frames:
            pcm_parts.append(_opus_decoder.decode(frame, frame_size))

        return b"".join(pcm_parts)
    except Exception:
        logger.warning("Opus decoding failed", exc_info=True)
        return b""

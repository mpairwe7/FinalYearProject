"""Streaming ASR — emit partial hypotheses for speculative prefetch (2026).

Wraps the existing ``SpeechModel.transcribe()`` in a sliding-window
interface that produces *partial* transcription results as audio arrives.
Stable prefixes (tokens confirmed across multiple consecutive windows)
are emitted early so downstream components can begin speculative
retrieval before the utterance completes.

This is NOT a native streaming ASR (CTC prefix beam search).  It is a
*simulation* that runs batch ASR on overlapping windows.  This is
practical because Whisper inference on a 2-second window takes ~150ms
on GPU — fast enough for a 500ms hop.

Feature flag: ``native_voice``
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..speech_service import SpeechModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WINDOW_S = float(os.getenv("STREAMING_ASR_WINDOW_S", "2.0"))
HOP_S = float(os.getenv("STREAMING_ASR_HOP_S", "0.5"))
STABILITY_COUNT = int(os.getenv("STREAMING_ASR_STABILITY_COUNT", "2"))

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class PartialHypothesis:
    """A partial ASR result, possibly incomplete."""

    text: str
    is_final: bool
    confidence: float
    language: str
    latency_ms: float
    stable_prefix: str  # confirmed tokens that won't change
    unstable_suffix: str  # may be revised by the next window

    @property
    def stable_token_count(self) -> int:
        return len(self.stable_prefix.split()) if self.stable_prefix else 0


# ---------------------------------------------------------------------------
# StreamingASR
# ---------------------------------------------------------------------------


class StreamingASR:
    """Sliding-window streaming ASR with token stability tracking.

    Strategy:
        Run ``SpeechModel.transcribe()`` on overlapping 2-second windows
        every 500ms of new audio.  Track which token positions produce
        the same token across ``STABILITY_COUNT`` consecutive windows.
        The contiguous run of stable tokens from position 0 forms the
        *stable prefix* that can be used for speculative prefetch.

    Args:
        speech_model: The existing ``SpeechModel`` instance.
        window_s: ASR window duration in seconds.
        hop_s: Interval between ASR runs in seconds.
        stability_count: How many consecutive windows must agree for
            a token to be considered stable.
        sample_rate: Audio sample rate (16 kHz default).
    """

    def __init__(
        self,
        speech_model: SpeechModel,
        *,
        window_s: float = WINDOW_S,
        hop_s: float = HOP_S,
        stability_count: int = STABILITY_COUNT,
        sample_rate: int = 16_000,
    ) -> None:
        self._speech = speech_model
        self._window_bytes = int(window_s * sample_rate) * 2  # PCM16 = 2 bytes/sample
        self._hop_bytes = int(hop_s * sample_rate) * 2
        self._stability_count = stability_count
        self._sample_rate = sample_rate

        # Accumulated audio
        self._buffer = bytearray()
        self._bytes_since_last_run = 0

        # Token stability tracking
        self._token_history: dict[int, dict[str, int]] = {}
        self._stable_tokens: list[str] = []
        self._last_full_text: str = ""
        self._last_language: str = "en"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def feed_chunk(self, pcm_bytes: bytes) -> PartialHypothesis | None:
        """Feed a PCM16 LE audio chunk and optionally emit a partial hypothesis.

        Returns ``None`` if not enough audio has accumulated since the
        last run (haven't reached the hop interval yet).
        """
        self._buffer.extend(pcm_bytes)
        self._bytes_since_last_run += len(pcm_bytes)

        # Only run ASR every hop interval
        if self._bytes_since_last_run < self._hop_bytes:
            return None

        # Not enough audio for a full window yet
        if len(self._buffer) < self._window_bytes:
            return None

        self._bytes_since_last_run = 0

        # Take the last window of audio
        window = bytes(self._buffer[-self._window_bytes:])

        t0 = time.perf_counter()
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                self._speech.transcribe,
                window,
                self._sample_rate,
                None,  # auto-detect language
            )
        except Exception:
            logger.warning("Streaming ASR window failed", exc_info=True)
            return None

        latency_ms = (time.perf_counter() - t0) * 1000

        if result.error or not result.text.strip():
            return None

        self._last_full_text = result.text.strip()
        self._last_language = result.language or "en"

        tokens = self._last_full_text.split()
        new_stable = self._update_stability(tokens)

        return PartialHypothesis(
            text=self._last_full_text,
            is_final=False,
            confidence=0.75,  # partial — will be replaced by final
            language=self._last_language,
            latency_ms=round(latency_ms, 1),
            stable_prefix=" ".join(new_stable),
            unstable_suffix=" ".join(tokens[len(new_stable):]),
        )

    def get_last_partial(self) -> PartialHypothesis | None:
        """Return the most recent partial hypothesis without feeding new audio."""
        if not self._last_full_text:
            return None
        return PartialHypothesis(
            text=self._last_full_text,
            is_final=False,
            confidence=0.75,
            language=self._last_language,
            latency_ms=0.0,
            stable_prefix=" ".join(self._stable_tokens),
            unstable_suffix="",
        )

    def finalize(self) -> None:
        """Reset all state for the next utterance."""
        self._buffer.clear()
        self._bytes_since_last_run = 0
        self._token_history.clear()
        self._stable_tokens.clear()
        self._last_full_text = ""
        self._last_language = "en"

    # ------------------------------------------------------------------
    # Stability tracking
    # ------------------------------------------------------------------

    def _update_stability(self, tokens: list[str]) -> list[str]:
        """Update token position history and return the stable prefix.

        A token at position *i* is stable when it has appeared at that
        position in at least ``_stability_count`` consecutive windows.
        Stability is *contiguous from position 0* — the first unstable
        position breaks the prefix.
        """
        for i, tok in enumerate(tokens):
            if i not in self._token_history:
                self._token_history[i] = {}
            key = tok.lower()
            self._token_history[i][key] = self._token_history[i].get(key, 0) + 1

        stable: list[str] = []
        for i in range(len(tokens)):
            counts = self._token_history.get(i, {})
            key = tokens[i].lower()
            if counts.get(key, 0) >= self._stability_count:
                stable.append(tokens[i])
            else:
                break  # contiguous from start

        self._stable_tokens = stable
        return stable

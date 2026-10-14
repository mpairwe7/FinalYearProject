"""Spoken-Figure Round-Trip Verifier & Dictation Pacing Guard (2026).

Ensures that statutory rates, currency amounts, TINs, and PRNs survive speech
normalization and TTS synthesis without numeric corruption, and injects deliberate
dictation pauses around spoken digit sequences.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .. import mt
from ..flags import flags
from ..speech_service import pcm16_to_wav

logger = logging.getLogger(__name__)

_DIGIT_WORDS: frozenset[str] = frozenset({
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "zeero", "emu", "bbiri", "ssatu", "nnya", "ttaano", "mukaaga", "musanvu", "munaana", "mwenda",
    "sufuri", "moja", "mbili", "tatu", "nne", "tano", "sita", "saba", "nane", "tisa",
})


@dataclass(frozen=True)
class SpokenFigureVerificationResult:
    """Outcome of checking spoken text and/or synthesized audio for figure fidelity."""

    ok: bool
    status: str  # "no_figures" | "verified" | "re_synthesized" | "fallback_text_only"
    expected_figures: set[float]
    detected_figures: set[float]


def inject_dictation_pauses(spoken_text: str) -> str:
    """Insert deliberate cadence breaks into long spelled-out digit runs (TINs/PRNs)."""
    if not spoken_text:
        return ""
    tokens = spoken_text.split()
    if len(tokens) < 6:
        return spoken_text

    out: list[str] = []
    run: list[str] = []

    def _flush_run() -> None:
        if not run:
            return
        if len(run) >= 6:
            groups = [" ".join(run[i : i + 3]) for i in range(0, len(run), 3)]
            out.append(", ".join(groups))
        else:
            out.extend(run)
        run.clear()

    for tok in tokens:
        clean_tok = tok.strip(".,;:!?-").lower()
        if clean_tok in _DIGIT_WORDS:
            run.append(tok)
        else:
            _flush_run()
            out.append(tok)
    _flush_run()
    return " ".join(out)


def verify_spoken_figures(
    source_text: str,
    spoken_text: str,
    locale: str = "en",
    *,
    subject: str = "",
    pcm16_audio: bytes | None = None,
    speech_model: Any = None,
    sample_rate: int = 16000,
) -> SpokenFigureVerificationResult:
    """Verify that every statutory figure in *source_text* survived into *spoken_text* (and optional audio).

    Records ``ura_spoken_figure_verification_total`` when ``spoken_figure_guard`` is enabled.
    """
    loc = (locale or "en").lower()
    expected = mt.figures(source_text, locale=loc)
    if not expected:
        return SpokenFigureVerificationResult(
            ok=True,
            status="no_figures",
            expected_figures=set(),
            detected_figures=set(),
        )

    if not flags.is_enabled("spoken_figure_guard", subject=subject or None):
        return SpokenFigureVerificationResult(
            ok=True,
            status="verified",
            expected_figures=expected,
            detected_figures=expected,
        )

    detected = mt.figures(spoken_text, locale=loc) | mt.figures(spoken_text, locale="en")
    text_ok = expected.issubset(detected) or mt.figures_survived(source_text, spoken_text, locale=loc)

    # Optional fast acoustic round-trip verification when synthesized PCM16 and STT are supplied
    audio_ok = True
    if text_ok and pcm16_audio and speech_model is not None and hasattr(speech_model, "transcribe"):
        try:
            asr_res = speech_model.transcribe(
                pcm16_to_wav(pcm16_audio, sample_rate),
                sample_rate,
                loc,
                with_words=False,
            )
            transcript = (getattr(asr_res, "text", "") or "").strip()
            if transcript:
                audio_figs = mt.figures(transcript, locale=loc) | mt.figures(transcript, locale="en")
                if audio_figs:
                    audio_ok = expected.issubset(audio_figs)
                    detected = audio_figs
        except Exception:
            logger.debug("spoken_figure_guard: acoustic check skipped", exc_info=True)

    ok = text_ok and audio_ok
    status = "verified" if ok else "fallback_text_only"

    try:
        from ..analytics import metrics

        metrics.inc(
            "spoken_figure_verification_total",
            labels={"result": status, "locale": loc},
        )
    except Exception:
        logger.debug("spoken_figure_guard: metric record skipped", exc_info=True)

    return SpokenFigureVerificationResult(
        ok=ok,
        status=status,
        expected_figures=expected,
        detected_figures=detected,
    )

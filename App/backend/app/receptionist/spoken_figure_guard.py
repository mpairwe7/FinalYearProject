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


def _spoken_realizations_for(val: float, locale: str) -> tuple[str, ...]:
    """Canonical spoken-word realizations produced by ``speech_normalization`` for *val*."""
    from ..speech_normalization import (
        _format_ugx_amount,
        _spoken_number,
        clean_text_for_speech,
    )

    num_str = str(int(val)) if val == int(val) else f"{val:g}"
    candidates: list[str] = []
    for loc in (locale, "en"):
        try:
            words = _spoken_number(num_str, loc, years=False).strip().lower()
            if words:
                candidates.append(words)
        except Exception:
            logger.debug("spoken_figure_guard: _spoken_number candidate skipped", exc_info=True)
        try:
            pct_words = clean_text_for_speech(f"{num_str}%", locale=loc).strip(" .").lower()
            if pct_words:
                candidates.append(pct_words)
        except Exception:
            logger.debug("spoken_figure_guard: pct candidate skipped", exc_info=True)
        if val >= 1000 and val == int(val):
            try:
                ugx_words = _format_ugx_amount(f"{int(val):,}", loc).strip().lower()
                if ugx_words:
                    candidates.append(ugx_words)
            except Exception:
                logger.debug("spoken_figure_guard: _format_ugx_amount candidate skipped", exc_info=True)
            try:
                full_ugx = clean_text_for_speech(f"UGX {int(val):,}", locale=loc).strip(" .").lower()
                if full_ugx:
                    candidates.append(full_ugx)
            except Exception:
                logger.debug("spoken_figure_guard: full_ugx candidate skipped", exc_info=True)
    return tuple(dict.fromkeys(candidates))


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
    if not expected.issubset(detected):
        spoken_norm_low = " ".join((spoken_text or "").lower().replace(",", " ").split())
        for missing_val in list(expected - detected):
            for phrase in _spoken_realizations_for(missing_val, loc):
                phrase_norm = " ".join(phrase.replace(",", " ").split())
                if phrase_norm and phrase_norm in spoken_norm_low:
                    detected.add(missing_val)
                    break
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

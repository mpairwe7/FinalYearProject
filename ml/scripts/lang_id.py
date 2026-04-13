"""Language identification wrapper (2026).

Thin wrapper around ``lingua-py`` (Apache-2.0) used by:

    * ``ml/scripts/mt/infer_mt.py``            — auto-routes En<->Lg
    * ``App/backend/app/speech_service.py``    — post-ASR language dispatch
    * ``ml/pipelines/evaluate_mt.py``          — eval-time lang sanity check

Design notes:

* No heavy model is loaded at import time; the detector is lazily
  constructed on first call so importing this module has zero cost when
  lang-ID is not used.
* If ``lingua`` is not installed the module gracefully degrades to a
  coarse character-script heuristic so the rest of the pipeline still
  runs on dev machines.
* Luganda (``lg`` / ``lug``) maps to ``lingua.Language.GANDA``. Swahili
  is included because Uganda speech frequently contains Swahili tokens
  which we want to *not* mis-route as English.

Usage::

    from ml.scripts.lang_id import LanguageDetector

    det = LanguageDetector()
    result = det.detect("Njagala okumanya ensimbi z'omusolo")
    #   LanguageResult(lang='lg', confidence=0.93, backend='lingua')

Commercial: lingua-py is Apache-2.0. The fallback heuristic is project-
internal. No CC-BY-NC dependencies.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class LanguageResult:
    lang: str  # ISO 639-1 code (en, lg, sw, unknown)
    confidence: float  # 0..1
    backend: str  # "lingua" | "heuristic"

    def is_confident(self, threshold: float = 0.60) -> bool:
        return self.confidence >= threshold


# ---------------------------------------------------------------------------
# Fallback heuristic (used when lingua is unavailable)
# ---------------------------------------------------------------------------
#
# Neither English nor Luganda use a unique script, so we lean on:
#   - ASCII ratio     (both use Latin script; lingua is much better)
#   - Luganda digraphs ('ny', 'mw', 'kw', 'ww') — very frequent in Luganda,
#     rare in English
#   - common English stopwords — very strong signal
#
# This is a last-resort signal. lingua is strongly preferred.

_LG_DIGRAPHS = re.compile(r"(nny|nnk|nnyw|kw|mw|mpw|ww|bw|ggw|gy|ffy)", re.IGNORECASE)
_EN_STOPWORDS = {
    "the",
    "and",
    "of",
    "to",
    "in",
    "is",
    "are",
    "was",
    "were",
    "be",
    "for",
    "on",
    "at",
    "with",
    "this",
    "that",
    "you",
    "your",
    "we",
    "have",
    "has",
    "had",
    "will",
    "can",
    "not",
    "but",
    "from",
    "it",
}
_WORD_RE = re.compile(r"[A-Za-z']+")


def _heuristic_detect(text: str) -> LanguageResult:
    if not text or not text.strip():
        return LanguageResult(lang="unknown", confidence=0.0, backend="heuristic")

    words = _WORD_RE.findall(text.lower())
    if not words:
        return LanguageResult(lang="unknown", confidence=0.0, backend="heuristic")

    en_hits = sum(1 for w in words if w in _EN_STOPWORDS)
    en_ratio = en_hits / len(words)

    lg_digraph_hits = len(_LG_DIGRAPHS.findall(text))
    lg_ratio = lg_digraph_hits / max(len(words), 1)

    # Decision: strong English stopword presence wins. Otherwise Luganda
    # digraphs. Both low => unknown.
    if en_ratio >= 0.15:
        return LanguageResult(lang="en", confidence=min(0.5 + en_ratio, 0.85), backend="heuristic")
    if lg_ratio >= 0.10:
        return LanguageResult(lang="lg", confidence=min(0.5 + lg_ratio, 0.85), backend="heuristic")
    # Default to English (most content is English in this project)
    return LanguageResult(lang="en", confidence=0.4, backend="heuristic")


# ---------------------------------------------------------------------------
# lingua-py backend (preferred)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _build_lingua_detector():
    """Lazily construct the lingua detector with our language set."""
    try:
        from lingua import Language, LanguageDetectorBuilder  # type: ignore
    except ImportError:
        return None

    languages = [
        Language.ENGLISH,
        Language.GANDA,  # Luganda
        Language.SWAHILI,  # common in Uganda code-switching
    ]
    return (
        LanguageDetectorBuilder.from_languages(*languages).with_preloaded_language_models().build()
    )


_LINGUA_TO_ISO = {
    "ENGLISH": "en",
    "GANDA": "lg",
    "SWAHILI": "sw",
}


def _lingua_detect(text: str) -> LanguageResult | None:
    det = _build_lingua_detector()
    if det is None:
        return None
    try:
        results = det.compute_language_confidence_values(text)
    except Exception as exc:
        log.debug("lingua failed: %s", exc)
        return None
    if not results:
        return None
    top = results[0]
    iso = _LINGUA_TO_ISO.get(top.language.name, "unknown")
    return LanguageResult(lang=iso, confidence=float(top.value), backend="lingua")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class LanguageDetector:
    """Drop-in language detector. Tries lingua, falls back to heuristic."""

    def __init__(self, min_confidence: float = 0.60, default_lang: str = "en") -> None:
        self.min_confidence = min_confidence
        self.default_lang = default_lang
        # Probe lingua at init time so callers get a fast log line on startup.
        if _build_lingua_detector() is None:
            log.warning(
                "lingua-py not installed; language-ID will use character heuristic. "
                "Install with: pip install lingua-language-detector"
            )

    def detect(self, text: str) -> LanguageResult:
        """Return the most likely language for ``text``.

        Never raises: on any error a heuristic result is returned.
        """
        if not text or not text.strip():
            return LanguageResult(lang=self.default_lang, confidence=0.0, backend="heuristic")
        result = _lingua_detect(text)
        if result is None:
            result = _heuristic_detect(text)
        if not result.is_confident(self.min_confidence):
            return LanguageResult(
                lang=self.default_lang,
                confidence=result.confidence,
                backend=result.backend,
            )
        return result

    def detect_many(self, texts: list[str]) -> list[LanguageResult]:
        return [self.detect(t) for t in texts]

    def detect_code_switching(
        self, text: str, segment_chars: int = 80
    ) -> list[tuple[str, str, float]]:
        """Segment text into code-switching spans.

        Returns a list of ``(span, lang, confidence)`` tuples. Uses
        whitespace-aware chunking so we don't split words. Coarse but good
        enough for token-routing to the MT system.
        """
        if not text or not text.strip():
            return []
        spans: list[tuple[str, str, float]] = []
        current: list[str] = []
        current_len = 0
        for word in text.split():
            current.append(word)
            current_len += len(word) + 1
            if current_len >= segment_chars:
                chunk = " ".join(current)
                res = self.detect(chunk)
                spans.append((chunk, res.lang, res.confidence))
                current = []
                current_len = 0
        if current:
            chunk = " ".join(current)
            res = self.detect(chunk)
            spans.append((chunk, res.lang, res.confidence))
        return spans


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> int:
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(description="Language detector (lingua + heuristic fallback)")
    parser.add_argument("text", nargs="?", help="Text to detect. If omitted, read from stdin.")
    parser.add_argument("--segment", action="store_true", help="Emit code-switching spans")
    parser.add_argument("--min-confidence", type=float, default=0.60)
    parser.add_argument("--default-lang", default="en")
    args = parser.parse_args()

    text = args.text or sys.stdin.read()
    det = LanguageDetector(min_confidence=args.min_confidence, default_lang=args.default_lang)
    if args.segment:
        spans = det.detect_code_switching(text)
        json.dump([{"span": s, "lang": l, "conf": c} for s, l, c in spans], sys.stdout, indent=2)
    else:
        result = det.detect(text)
        json.dump(
            {"lang": result.lang, "confidence": result.confidence, "backend": result.backend},
            sys.stdout,
            indent=2,
        )
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())

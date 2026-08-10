"""Language detection — lingua-py with heuristic fallback."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class LanguageResult:
    lang: str
    confidence: float
    backend: str

    def is_confident(self, threshold: float = 0.6) -> bool:
        return self.confidence >= threshold


class LanguageDetector:
    """Detect language of text input (EN, LG, SW)."""

    def __init__(self, min_confidence: float = 0.6, default_lang: str = "en"):
        self._detector = None
        self._lang_map = {}
        self.min_confidence = min_confidence
        self.default_lang = default_lang
        try:
            from lingua import Language, LanguageDetectorBuilder

            self._detector = (
                LanguageDetectorBuilder.from_languages(
                    Language.ENGLISH, Language.GANDA, Language.SWAHILI
                )
                .with_preloaded_language_models()
                .build()
            )
            self._lang_map = {
                Language.ENGLISH: "en",
                Language.GANDA: "lg",
                Language.SWAHILI: "sw",
            }
            logger.info("LanguageDetector: lingua backend ready")
        except ImportError:
            logger.info(
                "lingua-py not installed; language-ID will use character heuristic. "
                "Install with: pip install lingua-language-detector"
            )

    def detect(self, text: str) -> LanguageResult:
        if not text or not text.strip():
            return LanguageResult(lang=self.default_lang, confidence=0.0, backend="heuristic")

        if self._detector is not None:
            try:
                confidences = self._detector.compute_language_confidence_values(text)
                if confidences:
                    best = confidences[0]
                    lang = self._lang_map.get(best.language, "en")
                    result = LanguageResult(lang=lang, confidence=best.value, backend="lingua")
                    if result.is_confident(self.min_confidence):
                        return result
            except Exception:
                pass

        # Heuristic fallback — Luganda digraphs
        lg_patterns = re.compile(r"(nny|mw|kw|ww|bw|gy|ny|ng|gg|dd|ss|tt|nk|mp|mb|nd|nz)")
        en_stops = {"the", "is", "are", "was", "and", "for", "that", "this", "with", "from"}
        sw_stops = {
            "ninaweza",
            "ninawezaje",
            "nifanye",
            "nini",
            "kwa",
            "kupata",
            "biashara",
            "kodi",
            "jinsi",
            "vipi",
        }
        words = set(text.lower().split())
        lg_score = len(lg_patterns.findall(text.lower()))
        en_score = len(words & en_stops)
        sw_score = len(words & sw_stops)
        if sw_score >= 2:
            return LanguageResult(lang="sw", confidence=0.65, backend="heuristic")
        if lg_score > en_score:
            return LanguageResult(lang="lg", confidence=0.6, backend="heuristic")
        return LanguageResult(lang=self.default_lang, confidence=0.6, backend="heuristic")

    def detect_code_switching(self, text: str) -> list[tuple[str, str, float]]:
        """Detect per-span language (simplified: whole-text only)."""
        result = self.detect(text)
        return [(text, result.lang, result.confidence)]

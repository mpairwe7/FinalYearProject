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

        # Heuristic fallback — Lexical matching for Luganda, Swahili, and English
        en_stops = {
            "the", "is", "are", "was", "were", "and", "for", "that", "this", "with",
            "from", "what", "how", "do", "does", "did", "i", "you", "your", "my",
            "we", "our", "to", "in", "on", "at", "by", "of", "a", "an", "can",
            "could", "will", "would", "should", "must", "have", "has", "had", "pay",
            "tax", "taxes", "rate", "rates", "who", "when", "where", "why", "which",
            "apply", "register", "registration", "penalty", "income", "file", "filing",
            "return", "returns", "get", "need", "want", "help", "please", "or", "if",
            "not", "no", "yes", "about", "there", "here", "any", "some", "all", "vat", "tin",
        }
        lg_words = {
            "omusolo", "emisolo", "ebitundu", "ssente", "sente", "alipoota", "abakozi",
            "omukozi", "basasula", "okusasula", "sasula", "bwe", "era", "kye", "bye",
            "kampuni", "okwewandiisa", "wandiisa", "musanyufu", "ebisaanyizo", "enkola",
            "omusaala", "abakozesa", "ekitongole", "gyebaleko", "webale", "yee", "nedda",
            "nsaba", "olina", "okukola", "kola", "ki", "ani", "lwaki", "ddi", "bangi",
            "buli", "wa", "ku", "mu", "nga", "naye", "singa", "oba", "nze", "ffe", "gwe",
            "ntya", "nkola", "gwa", "gya", "bbeeyi",
        }
        sw_stops = {
            "ninaweza", "ninawezaje", "nifanye", "nini", "kwa", "kupata", "biashara",
            "kodi", "jinsi", "vipi", "ushuru", "kujisajili", "asilimia", "thamani",
            "marejesho", "huduma", "wafanyakazi", "mapato", "nchini", "binafsi",
            "habari", "asante", "shukrani", "kiasi", "gani", "kulipa", "zaidi",
        }
        words = set(re.findall(r"[a-z']+", text.lower()))
        en_score = len(words & en_stops)
        sw_score = len(words & sw_stops)
        lg_score = len(words & lg_words)

        if sw_score >= 2 or (sw_score >= 1 and en_score == 0):
            return LanguageResult(lang="sw", confidence=0.7, backend="heuristic")
        if lg_score >= 2 or (lg_score >= 1 and en_score == 0):
            return LanguageResult(lang="lg", confidence=0.7, backend="heuristic")
        if en_score > 0:
            return LanguageResult(lang="en", confidence=0.8, backend="heuristic")
        return LanguageResult(lang=self.default_lang, confidence=0.6, backend="heuristic")

    def detect_code_switching(self, text: str) -> list[tuple[str, str, float]]:
        """Detect per-span language (simplified: whole-text only)."""
        result = self.detect(text)
        return [(text, result.lang, result.confidence)]

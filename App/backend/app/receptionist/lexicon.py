"""URA term lexicon and candidate lookup for phone receptionist clarification."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Final

import jellyfish

from ..query import _ABBREVIATIONS, _TAX_DOMAIN_VOCAB, _damerau_levenshtein

logger = logging.getLogger(__name__)

# Fallback path for multi-word lexicon definitions
_LEXICON_PATH = Path(__file__).resolve().parents[4] / "data" / "receptionist" / "lexicon_en.txt"
_ALT_LEXICON_PATH = Path(__file__).resolve().parents[4] / "Data" / "receptionist" / "lexicon_en.txt"


class ReceptionistLexicon:
    """Tax domain vocabulary and phonetic index for speech clarification."""

    def __init__(self) -> None:
        self.single_terms: set[str] = set()
        self.multi_terms: set[str] = set()
        self.term_metaphones: dict[str, str] = {}
        self._load_sources()

    def _load_sources(self) -> None:
        # 1. Abbreviations and expansions from query.py
        for abbr, full in _ABBREVIATIONS.items():
            self.single_terms.add(abbr.lower())
            # Add words from full expansion
            for piece in re.findall(r"[A-Za-z]+", full):
                if len(piece) > 2:
                    self.single_terms.add(piece.lower())

        # 2. Tax domain vocab from query.py
        for term in _TAX_DOMAIN_VOCAB:
            self.single_terms.add(term.lower())

        # 3. Statutory glossary terms
        try:
            from ..glossary import URA_STATUTORY_GLOSSARY

            for term in URA_STATUTORY_GLOSSARY:
                term_clean = term.lower().strip()
                if " " in term_clean:
                    self.multi_terms.add(term_clean)
                    for part in term_clean.split():
                        if len(part) > 2:
                            self.single_terms.add(part)
                else:
                    self.single_terms.add(term_clean)
        except Exception:
            logger.debug("Could not import statutory glossary", exc_info=True)

        # 4. Spoken acronyms
        try:
            from ..speech_normalization import _ACRONYMS

            for pattern, spoken in _ACRONYMS:
                raw = pattern.pattern.replace(r"\b", "").strip()
                if raw.isalnum():
                    self.single_terms.add(raw.lower())
                spoken_clean = spoken.replace("-", "").lower().strip()
                if " " in spoken_clean:
                    self.multi_terms.add(spoken_clean)
                elif len(spoken_clean) > 2:
                    self.single_terms.add(spoken_clean)
        except Exception:
            logger.debug("Could not import speech normalization acronyms", exc_info=True)

        # 5. Curated multi-word file
        for path in (_LEXICON_PATH, _ALT_LEXICON_PATH):
            if path.is_file():
                try:
                    for line in path.read_text(encoding="utf-8").splitlines():
                        line = line.strip().lower()
                        if not line or line.startswith("#"):
                            continue
                        if " " in line:
                            self.multi_terms.add(line)
                            for piece in line.split():
                                if len(piece) > 2:
                                    self.single_terms.add(piece)
                        else:
                            self.single_terms.add(line)
                except Exception:
                    logger.debug("Failed reading lexicon file at %s", path, exc_info=True)

        # Build Metaphone table for single terms
        for term in self.single_terms:
            try:
                self.term_metaphones[term] = jellyfish.metaphone(term)
            except Exception:
                pass

    def candidates(self, word: str, context: str | None = None) -> list[tuple[str, float]]:
        """Find candidate term corrections for a poorly recognized word.

        Combines Damerau-Levenshtein distance and Metaphone phonetic similarity.
        Boosts candidate score if context + candidate forms a known multi-word term.
        """
        w = word.lower().strip(",.?!;:\"'()")
        if not w or len(w) < 2:
            return []

        w_no_dash = w.replace("-", "")
        w_meta = ""
        w_nodash_meta = ""
        try:
            w_meta = jellyfish.metaphone(w)
            w_nodash_meta = jellyfish.metaphone(w_no_dash)
        except Exception:
            pass

        ctx_words: list[str] = []
        if context:
            ctx_words = [p.lower().strip(",.?!;:\"'()") for p in context.split() if p.strip()]

        matches: list[tuple[str, float]] = []

        for term in self.single_terms:
            t_len = len(term)
            w_len = len(w)

            # Skip wildly different lengths
            if abs(w_len - t_len) > 4 and abs(len(w_no_dash) - t_len) > 4:
                continue

            # 1. Damerau-Levenshtein edit distance
            dist1 = _damerau_levenshtein(w, term)
            dist2 = _damerau_levenshtein(w_no_dash, term)
            dist = min(dist1, dist2)
            max_len = max(w_len, t_len, 1)

            # Normalized edit distance similarity [0..1]
            edit_sim = max(0.0, 1.0 - (dist / max_len))

            # 2. Metaphone phonetic match
            t_meta = self.term_metaphones.get(term, "")
            phonetic_sim = 0.0
            if t_meta:
                if w_meta == t_meta or w_nodash_meta == t_meta:
                    phonetic_sim = 1.0
                else:
                    meta_dist1 = _damerau_levenshtein(w_meta, t_meta) if w_meta else 99
                    meta_dist2 = _damerau_levenshtein(w_nodash_meta, t_meta) if w_nodash_meta else 99
                    meta_dist = min(meta_dist1, meta_dist2)
                    max_meta_len = max(len(w_meta), len(t_meta), 1)
                    phonetic_sim = max(0.0, 1.0 - (meta_dist / max_meta_len))

            # Base score: weighted combination of edit distance and phonetics
            score = 0.5 * edit_sim + 0.5 * phonetic_sim

            # 3. Context multi-word term bonus
            multi_boost = 0.0
            if ctx_words:
                prev_word = ctx_words[-1]
                bigram_before = f"{prev_word} {term}"
                if bigram_before in self.multi_terms:
                    multi_boost = 0.25

                if len(ctx_words) >= 2:
                    trigram_before = f"{ctx_words[-2]} {prev_word} {term}"
                    if trigram_before in self.multi_terms:
                        multi_boost = max(multi_boost, 0.3)

            total_score = min(1.0, score + multi_boost)
            if total_score >= 0.5:
                matches.append((term, round(total_score, 3)))

        # Sort descending by score
        matches.sort(key=lambda x: x[1], reverse=True)
        return matches


# Global singleton
_LEXICON_INSTANCE: ReceptionistLexicon | None = None


def get_lexicon() -> ReceptionistLexicon:
    global _LEXICON_INSTANCE
    if _LEXICON_INSTANCE is None:
        _LEXICON_INSTANCE = ReceptionistLexicon()
    return _LEXICON_INSTANCE


def candidates(word: str, context: str | None = None) -> list[tuple[str, float]]:
    """Lookup candidate terms for *word* given surrounding *context*."""
    return get_lexicon().candidates(word, context=context)

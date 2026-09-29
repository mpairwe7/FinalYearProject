"""URA term lexicon and candidate lookup for phone receptionist clarification."""

from __future__ import annotations

import logging
import re
from typing import Final

try:
    import jellyfish
except ImportError:
    jellyfish = None  # type: ignore[assignment]

from .._root import PROJECT_ROOT
from ..query import _ABBREVIATIONS, _TAX_DOMAIN_VOCAB, _damerau_levenshtein

logger = logging.getLogger(__name__)

# Fallback path for multi-word lexicon definitions
_LEXICON_PATH = PROJECT_ROOT / "Data" / "receptionist" / "lexicon_en.txt"
_ALT_LEXICON_PATH = PROJECT_ROOT / "data" / "receptionist" / "lexicon_en.txt"

# An acronym as Luganda or Swahili speakers say it: every word ends in a
# vowel and long vowels are written double, so "VAT" is heard as "vati" and
# "TIN" as "tiini". Undoing that and landing exactly on an acronym beats a
# look-alike that happens to be a letter closer ("vati" is VAT, not VATA).
_LOANWORD_SCORE: Final = 0.95
_VOWELS: Final = frozenset("aeiou")


def _squeeze(word: str) -> str:
    """Collapse doubled letters: "tiini" → "tini"."""
    return re.sub(r"(.)\1+", r"\1", word)


def _loanword_stem(word: str) -> str:
    """*word* without Bantu epenthesis: doubled letters collapsed, final vowel dropped."""
    stem = _squeeze(word)
    if len(stem) > 2 and stem[-1] in _VOWELS and stem[-2] not in _VOWELS:
        stem = stem[:-1]
    return stem


class ReceptionistLexicon:
    """Tax domain vocabulary and phonetic index for speech clarification."""

    def __init__(self) -> None:
        self.single_terms: set[str] = set()
        self.multi_terms: set[str] = set()
        # The English loanwords a Luganda or Swahili sentence keeps as they are
        # ("Nsaba okumanya ku TIN yange") — the only terms worth confirming in
        # a call that is not in English. See ClarifyGate.assess.
        self.acronyms: set[str] = set()
        self.term_metaphones: dict[str, str] = {}
        self._load_sources()

    def _load_sources(self) -> None:
        # 1. Abbreviations and expansions from query.py
        for abbr, full in _ABBREVIATIONS.items():
            self.single_terms.add(abbr.lower())
            self.acronyms.add(abbr.lower())
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
                    self.acronyms.add(raw.lower())
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
        if jellyfish is not None:
            for term in self.single_terms:
                try:
                    self.term_metaphones[term] = jellyfish.metaphone(term)
                except Exception:
                    pass

    def candidates(
        self, word: str, context: str | None = None, acronyms_only: bool = False
    ) -> list[tuple[str, float]]:
        """Find candidate term corrections for a poorly recognized word.

        Combines Damerau-Levenshtein distance and Metaphone phonetic similarity.
        Boosts candidate score if context + candidate forms a known multi-word term.
        ``acronyms_only`` limits the search to :attr:`acronyms`.
        """
        w = word.lower().strip(",.?!;:\"'()")
        if not w or len(w) < 2:
            return []

        w_no_dash = w.replace("-", "")
        w_meta = ""
        w_nodash_meta = ""
        if jellyfish is not None:
            try:
                w_meta = jellyfish.metaphone(w)
                w_nodash_meta = jellyfish.metaphone(w_no_dash)
            except Exception:
                pass

        loan_stem = _loanword_stem(w_no_dash)

        ctx_words: list[str] = []
        if context:
            ctx_words = [p.lower().strip(",.?!;:\"'()") for p in context.split() if p.strip()]

        matches: list[tuple[str, float]] = []

        for term in self.acronyms if acronyms_only else self.single_terms:
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
            if acronyms_only and loan_stem != w_no_dash and loan_stem == _squeeze(term):
                total_score = max(total_score, _LOANWORD_SCORE)
            if total_score >= 0.5:
                matches.append((term, round(total_score, 3)))

        # Best first; ties broken by name — set order varies between processes.
        matches.sort(key=lambda x: (-x[1], x[0]))
        return matches


# Global singleton
_LEXICON_INSTANCE: ReceptionistLexicon | None = None


def get_lexicon() -> ReceptionistLexicon:
    global _LEXICON_INSTANCE
    if _LEXICON_INSTANCE is None:
        _LEXICON_INSTANCE = ReceptionistLexicon()
    return _LEXICON_INSTANCE


def candidates(
    word: str, context: str | None = None, acronyms_only: bool = False
) -> list[tuple[str, float]]:
    """Lookup candidate terms for *word* given surrounding *context*."""
    return get_lexicon().candidates(word, context=context, acronyms_only=acronyms_only)


# ---------------------------------------------------------------------------
# Bilingual Luganda-English URA tax query mapping dictionary (Pillar 3)
# ---------------------------------------------------------------------------
LUGANDA_ENGLISH_TAX_TERMS: Final[dict[str, str]] = {
    # Rental income tax
    "omusolo gwa rental": "Rental Income Tax",
    "omusolo gw'enju": "Rental Income Tax",
    "omusolo gw enju": "Rental Income Tax",
    "musolo gwa rental": "Rental Income Tax",
    "rental tax": "Rental Income Tax",
    # TIN registration / application
    "okwewandiisa otya okufuna tin": "how to register for TIN",
    "okwewandiisa ku tin": "TIN registration",
    "okwewandiisa tin": "TIN registration",
    "okusaba tin": "TIN registration",
    "okufuna tin": "TIN registration",
    "saba tin": "TIN registration",
    # Tax returns filing
    "okufayiringa return": "file tax return",
    "okufayingisa return": "file tax return",
    "okuggyamu return": "file tax return",
    "okussaayo return": "file tax return",
    "obutagyamu return": "late tax return filing penalty",
    # VAT
    "omusolo gwa vat": "VAT rate",
    "musolo gwa vat": "VAT rate",
    "vat gw'ameka": "VAT rate Uganda",
    "vat y'emeka": "VAT rate Uganda",
    "vat yange": "VAT",
    "ebitundu bimeeka": "what percentage",
    "ebitundu bimeka": "what percentage",
    # Motor vehicle transfer
    "omusolo gw'emmotoka": "motor vehicle transfer tax",
    "omusolo gw emmotoka": "motor vehicle transfer tax",
    "omusolo gwa motoka": "motor vehicle transfer tax",
    "okukyusa emmotoka": "motor vehicle transfer ownership",
    "kyusa emmotoka": "motor vehicle transfer ownership",
    # EFRIS
    "efris kye ki": "EFRIS electronic fiscal receipting system",
    "enkola ya efris": "EFRIS electronic fiscal receipting system",
    # Payment & PRN
    "okusasula emisolo ku ssimu": "pay taxes via mobile money PRN",
    "okusasula ku ssimu": "pay tax using mobile money PRN",
    "ennamba ya prn": "PRN payment registration number",
    # Withholding tax
    "withholding tax": "Withholding Tax WHT",
    "omusolo gwa withholding": "Withholding Tax WHT",
    # Corporate tax
    "corporate tax": "Corporate Income Tax rate",
    "omusolo gwa kkampuni": "Corporate Income Tax rate",
    # Tax clearance
    "tax clearance": "tax clearance certificate TCC",
    "ennyingiza y'emisolo egy'entadde": "tax clearance certificate TCC",
    # Penalties / late payment
    "okusasula nga wayiise obudde": "late tax payment penalty",
    "nga wayiise obudde": "late payment penalty interest",
    # Disputes & objections
    "okuwakanya assessment": "tax assessment objection Section 23",
    "okuwakanya omusolo": "tax assessment objection dispute",
}


_ASR_ENTITY_FIXES: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    # Whisper-SALT's fluent mishears of TIN. "tiini" is the normal Luganda
    # pronunciation and is left alone; these are the ones that retrieved VAT.
    (re.compile(r"\btti+mu\b", re.IGNORECASE), "TIN"),
    (re.compile(r"\btiimu\b", re.IGNORECASE), "TIN"),
    (re.compile(r"\btimu\b", re.IGNORECASE), "TIN"),
    (re.compile(r"\bttini\b", re.IGNORECASE), "TIN"),
    # Same frames as clarify._CONTEXT_MISHEARS. "era" alone is "and".
    # "ora" is the GPU Whisper-SALT hearing of URA on lg_tin.wav
    # ("okuva mu ora"), not the conjunction.
    (re.compile(r"\bmu era\b", re.IGNORECASE), "mu URA"),
    (re.compile(r"\bku era\b", re.IGNORECASE), "ku URA"),
    (re.compile(r"\bfrom era\b", re.IGNORECASE), "from URA"),
    (re.compile(r"\bkwa era\b", re.IGNORECASE), "kwa URA"),
    (re.compile(r"\bkutoka era\b", re.IGNORECASE), "kutoka URA"),
    (re.compile(r"\bokuva era\b", re.IGNORECASE), "okuva URA"),
    (re.compile(r"\bmu ora\b", re.IGNORECASE), "mu URA"),
    (re.compile(r"\bku ora\b", re.IGNORECASE), "ku URA"),
    (re.compile(r"\bfrom ora\b", re.IGNORECASE), "from URA"),
    (re.compile(r"\bkwa ora\b", re.IGNORECASE), "kwa URA"),
    (re.compile(r"\bkutoka ora\b", re.IGNORECASE), "kutoka URA"),
    (re.compile(r"\bokuva ora\b", re.IGNORECASE), "okuva URA"),
    # NIN (National Identification Number) acoustic variants & mishears
    (re.compile(r"\b(?:nnamba\s+ya\s+nin|namba\s+ya\s+nin)\b", re.IGNORECASE), "NIN number"),
    (re.compile(r"\b(?:neen|niini|n-i-n)\b", re.IGNORECASE), "NIN"),
    # PRN (Payment Registration Number) acoustic variants
    (re.compile(r"\b(?:nnamba\s+ya\s+prn|namba\s+ya\s+prn)\b", re.IGNORECASE), "PRN number"),
    (re.compile(r"\b(?:peera|pier\s*en|pi\s*ar\s*en|p-r-n)\b", re.IGNORECASE), "PRN"),
    # EFRIS acoustic variants
    (re.compile(r"\b(?:e-fris|efrisi|efurisi)\b", re.IGNORECASE), "EFRIS"),
    # WHT acoustic variants
    (re.compile(r"\b(?:w-h-t|dabulyu\s*ech\s*ti)\b", re.IGNORECASE), "WHT"),
)


def repair_asr_entities(text: str) -> str:
    """Rewrite known Whisper mishears of TIN and URA before retrieval.

    The local stack searches Qdrant with this string. "ttiimu" does not hit
    the TIN passages; "TIN" does.
    """
    if not text:
        return ""
    repaired = text
    for pattern, replacement in _ASR_ENTITY_FIXES:
        repaired = pattern.sub(replacement, repaired)
    return repaired


def normalize_luganda_tax_query(text: str) -> str:
    """Pre-process common code-switched URA tax terms in Luganda queries into standard English domain terms.

    Replaces matched Luganda idioms (longest matches first) with English tax terms
    so that downstream BM25 and dense retrieval accurately hit relevant statutory passages.
    """
    if not text:
        return ""
    normalized = repair_asr_entities(text)
    # Sort terms by descending length to match longer idioms before substrings
    sorted_terms = sorted(LUGANDA_ENGLISH_TAX_TERMS.keys(), key=len, reverse=True)
    for term in sorted_terms:
        pattern = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
        if pattern.search(normalized):
            replacement = LUGANDA_ENGLISH_TAX_TERMS[term]
            normalized = pattern.sub(replacement, normalized)
    return normalized.strip()


SWAHILI_ENGLISH_TAX_TERMS: Final[dict[str, str]] = {
    # TIN registration / application
    "kujisajili namba ya tin": "how to register for TIN",
    "kujisajili kwa tin": "TIN registration",
    "kujisajili kupata tin": "TIN registration",
    "kupata namba ya tin": "TIN registration",
    "nambari ya tin": "TIN Taxpayer Identification Number",
    "namba ya tin": "TIN Taxpayer Identification Number",
    "nambari ya utambulisho wa mlipakodi": "TIN Taxpayer Identification Number",
    "namba ya mlipakodi": "TIN Taxpayer Identification Number",
    # VAT
    "kodi ya ongezeko la thamani": "VAT Value Added Tax",
    "kiwango cha vat": "VAT rate",
    "ushuru wa vat": "VAT Value Added Tax rate",
    "kulipa vat": "pay VAT tax",
    "kiwango cha asilimia": "what percentage",
    # Rental income tax
    "kodi ya mapato ya upangishaji": "Rental Income Tax",
    "kodi ya nyumba ya kupangisha": "Rental Income Tax",
    "kodi ya pango": "Rental Income Tax",
    # Tax return filing
    "kujaza marejesho ya kodi": "file tax return",
    "kuwasilisha marejesho": "file tax return",
    "kuchelewa kuwasilisha marejesho": "late tax return filing penalty",
    # Penalties and interest
    "adhabu ya kuchelewa kulipa": "late tax payment penalty interest",
    "kuchelewa kulipa": "late tax payment penalty interest",
    "adhabu ya kuchelewa": "late payment penalty interest",
    "riba ya kuchelewa": "late payment interest",
    # EFRIS
    "mfumo wa efris": "EFRIS electronic fiscal receipting system",
    "stakabadhi ya efris": "EFRIS electronic fiscal receipt invoice",
    "ankara ya efris": "EFRIS electronic fiscal invoice",
    # Withholding tax
    "kodi ya zuio": "Withholding Tax WHT",
    "kodi ya kuzuia": "Withholding Tax WHT",
    "ushuru wa zuio": "Withholding Tax WHT",
    # Corporate tax
    "kodi ya mapato ya kampuni": "Corporate Income Tax rate",
    "kodi ya kampuni": "Corporate Income Tax CIT",
    # Customs & Import
    "ushuru wa forodha": "customs duty clearance",
    "kodi ya kuagiza bidhaa": "import duty clearance",
    "tamko la forodha": "customs declaration",
    # Motor vehicle
    "uhamisho wa gari": "motor vehicle transfer ownership tax",
    "ushuru wa gari": "motor vehicle registration tax",
    # Dispute & Objection
    "kupinga makadirio ya kodi": "tax assessment objection",
    "kukata rufaa ya kodi": "tax assessment dispute objection",
    "kupinga ushuru": "tax assessment objection dispute",
    # Tax clearance
    "cheti cha kufuata kodi": "tax clearance certificate TCC",
    "cheti cha ushuru": "tax clearance certificate TCC",
    # Payment & PRN
    "nambari ya prn": "PRN payment registration number",
    "namba ya usajili wa malipo": "PRN payment registration number",
    "kulipa kwa simu": "pay tax using mobile money PRN",
}


# Longest first, so "kodi ya mapato ya kampuni" wins over "kodi ya kampuni".
_SWAHILI_TERM_PATTERNS: Final[tuple[tuple[re.Pattern[str], str], ...]] = tuple(
    (re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE), SWAHILI_ENGLISH_TAX_TERMS[term])
    for term in sorted(SWAHILI_ENGLISH_TAX_TERMS, key=len, reverse=True)
)


def normalize_swahili_tax_query(text: str) -> str:
    """Rewrite common Swahili URA tax phrases as the English terms the corpus uses.

    Swahili questions are answered from the English knowledge base; "kodi ya
    zuio" retrieves nothing there, "Withholding Tax WHT" does. Figures and
    section numbers are never added here: they come from the retrieved text.
    """
    if not text:
        return ""
    normalized = repair_asr_entities(text)
    for pattern, replacement in _SWAHILI_TERM_PATTERNS:
        normalized = pattern.sub(replacement, normalized)
    return normalized.strip()


def normalize_call_query(text: str, language: str) -> str:
    """The question a caller asked, as the knowledge base should be searched for it.

    Luganda has its own path (``normalize_luganda_tax_query`` inside the
    cross-lingual bridge); English and Swahili questions come through here.
    """
    if language == "sw":
        return normalize_swahili_tax_query(text)
    return repair_asr_entities(text)

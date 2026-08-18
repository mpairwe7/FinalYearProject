"""Query rewriting — synonym expansion, spell correction, domain normalization, and language detection.

Transforms raw user queries into optimized retrieval queries:
- Domain-specific abbreviation expansion (TIN, VAT, PAYE, etc.)
- Common misspelling correction for URA domain terms
- Query normalization (lowercasing, whitespace cleanup)
- Contextual rewriting from conversation history (coreference resolution)
- Automatic language detection (en, lg, sw, nyn, ach)
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# CodeQL py/log-injection: a request-supplied locale reaches a log call
# below. Strip CR/LF/control characters at the log call itself so a value
# can never forge a fake log line.
_LOG_STRIP_TABLE = dict.fromkeys(range(0x20), None)
_LOG_STRIP_TABLE[0x7F] = None


def _log_safe(value: str) -> str:
    """*value* with control characters (CR/LF included) removed."""
    return value.translate(_LOG_STRIP_TABLE)


# ---------------------------------------------------------------------------
# Language detection — heuristic patterns for Ugandan languages
# ---------------------------------------------------------------------------
# Runyankole (nyn) — Bantu noun-class prefixes (word-start) + common verb forms
_NYN_PREFIXES = re.compile(r"\b(oku|omu|aba|eki|ebi|obu|aha|omw|ogu|eky|enk|emb)\w+", re.IGNORECASE)
_NYN_WORDS = re.compile(
    r"\b(nkore|nkunda|tinkunda|kushonga|oine|aine|niwe|turi|"
    r"twine|rwire|nindwire|ninkunda|ninaba|maka|omushuija|"
    r"omwana|wangye|enshonga|nta|mpa|nimanya)\b",
    re.IGNORECASE,
)
_NYN_MARKERS = None  # computed in detect_language as prefix + word hits
# Acholi (ach) common morphemes and particles
_ACH_MARKERS = re.compile(
    r"\b(ango|ningo|atim|atwero|agengo|tye|bene|dong|kit|gin|"
    r"lwak|dano|latin|piny|kwo|cam|wek|twero|cako|myero)\b",
    re.IGNORECASE,
)
# Swahili (sw) common function words
_SW_MARKERS = re.compile(
    r"\b(ninaweza|ninazuia|nifanye|mtoto|nyumbani|biashara|"
    r"kwa|ya|lakini|sababu|jinsi|nini|gani|wapi|vipi|"
    r"kupata|kuanza|kufanya|kusaidia|kupanga)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Domain abbreviation expansion
# ---------------------------------------------------------------------------
# Expansions keep the acronym in parentheses on purpose.  Retrieval is
# hybrid: the dense side matches the spelled-out phrase, the sparse
# (BM25) side matches whichever surface form the corpus actually uses.
# Replacing "WHT" with "Withholding Tax" trades one exact match for
# another and loses BM25 recall on every document that writes the
# acronym — which URA guidance routinely does.  BM25 tokenisation
# strips the brackets, so "Withholding Tax (WHT)" indexes as
# ['withholding', 'tax', 'wht'] and both forms hit.
_ABBREVIATIONS: dict[str, str] = {
    "tin": "Taxpayer Identification Number (TIN)",
    "vat": "Value Added Tax (VAT)",
    "paye": "Pay As You Earn (PAYE)",
    "efris": "Electronic Fiscal Receipting and Invoicing System (EFRIS)",
    "ura": "Uganda Revenue Authority (URA)",
    "dts": "Digital Tracking Solution (DTS)",
    "trep": "Tax Registration Expansion Project (TREP)",
    "cit": "Corporate Income Tax (CIT)",
    "pit": "Personal Income Tax (PIT)",
    "wht": "Withholding Tax (WHT)",
    "whit": "Withholding Tax (WHT)",
    "etax": "e-Tax portal (eTax)",
    "efiling": "electronic filing (eFiling)",
    "kcca": "Kampala Capital City Authority (KCCA)",
    "nssf": "National Social Security Fund (NSSF)",
    "ugx": "Ugandan Shillings (UGX)",
    "usd": "US Dollars (USD)",
}

# ---------------------------------------------------------------------------
# Common misspellings in the URA domain
# ---------------------------------------------------------------------------
_CORRECTIONS: dict[str, str] = {
    "regester": "register",
    "registar": "register",
    "regsiter": "register",
    "registeration": "registration",
    "registation": "registration",
    "tax payer": "taxpayer",
    "taxpyer": "taxpayer",
    "withholdin": "withholding",
    "witholding": "withholding",
    "excise duity": "excise duty",
    "exise duty": "excise duty",
    "assesment": "assessment",
    "assement": "assessment",
    "refud": "refund",
    "refumd": "refund",
    "penality": "penalty",
    "penalyt": "penalty",
    "complience": "compliance",
    "compiance": "compliance",
    "decleration": "declaration",
    "declaraton": "declaration",
    "importaton": "importation",
    "clearence": "clearance",
    "clearanse": "clearance",
    "objection": "objection",
    "obejction": "objection",
    "receipting": "receipting",
    "receiping": "receipting",
    "invoiceing": "invoicing",
}


def expand_abbreviations(query: str) -> str:
    """Expand known abbreviations inline for better retrieval recall."""
    words = query.split()
    expanded = []
    for w in words:
        key = w.lower().strip(".,;:?!\"'()")
        if key in _ABBREVIATIONS:
            # Preserve trailing punctuation
            suffix = w[len(w.rstrip(".,;:?!\"'()")) :]
            expanded.append(_ABBREVIATIONS[key] + suffix)
        else:
            expanded.append(w)
    return " ".join(expanded)


def correct_spelling(query: str) -> str:
    """Fix common domain-specific misspellings.

    Substitutions are anchored to word boundaries. Without ``\\b`` a key that is a
    prefix of its own replacement corrupts the correctly-spelled word:
    ``"withholdin" -> "withholding"`` rewrote *"What is withholding tax?"* as
    *"What is withholdingg tax?"*, which drops "withholding" from the BM25 query
    entirely. Retrieval then fell back to generic tax matches and answered a
    withholding-tax question from VAT and EFRIS documents — measured against the
    live corpus, where a direct query ranks the Withholding-Tax PDF first.
    """
    result = query
    for wrong, right in _CORRECTIONS.items():
        result = re.sub(rf"\b{re.escape(wrong)}\b", right, result, flags=re.IGNORECASE)
    return result


def normalize(query: str) -> str:
    """Whitespace and basic cleanup."""
    return re.sub(r"\s+", " ", query.strip())


def rewrite_with_history(
    query: str,
    history: list[dict[str, str]],
) -> str:
    """Resolve coreferences using conversation history.

    Simple heuristic: if the query contains pronouns like "it", "that",
    "this", "they" without a clear subject, prepend context from the
    last assistant reply.
    """
    if not history:
        return query

    pronoun_pattern = re.compile(
        r"\b(it|that|this|they|them|those|its|their|the above|the same)\b",
        re.IGNORECASE,
    )

    if pronoun_pattern.search(query):
        last_turn = history[-1]
        last_user = last_turn.get("user_message", "")
        last_bot = last_turn.get("bot_reply", "")

        # Prefer a concrete entity from the previous user turn. This keeps
        # follow-ups like "How do I register for it?" anchored to "TIN"
        # instead of the whole prior assistant answer.
        subject = ""
        abbreviations = re.findall(r"\b[A-Z]{2,10}\b", last_user)
        if abbreviations:
            subject = abbreviations[-1]

        if subject:
            subject_phrase = _ABBREVIATIONS.get(subject.lower(), subject)
            replacement = subject_phrase
            if re.search(r"\bregister(?:ing)?\s+for\s+(it|that|this|the same)\b", query, re.IGNORECASE):
                article = "an" if subject_phrase[:1].lower() in "aeiou" else "a"
                replacement = f"{article} {subject_phrase}"

            rewritten = pronoun_pattern.sub(replacement, query)
            logger.debug("Query rewritten with user-turn subject: %s → %s", query, rewritten)
            return rewritten

        # Fallback: use the first assistant sentence as a broad context hint.
        first_sentence = re.split(r"(?<=[^A-Z])[.!?]\s", last_bot)[0].strip()
        if first_sentence and len(first_sentence) > 10:
            rewritten = f"Regarding '{first_sentence[:100]}': {query}"
            logger.debug("Query rewritten with assistant context: %s → %s", query, rewritten)
            return rewritten

    return query


# Lingua eager-preloads its language models, so constructing the detector is
# expensive (~12s). Build it ONCE per process and reuse it across requests —
# rebuilding per call made language detection the dominant chat-latency stage.
_LANG_DETECTOR = None
_LANG_DETECTOR_INIT_FAILED = False


def _get_language_detector():
    """Return a cached :class:`LanguageDetector`, or ``None`` if unavailable."""
    global _LANG_DETECTOR, _LANG_DETECTOR_INIT_FAILED
    if _LANG_DETECTOR is None and not _LANG_DETECTOR_INIT_FAILED:
        try:
            from ml.scripts.lang_id import LanguageDetector

            _LANG_DETECTOR = LanguageDetector(min_confidence=0.55)
        except Exception:
            _LANG_DETECTOR_INIT_FAILED = True
            logger.debug("LanguageDetector unavailable, using heuristic only")
    return _LANG_DETECTOR


def detect_language(text: str) -> str:
    """Detect input language, returning a locale code (en, lg, sw, nyn, ach).

    Uses a lightweight heuristic chain:
      1. Runyankole / Acholi / Swahili marker patterns (cheap, local)
      2. LanguageDetector from ml.scripts.lang_id (lingua or heuristic)
      3. Sunbird API as cloud fallback (only if local confidence is low)

    Returns "en" as default when detection is inconclusive.
    """
    if not text or len(text.strip()) < 5:
        return "en"

    cleaned = text.strip().lower()
    words = re.findall(r"[a-z\']+", cleaned)
    n_words = max(len(words), 1)

    # Quick heuristic: count marker hits per language
    nyn_hits = len(_NYN_PREFIXES.findall(cleaned)) + len(_NYN_WORDS.findall(cleaned))
    ach_hits = len(_ACH_MARKERS.findall(cleaned))
    sw_hits = len(_SW_MARKERS.findall(cleaned))

    nyn_ratio = nyn_hits / n_words
    ach_ratio = ach_hits / n_words
    sw_ratio = sw_hits / n_words

    # Strong signal thresholds
    if nyn_ratio >= 0.15 and nyn_hits >= 2:
        return "nyn"
    if ach_ratio >= 0.15 and ach_hits >= 2:
        return "ach"
    if sw_ratio >= 0.15 and sw_hits >= 2:
        return "sw"

    # Fall back to the cached LanguageDetector (en/lg/sw via lingua)
    det = _get_language_detector()
    if det is not None:
        try:
            result = det.detect(text)
            if result.is_confident(0.55):
                return result.lang
        except Exception:
            logger.debug("LanguageDetector.detect failed; using heuristic only")

    # If local detection is low-confidence, try Sunbird API
    try:
        from . import sunbird
        if sunbird.is_available():
            sb_result = sunbird.detect_language(text)
            if sb_result and sb_result.get("locale"):
                return sb_result["locale"]
    except Exception:
        logger.debug("Sunbird language detection unavailable")

    return "en"


def rewrite(
    query: str,
    history: list[dict[str, str]] | None = None,
) -> str:
    """Full query rewriting pipeline."""
    q = normalize(query)
    q = correct_spelling(q)
    q = expand_abbreviations(q)
    if history:
        q = rewrite_with_history(q, history)
    return q


# Sentence boundary requires trailing whitespace after `.!?`, so "37.5m"
# (no space after the period) is never mistaken for one — same guard
# claim_verifier applies. Em/en dash and colon/semicolon also split clauses
# even without a following `.!?` (distress preambles commonly use "— " or
# ": " rather than a full stop before the actual question). No leading `\s*`
# before the dash/colon branch — it and the trailing `\s+` would otherwise
# both match long whitespace runs, a polynomial-backtracking pattern on
# user-controlled text (CodeQL py/polynomial-redos); any leading space stays
# on the previous segment and is removed by the caller's per-segment strip().
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+|[—–:;]\s+")


def extract_question_span(text: str) -> str:
    """Return only the interrogative sentence(s) in *text*, or "" if none.

    Distress-framed messages ("I've tried three times and it still doesn't
    work!! What is EFRIS?", "I'm worried — What is EFRIS?") combine
    emotional preamble with a real question in one string. Retrieving on the
    raw combination dilutes BM25/embedding relevance enough to push an
    otherwise-answerable question into false abstention, so callers handling
    a distressed turn should retrieve on this extracted span instead of the
    full rewritten text.
    """
    sentences = _SENTENCE_BOUNDARY_RE.split((text or "").strip())
    questions = [s.strip() for s in sentences if s.strip().endswith("?")]
    return " ".join(questions)


# ---------------------------------------------------------------------------
# Query-time retrieval plan (G17 + agentic multi-intent)
# ---------------------------------------------------------------------------
# Hard filters only fire on *unambiguous* mentions. A bare "2026" is not a
# fiscal year (Ugandan FY is July–June). Soft preferences boost matching
# passages without starving recall when the preferred edition is missing.

def current_fiscal_year() -> str:
    """Soft-preference target for “this fiscal year” / “current”.

    ``CURRENT_FISCAL_YEAR`` in the environment wins. Otherwise use the
    rate-table year in force today so the boost cannot freeze on last
    year's edition (see ``App/docs/tax-rate-tables.md``).
    """
    env = os.getenv("CURRENT_FISCAL_YEAR", "").strip()
    if env:
        return env
    try:
        from .tax.tables import resolve_fiscal_year

        return resolve_fiscal_year()
    except Exception:
        return "FY2026-27"


CURRENT_FISCAL_YEAR = current_fiscal_year()

_FY_EXPLICIT_RE = re.compile(
    r"\bFY\s*(20\d{2})\s*[-/]\s*(?:20)?(\d{2})\b",
    re.I,
)
_FY_SLASH_RE = re.compile(r"\b(20\d{2})\s*/\s*(20)?(\d{2})\s+(?:fiscal\s+)?year\b", re.I)
_CURRENT_FY_RE = re.compile(
    r"\b(this\s+(?:fiscal\s+)?year|current\s+(?:fiscal\s+)?year|latest|this\s+fy)\b",
    re.I,
)

# Machine translation returns the expanded form of a tax term where the
# deterministic routers and the corpus both use the abbreviation: Sunbird
# renders a Luganda VAT question as "what is the value-added tax in Uganda",
# and the rate matcher wants the literal "VAT". Without this a translated
# question misses every fast path and abstains, which is the whole reason
# local-language questions used to get worse answers than English ones.
#
# Only terms whose abbreviation is what the matchers key on are listed. The
# substitution is deliberately one-way (expanded -> abbreviation) and leaves
# the rest of the sentence alone.
_EXPANDED_TAX_TERMS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bvalue[-\s]?added\s+tax\b", re.I), "VAT"),
    (re.compile(r"\bpay[-\s]?as[-\s]?you[-\s]?earn\b", re.I), "PAYE"),
    # MT renders this both ways — "Tax Identification Number" and "taxpayer
    # identification number" — so "payer" is optional here.
    (re.compile(r"\btax\s*(?:payer)?\s+identification\s+number\b", re.I), "TIN"),
]


def canonicalize_tax_terms(text: str) -> str:
    """Rewrite expanded tax terms to the abbreviations the matchers use.

    Applied to machine-translated text before it reaches the deterministic
    routers; a no-op for text that already uses the abbreviation.
    """
    out = text or ""
    for pattern, replacement in _EXPANDED_TAX_TERMS:
        out = pattern.sub(replacement, out)
    return out


_TAX_TYPE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(value[-\s]?added\s+tax|vat)\b", re.I), "vat"),
    (re.compile(r"\b(pay\s+as\s+you\s+earn|paye)\b", re.I), "paye"),
    (re.compile(r"\b(withholding\s+tax|wht)\b", re.I), "wht"),
    (re.compile(r"\b(corporate\s+income\s+tax|corporation\s+tax|cit)\b", re.I), "cit"),
    (re.compile(r"\b(personal\s+income\s+tax|pit)\b", re.I), "pit"),
    (re.compile(r"\b(excise\s+duty|excise)\b", re.I), "excise"),
    (re.compile(r"\b(customs?(?:\s+duty)?|import\s+duty)\b", re.I), "customs"),
    (re.compile(r"\b(capital\s+gains?(?:\s+tax)?|cgt)\b", re.I), "cgt"),
    (re.compile(r"\b(efris)\b", re.I), "efris"),
    (re.compile(r"\b(tin|taxpayer\s+identification)\b", re.I), "tin"),
]

# Split only on multi-intent markers. A bare "and" is too common
# ("VAT and PAYE rates" is one comparison, not two searches).
#
# Possessive quantifiers (Python 3.11+): \s+ adjacent to alternation here
# is exactly CodeQL's py/polynomial-redos shape — an adversarial run of
# whitespace lets the backtracking engine try many equivalent ways to
# split it across the \s+/\s* boundaries before a match ultimately fails.
# Making them possessive (\s++, \s*+) is the standard fix: the engine
# commits to the longest run and never backtracks into it. Verified
# behavior-identical to the backtracking originals across representative
# inputs, and empirically fast (µs, not seconds) on adversarial whitespace.
# decompose_query() below also runs normalize() before matching, which
# already collapses whitespace runs to one space — independently removing
# the long-run precondition these patterns would otherwise need.
_DECOMPOSE_SPLIT_RE = re.compile(
    r"\s++(?:and also|as well as|and then)\s++|"
    r"\s*+;\s++|"
    r"\?\s++(?=(?:what|how|when|where|which|who)\b)",
    re.I,
)
_AND_QUESTION_RE = re.compile(
    r"\s++and\s++(?=(?:what|how|when|where|which|who)\b)",
    re.I,
)


def _normalize_fy(start: str, end: str) -> str:
    end = end[-2:] if len(end) >= 2 else end
    return f"FY{start}-{end}"


def extract_retrieval_filters(query: str) -> dict[str, Any]:
    """Hard Qdrant payload filters for *explicit* metadata in the query.

    ``HybridRetriever.search`` already accepts filters; nothing in the
    serving path used them (G17). Only unambiguous FY labels become a
    hard filter — a missing edition must not silently empty the result
    set, so tax-type and "current year" stay as preferences.
    """
    filters: dict[str, Any] = {}
    text = query or ""
    match = _FY_EXPLICIT_RE.search(text) or _FY_SLASH_RE.search(text)
    if match:
        if match.re is _FY_SLASH_RE:
            filters["fiscal_year"] = _normalize_fy(match.group(1), match.group(3))
        else:
            filters["fiscal_year"] = _normalize_fy(match.group(1), match.group(2))
    return filters


def extract_retrieval_preferences(query: str) -> dict[str, Any]:
    """Soft ranking hints: mentioned tax type and 'current' fiscal year."""
    prefer: dict[str, Any] = {}
    text = query or ""
    if _CURRENT_FY_RE.search(text) and not extract_retrieval_filters(text):
        prefer["fiscal_year"] = current_fiscal_year()
    types = [name for pattern, name in _TAX_TYPE_PATTERNS if pattern.search(text)]
    if len(types) == 1:
        prefer["tax_type"] = types[0]
    return prefer


def decompose_query(query: str) -> list[str]:
    """Split a multi-intent question into at most three retrieval queries.

    Single-intent questions (the common case) return ``[query]`` unchanged.
    Used as a cheap stand-in for query decomposition / multi-hop planning
    without a second LLM call on the hot path.
    """
    text = normalize(query or "")
    if not text:
        return []
    parts = [p.strip(" .?") for p in _DECOMPOSE_SPLIT_RE.split(text) if p.strip()]
    if len(parts) == 1:
        parts = [p.strip(" .?") for p in _AND_QUESTION_RE.split(text) if p.strip()]
    cleaned: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if len(part.split()) < 2:
            continue
        key = part.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(part)
    if len(cleaned) <= 1:
        return [text]
    return cleaned[:3]


def plan_retrieval(query: str) -> dict[str, Any]:
    """Bundle filters, preferences, and sub-queries for one retrieval turn."""
    return {
        "filters": extract_retrieval_filters(query),
        "prefer": extract_retrieval_preferences(query),
        "subqueries": decompose_query(query),
    }


def english_retrieval_query(query: str, locale: str | None) -> str:
    """Query text to search the English corpus with (G18).

    Source documents are English. The generator answers in *locale*.
    Translation is best-effort: English, unknown, or a failed MT call
    returns the original string so dense/BM25 still run.
    """
    text = (query or "").strip()
    loc = (locale or "en").strip().lower().split("-")[0]
    if not text or loc in ("", "en"):
        return text
    try:
        from . import sunbird

        english = sunbird.translate_to_english(text, loc)
    except Exception:
        logger.debug(
            "english_retrieval_query: translation failed locale=%s",
            _log_safe(loc),
            exc_info=True,
        )
        return text
    if not english or english.strip().casefold() == text.casefold():
        return text
    return english.strip()

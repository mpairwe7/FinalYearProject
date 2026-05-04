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
import re

logger = logging.getLogger(__name__)

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
    "whit": "Withholding Tax",
    "etax": "e-Tax portal",
    "efiling": "electronic filing",
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
    """Fix common domain-specific misspellings."""
    result = query
    for wrong, right in _CORRECTIONS.items():
        result = re.sub(re.escape(wrong), right, result, flags=re.IGNORECASE)
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

    # Fall back to LanguageDetector (en/lg/sw via lingua)
    try:
        from ml.scripts.lang_id import LanguageDetector
        det = LanguageDetector(min_confidence=0.55)
        result = det.detect(text)
        if result.is_confident(0.55):
            return result.lang
    except Exception:
        logger.debug("LanguageDetector unavailable, using heuristic only")

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

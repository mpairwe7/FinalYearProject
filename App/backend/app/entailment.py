"""Entailment / contradiction grounding for high-stakes claims (P1-8).

The lexical claim verifier (token overlap + numeric containment) can let a
fluent-but-wrong claim through with only a soft support penalty. This module
adds a *contradiction* signal so the response judge escalates/withholds rather
than merely appending a disclaimer.

Always-on: a deterministic, high-precision check for conflicting percentages —
tax rates are the prime high-stakes numeric claim (e.g. answer says "VAT is
20%" while the cited passage says 18%). Optional: a real NLI cross-encoder when
``ENTAILMENT_MODEL`` is configured (graceful fallback to numeric-only).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# e.g. "cross-encoder/nli-deberta-v3-small"; empty → deterministic numeric only.
ENTAILMENT_MODEL = os.getenv("ENTAILMENT_MODEL", "")
_CONTRADICTION_PROB_MIN = float(os.getenv("ENTAILMENT_CONTRADICTION_MIN", "0.6"))

# A percentage written in English ("18%", "18 percent"), Swahili ("asilimia 18", "18 kwa mia"),
# or Luganda ("ebitundu 18 ku buli kikumi").
_PCT_RE = re.compile(
    r"(?:"
    r"\b(?:asilimia|ebitundu|kigero\s+kya)\s*(\d+(?:\.\d+)?)\b"
    r"|"
    r"(\d+(?:\.\d+)?)\s*(?:%|per\s?cent(?:age)?|\b(?:kwa\s+mia|ku\s+buli\s+kikumi|ku\s+100)\b)"
    r")",
    re.IGNORECASE,
)

_SWAHILI_PCT_WORDS = [
    ("kumi na mbili", "12"),
    ("kumi na tano", "15"),
    ("kumi na nane", "18"),
    ("kumi", "10"),
    ("tisa", "9"),
    ("nane", "8"),
    ("minane", "8"),
    ("saba", "7"),
    ("sita", "6"),
    ("tano", "5"),
    ("nne", "4"),
    ("tatu", "3"),
    ("mbili", "2"),
    ("moja nukta tano", "1.5"),
    ("moja", "1"),
    ("nusu", "0.5"),
    ("sifuri", "0"),
    ("ishirini", "20"),
    ("thelathini", "30"),
    ("arobaini", "40"),
    ("hamsini", "50"),
    ("sitini", "60"),
    ("sabini", "70"),
    ("themanini", "80"),
    ("tisini", "90"),
    ("mia", "100"),
]

_LUGANDA_PCT_WORDS = [
    ("kumi na bbiri", "12"),
    ("kumi na biri", "12"),
    ("kumi na bitaano", "15"),
    ("kumi na tano", "15"),
    ("kumi na munaana", "18"),
    ("kumi na munaanana", "18"),
    ("kumi", "10"),
    ("mwenda", "9"),
    ("munaana", "8"),
    ("musanvu", "7"),
    ("mukaaga", "6"),
    ("ttaano", "5"),
    ("taano", "5"),
    ("nnya", "4"),
    ("ssatu", "3"),
    ("bbiri", "2"),
    ("emu n'ekitundu", "1.5"),
    ("emu", "1"),
    ("kitundu", "0.5"),
    ("abiri", "20"),
    ("amakumi abiri", "20"),
    ("asatu", "30"),
    ("amakumi asatu", "30"),
    ("ana", "40"),
    ("amakumi ana", "40"),
    ("ataano", "50"),
    ("amakumi ataano", "50"),
    ("kikumi", "100"),
]

# A money amount: comma- or space-grouped ("1,500,000"), plain ("335000"), or
# suffixed ("1.5m", "300 million").  Percentages are excluded by the caller.
_AMOUNT_RE = re.compile(
    r"(?:ugx|ug\s?shs?|shs|shillings?|ssente|ensimbi|shilingi)?\s*"
    r"(\d{1,3}(?:[,\s]\d{3})+|\d+(?:\.\d+)?)\s*"
    r"(k|m|bn|b|thousand|million|billion|milioni|bilioni|elfu|laki|obukadde|akakadde|obuwumbi|akawumbi|emitwalo|omutwalo|enkumi|olukumi)?\b",
    re.IGNORECASE,
)
_AMOUNT_SUFFIX = {
    "k": 1_000,
    "thousand": 1_000,
    "m": 1_000_000,
    "million": 1_000_000,
    "b": 1_000_000_000,
    "bn": 1_000_000_000,
    "billion": 1_000_000_000,
    "milioni": 1_000_000,
    "bilioni": 1_000_000_000,
    "elfu": 1_000,
    "laki": 100_000,
    "obukadde": 1_000_000,
    "akakadde": 1_000_000,
    "obuwumbi": 1_000_000_000,
    "akawumbi": 1_000_000_000,
    "emitwalo": 10_000,
    "omutwalo": 10_000,
    "enkumi": 1_000,
    "olukumi": 1_000,
}

# Multipliers placed BEFORE digits (common in Swahili & Luganda, e.g. "milioni 150", "obukadde 150")
_AMOUNT_PREFIX_RE = re.compile(
    r"\b(milioni|bilioni|elfu|laki|obukadde|akakadde|obuwumbi|akawumbi|emitwalo|omutwalo|enkumi|olukumi)\s+"
    r"(\d{1,3}(?:[,\s]\d{3})+|\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)
_AMOUNT_PREFIX_MULTIPLIERS = {
    "milioni": 1_000_000,
    "bilioni": 1_000_000_000,
    "elfu": 1_000,
    "laki": 100_000,
    "obukadde": 1_000_000,
    "akakadde": 1_000_000,
    "obuwumbi": 1_000_000_000,
    "akawumbi": 1_000_000_000,
    "emitwalo": 10_000,
    "omutwalo": 10_000,
    "enkumi": 1_000,
    "olukumi": 1_000,
}

#: Statements *about the rule* — a wrong amount here is a factual error about
#: the law, not an arithmetic result.  Computed totals ("PAYE = UGX 202,000")
#: legitimately carry amounts the source passage never states, so the money
#: contradiction check fires only for rule-shaped sentences.
_RULE_CUE_RE = re.compile(
    r"\b(threshold|above|below|exceed\w*|at\s+least|minimum|maximum|"
    r"limit|register\w*|registration|band|bracket|allowance|cap(?:ped)?)\b",
    re.IGNORECASE,
)

_model: Any = None
_model_loaded = False


def percentages(text: str) -> set[str]:
    """Numeric values stated as percentages, e.g. {"18"} from "18%" / "asilimia 18"."""
    results: set[str] = set()
    lowered = (text or "").lower()
    for m in _PCT_RE.finditer(lowered):
        val = m.group(1) or m.group(2)
        if val:
            results.add(val)

    # Detect Swahili spoken percentage phrases, e.g. "asilimia kumi na nane" -> "18"
    check_text = lowered
    for word_phrase, num_str in _SWAHILI_PCT_WORDS:
        pattern = r"\basilimia\s+" + re.escape(word_phrase) + r"\b"
        if re.search(pattern, check_text):
            results.add(num_str)
            check_text = re.sub(pattern, " ", check_text)

    # Detect Luganda spoken percentage phrases, e.g. "ebitundu kumi na munaana" -> "18"
    for word_phrase, num_str in _LUGANDA_PCT_WORDS:
        pattern = r"\bebitundu\s+" + re.escape(word_phrase) + r"\b"
        if re.search(pattern, check_text):
            results.add(num_str)
            check_text = re.sub(pattern, " ", check_text)

    return results


def canonical_amounts(text: str) -> set[float]:
    """Money amounts in *text*, normalised to their numeric value.

    ``"UGX 1,500,000"``, ``"1.5m"``, ``"milioni 150"`` and ``"obukadde 150"``
    all yield ``150000000.0``. Without this, comma-grouped figures were tokenised
    into ``{"1", "500", "000"}`` and East African vernacular multipliers
    (Luganda obukadde, Swahili milioni) were dropped, falsely triggering
    numerical mismatch warnings on correct translations.
    """
    lowered = (text or "").lower()
    # Percentages are handled separately; drop them so "18%" is not read
    # as the amount 18.
    without_pct = _PCT_RE.sub(" ", lowered)
    for phrase, _ in _SWAHILI_PCT_WORDS:
        without_pct = re.sub(r"\basilimia\s+" + re.escape(phrase) + r"\b", " ", without_pct)
    for phrase, _ in _LUGANDA_PCT_WORDS:
        without_pct = re.sub(r"\bebitundu\s+" + re.escape(phrase) + r"\b", " ", without_pct)
    # Strip Ugandan toll-free and mobile phone numbers (e.g. 0800 117 000, 0772 140 000)
    # so contact lines are not falsely parsed as tax amounts
    without_pct = re.sub(r"\b0\d{2,3}[\s-]?\d{3}[\s-]?\d{3}\b", " ", without_pct)
    amounts: set[float] = set()

    # 1. Prefix multipliers (e.g. "milioni 150", "obukadde 150", "emitwalo 23.5")
    def _sub_prefix(m: re.Match[str]) -> str:
        mult = _AMOUNT_PREFIX_MULTIPLIERS.get(m.group(1).lower(), 1)
        digits = m.group(2).replace(",", "").replace(" ", "")
        try:
            amounts.add(float(digits) * mult)
        except ValueError:
            pass
        return " "

    remainder = _AMOUNT_PREFIX_RE.sub(_sub_prefix, without_pct)
    # Normalize English ordinal dates (e.g. "15th", "1st", "30th") to cardinal digits
    # so statutory filing deadlines survive translation into Swahili and Luganda
    remainder = re.sub(r"\b(\d{1,2})(?:st|nd|rd|th)\b", r"\1", remainder, flags=re.IGNORECASE)

    # 2. Standard suffixes (e.g. "150m", "150 million", "UGX 150,000,000") and plain numbers
    for match in _AMOUNT_RE.finditer(remainder):
        digits = match.group(1).replace(",", "").replace(" ", "")
        try:
            value = float(digits)
        except ValueError:
            continue
        value *= _AMOUNT_SUFFIX.get((match.group(2) or "").lower(), 1)
        amounts.add(value)

    # 3. Vernacular word numbers for small cardinal integers
    _CARDINAL_WORDS = {
        "munaana": 8.0, "minane": 8.0, "nane": 8.0, "musanvu": 7.0, "saba": 7.0,
        "mukaaga": 6.0, "sita": 6.0, "ttaano": 5.0, "taano": 5.0, "tano": 5.0,
        "nnya": 4.0, "nne": 4.0, "ssatu": 3.0, "tatu": 3.0, "bbiri": 2.0, "mbili": 2.0,
        "kkumi": 10.0, "kumi": 10.0, "asatu": 30.0, "thelathini": 30.0, "abiri": 20.0,
        "ishirini": 20.0, "ana": 40.0, "arobaini": 40.0, "ataano": 50.0, "hamsini": 50.0,
    }
    for word, val in _CARDINAL_WORDS.items():
        if re.search(rf"\b{word}\b", remainder):
            amounts.add(val)
    return amounts


def numeric_contradiction(claim: str, context: str, user_query: str = "") -> bool:
    """True when a claim's figures conflict with the cited context's.

    Two high-precision rules:

    *Percentages* — fires when both sides state percentages and the
    claim's are entirely absent from the context ("VAT is 20%" against a
    passage saying 18%).

    *Money* — fires only for rule-shaped sentences (a threshold, band or
    registration limit), where a figure the passage does not state is a
    misstatement of the law rather than an arithmetic result.  This is
    what catches a stale threshold after a budget moves one, which the
    percentage rule cannot see: the FY2026-27 amendments changed the PAYE
    tax-free threshold and the VAT registration threshold without
    changing a single rate.
    """
    cp = percentages(claim)
    xp = percentages(context)
    qp = percentages(user_query) if user_query else set()
    model_pct = cp - qp
    if model_pct and xp and model_pct.isdisjoint(xp):
        # A percentage is only a contradiction if the claim and the cited context
        # are actually discussing the same subject rather than unrelated facts.
        claim_words = set(re.findall(r"\w{4,}", claim.lower()))
        context_words = set(re.findall(r"\w{4,}", context.lower()))
        shared = (claim_words & context_words) - {"that", "with", "from", "this", "have", "were", "will", "your", "under"}
        if shared:
            return True

    if not _RULE_CUE_RE.search(claim):
        return False
    ca = canonical_amounts(claim)
    xa = canonical_amounts(context)
    qa = canonical_amounts(user_query) if user_query else set()
    model_amounts = ca - qa
    if model_amounts and xa and model_amounts.isdisjoint(xa):
        return True
    return False



def _load_model() -> Any:
    global _model, _model_loaded
    if _model_loaded:
        return _model
    _model_loaded = True
    if not ENTAILMENT_MODEL:
        return None
    try:
        from sentence_transformers import CrossEncoder

        _model = CrossEncoder(ENTAILMENT_MODEL)
        logger.info("Entailment NLI model loaded: %s", ENTAILMENT_MODEL)
    except Exception:
        logger.warning("Entailment model %s unavailable; numeric-only", ENTAILMENT_MODEL, exc_info=True)
        _model = None
    return _model


def _model_says_contradicted(claim: str, context: str) -> bool:
    """Best-effort NLI check; returns False when no model is configured."""
    model = _load_model()
    if model is None:
        return False
    try:
        import numpy as np

        logits = np.asarray(model.predict([(context, claim)]), dtype=float).reshape(-1)
        # Standard 3-way NLI label order is [contradiction, entailment, neutral].
        if logits.size < 3:
            return False
        shifted = np.exp(logits - logits.max())
        probs = shifted / shifted.sum()
        return bool(int(probs.argmax()) == 0 and probs[0] >= _CONTRADICTION_PROB_MIN)
    except Exception:
        logger.debug("NLI predict failed; numeric-only", exc_info=True)
        return False


def is_contradicted(claim: str, contexts: list[str], user_query: str = "") -> bool:
    """Return True if *claim* contradicts the cited *contexts* (P1-8)."""
    context = " ".join(c for c in contexts if c)
    if not claim.strip() or not context.strip():
        return False
    if numeric_contradiction(claim, context, user_query=user_query):
        return True
    return _model_says_contradicted(claim, context)

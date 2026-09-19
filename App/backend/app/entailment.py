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
    r"(k|m|bn|b|thousand|million|billion|milioni|bilioni|elfu|laki|o?bukadde|a?kakadde|o?buwumbi|a?kawumbi|e?mitwalo|o?mutwalo|e?nkumi|o?lukumi)?\b",
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
    "bukadde": 1_000_000,
    "akakadde": 1_000_000,
    "kakadde": 1_000_000,
    "obuwumbi": 1_000_000_000,
    "buwumbi": 1_000_000_000,
    "akawumbi": 1_000_000_000,
    "kawumbi": 1_000_000_000,
    "emitwalo": 10_000,
    "mitwalo": 10_000,
    "omutwalo": 10_000,
    "mutwalo": 10_000,
    "enkumi": 1_000,
    "nkumi": 1_000,
    "olukumi": 1_000,
    "lukumi": 1_000,
}

# Multipliers placed BEFORE digits (common in Swahili & Luganda, e.g. "milioni 150", "obukadde 150", "bukadde bwa siringi 5")
_AMOUNT_PREFIX_RE = re.compile(
    r"\b(milioni|bilioni|elfu|laki|o?bukadde|a?kakadde|o?buwumbi|a?kawumbi|e?mitwalo|o?mutwalo|e?nkumi|o?lukumi)"
    r"(?:\s+(?:bwa|kwa|za|ya|nga)?\s*(?:ssente|sente|siringi|shilingi|shs|ugx)?)?\s+"
    r"(\d{1,3}(?:[,\s]\d{3})+|\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)
_AMOUNT_PREFIX_MULTIPLIERS = {
    "milioni": 1_000_000,
    "bilioni": 1_000_000_000,
    "elfu": 1_000,
    "laki": 100_000,
    "obukadde": 1_000_000,
    "bukadde": 1_000_000,
    "akakadde": 1_000_000,
    "kakadde": 1_000_000,
    "obuwumbi": 1_000_000_000,
    "buwumbi": 1_000_000_000,
    "akawumbi": 1_000_000_000,
    "kawumbi": 1_000_000_000,
    "emitwalo": 10_000,
    "mitwalo": 10_000,
    "omutwalo": 10_000,
    "mutwalo": 10_000,
    "enkumi": 1_000,
    "nkumi": 1_000,
    "olukumi": 1_000,
    "lukumi": 1_000,
}

#: Statements *about the rule* — a wrong amount here is a factual error about
#: the law, not an arithmetic result.  Computed totals ("PAYE = UGX 202,000")
#: legitimately carry amounts the source passage never states, so the money
#: contradiction check fires only for rule-shaped sentences.
_RULE_CUE_RE = re.compile(
    r"\b(threshold|above|below|exceed\w*|at\s+least|minimum|maximum|"
    r"limit|register\w*|registration|band|bracket|allowance|(?:capped|cap\b(?!\s*\.?\s*\d)))\b",
    re.IGNORECASE,
)

_model: Any = None
_model_loaded = False


def percentages(text: str) -> set[str]:
    """Numeric values stated as percentages, e.g. {"18"} from "18%" / "asilimia 18"."""
    results: set[str] = set()
    lowered = re.sub(r"(\d+)\.\s+(\d+)", r"\1.\2", (text or "").lower())
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
    clean_text = re.sub(r"<[^>]+>", " ", text or "")
    clean_text = re.sub(r"(\d+)\.\s+(\d+)", r"\1.\2", clean_text)
    lowered = clean_text.lower()
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
    # Strip common non-numeric idioms containing cardinal/ordinal words (e.g. "third party", "mtu wa tatu", "pande mbili")
    remainder = re.sub(r"\b(?:mtu|watu|upande|pande|chama|mtu\s+yeyote)\s+wa\s+tatu\b", " ", remainder, flags=re.IGNORECASE)
    remainder = re.sub(r"\b(?:pande|upande|sehemu)\s+(?:za|ya|wa)?\s*mbili\b", " ", remainder, flags=re.IGNORECASE)
    remainder = re.sub(r"\b(?:njuyi|enjuyi|empande)\s+(?:zombi|z'ebbiri)\b", " ", remainder, flags=re.IGNORECASE)
    remainder = re.sub(r"\bthird[-\s]part(?:y|ies)\b", " ", remainder, flags=re.IGNORECASE)
    # Strip legal references (e.g. Cap 340, Cap. 343, Section 118, Article VII) so statute chapters are not parsed as monetary rules
    remainder = re.sub(
        r"\b(?:cap\.?|chapter|section|sura|kifungu|katundu|article)\s*(?:\d+|[IVXLCDM]+)\b",
        " ",
        remainder,
        flags=re.IGNORECASE,
    )

    # 2. Standard suffixes (e.g. "150m", "150 million", "UGX 150,000,000") and plain numbers
    for match in _AMOUNT_RE.finditer(remainder):
        digits = match.group(1).replace(",", "").replace(" ", "")
        try:
            value = float(digits)
        except ValueError:
            continue
        value *= _AMOUNT_SUFFIX.get((match.group(2) or "").lower(), 1)
        amounts.add(value)

    # 3. Small cardinal integers across English, Luganda, and Swahili
    _CARDINAL_WORDS = {
        # English
        "two": 2.0, "three": 3.0, "four": 4.0, "five": 5.0, "six": 6.0,
        "seven": 7.0, "eight": 8.0, "nine": 9.0, "ten": 10.0,
        "twenty": 20.0, "thirty": 30.0, "forty": 40.0, "fifty": 50.0,
        # Luganda & Swahili
        "munaana": 8.0, "minane": 8.0, "nane": 8.0, "omunaana": 8.0, "musanvu": 7.0, "omusanvu": 7.0,
        "mukaaga": 6.0, "sita": 6.0, "omukaaga": 6.0, "ttaano": 5.0, "taano": 5.0, "tano": 5.0, "etaano": 5.0, "ettaano": 5.0, "ebitaano": 5.0,
        "nnya": 4.0, "nne": 4.0, "ennya": 4.0, "bana": 4.0, "ssatu": 3.0, "essatu": 3.0, "tatu": 3.0, "esatu": 3.0, "ebisatu": 3.0,
        "bbiri": 2.0, "ebbiri": 2.0, "mbili": 2.0, "zibiri": 2.0, "ebibiri": 2.0,
        "mwenda": 9.0, "tisa": 9.0, "omwenda": 9.0,
        "kkumi": 10.0, "kumi": 10.0, "ekkumi": 10.0, "asatu": 30.0, "thelathini": 30.0, "abiri": 20.0,
        "ishirini": 20.0, "amakumi ana": 40.0, "arobaini": 40.0, "ataano": 50.0, "hamsini": 50.0,
    }
    for word, val in _CARDINAL_WORDS.items():
        if re.search(rf"\b{word}\b", remainder):
            amounts.add(val)
    # Swahili 'saba' (7) in numeric/counting contexts (avoids Luganda verb 'saba' meaning to apply/request)
    if re.search(r"\b(?:siku|miezi|miaka|asilimia|watu|mara|nambari|namba|bidhaa|kiasi|kiwango|tarehe)\s+saba\b|\bsaba\s+(?:ya|za|wa|kwa)\b", remainder, re.IGNORECASE):
        amounts.add(7.0)
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
        # A claim stating Uganda's official statutory 18% VAT rate does not contradict a withholding or income tax passage
        if model_pct == {"18"} and ("vat" in claim.lower() or "value added" in claim.lower()):
            pass
        else:
            def _tax_head(text: str) -> str | None:
                tl = text.lower()
                if "vat" in tl or "value added" in tl:
                    return "vat"
                if "withholding" in tl or "wht" in tl:
                    return "wht"
                if "paye" in tl or "pay as you earn" in tl or "employment" in tl:
                    return "paye"
                if "rental" in tl:
                    return "rental"
                if "corporation" in tl or "corporate" in tl:
                    return "corporation"
                if "environmental" in tl or "levy" in tl:
                    return "environmental_levy"
                if "import duty" in tl or "customs duty" in tl or "external tariff" in tl or "cet" in tl:
                    return "import_duty"
                if "infrastructure" in tl or "infrastructural" in tl:
                    return "infrastructure"
                if "excise" in tl:
                    return "excise"
                if "stamp duty" in tl:
                    return "stamp_duty"
                if "digital service" in tl or "dst" in tl or "electronic service" in tl:
                    return "digital_service"
                return None

            ch = _tax_head(claim)
            xh = _tax_head(context)
            # Only compare if they are about the same tax head (or neither specifies one)
            if ch and xh and ch != xh:
                pass
            elif ch and not xh and model_pct.issubset({"18", "6", "15", "25", "30", "35", "50", "5", "1.5", "2", "0.5", "0.4", "0.6", "0.7", "10", "12"}):
                pass
            else:
                claim_words = set(re.findall(r"\w{3,}", claim.lower()))
                context_words = set(re.findall(r"\w{3,}", context.lower()))
                shared = (claim_words & context_words) - {
                    "that", "with", "from", "this", "have", "were", "will", "your", "under",
                    "tax", "taxes", "standard", "services", "goods", "amount", "payment",
                    "vehicle", "motor", "used", "imported", "import", "customs", "duty", "levy", "value",
                }
                if shared or ("rate" in claim_words and "rate" in context_words):
                    return True

    if not _RULE_CUE_RE.search(claim):
        return False
    ca = canonical_amounts(claim)
    xa = canonical_amounts(context)
    qa = canonical_amounts(user_query) if user_query else set()
    model_amounts = ca - qa
    ca_money = {a for a in model_amounts if a >= 1000.0}
    xa_money = {a for a in xa if a >= 1000.0}
    if ca_money and xa_money and ca_money.isdisjoint(xa_money):
        def _rule_subjects(text: str) -> set[str]:
            tl = text.lower()
            res = set()
            if "audit" in tl or "audited" in tl or "accountant" in tl:
                res.add("audit")
            if "vat" in tl or "value added" in tl:
                res.add("vat")
            if "presumptive" in tl or "small business" in tl:
                res.add("presumptive")
            if "paye" in tl or "salary" in tl or "wage" in tl:
                res.add("paye")
            if "withholding" in tl or "wht" in tl or "withhold" in tl:
                res.add("wht")
            if "ngo" in tl or "non-governmental" in tl or "charit" in tl:
                res.add("ngo")
            return res

        cs = _rule_subjects(claim)
        xs = _rule_subjects(context)
        if cs and xs and cs.isdisjoint(xs):
            pass  # Different legal rules (e.g. 500m audit vs 150m VAT registration)
        else:
            claim_words = set(re.findall(r"\w{3,}", claim.lower()))
            context_words = set(re.findall(r"\w{3,}", context.lower()))
            shared = (claim_words & context_words) - {
                "that", "with", "from", "this", "have", "were", "will", "your", "under",
                "tax", "rate", "rates", "taxes", "standard", "services", "goods", "amount", "payment",
                "return", "returns", "filing", "file", "period", "month", "monthly", "annual", "year",
                "turnover", "gross", "income", "shillings", "person", "business", "taxpayer", "taxpayers",
            }
            if shared:
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

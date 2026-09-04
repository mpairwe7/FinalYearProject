"""Epistemic false-premise validation guard for fictitious statutory instruments (G43).

Detects queries that presuppose the existence of a non-existent tax, levy,
duty, or statutory fee (e.g. "URA Digital Nomad Levy", "Moon Tax", "Uganda
Crypto Luxury Duty") where neither URA statutory rate tables nor the retrieved
corpus mention the concept. Rejects them affirmatively rather than permitting
the LLM to synthesize authoritative-looking hallucinated rules and figures.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FalsePremiseResult:
    is_false_premise: bool
    concept: str = ""
    reply: str = ""


# Legitimate Ugandan tax categories and common modifier terms that should NOT
# be considered false premises.
_LEGITIMATE_TAX_MODIFIERS = frozenset({
    "income",
    "individual income",
    "employment income",
    "business income",
    "paye",
    "pay as you earn",
    "vat",
    "value added",
    "value-added",
    "corporation",
    "corporate",
    "company",
    "cit",
    "rental",
    "rental income",
    "capital gains",
    "cgt",
    "withholding",
    "wht",
    "withholding vat",
    "customs",
    "customs duty",
    "import",
    "import duty",
    "export",
    "export levy",
    "common external tariff",
    "cet",
    "excise",
    "excise duty",
    "local excise",
    "stamp",
    "stamp duty",
    "environmental",
    "environmental levy",
    "infrastructure",
    "infrastructure levy",
    "presumptive",
    "small business",
    "gaming",
    "lottery",
    "betting",
    "casino",
    "local service",
    "lst",
    "hotel",
    "local government",
    "property",
    "property rates",
    "land",
    "land rates",
    "motor vehicle",
    "vehicle",
    "advance",
    "advance tax",
    "digital services",
    "dst",
    "over the top",
    "ott",
    "social media",
    "road",
    "road user",
    "toll",
    "petroleum",
    "fuel",
    "mining",
    "mineral",
    "direct",
    "indirect",
    "turnover",
    "dividend",
    "interest",
    "royalty",
    "penalty",
    "penalties",
    "fines",
    "fine",
    "interest surcharge",
    "carbon",
    "commercial",
    "residential",
})

# Words that indicate actions, grammar, prepositions, and conversational intent
# rather than an asserted specific tax modifier.
_STOP_AND_ACTION_WORDS = frozenset({
    # Actions & verbs
    "pay", "paying", "paid", "file", "filing", "filed", "register", "registering", "registered",
    "declare", "declaring", "declared", "submit", "submitting", "submitted", "calculate",
    "calculating", "calculated", "compute", "computing", "computed", "deduct", "deducting",
    "deducted", "charge", "charging", "charged", "apply", "applying", "applied", "collect",
    "collecting", "collected", "remit", "remitting", "remitted", "avoid", "evade", "evading",
    "learn", "know", "see", "show", "tell", "check", "verify", "get", "got", "help", "want",
    "need", "wish", "like", "prefer", "ask", "start", "stop", "resume",
    # Modals and auxiliaries
    "can", "could", "would", "should", "may", "might", "must", "shall", "will",
    "do", "does", "did", "have", "has", "had", "am", "is", "are", "was", "were", "be", "been", "being",
    # Pronouns
    "i", "me", "my", "mine", "we", "us", "our", "ours", "you", "your", "yours",
    "he", "him", "his", "she", "her", "hers", "it", "its", "they", "them", "their", "theirs",
    "one", "someone", "anyone", "everyone", "who", "whom", "whose", "what", "which",
    # Prepositions and adverbs
    "how", "why", "when", "where", "to", "for", "about", "on", "in", "at", "by", "from", "of",
    "with", "into", "through", "during", "before", "after", "above", "below", "under", "up",
    "down", "off", "over", "again", "further", "then", "once", "here", "there", "all", "any",
    "both", "each", "few", "more", "most", "other", "some", "such", "no", "nor", "not", "only",
    "own", "same", "so", "than", "too", "very", "s", "t", "just", "don", "now", "as", "per",
    # Generic context nouns & qualifiers
    "online", "offline", "portal", "ura", "uganda", "ugandan", "government", "official",
    "law", "laws", "act", "acts", "policy", "rate", "rates", "table", "tables", "system",
    "code", "due", "payable", "applicable", "pending", "outstanding", "total", "standard",
    "general", "statutory", "new", "current", "annual", "monthly", "year", "years", "month",
    "the", "a", "an", "this", "that", "these", "those",
})

_CANDIDATE_PATTERNS = (
    re.compile(
        r"\b(?P<modifier>[a-zA-Z0-9\s\-]{2,40}?)\s+"
        r"(?P<kind>tax|taxes|levy|levies|duty|duties|tariff|tariffs|surcharge|surcharges)\b",
        re.IGNORECASE,
    ),
)


_INTERROGATIVE_PREFIX = re.compile(
    r"^(?:is\s+there(?:\s+an?|\s+any)?|are\s+there(?:\s+any)?|was\s+there|were\s+there|"
    r"what\s+is(?:\s+an?|\s+the)?|what\s+are(?:\s+the)?|tell\s+me\s+about(?:\s+the)?)\s+",
    re.IGNORECASE,
)


def _extract_candidate_tax_concepts(query: str) -> list[tuple[str, str]]:
    """Extract candidate (modifier, kind) pairs from *query*."""
    text = (query or "").strip()
    # Consume recognized interrogative prefixes so they never bleed into modifiers
    normalized_text = _INTERROGATIVE_PREFIX.sub("", text)
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()

    for target in (normalized_text, text):
        for pattern in _CANDIDATE_PATTERNS:
            for match in pattern.finditer(target):
                raw_mod = match.group("modifier").strip()
                kind = match.group("kind").strip().lower()
                # Normalize kind to singular for comparison
                if kind.endswith("ies"):
                    base_kind = kind[:-3] + "y"
                elif kind.endswith("es") and kind not in ("taxes",):
                    base_kind = kind[:-2]
                elif kind.endswith("s"):
                    base_kind = kind[:-1]
                else:
                    base_kind = kind

                words = [w.lower() for w in re.split(r"[\s\-]+", raw_mod) if w]
                # Scan backwards from the noun to isolate the contiguous modifier words
                # that qualify the tax name (stopping at action verbs, modals, or pronouns)
                modifier_words = []
                for w in reversed(words):
                    if w in _STOP_AND_ACTION_WORDS:
                        break
                    modifier_words.insert(0, w)

                if not modifier_words:
                    continue

                cleaned_mod = " ".join(modifier_words)
                if cleaned_mod in seen or len(cleaned_mod) < 2:
                    continue
                seen.add(cleaned_mod)
                candidates.append((cleaned_mod, base_kind))
    return candidates


def check_false_premise(
    query: str,
    hits: list[dict[str, Any]] | None = None,
) -> FalsePremiseResult:
    """Validate whether *query* presupposes a non-existent statutory tax/levy.

    Returns a FalsePremiseResult with is_false_premise=True if an asserted tax
    or levy is not known in Ugandan tax law and completely absent from the
    retrieved knowledge base.
    """
    candidates = _extract_candidate_tax_concepts(query)
    if not candidates:
        return FalsePremiseResult(is_false_premise=False)

    corpus_text = ""
    if hits:
        corpus_parts = []
        for h in hits:
            corpus_parts.append(str(h.get("text") or h.get("answer") or ""))
            corpus_parts.append(str(h.get("question") or ""))
            corpus_parts.append(str(h.get("source") or ""))
        corpus_text = " ".join(corpus_parts).lower()

    for clean_mod, kind in candidates:
        # Check against legitimate taxes
        if clean_mod in _LEGITIMATE_TAX_MODIFIERS:
            continue

        # Check whether the exact concept phrase appears
        # in the retrieved corpus or official rate tables
        found_in_corpus = (
            clean_mod in corpus_text
            or f"{clean_mod} {kind}" in corpus_text
        )
        if found_in_corpus:
            continue

        # Concept is not a known Ugandan tax and absent from retrieved passages
        words_cap = " ".join(w.capitalize() for w in clean_mod.split())
        concept_display = f"{words_cap} {kind.capitalize()}"

        # Tailor the guidance depending on whether the query asks about remote/digital work
        is_digital_remote = any(
            term in clean_mod for term in ("digital nomad", "nomad", "remote", "freelanc")
        ) or any(term in (query or "").lower() for term in ("digital nomad", "nomad"))

        if is_digital_remote:
            reply = (
                f"Under current Ugandan tax laws and Uganda Revenue Authority (URA) regulations, "
                f"there is no official \"{concept_display}\". The URA does not administer or impose a specific tax by this name.\n\n"
                f"If you are earning income in Uganda (including remote employment, digital freelancing, or foreign-sourced income), "
                f"standard income tax provisions apply based on your tax residency status. "
                f"Please consult an official URA office or tax advisor for guidance on your specific obligations."
            )
        else:
            reply = (
                f"Under current Ugandan tax laws and Uganda Revenue Authority (URA) regulations, "
                f"there is no official \"{concept_display}\". The URA does not administer or impose such a tax or levy.\n\n"
                f"Legitimate statutory tax heads administered by the URA include Income Tax (PAYE, Corporate Income Tax, Presumptive Tax), "
                f"Value Added Tax (VAT), Withholding Tax (WHT), Rental Tax, Stamp Duty, and Customs and Excise Duties. "
                f"Please consult official URA guidelines or an authorized tax officer for statutory requirements."
            )

        safe_query = query[:120].replace("\r", " ").replace("\n", " ").strip()
        safe_concept = concept_display.replace("\r", " ").replace("\n", " ").strip()
        logger.warning(
            "epistemic false-premise guard triggered for query '%s' (concept: %s)",
            safe_query,
            safe_concept,
        )
        return FalsePremiseResult(
            is_false_premise=True,
            concept=concept_display,
            reply=reply,
        )

    return FalsePremiseResult(is_false_premise=False)

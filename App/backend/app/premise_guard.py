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
    "hotel",
    "local government",
    "motor vehicle",
    "advance",
    "digital services",
    "dst",
    "over the top",
    "ott",
    "social media",
})

# Words that indicate generic tax questions rather than a specific named tax instrument
_GENERIC_WORDS = frozenset({
    "the",
    "a",
    "an",
    "my",
    "our",
    "your",
    "any",
    "this",
    "that",
    "ura",
    "uganda",
    "ugandan",
    "new",
    "current",
    "annual",
    "monthly",
    "direct",
    "indirect",
    "total",
    "applicable",
    "payable",
    "pending",
    "outstanding",
    "standard",
    "general",
    "statutory",
    "official",
    "government",
    "local",
    "national",
    "rates",
    "rate",
    "table",
    "tables",
    "policy",
    "act",
    "law",
    "laws",
})

_CANDIDATE_PATTERNS = (
    re.compile(
        r"\b(?:what\s+is|how\s+(?:much\s+is|do\s+i\s+pay|to\s+pay)|who\s+pays|rate\s+for|calculate|file)\s+"
        r"(?:the\s+)?(?:ura\s+)?(?P<modifier>[a-zA-Z0-9\s\-]{3,35}?)\s+"
        r"(?P<kind>tax|levy|duty|tariff|surcharge|charge)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:ura\s+)?(?P<modifier>[a-zA-Z0-9\s\-]{3,35}?)\s+"
        r"(?P<kind>tax|levy|duty|tariff|surcharge)\b",
        re.IGNORECASE,
    ),
)


def _extract_candidate_tax_concepts(query: str) -> list[tuple[str, str]]:
    """Extract candidate (modifier, kind) pairs from *query*."""
    text = (query or "").strip()
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()

    for pattern in _CANDIDATE_PATTERNS:
        for match in pattern.finditer(text):
            raw_mod = match.group("modifier").strip()
            kind = match.group("kind").strip().lower()
            # Clean modifier words
            mod_words = [
                w.lower()
                for w in re.split(r"[\s\-]+", raw_mod)
                if w and w.lower() not in _GENERIC_WORDS
            ]
            if not mod_words:
                continue
            cleaned_mod = " ".join(mod_words)
            if cleaned_mod in seen or len(cleaned_mod) < 3:
                continue
            seen.add(cleaned_mod)
            candidates.append((raw_mod, kind))
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

    for raw_mod, kind in candidates:
        mod_lower = raw_mod.lower().strip()
        words = [
            w for w in re.split(r"[\s\-]+", mod_lower)
            if w and w not in _GENERIC_WORDS
        ]
        if not words:
            continue

        clean_mod = " ".join(words)
        # Check against legitimate taxes
        if clean_mod in _LEGITIMATE_TAX_MODIFIERS:
            continue
        if any(clean_mod == leg or clean_mod.endswith(f" {leg}") for leg in _LEGITIMATE_TAX_MODIFIERS):
            continue

        # Check whether any distinctive word from the asserted concept appears
        # in the retrieved corpus or official rate tables
        found_in_corpus = any(w in corpus_text for w in words if len(w) >= 4)
        if found_in_corpus:
            continue

        # Concept is not a known Ugandan tax and absent from retrieved passages
        concept_display = f"{raw_mod.strip()} {kind.capitalize()}"
        reply = (
            f"Under current Ugandan tax laws and Uganda Revenue Authority (URA) regulations, "
            f"there is no official \"{concept_display}\". The URA does not administer or impose such a tax or levy.\n\n"
            f"If you are earning income in Uganda (including remote employment, digital freelancing, or foreign-sourced income), "
            f"standard income tax provisions apply based on your tax residency status. "
            f"Please consult an official URA office or tax advisor for guidance on your specific obligations."
        )
        logger.warning(
            "epistemic false-premise guard triggered for query '%s' (concept: %s)",
            query[:120],
            concept_display,
        )
        return FalsePremiseResult(
            is_false_premise=True,
            concept=concept_display,
            reply=reply,
        )

    return FalsePremiseResult(is_false_premise=False)

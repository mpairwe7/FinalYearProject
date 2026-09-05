"""Deterministic claim-level citation checks for RAG answers.

This is intentionally conservative. It is not a legal/semantic NLI model; it
checks whether each factual-looking sentence is visibly cited and lexically
supported by its cited passage. Low confidence becomes a revise/escalate signal
instead of allowing unsupported policy claims to pass silently.
"""

from __future__ import annotations

import os
import re
from typing import Any

from .entailment import canonical_amounts, is_contradicted, percentages
from .text_signals import is_courtesy_sentence

_CITATION_RE = re.compile(r"\[(\d{1,3})\]")
_SENTENCE_RE = re.compile(r"[^.!?\n]+(?:[.!?]+|$)")
_WORD_RE = re.compile(r"[a-zA-Z0-9]+")
_MIN_SUPPORT = float(os.getenv("CLAIM_VERIFIER_MIN_SUPPORT", "0.32"))
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "for",
    "from",
    "i",
    "if",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "with",
    "you",
    "your",
}
_NON_CLAIM_HINTS = (
    "based on the ura guidance",
    "i could not find",
    "please contact",
    "please try",
    "i don't have enough",
    # The uncontracted form the models actually emit. "i don't have enough" was
    # already exempt; a refusal written "I do not have enough information in the
    # provided context" was not, so the model's own refusal counted as an
    # uncited factual claim against it.
    "do not have enough",
    "ask a new",
    "you might also want to know",
    "you may also want to",
    "would you also like to know",
    "for more details, visit",
    "for more information, visit",
    "contact the ura contact centre",
    "visit https://ura",
    "call ura toll-free",
    # Statements ABOUT the context rather than about tax. A model instructed to
    # answer only from the passages says so when they run out, and that
    # disclaimer is exactly the behaviour we asked for — but it asserts nothing
    # citable, so it scored as an uncited, zero-overlap claim and dragged an
    # otherwise fully-cited answer to "revise". Measured in production: a reply
    # whose two substantive claims both carried correct refs still failed at
    # score 0.667 on the single sentence "Please note that the provided context
    # does not contain additional specific details regarding small traders."
    # This is the same category the hints above already exempt (a request to
    # contact URA is not a factual claim either), not a new leniency.
    "the provided context does not contain",
    "the provided context does not include",
    "the context does not contain",
    "the context does not include",
    "the provided information does not",
    "does not contain additional",
    "is not covered in the provided",
    "not specified in the provided",
    "no information about",
    "this response may not be fully supported",
    "verify with official ura sources",
    "verify with official ura",
    "verify with ura",
    "official ura sources at https://ura",
    "these services are crucial",
    "essential to the country",
    "here is the official guidance",
    "here is what you need to know",
    "services@ura.go.ug",
    "info@ura.go.ug",
)


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _WORD_RE.findall(text) if t.lower() not in _STOPWORDS}


def _numbers(text: str) -> set[str]:
    """Figures in *text*, normalised so containment is meaningful.

    Percentages stay as written ("18%"); money is canonicalised to its
    value, so "UGX 1,500,000" is the single figure ``1500000.0`` rather
    than the three fragments ``1``/``500``/``000`` a bare digit-run regex
    produced.  Those fragments matched nearly any passage containing a
    grouped number, which is how unsupported money claims scored as
    supported.
    """
    lowered = (text or "").lower()
    # Fixed-point, not "%g": six significant digits would collapse
    # 1,234,567 and 1,234,568 onto the same key and call them equal.
    figures = {f"{value:.2f}" for value in canonical_amounts(lowered)}
    figures.update(f"{pct}%" for pct in percentages(lowered))
    return figures


def _split_claims(reply: str) -> list[str]:
    claims: list[str] = []
    # Protect decimal points before sentence splitting.  FAQ answers commonly
    # contain figures such as ``37.5m``; treating that period as a sentence
    # boundary creates two truncated, apparently unsupported claims.
    text = re.sub(r"(?<=\d)\.(?=\d)", "<decimal_point>", reply or "")
    text = re.sub(r"([.!?])\s+(\[\d{1,3}\])", r" \2\1", text)
    for raw in _SENTENCE_RE.findall(text):
        sentence = " ".join(raw.strip(" -\t\r\n").split())
        sentence = sentence.replace("<decimal_point>", ".")
        if len(sentence) < 18:
            continue
        lowered = sentence.lower()
        if any(hint in lowered for hint in _NON_CLAIM_HINTS):
            continue
        if is_courtesy_sentence(sentence):
            continue
        if re.search(r"0800\s?117\s?000|0800\s?217\s?000|0772\s?140\s?000|services@ura\.go\.ug|info@ura\.go\.ug|ura\.go\.ug", lowered):
            continue
        if len(_tokens(sentence)) < 3:
            continue
        claims.append(sentence)
    return claims


def _citation_contexts(
    refs: list[str],
    citations: list[dict[str, Any]],
    hits: list[dict[str, Any]],
) -> list[str]:
    by_ref: dict[str, str] = {}
    for idx, citation in enumerate(citations or [], 1):
        ref = str(citation.get("ref") or f"[{idx}]").strip("[]")
        passage = str(citation.get("passage") or "").strip()
        if not passage and idx - 1 < len(hits or []):
            hit = hits[idx - 1]
            passage = str(hit.get("text") or hit.get("answer") or "").strip()
        by_ref[ref] = passage

    contexts = [by_ref[ref] for ref in refs if by_ref.get(ref)]
    if contexts:
        return contexts
    return [str(h.get("text") or h.get("answer") or "") for h in hits or [] if h]


def verify_claims(
    reply: str,
    citations: list[dict[str, Any]] | None,
    hits: list[dict[str, Any]] | None,
    *,
    min_support: float | None = None,
    query: str = "",
) -> dict[str, Any]:
    """Return a claim-verification report for a draft answer."""
    threshold = _MIN_SUPPORT if min_support is None else min_support
    claims = _split_claims(reply)
    report: dict[str, Any] = {
        "decision": "approve",
        "score": 1.0,
        "claim_count": len(claims),
        "supported_claim_count": 0,
        "uncited_claims": [],
        "unsupported_claims": [],
        "contradicted_claims": [],
        "backend": "deterministic_overlap_v1",
    }
    if not claims:
        return report

    has_grounding = bool(citations or hits)
    if not has_grounding:
        report["decision"] = "escalate"
        report["score"] = 0.0
        report["unsupported_claims"] = [
            {"text": claim[:220], "reason": "no retrieved passages available"} for claim in claims
        ]
        return report

    supported = 0
    for claim in claims:
        refs = _CITATION_RE.findall(claim)
        clean_claim = _CITATION_RE.sub("", claim)
        contexts = _citation_contexts(refs, citations or [], hits or [])
        claim_tokens = _tokens(clean_claim)
        context_text = " ".join(contexts)
        context_tokens = _tokens(context_text)
        overlap = len(claim_tokens & context_tokens) / max(1, len(claim_tokens))

        claim_numbers = _numbers(clean_claim)
        context_numbers = _numbers(context_text)
        query_numbers = _numbers(query) if query else set()
        model_introduced_numbers = claim_numbers - query_numbers
        if model_introduced_numbers and not model_introduced_numbers <= context_numbers:
            overlap = min(overlap, 0.25)

        # P1-8: a claim whose percentage conflicts with the cited context is a
        # hard contradiction (e.g. answer "20%" vs source "18%") — force it
        # unsupported so the response judge escalates rather than disclaiming.
        contradicted = is_contradicted(clean_claim, contexts, user_query=query)
        if contradicted:
            overlap = 0.0

        item = {
            "text": clean_claim.strip()[:220],
            "refs": [f"[{ref}]" for ref in refs],
            "support_score": round(overlap, 4),
        }
        if not refs and citations:
            report["uncited_claims"].append(item)
        if overlap >= threshold:
            supported += 1
        else:
            report["unsupported_claims"].append(item)
            if contradicted:
                report["contradicted_claims"].append(item)

    report["supported_claim_count"] = supported
    report["score"] = round(supported / len(claims), 4)

    if report["contradicted_claims"]:
        report["decision"] = "escalate"
    elif report["unsupported_claims"]:
        report["decision"] = "escalate" if report["score"] < 0.5 else "revise"
    elif report["uncited_claims"]:
        report["decision"] = "revise"
    return report

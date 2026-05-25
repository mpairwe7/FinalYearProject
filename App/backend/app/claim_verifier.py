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
    "ask a new",
    "you might also want to know",
    "you may also want to",
    "would you also like to know",
    "for more details, visit",
    "for more information, visit",
    "contact the ura contact centre",
    "visit https://ura",
    "call ura toll-free",
)


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _WORD_RE.findall(text) if t.lower() not in _STOPWORDS}


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?%?", text.lower()))


def _split_claims(reply: str) -> list[str]:
    claims: list[str] = []
    text = re.sub(r"([.!?])\s+(\[\d{1,3}\])", r" \2\1", reply or "")
    for raw in _SENTENCE_RE.findall(text):
        sentence = " ".join(raw.strip(" -\t\r\n").split())
        if len(sentence) < 18:
            continue
        lowered = sentence.lower()
        if any(hint in lowered for hint in _NON_CLAIM_HINTS):
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
        if claim_numbers and not claim_numbers <= context_numbers:
            overlap = min(overlap, 0.25)

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

    report["supported_claim_count"] = supported
    report["score"] = round(supported / len(claims), 4)

    if report["unsupported_claims"]:
        report["decision"] = "escalate" if report["score"] < 0.5 else "revise"
    elif report["uncited_claims"]:
        report["decision"] = "revise"
    return report

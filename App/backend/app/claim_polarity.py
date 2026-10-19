"""Polarity-aware grounding and contradiction detection for tax claims (Phase G.1).

Overlap-only faithfulness and claim verification miss directional inversions where
a sentence shares nearly every content word with its supporting passage or verified
fact but reverses the legal direction (e.g. D6: stating a housing benefit is
"subtracted from" gross salary when the handbook says it is "added to / forms part
of" taxable employment income).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

_WORD_RE = re.compile(r"[a-zA-Z0-9]+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")

_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for",
    "from", "has", "have", "in", "is", "it", "its", "may", "must", "not", "of",
    "on", "or", "shall", "should", "that", "the", "their", "this", "to", "under",
    "ura", "uganda", "was", "were", "when", "which", "who", "will", "with", "would",
    "you", "your", "added", "included", "subtracted", "deducted", "excluded",
    "exempt", "taxable", "allowed", "disallowed", "claimable", "lesser", "lower",
    "greater", "higher", "before", "after", "within", "increase", "increases",
    "decrease", "decreases", "reduces",
})


@dataclass(frozen=True)
class PolarityPair:
    """An opposing pair of tax-direction phrase sets."""

    name: str
    positive_cues: tuple[str, ...]
    negative_cues: tuple[str, ...]


@dataclass(frozen=True)
class Conflict:
    """A detected polarity conflict between a reply sentence and grounding text."""

    pair_name: str
    reply_sentence: str
    supporting_sentence: str
    reply_cue: str
    supporting_cue: str
    shared_tokens: tuple[str, ...]


# Curated lexicon of >= 20 opposing tax-direction pairs (§9.G.1).
DIRECTION_LEXICON: tuple[PolarityPair, ...] = (
    PolarityPair(
        name="inclusion_vs_subtraction",
        positive_cues=(
            "added to",
            "included in",
            "forms part of",
            "form part of",
            "part of taxable employment income",
            "part of gross employment income",
            "increases taxable employment income",
        ),
        negative_cues=(
            "subtracted from",
            "deducted from",
            "reduces gross salary",
            "reduces taxable employment income",
        ),
    ),
    PolarityPair(
        name="taxable_vs_exempt",
        positive_cues=(
            "is taxable",
            "are taxable",
            "subject to tax",
            "subject to paye",
            "subject to vat",
            "attracts vat",
            "attracts tax",
        ),
        negative_cues=(
            "is exempt",
            "are exempt",
            "not taxable",
            "exempt from tax",
            "exempt from paye",
            "exempt from vat",
        ),
    ),
    PolarityPair(
        name="resident_vs_non_resident",
        positive_cues=(
            "is a resident",
            "is a tax resident",
            "is a ugandan tax resident",
            "treated as a resident",
            "treated as resident",
            "qualifies as a resident",
        ),
        negative_cues=(
            "is a non-resident",
            "is a non resident",
            "treated as a non-resident",
            "treated as non-resident",
            "not a tax resident",
            "not a resident",
        ),
    ),
    PolarityPair(
        name="allowed_vs_disallowed_deduction",
        positive_cues=(
            "is deductible",
            "are deductible",
            "is allowed as a deduction",
            "are allowed to deduct",
            "can deduct",
            "may deduct",
        ),
        negative_cues=(
            "not deductible",
            "is disallowed",
            "are disallowed",
            "cannot deduct",
            "not allowed to deduct",
            "zero expense deductions",
        ),
    ),
    PolarityPair(
        name="claimable_vs_not_claimable",
        positive_cues=(
            "is claimable",
            "can claim input",
            "eligible to claim",
            "entitled to claim",
            "may claim input",
        ),
        negative_cues=(
            "not claimable",
            "cannot claim",
            "ineligible to claim",
            "not entitled to claim",
            "cannot be claimed",
        ),
    ),
    PolarityPair(
        name="lesser_vs_greater",
        positive_cues=("lesser of", "lower of"),
        negative_cues=("greater of", "higher of"),
    ),
    PolarityPair(
        name="before_vs_after_tax",
        positive_cues=("before paye", "before tax", "deducted before paye"),
        negative_cues=("after paye", "after tax", "deducted after paye"),
    ),
    PolarityPair(
        name="increase_vs_decrease",
        positive_cues=("increases the tax", "increases chargeable", "increases taxable"),
        negative_cues=("decreases the tax", "decreases chargeable", "decreases taxable"),
    ),
    PolarityPair(
        name="compulsory_vs_voluntary_registration",
        positive_cues=(
            "registration is compulsory",
            "registration is mandatory",
            "must register for vat",
            "required to register for vat",
        ),
        negative_cues=(
            "registration is not compulsory",
            "not required to register",
            "need not register",
            "voluntary registration is available",
        ),
    ),
    PolarityPair(
        name="within_vs_after_45_days",
        positive_cues=("within 45 days", "within forty-five days"),
        negative_cues=("after 45 days", "after forty-five days"),
    ),
    PolarityPair(
        name="within_vs_after_30_days",
        positive_cues=("within 30 days", "within thirty days"),
        negative_cues=("after 30 days", "after thirty days"),
    ),
    PolarityPair(
        name="within_vs_after_15_days",
        positive_cues=("within 15 days", "by the 15th"),
        negative_cues=("after 15 days", "after the 15th"),
    ),
    PolarityPair(
        name="inclusive_vs_exclusive_vat",
        positive_cues=("vat-inclusive", "inclusive of vat", "includes vat"),
        negative_cues=("vat-exclusive", "exclusive of vat", "excludes vat"),
    ),
    PolarityPair(
        name="gross_vs_net_rental_base",
        positive_cues=("12% of gross", "on gross rent", "on gross annual rent"),
        negative_cues=("12% of net", "on net rent", "after deducting expenses from individual"),
    ),
    PolarityPair(
        name="final_vs_creditable_wht",
        positive_cues=("is a final tax", "final withholding tax"),
        negative_cues=("not a final tax", "creditable against", "advance tax credit"),
    ),
    PolarityPair(
        name="zero_rated_vs_exempt_vat",
        positive_cues=("zero-rated", "zero rated", "taxed at 0%"),
        negative_cues=("exempt supply", "exempt supplies"),
    ),
    PolarityPair(
        name="prohibited_vs_permitted_import",
        positive_cues=("are prohibited", "is prohibited", "banned from import"),
        negative_cues=("are permitted", "is permitted", "allowed for import"),
    ),
    PolarityPair(
        name="worldwide_vs_uganda_sourced",
        positive_cues=("worldwide income", "both domestic and foreign"),
        negative_cues=("only uganda-sourced income", "only income sourced in uganda"),
    ),
    PolarityPair(
        name="service_vs_employment_contract_wht",
        positive_cues=(
            "includes an employment contract",
            "applies to an employment contract",
            "deduct 15% wht from salary",
        ),
        negative_cues=(
            "does not include an employment contract",
            "excludes employment contracts",
            "not subject to 15% service wht",
        ),
    ),
    PolarityPair(
        name="reducing_balance_vs_straight_line",
        positive_cues=("reducing balance", "declining balance"),
        negative_cues=("straight-line", "straight line"),
    ),
    PolarityPair(
        name="progressive_vs_flat_rate_paye",
        positive_cues=("progressive bands", "progressive tax", "marginal bands"),
        negative_cues=("simplified flat rate", "single flat rate", "flat rate for residents"),
    ),
    PolarityPair(
        name="free_vs_fee_tin",
        positive_cues=("free of charge", "is free", "at no cost", "no fee"),
        negative_cues=("application fee of", "charges a fee", "registration fee for a tin"),
    ),
)


def _subject_tokens(text: str) -> set[str]:
    return {
        w.lower()
        for w in _WORD_RE.findall(text or "")
        if len(w) > 2 and w.lower() not in _STOPWORDS and not w.isdigit()
    }


def _match_cue(lowered: str, cues: tuple[str, ...]) -> str | None:
    for cue in cues:
        idx = lowered.find(cue)
        if idx != -1:
            # Ensure the cue itself isn't immediately negated ("not subtracted from")
            prefix = lowered[max(0, idx - 18) : idx]
            if not cue.startswith(("not ", "cannot ", "no ", "does not ", "need not ")) and re.search(
                r"\b(?:not|never|neither|nor|cannot|can't|doesn't|does\s+not|isn't|aren't)\s+$",
                prefix,
            ):
                continue
            return cue
    return None


def _sentence_pole(lowered: str, pair: PolarityPair) -> tuple[str | None, str | None]:
    """Return ('positive', matched_cue) or ('negative', matched_cue) when unambiguous."""
    pos = _match_cue(lowered, pair.positive_cues)
    neg = _match_cue(lowered, pair.negative_cues)
    if pos and not neg:
        return "positive", pos
    if neg and not pos:
        return "negative", neg
    return None, None


def _iter_reference_sentences(
    passages: Iterable[str] | None,
    verified_facts: str | Iterable[str] | None,
) -> list[str]:
    refs: list[str] = []
    for p in passages or []:
        if p:
            refs.extend(s.strip() for s in _SENTENCE_SPLIT_RE.split(str(p)) if s.strip())
    if isinstance(verified_facts, str) and verified_facts.strip():
        refs.extend(s.strip() for s in _SENTENCE_SPLIT_RE.split(verified_facts) if s.strip())
    elif verified_facts is not None and not isinstance(verified_facts, str):
        for vf in verified_facts:
            if vf:
                refs.extend(s.strip() for s in _SENTENCE_SPLIT_RE.split(str(vf)) if s.strip())
    return refs


def polarity_conflicts(
    sentence: str,
    passages: Iterable[str] | None = None,
    verified_facts: str | Iterable[str] | None = None,
) -> list[Conflict]:
    """Return all directional polarity conflicts between *sentence* and supporting passages/facts."""
    sent_clean = (sentence or "").strip()
    if not sent_clean:
        return []
    sent_low = sent_clean.lower()
    sent_subj = _subject_tokens(sent_clean)
    if len(sent_subj) < 2:
        return []

    ref_sentences = _iter_reference_sentences(passages, verified_facts)
    if not ref_sentences:
        return []

    conflicts: list[Conflict] = []
    seen_pairs: set[str] = set()

    for pair in DIRECTION_LEXICON:
        s_pole, s_cue = _sentence_pole(sent_low, pair)
        if s_pole is None or s_cue is None:
            continue

        for ref in ref_sentences:
            ref_low = ref.lower()
            r_pole, r_cue = _sentence_pole(ref_low, pair)
            if r_pole is None or r_cue is None or r_pole == s_pole:
                continue
            ref_subj = _subject_tokens(ref)
            shared = sorted(sent_subj & ref_subj)
            if len(shared) >= 2:
                if pair.name not in seen_pairs:
                    seen_pairs.add(pair.name)
                    conflicts.append(
                        Conflict(
                            pair_name=pair.name,
                            reply_sentence=sent_clean,
                            supporting_sentence=ref,
                            reply_cue=s_cue,
                            supporting_cue=r_cue,
                            shared_tokens=tuple(shared),
                        )
                    )
                break

    return conflicts

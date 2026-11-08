"""FAQ binding: aliases, timing evidence and question intent (G486 review, CodeRabbit on #705).

The helpers moved out of ``service.py`` in G486; the review of the move found
three pre-existing defects in what they decide, pinned here.
"""

from __future__ import annotations

import pytest
from app import faq_binding as fb


def test_every_alias_names_a_final_term() -> None:
    aliases = fb._FAQ_TERM_ALIASES
    chained = {k: v for k, v in aliases.items() if v in aliases and aliases[v] != v}

    assert chained == {}


@pytest.mark.parametrize(
    ("vernacular", "english"), [("magari", "vehicles"), ("pikipiki", "motorcycle"), ("nambari", "number plate")]
)
def test_a_vernacular_word_reaches_the_same_term_as_its_english(vernacular: str, english: str) -> None:
    assert fb.faq_terms(vernacular) == fb.faq_terms(english)


DEADLINE = "What is the PAYE deadline?"


@pytest.mark.parametrize(
    "answer",
    [
        "PAYE is paid by the 15th of the following month.",
        "Remit the tax within 30 days of the deduction.",
        "File on or before 30 June each year.",
        "Remit it by the 15th day of the month following the deduction.",
    ],
)
def test_an_explicit_date_is_timing_evidence(answer: str) -> None:
    assert fb.faq_match_score(DEADLINE, {"question": "When is PAYE paid?", "answer": answer}) > 0.0


@pytest.mark.parametrize(
    "answer",
    [
        "PAYE is tax deducted from employment income.",
        "Employers may register for PAYE online.",
        "PAYE is charged on employment income earned each year.",
    ],
)
def test_a_topic_without_a_timing_rule_is_still_refused(answer: str) -> None:
    assert fb.faq_match_score(DEADLINE, {"question": "What is PAYE?", "answer": answer}) == 0.0


@pytest.mark.parametrize(
    ("question", "intent"),
    [
        ("When are capital gains taxed?", "timing"),
        ("How are capital gains taxed?", "procedure"),
        ("How much is the penalty?", "amount"),
        ("How long does a refund take?", "timing"),
        ("Who should register for VAT?", "party"),
        ("What is PAYE?", ""),
        ("When filing income tax returns, how is the advance tax treated?", "procedure"),
        ("If I sell land, when is capital gains tax due?", "timing"),
        ("When are capital gains taxed, and how are they taxed?", "timing"),
    ],
)
def test_question_intent(question: str, intent: str) -> None:
    assert fb.question_intent(question) == intent


def test_when_is_not_answered_by_how() -> None:
    when = {"question": "When are capital gains taxed?", "doc_type": "faq_jsonl"}
    how = {"question": "How are capital gains taxed?", "doc_type": "faq_jsonl"}

    assert fb.faq_question_equivalence("When are capital gains taxed?", when) == 1.0
    assert fb.faq_question_equivalence("When are capital gains taxed?", how) == 0.0
    promoted = fb.promote_equivalent_faq_hits("When are capital gains taxed?", [dict(how), dict(when)])
    assert promoted[0]["question"] == "When are capital gains taxed?"


def test_a_phrase_alias_is_matched_before_splitting_and_keeps_its_subject() -> None:
    """CodeRabbit on #705: "nambari maalum" never reached "personalized"."""
    terms = fb.faq_terms("Bei ya nambari maalum ni kiasi gani?")
    assert {"personalized", "number", "plate"} <= terms

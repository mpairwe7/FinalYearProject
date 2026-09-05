"""The FAQ coverage gate — a guard on the floor, not on the number.

`_FAQ_MATCH_MIN` refuses any FAQ that does not cover enough of the question.
It reads as too strict, especially on translated queries: 7 of the 12 Luganda
golden questions score 0.33-0.57 against a 0.58 floor and are refused, and
"What is EFRIS and how does it work?" lands at 0.57 despite the corpus having
EFRIS entries. The obvious response is to lower it.

Measured, that is the wrong move. Against off-domain questions that borrow
money and government vocabulary — the ones a coverage metric actually confuses,
not "how do I bake banana bread" — the distributions overlap, and so do the
BM25 scores. Dropping the floor to 0.40 took wrong answers from 1 of 6
off-domain questions to 5 of 6, including the president of Uganda answered from
the AEO scheme FAQ. The downstream abstention guard does not catch them.

So these tests pin the behaviour that matters — off-domain questions get
nothing — rather than the constant itself, which stays env-tunable. They fail
if someone lowers the floor far enough to start answering them.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import service  # noqa: E402

# Off-domain, but sharing vocabulary with a tax corpus: pay, apply, business,
# cost, Uganda, government. Plain nonsense is not the interesting case — a
# coverage metric rejects that easily.
OFF_DOMAIN = [
    "Who is the president of Uganda?",
    "How do I pay my rent to my landlord?",
    "What is the best business to start?",
    "How much does a car cost in Uganda?",
    "What is the salary of a teacher in Uganda?",
    "How do I apply for a passport?",
]

FAQ_INDEX = {
    "aeo": [
        {
            "question": "What are the objectives of the Uganda AEO scheme?",
            "answer": (
                "The Authorised Economic Operator scheme secures the supply chain and "
                "gives compliant traders faster clearance in Uganda."
            ),
        }
    ],
    "payments": [
        {
            "question": "How can I pay?",
            "answer": (
                "Pay through banks, mobile money, cards, EFT or RTGS after generating a "
                "payment registration number."
            ),
        }
    ],
    "vat": [
        {
            "question": "Can a business register for VAT voluntarily?",
            "answer": "A business below the threshold may register for VAT voluntarily.",
        }
    ],
}


class TestOffDomainQuestionsAreRefused(unittest.TestCase):
    def test_no_off_domain_question_is_answered_at_the_shipped_floor(self):
        for question in OFF_DOMAIN:
            with self.subTest(question=question):
                self.assertEqual(
                    service._simple_search(question, FAQ_INDEX, top_k=4),
                    [],
                    "an off-domain question reached an FAQ answer",
                )

    def test_the_floor_is_what_refuses_them(self):
        """Guards the claim above: these are refused BY the floor, so a lower
        one would let them through. If this ever stops being true the gate has
        been replaced by something else and the reasoning needs revisiting."""
        original = service._FAQ_MATCH_MIN
        try:
            service._FAQ_MATCH_MIN = 0.40
            leaked = [q for q in OFF_DOMAIN if service._simple_search(q, FAQ_INDEX, top_k=4)]
        finally:
            service._FAQ_MATCH_MIN = original
        self.assertTrue(
            leaked,
            "lowering the floor no longer leaks off-domain answers — re-run the "
            "measurement in the comment above _FAQ_MATCH_MIN before trusting it",
        )

    def test_an_in_domain_question_still_gets_through(self):
        """The gate must not be so tight that nothing survives it."""
        hits = service._simple_search(
            "Can a business register for VAT voluntarily?", FAQ_INDEX, top_k=4
        )
        self.assertTrue(hits)
        self.assertIn("VAT", hits[0]["question"])

    def test_text_embedded_faq_hit_survives_filter_and_is_promoted(self):
        """An FAQ hit whose question and answer are embedded in `text` (Vectorize / Qdrant)
        must extract question/answer, pass _filter_unbound_faq_hits, and be promoted."""
        query = "What services does URA provide?"
        hit = {
            "text": (
                "Question: What services does URA provide?\n"
                "Answer: URA (Uganda Revenue Authority) is the country's central tax and customs "
                "authority. Core services include: taxpayer registration (instant TIN); domestic "
                "tax administration -- VAT, PAYE and employment income."
            ),
            "source": "ura_about_ura_faqs.csv",
            "doc_type": "faq_jsonl",
            "score_rrf": 0.5,
        }
        score = service._faq_match_score(query, hit)
        self.assertGreaterEqual(score, 0.9, f"Expected high score, got {score}")
        self.assertEqual(hit.get("question"), "What services does URA provide?")

        filtered = service._filter_unbound_faq_hits(query, [hit])
        self.assertEqual(len(filtered), 1)

        promoted = service._promote_equivalent_faq_hits(query, filtered)
        self.assertEqual(promoted[0]["source"], "ura_about_ura_faqs.csv")


if __name__ == "__main__":
    unittest.main()

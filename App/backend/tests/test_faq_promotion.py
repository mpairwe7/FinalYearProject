"""An FAQ row the user asked verbatim must lead the evidence.

Ranking is RRF over 7,000+ document chunks, and a chunk can outrank the curated
FAQ row that answers the question word for word. Measured on the deployed Space:
"What are business records?" was answered from an agriculture-sector PDF while the
identically-worded FAQ row sat second, and "When are capital gains taxed?"
returned how they are taxed rather than when.

The promotion is narrow on purpose. It requires term-*equivalence*, not a high
score: at 0.8 it would also fire for "What is withholding tax exemption?" against
"What is withholding tax?", where the system currently answers correctly from the
withholding-tax guide.
"""

from __future__ import annotations

import unittest

from app.service import _promote_equivalent_faq_hits, faq_question_equivalence


def _faq(question: str, source: str = "ura_x_faqs.csv") -> dict:
    return {"doc_type": "faq_jsonl", "question": question, "answer": "...", "source": source}


def _chunk(source: str = "handbook.pdf") -> dict:
    return {"doc_type": "pdf_chunk", "text": "some prose", "source": source}


class QuestionEquivalenceTests(unittest.TestCase):
    def test_the_same_question_scores_one(self) -> None:
        self.assertEqual(
            faq_question_equivalence("What are business records?", _faq("What are business records?")),
            1.0,
        )

    def test_stopwords_and_wording_do_not_prevent_equivalence(self) -> None:
        self.assertEqual(
            faq_question_equivalence("what are the business records", _faq("What are business records?")),
            1.0,
        )

    def test_an_extra_subject_term_in_the_faq_blocks_equivalence(self) -> None:
        """The case that makes a high-score rule unsafe: the exemption FAQ must not
        be treated as equivalent to a question about withholding tax itself."""
        score = faq_question_equivalence(
            "What is withholding tax?", _faq("What is withholding tax exemption?")
        )
        self.assertLess(score, 1.0)
        self.assertGreater(score, 0.5)  # still a strong partial match, just not equal

    def test_an_extra_term_in_the_query_also_blocks_equivalence(self) -> None:
        self.assertLess(
            faq_question_equivalence("What is withholding tax exemption?", _faq("What is withholding tax?")),
            1.0,
        )

    def test_unrelated_questions_score_zero(self) -> None:
        self.assertEqual(faq_question_equivalence("poem about cats", _faq("What is VAT?")), 0.0)

    def test_empty_inputs_are_safe(self) -> None:
        self.assertEqual(faq_question_equivalence("", _faq("What is VAT?")), 0.0)
        self.assertEqual(faq_question_equivalence("What is VAT?", _faq("")), 0.0)


class PromotionTests(unittest.TestCase):
    def test_a_verbatim_faq_row_is_moved_ahead_of_a_chunk(self) -> None:
        hits = [_chunk("agri.pdf"), _faq("What are business records?", "ura_business_records_faqs.csv")]
        promoted = _promote_equivalent_faq_hits("What are business records?", hits)
        self.assertEqual(promoted[0]["source"], "ura_business_records_faqs.csv")
        self.assertEqual(len(promoted), 2, "promotion must not drop evidence")

    def test_nothing_moves_when_no_row_is_equivalent(self) -> None:
        hits = [_chunk("agri.pdf"), _faq("What is withholding tax exemption?")]
        self.assertEqual(_promote_equivalent_faq_hits("What is withholding tax?", hits), hits)

    def test_relative_order_is_preserved_within_each_group(self) -> None:
        """Where two files carry the same question, retrieval's ranking still
        decides between them."""
        a = _faq("What is VAT?", "ura_vat_faqs.csv")
        b = _faq("What is VAT?", "ura_taxation_handbook_faqs.csv")
        c = _chunk("first.pdf")
        d = _chunk("second.pdf")
        promoted = _promote_equivalent_faq_hits("What is VAT?", [c, a, d, b])
        self.assertEqual([h["source"] for h in promoted],
                         ["ura_vat_faqs.csv", "ura_taxation_handbook_faqs.csv", "first.pdf", "second.pdf"])

    def test_chunks_are_never_promoted_even_on_an_exact_text_match(self) -> None:
        """Only curated FAQ rows carry an authoritative question; a chunk has none."""
        hit = {"doc_type": "pdf_chunk", "question": "What is VAT?", "source": "x.pdf", "text": "y"}
        hits = [_chunk("a.pdf"), hit]
        self.assertEqual(_promote_equivalent_faq_hits("What is VAT?", hits), hits)

    def test_empty_hits_are_returned_unchanged(self) -> None:
        self.assertEqual(_promote_equivalent_faq_hits("anything", []), [])

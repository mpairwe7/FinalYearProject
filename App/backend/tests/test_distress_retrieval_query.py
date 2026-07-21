"""Distress-framed questions must retrieve on the question, not the preamble.

Found via a live 30-FAQ + EI regression battery: "I've tried three times and
it still doesn't work!! What is EFRIS?" abstained (mode=abstained) even
though the identical calm question ("What is EFRIS and who must use it?")
answered correctly with faithfulness_score=1.0. Root cause: rewrite_query()
only spell-corrects/expands abbreviations — it never strips the emotional
preamble, so the diluted combined text was reaching the retriever/keyword
index directly and pushing genuinely answerable questions into false
abstention.
"""

from __future__ import annotations

import unittest
import unittest.mock as mock

from app.query import extract_question_span
from app.service import _filter_unbound_faq_hits


class ExtractQuestionSpanTest(unittest.TestCase):
    def test_strips_frustration_preamble(self) -> None:
        text = "I've tried three times and it still doesn't work!! What is EFRIS and who must use it?"
        self.assertEqual(
            extract_question_span(text), "What is EFRIS and who must use it?"
        )

    def test_strips_anxiety_preamble(self) -> None:
        text = "I am really worried — What is EFRIS and who must use it?"
        self.assertEqual(
            extract_question_span(text), "What is EFRIS and who must use it?"
        )

    def test_no_question_mark_returns_empty(self) -> None:
        self.assertEqual(
            extract_question_span("I can't figure out how to pay VAT, please help"),
            "",
        )

    def test_multiple_questions_are_joined(self) -> None:
        text = "This is due tomorrow! What is VAT? How do I pay it?"
        self.assertEqual(
            extract_question_span(text), "What is VAT? How do I pay it?"
        )

    def test_decimal_point_not_mistaken_for_sentence_end(self) -> None:
        text = "I'm worried the rate might be 37.5m, is that right?"
        self.assertEqual(
            extract_question_span(text),
            "I'm worried the rate might be 37.5m, is that right?",
        )

    def test_calm_question_passthrough(self) -> None:
        text = "What is the VAT registration threshold?"
        self.assertEqual(extract_question_span(text), text)


_EFRIS_HIT = {
    "text": (
        "Question: What is EFRIS and who must use it?\n"
        "Answer: Electronic Fiscal Receipting and Invoicing System. It is "
        "mandatory for all VAT-registered taxpayers who must use it to "
        "issue e-invoices and e-receipts."
    ),
    "question": "What is EFRIS and who must use it?",
    "answer": (
        "Electronic Fiscal Receipting and Invoicing System. It is mandatory "
        "for all VAT-registered taxpayers who must use it to issue "
        "e-invoices and e-receipts."
    ),
    "source": "ura_efris_faqs.csv",
    "doc_type": "csv",
    "score_rrf": 1.0,
}


class FilterUnboundFaqHitsDistressTest(unittest.TestCase):
    """A third occurrence of the same root cause, found after the first two
    fixes still left distress-framed EFRIS questions abstaining: hits from
    hybrid search (already on the clean question) reached
    _filter_unbound_faq_hits, which re-scored them against the RAW message
    (still carrying the distress preamble) and filtered every hit out —
    should_abstain then saw 0 hits regardless of how good retrieval was.
    """

    def test_raw_distress_message_filters_out_the_hit(self) -> None:
        raw_message = (
            "I have tried three times and it still doesnt work!! "
            "What is EFRIS and who must use it?"
        )
        filtered = _filter_unbound_faq_hits(raw_message, [dict(_EFRIS_HIT)])
        self.assertEqual(filtered, [], "raw distress message should dilute the match score")

    def test_extracted_question_span_keeps_the_hit(self) -> None:
        binding_query = extract_question_span(
            "I have tried three times and it still doesnt work!! "
            "What is EFRIS and who must use it?"
        )
        filtered = _filter_unbound_faq_hits(binding_query, [dict(_EFRIS_HIT)])
        self.assertEqual(len(filtered), 1)


class DistressRetrievalQueryWiringTest(unittest.TestCase):
    """Verify generate() actually searches on the extracted span, not the raw text."""

    @classmethod
    def setUpClass(cls):
        from app import database as db

        db.init_db()
        from app import service

        cls.model = service.ChatModel()

    def _generate_and_capture_query(self, message: str) -> tuple[list[str], list[str]]:
        from app import service

        seen: list[str] = []
        seen_binding: list[str] = []

        def _capture_simple_search(query, *args, **kwargs):
            seen.append(query)
            seen_binding.append(kwargs.get("binding_query", ""))
            return []

        with mock.patch.object(service.flags, "is_enabled", return_value=False), \
                mock.patch.object(
                    service.ChatModel, "_priority_faq_hits",
                    side_effect=lambda query, **k: seen.append(query) or [],
                ), \
                mock.patch.object(service, "_simple_search", side_effect=_capture_simple_search), \
                mock.patch.object(service, "needs_clarification", return_value=""), \
                mock.patch.object(service.ChatModel, "_maybe_handle_fast_paths", return_value=None), \
                mock.patch.object(
                    service.ChatModel, "_deterministic_procedure_reply",
                    return_value=("", False),
                ), \
                mock.patch.object(self.model._cache, "get", return_value=None), \
                mock.patch.object(self.model._cache, "put"):
            self.model.generate(message=message)
        return seen, seen_binding

    def test_distressed_question_searches_on_question_span_only(self) -> None:
        seen, _ = self._generate_and_capture_query(
            "I've tried three times and it still doesn't work!! "
            "What is EFRIS and who must use it?"
        )
        self.assertTrue(seen, "expected at least one search call")
        for query in seen:
            self.assertNotIn("tried three times", query)
            self.assertIn("EFRIS", query)

    def test_distressed_question_binding_query_excludes_preamble(self) -> None:
        # binding_query gates FAQ-match authorization independently of the
        # search query (service.py's _faq_match_score) — the residual bug
        # after the first fix: retrieval used the clean question, but
        # binding_query still carried the raw distress preamble, diluting
        # coverage enough that _retain_faq_candidates rejected the very hit
        # retrieval had just found, so the turn abstained anyway.
        _, seen_binding = self._generate_and_capture_query(
            "I've tried three times and it still doesn't work!! "
            "What is EFRIS and who must use it?"
        )
        self.assertTrue(seen_binding, "expected at least one search call")
        for binding_query in seen_binding:
            self.assertNotIn("tried three times", binding_query)
            self.assertIn("EFRIS", binding_query)

    def test_calm_question_still_searches_on_full_rewritten_text(self) -> None:
        seen, seen_binding = self._generate_and_capture_query(
            "What is EFRIS and who must use it?"
        )
        self.assertTrue(seen, "expected at least one search call")
        for query in seen:
            self.assertIn("EFRIS", query)
        for binding_query in seen_binding:
            self.assertIn("EFRIS", binding_query)


if __name__ == "__main__":
    unittest.main()

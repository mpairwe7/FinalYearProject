"""Conversational framing for extractive answers.

The system prompt already asks for this and the generated path delivers it:
Rule 14 puts URA's contact details on procedural answers, Rule 15 ends a short
informational answer with a follow-up suggestion, Rule 26 closes long
procedures with reassurance. The extractive path never reaches those rules — it
lifts the FAQ row verbatim — so the same assistant answered in two registers
depending on which tier served the turn.

Measured over 114 indexed FAQs against the deployed Space: 111 came back as
`hybrid`, 84% were under 250 characters of verbatim corpus text, 7% addressed
the reader as "you" and 4% offered any further help.

The property that must not break is grounding: both additions match
`is_courtesy_sentence`, which `compute_faithfulness` excludes from both sides
of its ratio, so framing can never read as hallucination.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.retriever import HybridRetriever  # noqa: E402
from app.service import _DATA_DIR, ChatModel, _load_faq_data  # noqa: E402


class FrameTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = ChatModel.__new__(ChatModel)
        cls.model._faq_index, _ = _load_faq_data(_DATA_DIR)

    def frame(self, reply: str, *, query: str, tag: str = "efris", mode: str = "hybrid") -> str:
        return self.model._add_conversational_frame(
            reply, query=query, hits=[{"section": tag, "text": reply}], retrieval_mode=mode
        )


class TestProceduralAnswersGetContactDetails(FrameTestCase):
    def test_steps_earn_the_contact_footer(self):
        reply = "Login ura.go.ug → e-services → e-returns → select return type."
        out = self.frame(reply, query="How do I file a return?", tag="processes_systems")
        self.assertIn("0800 117 000", out)
        self.assertTrue(out.startswith(reply))

    def test_numbered_list_counts_as_procedural(self):
        reply = "1. Go to ura.go.ug\n2. Click Get a TIN\n3. Enter your NIN"
        out = self.frame(reply, query="How do I get a TIN?", tag="instant_tin_application")
        self.assertIn("ura.go.ug", out)
        self.assertIn("happy to help", out)


class TestInformationalAnswersGetAFollowUp(FrameTestCase):
    """Hits here are shaped like a REAL hybrid turn — a PDF chunk whose
    ``section`` is a document heading, not a FAQ tag.

    The first version of _related_question looked up siblings under that field
    assuming it carried the tag. Every unit test passed because each supplied a
    hand-made hit with a real tag in ``section``, while in production the
    suggestion never once appeared: 0 of 40 replies carried one. The fixture
    encoded the same assumption as the code, so the pair agreed with each other
    and not with reality. These now use the shape production actually sends.
    """

    PDF_HIT = [
        {
            "section": "Chapter 4 — Customs procedure",
            "source": "East_Africa_Community_Customs_Management_Act_Revised.pdf",
            "doc_type": "pdf",
            "text": "...",
        }
    ]

    def frame_pdf(self, reply: str, query: str) -> str:
        return self.model._add_conversational_frame(
            reply, query=query, hits=self.PDF_HIT, retrieval_mode="hybrid"
        )

    def test_a_definition_gets_a_follow_up_even_when_the_top_hit_is_a_pdf(self):
        out = self.frame_pdf("EFRIS is URA's real-time e-invoicing system.", "What is EFRIS?")
        self.assertIn("You might also want to know:", out)

    def test_the_suggestion_is_a_real_question_from_the_corpus(self):
        """Rule 15 is only useful if what is offered actually resolves."""
        out = self.frame_pdf("EFRIS is URA's e-invoicing system.", "What is EFRIS?")
        suggested = out.split("You might also want to know:")[1].strip()
        corpus = {
            e["question"].strip()
            for entries in self.model._faq_index.values()
            for e in entries
        }
        self.assertIn(suggested, corpus)

    def test_it_does_not_suggest_the_question_just_asked(self):
        asked = "What is EFRIS?"
        out = self.frame_pdf("EFRIS is URA's e-invoicing system.", asked)
        if "You might also want to know:" in out:
            suggested = out.split("You might also want to know:")[1].strip()
            self.assertNotEqual(suggested.lower(), asked.lower())

    def test_it_reaches_across_topics(self):
        """The tag-local version could only ever suggest within one file."""
        found_cross_topic = False
        for query in (
            "What is the VAT rate in Uganda?",
            "How will I receive my TIN?",
            "What are business records?",
        ):
            out = self.frame_pdf("A grounded answer.", query)
            if "You might also want to know:" in out:
                found_cross_topic = True
        self.assertTrue(found_cross_topic, "no suggestion surfaced for any common query")

    def test_an_unanswerable_query_gets_no_suggestion(self):
        out = self.frame_pdf("A grounded answer.", "zzzz qqqq xxxx")
        self.assertNotIn("You might also want to know:", out)


class TestFramingIsNotAppliedTwice(FrameTestCase):
    def test_a_reply_that_already_offers_help_is_untouched(self):
        reply = (
            "The standard VAT rate is 18%. If you get stuck at any step, URA is happy "
            "to help: visit https://ura.go.ug."
        )
        self.assertEqual(self.frame(reply, query="What is the VAT rate?", tag="vat"), reply)

    def test_modes_with_their_own_voice_are_untouched(self):
        reply = "Are you registering as an individual or an organisation?"
        for mode in ("workflow", "clarification", "abstained", "escalated", "calculator", "graph"):
            with self.subTest(mode=mode):
                self.assertEqual(self.frame(reply, query="TIN?", mode=mode), reply)

    def test_an_empty_reply_is_left_alone(self):
        self.assertEqual(self.frame("", query="anything"), "")


class TestFramingDoesNotDamageGrounding(FrameTestCase):
    """The whole design rests on this: courtesy sentences are excluded from
    faithfulness, so framing cannot make a grounded answer look invented."""

    def test_faithfulness_is_unchanged_by_the_footer(self):
        reply = "Login ura.go.ug → e-services → e-returns → select return type."
        framed = self.frame(reply, query="How do I file a return?", tag="processes_systems")
        self.assertNotEqual(framed, reply)
        self.assertEqual(
            HybridRetriever.compute_faithfulness(framed, [reply]),
            HybridRetriever.compute_faithfulness(reply, [reply]),
        )

    def test_faithfulness_is_unchanged_by_the_follow_up(self):
        reply = "EFRIS is URA's real-time e-invoicing system."
        framed = self.frame(reply, query="What is EFRIS?", tag="efris")
        self.assertNotEqual(framed, reply)
        self.assertEqual(
            HybridRetriever.compute_faithfulness(framed, [reply]),
            HybridRetriever.compute_faithfulness(reply, [reply]),
        )

    def test_a_fabricated_figure_still_scores_badly(self):
        """The courtesy exclusion must not become a hole to smuggle claims through."""
        grounded = "EFRIS is URA's e-invoicing system."
        invented = grounded + "\n\nThe EFRIS registration fee is UGX 4,500,000."
        self.assertLess(
            HybridRetriever.compute_faithfulness(invented, [grounded]),
            1.0,
        )


if __name__ == "__main__":
    unittest.main()


class TestSuggestionSurvivesAMissingBm25State(FrameTestCase):
    """bm25_state.json is absent in some deployments and _simple_search already
    degrades to term overlap there. The suggestion does the same rather than
    switching itself off, since silently producing nothing is the exact failure
    this method was rewritten to fix."""

    def test_a_suggestion_is_still_produced_without_the_encoder(self):
        import app.service as svc

        saved_enc, saved_flag = svc._BM25_ENCODER, svc._BM25_LOAD_ATTEMPTED
        svc._BM25_ENCODER, svc._BM25_LOAD_ATTEMPTED = None, True  # force the fallback
        try:
            out = self.model._add_conversational_frame(
                "EFRIS is URA's e-invoicing system.",
                query="What is EFRIS?",
                hits=[{"section": "Chapter 1", "source": "x.pdf", "doc_type": "pdf"}],
                retrieval_mode="hybrid",
            )
        finally:
            svc._BM25_ENCODER, svc._BM25_LOAD_ATTEMPTED = saved_enc, saved_flag
        self.assertIn("You might also want to know:", out)

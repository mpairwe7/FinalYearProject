"""Corpus-wide retrieval gate — the safety net for this class of bug.

Three defects shipped to production in one week, all in the same area, and
none of them would have been caught by the existing suite:

  * prepended hit groups arrived reversed, so a "how do I" question was
    answered from a "what is" FAQ;
  * the coverage filter then deleted the correct row by 0.009 of score;
  * the follow-up suggestion looked up a FAQ tag in a field carrying a PDF
    document heading, so it never fired at all — 0 of 40 replies.

Each was found by asking production questions by hand. Those three cases
now also live in ``Data/eval/rag_eval.jsonl`` (``reg-how-file-returns``,
``reg-how-submit-yearly``, ``reg-efris-what``) so the offline eval set
and this keyword gate cannot drift apart.

This asks every indexed FAQ its own question and checks the corpus answers
itself. It needs no network, no model and no Qdrant — it exercises the
keyword + priority + filter path that actually serves production — and runs
in about five seconds, so it can gate every PR.

The floor is deliberately below the measured rate rather than pinned to it:
the corpus changes weekly, and a gate that fails on a single new
near-duplicate FAQ gets disabled rather than fixed. Both scoring paths were
measured — 99.2% with bm25_state.json loaded, 98.4% on the term-overlap
fallback — so 97% holds either way and only a real regression breaks it.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.service import (  # noqa: E402
    _DATA_DIR,
    ChatModel,
    _faq_hits_to_retrieval_hits,
    _filter_unbound_faq_hits,
    _load_faq_data,
    _prepend_unique,
    _promote_equivalent_faq_hits,
    _simple_search,
)

# Measured 98.4% (overlap fallback) / 99.2% (BM25). Headroom for corpus churn.
SELF_RETRIEVAL_FLOOR = 0.97


class RetrievalGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = ChatModel.__new__(ChatModel)
        cls.model._faq_index, _ = _load_faq_data(_DATA_DIR)

    def answer_hits(self, query: str) -> list[dict]:
        """The path production serves from when Qdrant and the LLM are absent."""
        keyword = _simple_search(
            query, self.model._faq_index, top_k=4, binding_query=query, locale="en"
        )
        hits = _faq_hits_to_retrieval_hits(keyword)
        seen = {h.get("text", "")[:80] for h in hits}
        _prepend_unique(hits, self.model._priority_faq_hits(query, top_k=2), seen)
        return _promote_equivalent_faq_hits(query, _filter_unbound_faq_hits(query, hits))


class TestTheCorpusAnswersItself(RetrievalGate):
    def test_every_indexed_faq_returns_something(self):
        """An indexed question that returns nothing is always a bug — the
        answer is sitting in the corpus by construction."""
        unanswered = []
        for entries in self.model._faq_index.values():
            for entry in entries:
                question = entry["question"].strip()
                if question and not self.answer_hits(question):
                    unanswered.append(question)
        self.assertEqual(
            unanswered[:5], [], f"{len(unanswered)} indexed FAQs returned no hits"
        )

    def test_self_retrieval_stays_above_the_floor(self):
        total = matched = 0
        misses: list[tuple[str, str]] = []
        for entries in self.model._faq_index.values():
            for entry in entries:
                question = entry["question"].strip()
                if not question:
                    continue
                total += 1
                hits = self.answer_hits(question)
                got = hits[0].get("question", "").strip().lower() if hits else ""
                if got == question.lower():
                    matched += 1
                elif len(misses) < 8:
                    misses.append((question, got or "(nothing)"))
        rate = matched / total if total else 0.0
        detail = "\n".join(f"    asked {q!r}\n      got {g!r}" for q, g in misses)
        self.assertGreaterEqual(
            rate,
            SELF_RETRIEVAL_FLOOR,
            f"self-retrieval fell to {rate:.1%} over {total} FAQs "
            f"(floor {SELF_RETRIEVAL_FLOOR:.0%}); examples:\n{detail}",
        )


class TestTheDefectsThatShipped(RetrievalGate):
    """Named cases, so a repeat reads as this bug returning rather than as a
    percentage drifting."""

    def test_a_how_do_i_question_gets_the_procedure_not_the_definition(self):
        for query in (
            "How do I file my annual tax returns?",
            "How do I submit my yearly tax return in Uganda?",
        ):
            with self.subTest(query=query):
                hits = self.answer_hits(query)
                self.assertTrue(hits, "the procedural FAQ was filtered out entirely")
                answer = hits[0].get("answer", "").lower()
                self.assertNotIn(
                    "is a declaration to ura",
                    answer,
                    "answered with the definition of a return filing",
                )
                self.assertIn("e-returns", answer)

    def test_priority_rows_keep_the_order_they_were_ranked_in(self):
        priority = self.model._priority_faq_hits("How do I file a return?", top_k=2)
        self.assertTrue(priority)
        self.assertIn("how do i file a return", priority[0]["question"].lower())

        hits: list[dict] = []
        _prepend_unique(hits, priority, set())
        self.assertEqual(
            [h["question"] for h in hits], [h["question"] for h in priority]
        )

    def test_a_grounded_answer_is_framed(self):
        """The follow-up silently produced nothing for every hybrid turn. Hits
        are shaped the way production sends them — a PDF chunk whose section is
        a document heading, not a FAQ tag."""
        framed = self.model._add_conversational_frame(
            "EFRIS is URA's real-time e-invoicing system.",
            query="What is EFRIS?",
            hits=[{"section": "Chapter 2 — Compliance", "source": "guide.pdf", "doc_type": "pdf"}],
            retrieval_mode="hybrid",
        )
        self.assertIn("You might also want to know:", framed)


class TestOffDomainQuestionsStayRefused(RetrievalGate):
    """The counterweight. Every gate above pushes toward answering more, and
    the cost of over-answering is a tax authority stating something confidently
    from an unrelated FAQ."""

    def test_off_domain_questions_get_nothing(self):
        answered = [
            q
            for q in (
                "Who is the president of Uganda?",
                "How do I pay my rent to my landlord?",
                "What is the best business to start?",
                "How much does a car cost in Uganda?",
                "How do I bake banana bread?",
            )
            if self.answer_hits(q)
        ]
        self.assertEqual(answered, [], "an off-domain question reached an FAQ answer")


if __name__ == "__main__":
    unittest.main()

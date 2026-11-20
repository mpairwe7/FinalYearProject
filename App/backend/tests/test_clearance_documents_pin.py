"""Which FAQ rows the customs-clearance-documents pin puts first.

"What documents are required to clear an imported vehicle at customs?" was answered with "To
register a motor vehicle: 1. Login to the URA web portal...". The pin chose every row whose text
held "bill of lading", in file order, and the registration procedure lists one among its uploads.
Every question that reached the pin got that row first, the Kiswahili FAQ's own question included.

A message made only of the words of that question now gets the rows that answer it. Any other
word leaves the message to the old pin, because for registration after customs that row is right.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

POINT_OF_ENTRY = "Which documents are required at the point of entry for goods?"
LUGANDA_ROW = "Biwandiiko ki ebyetaagisa okuyisa emmotoka mu forodha?"
KISWAHILI_ROW = "Nyaraka gani zinahitajika kusafisha gari lililoagizwa katika forodha?"


class ClearanceDocumentsPinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.rag.pipeline import _DATA_DIR, ChatModel, _load_faq_data

        cls.model = ChatModel.__new__(ChatModel)
        cls.model._faq_index, _ = _load_faq_data(_DATA_DIR)

    def _hits(self, query: str) -> list[dict]:
        """What a turn keeps of the pin: two rows, as both callers ask, after the binding filter."""
        from app.rag.pipeline import _filter_unbound_faq_hits, _prepend_unique, _promote_equivalent_faq_hits

        hits: list[dict] = []
        _prepend_unique(hits, self.model._priority_faq_hits(query, top_k=2), set())
        return _promote_equivalent_faq_hits(query, _filter_unbound_faq_hits(query, hits))

    def test_an_english_question_gets_the_list_of_documents(self):
        for query in (
            "What documents are required to clear an imported vehicle at customs?",
            "Hello! What documents are required to clear an imported vehicle at customs?",
            "Which documents do I need to clear my car at customs?",
            "Documents needed for clearing a motor vehicle?",
            "What documents are required for customs clearance of a vehicle?",
            "What documents are needed to clear goods through customs?",  # App/Data/eval/rag_eval.jsonl
            "What documents do I need at customs?",
        ):
            with self.subTest(query=query):
                hits = self._hits(query)
                self.assertEqual(hits[0]["question"], POINT_OF_ENTRY)
                for document in ("bill of lading", "commercial invoice", "packing list", "certificate of origin"):
                    self.assertIn(document, hits[0]["answer"].lower())
                self.assertFalse([h["question"] for h in hits if "registration" in h["question"].lower()])

    def test_a_luganda_or_kiswahili_question_gets_the_row_in_its_language(self):
        for query, row in (
            (LUGANDA_ROW, LUGANDA_ROW),
            ("Biwandiiko ki bye nneetaaga okuyisa emmotoka yange mu forodha?", LUGANDA_ROW),
            (KISWAHILI_ROW, KISWAHILI_ROW),
            ("Nyaraka gani ninahitaji kusafisha gari langu forodhani?", KISWAHILI_ROW),
        ):
            with self.subTest(query=query):
                self.assertEqual(self._hits(query)[0]["question"], row)

    def test_a_question_with_any_other_word_is_read_as_before(self):
        """Registration after customs got the registration procedure from the old pin, and still
        does. A first version listed registration words and lost every phrasing it did not name."""
        for query in (
            "What documents are required to register an imported vehicle after customs clearance?",
            "What documents do I upload to register my car once customs has released it?",
            "What documents do I need for number plates after customs?",
            "What documents do I need for number-plates after customs?",
            "What documents do I need to license my imported car after clearing customs?",
            "What documents do I need to get a log-book for my car after customs?",
            "What documents do I need to take to the URA licensing office after customs?",
            "Which documents do I need to clear my car and get plates?",
            "Biwandiiko ki ebyetaagisa okwewandiisa emmotoka oluvannyuma lwa forodha?",
            "Nyaraka gani zinahitajika gari kusajiliwa baada ya forodha?",
            "documents gani zinahitajika kusafisha gari forodha?",
            "What documents are required to clear an imported vehicle at customs? And how do I register it?",
        ):
            with self.subTest(query=query):
                first = self._hits(query)[0]
                self.assertIn("motor vehicle registration", first["question"].lower())
                self.assertIn("to register a motor vehicle", first["answer"].lower())

    def test_the_corpus_still_holds_a_row_for_each_language(self):
        """The pin finds its rows by their question, so a reworded question stops matching."""
        from app.rag.pipeline import _CLEARANCE_DOCS_ROW_RE

        questions = [
            entry["question"]
            for tag in ("documents_point_of_entry", "customs_valuation", "advance_tax_transport")
            for entry in self.model._faq_index.get(tag, [])
            if _CLEARANCE_DOCS_ROW_RE.search(entry["question"])
        ]
        for row in (POINT_OF_ENTRY, LUGANDA_ROW, KISWAHILI_ROW):
            self.assertIn(row, questions)
        self.assertFalse([q for q in questions if "registration" in q.lower()])


if __name__ == "__main__":
    unittest.main()

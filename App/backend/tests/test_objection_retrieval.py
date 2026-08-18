"""Objection questions must find the objections FAQ, not fall into abstention.

Found on the live Crane Cloud deployment: "How do I object to a tax
assessment I disagree with?" answered with the abstention copy
(mode=abstained, no sources) while "objection to assessment" answered
correctly from the starter-pack row. The corpus was the cause, not the
retriever — the only indexed sentence on the subject was "Yes. You may
lodge an objection if dissatisfied with an assessment.", which carries no
procedure and shares almost no terms with how people actually ask. Anything
phrased as a how-to therefore scored below the abstention threshold.

Data/dataset/ura_objections_and_appeals_faqs.csv now carries the procedure
itself, grounded in the Tax Procedures Code Act section 23 wording already
used as the reference context in Data/eval/rag_eval.jsonl: 45 days from
service of the notice, in writing, grounds plus supporting documents, and a
decision from the Commissioner General within 90 days.

Note what this deliberately does NOT change: explicitly dispute-framed asks
("lodge an objection", "dispute", "appeal") still escalate to an officer via
the legal/dispute pattern in agents/patterns/en.py. That is policy, and it
is a better answer than abstaining. The gap being closed here is the
informational how-to that trips no such pattern and simply had nothing to
answer from.
"""

from __future__ import annotations

import csv
import unittest
from pathlib import Path

from app.service import _DATA_DIR, _load_faq_data, _simple_search

OBJECTION_CSV = "ura_objections_and_appeals_faqs.csv"


class ObjectionCorpusTest(unittest.TestCase):
    def test_objection_faq_file_is_present_and_well_formed(self) -> None:
        path = Path(_DATA_DIR) / OBJECTION_CSV
        self.assertTrue(path.is_file(), f"{path} is missing")
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertTrue(rows, "objections FAQ has no rows")
        for row in rows:
            self.assertTrue((row.get("question") or "").strip())
            self.assertTrue((row.get("answer") or "").strip())

    def test_the_statutory_facts_are_the_ones_the_eval_set_grounds_on(self) -> None:
        """45 / 90 days and the Commissioner General, not invented specifics."""
        body = (Path(_DATA_DIR) / OBJECTION_CSV).read_text(encoding="utf-8").lower()
        self.assertIn("45 days", body)
        self.assertIn("90 days", body)
        self.assertIn("commissioner general", body)
        self.assertIn("tax procedures code act", body)


class ObjectionRetrievalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.faq_index, _ = _load_faq_data(Path(_DATA_DIR))

    def test_objections_tag_is_indexed(self) -> None:
        self.assertIn("objections_and_appeals", self.faq_index)

    def test_how_to_phrasings_retrieve_the_objection_procedure(self) -> None:
        """The exact wording that abstained in production, plus its variants."""
        for query in (
            "How do I object to a tax assessment I disagree with?",
            "How do I object to a tax assessment?",
            "How long do I have to object to an assessment?",
            "What must a tax objection contain?",
        ):
            with self.subTest(query=query):
                hits = _simple_search(query, self.faq_index, top_k=4) or []
                self.assertTrue(hits, f"no hits for {query!r}")
                self.assertIn(
                    OBJECTION_CSV,
                    {hit.get("source") for hit in hits},
                    f"objections FAQ absent from hits for {query!r}",
                )

    def test_the_pre_existing_starter_pack_row_still_matches(self) -> None:
        """The new file must add coverage, not shadow what already answered."""
        hits = _simple_search("objection to assessment", self.faq_index, top_k=4) or []
        sources = {hit.get("source") for hit in hits}
        self.assertIn("ura_taxpayer_starter_pack_faqs.csv", sources)


if __name__ == "__main__":
    unittest.main()

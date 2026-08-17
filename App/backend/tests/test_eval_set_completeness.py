"""The CI RAG eval set must include the regressions that already shipped.

The 21-question English set missed three production bugs in one week
(see test_retrieval_regression_gate.py). Those cases now live in
Data/eval/rag_eval.jsonl with stable case_ids so a silent deletion
fails CI the same way the keyword gate does.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app._root import PROJECT_ROOT  # noqa: E402
from app.query import decompose_query, extract_retrieval_filters, plan_retrieval  # noqa: E402
from app.service import (  # noqa: E402
    ChatModel,
    _DATA_DIR,
    _faq_hits_to_retrieval_hits,
    _filter_unbound_faq_hits,
    _load_faq_data,
    _prepend_unique,
    _promote_equivalent_faq_hits,
    _simple_search,
)

EVAL_PATH = PROJECT_ROOT / "Data" / "eval" / "rag_eval.jsonl"

REQUIRED_CASE_IDS = (
    "reg-how-file-returns",
    "reg-how-submit-yearly",
    "reg-efris-what",
    "reg-current-vat-fy",
    "reg-explicit-fy",
    "reg-multi-intent",
    "reg-off-domain-france",
    "reg-off-domain-president",
    "reg-off-domain-banana-bread",
)


def _load_eval() -> list[dict]:
    rows = []
    for line in EVAL_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


class EvalSetCompletenessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = _load_eval()
        cls.by_id = {row["case_id"]: row for row in cls.rows if row.get("case_id")}

    def test_required_regression_ids_are_present(self) -> None:
        missing = [cid for cid in REQUIRED_CASE_IDS if cid not in self.by_id]
        self.assertEqual(missing, [], f"rag_eval.jsonl dropped {missing}")

    def test_eval_set_grew_past_the_21_question_hole(self) -> None:
        self.assertGreaterEqual(len(self.rows), 30)

    def test_off_domain_rows_are_marked_to_abstain(self) -> None:
        for cid in (
            "reg-off-domain-france",
            "reg-off-domain-president",
            "reg-off-domain-banana-bread",
        ):
            self.assertTrue(self.by_id[cid].get("should_abstain"), cid)

    def test_explicit_fy_row_parses_as_a_hard_filter(self) -> None:
        q = self.by_id["reg-explicit-fy"]["question"]
        self.assertEqual(extract_retrieval_filters(q), {"fiscal_year": "FY2024-25"})

    def test_multi_intent_row_decomposes(self) -> None:
        q = self.by_id["reg-multi-intent"]["question"]
        parts = decompose_query(q)
        self.assertGreaterEqual(len(parts), 2)
        plan = plan_retrieval(q)
        self.assertGreaterEqual(len(plan["subqueries"]), 2)


class EvalSetKeywordConsistencyTests(unittest.TestCase):
    """Same production keyword path the regression gate uses."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.model = ChatModel.__new__(ChatModel)
        cls.model._faq_index, _ = _load_faq_data(_DATA_DIR)
        cls.rows = {row["case_id"]: row for row in _load_eval() if row.get("case_id")}

    def _answer_hits(self, query: str) -> list[dict]:
        keyword = _simple_search(
            query, self.model._faq_index, top_k=4, binding_query=query, locale="en"
        )
        hits = _faq_hits_to_retrieval_hits(keyword)
        seen = {h.get("text", "")[:80] for h in hits}
        _prepend_unique(hits, self.model._priority_faq_hits(query, top_k=2), seen)
        return _promote_equivalent_faq_hits(query, _filter_unbound_faq_hits(query, hits))

    def test_how_do_i_file_is_the_procedure_not_the_definition(self) -> None:
        for cid in ("reg-how-file-returns", "reg-how-submit-yearly"):
            query = self.rows[cid]["question"]
            hits = self._answer_hits(query)
            self.assertTrue(hits, cid)
            answer = hits[0].get("answer", "").lower()
            self.assertNotIn("is a declaration to ura", answer, cid)
            self.assertIn("e-returns", answer, cid)

    def test_off_domain_eval_rows_do_not_retrieve_an_faq(self) -> None:
        leaked = [
            cid
            for cid in (
                "reg-off-domain-france",
                "reg-off-domain-president",
                "reg-off-domain-banana-bread",
            )
            if self._answer_hits(self.rows[cid]["question"])
        ]
        self.assertEqual(leaked, [], "off-domain eval rows reached an FAQ")


if __name__ == "__main__":
    unittest.main()

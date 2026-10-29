"""Relevance gates decide on evidence, and each retrieval stage fails on its own (G283-G285).

* **G283 — the lexical lift.** Three matched content words lifted any question
  to the lexical floor, whatever its IDF coverage, so a question sharing
  "bank", "business" and "account" with a chunk cleared the only relevance gate
  a sparse-only deployment has.
* **G284 — boosts are not evidence.** The current-FY (+0.08) and topic (+0.05)
  boosts were written into the score abstention, pruning and corrective RAG
  read, lifting a 0.18 passage over the 0.30 abstention threshold.
* **G285 — multi-intent merge.** Sub-query hits were sorted together, cut and
  pruned against the overall best, so the weaker intent's evidence vanished.
* A reranker failure (a CUDA OOM) was charged to Qdrant's circuit breaker and
  emptied the result; after three, everyone was on keyword search.
* The cache generation follows the index binding and never reuses an unknown
  tombstone state.
"""

from __future__ import annotations

import types
import unittest
from typing import Any
from unittest import mock

import numpy as np
from app import cache as cache_mod
from app import retriever as retriever_mod
from app.corrective_rag import _avg_relevance
from app.guardrails import OutputGuard
from app.retriever import (
    LEXICAL_RELEVANCE_FLOOR,
    BM25SparseEncoder,
    HybridRetriever,
    apply_preference_boost,
    gating_relevance,
    lexical_relevance,
    merge_retrieval_hits,
    prune_context,
)


class LexicalLiftTests(unittest.TestCase):
    def setUp(self) -> None:
        # "bank", "business", "account" and "online" are everywhere in a tax
        # corpus; "loan", "best" and "small" never occur, so they carry max IDF.
        filler = "open a business bank account online and pay taxes"
        self.encoder = BM25SparseEncoder().fit([filler] * 40 + ["vat registration threshold"] * 10)
        self.hit = {"text": "Open a business bank account online to pay your taxes."}

    def test_three_common_words_no_longer_clear_the_floor(self) -> None:
        query = "Which bank gives the best loan for a small business account online?"
        self.assertLess(lexical_relevance(query, self.hit, self.encoder), LEXICAL_RELEVANCE_FLOOR)

    def test_the_lift_still_serves_a_long_on_topic_question(self) -> None:
        """Enough IDF evidence behind three matched words still reaches the floor."""
        query = "As a trader how do I open a business bank account online to pay taxes quickly?"
        self.assertGreaterEqual(lexical_relevance(query, self.hit, self.encoder), LEXICAL_RELEVANCE_FLOOR)


class GatingTests(unittest.TestCase):
    def _boosted(self) -> dict[str, Any]:
        hit = {
            "text": "VAT registration guidance",
            "score_rerank": 0.18,
            "score_norm": 0.18,
            "score_calibrated": 0.18,
            "fiscal_year": "FY2026-27",
            "tax_type": "vat",
        }
        apply_preference_boost([hit], {"fiscal_year": "FY2026-27", "tax_type": "vat"})
        return hit

    def test_a_boost_reorders_but_does_not_make_a_passage_answerable(self) -> None:
        hit = self._boosted()
        self.assertGreater(hit["score_norm"], 0.30, "the ranking score is boosted")
        self.assertEqual(gating_relevance(hit), 0.18)
        self.assertTrue(OutputGuard.should_abstain([hit], locale="en"))

    def test_corrective_rag_and_pruning_read_the_calibrated_score(self) -> None:
        hit = self._boosted()
        self.assertAlmostEqual(_avg_relevance([hit]), 0.18)
        strong = {"text": "strong", "score_norm": 0.9, "score_calibrated": 0.9}
        self.assertEqual(prune_context([strong, hit]), [strong], "0.18 is far below 0.9 whatever its boost")

    def test_hits_never_reranked_keep_their_score_norm(self) -> None:
        self.assertEqual(gating_relevance({"score_norm": 0.42}), 0.42)
        self.assertIsNone(gating_relevance({"score_rrf": 0.3}))


class MultiIntentMergeTests(unittest.TestCase):
    def test_the_weaker_intent_keeps_its_evidence(self) -> None:
        vat = [{"id": f"vat{i}", "text": f"vat passage number {i} about rates", "score_norm": s}
               for i, s in enumerate((0.95, 0.90, 0.85, 0.80))]
        paye = [{"id": f"paye{i}", "text": f"paye passage number {i} about bands", "score_norm": s}
                for i, s in enumerate((0.45, 0.40))]
        merged = merge_retrieval_hits([vat, paye], top_k=4)
        ids = [h["id"] for h in merged]
        self.assertEqual(len(merged), 4)
        self.assertIn("paye0", ids)
        self.assertEqual(ids[0], "vat0", "still ordered best first, so [1] is the strongest passage")

    def test_a_batch_is_pruned_against_its_own_best(self) -> None:
        vat = [{"id": "vat0", "text": "vat rates passage", "score_norm": 0.95},
               {"id": "vat-noise", "text": "an unrelated passage on fishing licences", "score_norm": 0.05}]
        merged = merge_retrieval_hits([vat], top_k=4)
        self.assertEqual([h["id"] for h in merged], ["vat0"])


class _Points:
    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.points = [
            types.SimpleNamespace(id=f"p{i}", payload=payload, score=0.5 - i * 0.1)
            for i, payload in enumerate(payloads)
        ]


class RerankFailureIsolationTests(unittest.TestCase):
    def _retriever(self) -> HybridRetriever:
        retriever = HybridRetriever()
        retriever._ready = True
        retriever._client = mock.Mock(
            query_points=mock.Mock(
                return_value=_Points(
                    [
                        {"text": "VAT is charged at 18% on taxable supplies.", "source": "ura_vat_faqs.csv"},
                        {"text": "PAYE is deducted monthly by employers.", "source": "ura_paye_faqs.csv"},
                    ]
                )
            )
        )
        retriever._dense_model = mock.Mock(encode=mock.Mock(return_value=np.zeros(4, dtype=np.float32)))
        retriever._reranker = mock.Mock(predict=mock.Mock(side_effect=RuntimeError("CUDA out of memory")))
        return retriever

    def test_a_reranker_failure_serves_unscored_hits_and_spares_the_circuit(self) -> None:
        retriever = self._retriever()
        with mock.patch("app.flags.flags.is_enabled", return_value=True), mock.patch.object(
            retriever_mod, "_filter_tombstoned_candidates", side_effect=lambda c: c
        ), mock.patch.object(retriever_mod.metrics, "inc") as inc:
            for _ in range(4):
                hits = retriever.search("What is the VAT rate?", top_k=4)
        self.assertEqual(len(hits), 2)
        self.assertTrue(all("score_norm" not in h and "score_calibrated" not in h for h in hits))
        self.assertFalse(retriever.serving_degraded(), "Qdrant answered every time")
        inc.assert_any_call("retrieval_rerank_errors_total")
        self.assertNotIn(mock.call("qdrant_query_errors_total"), inc.call_args_list)


class GenerationInputsTests(unittest.TestCase):
    def setUp(self) -> None:
        cache_mod.reset_index_stamp()
        self.addCleanup(cache_mod.register_index_binding, "")
        self.addCleanup(cache_mod.reset_index_stamp)

    def test_a_new_index_binding_retires_the_cache(self) -> None:
        with mock.patch("app.freshness.load_status", return_value={}):
            cache_mod.register_index_binding("aaaa1111")
            first = cache_mod.cache_generation()
            cache_mod.register_index_binding("bbbb2222")
            self.assertNotEqual(cache_mod.cache_generation(), first)
            self.assertTrue(cache_mod.cache_generation().startswith("+bbbb2222"))

    def test_a_store_never_read_never_repeats_a_stamp(self) -> None:
        from app import database as db

        self.addCleanup(setattr, retriever_mod, "_last_tombstones", retriever_mod._last_tombstones)
        retriever_mod._last_tombstones = None
        with mock.patch("app.flags.flags.is_enabled", return_value=True), mock.patch.object(
            db, "get_active_corpus_tombstones", side_effect=RuntimeError("database is locked")
        ):
            first, second = cache_mod._read_tombstone_stamp(), cache_mod._read_tombstone_stamp()
        self.assertTrue(first.startswith("unread-"))
        self.assertNotEqual(first, second)

    def test_an_outage_after_a_good_read_stamps_what_retrieval_applies(self) -> None:
        """The cache and retrieval read the same tombstones through the same outage rule."""
        from app import database as db

        self.addCleanup(setattr, retriever_mod, "_last_tombstones", retriever_mod._last_tombstones)
        rows = [{"id": "tomb_1", "source_uri": "old.pdf", "chunk_id": ""}]
        with mock.patch("app.flags.flags.is_enabled", return_value=True):
            with mock.patch.object(db, "get_active_corpus_tombstones", return_value=rows):
                healthy = cache_mod._read_tombstone_stamp()
            with mock.patch.object(db, "get_active_corpus_tombstones", side_effect=RuntimeError("locked")):
                during_outage = cache_mod._read_tombstone_stamp()
        self.assertEqual(during_outage, healthy)
        self.assertFalse(healthy.startswith("unread-"))


class SearchFailureDegradedTests(unittest.TestCase):
    """A failed search below the circuit's threshold is still an outage answer."""

    def _retriever(self, client: Any) -> HybridRetriever:
        retriever = HybridRetriever()
        retriever._ready = True
        retriever._client = client
        retriever._sparse_only = True  # no dense model needed
        retriever._sparse_encoder = BM25SparseEncoder().fit(["vat rate", "paye bands"])
        return retriever

    def test_one_failed_search_marks_the_request_degraded_until_the_next_plan(self) -> None:
        import contextvars

        def scenario() -> None:
            broken = self._retriever(mock.Mock(query_points=mock.Mock(side_effect=TimeoutError("qdrant timed out"))))
            with mock.patch("app.flags.flags.is_enabled", return_value=False):
                self.assertEqual(broken.search("vat rate"), [])
            self.assertTrue(broken.serving_degraded(), "the circuit is still CLOSED after one failure")
            self.assertFalse(broken._circuit.state is not retriever_mod.CircuitState.CLOSED)
            healthy = self._retriever(mock.Mock(query_points=mock.Mock(return_value=types.SimpleNamespace(points=[]))))
            with mock.patch("app.flags.flags.is_enabled", return_value=False):
                healthy.search_planned("vat rate")
            self.assertFalse(healthy.serving_degraded(), "a new planned search starts clean")

        contextvars.copy_context().run(scenario)


class MergeModeTests(unittest.TestCase):
    def test_a_sub_query_that_found_nothing_relevant_earns_no_place(self) -> None:
        vat = [{"id": f"vat{i}", "text": f"vat passage {i} on rates", "score_norm": s} for i, s in enumerate((0.9, 0.8))]
        thanks = [{"id": "noise", "text": "an unrelated passage about fishing", "score_norm": 0.02}]
        ids = [h["id"] for h in merge_retrieval_hits([vat, thanks], top_k=4)]
        self.assertNotIn("noise", ids)

    def test_two_legs_of_one_question_take_one_cut(self) -> None:
        """G128: the untranslated leg must not ride into context on a reserved place."""
        english = [{"id": "en", "text": "vat is charged at eighteen percent", "score_norm": 0.9}]
        native = [{"id": "lg", "text": "omusolo passage that matched nothing", "score_norm": 0.30}]
        ids = [h["id"] for h in merge_retrieval_hits([english, native], top_k=4, per_intent=False)]
        self.assertEqual(ids, ["en"], "0.30 is below 0.9 - 0.45 and is pruned")


class RerankScaleIsPerModelTests(unittest.TestCase):
    def _rerank(self, retriever: HybridRetriever, scores: list[float]) -> list[float]:
        retriever._reranker.predict = mock.Mock(return_value=list(scores))
        candidates = [{"text": f"p{i}", "source": "ura_vat_faqs.csv"} for i in range(len(scores))]
        with mock.patch("app.flags.flags.is_enabled", return_value=True):
            retriever._rerank("What is the VAT rate?", candidates)
        return sorted(c["score_calibrated"] for c in candidates)

    def test_a_logit_model_is_squashed_even_when_one_batch_looks_like_probabilities(self) -> None:
        retriever = HybridRetriever()
        retriever._reranker = mock.Mock()
        retriever._reranker.activation_fn = type("Identity", (), {})()
        norms = self._rerank(retriever, [0.05, 0.3])
        self.assertAlmostEqual(norms[0], 1 / (1 + np.exp(-0.05)))

    def test_logits_once_seen_stay_logits(self) -> None:
        retriever = HybridRetriever()
        retriever._reranker = mock.Mock(spec=["predict"])
        self._rerank(retriever, [9.75, -2.0])
        norms = self._rerank(retriever, [0.05, 0.3])
        self.assertAlmostEqual(norms[1], 1 / (1 + np.exp(-0.3)))

    def test_a_sigmoid_model_is_read_as_probabilities(self) -> None:
        retriever = HybridRetriever()
        retriever._reranker = mock.Mock()
        retriever._reranker.activation_fn = type("Sigmoid", (), {})()
        self.assertEqual(self._rerank(retriever, [0.05, 0.3]), [0.05, 0.3])


if __name__ == "__main__":
    unittest.main()

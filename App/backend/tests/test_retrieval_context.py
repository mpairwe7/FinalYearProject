"""Retrieval context quality: pruning the tail, and keeping both acronym forms.

Two defects, both about what actually reaches the model.

The system decided *whether* to answer from the best hit — ``should_abstain``
uses ``max`` — and then handed the model **every** hit. A result set scored
[0.85, 0.04, 0.03, 0.02] clears that gate, and three passages the reranker
put at ~3% relevance still arrived as context. Irrelevant passages sitting
next to a relevant one are not inert padding: they are what lets a model
combine two chunks about different things into a claim supported by neither.

And ``expand_abbreviations`` replaced acronyms rather than augmenting them
for three entries, while ``wht`` — the form the supervisor and the
withholding calculator both use — was missing from the table entirely.
"""

from __future__ import annotations

import unittest

from app.query import _ABBREVIATIONS, expand_abbreviations
from app.retriever import BM25SparseEncoder, prune_context


def _hits(*relevances: float) -> list[dict]:
    return [{"id": str(i), "score_norm": r} for i, r in enumerate(relevances)]


class ContextPruningTests(unittest.TestCase):
    def test_the_irrelevant_tail_is_dropped(self) -> None:
        kept = prune_context(_hits(0.85, 0.04, 0.03, 0.02))
        self.assertEqual([h["id"] for h in kept], ["0"])

    def test_a_genuinely_good_spread_is_untouched(self) -> None:
        hits = _hits(0.85, 0.80, 0.72, 0.61)
        self.assertEqual(len(prune_context(hits)), 4)

    def test_a_hit_above_the_floor_but_far_below_the_best_is_dropped(self) -> None:
        # 0.25 clears the absolute floor, but next to a 0.95 hit it reads
        # as corroboration it has not earned.
        kept = prune_context(_hits(0.95, 0.25, 0.22))
        self.assertEqual(len(kept), 1)

    def test_the_best_hit_is_never_dropped(self) -> None:
        # Starving the model is worse than one weak passage, and whether
        # to answer at all is should_abstain's decision, not this one's.
        kept = prune_context(_hits(0.05, 0.04, 0.03))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["id"], "0")

    def test_ordering_is_preserved(self) -> None:
        kept = prune_context(_hits(0.9, 0.85, 0.1))
        self.assertEqual([h["id"] for h in kept], ["0", "1"])

    def test_a_single_hit_is_returned_unchanged(self) -> None:
        self.assertEqual(len(prune_context(_hits(0.02))), 1)

    def test_an_empty_result_set_stays_empty(self) -> None:
        self.assertEqual(prune_context([]), [])

    def test_it_is_a_no_op_without_a_reranker_signal(self) -> None:
        # RRF magnitudes are not on the same scale; gating on them is the
        # exact mistake hit_relevance exists to prevent.
        rrf = [{"id": str(i), "score_rrf": 0.016} for i in range(4)]
        self.assertEqual(len(prune_context(rrf)), 4)

    def test_a_partial_signal_does_not_prune(self) -> None:
        # One scored hit and three unscored is not a comparable set.
        mixed = [{"id": "0", "score_norm": 0.9}, {"id": "1", "score_rrf": 0.016}]
        self.assertEqual(len(prune_context(mixed)), 2)

    def test_the_floor_sits_below_the_abstention_threshold(self) -> None:
        # This trims a result set that was already good enough to answer
        # from. It must never be the thing that decides to abstain.
        from app.guardrails import ABSTENTION_THRESHOLD_NORM
        from app.retriever import _CONTEXT_FLOOR

        self.assertLess(_CONTEXT_FLOOR, ABSTENTION_THRESHOLD_NORM)

    def test_thresholds_are_overridable(self) -> None:
        hits = _hits(0.9, 0.5)
        self.assertEqual(len(prune_context(hits, floor=0.0, relative_drop=1.0)), 2)
        self.assertEqual(len(prune_context(hits, floor=0.8, relative_drop=1.0)), 1)

    def test_raw_rerank_logits_are_honoured_too(self) -> None:
        # hit_relevance squashes score_rerank when score_norm is absent.
        hits = [{"id": "0", "score_rerank": 6.0}, {"id": "1", "score_rerank": -6.0}]
        self.assertEqual(len(prune_context(hits)), 1)


class AcronymRecallTests(unittest.TestCase):
    """Hybrid retrieval needs both surface forms to survive expansion."""

    def test_wht_is_expanded_at_all(self) -> None:
        # It was absent from the table while the supervisor matched
        # \\b(withholding|wht)\\b and calculate_withholding existed.
        self.assertIn("wht", _ABBREVIATIONS)
        self.assertNotEqual(expand_abbreviations("what is WHT"), "what is WHT")

    def test_expansion_keeps_the_acronym_for_bm25(self) -> None:
        tokens = BM25SparseEncoder._tokenize(expand_abbreviations("what is WHT"))
        self.assertIn("wht", tokens)
        self.assertIn("withholding", tokens)

    def test_every_real_acronym_survives_its_own_expansion(self) -> None:
        # Replacing the acronym trades one exact match for another and
        # costs BM25 recall on every document that writes it short.
        # "whit" is exempt: it is a typo form, not a corpus token.
        lost = [
            key
            for key, expansion in _ABBREVIATIONS.items()
            if key != "whit" and key not in BM25SparseEncoder._tokenize(expansion)
        ]
        self.assertEqual(lost, [], f"expansion drops the acronym for: {lost}")

    def test_the_spelled_out_form_is_present_too(self) -> None:
        tokens = BM25SparseEncoder._tokenize(expand_abbreviations("VAT rate"))
        self.assertIn("value", tokens)
        self.assertIn("vat", tokens)

    def test_a_typo_form_still_reaches_the_right_expansion(self) -> None:
        self.assertIn("withholding", expand_abbreviations("whit rate").lower())

    def test_unknown_words_are_left_alone(self) -> None:
        self.assertEqual(expand_abbreviations("how do I register"), "how do I register")

    def test_punctuation_is_preserved(self) -> None:
        self.assertTrue(expand_abbreviations("what is WHT?").endswith("?"))


if __name__ == "__main__":
    unittest.main()

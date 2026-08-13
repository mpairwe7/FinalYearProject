"""Ordering of prepended retrieval hits.

"How do I file my annual tax returns?" was answered with the definition
"A return of income is a declaration to URA of income (or loss) for a
period…" — a *what is* answer to a *how do I* question. Retrieval was not at
fault: `_priority_faq_hits` had already sorted "How do I file a return?" to
the top, matching that string is its first sort key, and the procedural
passage from the Taxpayer Starter Pack was retrieved as well.

The loop that inserted those hits reversed them::

    for h in priority_hits:
        hits.insert(0, h)

Every insert lands at index 0, so the group arrives back-to-front and whatever
ranked last becomes citation [1]. `_prepend_unique` replaces it.

These tests pin the property rather than the mechanism: the best-ranked hit in
a prepended group stays first, and prepending never reorders a group.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.service import _prepend_unique  # noqa: E402


def _hit(name: str) -> dict:
    return {"text": f"passage for {name}", "question": name}


class TestPrependPreservesOrder(unittest.TestCase):
    def test_best_ranked_hit_stays_first(self):
        """The regression itself: two priority FAQs, procedural one ranked first."""
        hits = [_hit("existing")]
        seen = {h["text"][:80] for h in hits}
        priority = [_hit("How do I file a return?"), _hit("What is a return filing?")]

        _prepend_unique(hits, priority, seen)

        self.assertEqual(hits[0]["question"], "How do I file a return?")
        self.assertEqual(hits[1]["question"], "What is a return filing?")

    def test_a_group_of_any_size_keeps_its_order(self):
        hits: list[dict] = []
        seen: set[str] = set()
        group = [_hit(f"g{i}") for i in range(5)]

        _prepend_unique(hits, group, seen)

        self.assertEqual([h["question"] for h in hits], ["g0", "g1", "g2", "g3", "g4"])

    def test_groups_prepended_later_sit_above_earlier_ones(self):
        """Callers prepend graph claims, then priority FAQs. Priority must end
        up above graph — the between-group relationship the buggy loop also
        produced, and which this fix deliberately does not change."""
        hits = [_hit("existing")]
        seen = {h["text"][:80] for h in hits}

        _prepend_unique(hits, [_hit("graph-1"), _hit("graph-2")], seen)
        _prepend_unique(hits, [_hit("faq-1"), _hit("faq-2")], seen)

        self.assertEqual(
            [h["question"] for h in hits],
            ["faq-1", "faq-2", "graph-1", "graph-2", "existing"],
        )


class TestPrependDeduplicates(unittest.TestCase):
    def test_a_hit_already_present_is_not_added_again(self):
        existing = _hit("How do I file a return?")
        hits = [existing]
        seen = {existing["text"][:80] for _ in [0]}

        added = _prepend_unique(hits, [_hit("How do I file a return?")], seen)

        self.assertEqual(added, 0)
        self.assertEqual(len(hits), 1)

    def test_returns_the_count_added_so_callers_can_set_retrieval_mode(self):
        """Callers flip retrieval_mode only when something was actually added."""
        hits: list[dict] = []
        seen: set[str] = set()

        self.assertEqual(_prepend_unique(hits, [_hit("a"), _hit("b")], seen), 2)
        self.assertEqual(_prepend_unique(hits, [_hit("a")], seen), 0)
        self.assertEqual(_prepend_unique(hits, [], seen), 0)

    def test_duplicates_within_one_group_collapse(self):
        hits: list[dict] = []
        seen: set[str] = set()

        added = _prepend_unique(hits, [_hit("a"), _hit("a"), _hit("b")], seen)

        self.assertEqual(added, 2)
        self.assertEqual([h["question"] for h in hits], ["a", "b"])


class TestPriorityFaqSortStillRanksProcedureFirst(unittest.TestCase):
    """Guards the other half: the sort feeding _prepend_unique. If the corpus
    ever renames "How do I file a return?", its first sort key silently stops
    matching and the definition wins again — the failure this whole file is
    about, arriving by a different route."""

    def test_the_procedural_question_exists_in_the_corpus(self):
        from app.service import _DATA_DIR, _load_faq_data

        faq_index, _ = _load_faq_data(_DATA_DIR)
        questions = {
            entry["question"].lower()
            for tag in ("processes_systems", "taxpayer_starter_pack", "taxation_handbook_fy2025_26")
            for entry in faq_index.get(tag, [])
        }
        self.assertTrue(
            any("how do i file a return" in q for q in questions),
            "no FAQ question matches the priority sort's top key any more",
        )


if __name__ == "__main__":
    unittest.main()

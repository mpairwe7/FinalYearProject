"""G35: a situational preamble must not cost a row the answer.

``_faq_match_score`` divided coverage by every term the *user* supplied, so
context lowered the score of the row that answers the question:

    "Do I have to charge VAT?"                                    0.700
    "I am opening a hardware store in Jinja. Do I have to         0.273
     charge VAT?"

The floor is 0.58 and the answer is in ``ura_vat_faqs.csv`` throughout. Four
words of situation — *opening, hardware, store, Jinja* — that no FAQ row can
cover are what the row was charged for.

That looked like a reason to run ``extract_question_span`` on every preamble
rather than only on distressed turns, and against a thin index it measured like
one. It is not. Measured 2026-09-01 against a rebuilt 7,970-document index,
ungating the narrowing *cost* fact coverage on the VAT onboarding journey
(81.2% -> 43.8%; turn 1 1.00 -> 0.25, turn 2 0.75 -> 0.00). The FAQ scorer only
decides the answer once retrieval has fallen back to keyword matching; against a
healthy dense index the preamble is useful retrieval context and stripping it
loses signal. The earlier "win" was measured against a stale 729-document
snapshot.

So the narrowing belonged in the scorer, where nothing reaches retrieval, and
that is where it now is — in **one** of the scorer's two terms. Coverage is
computed over the question span's terms on *both* sides of the ratio, so a row
is neither charged for situation it cannot cover nor paid for situation it can.
The first version of the fix kept the whole query in the numerator, meaning to
credit a row that also matched the context; what it did instead was let
situation terms substitute for question terms, and a row about the licences a
hardware store in Jinja needs scored **0.7955** on "Do I have to charge VAT?" —
above the 0.640 of the row that answers it (CodeRabbit, #487). Subject *focus*
keeps the whole query: narrowing that
as well was tried and reverted, because it makes recall trivially 1.0 for any row
containing the one remaining subject, which trips the focus gate and hard-zeroes
the row. Five FAQ rows stopped retrieving their own question — "Bona fide
changing residence – what is exempt?" narrows to "what is exempt?", and the
subject asked about is the half that gets dropped.

The preamble question goes 0.273 -> 0.640 against a bare-question 0.700 and a
0.58 floor. The remaining 0.06 is the focus term, and that is the part that
should move.

These tests pin three things: the dilution is gone, the query that reaches
retrieval is untouched — the measured regression above is the one that matters —
and the distress narrowing still strips a distressed turn. That narrowing now has
no FAQ-scoring justification left; it stays because it also shapes
``binding_query`` and the distress path, not because the scorer needs it.
"""

from __future__ import annotations

import unittest
import unittest.mock as mock

from app.query import extract_question_span
from app.service import _FAQ_MATCH_MIN, _faq_match_score
from app.text_signals import detect_user_distress

_PREAMBLE_QUESTION = "I am opening a hardware store in Jinja. Do I have to charge VAT?"
_BARE_QUESTION = "Do I have to charge VAT?"
_DISTRESSED_QUESTION = (
    "I've tried three times and it still doesn't work!! Do I have to charge VAT?"
)

#: A real row from ura_vat_faqs.csv, shaped as the FAQ index holds it.
_VAT_OBLIGATIONS_ROW = {
    "question": "What are obligations after VAT registration?",
    "answer": (
        "A registered person must charge VAT on taxable supplies, issue tax "
        "invoices, file monthly returns by the 15th, and keep records for six "
        "years."
    ),
    "source": "ura_vat_faqs.csv",
}


class PreambleDilutionTest(unittest.TestCase):
    """The mechanism, pinned so it cannot drift unnoticed."""

    def test_bare_question_clears_the_match_floor(self) -> None:
        self.assertGreaterEqual(
            _faq_match_score(_BARE_QUESTION, _VAT_OBLIGATIONS_ROW),
            _FAQ_MATCH_MIN,
        )

    def test_same_question_with_preamble_now_clears_it_too(self) -> None:
        """G35. This asserted `assertLess` — it was pinning the defect."""
        self.assertGreaterEqual(
            _faq_match_score(_PREAMBLE_QUESTION, _VAT_OBLIGATIONS_ROW),
            _FAQ_MATCH_MIN,
        )

    def test_the_preamble_barely_costs_the_row_anything(self) -> None:
        """0.273 -> 0.640 against a bare-question 0.700.

        Not equality, and deliberately not. Coverage — what the row can fairly
        be charged for — is now judged on the question's own terms. Subject
        *focus* is still judged on everything asked, because narrowing that too
        makes recall trivially 1.0 and trips the focus gate: five FAQ rows
        stopped retrieving their own question when it was tried. The residual
        0.06 is that focus term, and it is the part that should move.
        """
        bare = _faq_match_score(_BARE_QUESTION, _VAT_OBLIGATIONS_ROW)
        preamble = _faq_match_score(_PREAMBLE_QUESTION, _VAT_OBLIGATIONS_ROW)
        self.assertGreaterEqual(preamble, _FAQ_MATCH_MIN)
        self.assertLess(bare - preamble, 0.1)

    def test_a_distressed_preamble_also_clears_the_floor_now(self) -> None:
        """The same root cause reached the distress path; see
        ``test_distress_retrieval_query.py``."""
        self.assertGreaterEqual(
            _faq_match_score(_DISTRESSED_QUESTION, _VAT_OBLIGATIONS_ROW),
            _FAQ_MATCH_MIN,
        )

    def test_the_span_the_scorer_narrows_to_is_the_bare_question(self) -> None:
        self.assertEqual(extract_question_span(_PREAMBLE_QUESTION), _BARE_QUESTION)

    def test_a_row_matching_only_the_situation_is_rejected(self) -> None:
        """The hole the first version of this fix opened.

        Narrowing the denominator without narrowing the numerator let the
        preamble pay for coverage of the question. This row answers the
        situation and says nothing about VAT.
        """
        hardware_licence = {
            "question": "What licences does a hardware store in Jinja need?",
            "answer": (
                "A hardware store opening in Jinja must obtain a trading licence "
                "from the municipal council."
            ),
            "source": "ura_tax_education_faqs.csv",
        }
        self.assertLess(
            _faq_match_score(_PREAMBLE_QUESTION, hardware_licence), _FAQ_MATCH_MIN
        )
        self.assertLess(
            _faq_match_score(_PREAMBLE_QUESTION, hardware_licence),
            _faq_match_score(_PREAMBLE_QUESTION, _VAT_OBLIGATIONS_ROW),
        )

    def test_an_unrelated_row_is_still_rejected(self) -> None:
        """The denominator narrowed; the gate did not open.

        Narrowing what a row is measured against must not turn every row into a
        match — the numerator still has to find the question's own terms.
        """
        unrelated = {
            "question": "How do I clear a consignment at Malaba?",
            "answer": (
                "Lodge a customs declaration through ASYCUDA, pay the assessed "
                "duty, and present the release order at the border post."
            ),
            "source": "ura_customs_valuation_faqs.csv",
        }
        self.assertLess(
            _faq_match_score(_PREAMBLE_QUESTION, unrelated), _FAQ_MATCH_MIN
        )

    def test_a_situational_preamble_is_not_distress(self) -> None:
        self.assertFalse(detect_user_distress(_PREAMBLE_QUESTION))
        self.assertTrue(detect_user_distress(_DISTRESSED_QUESTION))


class SituationalPreambleWiringTest(unittest.TestCase):
    """A calm preamble reaches retrieval intact; a distressed one does not."""

    @classmethod
    def setUpClass(cls) -> None:
        from app import database as db

        db.init_db()
        from app import service

        cls.model = service.ChatModel()

    def _capture(self, message: str) -> tuple[list[str], list[str]]:
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

    def test_calm_preamble_reaches_retrieval_intact(self) -> None:
        # The regression this guards is the *fix*, not the bug: ungating the
        # narrowing measured worse on a full index (see the module docstring).
        seen, _ = self._capture(_PREAMBLE_QUESTION)
        self.assertTrue(seen, "expected at least one search call")
        self.assertTrue(
            any("hardware store" in q for q in seen),
            f"calm preamble should reach retrieval unchanged, got {seen!r}",
        )

    def test_distressed_preamble_is_still_stripped(self) -> None:
        seen, seen_binding = self._capture(_DISTRESSED_QUESTION)
        self.assertTrue(seen, "expected at least one search call")
        for query in seen:
            self.assertNotIn("tried three times", query)
            self.assertIn("VAT", query)
        for binding_query in seen_binding:
            self.assertNotIn("tried three times", binding_query)

    def test_question_without_preamble_is_unchanged(self) -> None:
        seen, seen_binding = self._capture(_BARE_QUESTION)
        self.assertTrue(seen, "expected at least one search call")
        for query in seen:
            self.assertIn("VAT", query)
        for binding_query in seen_binding:
            self.assertIn("VAT", binding_query)


if __name__ == "__main__":
    unittest.main()

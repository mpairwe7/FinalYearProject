"""Why the question-span narrowing stays gated on distress.

`_faq_match_score` divides coverage by the terms the *user* supplied, so a
calm situational preamble dilutes the score of the row that answers the
question exactly the way a distressed one does:

    "Do I have to charge VAT?"                                    0.700
    "I am opening a hardware store in Jinja. Do I have to         0.273
     charge VAT?"

The floor is 0.58, and the answer is in ``ura_vat_faqs.csv`` throughout. That
looks like a reason to run ``extract_question_span`` on every preamble, not
only on distressed turns — and against a thin index it measures like one.

It is not. Measured 2026-09-01 against a rebuilt 7,970-document index, ungating
the narrowing *cost* fact coverage on the VAT onboarding journey (81.2% ->
43.8%; turn 1 1.00 -> 0.25, turn 2 0.75 -> 0.00). The FAQ scorer only decides
the answer once retrieval has fallen back to keyword matching; against a healthy
dense index the preamble is useful retrieval context and stripping it loses
signal. The earlier "win" was measured against a stale 729-document snapshot.

So these tests pin two things: the dilution is real (it is the reason the
distress narrowing exists at all), and a calm preamble is deliberately left
alone. Fixing the dilution belongs in the scorer, not in the call sites.
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

    def test_same_question_with_preamble_falls_under_the_floor(self) -> None:
        # Not a statement about what *should* happen — this is the dilution
        # itself, and it is why the distress narrowing exists. If this ever
        # stops holding, the narrowing has lost its reason to exist and can be
        # removed rather than merely re-gated.
        self.assertLess(
            _faq_match_score(_PREAMBLE_QUESTION, _VAT_OBLIGATIONS_ROW),
            _FAQ_MATCH_MIN,
        )

    def test_narrowing_would_restore_the_score(self) -> None:
        span = extract_question_span(_PREAMBLE_QUESTION)
        self.assertEqual(span, _BARE_QUESTION)
        self.assertGreaterEqual(
            _faq_match_score(span, _VAT_OBLIGATIONS_ROW),
            _FAQ_MATCH_MIN,
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

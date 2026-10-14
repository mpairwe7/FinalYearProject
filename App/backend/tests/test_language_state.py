"""The answer language as conversation state (app.language_state).

Phrases are the load suite's own Luganda and Swahili questions
(tests/load/ngrok_multilang_suite.py), not invented ones. Every case passes
what a real client sends: the web client sends ``locale="en"`` until the
taxpayer touches the picker, which is how auto-detection was lost (PR #529).
"""

from __future__ import annotations

import unittest

from app.language_state import (
    EXPLICIT_SOURCES,
    established_locale,
    normalize_locale_tag,
    resolve_turn_locale,
)

LG_TIN = "Nnyinza ntya okwewandiisa okufuna TIN?"
LG_VAT = "Omusolo gwa VAT mu Uganda guli ku bitundu bimeka?"
SW_TIN = "Ninawezaje kujisajili kupata namba ya TIN?"
SW_WHT = "Kodi ya zuio ni nini?"


def _turn(user: str, locale: str) -> dict[str, str]:
    return {"user_message": user, "bot_reply": "...", "locale": locale}


class NormalizeLocaleTagTest(unittest.TestCase):
    def test_bcp47_and_iso639_tags_name_their_base_language(self) -> None:
        cases = {
            "sw-UG": "sw",
            "SW": "sw",
            "lg": "lg",
            "lug": "lg",
            "swa": "sw",
            "en-GB": "en",
            "eng": "en",
            "sw_KE": "sw",
        }
        for tag, expected in cases.items():
            with self.subTest(tag=tag):
                self.assertEqual(normalize_locale_tag(tag), expected)

    def test_unsupported_or_empty_tags_are_empty(self) -> None:
        for tag in ("", None, "fr", "nyn-UG", "zz"):
            with self.subTest(tag=tag):
                self.assertEqual(normalize_locale_tag(tag), "")


class WebClientContractTest(unittest.TestCase):
    """What the browser sends, and what the taxpayer should get back."""

    def test_default_english_picker_still_detects_luganda_and_swahili(self) -> None:
        for message, expected in ((LG_TIN, "lg"), (SW_TIN, "sw")):
            with self.subTest(message=message):
                decision = resolve_turn_locale(message, requested_locale="en", locale_explicit=False)
                self.assertEqual(decision.locale, expected)
                self.assertEqual(decision.source, "detected")

    def test_client_that_omits_the_flag_treats_en_as_the_default(self) -> None:
        decision = resolve_turn_locale(LG_TIN, requested_locale="en", locale_explicit=None)
        self.assertEqual(decision.locale, "lg")

    def test_client_that_omits_the_flag_treats_lg_and_sw_as_a_choice(self) -> None:
        decision = resolve_turn_locale("How do I register for a TIN?", requested_locale="sw")
        self.assertEqual((decision.locale, decision.source), ("sw", "client_explicit"))

    def test_picked_english_wins_over_a_luganda_message(self) -> None:
        decision = resolve_turn_locale(LG_TIN, requested_locale="en", locale_explicit=True)
        self.assertEqual((decision.locale, decision.source), ("en", "client_explicit"))
        self.assertIn(decision.source, EXPLICIT_SOURCES)

    def test_typed_request_wins_over_the_picker(self) -> None:
        decision = resolve_turn_locale(
            "Please answer in Luganda", requested_locale="en", locale_explicit=True
        )
        self.assertEqual((decision.locale, decision.source), ("lg", "explicit_request"))

    def test_plain_english_question_is_english(self) -> None:
        decision = resolve_turn_locale(
            "How do I register for a TIN?", requested_locale="en", locale_explicit=False
        )
        self.assertEqual(decision.locale, "en")


class ContinuityTest(unittest.TestCase):
    """The stored answer language carries the thread; only a clear switch moves it."""

    def test_short_follow_up_stays_in_the_thread_language(self) -> None:
        history = [_turn(LG_TIN, "lg")]
        decision = resolve_turn_locale("What about 150m?", requested_locale="en", history=history)
        self.assertEqual((decision.locale, decision.source), ("lg", "continuity"))

    def test_full_english_sentence_switches_a_luganda_thread(self) -> None:
        history = [_turn(LG_TIN, "lg"), _turn(LG_VAT, "lg")]
        decision = resolve_turn_locale(
            "What documents do I need for the application?",
            requested_locale="lg",
            locale_explicit=False,
            history=history,
        )
        self.assertEqual((decision.locale, decision.source), ("en", "switched"))
        self.assertTrue(decision.switched)

    def test_code_switched_luganda_with_english_terms_stays_luganda(self) -> None:
        history = [_turn(LG_TIN, "lg")]
        decision = resolve_turn_locale(
            "Withholding tax eri ebitundu bimeka for my company?",
            requested_locale="lg",
            locale_explicit=False,
            history=history,
        )
        self.assertEqual(decision.locale, "lg")

    def test_swahili_message_moves_a_luganda_thread(self) -> None:
        history = [_turn(LG_TIN, "lg")]
        decision = resolve_turn_locale(SW_TIN, requested_locale="lg", locale_explicit=False, history=history)
        self.assertEqual((decision.locale, decision.source), ("sw", "switched"))

    def test_luganda_message_moves_an_english_thread(self) -> None:
        history = [_turn("How do I register for a TIN?", "en")]
        decision = resolve_turn_locale(LG_VAT, requested_locale="en", locale_explicit=False, history=history)
        self.assertEqual((decision.locale, decision.source), ("lg", "switched"))

    def test_stored_locale_beats_re_detecting_old_text(self) -> None:
        # The taxpayer typed English, but asked for (and got) Luganda answers.
        history = [_turn("How do I register for a TIN?", "lg")]
        self.assertEqual(established_locale(history), "lg")
        decision = resolve_turn_locale("And VAT?", requested_locale="en", history=history)
        self.assertEqual(decision.locale, "lg")

    def test_websocket_cache_carries_the_locale_on_the_assistant_message(self) -> None:
        history = [
            {"role": "user", "content": SW_WHT},
            {"role": "assistant", "content": "Kodi ya zuio ni ...", "locale": "sw"},
        ]
        self.assertEqual(established_locale(history), "sw")

    def test_rows_without_a_stored_locale_fall_back_to_the_taxpayers_words(self) -> None:
        history = [{"user_message": SW_TIN, "bot_reply": "Unaweza kujisajili ..."}]
        self.assertEqual(established_locale(history), "sw")
        self.assertEqual(established_locale([]), "")


class ProfileSeedTest(unittest.TestCase):
    def test_saved_language_answers_an_ambiguous_first_message(self) -> None:
        decision = resolve_turn_locale("TIN?", requested_locale="en", locale_explicit=False, profile_locale="lg")
        self.assertEqual((decision.locale, decision.source), ("lg", "profile"))

    def test_saved_language_never_overrides_what_was_typed(self) -> None:
        decision = resolve_turn_locale(
            "How do I register for a TIN?", requested_locale="en", locale_explicit=False, profile_locale="sw"
        )
        self.assertEqual(decision.locale, "en")

    def test_saved_language_does_not_beat_an_established_thread(self) -> None:
        decision = resolve_turn_locale(
            "TIN?", requested_locale="en", history=[_turn(SW_TIN, "sw")], profile_locale="lg"
        )
        self.assertEqual(decision.locale, "sw")


if __name__ == "__main__":
    unittest.main()

"""Everything the receptionist says itself exists in English, Luganda and Swahili."""

from __future__ import annotations

import unittest

from app.receptionist import phrases
from app.receptionist.clarify import classify_yes_no


class PhraseTableIsComplete(unittest.TestCase):
    def test_every_key_exists_in_every_language(self):
        for lang in ("en", "lg", "sw"):
            table = phrases._PHRASES[lang]
            with self.subTest(lang=lang):
                self.assertEqual(set(table), set(phrases.KEYS))
                self.assertTrue(all(text.strip() for text in table.values()))

    def test_every_language_has_its_own_fillers(self):
        for lang in ("en", "lg", "sw"):
            with self.subTest(lang=lang):
                self.assertGreaterEqual(len(phrases.fillers(lang)), 6)
        self.assertNotEqual(set(phrases.fillers("lg")), set(phrases.fillers("en")))

    def test_placeholders_match_across_languages(self):
        import string

        for key in phrases.KEYS:
            fields = {
                lang: {f for _, f, _, _ in string.Formatter().parse(phrases._PHRASES[lang][key]) if f}
                for lang in ("en", "lg", "sw")
            }
            with self.subTest(key=key):
                self.assertEqual(fields["lg"], fields["en"])
                self.assertEqual(fields["sw"], fields["en"])

    def test_lg_and_sw_are_marked_unreviewed_until_a_native_speaker_signs_off(self):
        self.assertEqual(phrases.UNREVIEWED, frozenset({"lg", "sw"}))


class Greeting(unittest.TestCase):
    def test_the_call_opens_with_the_agreed_english_line(self):
        greeting = phrases.phrase("greeting", "en")
        self.assertTrue(greeting.startswith("Hi, thanks for contacting URA."))
        self.assertIn("preferred language", greeting)
        self.assertTrue(greeting.endswith("How can I help you today?"))
        for name in ("English", "Luganda", "Swahili"):
            self.assertIn(name, greeting)

    def test_the_caller_hears_first_that_it_is_an_ai_and_a_person_is_one_request_away(self):
        greeting = phrases.phrase("greeting", "en")
        self.assertIn("I'm your AI assistant today.", greeting)  # "virtual" was heard as "vital"
        self.assertIn("You can ask for an officer at any time.", greeting)
        self.assertIn("AI", phrases.phrase("greeting", "lg"))
        self.assertIn("AI", phrases.phrase("greeting", "sw"))

    def test_the_brain_greets_with_it(self):
        from app.receptionist.brain import GREETING_TEXT

        self.assertEqual(GREETING_TEXT, phrases.phrase("greeting", "en"))


class Lookup(unittest.TestCase):
    def test_formatting(self):
        self.assertEqual(phrases.phrase("clarify_term", "lg", term="TIN"), "Nsonyiwa, ogambye TIN?")
        self.assertIn("TIC-1458BC4D", phrases.phrase("reference_screen", "sw", ref="TIC-1458BC4D"))

    def test_an_unknown_language_falls_back_to_english(self):
        self.assertEqual(phrases.phrase("transfer", "nyn"), phrases.phrase("transfer", "en"))
        self.assertEqual(phrases.fillers("nyn"), phrases.fillers("en"))

    def test_pick_filler_never_repeats_and_stays_in_language(self):
        last = None
        for _ in range(30):
            current = phrases.pick_filler("lg", last)
            self.assertIn(current, phrases.fillers("lg"))
            self.assertNotEqual(current, last)
            last = current

    def test_prewarm_list_leaves_out_lines_only_known_mid_call(self):
        lines = phrases.prewarm_phrases("lg")
        self.assertTrue(all("{" not in line for line in lines))
        self.assertIn(phrases.phrase("transfer", "lg"), lines)
        self.assertTrue(set(phrases.fillers("lg")) <= set(lines))

    def test_screen_lines_are_never_synthesised(self):
        for lang in ("en", "lg", "sw"):
            lines = phrases.prewarm_phrases(lang)
            with self.subTest(lang=lang):
                self.assertNotIn(phrases.phrase("crisis_screen", lang), lines)
                self.assertNotIn(phrases.phrase("tollfree_screen", lang), lines)


class NumbersTheVoiceCannotSay(unittest.TestCase):
    """Orpheus loops on phone numbers and said "999 or 112" as "nine nine or one twelve"."""

    def test_no_spoken_line_carries_a_number(self):
        import re

        for lang in ("en", "lg", "sw"):
            for key in phrases.KEYS:
                if key in phrases.UNSPOKEN:
                    continue
                with self.subTest(lang=lang, key=key):
                    self.assertIsNone(re.search(r"\d", phrases.phrase(key, lang)))

    def test_the_crisis_numbers_are_the_chat_s_verified_ones(self):
        from app.text_signals import crisis_support_reply

        chat = crisis_support_reply()
        for number in ("999", "112", "0800 21 21 21"):
            self.assertIn(number, chat)
            for lang in ("en", "lg", "sw"):
                with self.subTest(number=number, lang=lang):
                    self.assertIn(number, phrases.phrase("crisis_screen", lang))
        # Spoken in words, in each call language.
        self.assertIn("nine nine nine, or one one two", phrases.phrase("crisis_support", "en"))
        self.assertIn("mwenda mwenda mwenda", phrases.phrase("crisis_support", "lg"))
        self.assertIn("tisa tisa tisa", phrases.phrase("crisis_support", "sw"))


class YesNoInEachLanguage(unittest.TestCase):
    def test_luganda(self):
        self.assertEqual(classify_yes_no("Yee", "lg"), "yes")
        self.assertEqual(classify_yes_no("Nedda", "lg"), "no")
        self.assertEqual(classify_yes_no("yes", "lg"), "yes")  # English still understood

    def test_swahili(self):
        self.assertEqual(classify_yes_no("Ndiyo", "sw"), "yes")
        self.assertEqual(classify_yes_no("Hapana", "sw"), "no")
        self.assertEqual(classify_yes_no("ndiyo, hapana", "sw"), "yes")

    def test_english_calls_do_not_learn_foreign_words(self):
        self.assertEqual(classify_yes_no("Ndiyo", "en"), "neither")


if __name__ == "__main__":
    unittest.main()

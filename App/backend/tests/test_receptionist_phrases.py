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
        self.assertTrue(greeting.startswith("Hi, thanks for contacting URA. I'm your assistant today."))
        self.assertIn("preferred language", greeting)
        self.assertTrue(greeting.endswith("How can I help you today?"))
        for name in ("English", "Luganda", "Swahili"):
            self.assertIn(name, greeting)

    def test_the_brain_greets_with_it(self):
        from app.receptionist.brain import GREETING_TEXT

        self.assertEqual(GREETING_TEXT, phrases.phrase("greeting", "en"))


class Lookup(unittest.TestCase):
    def test_formatting(self):
        self.assertEqual(phrases.phrase("clarify_term", "lg", term="TIN"), "Nsonyiwa, ogambye TIN?")
        self.assertIn("URA-CALL", phrases.phrase("officers_busy", "sw", ref="URA-CALL"))

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

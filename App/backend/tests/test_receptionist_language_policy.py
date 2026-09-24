"""The language policy: when a receptionist call locks, switches, or stays put.

Pure logic — ``app.receptionist.language`` imports nothing from Pipecat — so
every rule is exercised here with hand-built votes.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.receptionist.language import (
    LanguagePolicy,
    LanguageVote,
    PolicyConfig,
    detect_explicit_request,
    fuse,
    lexical_hits,
    text_language,
)


def vote(top: str, p: float, speech_s: float = 3.0, text: str = "") -> LanguageVote:
    rest = [lang for lang in ("en", "sw", "lg") if lang != top]
    probs = {top: p, **{lang: round((1.0 - p) / 2, 4) for lang in rest}}
    return LanguageVote(probs, top, speech_s, text)


def policy(**kwargs) -> LanguagePolicy:
    return LanguagePolicy(active="en", config=PolicyConfig(**kwargs))


class ShortUtterancesNeverDecide(unittest.TestCase):
    def test_a_greeting_is_ignored_however_confident(self):
        p = policy()
        d = p.observe(vote("lg", 0.99, speech_s=0.8, text="Gyebale ko"))
        self.assertEqual((d.action, d.reason), ("none", "too_short"))
        self.assertFalse(p.locked)

    def test_a_vote_with_no_language_is_ignored(self):
        p = policy()
        d = p.observe(LanguageVote({}, "", 3.0))
        self.assertEqual(d.action, "none")
        self.assertFalse(p.locked)


class FirstContentUtteranceLocks(unittest.TestCase):
    def test_english_locks_english_without_a_switch(self):
        p = policy()
        d = p.observe(vote("en", 0.8))
        self.assertEqual((d.action, d.target, d.source), ("lock", "en", "auto"))
        self.assertTrue(p.locked)
        self.assertEqual(p.switch_count, 0)

    def test_luganda_switches_and_locks(self):
        p = policy()
        d = p.observe(vote("lg", 0.85))
        self.assertEqual((d.action, d.target), ("switch", "lg"))
        self.assertTrue(p.locked)
        self.assertEqual(p.active, "lg")
        self.assertEqual(p.switch_count, 1)

    def test_a_weak_vote_for_another_language_leaves_the_call_open(self):
        """Moving an English caller off English on a coin-flip is the costly error."""
        p = policy()
        d = p.observe(vote("lg", 0.6))
        self.assertEqual((d.action, d.reason), ("none", "weak_first_vote"))
        self.assertFalse(p.locked)
        self.assertEqual(p.active, "en")

    def test_a_weak_vote_for_the_opening_language_leaves_the_call_open_too(self):
        """A real Luganda caller's first question voted English at 0.63 and locked English."""
        p = policy()
        d = p.observe(vote("en", 0.63, text="Rwandakuyango na sema ya baama."))
        self.assertEqual((d.action, d.reason), ("none", "weak_first_vote"))
        self.assertFalse(p.locked)
        d = p.observe(vote("lg", 0.78))
        self.assertEqual((d.action, d.target), ("switch", "lg"))


class LockedCallsNeedEvidenceToMove(unittest.TestCase):
    def locked(self) -> LanguagePolicy:
        p = policy()
        p.observe(vote("en", 0.9))
        return p

    def test_one_confident_vote_switches(self):
        p = self.locked()
        d = p.observe(vote("lg", 0.95))
        self.assertEqual((d.action, d.target, d.reason), ("switch", "lg", "confident_vote"))

    def test_one_moderate_vote_only_accumulates(self):
        p = self.locked()
        d = p.observe(vote("lg", 0.8))
        self.assertEqual((d.action, d.reason), ("none", "accumulating"))
        self.assertEqual(p.active, "en")

    def test_two_moderate_votes_in_a_row_switch(self):
        p = self.locked()
        p.observe(vote("lg", 0.8))
        d = p.observe(vote("lg", 0.75))
        self.assertEqual((d.action, d.target, d.reason), ("switch", "lg", "hysteresis"))

    def test_one_vote_for_the_current_language_does_not_hide_the_others(self):
        """Two Luganda votes among the last three move the call, whatever sits between them."""
        p = self.locked()
        p.observe(vote("lg", 0.8))
        self.assertEqual(p.observe(vote("en", 0.9)).reason, "same_language")
        d = p.observe(vote("lg", 0.8))
        self.assertEqual((d.action, d.target, d.reason), ("switch", "lg", "hysteresis"))

    def test_votes_older_than_the_window_are_forgotten(self):
        p = self.locked()
        p.observe(vote("lg", 0.8))
        p.observe(vote("en", 0.9))
        p.observe(vote("en", 0.9))
        d = p.observe(vote("lg", 0.8))
        self.assertEqual((d.action, d.reason), ("none", "accumulating"))

    def test_the_real_luganda_caller_moves_within_three_turns(self):
        """Votes from a real Luganda caller's phone audio, locked into English (2026-09-24)."""
        p = policy()
        p.observe(vote("en", 0.73, text="Can we go to Ingolian in this one?"))
        self.assertEqual(p.observe(vote("lg", 0.78)).reason, "accumulating")
        self.assertEqual(p.observe(vote("en", 0.71, text="Katubuge oranku imashini.")).reason, "same_language")
        d = p.observe(vote("lg", 0.72))
        self.assertEqual((d.action, d.target), ("switch", "lg"))

    def test_moderate_votes_for_two_different_languages_do_not_add_up(self):
        p = self.locked()
        p.observe(vote("lg", 0.8))
        d = p.observe(vote("sw", 0.8))
        self.assertEqual((d.action, d.reason), ("none", "accumulating"))
        self.assertEqual(p.active, "en")

    def test_a_vote_below_the_support_bar_does_not_count(self):
        p = self.locked()
        p.observe(vote("lg", 0.8))
        d = p.observe(vote("lg", 0.4))
        self.assertEqual((d.action, d.reason), ("none", "weak_vote"))
        self.assertEqual(p.active, "en")

    def test_a_moderate_vote_supports_one_before_it(self):
        p = self.locked()
        p.observe(vote("lg", 0.8))
        d = p.observe(vote("lg", 0.55))
        self.assertEqual((d.action, d.target, d.reason), ("switch", "lg", "hysteresis"))

    def test_hysteresis_turns_is_configurable(self):
        p = policy(hysteresis_turns=3)
        p.observe(vote("en", 0.9))
        p.observe(vote("lg", 0.8))
        self.assertEqual(p.observe(vote("lg", 0.8)).action, "none")
        self.assertEqual(p.observe(vote("lg", 0.8)).action, "switch")


class CodeSwitchedLuganda(unittest.TestCase):
    """Luganda full of English tax terms reads as English to the acoustic model."""

    def test_luganda_words_with_some_lg_probability_make_it_luganda(self):
        p = policy()
        v = LanguageVote({"en": 0.58, "lg": 0.4, "sw": 0.02}, "en", 2.4, "Nsaba okumanya ku TIN yange")
        d = p.observe(v)
        self.assertEqual((d.action, d.target), ("switch", "lg"))

    def test_the_rule_needs_the_acoustic_model_to_give_luganda_a_chance(self):
        p = policy()
        v = LanguageVote({"en": 0.8, "lg": 0.15, "sw": 0.05}, "en", 2.4, "Nsaba okumanya ku TIN yange")
        self.assertEqual(p.observe(v).action, "lock")
        self.assertEqual(p.active, "en")

    def test_one_borrowed_word_is_not_luganda(self):
        p = policy()
        v = LanguageVote({"en": 0.6, "lg": 0.38, "sw": 0.02}, "en", 2.4, "Webale, what is the VAT rate?")
        self.assertEqual(p.observe(v).target, "en")

    def test_a_locked_luganda_call_does_not_flip_on_code_switching(self):
        p = policy()
        p.observe(vote("lg", 0.9, text="Nnyinza ntya okufuna TIN?"))
        v = LanguageVote({"en": 0.62, "lg": 0.36, "sw": 0.02}, "en", 2.0, "VAT yange ntya okugisasula?")
        d = p.observe(v)
        self.assertEqual(d.action, "none")
        self.assertEqual(p.active, "lg")


class LugandaHeardAsSwahili(unittest.TestCase):
    """Measured on the synthetic mixed set: SALT often votes Swahili for
    code-switched Luganda. The text settles Luganda vs Swahili."""

    def test_swahili_vote_with_luganda_text_is_luganda(self):
        p = policy()
        v = LanguageVote({"en": 0.25, "sw": 0.58, "lg": 0.17}, "sw", 2.2, "Rental income tax nsasula mmeka?")
        self.assertEqual(p.observe(v).target, "lg")

    def test_a_weak_luganda_vote_with_luganda_text_moves_the_call(self):
        p = policy()
        v = LanguageVote({"en": 0.3, "sw": 0.02, "lg": 0.68}, "lg", 2.5, "Njagala okuwandiisa bizinisi yange ku EFRIS.")
        d = p.observe(v)
        self.assertEqual((d.action, d.target), ("switch", "lg"))

    def test_real_swahili_stays_swahili(self):
        p = policy()
        v = LanguageVote({"en": 0.05, "sw": 0.8, "lg": 0.15}, "sw", 3.0, "Naomba kujua kodi ya mapato ni kiasi gani")
        self.assertEqual(p.observe(v).target, "sw")

    def test_english_acoustics_are_not_overruled_by_one_place_name(self):
        p = policy()
        v = LanguageVote({"en": 0.9, "sw": 0.05, "lg": 0.05}, "en", 3.0, "I live in Kampala, what is the VAT rate?")
        self.assertEqual(p.observe(v).target, "en")

    def test_the_text_cannot_pull_an_english_sounding_utterance_to_luganda(self):
        # Acoustics say English (Bantu mass 0.2) and P(lg) is under the threshold.
        p = policy()
        v = LanguageVote({"en": 0.8, "sw": 0.1, "lg": 0.1}, "en", 3.0, "Nsaba okumanya ku TIN yange")
        self.assertEqual(p.observe(v).target, "en")


class OneTurnSwitchWhenTheWordsAgree(unittest.TestCase):
    def luganda_call(self) -> LanguagePolicy:
        p = policy()
        p.observe(vote("lg", 0.95, text="Nnyinza ntya okufuna TIN?"))
        return p

    def test_english_with_english_words_leaves_luganda_in_one_turn(self):
        p = self.luganda_call()
        d = p.observe(vote("en", 0.8, text="What is the standard VAT rate in Uganda?"))
        self.assertEqual((d.action, d.target, d.reason), ("switch", "en", "text_confirmed"))

    def test_an_english_sounding_luganda_question_stays_luganda(self):
        """Measured: P(en) 0.89 for "Withholding tax eri ebitundu bimeka?"."""
        p = self.luganda_call()
        v = LanguageVote({"en": 0.89, "sw": 0.02, "lg": 0.09}, "en", 2.5, "Withholding tax eri ebitundu bimeka?")
        d = p.observe(v)
        self.assertEqual(d.action, "none")
        self.assertEqual(p.active, "lg")

    def test_without_corroborating_words_it_still_takes_two_votes(self):
        p = self.luganda_call()
        self.assertEqual(p.observe(vote("en", 0.8, text="")).reason, "accumulating")
        self.assertEqual(p.observe(vote("en", 0.8, text="")).action, "switch")

    def test_words_of_the_current_language_block_the_shortcut(self):
        p = self.luganda_call()
        d = p.observe(vote("en", 0.8, text="Nsaba what is the rate"))
        self.assertNotEqual(d.reason, "text_confirmed")


class ExplicitRequests(unittest.TestCase):
    def test_requests_in_each_language(self):
        cases = {
            "Can we speak Luganda?": "lg",
            "Tuyinza okwogera Oluganda?": "lg",
            "Naomba tuongee Kiswahili": "sw",
            "Please speak English": "en",
            "mu lungereza": "en",
            "Kwa Kiingereza tafadhali": "en",
        }
        for text, lang in cases.items():
            with self.subTest(text=text):
                self.assertEqual(detect_explicit_request(text), lang)

    def test_ordinary_sentences_are_not_requests(self):
        for text in ("What is the VAT rate?", "English is fine, what about PAYE?", "Omusolo gwa VAT guli ki?"):
            with self.subTest(text=text):
                self.assertIsNone(detect_explicit_request(text))

    def test_the_language_named_last_wins(self):
        self.assertEqual(detect_explicit_request("Not in English — in Luganda please"), "lg")

    def test_an_explicit_request_switches_even_a_short_utterance(self):
        p = policy()
        p.observe(vote("en", 0.95))
        d = p.observe(vote("en", 0.95, speech_s=1.1, text="Speak Swahili"))
        self.assertEqual((d.action, d.target, d.source), ("switch", "sw", "explicit"))

    def test_a_request_for_the_current_language_just_locks(self):
        p = policy()
        d = p.observe(vote("en", 0.9, text="Can we speak English?"))
        self.assertEqual((d.action, d.target, d.source), ("lock", "en", "explicit"))


class OnScreenOverride(unittest.TestCase):
    def test_override_switches_and_holds_against_auto_votes(self):
        p = policy()
        d = p.set_override("lg")
        self.assertEqual((d.action, d.target, d.source), ("switch", "lg", "override"))
        d = p.observe(vote("en", 0.99))
        self.assertEqual((d.action, d.reason), ("none", "override_locked"))
        self.assertEqual(p.active, "lg")

    def test_a_spoken_request_still_moves_an_overridden_call(self):
        p = policy()
        p.set_override("lg")
        d = p.observe(vote("en", 0.9, text="Can we speak English?"))
        self.assertEqual((d.action, d.target), ("switch", "en"))
        self.assertEqual(p.override, "en")

    def test_an_unoffered_language_is_refused(self):
        p = LanguagePolicy(active="en", config=PolicyConfig(languages=("en", "lg")))
        self.assertEqual(p.set_override("sw").action, "none")
        self.assertIsNone(p.override)

    def test_votes_for_an_unoffered_language_change_nothing(self):
        p = LanguagePolicy(active="en", config=PolicyConfig(languages=("en", "lg")))
        p.observe(vote("en", 0.9))
        self.assertEqual(p.observe(vote("sw", 0.99)).action, "none")


class TextHelpers(unittest.TestCase):
    def test_lexical_hits(self):
        hits = lexical_hits("Nsaba okumanya ku TIN yange")
        self.assertGreaterEqual(hits["lg"], 3)
        self.assertEqual(hits["sw"], 0)
        self.assertGreaterEqual(lexical_hits("Naomba kujua kodi ya VAT ni kiasi gani")["sw"], 3)

    def test_text_language_needs_two_words(self):
        self.assertEqual(text_language("Asante, what is VAT?", "en", ("en", "sw")), "en")
        self.assertEqual(text_language("Naomba kujua kodi ya mapato", "en", ("en", "sw")), "sw")
        self.assertEqual(text_language("", "sw", ("en", "sw")), "sw")

    def test_fusion_lets_text_overrule_an_unsure_acoustic_vote(self):
        v = LanguageVote({"en": 0.55, "lg": 0.4, "sw": 0.05}, "en", 2.0, "Nsaba okumanya ku omusolo gwange")
        fused = fuse(v)
        self.assertEqual(fused.top, "lg")
        self.assertAlmostEqual(fused.confidence, 0.8)

    def test_fusion_leaves_a_confident_vote_alone(self):
        v = LanguageVote({"en": 0.85, "lg": 0.1, "sw": 0.05}, "en", 2.0, "Nsaba okumanya ku omusolo gwange")
        self.assertIs(fuse(v), v)


class AnEngineCanReportTheLanguage(unittest.TestCase):
    """Gemini Live hears Luganda that the language token took for English."""

    def test_a_report_moves_a_call_locked_in_english(self):
        p = policy()
        p.observe(vote("en", 0.95))
        d = p.report("lg", "gemini_live_heard")
        self.assertEqual((d.action, d.target, d.reason, d.source), ("switch", "lg", "gemini_live_heard", "auto"))
        self.assertTrue(p.locked)

    def test_a_report_of_the_current_language_changes_nothing(self):
        p = policy()
        p.observe(vote("lg", 0.95))
        self.assertEqual(p.report("lg", "gemini_live_heard").action, "none")

    def test_an_on_screen_choice_beats_a_report(self):
        p = policy()
        p.set_override("en")
        d = p.report("lg", "gemini_live_heard")
        self.assertEqual((d.action, d.reason), ("none", "override_locked"))
        self.assertEqual(p.active, "en")

    def test_an_unoffered_language_is_not_reported_in(self):
        p = policy(languages=("en", "sw"))
        self.assertEqual(p.report("lg", "gemini_live_heard").reason, "unsupported_language")


class ConfigFromEnvironment(unittest.TestCase):
    def test_env_overrides(self):
        env = {
            "RECEPTIONIST_LID_MIN_SPEECH_S": "3.0",
            "RECEPTIONIST_LID_SWITCH_CONFIDENCE": "0.95",
            "RECEPTIONIST_LID_HYSTERESIS_CONFIDENCE": "0.75",
            "RECEPTIONIST_LID_HYSTERESIS_TURNS": "3",
            "RECEPTIONIST_LID_HYSTERESIS_WINDOW": "2",
            "RECEPTIONIST_LID_SUPPORT_CONFIDENCE": "0.6",
            "RECEPTIONIST_LG_MIX_THRESHOLD": "0.4",
            "RECEPTIONIST_LANGUAGES": "en,lg,xx",
        }
        with patch.dict(os.environ, env):
            cfg = PolicyConfig.from_env()
        self.assertEqual(cfg.min_speech_s, 3.0)
        self.assertEqual(cfg.switch_confidence, 0.95)
        self.assertEqual(cfg.hysteresis_confidence, 0.75)
        self.assertEqual(cfg.hysteresis_turns, 3)
        self.assertEqual(cfg.hysteresis_window, 3)  # never narrower than the votes it must hold
        self.assertEqual(cfg.support_confidence, 0.6)
        self.assertEqual(cfg.lg_mix_threshold, 0.4)
        self.assertEqual(cfg.languages, ("en", "lg"))

    def test_luganda_is_never_routed_to_gemini(self):
        from app.receptionist.config import get_engine_by_language

        with patch.dict(os.environ, {"RECEPTIONIST_ENGINE_BY_LANGUAGE": "en:cascaded,sw:cascaded,lg:gemini_live,xx:bad"}):
            table = get_engine_by_language()
        self.assertEqual(table, {"en": "cascaded", "sw": "cascaded", "lg": "cascaded"})


if __name__ == "__main__":
    unittest.main()

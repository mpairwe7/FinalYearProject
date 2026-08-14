"""Sunbird must serve every Ugandan language it actually covers.

Two configuration defects sent four of those languages to a general model:

  * LOCALE_TO_SUNBIRD had no entry for `teo` or `lgg`, while
    TRANSLATION_LANGUAGES listed both as supported. Every caller resolving a
    locale through that table got None and fell through. Verified against the
    live API: eng->teo and eng->lgg both translate in about 2s. The omission
    was easy to miss because those locale codes EQUAL their Sunbird codes, so
    any path passing the raw string through unmapped worked and only the
    lookups broke.

  * the tier order gated on `"lg" in (source, target)`, so only Luganda led
    with Sunbird. Runyankole, Acholi, Ateso and Lugbara led with Gemini —
    which answers, so Sunbird was never reached. Measured in production:
    lg 3.2s on sunbird_cloud, nyn/ach/teo 17-18s on gemini_flash.

Swahili is the deliberate exception: it has a Sunbird TTS voice but their
translate endpoint does not serve it, so it must keep leading with Gemini.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import sunbird  # noqa: E402
from app.speech_service import SpeechModel  # noqa: E402


class TestTheLocaleTableCoversWhatWeClaimToSupport(unittest.TestCase):
    def test_every_translation_language_is_reachable_from_a_locale(self):
        """The invariant that was violated: a language listed as supported
        must be resolvable from some locale, or it is unreachable in practice."""
        mapped = set(sunbird.LOCALE_TO_SUNBIRD.values())
        missing = sunbird.TRANSLATION_LANGUAGES - mapped
        self.assertEqual(
            missing,
            set(),
            f"{sorted(missing)} are declared translatable but no locale maps to them",
        )

    def test_ateso_and_lugbara_resolve(self):
        self.assertEqual(sunbird.LOCALE_TO_SUNBIRD.get("teo"), "teo")
        self.assertEqual(sunbird.LOCALE_TO_SUNBIRD.get("lgg"), "lgg")


class TestTheSameTableProtectsSpeechToText(unittest.TestCase):
    """The STT path resolves its language through this table too, with an
    "eng" default — so a missing entry does not fail loudly, it submits the
    audio TAGGED AS ENGLISH. Sunbird then transcribes with an English model and
    returns confident nonsense, which nothing downstream can detect. That is
    documented in speech_service for Swahili/Runyankole/Acholi; Ateso and
    Lugbara had the same hole until the table gained their entries.
    """

    def test_no_supported_locale_silently_resolves_to_english(self):
        for locale in ("lg", "nyn", "ach", "teo", "lgg"):
            with self.subTest(locale=locale):
                code = sunbird.LOCALE_TO_SUNBIRD.get(locale, "eng")
                self.assertNotEqual(
                    code, "eng", f"{locale} audio would be transcribed as English"
                )
                self.assertIn(code, sunbird.TRANSLATION_LANGUAGES)


class TestSunbirdLeadsForEveryLanguageItServes(unittest.TestCase):
    """Ordering, asserted through the real function rather than a copy of its
    predicate — an earlier test in this repo passed while the wiring it was
    supposed to cover had been removed."""

    def _first_backend_called(self, source: str, target: str) -> str:
        model = SpeechModel.__new__(SpeechModel)
        model._mt = None
        model._chat_model = None
        order: list[str] = []

        def sunbird_translate(text, src, tgt):
            order.append("sunbird")
            return "translated"

        def gemini(self_, text, src, tgt):  # bound method signature
            order.append("gemini")
            return "translated"

        with patch.object(sunbird, "translate", side_effect=sunbird_translate), patch.object(
            sunbird, "is_available", return_value=True
        ), patch.object(SpeechModel, "_gemini_translate", gemini), patch.object(
            SpeechModel, "_cf_llama_translate", lambda *a, **k: None
        ):
            model._do_translate("Where is the tax office?", source, target)
        return order[0] if order else "none"

    def test_every_ugandan_language_leads_with_sunbird(self):
        for target in ("lg", "nyn", "ach", "teo", "lgg"):
            with self.subTest(target=target):
                self.assertEqual(
                    self._first_backend_called("en", target),
                    "sunbird",
                    f"en->{target} must try Sunbird before a general model",
                )

    def test_the_reverse_direction_also_leads_with_sunbird(self):
        self.assertEqual(self._first_backend_called("nyn", "en"), "sunbird")

    def test_swahili_still_leads_with_gemini(self):
        """Sunbird's translate endpoint does not serve Swahili; leading with it
        would spend a guaranteed-failed call first."""
        self.assertEqual(self._first_backend_called("en", "sw"), "gemini")

    def test_an_unrelated_language_leads_with_gemini(self):
        self.assertEqual(self._first_backend_called("en", "fr"), "gemini")


if __name__ == "__main__":
    unittest.main()

"""A chosen narration voice has to survive the trip to Sunbird.

`text_to_speech` looked its speaker up from `TTS_VOICES[locale]` and took no
voice argument at all, so the `voice` a caller sent to /v1/tts was dropped on
the floor for every Ugandan language.

An earlier version of this docstring said English was unaffected because
"edge-tts honours the voice name directly". That was an assumption, not a
finding, and it was wrong: `_synthesize_edge_tts` ignored the argument too, so
all four English voices synthesized as en-US-AriaNeural. It took a live probe of
the deployment to catch. See test_edge_voice_selection.py for that half.

The catalog it now validates against was confirmed against the live
/tasks/audio/speech endpoint — 21 tags, 21 with fetchable audio, 0 rejected.
That verification matters more than it looks: an unusable tag is not a loud
failure but a voice a person can select and never hear, because the request
400s and the chain degrades to an English voice reading Luganda.

Validation is per-locale on purpose. Forwarding a tag from another language
does not error — orpheus either rejects it or synthesises the wrong language,
and both are worse than quietly using the right default.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import sunbird  # noqa: E402


class ResolveTtsVoiceTests(unittest.TestCase):
    def test_no_request_uses_the_locale_default(self):
        for locale, expected in sunbird.TTS_VOICES.items():
            with self.subTest(locale=locale):
                self.assertEqual(sunbird.resolve_tts_voice(locale, None), expected)
                self.assertEqual(sunbird.resolve_tts_voice(locale, ""), expected)

    def test_catalogue_alternate_is_honoured(self):
        self.assertEqual(
            sunbird.resolve_tts_voice("lg", "waxal_lug_0005"), "waxal_lug_0005"
        )
        self.assertEqual(
            sunbird.resolve_tts_voice("ach", "waxal_ach_0008"), "waxal_ach_0008"
        )

    def test_tag_from_another_language_is_not_forwarded(self):
        # The whole point: an Acholi speaker must never synthesise Luganda.
        self.assertEqual(sunbird.resolve_tts_voice("lg", "salt_ach_0001"), "salt_lug_0001")
        self.assertEqual(sunbird.resolve_tts_voice("ach", "waxal_lug_0002"), "salt_ach_0001")

    def test_voice_names_from_other_backends_fall_back(self):
        # `synthesize` fills `voice` with a Piper/edge name when the caller sent
        # none, so these genuinely arrive here and must not be forwarded.
        for foreign in ("en-US-AriaNeural", "en_US-lessac-medium", "luganda-vits-v1"):
            with self.subTest(voice=foreign):
                self.assertEqual(sunbird.resolve_tts_voice("lg", foreign), "salt_lug_0001")

    def test_unknown_locale_has_no_voice(self):
        self.assertIsNone(sunbird.resolve_tts_voice("xx", "waxal_lug_0002"))
        self.assertIsNone(sunbird.resolve_tts_voice("xx", None))

    def test_every_default_is_first_in_its_own_catalogue(self):
        # Membership alone is not enough: /v1/speech/voices marks the HEAD of
        # each locale's list as the default, so a default sitting anywhere else
        # would be advertised as an alternate while still being what you hear.
        for locale, default in sunbird.TTS_VOICES.items():
            with self.subTest(locale=locale):
                catalogue = sunbird.TTS_VOICE_CATALOG[locale]
                self.assertIn(default, catalogue)
                self.assertEqual(catalogue[0], default)

    def test_catalogue_entries_are_unique_and_locale_scoped(self):
        seen: dict[str, str] = {}
        for locale, tags in sunbird.TTS_VOICE_CATALOG.items():
            self.assertEqual(len(set(tags)), len(tags), f"{locale} repeats a tag")
            for tag in tags:
                self.assertNotIn(
                    tag, seen, f"{tag} is claimed by both {seen.get(tag)} and {locale}"
                )
                seen[tag] = locale


class TextToSpeechForwardingTests(unittest.TestCase):
    """The requested speaker must reach the request body, not just be accepted."""

    def _capture_payload(self, locale: str, voice: str | None) -> dict:
        captured: dict = {}

        class _Resp:
            status_code = 200

            @staticmethod
            def json():
                return {"output": {"audio_url": "https://example.test/a.wav"}}

        def fake_post(path, json=None, **kwargs):
            captured.update(json or {})
            return _Resp()

        with patch.object(sunbird, "is_available", return_value=True), patch.object(
            sunbird, "_post", side_effect=fake_post
        ):
            sunbird.text_to_speech("nkwagaliza", locale=locale, voice=voice)
        return captured

    def test_requested_voice_reaches_the_payload(self):
        payload = self._capture_payload("lg", "waxal_lug_0004")
        self.assertEqual(payload["voice"], "waxal_lug_0004")
        self.assertEqual(payload["model"], "orpheus-3b-tts")
        self.assertEqual(payload["language"], sunbird.LOCALE_TO_SUNBIRD["lg"])

    def test_default_reaches_the_payload_when_none_requested(self):
        self.assertEqual(self._capture_payload("nyn", None)["voice"], "salt_nyn_0001")

    def test_foreign_tag_is_replaced_before_the_request(self):
        self.assertEqual(self._capture_payload("lg", "waxal_swa_0007")["voice"], "salt_lug_0001")


if __name__ == "__main__":
    unittest.main()

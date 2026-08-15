"""The English narration voice a caller picks has to reach edge-tts.

Companion to test_sunbird_voice_selection.py, which fixed the same class of bug
on the Sunbird side. The English half was assumed working and was not:
`_synthesize_edge_tts` took only (text, language) and looked its speaker up from
a two-entry `voice_map`, so the `voice` argument stopped at `synthesize()`.
/v1/speech/voices advertised four English speakers and the settings picker
offered all four; every one of them synthesized as en-US-AriaNeural.

It survived the earlier pass because both suites tested the layer above —
`resolve_tts_voice` and the Sunbird payload — and no test followed an English
voice all the way to the `edge_tts.Communicate(...)` call. `test_edge_call_uses_*`
below does exactly that, which is the only assertion here that would have
failed before the fix.

The drift guard matters as much as the resolution tests: the endpoint's offered
list and the resolver's accepted list are now the same function, so a
deployment overriding SPEECH_EN_EDGE_VOICE cannot end up advertising a voice
that synthesis then refuses.
"""

from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import speech_service  # noqa: E402
from app.speech_service import (  # noqa: E402
    SpeechModel,
    en_edge_voice_choices,
    resolve_edge_voice,
)


def _wav(num_samples: int = 240) -> bytes:
    """A minimal but real PCM16 WAV, so _audio_metadata parses it."""
    pcm = b"\x00\x00" * num_samples
    return (
        b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 24000, 48000, 2, 16)
        + b"data" + struct.pack("<I", len(pcm)) + pcm
    )


class ResolveEdgeVoiceTests(unittest.TestCase):
    def test_no_request_uses_the_configured_english_voice(self):
        self.assertEqual(resolve_edge_voice("en", None), speech_service.SPEECH_EN_EDGE_VOICE)
        self.assertEqual(resolve_edge_voice("en", ""), speech_service.SPEECH_EN_EDGE_VOICE)

    def test_an_offered_english_voice_is_honoured(self):
        self.assertEqual(
            resolve_edge_voice("en", "en-GB-SoniaNeural"), "en-GB-SoniaNeural"
        )
        self.assertEqual(resolve_edge_voice("en", "en-US-GuyNeural"), "en-US-GuyNeural")

    def test_every_offered_english_voice_is_accepted(self):
        """The endpoint's list and the resolver's list cannot drift apart."""
        for name in en_edge_voice_choices():
            with self.subTest(voice=name):
                self.assertEqual(resolve_edge_voice("en", name), name)

    def test_a_configured_voice_outside_the_tuple_is_offered_and_accepted(self):
        with patch.object(speech_service, "SPEECH_EN_EDGE_VOICE", "en-AU-NatashaNeural"):
            self.assertIn("en-AU-NatashaNeural", en_edge_voice_choices())
            self.assertEqual(
                resolve_edge_voice("en", "en-AU-NatashaNeural"), "en-AU-NatashaNeural"
            )

    def test_unknown_english_voice_falls_back_rather_than_failing(self):
        self.assertEqual(
            resolve_edge_voice("en", "not-a-real-voice"), speech_service.SPEECH_EN_EDGE_VOICE
        )

    def test_a_sunbird_tag_is_never_forwarded_to_edge(self):
        """edge has no such speaker; sending it raises 'No audio was received'."""
        self.assertEqual(
            resolve_edge_voice("en", "salt_eng_0001"), speech_service.SPEECH_EN_EDGE_VOICE
        )
        self.assertEqual(
            resolve_edge_voice("lg", "waxal_lug_0002"), speech_service.SPEECH_LG_EDGE_VOICE
        )

    def test_luganda_uses_the_east_african_english_standin(self):
        for voice in (None, "waxal_lug_0002", "en-GB-SoniaNeural"):
            with self.subTest(voice=voice):
                self.assertEqual(
                    resolve_edge_voice("lg", voice), speech_service.SPEECH_LG_EDGE_VOICE
                )

    def test_other_ugandan_locales_get_the_english_standin(self):
        for locale in ("nyn", "ach", "sw"):
            with self.subTest(locale=locale):
                self.assertEqual(
                    resolve_edge_voice(locale, "salt_nyn_0001"),
                    speech_service.SPEECH_EN_EDGE_VOICE,
                )


class EdgeSynthesisForwardingTests(unittest.TestCase):
    """Follow the voice all the way to edge_tts.Communicate."""

    def _capture_voice(self, language: str, voice: str | None) -> tuple[str, object]:
        seen: dict[str, str] = {}

        class _FakeCommunicate:
            def __init__(self, text: str, voice_name: str):
                seen["voice"] = voice_name

            async def stream(self):
                yield {"type": "audio", "data": _wav()}

        fake = type(sys)("edge_tts")
        fake.Communicate = _FakeCommunicate
        with patch.dict(sys.modules, {"edge_tts": fake}):
            # No instance attributes are touched, so an unbound call keeps this
            # test off the model-loading path.
            result = SpeechModel._synthesize_edge_tts(None, "Hello", language, voice)
        return seen["voice"], result

    def test_edge_call_uses_the_requested_english_voice(self):
        used, result = self._capture_voice("en", "en-GB-SoniaNeural")
        self.assertEqual(used, "en-GB-SoniaNeural")
        self.assertEqual(result.voice, "en-GB-SoniaNeural")

    def test_edge_call_reports_the_speaker_it_actually_used(self):
        used, result = self._capture_voice("en", "not-a-real-voice")
        self.assertEqual(used, speech_service.SPEECH_EN_EDGE_VOICE)
        self.assertEqual(result.voice, speech_service.SPEECH_EN_EDGE_VOICE)

    def test_edge_call_for_luganda_ignores_the_sunbird_tag(self):
        used, result = self._capture_voice("lg", "waxal_lug_0002")
        self.assertEqual(used, speech_service.SPEECH_LG_EDGE_VOICE)
        self.assertEqual(result.voice, speech_service.SPEECH_LG_EDGE_VOICE)

    def test_two_different_picks_reach_edge_as_two_different_speakers(self):
        """The regression in one line: these were both Aria before the fix."""
        first, _ = self._capture_voice("en", "en-US-GuyNeural")
        second, _ = self._capture_voice("en", "en-GB-SoniaNeural")
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()

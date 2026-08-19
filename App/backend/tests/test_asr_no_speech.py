"""Hearing nothing is not the same as every backend being broken.

`_transcribe_chain` advances on `result.text`, so a backend that ran fine and
returned an empty transcript — silence, a mis-tap, background noise — looked
exactly like one that crashed, and the chain ended on:

    All ASR backends failed (Whisper+LoRA, Sherpa, faster-whisper, Sunbird,
    Workers AI)

That string was not internal. /v1/voice/chat already has the right branch for
this case ("No speech detected. Please speak clearly and try again."), but it is
gated on `error` being unset, so the failure branch won every time and the
assistant replied to a moment of silence by naming five components. Found by
probing the live deployment with silence and misreading the result as an outage
— which is the same mistake the message invites.

These tests pin the distinction at the layer that creates it, since the caller
can only be as truthful as `error` lets it be.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.speech_service import SpeechModel, TranscribeResult  # noqa: E402


def _chain(model: SpeechModel, language: str = "en") -> TranscribeResult:
    return SpeechModel._transcribe_chain(model, b"\x00\x00" * 1600, 16000, language)


class _Bare:
    """A SpeechModel stand-in with every backend under the test's control.

    The chain calls its backends through `self`, so these have to live on the
    instance — patching SpeechModel.* does nothing here, and the AttributeError
    that follows is swallowed by the chain's own `except Exception`, which makes
    a mis-wired test look like a passing one. Constructing the real model would
    load Whisper, so a stand-in it is.
    """

    enabled = True
    _asr = None
    _lang_det = None
    _whisper_peft = None
    _whisper_salt = None
    _accent_detector = None
    _whisper_adapters: dict = {}
    _breakers: dict = {}
    _executor = None

    def __init__(self, peft=None, faster=None, cf_text=""):
        self._peft, self._faster, self._cf_text = peft, faster, cf_text

    def _transcribe_whisper_peft(self, *_a, **_kw):
        return self._peft

    def _transcribe_faster_whisper(self, *_a, **_kw):
        return self._faster

    def _cf_whisper_transcribe(self, *_a, **_kw) -> str:
        """Workers AI is credential-gated; absent here, like the local models."""
        return self._cf_text

    def _decode_audio_bytes(self, *_a, **_kw):
        import numpy as np

        return np.zeros(1600, dtype="float32")


class NoSpeechIsNotFailureTests(unittest.TestCase):
    def test_sunbird_hearing_nothing_is_reported_without_an_error(self):
        """The case that reached users: Sunbird answered, with no words in it."""
        model = _Bare()
        with (
            patch("app.sunbird.is_available", return_value=True),
            patch("app.sunbird.speech_to_text", return_value={"text": "", "language": "eng"}),
        ):
            result = _chain(model)

        self.assertEqual(result.text, "")
        self.assertIsNone(result.error, "an empty transcript must not read as a backend failure")
        self.assertEqual(result.backend, "sunbird_cloud", "say which backend actually answered")

    def test_nothing_reachable_still_reports_a_failure(self):
        """The opposite case has to keep failing loudly, or the fix hides outages."""
        model = _Bare()
        with patch("app.sunbird.is_available", return_value=False):
            result = _chain(model)

        self.assertEqual(result.text, "")
        self.assertEqual(result.backend, "unavailable")
        self.assertIn("All ASR backends failed", result.error or "")

    def test_a_local_backend_hearing_nothing_is_also_not_an_error(self):
        """Same rule for the offline tiers, so it does not depend on Sunbird."""
        model = _Bare(faster=TranscribeResult(text="", backend="faster_whisper"))
        with patch("app.sunbird.is_available", return_value=False):
            result = _chain(model)

        self.assertIsNone(result.error)
        self.assertEqual(result.backend, "faster_whisper")

    def test_a_real_transcript_still_wins_over_everything(self):
        """The happy path must not be disturbed by the new bookkeeping."""
        model = _Bare(faster=TranscribeResult(text="what is the VAT rate", backend="faster_whisper"))
        result = _chain(model)

        self.assertEqual(result.text, "what is the VAT rate")
        self.assertIsNone(result.error)


if __name__ == "__main__":
    unittest.main()

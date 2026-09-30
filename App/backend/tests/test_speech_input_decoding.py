"""What the chat's voice paths hear: uploaded PCM is decoded as PCM.

On the local stack a Luganda question came back from ``/v1/asr`` as
"Ekiriza e e e e e…" while the same clip, wrapped in a WAV header, read
correctly. Its first sample was -1, whose bytes (FF FF) passed a bare MPEG
frame-sync test, so libsndfile decoded the speech as an MP3.
"""

from __future__ import annotations

import io
import unittest
from unittest.mock import patch

import numpy as np
from app.speech_service import SpeechModel, _looks_like_mp3


def _tone(first_sample: int | None = None, peak: float = 0.25, offset: int = 0) -> np.ndarray:
    """One second of 16-bit, 16 kHz speech-band tone."""
    t = np.arange(16000) / 16000
    samples = (np.sin(2 * np.pi * 220 * t) * peak * 32767).astype(np.int32) + offset
    pcm = np.clip(samples, -32768, 32767).astype("<i2")
    if first_sample is not None:
        pcm[0] = first_sample
    return pcm


def _decode(data: bytes) -> np.ndarray:
    return SpeechModel.__new__(SpeechModel)._decode_audio_bytes(data)


class RawPcmIsNotMistakenForMp3(unittest.TestCase):
    def test_a_recording_that_starts_on_a_frame_sync_is_decoded_as_pcm(self):
        # -1, -3841 and -7937 are FF FF, FF F0 and FF E0: the MPEG frame sync.
        # Whether libsndfile then rejects the bytes or turns them into noise
        # depends on the audio, so the routing itself is what is pinned.
        routed = AssertionError("raw PCM was sent to the container (MP3) decoder")
        for first in (-1, -3841, -7937):
            with self.subTest(first_sample=first), patch.object(SpeechModel, "_decode_container", side_effect=routed):
                pcm = _tone(first_sample=first)
                self.assertFalse(_looks_like_mp3(pcm.tobytes()))
                np.testing.assert_allclose(_decode(pcm.tobytes()), pcm / 32768.0, atol=1e-6)

    def test_a_real_mp3_is_still_recognised(self):
        import soundfile as sf

        buf = io.BytesIO()
        try:
            sf.write(buf, _tone() / 32768.0, 16000, format="MP3")
        except (sf.LibsndfileError, ValueError, TypeError):
            self.skipTest("this libsndfile cannot write MP3")
        self.assertTrue(_looks_like_mp3(buf.getvalue()))
        self.assertGreater(len(_decode(buf.getvalue())), 12000)


class Int16IsNotMistakenForFloat32(unittest.TestCase):
    def test_quiet_speech_with_a_dc_offset_is_decoded_as_int16(self):
        # Every sample positive and quiet: read as float32 its largest value
        # is tiny, which passed the old range check.
        pcm = _tone(peak=0.03, offset=1200)
        self.assertEqual(len(pcm.tobytes()) % 4, 0)
        np.testing.assert_allclose(_decode(pcm.tobytes()), pcm / 32768.0, atol=1e-6)

    def test_real_float32_audio_is_still_float32(self):
        audio = (_tone() / 32768.0).astype(np.float32)
        np.testing.assert_allclose(_decode(audio.tobytes()), audio, atol=1e-7)


if __name__ == "__main__":
    unittest.main()

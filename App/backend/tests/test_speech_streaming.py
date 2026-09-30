"""Streamed speech for the chat: how a reply is cut, and what each piece is sent as."""

from __future__ import annotations

import io
import unittest

import numpy as np
import soundfile as sf
from app.speech_service import (
    STREAM_FIRST_PIECE_CHARS,
    STREAM_SECOND_PIECE_CHARS,
    SynthesizeResult,
    audio_for_client,
    voice_pieces,
)

REPLY = (
    "Happy to help you register for a TIN! To register for a TIN with URA in Uganda, visit the "
    "official URA web portal, open eServices and choose TIN Registration. Fill in the online "
    "application form, attach a copy of your National ID, and submit it. You will receive your "
    "TIN by email once the application is approved."
)


def _wav(rate: int = 24000, seconds: float = 1.0) -> bytes:
    t = np.arange(int(rate * seconds)) / rate
    buf = io.BytesIO()
    sf.write(buf, (np.sin(2 * np.pi * 220 * t) * 0.3).astype(np.float32), rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def _result(audio: bytes) -> SynthesizeResult:
    return SynthesizeResult(audio=audio, sample_rate=24000, num_samples=0, duration_s=1.0,
                            latency_s=0.1, backend="orpheus_salt", voice="v")


class VoicePieces(unittest.TestCase):
    def test_a_long_reply_starts_short_and_grows(self):
        pieces = voice_pieces(REPLY)
        self.assertGreater(len(pieces), 2)
        self.assertLessEqual(len(pieces[0]), STREAM_FIRST_PIECE_CHARS)
        self.assertLessEqual(len(pieces[1]), STREAM_SECOND_PIECE_CHARS)
        self.assertTrue(all(len(piece) <= 120 for piece in pieces))  # the Orpheus request cap
        self.assertGreater(max(len(piece) for piece in pieces[2:]), STREAM_SECOND_PIECE_CHARS)
        self.assertEqual(" ".join(pieces).split(), REPLY.split())  # nothing lost or reordered

    def test_a_short_reply_is_one_piece(self):
        self.assertEqual(voice_pieces("The VAT rate is 18 percent."), ["The VAT rate is 18 percent."])

    def test_nothing_to_say_is_no_pieces(self):
        self.assertEqual(voice_pieces("   "), [])


class AudioForClient(unittest.TestCase):
    def test_wav_becomes_opus_when_asked(self):
        wav = _wav()
        audio, fmt = audio_for_client(_result(wav), "opus")
        self.assertEqual((fmt, audio[:4]), ("ogg_opus", b"OggS"))
        self.assertLess(len(audio), len(wav) / 5)
        decoded, rate = sf.read(io.BytesIO(audio))
        self.assertAlmostEqual(len(decoded) / rate, 1.0, delta=0.1)

    def test_wav_stays_wav_otherwise(self):
        wav = _wav()
        self.assertEqual(audio_for_client(_result(wav), "wav"), (wav, "wav"))

    def test_a_rate_opus_cannot_take_is_sent_as_wav(self):
        wav = _wav(rate=22050)
        self.assertEqual(audio_for_client(_result(wav), "opus"), (wav, "wav"))

    def test_mp3_is_passed_through(self):
        # Two MPEG-1 Layer III frames (128 kbps, 44.1 kHz): edge-tts output is already compressed.
        mp3 = (b"\xff\xfb\x90\x00" + b"\x00" * 413) * 2
        self.assertEqual(audio_for_client(_result(mp3), "opus"), (mp3, "mp3"))

    def test_audio_that_will_not_encode_is_sent_as_it_came(self):
        broken = b"RIFF" + b"\x00" * 60
        self.assertEqual(audio_for_client(_result(broken), "opus"), (broken, "wav"))


if __name__ == "__main__":
    unittest.main()

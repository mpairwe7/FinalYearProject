"""Tests for TTS response metadata (`sample_rate`, `num_samples`, `duration_s`).

Every cloud TTS tier used to report these as hardcoded constants and zeros, so
`/v1/tts` advertised a sample rate the audio did not have and a duration of 0
for real speech. edge-tts was worse than absent: it derived duration with the
bytes-to-samples formula for raw PCM16, but edge-tts returns MP3, so a ~4s
phrase was reported as ~0.5s.

These fields are advisory — playback uses the container's own header — so the
risk is not a crash but silent, plausible-looking wrongness. Hence the emphasis
here on exact expected values rather than "is it non-zero".
"""

from __future__ import annotations

import io
import math
import struct
import sys
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.speech_service import (  # noqa: E402
    _audio_metadata,
    _input_duration_s,
    _mp3_metadata,
)


def _wav(sample_rate: int = 24_000, seconds: float = 1.0) -> bytes:
    """A real WAV container (440 Hz tone) at the requested rate/duration."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(
            b"".join(
                struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * t / sample_rate)))
                for t in range(int(sample_rate * seconds))
            )
        )
    return buf.getvalue()


def _mp3_frames(n_frames: int, bitrate_kbps: int = 48, sample_rate: int = 24_000) -> bytes:
    """A CBR MPEG2 Layer III bitstream of *n_frames* frames.

    Mirrors what edge-tts emits (24 kHz, 48 kbps, mono). Frame payloads are
    zeroed — only the headers matter for duration.
    """
    bitrate_index = {8: 1, 16: 2, 24: 3, 32: 4, 40: 5, 48: 6, 64: 8}[bitrate_kbps]
    rate_index = {22050: 0, 24000: 1, 16000: 2}[sample_rate]
    # 0xFF, then sync + MPEG2 (ver=2) + Layer III (layer=1) + no CRC
    h1 = 0xE0 | (2 << 3) | (1 << 1) | 1
    h2 = (bitrate_index << 4) | (rate_index << 2)
    frame_size = (72 * bitrate_kbps * 1000) // sample_rate
    return b"".join(
        bytes([0xFF, h1, h2, 0x00]) + b"\x00" * (frame_size - 4) for _ in range(n_frames)
    )


class TestWavMetadata(unittest.TestCase):
    def test_reads_rate_samples_and_duration_exactly(self):
        for rate, seconds in ((24_000, 1.0), (22_050, 0.5), (16_000, 2.0), (48_000, 0.25)):
            with self.subTest(rate=rate, seconds=seconds):
                got_rate, samples, duration = _audio_metadata(_wav(rate, seconds), 24_000)
                self.assertEqual(got_rate, rate)
                self.assertEqual(samples, int(rate * seconds))
                self.assertAlmostEqual(duration, seconds, places=2)

    def test_reported_rate_comes_from_the_audio_not_the_default(self):
        """The Sunbird path hardcoded 22050 while serving 24 kHz audio."""
        rate, _, _ = _audio_metadata(_wav(24_000, 0.5), default_rate=22_050)
        self.assertEqual(rate, 24_000)


class TestMp3Metadata(unittest.TestCase):
    def test_duration_matches_frame_count(self):
        """MPEG2 Layer III carries 576 samples per frame — the exact duration."""
        for n in (10, 173, 500):
            with self.subTest(frames=n):
                data = _mp3_frames(n)
                expected = n * 576 / 24_000
                rate, samples, duration = _audio_metadata(data, 24_000)
                self.assertEqual(rate, 24_000)
                self.assertAlmostEqual(duration, expected, places=2)
                self.assertEqual(samples, int(expected * 24_000))

    def test_beats_the_raw_pcm_formula_it_replaced(self):
        """len/(rate*2) treats MP3 bytes as PCM16 and understates ~8x here."""
        data = _mp3_frames(173)
        truth = 173 * 576 / 24_000
        _, _, duration = _audio_metadata(data, 24_000)
        old = len(data) / (24_000 * 2)
        self.assertLess(abs(duration - truth), 0.05)
        self.assertGreater(abs(old - truth) / truth, 0.5)

    def test_skips_id3v2_tag(self):
        """A leading ID3 tag must not be mistaken for frame data."""
        body = _mp3_frames(50)
        tag_size = 300
        # 10-byte header + syncsafe size, then padding.
        id3 = b"ID3\x04\x00\x00" + bytes([0, 0, (tag_size >> 7) & 0x7F, tag_size & 0x7F])
        data = id3 + b"\x00" * tag_size + body
        _, _, duration = _audio_metadata(data, 24_000)
        self.assertAlmostEqual(duration, 50 * 576 / 24_000, places=2)

    def test_returns_none_for_non_mpeg(self):
        self.assertIsNone(_mp3_metadata(b"\x00" * 500))


class TestDegradesSafely(unittest.TestCase):
    """Metadata is advisory; a bad payload must never break a TTS response."""

    def test_unreadable_input_falls_back_to_defaults(self):
        for name, data in (
            ("empty", b""),
            ("too short", b"AB"),
            ("html error page", b"<!doctype html><html><body>error</body></html>" * 4),
            ("truncated riff", b"RIFF" + b"\x00" * 40),
            ("random bytes", bytes(range(256))),
        ):
            with self.subTest(case=name):
                self.assertEqual(_audio_metadata(data, 24_000), (24_000, 0, 0.0))

    def test_never_raises(self):
        for data in (b"", b"\xff", b"\xff\xf3", b"RIFF", b"ID3", b"ID3\x04\x00\x00"):
            _audio_metadata(data, 24_000)  # must not raise


class TestInputDuration(unittest.TestCase):
    def test_prefers_the_container_header(self):
        self.assertAlmostEqual(_input_duration_s(_wav(24_000, 1.5), 16_000), 1.5, places=2)

    def test_falls_back_to_raw_pcm16(self):
        """/v1/asr documents its body as raw PCM16, which has no header."""
        pcm = b"\x00\x00" * 16_000  # 1s mono int16 @ 16 kHz
        self.assertAlmostEqual(_input_duration_s(pcm, 16_000), 1.0, places=2)

    def test_degenerate_input_is_zero_not_an_error(self):
        self.assertEqual(_input_duration_s(b"", 16_000), 0.0)
        self.assertEqual(_input_duration_s(b"\x00" * 100, 0), 0.0)


class TestTranscribeTimingFill(unittest.TestCase):
    """Cloud ASR backends returned null duration_s/rtf; the wrapper fills them."""

    def _model(self, chain_result):
        from app.speech_service import SpeechModel

        model = SpeechModel.__new__(SpeechModel)
        model.enabled = True
        model._transcribe_chain = lambda *a, **k: chain_result
        return model

    def test_fills_timing_a_cloud_backend_omitted(self):
        from app.speech_service import TranscribeResult

        model = self._model(TranscribeResult(text="hi", backend="sunbird_cloud"))
        out = model.transcribe(b"\x00\x00" * 16_000, sample_rate=16_000)
        self.assertAlmostEqual(out.duration_s, 1.0, places=2)
        self.assertIsNotNone(out.latency_s)
        self.assertIsNotNone(out.rtf)

    def test_does_not_overwrite_a_local_backend(self):
        """whisper_peft/faster_whisper measure their own; keep their numbers."""
        from app.speech_service import TranscribeResult

        model = self._model(
            TranscribeResult(
                text="hi", backend="whisper_peft", duration_s=9.0, latency_s=0.5, rtf=0.06
            )
        )
        out = model.transcribe(b"\x00\x00" * 16_000, sample_rate=16_000)
        self.assertEqual((out.duration_s, out.latency_s, out.rtf), (9.0, 0.5, 0.06))

    def test_silent_failure_still_reports_input_duration(self):
        """backend='unavailable' should still say how much audio was sent."""
        from app.speech_service import TranscribeResult

        model = self._model(TranscribeResult(text="", backend="unavailable"))
        out = model.transcribe(b"\x00\x00" * 32_000, sample_rate=16_000)
        self.assertAlmostEqual(out.duration_s, 2.0, places=2)


if __name__ == "__main__":
    unittest.main()

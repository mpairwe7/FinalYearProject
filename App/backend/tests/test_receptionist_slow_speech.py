""""Speak slower": the local voices have no speed control, so sentences are spaced out."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("pipecat")

from app.receptionist.tts import UraSpeechTTS  # noqa: E402


class SlowSpeechPauseTests(unittest.IsolatedAsyncioTestCase):
    def _tts(self, slow: bool) -> UraSpeechTTS:
        room = SimpleNamespace(state=SimpleNamespace(locale="en", speech_rate_slow=slow))
        tts = UraSpeechTTS(speech_model=MagicMock(), language="en", room=room)

        async def one_frame(_text: str, _context_id: str):
            yield "voice"

        tts._voice = one_frame  # type: ignore[method-assign]
        return tts

    async def frames(self, tts: UraSpeechTTS) -> list[object]:
        return [frame async for frame in tts.run_tts("The VAT rate is 18 percent.", "ctx")]

    async def test_a_sentence_is_followed_by_a_pause_once_the_caller_asked(self):
        with patch.dict(os.environ, {"RECEPTIONIST_SLOW_PAUSE_MS": "600"}):
            frames = await self.frames(self._tts(slow=True))
        self.assertEqual(frames[0], "voice")
        silence = frames[1:]
        self.assertEqual(len(silence), 30)  # 600 ms of 20 ms frames
        self.assertTrue(all(set(f.audio) == {0} and len(f.audio) == 640 for f in silence))

    async def test_no_pause_at_normal_speed(self):
        self.assertEqual(await self.frames(self._tts(slow=False)), ["voice"])


if __name__ == "__main__":
    unittest.main()

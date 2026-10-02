"""Turn-taking for the cascaded engine (Whisper-SALT → RAG → TTS).

The cascaded engine used to close the caller's turn on the first transcript
after 0.6 s of silence. That cut off anyone who pauses mid-sentence — which a
Luganda caller searching for the English name of a tax does all the time —
and Smart Turn v3, Pipecat's learned end-of-turn model, covers neither
Luganda nor Swahili. So:

* **stop** — :class:`SpeechTimeoutUserTurnStopStrategy`: the turn closes once
  the caller has been silent for ``RECEPTIONIST_TURN_TIMEOUT_S`` (1.0 s) after
  VAD's own 0.5 s stop window *and* a transcript exists.
* **start** — VAD opens a turn while the assistant is quiet. While it is
  talking, a turn (and so an interruption) needs two transcribed words
  (:class:`MinWordsUserTurnStartStrategy`), fed mid-utterance by the live
  partial transcripts: a cough or an "mm" no longer stops the answer.
"""

from __future__ import annotations

from typing import Any

from .config import get_turn_timeout_s

#: VAD silence before it reports the caller stopped (the turn timeout runs after it).
VAD_STOP_SECS = 0.5
BARGE_IN_MIN_WORDS = 2


def build_cascaded_turn_strategies() -> Any:
    """``UserTurnStrategies`` for the cascaded engine. Needs Pipecat."""
    from pipecat.frames.frames import BotStartedSpeakingFrame, BotStoppedSpeakingFrame, Frame
    from pipecat.turns.types import ProcessFrameResult
    from pipecat.turns.user_start.min_words_user_turn_start_strategy import (
        MinWordsUserTurnStartStrategy,
    )
    from pipecat.turns.user_start.vad_user_turn_start_strategy import VADUserTurnStartStrategy
    from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
        SpeechTimeoutUserTurnStopStrategy,
    )
    from pipecat.turns.user_turn_strategies import UserTurnStrategies

    class VADWhileBotQuietStartStrategy(VADUserTurnStartStrategy):
        """VAD starts a turn only while the assistant is not speaking."""

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self._bot_speaking = False

        async def process_frame(self, frame: Frame) -> ProcessFrameResult:
            if isinstance(frame, BotStartedSpeakingFrame):
                self._bot_speaking = True
            elif isinstance(frame, BotStoppedSpeakingFrame):
                self._bot_speaking = False
            if self._bot_speaking:
                return ProcessFrameResult.CONTINUE
            return await super().process_frame(frame)

    return UserTurnStrategies(
        start=[
            VADWhileBotQuietStartStrategy(),
            MinWordsUserTurnStartStrategy(min_words=BARGE_IN_MIN_WORDS),
        ],
        stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=get_turn_timeout_s())],
    )

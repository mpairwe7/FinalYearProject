"""Turn-taking for the cascaded engine (Whisper-SALT → RAG → TTS).

The cascaded engine used to close the caller's turn on the first transcript
after 0.6 s of silence. That cut off anyone who pauses mid-sentence — which a
Luganda caller searching for the English name of a tax does all the time —
and Smart Turn v3, Pipecat's learned end-of-turn model, covers neither
Luganda nor Swahili. So:

* **stop** — :class:`AdaptiveSpeechTimeoutStopStrategy`: the turn closes
  dynamically:
  - 0.35 s for questions ending with terminal punctuation (?, !, .) or starting with
    interrogative markers across English, Luganda and Swahili (saves ~650 ms).
  - 1.2 s when trailing off on hesitation particles (and, or, era, oba, na, au...).
  - 1.0 s standard fallback.
* **start** — VAD opens a turn while the assistant is quiet. While it is
  talking, a turn (and so an interruption) needs two transcribed words
  (:class:`MinWordsUserTurnStartStrategy`), fed mid-utterance by the live
  partial transcripts: a cough or an "mm" no longer stops the answer.
"""

from __future__ import annotations

import re
from typing import Any

from .config import get_fast_turn_timeout_s, get_hesitation_turn_timeout_s, get_turn_timeout_s

#: VAD silence before it reports the caller stopped (the turn timeout runs after it).
VAD_STOP_SECS = 0.5
BARGE_IN_MIN_WORDS = 2

_HESITATION_WORDS: frozenset[str] = frozenset({
    # English
    "and", "or", "because", "but", "then", "like", "so", "if", "with", "for", "to",
    # Luganda
    "era", "oba", "kubanga", "naye", "nga", "ne", "ku", "mu", "ate", "lwaki",
    # Swahili
    "na", "au", "lakini", "kwa", "sababu", "ili", "halafu", "kama", "bila",
})

_INTERROGATIVE_PREFIXES: frozenset[str] = frozenset({
    # English
    "what", "how", "when", "where", "why", "who", "which", "can", "could", "would", "is", "are", "do", "does",
    # Luganda
    "nnyinza", "oyinza", "asobola", "kisoboka", "ani", "ki", "wa", "ddi", "lwaki", "otya", "bitya", "bimeka", "meka",
    # Swahili
    "ninawezaje", "unawezaje", "inawezekana", "nani", "nini", "wapi", "lini", "vipi", "je",
})


def build_cascaded_turn_strategies() -> Any:
    """``UserTurnStrategies`` for the cascaded engine. Needs Pipecat."""
    from pipecat.frames.frames import (
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        Frame,
        TranscriptionFrame,
    )
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

    class AdaptiveSpeechTimeoutStopStrategy(SpeechTimeoutUserTurnStopStrategy):
        """Adapts post-VAD silence wait based on question completeness and trailing syntax."""

        def _compute_adaptive_timeout(self) -> float:
            text = (self._text or "").strip()
            if not text:
                return get_turn_timeout_s()
            words = [w.lower().strip(".,?!;:-_\"'") for w in text.split() if w.strip()]
            if not words:
                return get_turn_timeout_s()

            # 1. Trailing hesitation particle: caller paused mid-clause, grant pause buffer
            last_word = words[-1]
            if last_word in _HESITATION_WORDS:
                return get_hesitation_turn_timeout_s()

            # 2. Terminal punctuation or complete question prefix: quick close
            has_terminal_punct = bool(re.search(r"[.?!]\s*$", text))
            has_interrogative = words[0] in _INTERROGATIVE_PREFIXES and len(words) >= 3
            if has_terminal_punct or has_interrogative:
                return get_fast_turn_timeout_s()

            return get_turn_timeout_s()

        async def _restart_user_speech_timer(self) -> None:
            if self._user_speech_timeout_task:
                await self.task_manager.cancel_task(self._user_speech_timeout_task)
                self._user_speech_timeout_task = None
            self._user_speech_wait_done = False
            timeout = self._compute_adaptive_timeout()
            self._user_speech_timeout_task = self.task_manager.create_task(
                self._user_speech_timeout_handler(timeout),
                f"{self}::_user_speech_timeout_handler",
            )

        async def _handle_transcription(self, frame: TranscriptionFrame) -> None:
            await super()._handle_transcription(frame)
            # If user stopped speaking and we are waiting on the timer, re-adjust if question completed
            if self._vad_stopped and not self._vad_user_speaking and not self._user_speech_wait_done:
                new_timeout = self._compute_adaptive_timeout()
                if new_timeout < get_turn_timeout_s():
                    await self._restart_user_speech_timer()

    return UserTurnStrategies(
        start=[
            VADWhileBotQuietStartStrategy(),
            MinWordsUserTurnStartStrategy(min_words=BARGE_IN_MIN_WORDS),
        ],
        stop=[AdaptiveSpeechTimeoutStopStrategy(user_speech_timeout=get_turn_timeout_s())],
    )

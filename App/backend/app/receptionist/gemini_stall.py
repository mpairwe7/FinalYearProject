"""Move a Gemini Live turn onto the local cascade when Gemini stays silent.

English and Kiswahili calls are answered by Gemini Live. On this deployment
the words come from Sunflower, the audio from Whisper-SALT and Spark-TTS-SALT,
and Qdrant holds the passages — but only on the cascaded branch. A Gemini
turn that never produces audio used to sit there (measured answer captions
past 30 seconds). This guard waits ``RECEPTIONIST_GEMINI_AUDIO_DEADLINE_S``
(15 seconds: above the measured 11–14 second first-audio turns, under the
hangs) and then asks the local branch to answer the same transcript.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

OnStall = Callable[[str], Awaitable[Any] | Any]


class GeminiStallGuard:
    """One deadline per caller turn. Hearing assistant audio cancels it."""

    def __init__(self, deadline_s: float, on_stall: OnStall) -> None:
        self.deadline_s = deadline_s
        self._on_stall = on_stall
        self._token = 0
        self._task: asyncio.Task[None] | None = None

    def arm(self, text: str) -> None:
        """Start (or restart) the deadline for this caller transcript."""
        self.cancel()
        cleaned = (text or "").strip()
        if not cleaned or self.deadline_s <= 0:
            return
        self._token += 1
        token = self._token
        self._task = asyncio.create_task(self._wait(cleaned, token))

    def heard(self) -> None:
        """Gemini produced text or audio for this turn."""
        self.cancel()

    def cancel(self) -> None:
        """The caller started speaking again, or the turn was abandoned."""
        self._token += 1
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()

    async def _wait(self, text: str, token: int) -> None:
        try:
            await asyncio.sleep(self.deadline_s)
        except asyncio.CancelledError:
            return
        if token != self._token:
            return
        try:
            result = self._on_stall(text)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.exception("Gemini stall fallback failed")

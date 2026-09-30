"""Carries out the language policy's decisions on a live call.

The sentinel produces votes; :class:`app.receptionist.language.LanguagePolicy`
turns them into decisions; this applies them:

* **state** — ``room.state.locale`` (which every language-aware stage reads per
  utterance), the ``voice_calls`` row, the per-call language metrics;
* **the question** — when a vote moves the call to another language, the turn
  that triggered it is transcribed in the new language and answered there.
  The caller never repeats a question because of a switch;
* **barge-in** — the sentinel hears the caller talking over the assistant
  sooner than the engine's own two-transcribed-words rule, and stops it;
* **events** — ``{"type": "language", ...}`` to the caller's screen, and
  ``language`` / ``call.language`` on the staff hub.

Every language runs on the one local engine (Whisper-SALT → Sunflower against
Qdrant → Orpheus / Spark-TTS-SALT), so a switch changes the language that
engine listens and speaks in, never the engine.

No Pipecat import at module level: the router is unit-tested without it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from ..guardrails import redact_pii_text
from ..speech_service import pcm16_to_wav
from .hub import hub
from .language import LANGUAGE_NAMES, Decision, LanguagePolicy, LanguageVote
from .phrases import phrase
from .store import create_turn, update_call

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _PendingSwitch:
    """A switch decided mid-turn, carried out at the end of the turn."""

    decision: Decision
    decoded: tuple[str, str] | None


_SOURCE_NOTES = {"auto": "detected", "explicit": "caller asked", "override": "chosen on screen"}


def announce_language(room: Any, language: str, source: str, confidence: float | None = None) -> dict[str, Any]:
    """Record a language lock or change and publish it to staff; returns the caller's message.

    Also writes a ``system`` turn of kind ``language`` — in English, for the
    officers — so the switch shows in the live transcript and in the call's
    history in the order it happened.
    """
    state = room.state
    if language not in state.languages_used:
        state.languages_used.append(language)
    state.language_source = source
    note = _SOURCE_NOTES.get(source, source)
    if confidence is not None and source == "auto":
        note = f"{note}, {round(float(confidence) * 100)}%"
    try:
        update_call(room.call_id, locale=language)
        state.turn_seq += 1
        turn = create_turn(
            call_id=room.call_id,
            seq=state.turn_seq,
            speaker="system",
            kind="language",
            text=f"Language: {LANGUAGE_NAMES.get(language, language)} ({note})",
        )
        hub.publish_call(room.call_id, "turn", turn)
    except Exception:
        logger.debug("Failed persisting call language", exc_info=True)
    message: dict[str, Any] = {"type": "language", "language": language, "source": source}
    if confidence is not None:
        message["confidence"] = round(float(confidence), 2)
    hub.publish_call(room.call_id, "language", message)
    hub.publish_lobby("call.language", {"call_id": room.call_id, "language": language, "source": source})
    return message


class LanguageRouter:
    """See the module docstring. One per multilingual call."""

    def __init__(
        self,
        room: Any,
        policy: LanguagePolicy,
        speech_model: Any,
        *,
        outlet: Any = None,
        brain: Any = None,
    ) -> None:
        self.room = room
        self.policy = policy
        self.speech_model = speech_model
        self.outlet = outlet
        self.brain = brain
        # Set by the pipeline builder once the sentinel exists (it needs us first).
        self.interrupt: Any = None
        self.barge_in: Any = None
        self._lock = asyncio.Lock()
        self._reasks: set[asyncio.Task[None]] = set()
        # Set when a switch interruption has passed the brain. Speaking before
        # that would be flushed by the interruption arriving behind it.
        self._switch_settled = asyncio.Event()
        self._pending: _PendingSwitch | None = None
        # True from a switch decided in a turn until the caller's next turn:
        # the router re-asks that turn in the new language, so the brain must
        # not also answer the transcript it got in the old one.
        self._turn_claimed = False
        # When the caller's current turn began (time.monotonic()), for the
        # re-ask: the old-language transcript can reach the brain before the
        # vote, and whatever it opened then is this turn's, not an earlier one's.
        self._turn_started_at: float | None = None

    def claims_turn(self) -> bool:
        """Wired to the brain's ``turn_claimed``: whether this turn is the router's to answer."""
        return self._turn_claimed

    def switch_passed(self) -> None:
        """Wired to the brain's ``on_switch_interrupt``."""
        self._switch_settled.set()

    async def _await_switch(self, timeout_s: float = 0.5) -> None:
        try:
            await asyncio.wait_for(self._switch_settled.wait(), timeout=timeout_s)
        except TimeoutError:
            logger.debug("Switch interruption not seen within %.1fs", timeout_s)

    async def _interrupt_for_switch(self) -> None:
        self._switch_settled.clear()
        if self.interrupt is not None:
            await self.interrupt()

    # -- inputs ------------------------------------------------------------

    async def on_speech_started(self) -> None:
        """An utterance began: a new turn, unless a switch is waiting on this one."""
        if self._turn_started_at is None:
            self._turn_started_at = time.monotonic()
        if self._pending is None:
            self._turn_claimed = False

    async def on_barge_in(self) -> None:
        """The caller has been talking over the assistant: stop it now.

        The engine also stops itself once two words are transcribed, but that
        waits on Whisper; the sentinel's VAD hears the caller first. Not over a
        switch waiting for the turn to end — that has its own interruption
        coming. The brain counts the barge-in when the interruption reaches it.
        """
        if self.barge_in is None or self._pending is not None or self.room.state.mode != "ai":
            return
        logger.info("Caller talking over the assistant on call %s: stopping it", self.room.call_id)
        await self.barge_in()

    async def on_vote(self, vote: LanguageVote, pcm: bytes | None, decoded: tuple[str, str] | None) -> None:
        state = self.room.state
        state.lid_latencies_ms.append(vote.latency_ms)
        if vote.top:
            state.lid_confidences.append(vote.confidence)
        decision = self.policy.observe(vote)
        logger.info(
            "Language vote %s p=%.2f %.1fs text=%r -> %s %s (%s)",
            # What the caller said, with identifiers (TIN, NIN, phone…) masked.
            vote.top or "-", vote.confidence, vote.speech_s, redact_pii_text(vote.text[:60]),
            decision.action, decision.target, decision.reason,
        )
        await self.apply(decision, pcm=pcm, decoded=decoded)

    async def on_override(self, language: str) -> None:
        """The caller picked a language on screen."""
        self.room.state.language_overrides += 1
        await self.apply(self.policy.set_override(language))

    # -- decisions ---------------------------------------------------------

    async def apply(
        self,
        decision: Decision,
        pcm: bytes | None = None,
        decoded: tuple[str, str] | None = None,
    ) -> None:
        async with self._lock:
            if decision.action == "none":
                return

            if decision.action == "lock":
                await self._send(announce_language(self.room, decision.target, decision.source, decision.confidence))
                return

            if decision.source in ("auto", "explicit") and pcm is not None:
                if decision.target == self.room.state.locale:
                    # A later vote in the same turn went back to the language
                    # in use: nothing is waiting any more.
                    self._pending = None
                    self._turn_claimed = False
                    return
                # Decided on one segment; carried out when the caller's turn
                # ends (on_turn_end), so the question answered is the whole one
                # and nothing is said over the caller finishing it.
                self._pending = _PendingSwitch(decision, decoded)
                self._turn_claimed = True
                return

            # An on-screen choice: nothing to wait for.
            self._pending = None
            await self._execute(decision, pcm, decoded)

    async def on_turn_end(self, pcm: bytes, segments: int) -> None:
        """The caller finished a turn. Carry out a switch decided during it."""
        async with self._lock:
            started, self._turn_started_at = self._turn_started_at, None
            pending, self._pending = self._pending, None
            if pending is None:
                return
            # The sentinel's text is of one segment; a longer turn is re-read.
            decoded = pending.decoded if segments <= 1 else None
            await self._execute(pending.decision, pcm, decoded, turn_started_at=started)

    async def _execute(
        self,
        decision: Decision,
        pcm: bytes | None,
        decoded: tuple[str, str] | None,
        *,
        turn_started_at: float | None = None,
    ) -> None:
        state = self.room.state
        state.locale = decision.target
        state.language_switches += 1
        message = announce_language(self.room, decision.target, decision.source, decision.confidence)
        # The turn in flight was transcribed in the old language: drop its
        # answer and stop whatever is playing, here and in the browser.
        state.generation_id += 1
        # The re-asked turn ran from its first word to now: a clarification the
        # brain opened in between came from its old-language transcript. One
        # opened later belongs to a turn the caller began meanwhile.
        window = (turn_started_at, time.monotonic()) if turn_started_at is not None else None
        await self._interrupt_for_switch()
        await self._send(message)
        logger.info("Call %s moved to %s (%s)", self.room.call_id, decision.target, decision.reason)
        self._spawn(self._answer(decision, pcm, decoded, window))

    async def _answer(
        self,
        decision: Decision,
        pcm: bytes | None,
        decoded: tuple[str, str] | None,
        reask_window: tuple[float, float] | None = None,
    ) -> None:
        if self.brain is None:
            return
        target = decision.target
        await self._await_switch()
        if decision.source != "auto" or pcm is None:
            # An explicit request or an on-screen choice is not a question:
            # confirm the switch, in the new language.
            await self.brain._say_and_record(phrase("switched", target), kind="notice")
            return

        # Something to hear straight away: the vote already took ~1 s.
        await self.brain.say_filler()
        text, words = await self._transcribe(pcm, target, decoded)
        if not text:
            logger.info("Nothing to re-ask after the switch to %s", target)
            return
        await self._send({
            "type": "caption", "speaker": "caller", "text": text, "final": True,
            "turn_id": self.room.state.turn_seq + 1,
        })
        await self.brain.handle_external_question(text, words, reask_window=reask_window)

    async def _transcribe(
        self, pcm: bytes, language: str, decoded: tuple[str, str] | None
    ) -> tuple[str, list[Any]]:
        # The sentinel usually decoded this utterance in the new language
        # already; a second pass would cost the caller another second just to
        # add per-word scores (clarification then works from the text alone).
        if decoded is not None and decoded[1] == language:
            return decoded[0], []
        try:
            res = await asyncio.to_thread(
                self.speech_model.transcribe, pcm16_to_wav(pcm, 16000), 16000, language, True
            )
        except Exception:
            logger.exception("Re-transcription after a language switch failed")
            return (decoded[0] if decoded else ""), []
        return (getattr(res, "text", "") or "").strip(), list(getattr(res, "words", None) or [])

    # -- plumbing ----------------------------------------------------------

    async def _send(self, message: dict[str, Any]) -> None:
        if self.outlet is not None:
            await self.outlet.send(message)

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._reasks.add(task)
        task.add_done_callback(self._reasks.discard)

    async def close(self) -> None:
        for task in list(self._reasks):
            task.cancel()

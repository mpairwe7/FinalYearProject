"""Carries out the language policy's decisions on a live call.

The sentinel produces votes; :class:`app.receptionist.language.LanguagePolicy`
turns them into decisions; this applies them:

* **state** — ``room.state.locale`` (which every language-aware stage reads per
  utterance), the ``voice_calls`` row, the per-call language metrics;
* **engine** — flips the :class:`EngineSelector` the branch gates consult
  (Gemini Live for English/Swahili, the cascaded engine for Luganda);
* **the held reply** — releases Gemini's buffered first answer when the call
  stays on Gemini, discards it when it does not;
* **the question** — when a vote moves the call to another engine, the
  utterance that triggered it is transcribed in the new language and answered
  there. The caller never repeats a question because of a switch;
* **events** — ``{"type": "language", ...}`` to the caller's screen, and
  ``language`` / ``call.language`` on the staff hub.

No Pipecat import at module level: ``announce_language`` is also used by the
single-engine Gemini pipeline.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from ..speech_service import pcm16_to_wav
from .hub import hub
from .language import LANGUAGE_NAMES, Decision, LanguagePolicy, LanguageVote
from .phrases import phrase
from .store import create_turn, list_turns, update_call

logger = logging.getLogger(__name__)

#: Earlier turns replayed to Gemini when a call moves onto it mid-conversation.
RECAP_TURNS = 3


@dataclass(frozen=True)
class _PendingSwitch:
    """A switch decided mid-turn, carried out at the end of the turn."""

    decision: Decision
    decoded: tuple[str, str] | None


class EngineSelector:
    """Which engine is answering the call right now. The branch gates read it."""

    def __init__(self, active: str) -> None:
        self.active = active


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
        selector: EngineSelector,
        engine_by_language: dict[str, str],
        speech_model: Any,
        *,
        outlet: Any = None,
        hold_gate: Any = None,
        brain: Any = None,
        gemini_service: Any = None,
    ) -> None:
        self.room = room
        self.policy = policy
        self.selector = selector
        self.engine_by_language = engine_by_language
        self.speech_model = speech_model
        self.outlet = outlet
        self.hold_gate = hold_gate
        self.brain = brain
        self.gemini_service = gemini_service
        # Set by the pipeline builder once the sentinel exists (it needs us first).
        self.interrupt: Any = None
        self._lock = asyncio.Lock()
        self._reasks: set[asyncio.Task[None]] = set()
        # One per engine: set when a switch interruption has passed that
        # engine's branch (its brain, or the tap after Gemini). Speaking there
        # before that would be flushed by the interruption arriving behind.
        self._settled: dict[str, asyncio.Event] = {"cascaded": asyncio.Event(), "gemini_live": asyncio.Event()}
        self._pending: _PendingSwitch | None = None

    def switch_passed(self, engine: str) -> None:
        """Wired to the branch's ``on_switch_interrupt``."""
        event = self._settled.get(engine)
        if event is not None:
            event.set()

    async def _await_switch(self, engine: str, timeout_s: float = 0.5) -> None:
        event = self._settled.get(engine)
        if event is None:
            return
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout_s)
        except TimeoutError:
            logger.debug("Switch interruption not seen in %s within %.1fs", engine, timeout_s)

    async def _interrupt_for_switch(self) -> None:
        for event in self._settled.values():
            event.clear()
        if self.interrupt is not None:
            await self.interrupt()

    def engine_may_act(self, engine: str) -> bool:
        """Whether *engine*'s tool calls may take effect: it is in use and not being left.

        A switch decided mid-turn waits for the turn to end, and meanwhile the
        engine being left still hears the caller. Gemini, hearing Luganda, once
        opened a transfer "because the caller speaks Luganda" while its spoken
        reply sat held and was then thrown away — the ticket was not.
        """
        if self.selector.active != engine:
            return False
        pending = self._pending
        return pending is None or self.engine_by_language.get(pending.decision.target, engine) == engine

    @property
    def detection_pending(self) -> bool:
        return self.hold_gate is not None and self.hold_gate.holding

    # -- inputs ------------------------------------------------------------

    async def on_speech_started(self) -> None:
        """An utterance began. Until the language is locked, hold Gemini's reply."""
        if self.policy.locked or self.policy.override is not None:
            return
        if self.selector.active == "gemini_live" and self.hold_gate is not None:
            self.hold_gate.hold()

    async def on_vote(self, vote: LanguageVote, pcm: bytes | None, decoded: tuple[str, str] | None) -> None:
        state = self.room.state
        state.lid_latencies_ms.append(vote.latency_ms)
        if vote.top:
            state.lid_confidences.append(vote.confidence)
        decision = self.policy.observe(vote)
        logger.info(
            "Language vote %s p=%.2f %.1fs text=%r -> %s %s (%s)",
            vote.top or "-", vote.confidence, vote.speech_s, vote.text[:60],
            decision.action, decision.target, decision.reason,
        )
        await self.apply(decision, pcm=pcm, decoded=decoded)

    async def on_override(self, language: str) -> None:
        """The caller picked a language on screen."""
        self.room.state.language_overrides += 1
        await self.apply(self.policy.set_override(language))

    def on_held(self, held_ms: float) -> None:
        self.room.state.held_ms.append(held_ms)

    # -- decisions ---------------------------------------------------------

    async def apply(
        self,
        decision: Decision,
        pcm: bytes | None = None,
        decoded: tuple[str, str] | None = None,
    ) -> None:
        async with self._lock:
            if decision.action == "none":
                # The reply being held (if any) is in the right language —
                # unless a switch is waiting for the end of this turn.
                if self._pending is None:
                    await self._release_hold()
                return

            if decision.action == "lock":
                if self._pending is None:
                    await self._release_hold()
                await self._send(announce_language(self.room, decision.target, decision.source, decision.confidence))
                return

            new_engine = self.engine_by_language.get(decision.target, self.selector.active)
            reasks = new_engine != self.selector.active or new_engine == "cascaded"
            if decision.source in ("auto", "explicit") and pcm is not None and reasks:
                # Decided on one segment; carried out when the caller's turn
                # ends (on_turn_end), so the question answered is the whole one
                # and nothing is said over the caller finishing it. Meanwhile
                # Gemini's reply — if it is the engine being left — is held.
                self._pending = _PendingSwitch(decision, decoded)
                if self.hold_gate is not None and new_engine != self.selector.active and self.selector.active == "gemini_live":
                    self.hold_gate.pin()
                return

            # A later vote in the same turn went back to the engine already in
            # use (or the caller chose on screen): nothing is waiting any more.
            self._pending = None
            await self._execute(decision, pcm, decoded)

    async def on_turn_end(self, pcm: bytes, segments: int) -> None:
        """The caller finished a turn. Carry out a switch decided during it."""
        async with self._lock:
            pending, self._pending = self._pending, None
            if pending is None:
                return
            # The sentinel's text is of one segment; a longer turn is re-read.
            decoded = pending.decoded if segments <= 1 else None
            await self._execute(pending.decision, pcm, decoded)

    async def _execute(
        self,
        decision: Decision,
        pcm: bytes | None,
        decoded: tuple[str, str] | None,
    ) -> None:
        state = self.room.state
        target = decision.target
        old_engine = self.selector.active
        new_engine = self.engine_by_language.get(target, old_engine)
        state.locale = target
        state.language_switches += 1
        message = announce_language(self.room, target, decision.source, decision.confidence)

        if new_engine == old_engine:
            await self._release_hold()
            await self._send(message)
            await self._continue_same_engine(decision, pcm, decoded)
            return

        if self.hold_gate is not None:
            await self.hold_gate.discard()
        # Interrupt first, while the old engine's gates are still the open
        # ones: whatever it is playing stops, here and in the browser.
        state.generation_id += 1
        await self._interrupt_for_switch()
        self.selector.active = new_engine
        state.engine = new_engine
        await self._send(message)
        logger.info("Call %s moved %s -> %s (%s, %s)", self.room.call_id, old_engine, new_engine, target, decision.reason)
        self._spawn(self._answer_on(new_engine, decision, pcm, decoded))

    async def _continue_same_engine(
        self, decision: Decision, pcm: bytes | None, decoded: tuple[str, str] | None
    ) -> None:
        engine = self.selector.active
        if engine == "gemini_live":
            # Gemini hears the caller and follows their language itself; only a
            # choice made on screen is news to it.
            if decision.source == "override":
                await self._tell_gemini_language(decision.target)
            return
        # Cascaded → cascaded (Luganda ↔ Swahili when both run cascaded): the
        # turn in flight was transcribed in the old language, so answer it again.
        self.room.state.generation_id += 1
        await self._interrupt_for_switch()
        self._spawn(self._answer_on(engine, decision, pcm, decoded))

    async def _answer_on(
        self, engine: str, decision: Decision, pcm: bytes | None, decoded: tuple[str, str] | None
    ) -> None:
        target = decision.target
        await self._await_switch(engine)
        if decision.source != "auto" or pcm is None:
            # An explicit request or an on-screen choice is not a question:
            # confirm the switch, in the new language.
            if engine == "cascaded" and self.brain is not None:
                await self.brain._say_and_record(phrase("switched", target), kind="notice")
            elif engine == "gemini_live":
                await self._tell_gemini_language(target)
            return

        if engine == "cascaded" and self.brain is not None:
            # Something to hear straight away: the vote already took ~1 s.
            await self.brain.say_filler()
        text, words = await self._transcribe(pcm, target, decoded, with_words=engine == "cascaded")
        if not text:
            logger.info("Nothing to re-ask after the switch to %s", target)
            return
        if engine == "cascaded" and self.brain is not None:
            await self._send({
                "type": "caption", "speaker": "caller", "text": text, "final": True,
                "turn_id": self.room.state.turn_seq + 1,
            })
            await self.brain.handle_external_question(text, words)
        elif engine == "gemini_live":
            await self._ask_gemini(text, target)

    async def _transcribe(
        self, pcm: bytes, language: str, decoded: tuple[str, str] | None, with_words: bool
    ) -> tuple[str, list[Any]]:
        # The sentinel usually decoded this utterance in the new language
        # already; a second pass would cost the caller another second just to
        # add per-word scores (clarification then works from the text alone).
        if decoded is not None and decoded[1] == language:
            return decoded[0], []
        try:
            res = await asyncio.to_thread(
                self.speech_model.transcribe, pcm16_to_wav(pcm, 16000), 16000, language, with_words
            )
        except Exception:
            logger.exception("Re-transcription after a language switch failed")
            return (decoded[0] if decoded else ""), []
        return (getattr(res, "text", "") or "").strip(), list(getattr(res, "words", None) or [])

    # -- Gemini ------------------------------------------------------------

    async def _ask_gemini(self, text: str, language: str) -> None:
        name = LANGUAGE_NAMES.get(language, language)
        recap = self._recap()
        note = f"Earlier in this call: {recap} " if recap else ""
        await self._gemini_text(
            f"[{note}The caller's latest question, spoken in {name}, is below. Answer it in {name}.]\n{text}"
        )

    async def _tell_gemini_language(self, language: str) -> None:
        name = LANGUAGE_NAMES.get(language, language)
        await self._gemini_text(
            f"[System note, not the caller: continue this call in {name} only. "
            f"Tell the caller briefly, in {name}, that you will continue in {name}.]"
        )

    async def _gemini_text(self, text: str) -> None:
        if self.gemini_service is None:
            return
        from pipecat.frames.frames import InputTextRawFrame

        await self.gemini_service.queue_frame(InputTextRawFrame(text=text))

    def _recap(self) -> str:
        try:
            turns = [t for t in list_turns(self.room.call_id) if t.get("speaker") in ("caller", "assistant")]
        except Exception:
            return ""
        lines = [f"{t['speaker']}: {str(t.get('text', ''))[:200]}" for t in turns[-RECAP_TURNS:]]
        return " | ".join(lines)

    # -- plumbing ----------------------------------------------------------

    async def _release_hold(self) -> None:
        if self.hold_gate is not None and (self.hold_gate.holding or self.hold_gate.buffered):
            await self.hold_gate.release()

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

"""Pipecat custom LLM service managing receptionist conversation turns, clarification, and handoffs."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from functools import lru_cache
from typing import Any

from .. import database as db
from ..flags import flags
from ..speech_normalization import clean_text_for_speech
from .clarify import ClarifyGate, ClarifyState
from .config import (
    KNOWN_LANGUAGES,
    get_clarify_threshold,
    get_clarify_threshold_for,
    get_default_language,
    get_filler_after_ms,
    get_max_clarify_attempts,
    get_max_spoken_sentences,
    get_transfer_timeout_s,
)
from .hub import hub
from .phrases import fillers, phrase, pick_filler
from .store import create_turn, update_call
from .transfer import close_transfer_on_timeout, open_transfer

logger = logging.getLogger(__name__)

from .serializer import (
    Frame,
    InterruptionFrame,
    OutputTransportMessageFrame,
    RequestOfficerFrame,
)

try:
    from pipecat.services.llm_service import LLMService
    from pipecat.processors.frame_processor import FrameDirection
    from pipecat.frames.frames import (
        LLMContextFrame,
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        LLMTextFrame,
    )
except ImportError:
    class FrameDirection:  # type: ignore[no-redef]
        DOWNSTREAM = 1
        UPSTREAM = 2

    class LLMService:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            pass

        async def push_frame(self, frame: Any, direction: Any = None):
            pass

    class LLMContextFrame(Frame):  # type: ignore[no-redef]
        def __init__(self, context: Any = None):
            self.context = context

    class LLMFullResponseStartFrame(Frame):  # type: ignore[no-redef]
        pass

    class LLMTextFrame(Frame):  # type: ignore[no-redef]
        def __init__(self, text: str):
            self.text = text

    class LLMFullResponseEndFrame(Frame):  # type: ignore[no-redef]
        pass


_HUMAN_REQUEST_RE = re.compile(
    r"\b(speak\s+to|talk\s+to|connect\s+to|transfer\s+to|contact|call)\s+(?:a|an|the)?\s*"
    r"(?:human|person|officer|agent|someone|operator|real person)\b|"
    r"\b(?:i\s+want|can\s+i|need\s+to)\s+(?:talk\s+to|speak\s+with|see)\s+(?:an?\s+)?officer\b|"
    r"\btalk\s+to\s+an\s+officer\b",
    re.IGNORECASE,
)

# Every call opens in the default language, whatever the caller selected in
# the chat: taxpayers routinely pick English and then speak Luganda, so the
# first real question decides (see language.py). Both engines speak this line.
GREETING_TEXT = phrase("greeting", get_default_language())
FILLER_POOL: tuple[str, ...] = fillers("en")
FILLER_TEXT = FILLER_POOL[-1]


@lru_cache(maxsize=1)
def _local_human_request_patterns() -> tuple[re.Pattern[str], ...]:
    """The supervisor's own "I want a person" patterns for lg and sw.

    Only the human-request rules — the tables also route legal disputes to
    escalation, which on a call is ChatModel.generate's decision, not a
    keyword's.
    """
    from ..agents.patterns.lg import LG_PATTERNS
    from ..agents.patterns.sw import SW_PATTERNS

    # Read from the locale modules directly: the supervisor registers only
    # the Ugandan-language extensions (for_locale("sw") is English-only), and
    # widening that registry would change chat routing, not just calls.
    tables = {"lg": LG_PATTERNS, "sw": SW_PATTERNS}
    found: list[re.Pattern[str]] = []
    for lang in KNOWN_LANGUAGES:
        table = tables.get(lang)
        if table is None:
            continue
        for pattern, reason in table.escalate:
            if "asked for a human" in reason or "officer request" in reason:
                found.append(pattern)
    return tuple(found)


def is_human_request(text: str) -> bool:
    """True when *text* asks for a person, in any language the call can be in.

    Checked in all of them rather than the call's current one: the request
    that matters most is the one made right after a mis-detected switch.
    """
    return bool(_HUMAN_REQUEST_RE.search(text)) or any(
        p.search(text) for p in _local_human_request_patterns()
    )


def _split_into_sentences(text: str) -> list[str]:
    """Split text into sentences cleanly."""
    pieces = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in pieces if p.strip()]


class UraReceptionistBrain(LLMService):
    """Custom LLM service consuming LLMContextFrame to drive the AI receptionist."""

    def __init__(
        self,
        room: Any,
        chat_model: Any,
        clarify_gate: ClarifyGate | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(enable_direct_mode=True, **kwargs)
        self.room = room
        self.chat_model = chat_model
        self.clarify_gate = clarify_gate or ClarifyGate(
            threshold=get_clarify_threshold(),
            max_attempts=get_max_clarify_attempts(),
        )
        self._last_filler: str | None = None
        # Set by say_filler(): the next answer already has its filler.
        self._prefilled = False
        # Called once a language-switch interruption has passed this brain
        # (the router waits for it before speaking here — see router.py).
        self.on_switch_interrupt: Any = None

    @property
    def language(self) -> str:
        """The call's language right now — it can change mid-call."""
        return self.room.state.locale or "en"

    def _pick_filler(self) -> str:
        """A filler in the call's language, never the one just used."""
        chosen = pick_filler(self.language, self._last_filler)
        self._last_filler = chosen
        return chosen

    async def say_greeting(self) -> None:
        """Push initial greeting to caller and publish turn."""
        await self._say_and_record(GREETING_TEXT, kind="notice")

    async def say_filler(self) -> None:
        """A filler now, ahead of an answer that is about to be worked out.

        The language router calls this the moment a call moves to this engine:
        by then deciding the language has already cost the caller about a
        second, so waiting the usual RECEPTIONIST_FILLER_AFTER_MS on top would
        only lengthen the silence. The answer that follows skips its own filler.
        """
        self._prefilled = True
        await self._say_and_record(self._pick_filler(), kind="filler", skip_log=True)

    async def process_frame(
        self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        """Process incoming Pipecat frames."""
        if isinstance(frame, InterruptionFrame):
            switching = bool((getattr(frame, "metadata", None) or {}).get("language_switch"))
            if switching:
                # The router bumped generation_id itself before interrupting
                # (dropping our in-flight answer if the call is leaving us).
                # Bumping again here would also drop the answer it is about to
                # ask for if the call is arriving — this frame can land after.
                await self.push_frame(frame, direction)
                if self.on_switch_interrupt is not None:
                    self.on_switch_interrupt()
                return
            # Caller barged in: invalidate pending LLM generation. On a
            # multilingual call interruptions reach this branch even while the
            # other engine is talking; only count the ones that interrupted us.
            self.room.state.generation_id += 1
            if self.room.state.engine in ("", "cascaded"):
                self.room.state.barge_in_count += 1
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, RequestOfficerFrame):
            await self._transfer("caller_requested")
            return

        if isinstance(frame, LLMContextFrame):
            await self._handle_context_frame(frame, direction)
            return

        await self.push_frame(frame, direction)

    async def _handle_context_frame(
        self, frame: LLMContextFrame, direction: FrameDirection
    ) -> None:
        logger.info("UraReceptionistBrain _handle_context_frame invoked with context: %r", getattr(frame, "context", None))
        # Extract user utterance from frame context
        user_text = ""
        context = getattr(frame, "context", None)
        if hasattr(context, "get_messages"):
            msgs = context.get_messages()
            if msgs and isinstance(msgs, list):
                last_msg = msgs[-1]
                user_text = last_msg.get("content", "") if isinstance(last_msg, dict) else getattr(last_msg, "content", "")
        elif isinstance(context, list) and context:
            last_msg = context[-1]
            user_text = last_msg.get("content", "") if isinstance(last_msg, dict) else getattr(last_msg, "content", "")
        elif isinstance(context, str):
            user_text = context

        user_text = user_text.strip()
        if not user_text:
            return

        # Pop turn words stashed by TranscriptTap
        words = list(self.room.state.turn_words)
        self.room.state.turn_words = []
        if self.room.state.engine not in ("", "cascaded"):
            # A multilingual call moved to Gemini while this turn was still
            # being transcribed; the router has already re-asked it there.
            logger.info("Dropping cascaded turn closed after the call left this engine")
            return
        await self.handle_external_question(user_text, words)

    async def handle_external_question(self, user_text: str, words: list[Any] | None = None) -> None:
        """Record the caller's turn and respond to it.

        The context aggregator reaches this through ``_handle_context_frame``;
        the language router calls it directly when a call moves to this engine
        mid-turn, so the question that triggered the switch is answered
        without the caller repeating it.
        """
        user_text = user_text.strip()
        if not user_text:
            return
        words = list(words or [])
        language = self.language

        # Record caller turn in persistence and pub/sub
        self.room.state.turn_seq += 1
        self.room.state.caller_turns_count += 1

        low_conf_list: list[dict[str, Any]] = []
        th = get_clarify_threshold_for(language)
        word_probs: list[float] = []
        for w in words:
            prob = getattr(w, "prob", None) or (w.get("prob") if isinstance(w, dict) else 1.0)
            word_str = getattr(w, "word", None) or (w.get("word") if isinstance(w, dict) else "")
            word_probs.append(prob)
            if prob < th:
                low_conf_list.append({"word": word_str, "prob": round(prob, 3)})

        if word_probs:
            self.room.state.word_probs.extend(word_probs)
        if low_conf_list:
            self.room.state.low_conf_words_count += len(low_conf_list)

        mean_prob = round(sum(word_probs) / len(word_probs), 3) if word_probs else None

        caller_turn = create_turn(
            call_id=self.room.call_id,
            seq=self.room.state.turn_seq,
            speaker="caller",
            kind="utterance",
            text=user_text,
            low_conf_words=low_conf_list,
            mean_word_prob=mean_prob,
        )
        hub.publish_call(self.room.call_id, "turn", caller_turn)

        # Dispatch based on current mode
        if self.room.state.mode in ("bridged", "transferring", "ended"):
            # Transcript recorded; no bot reply generated
            return

        # Mode is "ai"
        # 1. Explicit Human Request
        if is_human_request(user_text):
            await self._transfer("caller_requested")
            return

        # 2. Pending Clarification
        if self.room.state.clarify is not None:
            action = self.clarify_gate.resolve(
                user_text,
                words=words,
                state=self.room.state.clarify,
                max_attempts=get_max_clarify_attempts(),
                language=language,
            )
            if action.action == "ask_confirm_question":
                self.room.state.clarifications_asked += 1
                await self._say_and_record(action.prompt or "", kind="confirm")
                return

            if action.action == "restart":
                self.room.state.clarification_failures += 1
                self.room.state.clarify = None
                await self._say_and_record(action.prompt or "", kind="clarify")
                return

            if action.action == "transfer":
                self.room.state.clarification_failures += 1
                self.room.state.clarify = None
                await self._transfer("clarification_failed")
                return

            if action.action == "answer":
                # Caller confirmed paraphrase; proceed to answer corrected question
                self.room.state.clarified_first_try += 1
                q_to_answer = action.corrected_text or self.room.state.clarify.original_question
                self.room.state.clarify = None
                await self._answer_question(q_to_answer, mean_word_prob=mean_prob)
                return

        # 3. New Question — Assess via ClarifyGate
        action = self.clarify_gate.assess(user_text, words=words, threshold=th, language=language)
        if action.action in ("ask_term", "ask_repeat"):
            self.room.state.clarifications_asked += 1
            self.room.state.clarify = ClarifyState(
                stage="term" if action.action == "ask_term" else "repeat",
                original_question=user_text,
                target_word=action.target_word,
                suggested_term=action.candidate,
                previous_word=action.previous_word,
                attempts=0,
            )
            await self._say_and_record(action.prompt or "", kind="clarify")
            return

        # 4. Standard Answer Generation
        await self._answer_question(user_text, mean_word_prob=mean_prob)

    async def _answer_question(self, question: str, mean_word_prob: float | None = None) -> None:
        """Run ChatModel.generate with filler delay, trim for speech, and play reply."""
        curr_gen_id = self.room.state.generation_id
        t0 = time.perf_counter()
        prefilled, self._prefilled = self._prefilled, False

        # Start background task for LLM answer
        gen_task = asyncio.create_task(
            asyncio.to_thread(
                self.chat_model.generate,
                message=question,
                conversation_id=self.room.state.conversation_id,
                session_id=self.room.call_id,
                top_k=4,
                locale=self.room.state.locale,
                user_id=self.room.state.user_id,
                tenant_id=self.room.state.tenant_id,
            )
        )

        # Wait up to FILLER_AFTER_MS before sending filler audio
        filler_ms = get_filler_after_ms()
        done, _ = await asyncio.wait([gen_task], timeout=filler_ms / 1000.0)

        if not done and not prefilled:
            # Answer is taking more than threshold; play filler if not barged in
            if curr_gen_id == self.room.state.generation_id and self.room.state.mode == "ai":
                filler = self._pick_filler()
                await self._say_and_record(filler, kind="filler", skip_log=True)

        # Wait up to 25s for completion
        try:
            result = await asyncio.wait_for(gen_task, timeout=25.0)
        except asyncio.TimeoutError:
            logger.warning("ChatModel.generate timed out for call %s", self.room.call_id)
            if self.room.state.mode == "ai":
                await self._say_and_record(phrase("timeout_transfer", self.language), kind="answer")
                await self._transfer("timeout")
            return
        except Exception:
            logger.exception("ChatModel.generate failed for call %s", self.room.call_id)
            if self.room.state.mode == "ai":
                await self._say_and_record(phrase("error_transfer", self.language), kind="answer")
                await self._transfer("system_error")
            return

        # Check if caller barged in while generate was running
        if curr_gen_id != self.room.state.generation_id:
            logger.info("Dropping late generation for call %s after barge-in", self.room.call_id)
            return

        bot_reply = result.get("reply", "") or result.get("text", "")
        faithfulness = result.get("faithfulness_score")
        if faithfulness is not None:
            self.room.state.faithfulness_scores.append(float(faithfulness))

        gen_latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        self.room.state.latencies.append({"brain_ms": gen_latency_ms})

        # Save Q/A in database conversation history for continuity & tickets
        try:
            db.log_conversation(
                session_id=self.room.call_id,
                conversation_id=self.room.state.conversation_id,
                user_message=question,
                bot_reply=bot_reply,
                sources=json.dumps(result.get("sources", [])),
                response_time_ms=gen_latency_ms,
                confidence=float(mean_word_prob or 0.0),
                user_id=self.room.state.user_id,
                locale=self.room.state.locale,
            )
        except Exception:
            logger.debug("Failed logging conversation turn to DB", exc_info=True)

        # Check escalation flags from chat_model
        if result.get("escalation_required") or result.get("ticket_id"):
            await self._transfer(
                result.get("escalation_reason", "rule_triggered"),
                ticket_id=result.get("ticket_id"),
                handoff=result.get("handoff"),
            )
            return

        # Trim reply for spoken output
        sentences = _split_into_sentences(bot_reply)
        max_sentences = get_max_spoken_sentences()
        if len(sentences) > max_sentences:
            spoken_text = " ".join(sentences[:max_sentences]) + " " + phrase("more_detail", self.language)
        else:
            spoken_text = bot_reply

        spoken_text = clean_text_for_speech(spoken_text, locale=self.language)

        self.room.state.ai_answers_count += 1
        await self._say_and_record(
            spoken_text,
            kind="answer",
            faithfulness=faithfulness,
            latencies={"brain_ms": gen_latency_ms},
        )

    async def _say_and_record(
        self,
        text: str,
        kind: str = "answer",
        faithfulness: float | None = None,
        latencies: dict[str, Any] | None = None,
        skip_log: bool = False,
    ) -> None:
        """Output text via Pipecat TTS pipeline, save turn, and send caption."""
        self.room.state.turn_seq += 1
        turn_seq = self.room.state.turn_seq

        if not skip_log:
            turn = create_turn(
                call_id=self.room.call_id,
                seq=turn_seq,
                speaker="assistant",
                kind=kind,
                text=text,
                faithfulness=faithfulness,
                latencies=latencies,
            )
            hub.publish_call(self.room.call_id, "turn", turn)

        # Send caption to caller WebSocket
        caption_data = {
            "type": "caption",
            "speaker": "assistant",
            "text": text,
            "final": True,
            "turn_id": turn_seq,
        }
        await self.push_frame(
            OutputTransportMessageFrame(caption_data), FrameDirection.DOWNSTREAM
        )
        hub.publish_call(self.room.call_id, "caption", caption_data)

        # Stream text into Pipecat TTS service
        await self.push_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await self.push_frame(LLMTextFrame(text), FrameDirection.DOWNSTREAM)
        await self.push_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)

    async def _transfer(
        self,
        reason: str,
        ticket_id: str | None = None,
        handoff: dict[str, Any] | None = None,
    ) -> None:
        """Transfer caller to human officer, enforcing ticket_queue invariant."""
        # 1. Human oversight uses ticket_queue: if disabled, do NOT promise a handoff
        if not flags.is_enabled("ticket_queue"):
            await self._say_and_record(phrase("queue_disabled", self.language), kind="notice")
            update_call(self.room.call_id, transfer_reason=f"{reason}_queue_disabled")
            return

        # 2-3. Ticket, call state, voice_calls row, staff lobby event
        tid, status_event = open_transfer(
            self.room, self.chat_model, reason, ticket_id=ticket_id, handoff=handoff
        )

        # 4. Spoken notice & status event to caller
        await self._say_and_record(phrase("transfer", self.language), kind="handoff")
        await self.push_frame(
            OutputTransportMessageFrame(status_event), FrameDirection.DOWNSTREAM
        )

        # 6. Start transfer timeout timer
        timeout_s = get_transfer_timeout_s()
        self.room.state.transfer_timer_task = asyncio.create_task(
            self._transfer_timeout_countdown(timeout_s, tid or "")
        )

    async def _transfer_timeout_countdown(self, timeout_s: float, ticket_ref: str) -> None:
        """Handle officer wait timeout when no officer joins within the window."""
        await asyncio.sleep(timeout_s)
        status_event = close_transfer_on_timeout(self.room, ticket_ref)
        if status_event is None:
            return
        msg = phrase("officers_busy", self.language, ref=ticket_ref or "URA-CALL")
        await self._say_and_record(msg, kind="notice")
        await self.push_frame(OutputTransportMessageFrame(status_event), FrameDirection.DOWNSTREAM)

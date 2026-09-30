"""Pipecat custom LLM service managing receptionist conversation turns, clarification, and handoffs."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from functools import lru_cache
from typing import Any, Final

from .. import database as db
from ..flags import flags
from ..speech_normalization import clean_text_for_speech
from ..text_signals import detect_crisis
from .clarify import ClarifyGate, ClarifyState
from .config import (
    KNOWN_LANGUAGES,
    get_brief_model,
    get_clarify_threshold,
    get_clarify_threshold_for,
    get_default_language,
    get_filler_after_ms,
    get_hold_update_s,
    get_idle_reprompts,
    get_max_clarify_attempts,
    get_max_spoken_sentences,
    get_transfer_timeout_s,
)
from .hub import hub
from .lexicon import normalize_call_query
from .phrases import fillers, phrase, pick_filler
from .store import create_turn, get_call, update_call
from .transfer import case_reference, close_transfer_on_timeout, open_transfer

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
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
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

    class BotStartedSpeakingFrame(Frame):  # type: ignore[no-redef]
        pass

    class BotStoppedSpeakingFrame(Frame):  # type: ignore[no-redef]
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


#: A request for a person goes straight to an officer ("zero-out"), however it
#: is put: "connect me to an officer", "put me through to someone", "speak
#: with an agent". Article then name, never two optional runs of spaces side
#: by side, so a long pause in a transcript cannot make it slow.
_HUMAN_REQUEST_RE = re.compile(
    r"\b(?:(?:speak|talk)\s+(?:to|with)|(?:connect|transfer|put)\s+(?:me\s+)?(?:through\s+)?to|get\s+me|contact|call)"
    r"\s+(?:(?:a|an|the)\s+)?(?:human|person|officer|agent|representative|someone|somebody|operator|real\s+person)\b|"
    r"\b(?:i\s+want|can\s+i|need\s+to)\s+(?:talk\s+to|speak\s+(?:to|with)|see)\s+(?:an?\s+)?officer\b",
    re.IGNORECASE,
)

#: The one-word zero-out: a whole turn that only names a person ("Officer.",
#: "An agent, please", "Yes, a real person"), the way callers get past any
#: automated line. The greeting tells them they can. Inside a question the
#: word is just a word ("What does a tax officer do?"). Luganda omukozi /
#: omuntu, Swahili afisa / mtu / binadamu / mhudumu.
_HUMAN_WORD_RE: Final[re.Pattern[str]] = re.compile(
    r"^\W*(?:(?:yes|yeah|ok(?:ay)?|please)\W+)?"
    r"(?:(?:i\s+(?:want|need)|give\s+me|can\s+i\s+(?:have|get))\s+)?(?:(?:a|an|the)\s+)?(?:real\s+)?"
    r"(?:officer|agent|human|person|representative|operator|customer\s+(?:care|service)"
    r"|omukozi|omuntu|afisa|mtu|binadamu|mhudumu)"
    r"(?:\W+(?:please|now|nsaba|tafadhali))*\W*$",
    re.IGNORECASE,
)

_DISPUTE_RE = re.compile(
    r"\b(dispute|object(?:ion)?|appeal|tribunal|tat|court|lawyer|advocate|illegal|fraud|"
    r"okuwakanya|kuwakanya|nwakanya|okujulira|omusango|loya)\b",
    re.IGNORECASE,
)


def is_tax_dispute(text: str, reason: str = "") -> bool:
    """True when the question or escalation reason involves a tax dispute or legal objection."""
    return bool(_DISPUTE_RE.search(text) or _DISPUTE_RE.search(reason))


# Direct context-to-Luganda generation prompt templates (Pillars 1 & 2)
LUGANDA_RAG_SYSTEM = (
    "You are the official voice receptionist of the Uganda Revenue Authority (URA) speaking directly to a taxpayer in Luganda. "
    "Synthesize a direct, concise 1 to 2 sentence answer in natural Luganda based strictly on the provided English context. "
    "You MUST preserve all exact figures, percentages (e.g. 18%, 2%), statutory timeframes, rates, and legal numbers from the context. "
    "Speak directly to the caller without any preamble, metadata, or English filler."
)

LUGANDA_RAG_PROMPT_TEMPLATE = (
    "Context (English URA Statutes):\n{passages}\n\n"
    "Taxpayer Question (Luganda):\n{luganda_query}\n\n"
    "Provide a concise 1-2 sentence response directly in Luganda using exact figures from the context:"
)

QUERY_EXTRACT_SYSTEM = (
    "You convert Luganda taxpayer questions into concise English search queries "
    "for the Uganda Revenue Authority legal knowledge base. "
    "Focus on the core tax type, rates, deadlines, or procedure. "
    "Output ONLY the English search query and nothing else."
)

# Every call opens in the default language, whatever the caller selected in
# the chat: taxpayers routinely pick English and then speak Luganda, so the
# first real question decides (see language.py).
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
    return bool(_HUMAN_REQUEST_RE.search(text) or _HUMAN_WORD_RE.search(text)) or any(
        p.search(text) for p in _local_human_request_patterns()
    )


#: The caller's answer to the yes/no question the AI's last turn ended on
#: ("Would you like to speak to an officer?", "Would you like more detail?"):
#: the whole turn, never a word inside a new question ("Yes, and what is the
#: VAT rate?" is a question). English, Luganda, Swahili.
_OFFER_ACCEPT_RE: Final[re.Pattern[str]] = re.compile(
    r"^\W*(?:yes|yeah|yep|sure|ok(?:ay)?|alright|please|go\s+ahead|connect\s+me|please\s+do"
    r"|yee|kale|weewaawo|nsaba|ndiyo|ndio|sawa|naam|tafadhali)"
    r"(?:\W+(?:yes|please|sure|thanks|thank\s+you|go\s+ahead|connect\s+me|do|nsaba|webale|tafadhali|asante))*\W*$",
    re.IGNORECASE,
)
_OFFER_DECLINE_RE: Final[re.Pattern[str]] = re.compile(
    r"^\W*(?:no|nope|not\s+now|i'?m\s+(?:fine|okay|ok)|that'?s\s+(?:all|enough|it)|nedda|hapana)"
    r"(?:\W+(?:thanks|thank\s+you|not\s+now|i'?m\s+(?:fine|okay|ok)|that'?s\s+(?:all|enough|it)"
    r"|webale|asante))*\W*$",
    re.IGNORECASE,
)
#: "Tell me more" answers "Would you like more detail?" — and only that: said
#: to an officer offer it is not a yes. Luganda weeyongere, Swahili endelea / zaidi.
_MORE_ACCEPT_RE: Final[re.Pattern[str]] = re.compile(
    r"^\W*(?:(?:yes|yeah|sure|ok(?:ay)?|please)\W+)?"
    r"(?:tell\s+me\s+more|go\s+on|carry\s+on|continue|more(?:\s+detail)?|weeyongere|endelea|zaidi)"
    r"(?:\W+please)?\W*$",
    re.IGNORECASE,
)


def _offer_question(offer: str, reason: str) -> str:
    """The phrase key of the question a pending offer asked, to ask it again."""
    if offer == "more":
        return "more_detail"
    return "crisis_offer" if reason == "safety_concern" else "officer_offer"

#: In-call controls are whole requests, not words inside a question: "I said
#: goodbye to my employer — how do I file PAYE?" must be answered, not hung up.
_CONTROL_MAX_WORDS = 8

_REPEAT_PATTERNS: Final[re.Pattern[str]] = re.compile(
    r"\b(?:repeat\s+that|say\s+that\s+again|could\s+you\s+repeat|pardon\s+me|what\s+did\s+you\s+say"
    r"|kiddemu|ddamu|nsaba\s+oddemu|kiddemu\s+katono"
    r"|rudia\s+tena|rudia|sema\s+tena|rudia\s+uliyosema)\b",
    re.IGNORECASE,
)

_SPEED_SLOW_PATTERNS: Final[re.Pattern[str]] = re.compile(
    r"\b(?:speak\s+slower|more\s+slowly|talk\s+slower|slow\s+down"
    r"|yogera\s+mpola|kiddemu\s+mpola|oyogere\s+mpola"
    r"|ongea\s+polepole|ongea\s+taratibu|sema\s+polepole)\b",
    re.IGNORECASE,
)

_HANGUP_VOICE_PATTERNS: Final[re.Pattern[str]] = re.compile(
    r"\b(?:hang\s+up|end\s+(?:the\s+)?call|disconnect|goodbye"
    r"|komya\s+essimu|katikoma\s+wano|weeraba"
    r"|kata\s+simu|maliza\s+simu|kwaheri)\b",
    re.IGNORECASE,
)


def _is_control_request(pattern: re.Pattern[str], text: str) -> bool:
    """*text* is a short in-call request matching *pattern* (see _CONTROL_MAX_WORDS)."""
    return len(text.split()) <= _CONTROL_MAX_WORDS and pattern.search(text) is not None


_TRAILING_LIST_NUMBER_RE = re.compile(r"(?:^|\s)(\d{1,2})\.$")
_TRAILING_ABBREVIATION_RE = re.compile(
    r"(?:^|\s)(?:e\.g|i\.e|etc|No|Sec|Art|Cap|Mr|Mrs|Ms|Dr|St|vs)\.$", re.IGNORECASE
)


def _ends_mid_sentence(sentence: str) -> bool:
    """Whether the full stop ending *sentence* belongs to a list number or an abbreviation.

    "…in Uganda: 1." opens a list and "… 2." continues one, so the steps stay
    with the sentence that introduces them; "The rate is 18." still ends one.
    """
    if _TRAILING_ABBREVIATION_RE.search(sentence):
        return True
    match = _TRAILING_LIST_NUMBER_RE.search(sentence)
    if match is None:
        return False
    number = int(match.group(1))
    before = sentence[: match.start()].rstrip()
    if not before:
        return True  # a bare "2." never ends a sentence
    if number == 1:
        return before.endswith(":")
    return re.search(rf"(?:^|\s){number - 1}\.\s", sentence) is not None


def _split_into_sentences(text: str) -> list[str]:
    """Split text into sentences, keeping a numbered procedure in one piece.

    The spoken answer is cut to RECEPTIONIST_MAX_SPOKEN_SENTENCES. Splitting at
    every full stop counted "1." and "2." as sentences, so a TIN guide was read
    out as its first step, cut off, then "2." and "Would you like more detail?".
    """
    sentences: list[str] = []
    for piece in re.split(r"(?<=[.!?])\s+", text.strip()):
        piece = piece.strip()
        if not piece:
            continue
        if sentences and _ends_mid_sentence(sentences[-1]):
            sentences[-1] = f"{sentences[-1]} {piece}"
        else:
            sentences.append(piece)
    return sentences


class UraReceptionistBrain(LLMService):
    """Custom LLM service consuming LLMContextFrame to drive the AI receptionist."""

    def __init__(
        self,
        room: Any,
        chat_model: Any,
        clarify_gate: ClarifyGate | None = None,
        speech_model: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(enable_direct_mode=True, **kwargs)
        self.room = room
        self.chat_model = chat_model
        # For lines spoken outside the TTS pipeline (a goodbye before hanging up).
        self.speech_model = speech_model
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
        # Set by the multilingual builder: True while the language router is
        # re-asking the current turn in the language the caller switched to.
        self.turn_claimed: Any = None
        # True while an answer is being worked out: the silence is ours then,
        # not the caller's (see on_caller_idle).
        self._answering = False
        # True while the assistant's audio is going out: an interruption then
        # is the caller talking over it (see CallState.offer_interrupted).
        self._bot_speaking = False

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
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False

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
            # Caller barged in: invalidate pending LLM generation.
            self.room.state.generation_id += 1
            self.room.state.barge_in_count += 1
            # Pipecat also interrupts as each caller turn starts; only one that
            # cuts the assistant off can have cut off the question it ended on.
            if self.room.state.pending_offer and self._bot_speaking:
                self.room.state.offer_interrupted = True
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
        # Never the context itself at INFO: it is the caller's conversation.
        logger.debug("Receptionist turn on call %s", self.room.call_id)
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
        if self.turn_claimed is not None and self.turn_claimed():
            # The router re-asks this turn in the new language; answering the
            # old-language transcript as well would answer the caller twice.
            logger.info("Dropping a turn the language router is re-asking on call %s", self.room.call_id)
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
        self.room.state.idle_prompts = 0

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
        # 0. A caller who speaks of ending their life hears where to get help
        # now, in full and before anything else: never a tax answer, never cut
        # short, never taken for a goodbye.
        if detect_crisis(user_text):
            self._take_offer()
            await self._support_crisis(asked_for_person=is_human_request(user_text))
            return

        # 0b. The answer to the yes/no question the AI's last turn ended on.
        # Anything else is a new question, and the offer lapses.
        unheard = self.room.state.offer_interrupted
        offer_at_risk = self.room.state.offer_at_risk
        offer, offer_reason, more = self._take_offer()
        replied = offer and (
            _OFFER_DECLINE_RE.search(user_text)
            or _OFFER_ACCEPT_RE.search(user_text)
            or (offer == "more" and _MORE_ACCEPT_RE.search(user_text))
        )
        if replied and unheard:
            # Said over the answer, before its question: ask it again rather
            # than transfer a caller who only said "okay" while listening.
            self._offer(offer, reason=offer_reason, more=more, at_risk=offer_at_risk)
            await self._say_and_record(phrase(_offer_question(offer, offer_reason), language), kind="notice")
            return
        if offer and _OFFER_DECLINE_RE.search(user_text):
            declined = "crisis_declined" if offer_reason == "safety_concern" else "offer_declined"
            await self._say_and_record(phrase(declined, language), kind="notice")
            return
        if offer == "officer" and _OFFER_ACCEPT_RE.search(user_text):
            await self._transfer(offer_reason or "offer_accepted")
            return
        if offer == "more" and (_OFFER_ACCEPT_RE.search(user_text) or _MORE_ACCEPT_RE.search(user_text)):
            await self._say_more(more)
            return
        if offer_at_risk and unheard:
            # Talked over before it was heard, then a new question: the offer
            # to an at-risk caller was never made, so it comes with this answer.
            self.room.state.risk_offer_made = False

        # 1. Explicit Human Request
        if is_human_request(user_text):
            await self._transfer("caller_requested")
            return

        # 1b. In-Call User Control: Repeat Request
        if _is_control_request(_REPEAT_PATTERNS, user_text):
            last_ans = getattr(self.room.state, "last_assistant_answer", "")
            if last_ans:
                prefix = {
                    "en": "Sure, let me repeat that: ",
                    "lg": "Kale, ka nkiddemu: ",
                    "sw": "Sawa, ngoja nirudie: ",
                }.get(language, "Sure, let me repeat that: ")
                await self._say_and_record(f"{prefix}{last_ans}", kind="answer")
                # A repeat is still the last answer (not "Sure, let me repeat
                # that: Sure, let me repeat that: …" the next time), and it ends
                # on the same question, which is open again.
                self.room.state.last_assistant_answer = last_ans
                self._offer(offer, reason=offer_reason, more=more, at_risk=offer_at_risk)
                return

        # 1c. In-Call User Control: Speech Rate Adjustment. The local voices
        # have no speed control; the call pauses between sentences instead
        # (UraSpeechTTS, RECEPTIONIST_SLOW_PAUSE_MS), which is what it promises.
        if _is_control_request(_SPEED_SLOW_PATTERNS, user_text):
            self.room.state.speech_rate_slow = True
            await self._say_and_record(phrase("slow_ack", language), kind="notice")
            return

        # 1d. In-Call User Control: Voice Hangup / Disconnect
        if _is_control_request(_HANGUP_VOICE_PATTERNS, user_text):
            logger.info("Ending call %s: the caller said goodbye", self.room.call_id)
            await self._end_call_after(phrase("officer_closing", language), "caller_voice_hangup")
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

    def _extract_english_tax_query(self, luganda_query: str) -> str:
        """Extract a clean English search query from a Luganda taxpayer question."""
        from .lexicon import normalize_luganda_tax_query, repair_asr_entities

        # The GPU stack's Sunflower reads this. Repair Whisper's TIN/URA
        # mishears first so Qdrant is searched for the tax the caller named.
        repaired = repair_asr_entities(luganda_query)
        normalized = normalize_luganda_tax_query(repaired)

        def _sunflower_query() -> str:
            from ..llm import _vllm_generate

            prompt = f"Luganda question: {normalized}\nEnglish search query:"
            messages = [
                {"role": "system", "content": QUERY_EXTRACT_SYSTEM},
                {"role": "user", "content": prompt},
            ]
            return _vllm_generate(messages, max_tokens=64, temperature=0.0).strip().strip('"')

        def _gemini_query() -> str:
            from ..providers.gateway import gemini_generate

            model = get_brief_model()
            prompt = f"Luganda question: {normalized}\nEnglish search query:"
            return gemini_generate(
                prompt,
                system=QUERY_EXTRACT_SYSTEM,
                model=model,
                max_tokens=64,
                temperature=0.0,
                locale="en",
            ).strip().strip('"')

        # Local Sunflower on vLLM first. Gemini is only the outage fallback.
        for gen in (_sunflower_query, _gemini_query):
            try:
                res = gen()
                if res and len(res) > 2:
                    return res
            except Exception:
                continue

        return normalized or luganda_query

    def _synthesize_luganda_reply(self, context_text: str, question: str) -> str:
        """Single-pass synthesis from English statutory context directly into Luganda."""
        prompt = LUGANDA_RAG_PROMPT_TEMPLATE.format(passages=context_text, luganda_query=question)

        def _sunflower_synth() -> str:
            from ..llm import _vllm_generate

            messages = [
                {"role": "system", "content": LUGANDA_RAG_SYSTEM},
                {"role": "user", "content": prompt},
            ]
            return _vllm_generate(messages, max_tokens=256, temperature=0.1).strip()

        def _gemini_synth() -> str:
            from ..providers.gateway import gemini_generate

            model = get_brief_model()
            return gemini_generate(
                prompt,
                system=LUGANDA_RAG_SYSTEM,
                model=model,
                max_tokens=256,
                temperature=0.1,
                locale="en",
            ).strip()

        # Same order as query extraction: the local Sunflower weights, then Gemini.
        for synth in (_sunflower_synth, _gemini_synth):
            try:
                res = synth()
                if res and len(res) > 3:
                    return res
            except Exception:
                continue
        return ""

    async def _generate_luganda_answer(self, question: str) -> dict[str, Any]:
        """Fast cross-lingual RAG bridge: local Sunflower first, Gemini only if it is down.

        Replaces the slow 4-hop MT pipeline with direct cross-lingual understanding,
        English URA knowledge base retrieval, and direct Luganda synthesis in a single pass.
        """
        # A Luganda calculation is answered by the same MCP calculator as chat,
        # before retrieval can substitute a nearby tax fact.
        from .lexicon import repair_asr_entities

        repaired = repair_asr_entities(question)
        thread_id = self.room.state.conversation_id or self.room.call_id
        calc = await asyncio.to_thread(
            self.chat_model._maybe_handle_fast_paths,
            message=repaired,
            rewritten=repaired,
            thread_id=thread_id,
            locale="lg",
        )
        if isinstance(calc, dict) and calc.get("reply"):
            answered = dict(calc)
            answered["locale"] = "lg"
            answered["english_query"] = repaired
            # A calculator reply has no retrieval passages. Leaving confidence
            # empty made the call treat that as "no knowledge" and transfer.
            if not answered.get("confidence"):
                answered["confidence"] = 1.0
            return answered

        # 1. Clean query extraction
        english_query = await asyncio.to_thread(self._extract_english_tax_query, question)

        # 2. Query official URA tax knowledge in English
        rag_result = await asyncio.to_thread(
            self.chat_model.generate,
            message=english_query,
            conversation_id=self.room.state.conversation_id,
            session_id=self.room.call_id,
            top_k=4,
            locale="en",
            user_id=self.room.state.user_id,
            tenant_id=self.room.state.tenant_id,
        )

        # Read for a crisis only now that it is in English: the call's crisis
        # lines answer it, never a Luganda paraphrase of the chat's.
        if rag_result.get("retrieval_mode") == "crisis_support":
            return {**rag_result, "locale": "lg"}

        english_reply = (rag_result.get("reply", "") or rag_result.get("text", "")).strip()
        sources = rag_result.get("sources", [])

        # Hard failure: zero retrieval hits & no reply
        if not english_reply and not sources:
            return {
                "reply": "",
                "sources": [],
                "escalation_required": True,
                "escalation_reason": "no_knowledge_match",
                "ticket_id": rag_result.get("ticket_id"),
                "handoff": rag_result.get("handoff"),
                "locale": "lg",
            }

        # Assemble grounding context
        passages: list[str] = []
        if english_reply:
            passages.append(english_reply)
        for s in sources:
            if isinstance(s, dict) and s.get("text"):
                passages.append(s["text"])
            elif isinstance(s, str) and s:
                passages.append(s)
        context_text = "\n\n".join(passages[:3])

        # 3. Direct Luganda Generation
        luganda_reply = await asyncio.to_thread(self._synthesize_luganda_reply, context_text, question)
        if not luganda_reply:
            if english_reply:

                loc_fn = getattr(self.chat_model, "_localize_reply", None)
                if callable(loc_fn):
                    try:
                        localized = loc_fn(english_reply, "lg")
                        if isinstance(localized, str) and localized.strip():
                            luganda_reply = localized.strip()
                        else:
                            luganda_reply = english_reply
                    except Exception:
                        luganda_reply = english_reply
                else:
                    luganda_reply = english_reply
            else:
                luganda_reply = phrase("error_transfer", "lg")

        return {
            "reply": str(luganda_reply),
            "sources": sources,
            "citations": rag_result.get("citations", []),
            "faithfulness_score": rag_result.get("faithfulness_score", 0.95),
            "confidence": rag_result.get("confidence") or rag_result.get("retrieval_confidence", 0.8),
            "escalation_required": rag_result.get("escalation_required", False),
            "escalation_reason": rag_result.get("escalation_reason", ""),
            "ticket_id": rag_result.get("ticket_id"),
            "handoff": rag_result.get("handoff"),
            "locale": "lg",
            "english_query": english_query,
        }

    async def on_caller_idle(self) -> None:
        """The caller has said nothing since the assistant stopped speaking.

        Checks they are still there, up to ``RECEPTIONIST_IDLE_REPROMPTS``
        times; then says goodbye and ends the call, so an abandoned call stops
        holding a call slot until ``RECEPTIONIST_MAX_CALL_S``. Never while an
        officer has, or is being fetched for, the call, and never while an
        answer is still being worked out.
        """
        state = self.room.state
        if state.mode != "ai" or self._answering:
            return
        state.idle_prompts += 1
        if state.idle_prompts <= get_idle_reprompts():
            # "Are you still there?" is the question now: a "yes" to it is
            # not a yes to an officer offer left unanswered.
            self._take_offer()
            await self._say_and_record(phrase("idle_check", self.language), kind="notice")
            return
        logger.info("Ending call %s: the caller has been silent", self.room.call_id)
        await self._end_call_after(
            phrase("idle_goodbye", self.language),
            "caller_idle",
            # Speaking during the goodbye (handle_external_question resets the count) keeps the call.
            still_wanted=lambda: state.idle_prompts > get_idle_reprompts(),
        )

    async def _end_call_after(self, goodbye: str, reason: str, still_wanted: Any = None) -> None:
        """Say *goodbye*, then end the call once the caller has heard it.

        Spoken like an officer's closing line: its length is known, so the call
        ends after the caller has heard it, not when the transport (which sends
        faster than real time) has finished sending it. Shielded: ending the
        call cancels the pipeline task this may run in. Checked again before
        hanging up: an officer may have taken the call meanwhile, or (for
        *still_wanted*) the silent caller may have spoken.
        """
        from .desk import hang_up_caller, say_to_caller

        async def _goodbye() -> None:
            duration = await say_to_caller(self.room, goodbye, self.speech_model)
            await asyncio.sleep(duration + 0.3)
            if self.room.state.mode != "ai" or (still_wanted is not None and not still_wanted()):
                logger.info("Not ending call %s after all (%s)", self.room.call_id, reason)
                return
            await hang_up_caller(self.room, reason)

        await asyncio.shield(asyncio.ensure_future(_goodbye()))

    async def _answer_question(self, question: str, mean_word_prob: float | None = None) -> None:
        """Answer *question*; until it has been handed to TTS, caller silence is not idleness."""
        self._answering = True
        try:
            await self._generate_and_speak(question, mean_word_prob)
        finally:
            self._answering = False

    async def _generate_and_speak(self, question: str, mean_word_prob: float | None = None) -> None:
        """Run answer generation with filler delay, calibrated escalations, and speech playout."""
        curr_gen_id = self.room.state.generation_id
        t0 = time.perf_counter()
        prefilled, self._prefilled = self._prefilled, False

        # Start background task for LLM answer
        if self.language == "lg":
            gen_task = asyncio.create_task(self._generate_luganda_answer(question))
        else:
            gen_task = asyncio.create_task(
                asyncio.to_thread(
                    self.chat_model.generate,
                    message=normalize_call_query(question, self.room.state.locale),
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
            logger.warning("Generation timed out for call %s", self.room.call_id)
            if self.room.state.mode == "ai":
                await self._say_and_record(phrase("timeout_transfer", self.language), kind="answer")
                await self._transfer("timeout")
            return
        except Exception:
            logger.exception("Generation failed for call %s", self.room.call_id)
            if self.room.state.mode == "ai":
                await self._say_and_record(phrase("error_transfer", self.language), kind="answer")
                await self._transfer("system_error")
            return

        # Check if caller barged in while generate was running
        if curr_gen_id != self.room.state.generation_id:
            logger.info("Dropping late generation for call %s after barge-in", self.room.call_id)
            return

        # The chat's own crisis check caught what the call's did not (a
        # Luganda turn is read for it only once it has been put into English).
        if result.get("retrieval_mode") == "crisis_support":
            await self._support_crisis(asked_for_person=False)
            return

        bot_reply = (result.get("reply", "") or result.get("text", "")).strip()
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

        # Voice-Calibrated Escalation Policy (Pillar 2)
        escalation_required = bool(result.get("escalation_required"))
        ticket_id = result.get("ticket_id")
        esc_reason = str(result.get("escalation_reason", "") or "rule_triggered")
        sources = result.get("sources", [])
        confidence = float(result.get("confidence") or mean_word_prob or 1.0)

        # 1. Hard escalation: Explicit human request
        if is_human_request(question) or esc_reason in ("user_requested", "caller_requested", "human_requested"):
            await self._transfer("caller_requested", ticket_id=ticket_id, handoff=result.get("handoff"))
            return

        # 2. Hard escalation: Hard failure / zero retrieval hits
        if not bot_reply or (not sources and confidence < 0.35):
            await self._transfer(
                esc_reason if esc_reason != "rule_triggered" else "no_knowledge_match",
                ticket_id=ticket_id,
                handoff=result.get("handoff"),
            )
            return

        # 3. Hard escalation: Verified tax dispute / legal objection requiring human handling
        if is_tax_dispute(question, esc_reason):
            await self._transfer(
                esc_reason if esc_reason != "rule_triggered" else "tax_dispute",
                ticket_id=ticket_id,
                handoff=result.get("handoff"),
            )
            return

        # 4. Moderate retrieval confidence (0.35–0.50), a soft abstain, or a
        # call going badly: the best statutory answer, and an officer offered
        # rather than imposed.
        uncertain = escalation_required or (0.35 <= confidence < 0.50)
        at_risk = not uncertain and self._call_at_risk()
        spoken_text = clean_text_for_speech(
            self._spoken_answer(bot_reply, offer_officer=uncertain or at_risk, at_risk=at_risk),
            locale=self.language,
        )

        self.room.state.ai_answers_count += 1
        await self._say_and_record(
            spoken_text,
            kind="answer",
            faithfulness=faithfulness,
            latencies={"brain_ms": gen_latency_ms},
        )

    def _call_at_risk(self) -> bool:
        """True once per call, when the risk monitor has marked it ``at_risk``.

        On an AI-first line the assistant offers a person when a call is going
        badly (distress, a repeated question, clarifications, no answers)
        rather than waiting for an officer to notice it on the desk: officers
        only take calls the AI hands over.
        """
        if self.room.state.risk_offer_made:
            return False
        try:
            risk = json.loads((get_call(self.room.call_id) or {}).get("risk_json") or "{}")
        except (TypeError, ValueError):
            return False
        if not isinstance(risk, dict) or risk.get("level") != "at_risk":
            return False
        self.room.state.risk_offer_made = True
        return True

    def _spoken_answer(self, reply: str, *, offer_officer: bool, at_risk: bool = False) -> str:
        """*reply* cut for speech, ending on at most one yes/no question.

        One question a turn, so that "yes" can only mean one thing: an officer
        offer takes the place of "Would you like more detail?". Whichever is
        asked becomes the pending offer the caller's next turn may answer.
        """
        sentences = _split_into_sentences(reply)
        max_sentences = get_max_spoken_sentences()
        rest = sentences[max_sentences:]
        spoken = " ".join(sentences[:max_sentences]) if rest else reply
        if offer_officer:
            self._offer("officer", reason="offer_accepted", at_risk=at_risk)
            return f"{spoken} {phrase('officer_offer', self.language)}"
        if rest:
            self._offer("more", more=" ".join(rest))
            return f"{spoken} {phrase('more_detail', self.language)}"
        return spoken

    async def _say_more(self, rest: str) -> None:
        """The next part of an answer the caller said yes to hearing more of."""
        spoken = self._spoken_answer(rest, offer_officer=False)
        await self._say_and_record(clean_text_for_speech(spoken, locale=self.language), kind="answer")

    def _offer(self, kind: str, *, reason: str = "", more: str = "", at_risk: bool = False) -> None:
        """Record the yes/no question this turn ends on (see ``CallState.pending_offer``)."""
        state = self.room.state
        state.pending_offer, state.offer_reason, state.more_detail = kind, reason, more
        state.offer_at_risk = at_risk
        state.offer_interrupted = False

    def _take_offer(self) -> tuple[str, str, str]:
        """The pending offer as ``(kind, transfer reason, rest of the answer)``, now cleared."""
        state = self.room.state
        taken = (state.pending_offer, state.offer_reason, state.more_detail)
        self._offer("")
        return taken

    async def _support_crisis(self, *, asked_for_person: bool) -> None:
        """Where to get help now, spoken in full, with the numbers on screen; then a person.

        The call's version of the chat's ``_crisis_support_result``: no tax
        content, and an officer offered rather than imposed, unless the
        caller asked for one, when the transfer goes at once. Either way it
        goes as ``safety_concern``, at the front of the queue.
        """
        language = self.language
        spoken = phrase("crisis_support", language)
        if not asked_for_person:
            spoken = f"{spoken} {phrase('crisis_offer', language)}"
        await self._say_and_record(spoken, kind="answer")
        await self._say_and_record(phrase("crisis_screen", language), kind="notice", speak=False)
        if asked_for_person:
            await self._transfer("safety_concern")
        else:
            self._offer("officer", reason="safety_concern")

    async def _say_and_record(
        self,
        text: str,
        kind: str = "answer",
        faithfulness: float | None = None,
        latencies: dict[str, Any] | None = None,
        skip_log: bool = False,
        speak: bool = True,
    ) -> None:
        """Output text via Pipecat TTS pipeline, save turn, and send caption.

        With *speak* false the line is only captioned and recorded: for what
        the caller needs to keep and the voice reads badly (``phrases.UNSPOKEN``).
        """
        self.room.state.turn_seq += 1
        turn_seq = self.room.state.turn_seq
        if kind == "answer":
            self.room.state.last_assistant_answer = text

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
        if not speak:
            return

        # One text frame: the TTS service's aggregator splits it into sentences
        # and voices the first while the rest wait, so nothing is gained by
        # pushing sentences separately (and without the space between them the
        # aggregator saw "…you.To register" as one sentence).
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
        # Whatever the AI last asked is settled by the transfer: after a
        # timeout, a "yes" must not answer a question from before it.
        self._take_offer()
        # 1. Human oversight uses ticket_queue: if disabled, do NOT promise a handoff
        if not flags.is_enabled("ticket_queue"):
            await self._say_and_record(phrase("queue_disabled", self.language), kind="notice")
            await self._say_and_record(phrase("tollfree_screen", self.language), kind="notice", speak=False)
            update_call(self.room.call_id, transfer_reason=f"{reason}_queue_disabled")
            return

        # 2-3. Ticket, call state, voice_calls row, staff lobby event. A caller
        # at risk goes to the front of the queue, whatever they called about.
        tid, status_event = open_transfer(
            self.room,
            self.chat_model,
            reason,
            ticket_id=ticket_id,
            handoff=handoff,
            priority="urgent" if reason == "safety_concern" else "",
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
        """Keep a waiting caller told; hand the call back when no officer joins in time.

        Every ``RECEPTIONIST_HOLD_UPDATE_S`` a "thank you for holding" line,
        so silence never reads as a dropped call, except over an officer who
        has taken the call and is joining. An officer who takes it at the
        last moment is waited for: their claim connects or lapses within
        ``RECEPTIONIST_CLAIM_TIMEOUT_S``, and "all our officers are busy" is
        never said to a caller an officer is about to greet. Then the
        callback: the reference goes on the caller's screen, short and in the
        form the chat's support cases use, never read out as a ticket UUID.
        """
        state = self.room.state
        every = get_hold_update_s()
        waited = 0.0
        while every and waited + every < timeout_s:
            await asyncio.sleep(every)
            waited += every
            if state.mode == "transferring" and not state.claimed_by and not state.reconnecting_officer:
                await self._say_and_record(phrase("still_holding", self.language), kind="notice")
        await asyncio.sleep(timeout_s - waited)
        while state.mode == "transferring" and state.claimed_by:
            await asyncio.sleep(0.5)
        status_event = close_transfer_on_timeout(self.room, ticket_ref)
        if status_event is None:
            return
        await self.push_frame(OutputTransportMessageFrame(status_event), FrameDirection.DOWNSTREAM)
        await self._say_and_record(phrase("officers_busy", self.language), kind="notice")
        if ticket_ref:
            reference = phrase("reference_screen", self.language, ref=case_reference(ticket_ref))
            await self._say_and_record(reference, kind="notice", speak=False)

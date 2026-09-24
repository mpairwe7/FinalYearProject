"""Gemini Live multimodal speech-to-speech receptionist adapter.

Enables full-duplex conversational voice via Google Gemini Live API in Pipecat,
preserving the URA RAG knowledge base and ticket transfer workflows as tools.

Gemini Live speaks English and Swahili (not Luganda — see
``receptionist/config.get_engine_by_language``). It follows the caller's
language on its own, steered by :func:`build_gemini_system_instruction`;
retrieval always runs in English (Gemini translates the question in and the
answer out), so a Swahili turn costs no extra machine-translation hop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable, Sequence
from typing import Any

from .. import database as db
from ..flags import flags
from .brain import GREETING_TEXT
from .config import (
    get_engine_by_language,
    get_gemini_live_model,
    get_gemini_live_voice,
    get_gemini_vad_end_sensitivity,
    get_gemini_vad_prefix_padding_ms,
    get_gemini_vad_silence_ms,
    get_gemini_vad_start_sensitivity,
    get_max_call_s,
    get_transfer_timeout_s,
)
from .hub import hub
from .language import LANGUAGE_NAMES, text_language
from .phrases import phrase
from .serializer import BrowserCallSerializer
from .store import create_turn, update_call
from .taps import CallerAudioTap
from .transfer import close_transfer_on_timeout, open_transfer

try:
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
    from pipecat.frames.frames import (
        BotStoppedSpeakingFrame,
        Frame,
        InterruptionFrame,
        LLMFullResponseEndFrame,
        OutputTransportMessageUrgentFrame,
        TranscriptionFrame,
        TTSTextFrame,
        UserStartedSpeakingFrame,
    )
except ImportError:
    class FrameDirection:  # type: ignore[no-redef]
        DOWNSTREAM = 1
        UPSTREAM = 2

    class FrameProcessor:  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None: pass
        async def push_frame(self, frame: Any, direction: Any = None) -> None: pass

    class Frame:  # type: ignore[no-redef]
        pass

    class OutputTransportMessageUrgentFrame(Frame):  # type: ignore[no-redef]
        def __init__(self, message: Any) -> None: self.message = message

    class TranscriptionFrame(Frame):  # type: ignore[no-redef]
        pass

    class TTSTextFrame(Frame):  # type: ignore[no-redef]
        pass

    class BotStoppedSpeakingFrame(Frame):  # type: ignore[no-redef]
        pass

    class UserStartedSpeakingFrame(Frame):  # type: ignore[no-redef]
        pass

    class InterruptionFrame(Frame):  # type: ignore[no-redef]
        pass

    class LLMFullResponseEndFrame(Frame):  # type: ignore[no-redef]
        pass

logger = logging.getLogger(__name__)

#: The languages Gemini Live may speak on a call. Luganda is not one of them.
GEMINI_LANGUAGES: tuple[str, ...] = ("en", "sw")


def gemini_languages() -> tuple[str, ...]:
    """Languages routed to Gemini by ``RECEPTIONIST_ENGINE_BY_LANGUAGE``."""
    table = get_engine_by_language()
    langs = tuple(lang for lang in GEMINI_LANGUAGES if table.get(lang) == "gemini_live")
    return langs or ("en",)


class GeminiCallerTap(FrameProcessor):
    """Records what the caller said, from Gemini Live's own input transcription.

    Gemini pushes those transcriptions *upstream*, towards the user
    aggregator, so this tap sits between the aggregator and the service —
    a tap after the service never sees them (and until this one existed,
    Gemini calls logged no caller turns at all: the staff transcript, the
    summary and the transfer ticket had only the assistant's side).

    With ``track_language`` (single-engine Gemini calls) it also follows the
    language the caller speaks, so the turn log, the RAG call log and the
    summary know the call went to Swahili. On a multilingual call the
    language sentinel owns that decision and this is off.
    """

    def __init__(
        self,
        room: Any,
        track_language: bool = False,
        languages: Sequence[str] = GEMINI_LANGUAGES,
        **kwargs: Any,
    ) -> None:
        super().__init__(enable_direct_mode=True, **kwargs)
        self.room = room
        self.track_language = track_language
        self.languages = tuple(languages)

    async def process_frame(
        self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        await self.push_frame(frame, direction)
        if not isinstance(frame, TranscriptionFrame) or self.room.state.engine not in ("", "gemini_live"):
            return
        text = (getattr(frame, "text", "") or "").strip()
        if not text:
            return
        self.room.state.turn_seq += 1
        self.room.state.caller_turns_count += 1
        turn = create_turn(
            call_id=self.room.call_id,
            seq=self.room.state.turn_seq,
            speaker="caller",
            kind="utterance",
            text=text,
        )
        hub.publish_call(self.room.call_id, "turn", turn)
        self.room.state.last_caller_text = text
        if self.track_language:
            await self._follow_language(text)

    async def _follow_language(self, text: str) -> None:
        current = self.room.state.locale or "en"
        detected = text_language(text, current, self.languages)
        if detected == current:
            return
        from .router import announce_language

        self.room.state.locale = detected
        message = announce_language(self.room, detected, source="auto")
        await self.push_frame(OutputTransportMessageUrgentFrame(message), FrameDirection.DOWNSTREAM)


class GeminiLiveTranscriptTap(FrameProcessor):
    """Live captions and turn records for what Gemini says (after the service).

    The caller's side is :class:`GeminiCallerTap`'s.
    """

    def __init__(self, room: Any, **kwargs: Any) -> None:
        super().__init__(enable_direct_mode=True, **kwargs)
        self.room = room
        # Multilingual calls: called once a language-switch interruption has
        # passed Gemini (this tap sits right after the service).
        self.on_switch_interrupt: Any = None
        self._assistant_buffer = ""
        self._last_emitted_text = ""

    async def _emit(self, message: dict[str, Any]) -> None:
        # Always downstream: the upstream copy of BotStoppedSpeakingFrame would
        # otherwise carry the final caption away from the output transport.
        await self.push_frame(OutputTransportMessageUrgentFrame(message), FrameDirection.DOWNSTREAM)

    async def process_frame(
        self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        if (
            isinstance(frame, InterruptionFrame)
            and (getattr(frame, "metadata", None) or {}).get("language_switch")
        ):
            # What Gemini said while its reply was held is discarded with it;
            # left here it would open Gemini's next caption on the call.
            self._assistant_buffer = ""
            self._last_emitted_text = ""
            await self.push_frame(frame, direction)
            if self.on_switch_interrupt is not None:
                self.on_switch_interrupt()
            return
        if self.room.state.engine not in ("", "gemini_live"):
            # A multilingual call has moved to the other engine; whatever
            # Gemini still says about the turn it lost is never heard, so it
            # is not logged or captioned either.
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, (UserStartedSpeakingFrame, InterruptionFrame)):
            # User started speaking or barged in: clear assistant text buffer
            self._assistant_buffer = ""
            self._last_emitted_text = ""
            # Notify frontend that user is speaking to reset active AI text
            # display — unless this is a language switch cutting Gemini off.
            if not (getattr(frame, "metadata", None) or {}).get("language_switch"):
                await self._emit({"type": "user_speaking", "speaking": True})

        elif isinstance(frame, TTSTextFrame):
            text = getattr(frame, "text", "") or ""
            if text:
                self._assistant_buffer += text
                accumulated = self._assistant_buffer.strip()
                if accumulated != self._last_emitted_text:
                    self._last_emitted_text = accumulated
                    caption_data = {
                        "type": "caption",
                        "speaker": "assistant",
                        "text": accumulated,
                        "final": False,
                        "turn_id": self.room.state.turn_seq,
                    }
                    await self._emit(caption_data)
                    hub.publish_call(self.room.call_id, "caption", caption_data)

        elif isinstance(frame, (BotStoppedSpeakingFrame, LLMFullResponseEndFrame)):
            accumulated = self._assistant_buffer.strip()
            if accumulated:
                self.room.state.turn_seq += 1
                self.room.state.ai_answers_count += 1
                turn = create_turn(
                    call_id=self.room.call_id,
                    seq=self.room.state.turn_seq,
                    speaker="assistant",
                    kind="answer",
                    text=accumulated,
                )
                hub.publish_call(self.room.call_id, "turn", turn)

                caption_data = {
                    "type": "caption",
                    "speaker": "assistant",
                    "text": accumulated,
                    "final": True,
                    "turn_id": self.room.state.turn_seq,
                }
                await self._emit(caption_data)
                hub.publish_call(self.room.call_id, "caption", caption_data)
                self._assistant_buffer = ""
                self._last_emitted_text = ""

        await self.push_frame(frame, direction)



class OfficerRequestBridge(FrameProcessor):
    """Turns the caller's "Talk to an officer" button into a request Gemini acts on.

    The cascaded brain handles ``RequestOfficerFrame`` itself; Gemini Live has
    no such hook, so on a Gemini call the button did nothing. This sits in
    front of the service and hands the request to Gemini as a system note,
    so it calls ``request_human_officer`` — the same path as asking aloud,
    ticket_queue check included — and tells the caller in their language.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(enable_direct_mode=True, **kwargs)

    async def process_frame(self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM) -> None:
        from .serializer import RequestOfficerFrame

        await super().process_frame(frame, direction)
        if isinstance(frame, RequestOfficerFrame):
            from pipecat.frames.frames import InputTextRawFrame

            await self.push_frame(InputTextRawFrame(
                text="[System note, not the caller: the caller pressed the 'Talk to an officer' button. "
                "Call request_human_officer now with reason \"caller_requested\".]"
            ), direction)
            return
        await self.push_frame(frame, direction)


def build_gemini_system_instruction(
    languages: Sequence[str] = GEMINI_LANGUAGES, luganda_handover: bool = False
) -> str:
    """Gemini's system instruction for a call it may hold in *languages*.

    ``luganda_handover``: the call can move to a Luganda engine, and Gemini
    has the ``hand_over_to_luganda`` tool to move it.
    """
    langs = [lang for lang in languages if lang in GEMINI_LANGUAGES] or ["en"]
    names = [LANGUAGE_NAMES[lang] for lang in langs]
    parts = [
        "You are the official simulated AI voice receptionist for the Uganda Revenue Authority (URA). "
        "You speak directly with taxpayers over a live audio call. "
        "Your tone is polite, professional, warm, and natural for an East African context.",
    ]
    if len(langs) == 1:
        parts.append(f"Always speak {names[0]}.")
    else:
        parts.append(
            "The call starts in English. Reply in the language of the caller's latest question — "
            f"{' or '.join(names)}. You yourself speak only {' and '.join(names)}."
        )
        if luganda_handover:
            # Without this Gemini, hearing Luganda, told a real caller it only
            # speaks English and Swahili while the language id still said
            # "English" — the Luganda colleague never got the call.
            parts.append(
                "A colleague on this call speaks Luganda. As soon as the caller speaks Luganda — a "
                "greeting, a question, or Luganda mixed with English tax words — or asks to speak "
                "Luganda, call `hand_over_to_luganda` and say nothing yourself: the colleague answers "
                "the same question in Luganda. Never tell a caller you cannot speak Luganda, and never "
                "request an officer because of the caller's language."
            )
        parts.append(
            "If a caller speaks any other language, reply briefly in English. English tax terms such as "
            "TIN, VAT or PAYE do not make a sentence Swahili. The tool result's `caller_language` is what "
            "the call's speech recognizer heard; trust it unless the words of the question are plainly "
            "in the other language."
        )
    parts += [
        "CRITICAL REQUIREMENT: For any specific tax questions (including TIN registration, tax rates, "
        "VAT, income tax, customs, EFRIS, motor vehicle transfers, or deadlines), you MUST invoke the "
        "`query_ura_tax_knowledge` tool to retrieve verified facts from URA's legal knowledge base. "
        "Always call it with the caller's question translated into English, then give the answer in "
        "the caller's language. Never guess or invent tax rates or legal deadlines.",
        "Copy every number, amount, percentage, date, TIN and reference exactly as the tool returns it — "
        "never round, convert or re-express a figure.",
        "When you are asked to say a line word for word, say it exactly as given, including any "
        "languages it names.",
        "If the caller explicitly requests to talk to a human agent, officer, or supervisor, invoke "
        "the `request_human_officer` tool.",
        "Keep your spoken answers concise, conversational, and easy to understand over the phone "
        "(1 to 3 sentences).",
    ]
    return " ".join(parts)


def is_gemini_live_available() -> bool:
    """Check whether Gemini Live dependencies and API key are configured."""
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        return False
    try:
        from pipecat.services.google.gemini_live.llm import GeminiLiveLLMService  # noqa: F401
        return True
    except (ImportError, Exception):
        return False


class GeminiLiveReceptionistBrain:
    """Controller companion for GeminiLiveLLMService handling URA state and greeting."""

    def __init__(
        self,
        room: Any,
        service: Any,
        chat_model: Any,
        task: Any = None,
        context: Any = None,
    ) -> None:
        self.room = room
        self.service = service
        self.chat_model = chat_model
        self.task = task
        self.context = context

    async def say_greeting(self) -> None:
        """Publish initial greeting turn for the taxpayer UI and conversation log."""
        self.room.state.turn_seq += 1
        turn = create_turn(
            call_id=self.room.call_id,
            seq=self.room.state.turn_seq,
            speaker="assistant",
            kind="notice",
            text=GREETING_TEXT,
        )
        hub.publish_call(self.room.call_id, "turn", turn)
        caption_data = {
            "type": "caption",
            "speaker": "assistant",
            "text": GREETING_TEXT,
            "final": True,
            "turn_id": self.room.state.turn_seq,
        }
        hub.publish_call(self.room.call_id, "caption", caption_data)

        # Trigger initial spoken greeting by adding developer message and queuing LLMRunFrame.
        # "Say exactly": asked to greet "with" a line, Gemini paraphrases it, and
        # the paraphrase drops the one sentence that matters here — that the
        # caller may use their own language.
        try:
            from pipecat.frames.frames import LLMRunFrame
            if self.context is not None:
                self.context.add_message({
                    "role": "developer",
                    "content": f"Greet the caller now. Say exactly, in English, word for word: \"{GREETING_TEXT}\"",
                })
            if self.task is not None:
                await self.task.queue_frames([LLMRunFrame()])
        except Exception:
            logger.debug("Failed queueing initial greeting LLMRunFrame", exc_info=True)


# What a tool tells Gemini when the call is being handed to the other engine.
_LEAVING_NOTICE = (
    "The caller is being passed to a colleague who speaks their language. "
    "Do nothing and say nothing further."
)


def build_gemini_tools(
    room: Any,
    chat_model: Any,
    service_ref: dict[str, Any],
    may_act: Callable[[], bool] | None = None,
    on_luganda: Callable[[], bool] | None = None,
) -> list[Any]:
    """The tools Gemini may call, bound to *room*.

    ``service_ref["service"]`` is filled in once the service exists — the
    transfer tool pushes the caller's status event through it. ``may_act``
    (multilingual calls) is false while the call is moving to another engine:
    Gemini still hears the caller then, and a transfer it opens or an answer
    it logs would outlive the reply that is thrown away. ``on_luganda``
    (multilingual calls with a Luganda engine) adds ``hand_over_to_luganda``;
    it returns whether the call is moving to Luganda.
    """
    from pipecat.adapters.schemas.function_schema import FunctionSchema

    def leaving(tool: str) -> bool:
        if may_act is None or may_act():
            return False
        logger.info("Ignoring Gemini's %r: the call is moving to another engine", tool)
        return True

    async def rag_query_handler(params: Any) -> None:
        """Execute RAG query against URA tax knowledge base via chat_model."""
        query = params.arguments.get("query", "")
        logger.info("Gemini Live tool call 'query_ura_tax_knowledge': %r", query)
        if leaving("query_ura_tax_knowledge"):
            await params.result_callback({"official_answer": "", "notice": _LEAVING_NOTICE})
            return

        reply_text = "Information is currently unavailable."
        sources: list[Any] = []
        if chat_model and hasattr(chat_model, "generate"):
            try:
                # English in, English out: Gemini was told to translate the
                # caller's question into English, and it translates the answer
                # back. Asking for the caller's locale here would add a
                # Sunflower MT hop each way for text Gemini re-translates anyway.
                res = await asyncio.to_thread(
                    chat_model.generate,
                    message=query,
                    conversation_id=room.state.conversation_id,
                    session_id=room.call_id,
                    top_k=4,
                    locale="en",
                    user_id=room.state.user_id,
                    tenant_id=room.state.tenant_id,
                )
                reply_text = res.get("reply", "") or res.get("text", "")
                sources = res.get("sources", [])
                faithfulness = res.get("faithfulness_score")
                if faithfulness is not None:
                    room.state.faithfulness_scores.append(float(faithfulness))
            except Exception:
                logger.exception("RAG tool execution failed for query %s", query)

        # Log conversation turn in DB — in the language the caller is speaking.
        try:
            db.log_conversation(
                session_id=room.call_id,
                conversation_id=room.state.conversation_id,
                user_message=f"[Tool query]: {query}",
                bot_reply=reply_text,
                # A JSON string, as the column expects — a bare list made
                # sqlite refuse the row, so no Gemini turn was ever logged.
                sources=json.dumps(sources if isinstance(sources, list) else []),
                user_id=room.state.user_id,
                locale=room.state.locale,
            )
        except Exception:
            logger.debug("Failed logging tool query to DB", exc_info=True)

        await params.result_callback({
            "official_answer": reply_text,
            "sources": sources[:2] if isinstance(sources, list) else [],
            # What our own language id heard — Gemini's guess alone once
            # answered an English caller in Swahili.
            "caller_language": LANGUAGE_NAMES.get(room.state.locale, "English"),
        })

    async def transfer_officer_handler(params: Any) -> None:
        """Transfer caller to human officer, enforcing ticket_queue invariant."""
        reason = params.arguments.get("reason", "caller_requested")
        logger.info("Gemini Live tool call 'request_human_officer': %r", reason)
        if leaving("request_human_officer"):
            await params.result_callback({"transferred": False, "notice": _LEAVING_NOTICE})
            return

        if not flags.is_enabled("ticket_queue"):
            update_call(room.call_id, transfer_reason=f"{reason}_queue_disabled")
            await params.result_callback({
                "transferred": False,
                "notice": (
                    "Transfers to live officers are currently unavailable. "
                    "Please inform the taxpayer to contact URA at 0800 117 000 during office hours."
                ),
            })
            return

        tid, status_event = open_transfer(
            room, chat_model, reason, question=room.state.last_caller_text or None
        )
        service = service_ref.get("service")
        if service is not None:
            await service.push_frame(OutputTransportMessageUrgentFrame(status_event))
        room.state.transfer_timer_task = asyncio.create_task(
            _gemini_transfer_timeout(room, service_ref, get_transfer_timeout_s(), tid)
        )

        await params.result_callback({
            "transferred": True,
            "ticket_id": tid,
            "notice": "Transfer initiated. Let the caller know an officer is being connected.",
        })

    async def luganda_handover_handler(params: Any) -> None:
        """Gemini heard Luganda: move the call to the engine that speaks it."""
        logger.info("Gemini Live tool call 'hand_over_to_luganda'")
        if leaving("hand_over_to_luganda") or (on_luganda is not None and on_luganda()):
            await params.result_callback({"handed_over": True, "notice": _LEAVING_NOTICE})
            return
        await params.result_callback({
            "handed_over": False,
            "notice": "The caller chose this call's language on screen; carry on in it.",
        })

    async def verify_tin_handler(params: Any) -> None:
        """Validate TIN format and check registration."""
        tin = str(params.arguments.get("tin", "")).strip()
        valid = len(tin) == 10 and tin.isdigit()
        await params.result_callback({
            "tin": tin,
            "valid": valid,
            "status": "valid_tin" if valid else "invalid_tin_format",
        })

    tools = [
        FunctionSchema(
            name="query_ura_tax_knowledge",
            description="Query official Uganda Revenue Authority tax guides, laws, rates, TIN registration, and compliance procedures.",
            properties={
                "query": {
                    "type": "string",
                    "description": "The taxpayer's question, translated into English.",
                }
            },
            required=["query"],
            handler=rag_query_handler,
        ),
        FunctionSchema(
            name="request_human_officer",
            description="Transfer the caller to a human URA tax officer or support agent when requested or when complex dispute resolution is required.",
            properties={
                "reason": {
                    "type": "string",
                    "description": "Reason for human transfer requested by caller.",
                }
            },
            required=["reason"],
            handler=transfer_officer_handler,
        ),
        FunctionSchema(
            name="verify_taxpayer_tin",
            description="Verify if a 10-digit Tax Identification Number (TIN) format is valid.",
            properties={
                "tin": {
                    "type": "string",
                    "description": "10-digit TIN.",
                }
            },
            required=["tin"],
            handler=verify_tin_handler,
        ),
    ]
    if on_luganda is not None:
        tools.append(FunctionSchema(
            name="hand_over_to_luganda",
            description=(
                "Pass the call to the colleague who speaks Luganda. Call it as soon as the caller "
                "speaks Luganda (a greeting, a question, or Luganda mixed with English words) or asks "
                "to speak Luganda, then say nothing more yourself."
            ),
            properties={
                "heard": {
                    "type": "string",
                    "description": "A few of the caller's words, as you heard them.",
                }
            },
            required=[],
            handler=luganda_handover_handler,
        ))
    return tools


async def _gemini_transfer_timeout(room: Any, service_ref: dict[str, Any], timeout_s: float, ticket_ref: str) -> None:
    """No officer came: tell the caller their reference, through Gemini's own voice."""
    await asyncio.sleep(timeout_s)
    status_event = close_transfer_on_timeout(room, ticket_ref)
    if status_event is None:
        return
    service = service_ref.get("service")
    if service is None:
        return
    from pipecat.frames.frames import InputTextRawFrame

    await service.push_frame(OutputTransportMessageUrgentFrame(status_event))
    line = phrase("officers_busy", room.state.locale, ref=ticket_ref or "URA-CALL")
    await service.queue_frame(InputTextRawFrame(
        text=f"[System note, not the caller: no officer is free. Tell the caller exactly this: \"{line}\"]"
    ))


def build_gemini_live_service(
    room: Any,
    chat_model: Any = None,
    languages: Sequence[str] | None = None,
    may_act: Callable[[], bool] | None = None,
    on_luganda: Callable[[], bool] | None = None,
) -> tuple[Any, Any, Any]:
    """Gemini Live service plus its context and aggregator pair.

    Returns ``(service, context, context_aggregator)``. Shared by the
    single-engine pipeline below and the multilingual one, which passes
    ``may_act`` and ``on_luganda`` (see :func:`build_gemini_tools`).
    """
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is required for Gemini Live engine")

    try:
        from pipecat.processors.aggregators.llm_context import LLMContext
        from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
        from pipecat.services.google.gemini_live.llm import (
            GeminiLiveLLMService,
            GeminiLiveLLMSettings,
            GeminiModalities,
            GeminiVADParams,
        )
    except ImportError as exc:
        raise RuntimeError("Pipecat Google Gemini Live dependencies are not installed") from exc

    service_ref: dict[str, Any] = {}
    tools = build_gemini_tools(room, chat_model, service_ref, may_act, on_luganda)

    try:
        from google.genai.types import EndSensitivity, StartSensitivity
        # Gemini's own VAD is what lets a caller talk over it — it sends
        # "interrupted" and stops. At START_SENSITIVITY_LOW a caller talking
        # over the greeting was never heard and it played to the end.
        vad_config = GeminiVADParams(
            start_sensitivity=(
                StartSensitivity.START_SENSITIVITY_HIGH
                if get_gemini_vad_start_sensitivity() == "high"
                else StartSensitivity.START_SENSITIVITY_LOW
            ),
            end_sensitivity=(
                EndSensitivity.END_SENSITIVITY_HIGH
                if get_gemini_vad_end_sensitivity() == "high"
                else EndSensitivity.END_SENSITIVITY_LOW
            ),
            silence_duration_ms=get_gemini_vad_silence_ms(),
            prefix_padding_ms=get_gemini_vad_prefix_padding_ms(),
        )
    except Exception:
        vad_config = None

    settings = GeminiLiveLLMSettings(
        model=get_gemini_live_model(),
        system_instruction=build_gemini_system_instruction(
            languages or gemini_languages(), luganda_handover=on_luganda is not None
        ),
        voice=get_gemini_live_voice(),
        modalities=GeminiModalities.AUDIO,
        vad=vad_config,
    )
    service = GeminiLiveLLMService(api_key=api_key, settings=settings, tools=tools)
    service_ref["service"] = service

    context = LLMContext()
    context_aggregator = LLMContextAggregatorPair(context)
    return service, context, context_aggregator


def build_gemini_live_pipeline(
    room: Any,
    websocket: Any,
    speech_model: Any = None,
    chat_model: Any = None,
) -> tuple[Any, Any, GeminiLiveReceptionistBrain]:
    """Assemble a Pipecat pipeline using GeminiLiveLLMService with RAG function calling.

    The RAG brain (chat_model.generate) is preserved as a tool callable by Gemini Live.
    Human officer handoff is preserved as a tool honoring the ticket_queue invariant.
    """
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is required for Gemini Live engine")

    try:
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.task import PipelineParams, PipelineTask
        from pipecat.transports.websocket.fastapi import (
            FastAPIWebsocketParams,
            FastAPIWebsocketTransport,
        )
    except ImportError as exc:
        raise RuntimeError("Pipecat Google Gemini Live dependencies are not installed") from exc

    languages = gemini_languages()
    gemini_live_service, context, context_aggregator = build_gemini_live_service(
        room, chat_model, languages
    )

    serializer = BrowserCallSerializer(room=room)
    transport_params = FastAPIWebsocketParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        add_wav_header=False,
        serializer=serializer,
        session_timeout=get_max_call_s(),
    )
    transport = FastAPIWebsocketTransport(websocket, transport_params)

    caller_tap = CallerAudioTap(room=room)
    transcript_tap = GeminiLiveTranscriptTap(room=room)

    pipeline_elements = [
        transport.input(),
        caller_tap,
        context_aggregator.user(),
        GeminiCallerTap(room=room, track_language=len(languages) > 1, languages=languages),
        OfficerRequestBridge(),
        gemini_live_service,
        transcript_tap,
        transport.output(),
        context_aggregator.assistant(),
    ]

    pipeline = Pipeline(pipeline_elements)
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=16000,
            audio_out_sample_rate=16000,
            enable_metrics=True,
        ),
        cancel_on_idle_timeout=False,
    )

    brain = GeminiLiveReceptionistBrain(
        room=room,
        service=gemini_live_service,
        chat_model=chat_model,
        task=task,
        context=context,
    )

    return task, transport, brain


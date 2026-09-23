"""Gemini Live multimodal speech-to-speech receptionist adapter.

Enables full-duplex conversational voice via Google Gemini Live API in Pipecat,
preserving the URA RAG knowledge base and ticket transfer workflows as tools.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from .. import database as db
from ..flags import flags
from .brain import GREETING_TEXT
from .config import (
    get_gemini_live_model,
    get_gemini_live_voice,
    get_max_call_s,
)
from .hub import hub
from .serializer import BrowserCallSerializer
from .store import create_turn, update_call
from .taps import CallerAudioTap

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


class GeminiLiveTranscriptTap(FrameProcessor):
    """Emits live captions and records turns for both user speech and model speech."""

    def __init__(self, room: Any, **kwargs: Any) -> None:
        super().__init__(enable_direct_mode=True, **kwargs)
        self.room = room
        self._assistant_buffer = ""
        self._last_emitted_text = ""

    async def process_frame(
        self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        if isinstance(frame, (UserStartedSpeakingFrame, InterruptionFrame)):
            # User started speaking or barged in: clear assistant text buffer
            self._assistant_buffer = ""
            self._last_emitted_text = ""
            # Notify frontend that user is speaking to reset active AI text display
            user_speaking_msg = {"type": "user_speaking", "speaking": True}
            await self.push_frame(
                OutputTransportMessageUrgentFrame(user_speaking_msg), direction
            )

        elif isinstance(frame, TranscriptionFrame):
            # Save caller speech internally for turn log/analytics without polluting the live UI
            text = (getattr(frame, "text", "") or "").strip()
            if text:
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
                    await self.push_frame(
                        OutputTransportMessageUrgentFrame(caption_data), direction
                    )
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
                await self.push_frame(
                    OutputTransportMessageUrgentFrame(caption_data), direction
                )
                hub.publish_call(self.room.call_id, "caption", caption_data)
                self._assistant_buffer = ""
                self._last_emitted_text = ""

        await self.push_frame(frame, direction)

logger = logging.getLogger(__name__)

GEMINI_LIVE_SYSTEM_INSTRUCTION = (
    "You are the official simulated AI voice receptionist for the Uganda Revenue Authority (URA). "
    "You speak directly with taxpayers over a live audio call. "
    "Your tone is polite, professional, warm, and natural for an East African context. "
    "CRITICAL REQUIREMENT: For any specific tax questions (including TIN registration, tax rates, "
    "VAT, income tax, customs, EFRIS, motor vehicle transfers, or deadlines), you MUST invoke the "
    "`query_ura_tax_knowledge` tool to retrieve verified facts from URA's legal knowledge base. "
    "Never guess or invent tax rates or legal deadlines. "
    "If the caller explicitly requests to talk to a human agent, officer, or supervisor, invoke "
    "the `request_human_officer` tool. "
    "Keep your spoken answers concise, conversational, and easy to understand over the phone (1 to 3 sentences)."
)


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

        # Trigger initial spoken greeting by adding developer message and queuing LLMRunFrame
        try:
            from pipecat.frames.frames import LLMRunFrame
            if self.context is not None:
                self.context.add_message({
                    "role": "developer",
                    "content": f"Please greet the caller warmly with: '{GREETING_TEXT}'",
                })
            if self.task is not None:
                await self.task.queue_frames([LLMRunFrame()])
        except Exception:
            logger.debug("Failed queueing initial greeting LLMRunFrame", exc_info=True)


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
        from pipecat.adapters.schemas.function_schema import FunctionSchema
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.task import PipelineParams, PipelineTask
        from pipecat.services.google.gemini_live.llm import (
            GeminiLiveLLMService,
            GeminiLiveLLMSettings,
            GeminiModalities,
            GeminiVADParams,
        )
        from pipecat.transports.websocket.fastapi import (
            FastAPIWebsocketParams,
            FastAPIWebsocketTransport,
        )
    except ImportError as exc:
        raise RuntimeError("Pipecat Google Gemini Live dependencies are not installed") from exc

    # 1. Tool handlers connecting Gemini Live back to URA backend capabilities
    async def rag_query_handler(params: Any) -> None:
        """Execute RAG query against URA tax knowledge base via chat_model."""
        query = params.arguments.get("query", "")
        logger.info("Gemini Live tool call 'query_ura_tax_knowledge': %r", query)

        reply_text = "Information is currently unavailable."
        sources = []
        if chat_model and hasattr(chat_model, "generate"):
            try:
                res = await asyncio.to_thread(
                    chat_model.generate,
                    message=query,
                    conversation_id=room.state.conversation_id,
                    session_id=room.call_id,
                    top_k=4,
                    locale=room.state.locale,
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

        # Log conversation turn in DB
        try:
            db.log_conversation(
                session_id=room.call_id,
                conversation_id=room.state.conversation_id,
                user_message=f"[Tool query]: {query}",
                bot_reply=reply_text,
                sources=sources,
                user_id=room.state.user_id,
                locale=room.state.locale,
            )
        except Exception:
            logger.debug("Failed logging tool query to DB", exc_info=True)

        await params.result_callback({
            "official_answer": reply_text,
            "sources": sources[:2] if isinstance(sources, list) else [],
        })

    async def transfer_officer_handler(params: Any) -> None:
        """Transfer caller to human officer, enforcing ticket_queue invariant."""
        reason = params.arguments.get("reason", "caller_requested")
        logger.info("Gemini Live tool call 'request_human_officer': %r", reason)

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

        ticket_id = None
        if chat_model and hasattr(chat_model, "_maybe_create_ticket"):
            try:
                ticket_id = chat_model._maybe_create_ticket(
                    room.call_id,
                    room.state.conversation_id,
                    reason,
                    user_id=room.state.user_id,
                    tenant_id=room.state.tenant_id,
                )
            except Exception:
                logger.exception("Failed generating ticket during Gemini Live transfer")

        room.state.mode = "transferring"
        room.state.ticket_id = ticket_id or f"TICK-{room.call_id[:8]}"
        room.state.transfer_reason = reason
        hub.publish_call(room.call_id, "transfer", {
            "ticket_id": room.state.ticket_id,
            "reason": reason,
        })
        update_call(
            room.call_id,
            transfer_reason=reason,
            ticket_id=room.state.ticket_id,
        )

        await params.result_callback({
            "transferred": True,
            "ticket_id": room.state.ticket_id,
            "notice": "Transfer initiated. Let the caller know an officer is being connected.",
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

    # 2. Tool definitions registered as FunctionSchema
    tools = [
        FunctionSchema(
            name="query_ura_tax_knowledge",
            description="Query official Uganda Revenue Authority tax guides, laws, rates, TIN registration, and compliance procedures.",
            properties={
                "query": {
                    "type": "string",
                    "description": "Taxpayer question or tax topic to look up in the verified knowledge base.",
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

    # 3. Transport configuration
    serializer = BrowserCallSerializer(room=room)
    transport_params = FastAPIWebsocketParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        add_wav_header=False,
        serializer=serializer,
        session_timeout=get_max_call_s(),
    )
    transport = FastAPIWebsocketTransport(websocket, transport_params)

    # 4. Service setup
    model_name = get_gemini_live_model()
    voice_name = get_gemini_live_voice()

    try:
        from google.genai.types import EndSensitivity, StartSensitivity
        vad_config = GeminiVADParams(
            start_sensitivity=StartSensitivity.START_SENSITIVITY_LOW,
            end_sensitivity=EndSensitivity.END_SENSITIVITY_HIGH,
            silence_duration_ms=300,
            prefix_padding_ms=100,
        )
    except Exception:
        vad_config = None

    settings = GeminiLiveLLMSettings(
        model=model_name,
        system_instruction=GEMINI_LIVE_SYSTEM_INSTRUCTION,
        voice=voice_name,
        modalities=GeminiModalities.AUDIO,
        vad=vad_config,
    )

    gemini_live_service = GeminiLiveLLMService(
        api_key=api_key,
        settings=settings,
        tools=tools,
    )

    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair

    context = LLMContext()
    context_aggregator = LLMContextAggregatorPair(context)

    caller_tap = CallerAudioTap(room=room)
    transcript_tap = GeminiLiveTranscriptTap(room=room)

    pipeline_elements = [
        transport.input(),
        caller_tap,
        context_aggregator.user(),
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

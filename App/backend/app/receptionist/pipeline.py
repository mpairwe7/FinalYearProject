"""Assembles the Pipecat voice pipeline for an active CallRoom."""

from __future__ import annotations

import logging
from typing import Any

def _get_speech_model() -> Any:
    try:
        from ..main import app
        return getattr(getattr(app, "state", None), "speech", None)
    except Exception:
        return None
from .brain import UraReceptionistBrain
from .config import get_max_call_s, get_receptionist_engine
from .serializer import BrowserCallSerializer
from .stt import UraWhisperSTT
from .taps import CallerAudioTap, LivePartialTranscriptTap, TranscriptTap
from .tts import UraSpeechTTS

logger = logging.getLogger(__name__)


def build_call_pipeline(room: Any, websocket: Any) -> Any:
    """Build and wire the Pipecat pipeline, transport, and runner for *room*."""
    try:
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.runner import PipelineRunner
        from pipecat.pipeline.task import PipelineParams, PipelineTask
        from pipecat.transports.websocket.fastapi import (
            FastAPIWebsocketParams,
            FastAPIWebsocketTransport,
        )
    except ImportError as exc:
        raise RuntimeError("Pipecat is required to build the call pipeline") from exc

    speech_model = _get_speech_model()
    chat_model = None
    try:
        from ..main import app
        chat_model = getattr(getattr(app, "state", None), "model", None)
    except Exception:
        logger.debug("ChatModel singleton lookup failed; using mock/fallback")

    if get_receptionist_engine() == "gemini_live":
        try:
            from .gemini_live import build_gemini_live_pipeline
            return build_gemini_live_pipeline(
                room, websocket, speech_model=speech_model, chat_model=chat_model
            )
        except Exception as exc:
            logger.warning(
                "Failed to initialize Gemini Live pipeline (%s); falling back to cascaded pipeline",
                exc,
            )

    # 1. Transport
    serializer = BrowserCallSerializer(room=room)
    transport_params = FastAPIWebsocketParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        add_wav_header=False,
        serializer=serializer,
        session_timeout=get_max_call_s(),
    )
    transport = FastAPIWebsocketTransport(websocket, transport_params)

    # 2. Pipeline processors
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams
    from pipecat.processors.audio.vad_processor import VADProcessor
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import (
        LLMContextAggregatorPair,
        LLMUserAggregatorParams,
    )
    from pipecat.turns.user_turn_strategies import (
        BaseUserTurnStopStrategy,
        UserTurnStrategies,
        VADUserTurnStartStrategy,
    )
    from pipecat.frames.frames import TranscriptionFrame

    class STTTranscriptionStopStrategy(BaseUserTurnStopStrategy):
        """Finalize the user turn as soon as STT produces a finalized transcription."""

        async def process_frame(self, frame: Any) -> Any:
            logger.info("STTTranscriptionStopStrategy.process_frame: frame=%r", frame)
            if isinstance(frame, TranscriptionFrame) and getattr(frame, "finalized", False) and getattr(frame, "text", "").strip():
                logger.info("STTTranscriptionStopStrategy: final transcript received %r -> triggering turn stop", frame.text)
                await self.trigger_user_turn_stopped()
            return None

    vad_analyzer = SileroVADAnalyzer(params=VADParams(stop_secs=0.6, start_secs=0.2))
    vad_processor = VADProcessor(vad_analyzer=vad_analyzer)
    context = LLMContext()
    context_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                start=[VADUserTurnStartStrategy()],
                stop=[STTTranscriptionStopStrategy()],
            ),
            user_turn_stop_timeout=20.0,
        ),
    )

    caller_tap = CallerAudioTap(room=room)
    stt = UraWhisperSTT(speech_model=speech_model, user_id=room.state.user_id, language=room.state.locale)
    # Sits after the VAD (it is driven by the speaking frames the VAD
    # broadcasts) and before the STT, so the caller reads their sentence taking
    # shape instead of waiting for the segmented recognizer to close the turn.
    partial_tap = LivePartialTranscriptTap(
        room=room, speech_model=speech_model, language=room.state.locale
    )
    transcript_tap = TranscriptTap(room=room)
    brain = UraReceptionistBrain(room=room, chat_model=chat_model)
    tts = UraSpeechTTS(speech_model=speech_model, language=room.state.locale)

    # 3. Assemble pipeline elements
    pipeline_elements = [
        transport.input(),
        caller_tap,
        vad_processor,
        partial_tap,
        stt,
        transcript_tap,
        context_aggregator.user(),
        brain,
        tts,
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
    )

    return task, transport, brain

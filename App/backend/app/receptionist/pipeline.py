"""Assembles the Pipecat voice pipeline for an active CallRoom."""

from __future__ import annotations

import logging
from typing import Any

from ..flags import flags
from .brain import UraReceptionistBrain
from .config import get_max_call_s, get_receptionist_engine
from .serializer import BrowserCallSerializer
from .stt import UraWhisperSTT
from .taps import CallerAudioTap, LivePartialTranscriptTap, TranscriptTap
from .tts import UraSpeechTTS
from .turns import VAD_STOP_SECS, build_cascaded_turn_strategies

logger = logging.getLogger(__name__)


def _get_speech_model() -> Any:
    try:
        from ..main import app
        return getattr(getattr(app, "state", None), "speech", None)
    except Exception:
        return None


def _get_chat_model() -> Any:
    try:
        from ..main import app
        return getattr(getattr(app, "state", None), "model", None)
    except Exception:
        logger.debug("ChatModel singleton lookup failed; using mock/fallback")
        return None


def build_transport(room: Any, websocket: Any) -> Any:
    """The caller's WebSocket as a Pipecat transport (16 kHz PCM both ways)."""
    from pipecat.transports.websocket.fastapi import (
        FastAPIWebsocketParams,
        FastAPIWebsocketTransport,
    )

    params = FastAPIWebsocketParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        add_wav_header=False,
        serializer=BrowserCallSerializer(room=room),
        session_timeout=get_max_call_s(),
    )
    return FastAPIWebsocketTransport(websocket, params)


def build_cascaded_branch(room: Any, speech_model: Any, chat_model: Any) -> tuple[list[Any], Any, UraReceptionistBrain]:
    """The cascaded engine's processors, from VAD to TTS.

    Returns ``(processors, assistant_aggregator, brain)``. The assistant
    aggregator is returned separately: the single-engine pipeline places it
    after the output transport, the multilingual one at the end of the branch.
    Every stage that depends on the language reads ``room.state.locale`` per
    utterance, so a call that switches into Luganda is transcribed, answered
    and voiced in Luganda from its next turn.
    """
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import (
        LLMContextAggregatorPair,
        LLMUserAggregatorParams,
    )
    from pipecat.processors.audio.vad_processor import VADProcessor

    vad_processor = VADProcessor(
        vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=VAD_STOP_SECS, start_secs=0.2))
    )
    context_aggregator = LLMContextAggregatorPair(
        LLMContext(),
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=build_cascaded_turn_strategies(),
            # Whisper-SALT is segmented: its transcript can take a couple of
            # seconds after the caller stops on a long Luganda turn. This is
            # only the backstop for a turn no strategy ever closes.
            user_turn_stop_timeout=20.0,
        ),
    )
    initial = room.state.locale
    stt = UraWhisperSTT(speech_model=speech_model, user_id=room.state.user_id, language=initial, room=room)
    # Sits after the VAD (it is driven by the speaking frames the VAD
    # broadcasts) and before the STT, so the caller reads their sentence taking
    # shape instead of waiting for the segmented recognizer to close the turn.
    partial_tap = LivePartialTranscriptTap(room=room, speech_model=speech_model, language=initial)
    brain = UraReceptionistBrain(room=room, chat_model=chat_model)
    tts = UraSpeechTTS(speech_model=speech_model, language=initial, room=room)
    processors = [
        vad_processor,
        partial_tap,
        stt,
        TranscriptTap(room=room),
        context_aggregator.user(),
        brain,
        tts,
    ]
    return processors, context_aggregator.assistant(), brain


def build_call_pipeline(room: Any, websocket: Any) -> Any:
    """Build and wire the Pipecat pipeline, transport, and runner for *room*."""
    try:
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.task import PipelineParams, PipelineTask
    except ImportError as exc:
        raise RuntimeError("Pipecat is required to build the call pipeline") from exc

    speech_model = _get_speech_model()
    chat_model = _get_chat_model()

    if flags.is_enabled("receptionist_language_detection"):
        try:
            from .multilingual import build_multilingual_pipeline
            return build_multilingual_pipeline(
                room, websocket, speech_model=speech_model, chat_model=chat_model
            )
        except Exception as exc:
            logger.warning(
                "Failed to initialize the multilingual pipeline (%s); falling back to one engine",
                exc,
                exc_info=True,
            )

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

    from .hold_gate import OfficerOutputGate

    transport = build_transport(room, websocket)
    processors, assistant_aggregator, brain = build_cascaded_branch(room, speech_model, chat_model)
    pipeline = Pipeline([
        transport.input(),
        CallerAudioTap(room=room),
        *processors,
        OfficerOutputGate(room),
        transport.output(),
        assistant_aggregator,
    ])
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=16000,
            audio_out_sample_rate=16000,
            enable_metrics=True,
        ),
    )

    return task, transport, brain

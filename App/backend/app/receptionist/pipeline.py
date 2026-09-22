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
from .config import get_max_call_s
from .serializer import BrowserCallSerializer
from .stt import UraWhisperSTT
from .taps import CallerAudioTap, TranscriptTap
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
    caller_tap = CallerAudioTap(room=room)
    stt = UraWhisperSTT(speech_model=speech_model, user_id=room.state.user_id, language=room.state.locale)
    transcript_tap = TranscriptTap(room=room)
    brain = UraReceptionistBrain(room=room, chat_model=chat_model)
    tts = UraSpeechTTS(speech_model=speech_model, language=room.state.locale)

    # 3. Assemble pipeline elements
    pipeline_elements = [
        transport.input(),
        caller_tap,
        stt,
        transcript_tap,
        brain,
        tts,
        transport.output(),
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

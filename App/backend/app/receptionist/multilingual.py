"""The multilingual receptionist pipeline (flag ``receptionist_language_detection``).

::

    transport.input()
      → CallerAudioTap        (bridged calls: caller audio to the officer)
      → LanguageSentinel      (copies audio, votes on each utterance's language)
      → VAD → partials → Whisper-SALT → TranscriptTap → user agg
      → UraReceptionistBrain  (Sunflower against Qdrant, in the call's language)
      → UraSpeechTTS          (Orpheus; Spark-TTS-SALT when the sidecar is down)
      → assistant agg
      → ClientEventOutlet     (the router's language events to the caller's screen)
      → transport.output()

Every call opens in the default language. The sentinel and
:class:`~app.receptionist.router.LanguageRouter` move it to English, Luganda or
Swahili when the caller speaks it, and back; every stage reads
``room.state.locale`` per utterance, so one local engine serves all three.
No stage calls a cloud speech or language API.
"""

from __future__ import annotations

import logging
from typing import Any

from .config import get_default_language, get_languages
from .language import LanguagePolicy, PolicyConfig
from .router import LanguageRouter
from .taps import CallerAudioTap

logger = logging.getLogger(__name__)

try:
    from pipecat.frames.frames import Frame, OutputTransportMessageUrgentFrame
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
except ImportError:  # pragma: no cover — only built inside a Pipecat pipeline
    FrameProcessor = object  # type: ignore[assignment,misc]


class ClientEventOutlet(FrameProcessor):  # type: ignore[misc,valid-type]
    """Where the router's messages join the stream to the caller's socket."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(enable_direct_mode=True, **kwargs)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)

    async def send(self, message: dict[str, Any]) -> None:
        await self.push_frame(OutputTransportMessageUrgentFrame(message), FrameDirection.DOWNSTREAM)


def build_multilingual_pipeline(
    room: Any,
    websocket: Any,
    speech_model: Any = None,
    chat_model: Any = None,
) -> tuple[Any, Any, Any]:
    """Assemble the pipeline in the module docstring. Returns ``(task, transport, brain)``."""
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.task import PipelineParams, PipelineTask

    from .hold_gate import OfficerOutputGate
    from .pipeline import build_cascaded_branch, build_transport
    from .sentinel import LanguageSentinel

    languages = get_languages()
    default = get_default_language()

    # The call opens in the default language whatever the chat was set to;
    # the caller's own choice is kept as a hint for staff and metrics only.
    room.state.preferred_locale = room.state.locale
    room.state.locale = default
    room.state.initial_locale = default
    room.state.languages_used = [default]

    policy = LanguagePolicy(active=default, config=PolicyConfig.from_env())
    outlet = ClientEventOutlet()
    router = LanguageRouter(room, policy, speech_model, outlet=outlet)

    processors, assistant_aggregator, brain = build_cascaded_branch(room, speech_model, chat_model)
    router.brain = brain
    brain.on_switch_interrupt = router.switch_passed
    brain.turn_claimed = router.claims_turn

    sentinel = LanguageSentinel(room, speech_model, router, languages)
    router.interrupt = sentinel.interrupt_for_switch
    router.barge_in = sentinel.interrupt_for_barge_in

    transport = build_transport(room, websocket)
    pipeline = Pipeline([
        transport.input(),
        CallerAudioTap(room=room, speech_model=speech_model),
        sentinel,
        *processors,
        assistant_aggregator,
        outlet,
        OfficerOutputGate(room),
        transport.output(),
    ])
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=16000,
            audio_out_sample_rate=16000,
            enable_metrics=True,
        ),
        cancel_on_idle_timeout=False,
    )

    logger.info("Multilingual call %s: languages=%s opening=%s", room.call_id, languages, default)
    return task, transport, brain

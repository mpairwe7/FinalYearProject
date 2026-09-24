"""The multilingual receptionist pipeline (flag ``receptionist_language_detection``).

::

    transport.input()
      → CallerAudioTap        (bridged calls: caller audio to the officer)
      → LanguageSentinel      (copies audio, votes on each utterance's language)
      → ParallelPipeline      (one branch per engine, gated by EngineSelector)
          ├─ gemini_live: EngineGate → user agg → GeminiCallerTap → OfficerRequestBridge → GeminiLiveLLMService
          │               → InterruptedReplyMute → GeminiLiveTranscriptTap → OutputHoldGate → assistant agg
          │               → EngineGate
          └─ cascaded:    EngineGate → VAD → partials → Whisper-SALT → TranscriptTap → user agg
                          → UraReceptionistBrain → UraSpeechTTS (Orpheus for lg) → assistant agg → EngineGate
      → ClientEventOutlet     (the router's language events to the caller's screen)
      → transport.output()

Every call opens in the default language (English, on Gemini). The sentinel
and :class:`~app.receptionist.router.LanguageRouter` move it to Luganda (the
cascaded engine) or Swahili (Gemini, or cascaded if so configured) when the
caller speaks it, and back.

Why not Pipecat's ``ServiceSwitcher``: it can hold a whole ``Pipeline`` as a
"service", but its gates filter only frames going *into* a service and
frames coming back *up* out of it. An inactive engine's in-flight reply —
Gemini finishing the answer to a question that just moved to Luganda, or the
cascaded brain's late answer after a move to English — would still flow
downstream to the speaker. :class:`EngineGate` closes both ends, both ways.
"""

from __future__ import annotations

import logging
from typing import Any

from .config import (
    get_default_language,
    get_engine_by_language,
    get_languages,
    get_lid_hold_timeout_ms,
)
from .language import LanguagePolicy, PolicyConfig
from .router import EngineSelector, LanguageRouter
from .taps import CallerAudioTap

logger = logging.getLogger(__name__)

try:
    from pipecat.frames.frames import (
        CancelFrame,
        EndFrame,
        ErrorFrame,
        Frame,
        InterruptionFrame,
        LLMRunFrame,
        OutputTransportMessageUrgentFrame,
        StartFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
except ImportError:  # pragma: no cover — only built inside a Pipecat pipeline
    FrameProcessor = object  # type: ignore[assignment,misc]


class EngineGate(FrameProcessor):  # type: ignore[misc,valid-type]
    """Passes frames through one engine's branch only while that engine is active.

    Always passes, whichever engine is active: lifecycle frames (every branch
    must start and stop), errors, and interruptions (a language switch
    interrupts the engine being left). ``LLMRunFrame`` also reaches both
    branches going in: it primes Gemini's session even when the call opens
    on the other engine, and the cascaded brain ignores a context with no
    question in it.
    """

    def __init__(self, selector: EngineSelector, engine: str, head: bool, **kwargs: Any) -> None:
        super().__init__(enable_direct_mode=True, **kwargs)
        self.selector = selector
        self.engine = engine
        self.head = head

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if self._passes(frame, direction):
            await self.push_frame(frame, direction)

    def _passes(self, frame: Frame, direction: FrameDirection) -> bool:
        if isinstance(frame, (StartFrame, EndFrame, CancelFrame, ErrorFrame, InterruptionFrame)):
            return True
        if self.head and direction == FrameDirection.DOWNSTREAM and isinstance(frame, LLMRunFrame):
            return True
        return self.selector.active == self.engine


class ClientEventOutlet(FrameProcessor):  # type: ignore[misc,valid-type]
    """Where the router's messages join the stream to the caller's socket."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(enable_direct_mode=True, **kwargs)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)

    async def send(self, message: dict[str, Any]) -> None:
        await self.push_frame(OutputTransportMessageUrgentFrame(message), FrameDirection.DOWNSTREAM)


class MultilingualGreeter:
    """``say_greeting`` for ws.py: the opening engine greets, Gemini is primed either way."""

    def __init__(self, opening_engine: str, gemini_brain: Any, cascaded_brain: Any, task: Any) -> None:
        self.opening_engine = opening_engine
        self.gemini_brain = gemini_brain
        self.cascaded_brain = cascaded_brain
        self.task = task

    async def say_greeting(self) -> None:
        if self.opening_engine == "gemini_live" and self.gemini_brain is not None:
            await self.gemini_brain.say_greeting()
            return
        if self.cascaded_brain is not None:
            await self.cascaded_brain.say_greeting()
        if self.gemini_brain is not None and self.task is not None:
            # Gemini only accepts text after its first context; give it one now
            # so a later switch onto it can be answered at once.
            await self.task.queue_frames([LLMRunFrame()])

    async def _transfer(self, reason: str) -> None:
        """ws.py's fallback loop calls this on a request_officer message."""
        if self.cascaded_brain is not None:
            await self.cascaded_brain._transfer(reason)


def build_multilingual_pipeline(
    room: Any,
    websocket: Any,
    speech_model: Any = None,
    chat_model: Any = None,
) -> tuple[Any, Any, MultilingualGreeter]:
    """Assemble the pipeline in the module docstring. Returns ``(task, transport, greeter)``."""
    from pipecat.pipeline.parallel_pipeline import ParallelPipeline
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.task import PipelineParams, PipelineTask

    from .gemini_live import (
        GeminiCallerTap,
        GeminiLiveReceptionistBrain,
        GeminiLiveTranscriptTap,
        OfficerRequestBridge,
        build_gemini_live_service,
    )
    from .hold_gate import InterruptedReplyMute, OutputHoldGate
    from .pipeline import build_cascaded_branch, build_transport
    from .sentinel import LanguageSentinel

    languages = get_languages()
    engine_by_language = {lang: engine for lang, engine in get_engine_by_language().items() if lang in languages}
    default = get_default_language()
    opening_engine = engine_by_language[default]

    # The call opens in the default language whatever the chat was set to;
    # the caller's own choice is kept as a hint for staff and metrics only.
    room.state.preferred_locale = room.state.locale
    room.state.locale = default
    room.state.initial_locale = default
    room.state.languages_used = [default]
    room.state.engine = opening_engine

    selector = EngineSelector(opening_engine)
    policy = LanguagePolicy(active=default, config=PolicyConfig.from_env())
    outlet = ClientEventOutlet()
    router = LanguageRouter(room, policy, selector, engine_by_language, speech_model, outlet=outlet)

    engines = set(engine_by_language.values())
    branches: list[list[Any]] = []
    gemini_brain = None
    task_ref: dict[str, Any] = {}

    if "gemini_live" in engines:
        gemini_langs = tuple(lang for lang, eng in engine_by_language.items() if eng == "gemini_live")
        # Gemini hears every caller and knows Luganda when it hears it — a
        # second listener beside the sentinel, for the Luganda that
        # Whisper-SALT's language token takes for English.
        luganda_elsewhere = engine_by_language.get("lg") not in (None, "gemini_live")
        service, context, aggregators = build_gemini_live_service(
            room,
            chat_model,
            gemini_langs,
            may_act=lambda: router.engine_may_act("gemini_live"),
            on_luganda=(lambda: router.on_engine_heard("lg", "gemini_live")) if luganda_elsewhere else None,
        )
        hold_gate = OutputHoldGate(timeout_ms=get_lid_hold_timeout_ms(), on_held=router.on_held)
        router.hold_gate = hold_gate
        router.gemini_service = service
        gemini_tap = GeminiLiveTranscriptTap(room=room)
        gemini_tap.on_switch_interrupt = lambda: router.switch_passed("gemini_live")
        branches.append([
            EngineGate(selector, "gemini_live", head=True),
            aggregators.user(),
            GeminiCallerTap(room=room, track_language=False, languages=gemini_langs),
            OfficerRequestBridge(),
            service,
            InterruptedReplyMute(),
            gemini_tap,
            hold_gate,
            aggregators.assistant(),
            EngineGate(selector, "gemini_live", head=False),
        ])
        task_ref["gemini"] = (service, context)

    cascaded_brain = None
    if "cascaded" in engines:
        processors, assistant_aggregator, cascaded_brain = build_cascaded_branch(room, speech_model, chat_model)
        router.brain = cascaded_brain
        cascaded_brain.on_switch_interrupt = lambda: router.switch_passed("cascaded")
        branches.append([
            EngineGate(selector, "cascaded", head=True),
            *processors,
            assistant_aggregator,
            EngineGate(selector, "cascaded", head=False),
        ])

    sentinel = LanguageSentinel(room, speech_model, router, languages)
    router.interrupt = sentinel.interrupt_for_switch
    router.barge_in = sentinel.interrupt_for_barge_in

    transport = build_transport(room, websocket)
    pipeline = Pipeline([
        transport.input(),
        CallerAudioTap(room=room),
        sentinel,
        ParallelPipeline(*branches),
        outlet,
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

    if "gemini" in task_ref:
        service, context = task_ref["gemini"]
        gemini_brain = GeminiLiveReceptionistBrain(
            room=room, service=service, chat_model=chat_model, task=task, context=context
        )
    logger.info(
        "Multilingual call %s: languages=%s engines=%s opening=%s",
        room.call_id, languages, engine_by_language, opening_engine,
    )
    return task, transport, MultilingualGreeter(opening_engine, gemini_brain, cascaded_brain, task)

"""The language sentinel: hears every caller utterance and votes on its language.

It sits on the shared input path, after ``CallerAudioTap`` and before the
engine switch, and passes every frame straight through (direct mode) — it adds
nothing to the audio path. What it does happens off to the side:

1. Caller audio is copied into its *own* Silero VAD, run as a bare analyzer.
   Nothing it detects is pushed downstream: VAD frames reaching the Gemini
   branch's aggregator would read as the caller interrupting.
2. When an utterance starts and the call's language is not yet locked, the
   router is told (``on_speech_started``), which holds Gemini's reply back.
3. When it ends, :meth:`app.speech_service.SpeechModel.identify_language`
   scores it (one encoder pass), and a short or unsure utterance is also
   decoded as text in the language it most likely is — explicit requests
   ("Tuyinza okwogera Oluganda?") and code-switching are read from the text.
   The resulting :class:`LanguageVote` goes to the router.

It also turns the caller's on-screen language choice (``SetLanguageFrame``)
into a router override, and consumes that frame. It stops listening while the
call is with an officer.

Its VAD is also the call's second ear for barge-in: when the caller keeps
talking over the assistant (``RECEPTIONIST_BARGE_IN_MIN_S`` past the onset),
the router is told (``on_barge_in``), which stops Gemini if its own VAD has
not.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ..speech_service import pcm16_to_wav
from .config import get_barge_in_min_s, get_lid_method, get_turn_timeout_s, local_barge_in_enabled
from .language import LanguageVote, fuse
from .serializer import SetLanguageFrame

logger = logging.getLogger(__name__)

try:
    from pipecat.frames.frames import (
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        CancelFrame,
        EndFrame,
        Frame,
        InputAudioRawFrame,
        InterruptionFrame,
        StartFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
except ImportError:  # pragma: no cover — only built inside a Pipecat pipeline
    FrameProcessor = object  # type: ignore[assignment,misc]

_BYTES_PER_SECOND = 16000 * 2

#: Utterances up to this long are always decoded as text too — explicit
#: language requests are short. Longer ones only when the acoustic vote is unsure.
TEXT_DECODE_MAX_S = 5.0
#: Whisper's receptive field is 30 s; there is nothing to gain beyond ~20 s.
MAX_UTTERANCE_S = 20.0


class LanguageSentinel(FrameProcessor):  # type: ignore[misc,valid-type]
    """See the module docstring."""

    def __init__(
        self,
        room: Any,
        speech_model: Any,
        router: Any,
        languages: tuple[str, ...],
        vad_analyzer: Any = None,
        preroll_s: float = 0.3,
        **kwargs: Any,
    ) -> None:
        super().__init__(enable_direct_mode=True, **kwargs)
        self.room = room
        self.speech_model = speech_model
        self.router = router
        self.languages = languages
        self.method = get_lid_method()
        if vad_analyzer is None:
            from pipecat.audio.vad.silero import SileroVADAnalyzer
            from pipecat.audio.vad.vad_analyzer import VADParams

            vad_analyzer = SileroVADAnalyzer(params=VADParams(stop_secs=0.4, start_secs=0.2))
        self._vad = vad_analyzer
        self._preroll_bytes = int(preroll_s * _BYTES_PER_SECOND)
        self._max_bytes = int(MAX_UTTERANCE_S * _BYTES_PER_SECOND)
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=500)
        self._worker: asyncio.Task[None] | None = None
        self._vote_lock = asyncio.Lock()
        self._vote_tasks: set[asyncio.Task[None]] = set()
        # The caller's *turn*: every segment until they have been quiet for
        # the cascaded engine's own end-of-turn silence. Votes are per segment
        # (fast); a switch re-asks the whole turn — "VAT yange ntya
        # okugisasula, [pause] era ebitundu bimeka?" is one question.
        self._turn_timeout_s = get_turn_timeout_s()
        self._turn_pcm = bytearray()
        self._turn_segments = 0
        self._turn_timer: asyncio.Task[None] | None = None
        self._turn_closing = False
        # Barge-in: the assistant is audible (the output transport says so),
        # and how much speech past the onset counts as talking over it.
        self._bot_speaking = False
        self._barge_in_bytes = (
            int(get_barge_in_min_s() * _BYTES_PER_SECOND) if local_barge_in_enabled() else None
        )

    # -- frame path ------------------------------------------------------

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, SetLanguageFrame):
            await self.router.on_override(frame.language)
            return

        await self.push_frame(frame, direction)

        if isinstance(frame, StartFrame):
            self._start()
        elif isinstance(frame, (EndFrame, CancelFrame)):
            await self._stop()
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
        elif (
            isinstance(frame, InputAudioRawFrame)
            and direction == FrameDirection.DOWNSTREAM
            and self._listening()
        ):
            try:
                self._queue.put_nowait(frame.audio)
            except asyncio.QueueFull:
                logger.debug("Language sentinel falling behind; dropping a chunk")

    def _listening(self) -> bool:
        return self.room.state.mode == "ai"

    async def interrupt_for_switch(self) -> None:
        """Interrupt whatever is playing, marked as a language switch.

        A plain barge-in would be counted as the caller interrupting; this one
        carries ``metadata["language_switch"]`` so it is not.
        """
        await self._interrupt("language_switch")

    async def interrupt_for_barge_in(self) -> None:
        """Interrupt the assistant because the caller is talking over it.

        Marked ``metadata["local_barge_in"]``: Gemini did not stop by itself,
        so the rest of the reply it is still streaming must be dropped
        (``InterruptedReplyMute``).
        """
        await self._interrupt("local_barge_in")

    async def _interrupt(self, reason: str) -> None:
        down, up = InterruptionFrame(), InterruptionFrame()
        for frame in (down, up):
            frame.metadata[reason] = True
        down.broadcast_sibling_id, up.broadcast_sibling_id = up.id, down.id
        await self.push_frame(down, FrameDirection.DOWNSTREAM)
        await self.push_frame(up, FrameDirection.UPSTREAM)

    # -- segmentation ----------------------------------------------------

    def _start(self) -> None:
        if self._worker is None:
            self._vad.set_sample_rate(16000)
            self._worker = asyncio.create_task(self._segment())

    async def _stop(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
        for task in list(self._vote_tasks):
            task.cancel()
        if self._turn_timer is not None:
            self._turn_timer.cancel()

    def _turn_resumed(self) -> None:
        """Speech again before the turn timed out: it is the same turn."""
        timer = self._turn_timer
        if timer is not None and not timer.done() and not self._turn_closing:
            timer.cancel()
            self._turn_timer = None

    async def _end_turn_after(self, delay_s: float) -> None:
        await asyncio.sleep(delay_s)
        self._turn_closing = True
        try:
            # Every vote of this turn must have reached the policy first.
            pending = [t for t in self._vote_tasks if not t.done()]
            if pending:
                await asyncio.wait(pending)
            pcm, segments = bytes(self._turn_pcm), self._turn_segments
            self._turn_pcm.clear()
            self._turn_segments = 0
            await self.router.on_turn_end(pcm, segments)
        finally:
            self._turn_closing = False
            if self._turn_timer is asyncio.current_task():
                self._turn_timer = None

    async def _segment(self) -> None:
        from pipecat.audio.vad.vad_analyzer import VADState

        speaking = False
        preroll = bytearray()
        utterance = bytearray()
        heard = 0  # bytes of speech since the onset
        barged = False
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                return
            try:
                state = await self._vad.analyze_audio(chunk)
            except Exception:
                logger.debug("Sentinel VAD failed on a chunk", exc_info=True)
                continue

            if speaking:
                if len(utterance) < self._max_bytes:
                    utterance.extend(chunk)
                heard += len(chunk)
                if (
                    not barged
                    and self._bot_speaking
                    and self._barge_in_bytes is not None
                    and heard >= self._barge_in_bytes
                ):
                    barged = True
                    await self.router.on_barge_in()
            else:
                preroll.extend(chunk)
                if len(preroll) > self._preroll_bytes:
                    del preroll[: len(preroll) - self._preroll_bytes]

            if not speaking and state == VADState.SPEAKING:
                speaking = True
                utterance = bytearray(preroll)
                preroll.clear()
                heard, barged = 0, False
                self._turn_resumed()
                await self.router.on_speech_started()
            elif speaking and state == VADState.QUIET:
                speaking = False
                pcm = bytes(utterance)
                utterance = bytearray()
                task = asyncio.create_task(self._vote(pcm, time.monotonic()))
                self._vote_tasks.add(task)
                task.add_done_callback(self._vote_tasks.discard)
                if len(self._turn_pcm) < self._max_bytes:
                    self._turn_pcm.extend(pcm)
                self._turn_segments += 1
                if self._turn_timer is None or self._turn_timer.done():
                    self._turn_timer = asyncio.create_task(self._end_turn_after(self._turn_timeout_s))

    # -- voting ----------------------------------------------------------

    async def _vote(self, pcm: bytes, ended_at: float) -> None:
        # One vote at a time, in utterance order: the policy is a state machine.
        async with self._vote_lock:
            try:
                vote, decoded = await self._build_vote(pcm)
            except Exception:
                logger.exception("Language vote failed")
                vote, decoded = LanguageVote({}, "", len(pcm) / _BYTES_PER_SECOND), None
            latency_ms = round((time.monotonic() - ended_at) * 1000, 1)
            vote = LanguageVote(vote.probs, vote.top, vote.speech_s, vote.text, latency_ms)
            await self.router.on_vote(vote, pcm, decoded)

    async def _build_vote(self, pcm: bytes) -> tuple[LanguageVote, tuple[str, str] | None]:
        speech_s = round(len(pcm) / _BYTES_PER_SECOND, 3)
        probs: dict[str, float] = {}
        top = ""
        if self.method != "keyword":
            res = await asyncio.to_thread(self.speech_model.identify_language, pcm, 16000, self.languages)
            if res.error:
                logger.debug("No language vote for a %.1fs utterance: %s", speech_s, res.error)
            probs, top = res.probs, res.top

        confidence = probs.get(top, 0.0)
        switch_confidence = self.router.policy.config.switch_confidence
        decoded: tuple[str, str] | None = None
        if speech_s <= TEXT_DECODE_MAX_S or confidence < switch_confidence:
            lang = top or self.room.state.locale or "en"
            result = await asyncio.to_thread(
                self.speech_model.transcribe, pcm16_to_wav(pcm, 16000), 16000, lang, False
            )
            text = (getattr(result, "text", "") or "").strip()
            if text:
                decoded = (text, lang)

        vote = LanguageVote(probs, top, speech_s, decoded[0] if decoded else "")
        if self.method == "fusion":
            vote = fuse(vote)
        return vote, decoded

    async def cleanup(self) -> None:
        await self._stop()
        await super().cleanup()

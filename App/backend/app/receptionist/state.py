"""In-memory call state, call room, and active call registry."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .clarify import ClarifyState
from .store import update_call

logger = logging.getLogger(__name__)


@dataclass
class CallState:
    """Dynamic in-memory state of an ongoing call."""

    call_id: str
    conversation_id: str
    user_id: str = ""
    tenant_id: str = "default"
    locale: str = "en"
    mode: str = "ai"  # "ai" | "transferring" | "bridged" | "ended"
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    end_reason: str = ""
    turn_seq: int = 0
    turn_words: list[Any] = field(default_factory=list)
    last_caller_text: str = ""
    clarify: ClarifyState | None = None
    ticket_id: str | None = None
    transfer_reason: str | None = None
    transfer_requested_at: float | None = None
    topic: str = ""
    priority: str = "normal"
    # Times this call has been put in the officers' queue: the AI's transfer
    # is the first; an officer passing it on (Call Desk, Phase 2) adds one.
    transfer_attempts: int = 0
    generation_id: int = 0
    barge_in_count: int = 0
    caller_turns_count: int = 0
    # "Are you still there?" checks since the caller last said anything.
    idle_prompts: int = 0
    ai_answers_count: int = 0
    clarifications_asked: int = 0
    clarified_first_try: int = 0
    clarification_failures: int = 0
    low_conf_words_count: int = 0
    word_probs: list[float] = field(default_factory=list)
    faithfulness_scores: list[float] = field(default_factory=list)
    latencies: list[dict[str, float]] = field(default_factory=list)
    officer_name: str | None = None
    officer_id: str | None = None
    # Call Desk: the officer holding the call (claimed, not yet connected or
    # connected), and when their audio joined.
    claimed_by: str = ""
    claimed_name: str = ""  # "Officer Nakato" — from the claimant's token, not their id
    claimed_at: float | None = None
    bridged_at: float | None = None
    on_hold: bool = False
    hold_started_at: float | None = None
    hold_total_s: float = 0.0
    target_team: str = ""
    reconnecting_officer: str | None = None
    livekit_room: str = ""
    livekit_caller_identity: str = ""
    livekit_officer_identity: str = ""
    livekit_agent_identity: str = ""
    livekit_observer_identities: set[str] = field(default_factory=set)
    livekit_officer_route_active: bool = False
    reconnect_timer: asyncio.Task | None = None
    transfer_timer_task: asyncio.Task | None = None
    # Multilingual calls (receptionist_language_detection). `locale` above is
    # the language the call is in *now*; these record how it got there.
    initial_locale: str = "en"
    preferred_locale: str = ""  # what the chat was set to; a hint, never the call's language
    language_source: str = "default"  # "default" | "auto" | "explicit" | "override"
    languages_used: list[str] = field(default_factory=list)
    language_switches: int = 0
    language_overrides: int = 0
    lid_latencies_ms: list[float] = field(default_factory=list)
    lid_confidences: list[float] = field(default_factory=list)
    held_ms: list[float] = field(default_factory=list)


class CallRoom:
    """Manages the in-process state, sockets, and worker for one call."""

    def __init__(
        self,
        call_id: str,
        state: CallState,
        caller_ws: Any = None,
    ) -> None:
        self.call_id = call_id
        self.state = state
        self.caller_ws = caller_ws
        self.officer: Any | None = None
        self.transport: Any | None = None
        self.pipeline_task: Any | None = None
        self.pipeline_runner_task: asyncio.Task[Any] | None = None
        self.caller_reconnect_task: asyncio.Task[Any] | None = None
        self.participant_joined: dict[str, asyncio.Event] = {}
        self.worker: Any | None = None
        self.lock = asyncio.Lock()
        # Every officer action on this call (claim, release, join, end) runs
        # under this lock; the claim itself is also atomic in the database.
        self.desk_lock = asyncio.Lock()
        self.claim_timer: asyncio.Task[None] | None = None
        self.end_event = asyncio.Event()
        self.listeners: list[asyncio.Queue[bytes]] = []

    def add_listener(self, queue: asyncio.Queue[bytes]) -> None:
        self.listeners.append(queue)

    def remove_listener(self, queue: asyncio.Queue[bytes]) -> None:
        self.listeners = [q for q in self.listeners if q is not queue]

    def broadcast_audio(self, pcm: bytes) -> None:
        for q in list(self.listeners):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(pcm)
            except asyncio.QueueFull:
                pass

    def mark_participant_joined(self, identity: str) -> None:
        """Record that *identity* joined the call's LiveKit room; wakes waiters."""
        self.participant_joined.setdefault(identity, asyncio.Event()).set()

    def mark_participant_left(self, identity: str) -> None:
        """Forget *identity*; a later wait blocks until it joins again."""
        self.participant_joined.pop(identity, None)

    async def wait_for_participant(self, identity: str, timeout_s: float = 20.0) -> bool:
        """Wait until *identity* is in the room; ``False`` after *timeout_s*."""
        event = self.participant_joined.setdefault(identity, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout_s)
            return True
        except asyncio.TimeoutError:
            return False

    async def end(self, reason: str = "normal") -> None:
        """End the call room and trigger teardown."""
        async with self.lock:
            if self.state.mode == "ended":
                return
            self.state.mode = "ended"
            self.state.ended_at = time.time()
            self.state.end_reason = reason
            self.end_event.set()

            if self.state.transfer_timer_task and not self.state.transfer_timer_task.done():
                self.state.transfer_timer_task.cancel()
            if self.caller_reconnect_task and not self.caller_reconnect_task.done():
                self.caller_reconnect_task.cancel()

        # An ended call must not keep its Pipecat pipeline running until the caller's control loop notices. PipelineTask.cancel()
        # only queues a CancelFrame, so it is safe from inside the pipeline too.
        runner = self.pipeline_runner_task
        if runner is not None and not runner.done() and self.pipeline_task is not None:
            try:
                await self.pipeline_task.cancel()
            except Exception:
                logger.debug("Could not cancel the pipeline for call %s", self.call_id, exc_info=True)

        # Update persistence
        try:
            update_call(
                self.call_id,
                status="ended",
                ended_at=self.state.ended_at,
                end_reason=reason,
            )
        except Exception:
            logger.debug("Failed to record call end in DB for %s", self.call_id, exc_info=True)

        # Notify officer leg if present
        if self.officer is not None:
            try:
                await self.officer.close(reason=reason)
            except Exception:
                pass

        if self.state.livekit_room:
            try:
                from . import livekit

                await livekit.close_room(self.state.livekit_room)
            except Exception:
                logger.warning(
                    "Failed to close LiveKit room for call %s",
                    self.call_id,
                    exc_info=True,
                )


class CallRegistry:
    """In-memory registry of active call rooms for single-worker deployment."""

    def __init__(self) -> None:
        self._rooms: dict[str, CallRoom] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        call_id: str,
        conversation_id: str,
        user_id: str = "",
        tenant_id: str = "default",
        locale: str = "en",
        caller_ws: Any = None,
    ) -> CallRoom:
        state = CallState(
            call_id=call_id,
            conversation_id=conversation_id,
            user_id=user_id,
            tenant_id=tenant_id,
            locale=locale,
            mode="ai",
            started_at=time.time(),
            initial_locale=locale,
            languages_used=[locale],
        )
        room = CallRoom(call_id=call_id, state=state, caller_ws=caller_ws)
        async with self._lock:
            self._rooms[call_id] = room
        return room

    def get(self, call_id: str) -> CallRoom | None:
        return self._rooms.get(call_id)

    async def remove(self, call_id: str) -> CallRoom | None:
        async with self._lock:
            return self._rooms.pop(call_id, None)

    def list_live(self) -> list[CallRoom]:
        return [r for r in self._rooms.values() if r.state.mode != "ended"]


# Global registry singleton
registry = CallRegistry()

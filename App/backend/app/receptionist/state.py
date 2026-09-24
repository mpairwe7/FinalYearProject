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
    generation_id: int = 0
    barge_in_count: int = 0
    caller_turns_count: int = 0
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
    transfer_timer_task: asyncio.Task | None = None
    # Multilingual calls (receptionist_language_detection). `locale` above is
    # the language the call is in *now*; these record how it got there.
    engine: str = ""  # "gemini_live" | "cascaded"; "" on a single-engine call
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
        self.worker: Any | None = None
        self.lock = asyncio.Lock()
        self.end_event = asyncio.Event()

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

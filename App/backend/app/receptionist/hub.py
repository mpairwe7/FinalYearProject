"""In-process pub/sub event hub for staff call lobby and per-call live transcripts."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

QUEUE_MAXSIZE = 100


class CallEventHub:
    """Fan events out to staff lobby and per-call live listeners."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._lobby: list[tuple[asyncio.Queue[dict[str, Any]], asyncio.AbstractEventLoop]] = []
        self._call_channels: dict[
            str, list[tuple[asyncio.Queue[dict[str, Any]], asyncio.AbstractEventLoop]]
        ] = {}

    # ------------------------------------------------------------------
    # Lobby (metadata only)
    # ------------------------------------------------------------------

    def subscribe_lobby(self, loop: asyncio.AbstractEventLoop) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        with self._lock:
            self._lobby.append((queue, loop))
        return queue

    def unsubscribe_lobby(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._lobby = [(q, loop) for q, loop in self._lobby if q is not queue]

    def publish_lobby(self, event_type: str, payload: dict[str, Any]) -> None:
        """Publish a metadata-only event to all lobby subscribers. No transcripts."""
        event = {
            "type": event_type,
            "data": payload,
            "ts": time.time(),
        }
        with self._lock:
            targets = list(self._lobby)

        for queue, loop in targets:
            self._safe_enqueue(queue, loop, event)

    # ------------------------------------------------------------------
    # Per-call Live (transcripts, captions, status deltas)
    # ------------------------------------------------------------------

    def subscribe_call(
        self, call_id: str, loop: asyncio.AbstractEventLoop
    ) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        with self._lock:
            if call_id not in self._call_channels:
                self._call_channels[call_id] = []
            self._call_channels[call_id].append((queue, loop))
        return queue

    def unsubscribe_call(self, call_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            if call_id in self._call_channels:
                self._call_channels[call_id] = [
                    (q, loop) for q, loop in self._call_channels[call_id] if q is not queue
                ]
                if not self._call_channels[call_id]:
                    del self._call_channels[call_id]

    def publish_call(self, call_id: str, event_type: str, payload: dict[str, Any]) -> None:
        """Publish a call-specific event (turn, caption, status) to listeners."""
        event = {
            "type": event_type,
            "call_id": call_id,
            "data": payload,
            "ts": time.time(),
        }
        with self._lock:
            targets = list(self._call_channels.get(call_id, []))

        for queue, loop in targets:
            self._safe_enqueue(queue, loop, event)

    # ------------------------------------------------------------------
    # Enqueue helper
    # ------------------------------------------------------------------

    def _safe_enqueue(
        self,
        queue: asyncio.Queue[dict[str, Any]],
        loop: asyncio.AbstractEventLoop,
        event: dict[str, Any],
    ) -> None:
        def _put() -> None:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

        if loop.is_running():
            loop.call_soon_threadsafe(_put)


# Global hub singleton
hub = CallEventHub()

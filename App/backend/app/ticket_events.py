"""Push escalations to staff as they arrive.

The queue polls every 20 seconds. That is fine for a quiet day and wrong
for the case the whole pipeline exists to serve: a taxpayer in hardship
whose ticket sits unseen because nobody happened to refresh.

Two things shape this module.

**It has to work across replicas.** A WebSocket lives on one pod. A
ticket is created on whichever pod served the taxpayer. An in-process
hub alone would show an officer only the tickets that happened to land
on the same pod — the exact per-replica failure this codebase has spent
several changes removing. So each pod publishes to a shared channel and
fans out to its own sockets. Postgres ``LISTEN``/``NOTIFY`` carries it
when the Postgres backend is active; on SQLite (single-node by
definition) the in-process hub is the whole story and is correct.

**It carries no transcript.** Same rule as the webhook: the event says a
ticket exists and how urgent it is, and the officer fetches the
conversation through the authenticated admin API. A broadcast fans out
to every connected staff socket, which is the wrong shape for a
taxpayer's tax affairs.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
from collections.abc import Iterator
from typing import Any

logger = logging.getLogger(__name__)

#: Postgres channel name for cross-replica fan-in.
PG_CHANNEL = "ura_ticket_events"

#: Bound per socket. A slow client must not grow memory without limit;
#: dropping the oldest is right here because the queue view is the
#: source of truth and a reconnect re-reads it.
QUEUE_MAXSIZE = 100


class TicketEventHub:
    """Fan events out to the staff sockets connected to *this* process.

    Subscribers are ``asyncio.Queue``s. ``publish`` is safe to call from
    any thread — ticket creation happens on the request path, which may
    not be the loop thread — so it hops onto each subscriber's loop.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[tuple[asyncio.Queue[dict[str, Any]], asyncio.AbstractEventLoop]] = []

    def subscribe(self, loop: asyncio.AbstractEventLoop) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        with self._lock:
            self._subscribers.append((queue, loop))
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subscribers = [(q, loop) for q, loop in self._subscribers if q is not queue]

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def publish_local(self, event: dict[str, Any]) -> int:
        """Deliver to this process's subscribers. Returns how many got it."""
        with self._lock:
            targets = list(self._subscribers)
        delivered = 0
        for queue, loop in targets:
            try:
                loop.call_soon_threadsafe(_offer, queue, event)
                delivered += 1
            except RuntimeError:
                # Loop already closed — the socket is going away and its
                # unsubscribe is imminent.
                continue
        return delivered


def _offer(queue: asyncio.Queue[dict[str, Any]], event: dict[str, Any]) -> None:
    """Enqueue, dropping the oldest rather than blocking the publisher."""
    if queue.full():
        with contextlib.suppress(asyncio.QueueEmpty):
            queue.get_nowait()
    with contextlib.suppress(asyncio.QueueFull):
        queue.put_nowait(event)


hub = TicketEventHub()


def _pg_pool() -> Any | None:
    """The Postgres pool when that backend is active, else ``None``."""
    from . import database as db

    if db.ANALYTICS_BACKEND != "postgres":
        return None
    try:
        from . import postgres as pg
    except Exception:  # pragma: no cover - import guarded at dispatch
        return None
    return pg._get_pool()


def publish(event: dict[str, Any]) -> None:
    """Announce an event to every staff socket on every replica.

    Never raises: a broadcast is best-effort by construction. The ticket
    is already committed and the queue view still shows it, so a failure
    here costs a notification, not an escalation.
    """
    try:
        hub.publish_local(event)
    except Exception:
        logger.warning("local ticket event fan-out failed", exc_info=True)

    try:
        pool = _pg_pool()
    except Exception:
        logger.warning("could not reach the cross-replica channel", exc_info=True)
        return
    if pool is None:
        return  # SQLite is single-node; the local hub is the whole story.
    try:
        payload = json.dumps(event)
        if len(payload) > 7000:  # NOTIFY payloads are capped at 8000 bytes
            payload = json.dumps({k: event[k] for k in ("event", "id", "priority") if k in event})
        with pool.connection() as conn:
            with conn.cursor() as cur:
                # pg_notify(), not NOTIFY: the latter is a utility
                # statement and takes no bind parameters, so the payload
                # would have to be interpolated into the SQL.
                cur.execute("SELECT pg_notify(%s, %s)", (PG_CHANNEL, payload))
            conn.commit()
    except Exception:
        logger.warning("cross-replica ticket NOTIFY failed", exc_info=True)


def listen_for_events(stop: threading.Event) -> Iterator[dict[str, Any]]:
    """Yield events published by *other* replicas.

    Blocking generator meant for a background thread. Yields nothing at
    all on SQLite, where there are no other replicas to hear from.
    """
    pool = _pg_pool()
    if pool is None:
        return
    try:
        with pool.connection() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(f"LISTEN {PG_CHANNEL}")
            while not stop.is_set():
                for notify in conn.notifies(timeout=1.0, stop_after=1):
                    try:
                        yield json.loads(notify.payload)
                    except (TypeError, ValueError):
                        logger.debug("unparseable ticket event payload")
                if stop.is_set():
                    break
    except Exception:
        logger.warning("ticket event listener stopped", exc_info=True)


def build_event(ticket: dict[str, Any], event_type: str = "escalation.created") -> dict[str, Any]:
    """Triage metadata for the staff UI — never the conversation.

    Same allowlist discipline as the webhook payload: a field added to
    the ticket later has to be opted in, and the transcript is fetched
    through the authenticated admin API rather than broadcast to every
    connected socket.
    """
    handoff = ticket.get("handoff") or {}
    event = {
        "event": event_type,
        "id": ticket.get("id", ""),
        "priority": ticket.get("priority", "normal"),
        "status": ticket.get("status", "open"),
        "team": ticket.get("team", ""),
        "reason": ticket.get("reason", ""),
        "created_at": ticket.get("created_at", 0),
    }
    for key in ("topic", "sentiment", "transfer_style"):
        if handoff.get(key) is not None:
            event[key] = handoff[key]
    return event

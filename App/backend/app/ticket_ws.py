"""Staff WebSocket for live escalation events.

Read-only and staff-only. The socket announces that a ticket exists and
how urgent it is; everything else is fetched through the authenticated
admin API, so a broadcast never carries a taxpayer's conversation.

Frames sent to the client::

    {"type": "subscribed", "team": "customs"}   once, on connect
    {"type": "escalation.created", ...}         as tickets arrive
    {"type": "ping"}                            keepalive

The client sends nothing. A socket that only ever receives cannot be
used to reach the ticket store, which is the right shape for something
exposed to a browser.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading

from starlette.websockets import WebSocket, WebSocketDisconnect

from .ticket_events import hub, listen_for_events

logger = logging.getLogger(__name__)

#: Roles allowed to watch the queue.
STAFF_ROLES = frozenset({"ura_staff", "ura_admin", "ura_auditor"})

#: Sent when nothing has happened, so proxies do not reap an idle socket.
KEEPALIVE_S = 25.0

_listener_started = threading.Event()
_listener_stop = threading.Event()


def _start_cross_replica_listener() -> None:
    """Bridge other replicas' events into this process's hub, once.

    A WebSocket lives on one pod while tickets are created on whichever
    pod served the taxpayer. Without this an officer sees only the
    tickets that happened to land on the same pod.
    """
    if _listener_started.is_set():
        return
    _listener_started.set()

    def _run() -> None:
        for event in listen_for_events(_listener_stop):
            hub.publish_local(event)

    threading.Thread(target=_run, name="ticket-event-listener", daemon=True).start()


async def ticket_stream_ws(websocket: WebSocket) -> None:
    """Serve one staff subscriber."""
    from .chat_ws_v2 import _resolve_ws_principal
    from .auth import JWTAuthError

    try:
        _user_id, _tenant_id, role, _purposes = _resolve_ws_principal(websocket, required=True)
    except JWTAuthError:
        await websocket.close(code=4401, reason="authentication required")
        return
    except Exception:
        logger.warning("ticket stream auth failed", exc_info=True)
        await websocket.close(code=4401, reason="authentication required")
        return

    if role not in STAFF_ROLES:
        # Deny before accepting: an unauthorised client should not get a
        # socket it can hold open.
        await websocket.close(code=4403, reason="staff access required")
        return

    team = websocket.query_params.get("team", "").strip()

    await websocket.accept()
    _start_cross_replica_listener()
    loop = asyncio.get_running_loop()
    queue = hub.subscribe(loop)

    try:
        await websocket.send_json({"type": "subscribed", "team": team or "all"})
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_S)
            except TimeoutError:
                await websocket.send_json({"type": "ping"})
                continue
            # An officer watching one team should not be interrupted by
            # another's queue.
            if team and str(event.get("team", "")) != team:
                continue
            await websocket.send_json({"type": event.get("event", "escalation.created"), **event})
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.info("ticket stream closed", exc_info=True)
    finally:
        hub.unsubscribe(queue)
        with contextlib.suppress(Exception):
            await websocket.close()

"""Which role this API process has, and which request paths are call paths.

G306: a live call lives in one process's memory (its room, its transfer
timers, the officers' sockets), so the Space runs a ``calls`` process that owns
every call path and a ``chat`` process that owns everything else. nginx routes
by path; this module is the shared vocabulary the middleware, ``/ready`` and the
container config agree on. ``URA_PROCESS_ROLE`` unset keeps today's behaviour:
one process, every route.
"""

from __future__ import annotations

import json
import logging
import os
import re

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .analytics import metrics

logger = logging.getLogger(__name__)

ROLE_ENV = "URA_PROCESS_ROLE"
#: Names the process that answered, on every response while a role is set.
PROCESS_HEADER = "X-URA-Process"
CALLS_URL_ENV = "URA_CALLS_PROCESS_URL"

#: The roles a process may have. Anything else is a typo and counts as unset.
ROLES = frozenset({"calls", "chat"})

DEFAULT_CALLS_PROCESS_URL = "http://127.0.0.1:8082"

#: Call paths as the app sees them (nginx has stripped ``/api``): the taxpayer
#: call socket, the staff console under ``/admin/calls`` and officer presence.
#: Not ``/calls``: that is the frontend's page. Not ``/v1/calls``: no such route.
CALL_PATH_RE: re.Pattern[str] = re.compile(
    r"^/(v1/)?(calls/stream|admin/calls(/.*)?|admin/officers/(me/)?presence)$"
)

#: The same paths as nginx sees them, ``/api`` prefix included. nginx.conf
#: carries this exact text in its call location; a test keeps them equal.
NGINX_CALL_LOCATION = r"^/(api/(v1/)?|v1/)(calls/stream|admin/calls(/.*)?|admin/officers/(me/)?presence)$"

# Each distinct bad value is reported once: the role is read on every request.
_LOGGED_INVALID_ROLES: set[str] = set()


def process_role() -> str:
    """``"calls"``, ``"chat"`` or ``""`` (unset), read from the environment each time.

    A value that is set but is neither role is logged as an error (once per
    value) and treated as unset, so a typo cannot silently refuse every call.
    """
    raw = os.getenv(ROLE_ENV, "")
    role = raw.strip().lower()
    if not role:
        return ""
    if role in ROLES:
        return role
    if raw not in _LOGGED_INVALID_ROLES:
        _LOGGED_INVALID_ROLES.add(raw)
        logger.error(
            "%s=%r is not a process role (expected 'calls' or 'chat'); treating it as unset",
            ROLE_ENV,
            raw,
        )
    return ""


def is_call_path(path: str) -> bool:
    """Whether *path* (``scope["path"]``: no query string) belongs to the call process."""
    return CALL_PATH_RE.fullmatch(path) is not None


def calls_process_url() -> str:
    """Where the chat process reaches the call process, without a trailing slash."""
    raw = os.getenv(CALLS_URL_ENV, "").strip().rstrip("/")
    return raw or DEFAULT_CALLS_PROCESS_URL


#: The ways a call request can reach the chat process: the ASGI scope types,
#: and the ``kind`` label values.
MISROUTED_KINDS: tuple[str, ...] = ("http", "websocket")

#: What a refused call socket is told before it is closed (1013: try again later).
_MISROUTED_FRAME = json.dumps({"type": "error", "code": "unavailable", "recoverable": False})
_MISROUTED_DETAIL = (
    "This request belongs to the call process but reached the chat process; "
    "the proxy's routing for call paths has drifted."
)


def declare_metrics() -> None:
    """Create ``receptionist_misrouted_total`` at zero for both kinds.

    ``increase()`` sees nothing in a series that first appears at 1, so without
    this the first misrouted request after a restart would not trip its alert.
    """
    metrics.reserve("receptionist_misrouted_total", "kind", MISROUTED_KINDS)
    for kind in MISROUTED_KINDS:
        metrics.inc("receptionist_misrouted_total", 0, labels={"kind": kind})


class ProcessRoleMiddleware:
    """Name the process on every response, and refuse call requests the chat process was sent.

    nginx routes call paths to the ``calls`` process; one that reaches the
    ``chat`` process anyway would act on call state that process does not hold
    (an empty lobby, a room nobody joined), so it is refused loudly and counted
    instead. Pure ASGI because a call is a WebSocket, which Starlette's
    ``BaseHTTPMiddleware`` cannot carry. With no role set it does nothing.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        declare_metrics()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]  # "http" or "websocket": also the counter's `kind` label
        role = process_role() if kind in MISROUTED_KINDS else ""  # not for "lifespan"
        if not role:
            await self.app(scope, receive, send)
            return

        misrouted = role == "chat" and is_call_path(scope["path"])
        if misrouted:
            metrics.inc("receptionist_misrouted_total", labels={"kind": kind})
            logger.error(
                "misrouted %s request for %r reached the chat process; "
                "nginx should have sent it to the call process",
                kind,
                scope["path"],
            )

        async def send_labelled(message: Message) -> None:
            if message["type"] in ("http.response.start", "websocket.accept"):
                message.setdefault("headers", [])
                MutableHeaders(scope=message)[PROCESS_HEADER] = role
            await send(message)

        if not misrouted:
            await self.app(scope, receive, send_labelled)
        elif kind == "http":
            response = JSONResponse({"detail": _MISROUTED_DETAIL}, status_code=421)
            await response(scope, receive, send_labelled)
        else:
            await self._refuse_socket(receive, send_labelled)

    @staticmethod
    async def _refuse_socket(receive: Receive, send: Send) -> None:
        """Accept, say why, close 1013: a socket closed before accepting reads as a dropped call."""
        if (await receive())["type"] != "websocket.connect":
            return
        await send({"type": "websocket.accept"})
        await send({"type": "websocket.send", "text": _MISROUTED_FRAME})
        await send({"type": "websocket.close", "code": 1013})

"""Which role this API process has, and which request paths are call paths.

G306: a live call lives in one process's memory (its room, its transfer
timers, the officers' sockets), so the Space runs a ``calls`` process that owns
every call path and a ``chat`` process that owns everything else. nginx routes
by path; this module is the shared vocabulary the middleware, ``/ready`` and the
container config agree on. ``URA_PROCESS_ROLE`` unset keeps today's behaviour:
one process, every route.
"""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

ROLE_ENV = "URA_PROCESS_ROLE"
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

"""The process's optional Redis clients: one place that connects, and connects again.

Seven modules each built their own client and gave up on Redis for the life of
the process after one failed connection (G685): the MCP velocity limit and the
guard strikes fell back to per-worker counts, and the corpus-sync lock stopped
being a lock between workers. Callers keep their in-process fallback and use it
whenever :func:`shared_redis` returns ``None`` or a command raises.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

#: Seconds before another connection attempt after one failed.
RETRY_SECONDS = 30.0

_lock = threading.Lock()
_clients: dict[tuple[bool, float], Any] = {}
_retry_at: dict[tuple[bool, float], float] = {}
_enabled = True


def shared_redis(*, decode_responses: bool = False, timeout: float = 1.0) -> Any | None:
    """The client for ``REDIS_URL``, or ``None`` while it is unset or unreachable.

    One client per (*decode_responses*, *timeout*) pair, shared by every caller
    in the process. A failed connection is tried again after ``RETRY_SECONDS``,
    so a Redis that comes up later is used without a restart.
    """
    url = os.getenv("REDIS_URL", "")
    if not url or not _enabled:
        return None
    key = (decode_responses, timeout)
    client = _clients.get(key)
    if client is not None:
        return client
    with _lock:
        client = _clients.get(key)
        if client is not None:
            return client
        if time.monotonic() < _retry_at.get(key, 0.0):
            return None
        try:
            import redis

            # shortcut: a Redis that accepts and then hangs costs each call one socket
            # timeout (measured: no hidden retries); add a breaker if Redis moves off-host.
            client = redis.from_url(
                url,
                decode_responses=decode_responses,
                socket_connect_timeout=min(timeout, 0.5),
                socket_timeout=timeout,
                health_check_interval=30,  # an idle connection is pinged before reuse
            )
            client.ping()
        except Exception:  # noqa: BLE001 - no Redis means in-process state until it answers
            if key not in _retry_at:
                logger.warning("REDIS_URL is set but unreachable; shared state stays per process until it answers")
            _retry_at[key] = time.monotonic() + RETRY_SECONDS
            return None
        _retry_at.pop(key, None)
        _clients[key] = client
        return client


def reset(*, enabled: bool = True) -> None:
    """Forget every client and failed attempt. ``enabled=False`` keeps Redis off until the next reset (tests)."""
    global _enabled
    with _lock:
        _clients.clear()
        _retry_at.clear()
        _enabled = enabled

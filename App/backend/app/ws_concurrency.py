"""Shared per-user + global WebSocket concurrency caps (P1-9).

Voice (and other) WebSocket sessions spin up per-connection work — ASR, TTS,
LLM, speculative prefetch.  Without a bound, an unauthenticated client can
open an unlimited number of sockets and exhaust CPU/memory.  This module
provides a small, dependency-free, thread-safe slot limiter keyed by a named
``pool`` so different socket types can have independent budgets.

Usage::

    key = user_id or f"anon::{host}"
    if not ws_concurrency.try_acquire("voice", key,
                                      per_user_cap=3, global_cap=64):
        await websocket.close(code=1013)
        return
    try:
        ...
    finally:
        ws_concurrency.release("voice", key)
"""

from __future__ import annotations

import os
import threading

_lock = threading.Lock()
# (pool, user_key) -> active count
_per_user: dict[tuple[str, str], int] = {}
# pool -> active count
_global: dict[str, int] = {}


def int_env(name: str, default: int) -> int:
    """Read a positive int from the environment, falling back to *default*."""
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def try_acquire(
    pool: str,
    user_key: str,
    *,
    per_user_cap: int,
    global_cap: int,
) -> bool:
    """Atomically reserve a slot for *user_key* in *pool*.

    Returns False (reserving nothing) when either the global cap for the pool
    or the per-user cap is already reached.
    """
    with _lock:
        if _global.get(pool, 0) >= global_cap:
            return False
        if _per_user.get((pool, user_key), 0) >= per_user_cap:
            return False
        _global[pool] = _global.get(pool, 0) + 1
        _per_user[(pool, user_key)] = _per_user.get((pool, user_key), 0) + 1
        return True


def release(pool: str, user_key: str) -> None:
    """Release a slot previously taken by :func:`try_acquire`."""
    with _lock:
        g = _global.get(pool, 0)
        if g <= 1:
            _global.pop(pool, None)
        else:
            _global[pool] = g - 1
        key = (pool, user_key)
        u = _per_user.get(key, 0)
        if u <= 1:
            _per_user.pop(key, None)
        else:
            _per_user[key] = u - 1


def active(pool: str) -> int:
    """Return the current number of active slots in *pool* (for tests/metrics)."""
    with _lock:
        return _global.get(pool, 0)


def reset() -> None:
    """Testing hook — clear all counters."""
    with _lock:
        _per_user.clear()
        _global.clear()

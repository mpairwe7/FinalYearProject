"""Working memory — 30-minute TTL, in-process dict (Redis-ready).

Phase 16 Lite: process-local dict with monotonic expiry.  Upgrade
path for multi-replica deploys is a Redis hash keyed on
``working:{user_id}`` with a TTL; the public API stays the same
so the swap is one-line.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


WORKING_TTL_SECONDS = 30 * 60


@dataclass
class _Entry:
    payload: dict[str, Any]
    expires_at: float


class WorkingMemory:
    """Thread-safe short-term memory with TTL expiry."""

    def __init__(self, ttl_seconds: int = WORKING_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def set(self, user_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._store[user_id] = _Entry(
                payload=dict(payload),
                expires_at=time.time() + self._ttl,
            )

    def get(self, user_id: str) -> dict[str, Any] | None:
        now = time.time()
        with self._lock:
            entry = self._store.get(user_id)
            if entry is None:
                return None
            if entry.expires_at < now:
                self._store.pop(user_id, None)
                return None
            return dict(entry.payload)

    def update(self, user_id: str, **fields: Any) -> None:
        """Merge fields into the existing entry (or create)."""
        with self._lock:
            entry = self._store.get(user_id)
            if entry is None:
                self._store[user_id] = _Entry(
                    payload=dict(fields),
                    expires_at=time.time() + self._ttl,
                )
            else:
                entry.payload.update(fields)
                entry.expires_at = time.time() + self._ttl

    def clear(self, user_id: str) -> None:
        with self._lock:
            self._store.pop(user_id, None)

    def clear_all(self) -> None:
        with self._lock:
            self._store.clear()

    def size(self) -> int:
        with self._lock:
            now = time.time()
            # Lazy eviction
            expired = [k for k, v in self._store.items() if v.expires_at < now]
            for k in expired:
                self._store.pop(k, None)
            return len(self._store)

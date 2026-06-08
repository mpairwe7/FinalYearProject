"""Free-tier budget guards: Workers AI daily neurons + Gemini RPM token bucket.

Backed by the app's Redis when present (shared across workers/restarts), with an
in-process fallback otherwise. A ``False`` return means "over budget" → the
caller treats it exactly like a tripped breaker and silently falls to the next
existing tier (keyword retrieval, Sunbird, etc.). AI Gateway caching is a second
multiplier: identical requests are served from the gateway cache and burn no
neurons.
"""

from __future__ import annotations

import logging
import os
import threading
import time

from .config import get_cloud_settings

logger = logging.getLogger("ura.providers.budget")

_REDIS_URL = os.getenv("REDIS_URL", "")
_redis = None
_redis_tried = False
_lock = threading.Lock()
_local_neurons: dict[str, int] = {}
_local_gemini: list[float] = []


def _get_redis():
    global _redis, _redis_tried
    if _redis_tried:
        return _redis
    _redis_tried = True
    if not _REDIS_URL:
        return None
    try:
        import redis  # noqa: PLC0415 — optional, lazy

        client = redis.from_url(_REDIS_URL, socket_timeout=2)
        client.ping()
        _redis = client
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.info("budget: Redis unavailable (%s); using in-process counters", exc)
        _redis = None
    return _redis


def try_consume_neurons(estimate: int = 1, *, now: float | None = None) -> bool:
    """Reserve *estimate* Workers AI neurons against the daily budget."""
    now = time.time() if now is None else now
    budget = get_cloud_settings().cf_neuron_daily_budget
    if budget <= 0:
        return True
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    client = _get_redis()
    if client is not None:
        try:
            key = f"cf:neurons:{day}"
            total = client.incrby(key, estimate)
            if total == estimate:
                client.expire(key, 172800)
            return total <= budget
        except Exception:  # pragma: no cover - fall through to local
            pass
    with _lock:
        used = _local_neurons.get(day, 0) + estimate
        _local_neurons.clear()
        _local_neurons[day] = used
        return used <= budget


def try_consume_gemini_call(*, now: float | None = None) -> bool:
    """Consume one Gemini request against the per-minute RPM bucket."""
    now = time.time() if now is None else now
    rpm = get_cloud_settings().gemini_rpm
    if rpm <= 0:
        return True
    client = _get_redis()
    if client is not None:
        try:
            key = f"gemini:rpm:{int(now // 60)}"
            count = client.incr(key)
            if count == 1:
                client.expire(key, 120)
            return count <= rpm
        except Exception:  # pragma: no cover
            pass
    with _lock:
        cutoff = now - 60
        _local_gemini[:] = [t for t in _local_gemini if t > cutoff]
        if len(_local_gemini) >= rpm:
            return False
        _local_gemini.append(now)
        return True

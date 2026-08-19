"""Prometheus-compatible metrics and request analytics middleware.

Exposes a /metrics endpoint in Prometheus text exposition format and
tracks request latency, throughput, and error rates in-process.
Uses a fixed-capacity circular buffer for histograms to bound memory.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict, deque
from datetime import datetime
from threading import Lock
from typing import TYPE_CHECKING, Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from . import database as db

if TYPE_CHECKING:
    from fastapi import Request, Response

logger = logging.getLogger(__name__)

# Maximum observations per histogram metric (bounded memory)
_MAX_HISTOGRAM_SIZE = 5000
_QUANTILES = [("0.5", 0.5), ("0.95", 0.95), ("0.99", 0.99)]


def _analytics_subject(request: Request) -> str:
    """Return an opted-in subject id, otherwise fail closed.

    Request metrics stay aggregated in process, but durable session and event
    records are personal data.  Only a verified subject with an active
    ``analytics`` consent receipt may create those records.
    """
    ctx = getattr(request.state, "auth", None)
    user_id = getattr(ctx, "user_id", "") if getattr(ctx, "authenticated", False) else ""
    if not user_id:
        return ""
    try:
        return user_id if db.has_active_consent(user_id, "analytics") else ""
    except Exception:
        logger.warning("Analytics consent lookup failed; durable tracking skipped", exc_info=True)
        return ""


# ---------------------------------------------------------------------------
# In-process metrics store (thread-safe)
# ---------------------------------------------------------------------------
class MetricsStore:
    """Lightweight in-process metrics collector.

    Stores counters and histograms for Prometheus-compatible export without
    requiring the prometheus_client library.  Uses deque for bounded memory.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[str, int] = defaultdict(int)
        self._histograms: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=_MAX_HISTOGRAM_SIZE)
        )
        self._start_time = time.time()

    def inc(self, name: str, value: int = 1, labels: dict[str, str] | None = None) -> None:
        key = self._label_key(name, labels)
        with self._lock:
            self._counters[key] += value

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        key = self._label_key(name, labels)
        with self._lock:
            self._histograms[key].append(value)

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of all metrics."""
        with self._lock:
            counters = dict(self._counters)
            histograms = {}
            for k, values in self._histograms.items():
                if values:
                    sv = sorted(values)
                    n = len(sv)
                    total = sum(sv)
                    histograms[k] = {
                        "count": n,
                        "sum": round(total, 4),
                        "avg": round(total / n, 4),
                        "p50": round(sv[int(n * 0.5)], 4),
                        "p95": round(sv[min(int(n * 0.95), n - 1)], 4),
                        "p99": round(sv[min(int(n * 0.99), n - 1)], 4),
                        "min": round(sv[0], 4),
                        "max": round(sv[-1], 4),
                    }
        return {
            "uptime_seconds": round(time.time() - self._start_time, 1),
            "counters": counters,
            "histograms": histograms,
        }

    def to_prometheus(self) -> str:
        """Render metrics in Prometheus text exposition format."""
        lines: list[str] = []
        declared_types: set[str] = set()

        with self._lock:
            for key, val in sorted(self._counters.items()):
                name, labels = self._parse_key(key)
                if name not in declared_types:
                    lines.append(f"# TYPE {name} counter")
                    declared_types.add(name)
                lines.append(f"{name}{labels} {val}")

            for key, values in sorted(self._histograms.items()):
                if not values:
                    continue
                name, labels = self._parse_key(key)
                if name not in declared_types:
                    lines.append(f"# TYPE {name} summary")
                    declared_types.add(name)
                sv = sorted(values)
                n = len(sv)
                total = sum(sv)
                for label, q in _QUANTILES:
                    idx = min(int(n * q), n - 1)
                    if labels:
                        combined = f'quantile="{label}",{labels[1:-1]}'
                        lines.append(f"{name}{{{combined}}} {sv[idx]:.4f}")
                    else:
                        lines.append(f'{name}{{quantile="{label}"}} {sv[idx]:.4f}')
                lines.append(f"{name}_count{labels} {n}")
                lines.append(f"{name}_sum{labels} {total:.4f}")

        lines.extend(_index_lifecycle_prometheus_metrics())
        return "\n".join(lines) + "\n"

    @staticmethod
    def _label_key(name: str, labels: dict[str, str] | None) -> str:
        if not labels:
            return name
        lbl = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        return f"{name}{{{lbl}}}"

    @staticmethod
    def _parse_key(key: str) -> tuple[str, str]:
        if "{" in key:
            idx = key.index("{")
            return key[:idx], key[idx:]
        return key, ""


def _status_timestamp(status: dict[str, Any] | None, field: str) -> float:
    """Return an epoch timestamp from an operational status file, or zero."""
    if not status:
        return 0.0
    raw = str(status.get(field) or "")
    if not raw:
        return 0.0
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _index_lifecycle_prometheus_metrics() -> list[str]:
    """Render index lifecycle gauges from the atomically-written status files.

    The indexer and backup scheduler run in separate short-lived containers, so
    process-local counters would disappear before Prometheus scrapes the API.
    Reading their small status records gives the API durable, scrapeable gauges
    without adding a separate exporter or exposing the Qdrant management API.
    """
    try:
        from .freshness import load_backup_status, load_lifecycle_status, load_status

        freshness = load_status()
        lifecycle = load_lifecycle_status()
        backup = load_backup_status()
    except Exception:
        logger.warning("Could not read Qdrant lifecycle status for /metrics", exc_info=True)
        freshness = lifecycle = backup = None

    source_drift = bool(
        freshness
        and (
            freshness.get("index_drift")
            or freshness.get("snapshot_missing")
            or freshness.get("index_snapshot_missing")
            or freshness.get("drift_count")
        )
    )
    backup_required = os.getenv("QDRANT_BACKUP_REQUIRED", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    gauges = {
        "ura_qdrant_index_fresh": int(bool(freshness and freshness.get("ok"))),
        "ura_qdrant_index_drift": int(source_drift),
        "ura_qdrant_index_status_timestamp_seconds": _status_timestamp(freshness, "checked_at"),
        "ura_qdrant_rebuild_failed": int(bool(lifecycle and not lifecycle.get("ok"))),
        "ura_qdrant_rebuild_timestamp_seconds": _status_timestamp(lifecycle, "last_attempt_at"),
        "ura_qdrant_backup_required": int(backup_required),
        "ura_qdrant_backup_failed": int(bool(backup_required and backup and not backup.get("ok"))),
        "ura_qdrant_backup_timestamp_seconds": _status_timestamp(backup, "last_attempt_at"),
        "ura_qdrant_restore_drill_failed": int(
            bool(backup_required and backup and backup.get("restore_drill_ok") is False)
        ),
        "ura_qdrant_restore_drill_timestamp_seconds": _status_timestamp(
            backup,
            "last_restore_drill_at",
        ),
    }
    lines: list[str] = []
    for name, value in gauges.items():
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {value}")
    return lines


# Global singleton
metrics = MetricsStore()


# ---------------------------------------------------------------------------
# FastAPI middleware
# ---------------------------------------------------------------------------
class AnalyticsMiddleware(BaseHTTPMiddleware):
    """Track request latency, status codes, and log conversations."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.perf_counter()
        method = request.method
        path = request.url.path

        # Extract session ID from header (set by frontend)
        session_id = request.headers.get("X-Session-ID", "")

        response: Response = await call_next(request)

        elapsed_ms = (time.perf_counter() - start) * 1000
        status = str(response.status_code)

        # Track metrics
        metrics.inc(
            "http_requests_total", labels={"method": method, "path": path, "status": status}
        )
        metrics.observe(
            "http_request_duration_ms", elapsed_ms, labels={"method": method, "path": path}
        )

        if response.status_code >= 400:
            metrics.inc(
                "http_errors_total", labels={"method": method, "path": path, "status": status}
            )

        # Track session activity
        analytics_user_id = _analytics_subject(request)
        if session_id and analytics_user_id and path.startswith("/v1/"):
            try:
                user_agent = request.headers.get("User-Agent", "")[:200]
                db.upsert_session(session_id, user_agent=user_agent, user_id=analytics_user_id)
            except Exception:
                logger.debug("Session tracking failed", exc_info=True)

        # Track specific endpoint analytics
        if path == "/v1/chat" and method == "POST":
            metrics.inc("chat_requests_total")
            metrics.observe("chat_response_time_ms", elapsed_ms)
            if analytics_user_id:
                try:
                    db.track_event(
                        "chat_request",
                        json.dumps({"response_time_ms": round(elapsed_ms, 2), "status": status}),
                        session_id=session_id or None,
                        user_id=analytics_user_id,
                    )
                except Exception:
                    logger.debug("Event tracking failed", exc_info=True)

        # Track retrieval mode distribution for degradation visibility
        if path == "/v1/chat" and method == "POST":
            try:
                body_bytes = getattr(response, "_body", b"")
                if body_bytes:
                    body_json = json.loads(body_bytes)
                    mode = body_json.get("retrieval_mode", "unknown")
                    metrics.inc("retrieval_mode_total", labels={"mode": mode})
                    faith = body_json.get("faithfulness_score")
                    if faith is not None:
                        metrics.observe("faithfulness_score", faith)
                    if body_json.get("escalation_required"):
                        metrics.inc("escalation_required_total")
            except Exception:
                pass  # best-effort metrics

        return response

"""Prometheus metrics and request analytics middleware.

Every metric in the API goes through :data:`metrics`, a small facade over
``prometheus_client``. Call sites keep the old ``metrics.inc(name, labels=…)``
/ ``metrics.observe(name, value, labels=…)`` shape; the facade turns each name
into a real Prometheus family under the ``ura_`` namespace.

Why a facade over the official client rather than the in-process store this
module used to be:

* **Several worker processes.** The API runs under ``uvicorn --workers N``.
  An in-process store gave each worker its own private counters, so every
  scrape read whichever worker answered and ``rate()`` saw counter resets on
  most scrapes. With ``PROMETHEUS_MULTIPROC_DIR`` set (``entrypoint.sh`` and
  the Crane Cloud supervisor set it, and wipe it before the workers start)
  each worker writes its samples there and ``/metrics`` aggregates all of
  them. Without it (tests, a single dev worker) the client runs in-process.
* **Real histograms.** Buckets, ``_count`` and ``_sum`` are cumulative
  counters, so ``rate()`` and ``histogram_quantile()`` are valid. The old
  "buckets" were recomputed from the last 5,000 samples and went down when
  latency got worse.
* **Bounded series.** Request labels use the route template
  (``/v1/tickets/{ticket_id}``), never the raw URL, and each family is capped
  at ``METRICS_MAX_SERIES_PER_METRIC`` label sets; anything past the cap is
  folded into one ``__other__`` series. A scanner requesting random paths can
  no longer grow memory without bound.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from datetime import datetime
from threading import Lock
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)


def _prepare_multiprocess_dir() -> str:
    """Return a usable multiprocess directory, or ``""`` for in-process mode.

    Must run before ``prometheus_client`` is imported: the client picks its
    storage backend from the environment at import time. A configured but
    unusable directory falls back to in-process metrics (and says so) rather
    than taking the API down.
    """
    path = os.getenv("PROMETHEUS_MULTIPROC_DIR", "").strip()
    if not path:
        return ""
    try:
        os.makedirs(path, exist_ok=True)
        if not os.access(path, os.W_OK):
            raise PermissionError(path)
    except OSError:
        logger.error(
            "PROMETHEUS_MULTIPROC_DIR=%s is not writable; metrics fall back to this worker only",
            path,
            exc_info=True,
        )
        os.environ.pop("PROMETHEUS_MULTIPROC_DIR", None)
        return ""
    return path


_MULTIPROC_DIR = _prepare_multiprocess_dir()

from prometheus_client import (  # noqa: E402 - must follow the multiprocess setup above
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    disable_created_metrics,
    generate_latest,
)
from prometheus_client import multiprocess as _prom_multiprocess  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint  # noqa: E402

from . import database as db  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from fastapi import Request, Response
    from prometheus_client.metrics_core import Metric

# ``*_created`` series double the output and nothing here reads them.
disable_created_metrics()

#: Content type for ``/metrics`` (Prometheus text exposition 0.0.4).
METRICS_CONTENT_TYPE = CONTENT_TYPE_LATEST

NAMESPACE = "ura_"

_MAX_SERIES_PER_METRIC = max(10, int(os.getenv("METRICS_MAX_SERIES_PER_METRIC", "500")))
_MAX_LABEL_VALUE_LEN = 120
_OVERFLOW_LABEL = "__other__"
_UNMATCHED_ROUTE = "__unmatched__"
_KNOWN_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
_NAME_RE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
_LABEL_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# Bucket presets, chosen from the metric's unit suffix.
_MS_BUCKETS = (5, 10, 25, 50, 100, 250, 500, 1000, 2000, 3000, 5000, 10000, 20000, 30000, 60000, 120000)
_SECONDS_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5, 10, 20, 30, 60, 120)
_RATIO_BUCKETS = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
_BYTES_BUCKETS = (1024, 4096, 16384, 65536, 262144, 1048576, 4194304, 16777216, 67108864)
_COUNT_BUCKETS = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000)

_QUANTILES = (("p50", 0.5), ("p95", 0.95), ("p99", 0.99))


def default_buckets(name: str) -> tuple[float, ...]:
    """Histogram buckets for *name*, inferred from its unit suffix."""
    if name.endswith("_ms"):
        return _MS_BUCKETS
    if name.endswith(("_seconds", "_s")):
        return _SECONDS_BUCKETS
    if name.endswith("_bytes"):
        return _BYTES_BUCKETS
    if "score" in name or "faithfulness" in name or name.endswith("_ratio"):
        return _RATIO_BUCKETS
    return _COUNT_BUCKETS


def exported_name(name: str) -> str:
    """The Prometheus family name for a call-site metric name."""
    clean = name.strip()
    if not _NAME_RE.match(clean):
        raise ValueError(f"invalid metric name: {name!r}")
    return clean if clean.startswith(NAMESPACE) else f"{NAMESPACE}{clean}"


def _clean_label_value(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("\r", " ").replace("\n", " ")[:_MAX_LABEL_VALUE_LEN]


class _Family:
    __slots__ = ("kind", "label_names", "metric", "seen")

    def __init__(self, kind: str, metric: Any, label_names: tuple[str, ...]) -> None:
        self.kind = kind
        self.metric = metric
        self.label_names = label_names
        self.seen: set[tuple[str, ...]] = set()


class MetricsStore:
    """Facade over ``prometheus_client`` with the store's historical API.

    ``inc`` / ``observe`` / ``set_gauge`` / ``add_gauge`` create the family on
    first use. A family's label names are fixed by its first call; a later
    call with other keys fills missing labels with ``""`` and drops unknown
    ones (logged once) instead of raising inside a request handler.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._registry = CollectorRegistry(auto_describe=True)
        self._families: dict[str, _Family] = {}
        self._warned: set[str] = set()
        self._start_time = time.time()

    # -- recording ---------------------------------------------------------
    def inc(self, name: str, value: float = 1, labels: Mapping[str, Any] | None = None) -> None:
        self._record("counter", name, labels, lambda child: child.inc(value))

    def observe(
        self,
        name: str,
        value: float,
        labels: Mapping[str, Any] | None = None,
        *,
        buckets: Iterable[float] | None = None,
    ) -> None:
        self._record("histogram", name, labels, lambda child: child.observe(value), buckets=buckets)

    def set_gauge(self, name: str, value: float, labels: Mapping[str, Any] | None = None) -> None:
        """Set a gauge to its latest value (across workers: the most recent write)."""
        self._record("gauge_latest", name, labels, lambda child: child.set(value))

    def add_gauge(self, name: str, delta: float, labels: Mapping[str, Any] | None = None) -> None:
        """Move an up/down gauge such as active connections (summed across live workers)."""
        self._record("gauge_sum", name, labels, lambda child: child.inc(delta))

    def _record(
        self,
        kind: str,
        name: str,
        labels: Mapping[str, Any] | None,
        action: Any,
        *,
        buckets: Iterable[float] | None = None,
    ) -> None:
        try:
            family = self._family(kind, name, tuple(sorted((labels or {}).keys())), buckets)
            if family is None:
                return
            if not family.label_names:
                action(family.metric)
                return
            values = self._label_values(name, family, labels or {})
            action(family.metric.labels(*values))
        except Exception:
            # Metrics are best-effort; they must never fail a request.
            self._warn_once(f"record:{name}", "Metric %s could not be recorded", name, exc_info=True)

    def _family(
        self,
        kind: str,
        name: str,
        label_names: tuple[str, ...],
        buckets: Iterable[float] | None,
    ) -> _Family | None:
        full = exported_name(name)
        family = self._families.get(full)
        if family is not None:
            if family.kind != kind:
                self._warn_once(
                    f"kind:{full}", "Metric %s is a %s; ignoring a %s write", full, family.kind, kind
                )
                return None
            return family
        with self._lock:
            family = self._families.get(full)
            if family is not None:
                return family if family.kind == kind else None
            for label in label_names:
                if not _LABEL_RE.match(label) or label.startswith("__") or label == "le":
                    raise ValueError(f"invalid label name {label!r} on {full}")
            doc = f"URA chatbot metric {full}"
            if kind == "counter":
                metric: Any = Counter(full, doc, label_names, registry=self._registry)
            elif kind == "histogram":
                metric = Histogram(
                    full,
                    doc,
                    label_names,
                    buckets=tuple(buckets) if buckets else default_buckets(full),
                    registry=self._registry,
                )
            elif kind == "gauge_latest":
                metric = Gauge(full, doc, label_names, registry=self._registry, multiprocess_mode="mostrecent")
            else:
                metric = Gauge(full, doc, label_names, registry=self._registry, multiprocess_mode="livesum")
            family = _Family(kind, metric, label_names)
            self._families[full] = family
            return family

    def _label_values(self, name: str, family: _Family, labels: Mapping[str, Any]) -> tuple[str, ...]:
        unknown = set(labels) - set(family.label_names)
        if unknown:
            self._warn_once(
                f"labels:{name}",
                "Metric %s has labels %s; dropping %s",
                name,
                family.label_names,
                sorted(unknown),
            )
        values = tuple(_clean_label_value(labels.get(key, "")) for key in family.label_names)
        if values in family.seen:
            return values
        with self._lock:
            if values in family.seen:
                return values
            if len(family.seen) >= _MAX_SERIES_PER_METRIC:
                self._warn_once(
                    f"cap:{name}",
                    "Metric %s reached %d label sets; folding new ones into %s",
                    name,
                    _MAX_SERIES_PER_METRIC,
                    _OVERFLOW_LABEL,
                )
                values = tuple(_OVERFLOW_LABEL for _ in family.label_names)
            family.seen.add(values)
        return values

    def _warn_once(self, key: str, message: str, *args: Any, exc_info: bool = False) -> None:
        if key in self._warned:
            return
        self._warned.add(key)
        logger.warning(message, *args, exc_info=exc_info)

    # -- reading -----------------------------------------------------------
    def _collection_registry(self) -> CollectorRegistry:
        if not _MULTIPROC_DIR:
            return self._registry
        registry = CollectorRegistry()
        _prom_multiprocess.MultiProcessCollector(registry, path=_MULTIPROC_DIR)
        return registry

    def _collect(self) -> list[Metric]:
        return list(self._collection_registry().collect())

    def to_prometheus(self) -> str:
        """Prometheus text exposition: every worker's samples plus index lifecycle gauges."""
        text = generate_latest(self._collection_registry()).decode("utf-8")
        return text + "\n".join(_index_lifecycle_prometheus_metrics()) + "\n"

    def snapshot(self) -> dict[str, Any]:
        """JSON view for the staff dashboard, aggregated across workers.

        Keys keep the historical ``name{label="value",…}`` form with the
        ``ura_`` namespace removed (``retrieval_mode_total{mode="hybrid"}``,
        ``http_request_duration_ms{method="POST",path="/v1/chat"}``).
        Histogram percentiles are estimated from the buckets the same way
        ``histogram_quantile`` does.
        """
        counters: dict[str, float] = {}
        gauges: dict[str, float] = {}
        histograms: dict[str, dict[str, float]] = {}
        for family in self._collect():
            if family.type == "counter":
                for sample in family.samples:
                    if sample.name.endswith("_total"):
                        counters[_snapshot_key(sample.name, sample.labels)] = _number(sample.value)
            elif family.type == "gauge":
                for sample in family.samples:
                    gauges[_snapshot_key(sample.name, sample.labels)] = _number(sample.value)
            elif family.type == "histogram":
                histograms.update(_histogram_summaries(family))
        return {
            "uptime_seconds": round(time.time() - self._start_time, 1),
            "counters": counters,
            "gauges": gauges,
            "histograms": histograms,
        }


def _number(value: float) -> float:
    return int(value) if float(value).is_integer() else round(value, 4)


def _snapshot_key(name: str, labels: Mapping[str, str]) -> str:
    base = name[len(NAMESPACE):] if name.startswith(NAMESPACE) else name
    if not labels:
        return base
    inner = ",".join(f'{key}="{labels[key]}"' for key in sorted(labels))
    return f"{base}{{{inner}}}"


def _histogram_summaries(family: Metric) -> dict[str, dict[str, float]]:
    grouped: dict[tuple[tuple[str, str], ...], dict[str, Any]] = {}
    for sample in family.samples:
        labels = {k: v for k, v in sample.labels.items() if k != "le"}
        entry = grouped.setdefault(tuple(sorted(labels.items())), {"buckets": [], "count": 0.0, "sum": 0.0})
        if sample.name.endswith("_bucket"):
            entry["buckets"].append((float(sample.labels["le"]), float(sample.value)))
        elif sample.name.endswith("_count"):
            entry["count"] = float(sample.value)
        elif sample.name.endswith("_sum"):
            entry["sum"] = float(sample.value)
    out: dict[str, dict[str, float]] = {}
    for label_items, entry in grouped.items():
        count = entry["count"]
        if count <= 0:
            continue
        buckets = sorted(entry["buckets"])
        summary: dict[str, float] = {
            "count": _number(count),
            "sum": round(entry["sum"], 4),
            "avg": round(entry["sum"] / count, 4),
        }
        for key, q in _QUANTILES:
            summary[key] = round(bucket_quantile(q, buckets, count), 4)
        out[_snapshot_key(family.name, dict(label_items))] = summary
    return out


def bucket_quantile(q: float, buckets: list[tuple[float, float]], count: float) -> float:
    """Estimate quantile *q* from cumulative ``(upper_bound, count)`` buckets.

    Mirrors PromQL ``histogram_quantile``: linear interpolation inside the
    bucket that holds the rank, with 0 as the first lower bound and the
    highest finite bound returned when the rank falls in ``+Inf``.
    """
    if count <= 0 or not buckets:
        return 0.0
    rank = q * count
    prev_upper, prev_cum = 0.0, 0.0
    for upper, cumulative in buckets:
        if cumulative >= rank:
            if math.isinf(upper):
                return prev_upper
            if cumulative == prev_cum:
                return upper
            return prev_upper + (upper - prev_upper) * (rank - prev_cum) / (cumulative - prev_cum)
        prev_upper, prev_cum = upper, cumulative
    return prev_upper


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


_LIFECYCLE_HELP = {
    "ura_qdrant_index_fresh": "1 when the serving index matches the shipped corpus",
    "ura_qdrant_index_drift": "1 when source/index drift or a missing index binding was reported",
    "ura_qdrant_index_status_timestamp_seconds": "When the freshness status was last checked",
    "ura_qdrant_rebuild_failed": "1 when the last staged rebuild failed before promotion",
    "ura_qdrant_rebuild_timestamp_seconds": "When the last staged rebuild was attempted",
    "ura_qdrant_backup_required": "1 when this deployment must keep snapshot backups",
    "ura_qdrant_backup_failed": "1 when the last required snapshot backup failed",
    "ura_qdrant_backup_timestamp_seconds": "When the last snapshot backup was attempted",
    "ura_qdrant_restore_drill_failed": "1 when the last restore drill failed",
    "ura_qdrant_restore_drill_timestamp_seconds": "When the last restore drill ran",
}


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
        lines.append(f"# HELP {name} {_LIFECYCLE_HELP[name]}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {value}")
    return lines


# Global singleton
metrics = MetricsStore()


#: ``(kind, metric name, histogram buckets)`` — the shape of the tables the
#: WebSocket modules keep for their own metric keys.
MetricSpec = tuple[str, str, tuple[float, ...] | None]


def record_mapped(
    table: Mapping[str, MetricSpec],
    key: str,
    value: float = 1.0,
    labels: Mapping[str, Any] | None = None,
) -> None:
    """Record ``value`` against ``table[key]``: a counter, up/down gauge or histogram."""
    spec = table.get(key)
    if spec is None:
        return
    kind, name, buckets = spec
    if kind == "gauge":
        metrics.add_gauge(name, value, labels)
    elif kind == "histogram":
        metrics.observe(name, value, labels, buckets=buckets)
    else:
        metrics.inc(name, value, labels)


# ---------------------------------------------------------------------------
# Chat turn metrics — one recorder for every channel
# ---------------------------------------------------------------------------
_OUTCOME_BY_MODE = {
    "blocked": "blocked",
    "out_of_scope": "out_of_scope",
    "out_of_jurisdiction": "out_of_scope",
    "abstained": "abstained",
    "false_premise_rejected": "abstained",
    "clarification": "clarification",
    "escalated": "escalated",
}


def turn_outcome(result: Mapping[str, Any]) -> str:
    """How a chat turn ended, for the containment and abstention SLIs."""
    mode = str(result.get("retrieval_mode") or "")
    outcome = _OUTCOME_BY_MODE.get(mode)
    if outcome and outcome != "escalated":
        return outcome
    if outcome == "escalated" or result.get("escalation_required") or result.get("handoff"):
        return "escalated"
    return "answered"


def record_chat_turn(
    result: Mapping[str, Any],
    *,
    elapsed_ms: float,
    channel: str,
    error: bool = False,
) -> None:
    """Record one finished chat turn, whichever transport carried it.

    ``channel`` is ``rest`` (``/v1/chat``), ``sse`` (``/v1/chat/stream``),
    ``ws`` (``/v2/chat/stream``), ``voice`` (``/v1/voice/chat``),
    ``multimodal`` (``/v1/voice/vision/chat``) or ``call`` (the receptionist).
    Every path calls this once per turn, so the counters agree with each
    other and no transport is invisible.
    """
    outcome = "error" if error else turn_outcome(result)
    mode = str(result.get("retrieval_mode") or "unknown")
    metrics.inc("chat_turns_total", labels={"channel": channel, "outcome": outcome})
    metrics.inc("retrieval_mode_total", labels={"mode": mode})
    if elapsed_ms > 0:
        metrics.observe("chat_response_time_ms", elapsed_ms, labels={"channel": channel, "mode": mode})
    faith = result.get("faithfulness_score")
    if faith is not None:
        try:
            metrics.observe("faithfulness_score", float(faith))
        except (TypeError, ValueError):
            pass
    # Counted apart from the outcome label: an abstained or blocked turn can
    # still open a ticket, and that hand-off is an escalation.
    if outcome == "escalated" or (not error and (result.get("escalation_required") or result.get("handoff"))):
        metrics.inc("escalation_required_total", labels={"channel": channel})


# ---------------------------------------------------------------------------
# Request metrics middleware
# ---------------------------------------------------------------------------
def _analytics_subject(request: Request) -> str:
    """Return an opted-in subject id, otherwise fail closed.

    Request metrics are aggregate, but durable session and event records are
    personal data. Only a verified subject with an active ``analytics``
    consent receipt may create those records.
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


def route_label(request: Request) -> str:
    """The matched route template (``/v1/tickets/{ticket_id}``), never the raw path."""
    route = request.scope.get("route")
    template = getattr(route, "path", None) or getattr(route, "path_format", None)
    return str(template) if template else _UNMATCHED_ROUTE


class AnalyticsMiddleware(BaseHTTPMiddleware):
    """Track request latency and status codes, plus consented session activity."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.perf_counter()
        method = request.method if request.method in _KNOWN_METHODS else "OTHER"
        session_id = request.headers.get("X-Session-ID", "")

        response: Response = await call_next(request)

        elapsed_ms = (time.perf_counter() - start) * 1000
        status = str(response.status_code)
        path = route_label(request)

        metrics.inc("http_requests_total", labels={"method": method, "path": path, "status": status})
        metrics.observe("http_request_duration_ms", elapsed_ms, labels={"method": method, "path": path})
        if response.status_code >= 400:
            metrics.inc("http_errors_total", labels={"method": method, "path": path, "status": status})

        analytics_user_id = _analytics_subject(request)
        if session_id and analytics_user_id and request.url.path.startswith("/v1/"):
            try:
                user_agent = request.headers.get("User-Agent", "")[:200]
                db.upsert_session(session_id, user_agent=user_agent, user_id=analytics_user_id)
            except Exception:
                logger.debug("Session tracking failed", exc_info=True)

        if path == "/v1/chat" and request.method == "POST" and analytics_user_id:
            try:
                db.track_event(
                    "chat_request",
                    json.dumps({"response_time_ms": round(elapsed_ms, 2), "status": status}),
                    session_id=session_id or None,
                    user_id=analytics_user_id,
                )
            except Exception:
                logger.debug("Event tracking failed", exc_info=True)

        return response

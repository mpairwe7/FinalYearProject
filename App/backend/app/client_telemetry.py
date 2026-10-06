"""Real-user monitoring from the web client: Core Web Vitals and client errors.

The browser posts here (``POST /v1/telemetry/vitals`` and
``POST /v1/telemetry/errors``, reached through the Next.js ``/api`` rewrite).
Before these endpoints existed the client beaconed to a path with no handler
and every report was dropped.

What is kept is aggregate and anonymous:

* vitals become ``ura_web_vitals_ms{metric,rating,route}`` (LCP, INP, FCP,
  TTFB) and ``ura_web_vitals_cls{rating,route}`` histograms, so dashboards
  show the 75th percentile real users see — the figure Google's Core Web
  Vitals assessment uses;
* client errors become ``ura_client_errors_total{kind,route}`` plus one
  WARNING log record (``event=client.error``) naming the error class, the
  Next.js digest and the script location — never the message text.

No user id, session id, IP address or query string is accepted. Routes are
normalised so a conversation or ticket id cannot become a label value.
"""

from __future__ import annotations

import logging
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .analytics import metrics

logger = logging.getLogger("app.client")

#: Largest telemetry body accepted (a full vitals batch is well under 2 KB).
MAX_BODY_BYTES = 16_384
_ID_SEGMENT = re.compile(r"/(?:[0-9a-fA-F-]{8,}|\d+|[A-Za-z0-9_-]{24,})(?=/|$)")
_CLS_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.15, 0.25, 0.5, 1.0, 2.0)


class WebVital(BaseModel):
    """One Web Vitals measurement as reported by the ``web-vitals`` library."""

    model_config = ConfigDict(extra="forbid")

    name: Literal["LCP", "INP", "CLS", "FCP", "TTFB"]
    value: float = Field(..., ge=0, le=600_000)
    rating: Literal["good", "needs-improvement", "poor"]
    route: str = Field("", max_length=200)
    navigation_type: str = Field("", max_length=32)


class WebVitalsBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrics: list[WebVital] = Field(..., min_length=1, max_length=20)


class ClientError(BaseModel):
    """An uncaught error or a rendering failure caught by an error boundary.

    Deliberately no message field: an error message can quote what the
    taxpayer typed. ``name`` (the error class), the Next.js ``digest`` and
    ``source`` (script file and line) are enough to find the failing code.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["error", "unhandledrejection", "boundary", "global-boundary"]
    name: str = Field("", max_length=80)
    digest: str = Field("", max_length=64)
    source: str = Field("", max_length=200)
    route: str = Field("", max_length=200)


def normalise_route(path: str) -> str:
    """Pathname -> bounded label: no query string, ids folded to ``:id``."""
    clean = (path or "/").split("?", 1)[0].split("#", 1)[0] or "/"
    if not clean.startswith("/"):
        clean = "/" + clean
    clean = _ID_SEGMENT.sub("/:id", clean)
    parts = [part for part in clean.split("/") if part][:3]
    return "/" + "/".join(parts)


def record_web_vitals(batch: WebVitalsBatch) -> int:
    """Record every measurement in *batch*; returns how many were recorded."""
    for vital in batch.metrics:
        route = normalise_route(vital.route)
        if vital.name == "CLS":
            metrics.observe(
                "web_vitals_cls",
                vital.value,
                labels={"rating": vital.rating, "route": route},
                buckets=_CLS_BUCKETS,
            )
        else:
            metrics.observe(
                "web_vitals_ms",
                vital.value,
                labels={"metric": vital.name, "rating": vital.rating, "route": route},
            )
    return len(batch.metrics)


def record_client_error(report: ClientError) -> None:
    route = normalise_route(report.route)
    metrics.inc("client_errors_total", labels={"kind": report.kind, "route": route})
    logger.warning(
        "client error %s on %s",
        report.kind,
        route,
        extra={
            "event": "client.error",
            "client_error_kind": report.kind,
            "client_error_name": report.name,
            "client_error_digest": report.digest,
            "client_error_source": report.source.split("?", 1)[0],
            "http_route": route,
        },
    )

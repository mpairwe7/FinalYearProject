"""OpenTelemetry GenAI tracing for the URA Chatbot.

Implements OpenTelemetry semantic conventions for Generative AI (2026):
- gen_ai.system: per-pipeline parent spans
- gen_ai.token.usage: prompt / completion token estimates
- rag.*: retrieval-specific child spans (embed, search, rerank)

Opt-in via ``OTEL_ENABLED=true``.

Reference: https://opentelemetry.io/docs/specs/semconv/gen-ai/
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from typing import Any, Generator

logger = logging.getLogger(__name__)

OTEL_ENABLED = os.getenv("OTEL_ENABLED", "false").lower() == "true"
OTEL_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "ura-chatbot-api")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

_tracer: Any = None
_meter: Any = None
_init_lock = threading.Lock()


def init_tracing() -> None:
    """Initialise the OpenTelemetry tracer + meter (no-op when disabled).

    Thread-safe: uses a lock so concurrent calls during startup are safe.
    """
    global _tracer, _meter

    with _init_lock:
        if _tracer is not None:
            return  # already initialised

        if not OTEL_ENABLED:
            logger.info("OpenTelemetry disabled (set OTEL_ENABLED=true to enable)")
            return

        try:
            from opentelemetry import metrics, trace
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
                OTLPMetricExporter,
            )
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            resource = Resource.create({"service.name": OTEL_SERVICE_NAME})

            # Traces
            tp = TracerProvider(resource=resource)
            tp.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT))
            )
            trace.set_tracer_provider(tp)
            _tracer = trace.get_tracer("ura.chatbot.genai", "1.0.0")

            # Metrics
            reader = PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=OTEL_ENDPOINT),
                export_interval_millis=60_000,
            )
            mp = MeterProvider(resource=resource, metric_readers=[reader])
            metrics.set_meter_provider(mp)
            _meter = metrics.get_meter("ura.chatbot.genai", "1.0.0")

            logger.info("OpenTelemetry tracing initialised (endpoint=%s)", OTEL_ENDPOINT)
        except ImportError:
            logger.warning("OpenTelemetry SDK not installed; tracing disabled")
        except Exception:
            logger.exception("Failed to initialise OpenTelemetry")


# ---------------------------------------------------------------------------
# Span helpers
# ---------------------------------------------------------------------------
@contextmanager
def trace_rag_pipeline(query: str) -> Generator[dict[str, Any], None, None]:
    """Parent span wrapping the full RAG pipeline."""
    ctx: dict[str, Any] = {"timings": {}}

    if _tracer is None:
        yield ctx
        return

    with _tracer.start_as_current_span(
        "rag.pipeline",
        attributes={
            "gen_ai.system": "ura-chatbot",
            "gen_ai.request.model": "ura-gemma-2-9b",
            "gen_ai.prompt": query[:500],
        },
    ) as span:
        ctx["span"] = span
        yield ctx
        if "faithfulness" in ctx:
            span.set_attribute("gen_ai.faithfulness_score", ctx["faithfulness"])
        if "num_sources" in ctx:
            span.set_attribute("gen_ai.retrieval.num_sources", ctx["num_sources"])
        if "locale" in ctx:
            span.set_attribute("gen_ai.request.locale", ctx["locale"])


@contextmanager
def trace_stage(name: str, attributes: dict[str, Any] | None = None) -> Generator[None, None, None]:
    """Child span for a specific RAG stage (embed, search, rerank, generate)."""
    if _tracer is None:
        yield
        return

    with _tracer.start_as_current_span(f"rag.{name}", attributes=attributes or {}):
        yield


# ---------------------------------------------------------------------------
# Metric helpers (GenAI semantic conventions)
# ---------------------------------------------------------------------------
def record_token_usage(prompt_tokens: int, completion_tokens: int) -> None:
    """Record ``gen_ai.client.token.usage`` counter."""
    if _meter is None:
        return
    try:
        counter = _meter.create_counter(
            "gen_ai.client.token.usage",
            unit="token",
            description="Tokens consumed by GenAI operations",
        )
        counter.add(prompt_tokens, {"gen_ai.token.type": "input"})
        counter.add(completion_tokens, {"gen_ai.token.type": "output"})
    except Exception:
        logger.debug("Failed to record token usage", exc_info=True)


def record_retrieval_metrics(
    num_results: int,
    latency_ms: float,
    faithfulness: float = 0.0,
) -> None:
    """Record retrieval-stage metrics."""
    if _meter is None:
        return
    try:
        hist = _meter.create_histogram(
            "gen_ai.retrieval.duration",
            unit="ms",
            description="Retrieval latency in milliseconds",
        )
        hist.record(latency_ms)

        counter = _meter.create_counter(
            "gen_ai.retrieval.results",
            description="Number of passages returned",
        )
        counter.add(num_results)
    except Exception:
        logger.debug("Failed to record retrieval metrics", exc_info=True)

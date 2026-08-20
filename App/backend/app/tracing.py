"""OpenTelemetry GenAI tracing for the URA Chatbot.

Implements OpenTelemetry semantic conventions for Generative AI (2025 stable):
- gen_ai.system               – static per-process attribute
- gen_ai.operation.name       – chat | embeddings | text_completion
- gen_ai.request.model        – model ID requested
- gen_ai.response.model       – model ID used (may differ, e.g. aliasing)
- gen_ai.usage.input_tokens   – real tokenizer counts (not word proxy)
- gen_ai.usage.output_tokens  – real tokenizer counts
- rag.*                       – retrieval-specific child spans

All attribute names are drawn from the current stable semconv as of 2025.
Opt-in via ``OTEL_ENABLED=true``.

Reference: https://opentelemetry.io/docs/specs/semconv/gen-ai/
"""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Generator

logger = logging.getLogger(__name__)

OTEL_ENABLED = os.getenv("OTEL_ENABLED", "false").lower() == "true"
OTEL_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "ura-chatbot-api")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
GEN_AI_MODEL = os.getenv("LLM_MODEL", "Sunbird/Sunflower-14B-FP8")

_tracer: Any = None
_meter: Any = None
_init_lock = threading.Lock()

# Cached metric instruments (created once, reused on every call)
_token_counter: Any = None
_retrieval_hist: Any = None
_retrieval_counter: Any = None


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
            tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT)))
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
def trace_rag_pipeline(
    query: str,
    request_id: str | None = None,
) -> Generator[dict[str, Any], None, None]:
    """Parent span wrapping the full RAG pipeline.

    The yielded *ctx* dict contains a ``timings`` sub-dict that child
    ``trace_stage`` calls populate automatically, giving a per-stage
    latency breakdown (embed, search, rerank, generate, guardrails, …).

    *request_id* (if provided) is attached as ``http.request.id`` so
    frontend-generated X-Request-IDs can be correlated end-to-end.
    """
    ctx: dict[str, Any] = {"timings": {}, "request_id": request_id}

    if _tracer is None:
        yield ctx
        return

    attributes = {
        "gen_ai.system": "ura-chatbot",
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": GEN_AI_MODEL,
        # log length, not content (PII safety)
        "gen_ai.prompt.length": len(query),
    }
    if request_id:
        attributes["http.request.id"] = request_id

    with _tracer.start_as_current_span("rag.pipeline", attributes=attributes) as span:
        ctx["span"] = span
        yield ctx
        # Record per-stage timings on the parent span
        for stage, ms in ctx["timings"].items():
            span.set_attribute(f"rag.stage.{stage}.duration_ms", ms)
        if "faithfulness" in ctx:
            span.set_attribute("gen_ai.faithfulness_score", ctx["faithfulness"])
        if "num_sources" in ctx:
            span.set_attribute("rag.retrieval.num_results", ctx["num_sources"])
        if "locale" in ctx:
            span.set_attribute("gen_ai.request.locale", ctx["locale"])
        if "input_tokens" in ctx:
            span.set_attribute("gen_ai.usage.input_tokens", ctx["input_tokens"])
        if "output_tokens" in ctx:
            span.set_attribute("gen_ai.usage.output_tokens", ctx["output_tokens"])


@contextmanager
def trace_stage(
    name: str,
    attributes: dict[str, Any] | None = None,
    timings: dict[str, float] | None = None,
) -> Generator[None, None, None]:
    """Child span for a specific RAG stage with automatic latency recording.

    If *timings* dict is provided, the stage duration (ms) is recorded into it
    under the key *name*, enabling per-stage latency breakdown in logs/metrics.
    """
    import time as _time

    t0 = _time.perf_counter()

    if _tracer is None:
        try:
            yield
        finally:
            if timings is not None:
                timings[name] = round((_time.perf_counter() - t0) * 1000, 2)
        return

    with _tracer.start_as_current_span(f"rag.{name}", attributes=attributes or {}) as span:
        try:
            yield
        finally:
            duration_ms = round((_time.perf_counter() - t0) * 1000, 2)
            span.set_attribute(f"rag.{name}.duration_ms", duration_ms)
            if timings is not None:
                timings[name] = duration_ms


# ---------------------------------------------------------------------------
# LLM operation span (gen_ai.operation.name = chat)
# ---------------------------------------------------------------------------
@contextmanager
def trace_llm_operation(
    model_id: str,
    operation: str = "chat",
) -> Generator[dict[str, Any], None, None]:
    """Child span wrapping a single LLM inference call.

    Attributes follow the stable GenAI semantic conventions (2025):
    gen_ai.operation.name, gen_ai.request.model, gen_ai.response.model,
    gen_ai.usage.input_tokens, gen_ai.usage.output_tokens.
    """
    ctx: dict[str, Any] = {}
    if _tracer is None:
        yield ctx
        return

    with _tracer.start_as_current_span(
        f"gen_ai.{operation}",
        attributes={
            "gen_ai.system": "ura-chatbot",
            "gen_ai.operation.name": operation,
            "gen_ai.request.model": model_id,
        },
    ) as span:
        ctx["span"] = span
        yield ctx
        if "input_tokens" in ctx:
            span.set_attribute("gen_ai.usage.input_tokens", ctx["input_tokens"])
        if "output_tokens" in ctx:
            span.set_attribute("gen_ai.usage.output_tokens", ctx["output_tokens"])
        if "response_model" in ctx:
            span.set_attribute("gen_ai.response.model", ctx["response_model"])
        if "finish_reason" in ctx:
            span.set_attribute("gen_ai.response.finish_reasons", [ctx["finish_reason"]])


# ---------------------------------------------------------------------------
# Metric helpers (GenAI semantic conventions)
# ---------------------------------------------------------------------------
def record_token_usage(input_tokens: int, output_tokens: int) -> None:
    """Record ``gen_ai.client.token.usage`` counter (stable 2025 semconv).

    The stable attribute is ``gen_ai.token.type`` with values
    ``input``/``output`` (matching ``gen_ai.usage.input_tokens`` /
    ``gen_ai.usage.output_tokens`` span attributes).
    """
    global _token_counter
    if _meter is None:
        return
    try:
        if _token_counter is None:
            _token_counter = _meter.create_counter(
                "gen_ai.client.token.usage",
                unit="{token}",
                description="Tokens consumed by GenAI operations",
            )
        _token_counter.add(input_tokens, {"gen_ai.token.type": "input"})
        _token_counter.add(output_tokens, {"gen_ai.token.type": "output"})
    except Exception:
        logger.debug("Failed to record token usage", exc_info=True)


def record_retrieval_metrics(
    num_results: int,
    latency_ms: float,
    faithfulness: float = 0.0,
) -> None:
    """Record retrieval-stage metrics."""
    global _retrieval_hist, _retrieval_counter
    if _meter is None:
        return
    try:
        if _retrieval_hist is None:
            _retrieval_hist = _meter.create_histogram(
                "gen_ai.retrieval.duration",
                unit="ms",
                description="Retrieval latency in milliseconds",
            )
        _retrieval_hist.record(latency_ms)

        if _retrieval_counter is None:
            _retrieval_counter = _meter.create_counter(
                "gen_ai.retrieval.results",
                description="Number of passages returned",
            )
        _retrieval_counter.add(num_results)
    except Exception:
        logger.debug("Failed to record retrieval metrics", exc_info=True)


# ---------------------------------------------------------------------------
# Phase 23 — Voice-specific tracing
# ---------------------------------------------------------------------------

_voice_session_hist = None
_voice_asr_hist = None
_voice_tts_hist = None


@contextmanager
def trace_voice_session(session_id: str):
    """Top-level span for one streaming voice WebSocket session."""
    if _tracer is None:
        ctx = {"timings": {}}
        yield ctx
        return
    with _tracer.start_as_current_span(
        "voice.session",
        attributes={
            "voice.session_id": session_id,
        },
    ):
        ctx = {"timings": {}}
        yield ctx


@contextmanager
def trace_voice_stage(name: str, ctx: dict | None = None):
    """Child span for a specific voice pipeline stage.

    Supported names: voice.vad, voice.streaming_asr, voice.streaming_tts,
    voice.barge_in, voice.consent_check.
    """
    t0 = time.perf_counter()
    if _tracer is None:
        yield
        if ctx and "timings" in ctx:
            ctx["timings"][name] = round((time.perf_counter() - t0) * 1000, 1)
        return
    with _tracer.start_as_current_span(f"voice.{name}"):
        yield
        duration = round((time.perf_counter() - t0) * 1000, 1)
        if ctx and "timings" in ctx:
            ctx["timings"][name] = duration


def record_voice_metrics(
    asr_latency_ms: float = 0,
    tts_first_chunk_ms: float = 0,
    session_duration_s: float = 0,
) -> None:
    """Record voice-specific histograms."""
    global _voice_session_hist, _voice_asr_hist, _voice_tts_hist
    if _meter is None:
        return
    try:
        if _voice_asr_hist is None:
            _voice_asr_hist = _meter.create_histogram(
                "voice.asr.duration", unit="ms",
                description="Streaming ASR latency",
            )
        if _voice_tts_hist is None:
            _voice_tts_hist = _meter.create_histogram(
                "voice.tts.first_chunk", unit="ms",
                description="Time-to-first-TTS-audio-byte",
            )
        if _voice_session_hist is None:
            _voice_session_hist = _meter.create_histogram(
                "voice.session.duration", unit="s",
                description="Voice WebSocket session duration",
            )
        if asr_latency_ms > 0:
            _voice_asr_hist.record(asr_latency_ms)
        if tts_first_chunk_ms > 0:
            _voice_tts_hist.record(tts_first_chunk_ms)
        if session_duration_s > 0:
            _voice_session_hist.record(session_duration_s)
    except Exception:
        logger.debug("Failed to record voice metrics", exc_info=True)

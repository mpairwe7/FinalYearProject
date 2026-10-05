"""OpenTelemetry tracing and GenAI telemetry for the URA Chatbot.

Opt-in with ``OTEL_ENABLED=true``. When enabled, :func:`init_tracing`:

* sets a tracer, meter and logger provider exporting OTLP to
  ``OTEL_EXPORTER_OTLP_ENDPOINT`` (an OpenTelemetry Collector in the
  monitoring profile), with ``service.name``, ``service.version``,
  ``deployment.environment.name`` and a per-worker ``service.instance.id``;
* instruments FastAPI (one server span per request, inbound W3C
  ``traceparent`` honoured) and httpx (client spans, ``traceparent``
  injected into calls to vLLM, Qdrant, Sunbird, Cloudflare and MCP servers);
* bridges Python logging to OTLP so log records carry the trace they belong
  to (``OTEL_LOGS_EXPORTER=none`` keeps logs on stdout only).

Sampling follows the standard ``OTEL_TRACES_SAMPLER`` /
``OTEL_TRACES_SAMPLER_ARG`` variables (default: parent-based, always on).

GenAI attributes follow the OpenTelemetry GenAI semantic conventions. Those
conventions are still at **Development** status — since June 2026 they live in
their own repository (open-telemetry/semantic-conventions-genai) — so the
names here may need to move with them:

* the turn is an ``invoke_agent ura-assistant`` span;
* each model call is ``{gen_ai.operation.name} {gen_ai.request.model}``
  (``chat Sunbird/Sunflower-14B-FP8``) with ``gen_ai.provider.name``,
  ``gen_ai.usage.input_tokens`` / ``output_tokens`` taken from the provider's
  ``usage`` block, ``gen_ai.response.finish_reasons`` and ``error.type``;
* each tool call is ``execute_tool {gen_ai.tool.name}``;
* scores (faithfulness, the response judge) are ``gen_ai.evaluation.result``
  events on the span they evaluate;
* metrics ``gen_ai.client.operation.duration`` (s) and
  ``gen_ai.client.token.usage`` ({token}) are histograms.

LLM calls are also counted on ``/metrics`` (``ura_llm_*``) whether or not
OpenTelemetry is on: Prometheus is the always-on metrics path, OTLP the
optional trace/log path. Prompt and completion text are never recorded.
"""

from __future__ import annotations

import contextvars
import logging
import os
import socket
import threading
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping

logger = logging.getLogger(__name__)

OTEL_ENABLED = os.getenv("OTEL_ENABLED", "false").lower() == "true"
OTEL_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "ura-chatbot-api")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
GEN_AI_MODEL = os.getenv("LLM_MODEL", "Sunbird/Sunflower-14B-FP8")
#: The assistant as a GenAI agent (``gen_ai.agent.name``).
AGENT_NAME = "ura-assistant"
_EXCLUDED_URLS = "health,ready,metrics"

_tracer: Any = None
_meter: Any = None
_init_lock = threading.Lock()
_instruments: dict[str, Any] = {}

# Token usage of every model call in the current turn. A turn may call the
# model several times (generation, translation, the response judge); the
# audit row and the turn span want the total.
_TURN_USAGE: contextvars.ContextVar[dict[str, int] | None] = contextvars.ContextVar(
    "turn_llm_usage", default=None
)


def _resource_attributes() -> dict[str, str]:
    return {
        "service.name": OTEL_SERVICE_NAME,
        "service.version": os.getenv("APP_VERSION", ""),
        "service.instance.id": f"{socket.gethostname()}-{os.getpid()}",
        "deployment.environment.name": os.getenv("APP_ENV", "development"),
    }


def init_tracing(app: Any = None) -> None:
    """Initialise OpenTelemetry for this worker (no-op unless ``OTEL_ENABLED``).

    Thread-safe and idempotent. *app* is the FastAPI application to instrument.
    """
    global _tracer, _meter

    with _init_lock:
        if _tracer is not None:
            return
        if not OTEL_ENABLED:
            logger.info("OpenTelemetry disabled (set OTEL_ENABLED=true to enable)")
            return
        try:
            from opentelemetry import metrics, trace
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            resource = Resource.create(_resource_attributes())

            tracer_provider = TracerProvider(resource=resource)
            tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT)))
            trace.set_tracer_provider(tracer_provider)
            _tracer = trace.get_tracer("ura.chatbot.genai", os.getenv("APP_VERSION") or None)

            reader = PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=OTEL_ENDPOINT), export_interval_millis=60_000
            )
            metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))
            _meter = metrics.get_meter("ura.chatbot.genai", os.getenv("APP_VERSION") or None)

            _init_log_export(resource)
            _instrument_libraries(app, tracer_provider)
            logger.info("OpenTelemetry initialised (endpoint=%s)", OTEL_ENDPOINT)
        except ImportError:
            logger.warning("OpenTelemetry SDK not installed; tracing disabled")
        except Exception:
            logger.exception("Failed to initialise OpenTelemetry")


def _init_log_export(resource: Any) -> None:
    """Send Python log records over OTLP too, unless ``OTEL_LOGS_EXPORTER=none``."""
    if os.getenv("OTEL_LOGS_EXPORTER", "otlp").lower() == "none":
        return
    try:
        from opentelemetry._logs import set_logger_provider
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    except ImportError:
        logger.warning("OpenTelemetry logs SDK unavailable; logs stay on stdout only")
        return
    provider = LoggerProvider(resource=resource)
    provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(endpoint=OTEL_ENDPOINT)))
    set_logger_provider(provider)
    handler = LoggingHandler(level=logging.INFO, logger_provider=provider)
    # The redaction filter guards OTLP the same way it guards stdout.
    from .logging_config import PIISanitizingFilter, RequestContextFilter

    handler.addFilter(RequestContextFilter())
    handler.addFilter(PIISanitizingFilter())
    logging.getLogger().addHandler(handler)


def _instrument_libraries(app: Any, tracer_provider: Any) -> None:
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument(tracer_provider=tracer_provider)
    except ImportError:
        logger.warning("opentelemetry-instrumentation-httpx missing; outbound calls are not traced")
    if app is None:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(
            app, tracer_provider=tracer_provider, excluded_urls=_EXCLUDED_URLS
        )
    except ImportError:
        logger.warning("opentelemetry-instrumentation-fastapi missing; requests have no server span")


def is_enabled() -> bool:
    return _tracer is not None


# ---------------------------------------------------------------------------
# Span helpers
# ---------------------------------------------------------------------------
@contextmanager
def trace_rag_pipeline(
    query: str,
    request_id: str | None = None,
) -> Generator[dict[str, Any], None, None]:
    """The turn: an ``invoke_agent ura-assistant`` span over the RAG pipeline.

    The yielded *ctx* dict collects per-stage ``timings`` (filled by
    :func:`trace_stage`) and other facts the pipeline learns along the way.
    The query is recorded by length only.
    """
    ctx: dict[str, Any] = {"timings": {}, "request_id": request_id}
    previous_usage = _TURN_USAGE.get()
    if previous_usage is None:
        _TURN_USAGE.set({"input_tokens": 0, "output_tokens": 0, "calls": 0})
    try:
        if _tracer is None:
            yield ctx
            return
        attributes = {
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": AGENT_NAME,
            "gen_ai.request.model": GEN_AI_MODEL,
            "ura.query.chars": len(query),
        }
        if request_id:
            attributes["ura.request_id"] = request_id
        with _tracer.start_as_current_span(f"invoke_agent {AGENT_NAME}", attributes=attributes) as span:
            ctx["span"] = span
            yield ctx
            for stage, ms in ctx["timings"].items():
                span.set_attribute(f"rag.stage.{stage}.duration_ms", ms)
            if "num_sources" in ctx:
                span.set_attribute("rag.retrieval.num_results", ctx["num_sources"])
            if "locale" in ctx:
                span.set_attribute("ura.locale", ctx["locale"])
            usage = _TURN_USAGE.get() or {}
            if usage.get("calls"):
                span.set_attribute("gen_ai.usage.input_tokens", usage["input_tokens"])
                span.set_attribute("gen_ai.usage.output_tokens", usage["output_tokens"])
    finally:
        _TURN_USAGE.set(previous_usage)


@contextmanager
def turn_usage_scope() -> Generator[dict[str, int], None, None]:
    """Collect the token usage of every model call made inside the block.

    Calls made from threads started with ``asyncio.to_thread`` count too: the
    copied context holds the same dict. Restores the previous value with
    ``set`` rather than a token reset, so it is safe across an async
    generator that may be finalised from another context.
    """
    usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
    previous = _TURN_USAGE.get()
    _TURN_USAGE.set(usage)
    try:
        yield usage
    finally:
        _TURN_USAGE.set(previous)


def usage_summary(usage: Mapping[str, int] | None) -> dict[str, Any] | None:
    """``{input_tokens, output_tokens, source}`` for an audit row, or ``None``."""
    if not usage or not usage.get("calls"):
        return None
    return {
        "input_tokens": int(usage["input_tokens"]),
        "output_tokens": int(usage["output_tokens"]),
        "source": "provider",
    }


@contextmanager
def trace_stage(
    name: str,
    attributes: dict[str, Any] | None = None,
    timings: dict[str, float] | None = None,
) -> Generator[None, None, None]:
    """Child span ``rag.<name>`` for one pipeline stage, timing it into *timings*."""
    t0 = time.perf_counter()
    if _tracer is None:
        try:
            yield
        finally:
            if timings is not None:
                timings[name] = round((time.perf_counter() - t0) * 1000, 2)
        return
    with _tracer.start_as_current_span(f"rag.{name}", attributes=attributes or {}) as span:
        try:
            yield
        finally:
            duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            span.set_attribute(f"rag.{name}.duration_ms", duration_ms)
            if timings is not None:
                timings[name] = duration_ms


class LLMCall:
    """What one model call reported; filled in by the caller inside :func:`llm_call`."""

    __slots__ = (
        "error_type",
        "finish_reason",
        "first_token_at",
        "input_tokens",
        "output_tokens",
        "response_model",
    )

    def __init__(self) -> None:
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.finish_reason: str = ""
        self.response_model: str = ""
        self.first_token_at: float | None = None
        self.error_type: str = ""

    def fail(self, error_type: str) -> None:
        """Mark the call failed when the caller recovers instead of raising."""
        self.error_type = error_type

    def response(self, payload: Mapping[str, Any] | None) -> None:
        """Read ``usage``, ``model`` and the finish reason from an OpenAI-style payload."""
        if not payload:
            return
        usage = payload.get("usage") or {}
        if isinstance(usage, dict):
            if usage.get("prompt_tokens") is not None:
                self.input_tokens = int(usage["prompt_tokens"])
            if usage.get("completion_tokens") is not None:
                self.output_tokens = int(usage["completion_tokens"])
        self.response_model = str(payload.get("model") or self.response_model)
        choices = payload.get("choices") or []
        if choices and isinstance(choices[0], dict) and choices[0].get("finish_reason"):
            self.finish_reason = str(choices[0]["finish_reason"])

    def usage(self, input_tokens: Any, output_tokens: Any) -> None:
        if input_tokens is not None:
            self.input_tokens = int(input_tokens)
        if output_tokens is not None:
            self.output_tokens = int(output_tokens)

    def first_token(self) -> None:
        if self.first_token_at is None:
            self.first_token_at = time.perf_counter()


@contextmanager
def llm_call(
    operation: str,
    model: str,
    provider: str,
    *,
    streaming: bool = False,
) -> Generator[LLMCall, None, None]:
    """Span, metrics and turn usage for one model call.

    Records on exit — whatever happened — ``ura_llm_requests_total``,
    ``ura_llm_request_duration_seconds``, ``ura_llm_tokens_total`` and, for
    streams, ``ura_llm_time_to_first_token_seconds``; plus the OTel
    ``gen_ai.client.*`` histograms when OpenTelemetry is on. An exception is
    re-raised after being recorded as ``error.type``.
    """
    call = LLMCall()
    started = time.perf_counter()
    error_type = ""
    name = f"{operation} {model}"
    attributes = {
        "gen_ai.operation.name": operation,
        "gen_ai.provider.name": provider,
        "gen_ai.request.model": model,
    }
    # A stream is consumed token by token, often from other threads, so its
    # span is never made current: attaching it to one context and detaching it
    # from another would fail. Its outbound HTTP span is then a sibling, not a
    # child — the trade for not corrupting the context.
    span_cm = None
    span = None
    if _tracer is not None:
        if streaming:
            span = _tracer.start_span(name, attributes=attributes)
        else:
            span_cm = _tracer.start_as_current_span(name, attributes=attributes)
            span = span_cm.__enter__()
    try:
        yield call
    except GeneratorExit:
        error_type = "cancelled"
        raise
    except BaseException as exc:
        error_type = type(exc).__name__
        raise
    finally:
        duration = time.perf_counter() - started
        error_type = error_type or call.error_type
        _finish_llm_call(call, operation, model, provider, streaming, duration, started, error_type, span)
        if span_cm is not None:
            span_cm.__exit__(None, None, None)
        elif span is not None:
            span.end()


def _finish_llm_call(
    call: LLMCall,
    operation: str,
    model: str,
    provider: str,
    streaming: bool,
    duration: float,
    started: float,
    error_type: str,
    span: Any,
) -> None:
    try:
        from .analytics import metrics

        labels = {"provider": provider, "model": model, "operation": operation}
        status = "error" if error_type and error_type != "cancelled" else (error_type or "ok")
        metrics.inc("llm_requests_total", labels={**labels, "status": status})
        metrics.observe("llm_request_duration_seconds", duration, labels=labels)
        if call.input_tokens is not None:
            metrics.inc("llm_tokens_total", call.input_tokens, labels={**labels, "type": "input"})
        if call.output_tokens is not None:
            metrics.inc("llm_tokens_total", call.output_tokens, labels={**labels, "type": "output"})
        if streaming and call.first_token_at is not None:
            metrics.observe(
                "llm_time_to_first_token_seconds", call.first_token_at - started, labels=labels
            )
        usage = _TURN_USAGE.get()
        if usage is not None and call.input_tokens is not None:
            usage["input_tokens"] += call.input_tokens
            usage["output_tokens"] += call.output_tokens or 0
            usage["calls"] += 1
        _record_otel_llm_metrics(call, operation, model, provider, duration, error_type)
        if span is not None:
            if call.input_tokens is not None:
                span.set_attribute("gen_ai.usage.input_tokens", call.input_tokens)
            if call.output_tokens is not None:
                span.set_attribute("gen_ai.usage.output_tokens", call.output_tokens)
            if call.response_model:
                span.set_attribute("gen_ai.response.model", call.response_model)
            if call.finish_reason:
                span.set_attribute("gen_ai.response.finish_reasons", [call.finish_reason])
            if error_type:
                span.set_attribute("error.type", error_type)
                if error_type != "cancelled":
                    from opentelemetry.trace import Status, StatusCode

                    span.set_status(Status(StatusCode.ERROR))
    except Exception:
        logger.debug("LLM call telemetry failed", exc_info=True)


def _instrument(kind: str, name: str, unit: str, description: str, **kwargs: Any) -> Any:
    key = f"{kind}:{name}"
    if key not in _instruments:
        factory = _meter.create_histogram if kind == "histogram" else _meter.create_counter
        _instruments[key] = factory(name, unit=unit, description=description, **kwargs)
    return _instruments[key]


def _record_otel_llm_metrics(
    call: LLMCall, operation: str, model: str, provider: str, duration: float, error_type: str
) -> None:
    if _meter is None:
        return
    attrs = {
        "gen_ai.operation.name": operation,
        "gen_ai.provider.name": provider,
        "gen_ai.request.model": model,
    }
    if call.response_model:
        attrs["gen_ai.response.model"] = call.response_model
    duration_attrs = {**attrs, "error.type": error_type} if error_type else attrs
    _instrument(
        "histogram",
        "gen_ai.client.operation.duration",
        "s",
        "GenAI operation duration",
        explicit_bucket_boundaries_advisory=[0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1.28, 2.56, 5.12, 10.24, 20.48, 40.96, 81.92],
    ).record(duration, duration_attrs)
    usage_hist = _instrument(
        "histogram",
        "gen_ai.client.token.usage",
        "{token}",
        "Number of input and output tokens used",
        explicit_bucket_boundaries_advisory=[1, 4, 16, 64, 256, 1024, 4096, 16384, 65536, 262144, 1048576],
    )
    if call.input_tokens is not None:
        usage_hist.record(call.input_tokens, {**attrs, "gen_ai.token.type": "input"})
    if call.output_tokens is not None:
        usage_hist.record(call.output_tokens, {**attrs, "gen_ai.token.type": "output"})


@contextmanager
def trace_tool_call(
    tool_name: str, call_id: str = "", tool_type: str = "function"
) -> Generator[dict[str, bool], None, None]:
    """``execute_tool {tool_name}`` span plus ``ura_tool_calls_total`` / duration metrics.

    Yields ``{"ok": True}``; a caller whose tool reports failure without
    raising sets ``ok`` to False so it is counted as an error.
    """
    started = time.perf_counter()
    outcome = {"ok": True}
    span_cm = (
        _tracer.start_as_current_span(
            f"execute_tool {tool_name}",
            attributes={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": tool_name,
                "gen_ai.tool.call.id": call_id or tool_name,
                "gen_ai.tool.type": tool_type,
            },
        )
        if _tracer is not None
        else None
    )
    span = span_cm.__enter__() if span_cm is not None else None
    error_type = ""
    try:
        yield outcome
    except BaseException as exc:
        error_type = type(exc).__name__
        raise
    finally:
        status = "ok" if outcome["ok"] and not error_type else "error"
        if span is not None and status == "error":
            span.set_attribute("error.type", error_type or "tool_error")
        try:
            from .analytics import metrics

            metrics.inc("tool_calls_total", labels={"tool": tool_name, "status": status})
            metrics.observe(
                "tool_call_duration_seconds", time.perf_counter() - started, labels={"tool": tool_name}
            )
        except Exception:
            logger.debug("Tool call metrics failed", exc_info=True)
        if span_cm is not None:
            span_cm.__exit__(None, None, None)


def record_evaluation(name: str, score: float | None, label: str = "", explanation: str = "") -> None:
    """Attach a ``gen_ai.evaluation.result`` event to the current span."""
    if _tracer is None or score is None:
        return
    try:
        from opentelemetry import trace

        attributes: dict[str, Any] = {
            "gen_ai.evaluation.name": name,
            "gen_ai.evaluation.score.value": float(score),
        }
        if label:
            attributes["gen_ai.evaluation.score.label"] = label
        if explanation:
            attributes["gen_ai.evaluation.explanation"] = explanation[:256]
        trace.get_current_span().add_event("gen_ai.evaluation.result", attributes=attributes)
    except Exception:
        logger.debug("Evaluation event failed", exc_info=True)


def record_retrieval_metrics(num_results: int, latency_ms: float) -> None:
    """Retrieval latency and hit count on ``/metrics``."""
    from .analytics import metrics

    metrics.observe("retrieval_duration_ms", latency_ms)
    metrics.observe("retrieval_results", float(num_results))


# ---------------------------------------------------------------------------
# W3C Trace Context helpers
# ---------------------------------------------------------------------------
def get_current_trace_id() -> str:
    """Return the active 32-char hex trace_id or empty string."""
    try:
        from opentelemetry import trace

        ctx = trace.get_current_span().get_span_context()
        if ctx and ctx.is_valid:
            return f"{ctx.trace_id:032x}"
    except Exception as exc:
        logger.debug("Failed to resolve active trace_id: %s", exc)
    return ""


def get_current_span_id() -> str:
    """Return the active 16-char hex span_id or empty string."""
    try:
        from opentelemetry import trace

        ctx = trace.get_current_span().get_span_context()
        if ctx and ctx.is_valid:
            return f"{ctx.span_id:016x}"
    except Exception as exc:
        logger.debug("Failed to resolve active span_id: %s", exc)
    return ""


def current_traceresponse() -> str:
    """The W3C ``traceresponse`` value for the active server span, or ``""``.

    Lets a client (or the person reading a bug report) find this request's
    trace. Empty when tracing is off or no span is active — the API never
    invents a trace id that no backend has recorded.
    """
    if _tracer is None:
        return ""
    try:
        from opentelemetry import trace

        ctx = trace.get_current_span().get_span_context()
        if ctx and ctx.is_valid:
            return f"00-{ctx.trace_id:032x}-{ctx.span_id:016x}-{int(ctx.trace_flags):02x}"
    except Exception as exc:
        logger.debug("traceresponse unavailable: %s", exc)
    return ""

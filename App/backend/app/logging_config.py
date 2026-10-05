"""Structured logging: one JSON line per record, correlated and scrubbed.

:func:`install_logging` puts one handler on the **root** logger, so the API's
own loggers, uvicorn's and every library's records come out in the same
format. In JSON mode (``LOG_FORMAT=json``, or ``APP_ENV=production`` unless
``LOG_FORMAT=text``) each line carries:

* ``timestamp`` (RFC 3339, UTC), ``level`` / ``severity_text`` and the
  OpenTelemetry ``severity_number``;
* ``trace_id`` / ``span_id`` / ``trace_flags`` of the active span, and the
  request's ``request_id`` (the validated ``X-Request-ID``) on every record
  written while serving that request — not only the access line;
* ``attributes``: everything passed with ``extra=`` (structured event
  fields such as ``event`` or ``injection_detected``), scrubbed like the
  message;
* ``exception``: the traceback, also scrubbed — a stack trace carries
  user input as easily as a message does.

Redaction uses :func:`guardrails.redact_pii_text` (TINs, phone numbers,
e-mail addresses, NINs and card numbers). It is pattern-based: it cannot
recognise a bare name, so code must still never log free text it did not
need to.
"""

from __future__ import annotations

import contextvars
import datetime
import json
import logging
import os
import traceback
from typing import Any

from .guardrails import redact_pii_text
from .tracing import get_current_span_id, get_current_trace_id

#: The current request's validated ``X-Request-ID``; set by the request
#: middleware, read by :class:`RequestContextFilter`.
REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")

_HANDLER_MARK = "_ura_log_handler"
_SEVERITY_NUMBER = {
    logging.DEBUG: 5,
    logging.INFO: 9,
    logging.WARNING: 13,
    logging.ERROR: 17,
    logging.CRITICAL: 21,
}
# Attributes every LogRecord has; anything else arrived through ``extra=``.
_STANDARD_ATTRS = frozenset(
    vars(logging.LogRecord("x", logging.INFO, "x", 0, "", None, None)).keys()
) | {"message", "asctime", "request_id", "otelSpanID", "otelTraceID", "otelTraceSampled", "otelServiceName"}
# Libraries that log every request at INFO; their WARNINGs still come through.
_NOISY_LOGGERS = ("httpx", "httpcore", "urllib3", "hpack", "multipart")


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        return redact_pii_text(value)
    if isinstance(value, dict):
        return {key: _scrub(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_scrub(item) for item in value)
    return value


class RequestContextFilter(logging.Filter):
    """Stamp the current request's id on every record written while serving it."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "request_id", None):
            request_id = REQUEST_ID.get()
            if request_id:
                record.request_id = request_id
        return True


class PIISanitizingFilter(logging.Filter):
    """Redact PII from the message, its arguments, ``extra`` fields and tracebacks."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_pii_text(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {key: _scrub(value) for key, value in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(_scrub(arg) for arg in record.args)
        for key, value in list(vars(record).items()):
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                setattr(record, key, _scrub(value))
        if record.exc_info and not record.exc_text:
            # Formatters reuse exc_text when it is set, so every handler
            # (stdout and OTLP) emits the scrubbed traceback.
            record.exc_text = redact_pii_text("".join(traceback.format_exception(*record.exc_info)).strip())
        return True


class StructuredJsonFormatter(logging.Formatter):
    """Single-line JSON using OpenTelemetry log field names where they exist."""

    def __init__(self, service_name: str = "ura-chatbot-api") -> None:
        super().__init__()
        self.service_name = service_name
        self.service_version = os.getenv("APP_VERSION", "")
        self.environment = os.getenv("APP_ENV", "development")

    def format(self, record: logging.LogRecord) -> str:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)

        log_data: dict[str, Any] = {
            "timestamp": datetime.datetime.fromtimestamp(record.created, tz=datetime.timezone.utc).isoformat(),  # noqa: UP017
            "level": record.levelname,
            "severity_text": record.levelname,
            "severity_number": _SEVERITY_NUMBER.get(record.levelno, 9),
            "logger": record.name,
            "message": message,
            "service": self.service_name,
            "service.version": self.service_version or os.getenv("APP_VERSION", ""),
            "deployment.environment": self.environment,
        }

        trace_id = get_current_trace_id()
        if trace_id:
            log_data["trace_id"] = trace_id
            log_data["span_id"] = get_current_span_id()

        request_id = getattr(record, "request_id", None)
        if request_id:
            log_data["request_id"] = request_id

        attributes = {
            key: value
            for key, value in vars(record).items()
            if key not in _STANDARD_ATTRS and not key.startswith("_")
        }
        if attributes:
            log_data["attributes"] = attributes

        if record.exc_info or record.exc_text:
            log_data["exception"] = record.exc_text or "".join(
                traceback.format_exception(*record.exc_info)
            ).strip()

        return json.dumps(log_data, default=str)


def _use_json() -> bool:
    log_format = os.getenv("LOG_FORMAT", "").lower()
    app_env = os.getenv("APP_ENV", "development").lower()
    return log_format == "json" or (log_format != "text" and app_env == "production")


def configure_logging() -> logging.Handler:
    """Build the stdout handler for the configured ``LOG_FORMAT`` / ``LOG_LEVEL``."""
    log_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    handler = logging.StreamHandler()
    handler.addFilter(RequestContextFilter())
    handler.addFilter(PIISanitizingFilter())
    if _use_json():
        handler.setFormatter(
            StructuredJsonFormatter(service_name=os.getenv("OTEL_SERVICE_NAME", "ura-chatbot-api"))
        )
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.setLevel(log_level)
    setattr(handler, _HANDLER_MARK, True)
    return handler


def install_logging() -> logging.Handler:
    """Route every logger through one structured handler; idempotent per process.

    The handler goes on the root logger. In JSON mode it also replaces the
    plain-text handlers uvicorn installs on its own loggers, so access and
    error lines are JSON too. Library loggers that narrate every HTTP call at
    INFO are held at WARNING.
    """
    root = logging.getLogger()
    for existing in root.handlers:
        if getattr(existing, _HANDLER_MARK, False):
            return existing
    handler = configure_logging()
    root.addHandler(handler)
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
    app_logger = logging.getLogger("app")
    app_logger.setLevel(level)
    app_logger.propagate = True
    if _use_json():
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            uvicorn_logger = logging.getLogger(name)
            uvicorn_logger.handlers = [handler]
            uvicorn_logger.propagate = False
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    return handler

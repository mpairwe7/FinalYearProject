"""Production structured logging with OpenTelemetry trace correlation & PII scrubbing (2026)."""

from __future__ import annotations

import datetime
import json
import logging
import os
import traceback
from typing import Any

from .guardrails import redact_pii_text
from .tracing import get_current_span_id, get_current_trace_id


class PIISanitizingFilter(logging.Filter):
    """Filter that redacts PII patterns from log messages and arguments."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_pii_text(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: (redact_pii_text(v) if isinstance(v, str) else v)
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    redact_pii_text(a) if isinstance(a, str) else a
                    for a in record.args
                )
        return True


class StructuredJsonFormatter(logging.Formatter):
    """Format logs as single-line JSON adhering to OpenTelemetry Log Data Model."""

    def __init__(self, service_name: str = "ura-chatbot-api") -> None:
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)

        log_data: dict[str, Any] = {
            "timestamp": datetime.datetime.fromtimestamp(
                record.created, tz=datetime.timezone.utc  # noqa: UP017
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "service": self.service_name,
        }

        trace_id = get_current_trace_id()
        span_id = get_current_span_id()
        if trace_id:
            log_data["trace_id"] = trace_id
        if span_id:
            log_data["span_id"] = span_id

        if hasattr(record, "request_id"):
            log_data["request_id"] = record.request_id

        if record.exc_info:
            log_data["exception"] = "".join(traceback.format_exception(*record.exc_info)).strip()

        return json.dumps(log_data, default=str)


def configure_logging() -> logging.Handler:
    """Configure root and app loggers according to LOG_FORMAT and LOG_LEVEL."""
    log_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    log_format = os.getenv("LOG_FORMAT", "").lower()
    app_env = os.getenv("APP_ENV", "development").lower()

    use_json = log_format == "json" or (log_format != "text" and app_env == "production")

    handler = logging.StreamHandler()
    handler.addFilter(PIISanitizingFilter())

    if use_json:
        handler.setFormatter(
            StructuredJsonFormatter(service_name=os.getenv("OTEL_SERVICE_NAME", "ura-chatbot-api"))
        )
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    handler.setLevel(log_level)
    return handler

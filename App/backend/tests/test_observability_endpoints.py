"""Logging, security events, /metrics access and the RUM endpoints.

Covers gaps G101 (structured logging that dropped request ids and extras),
G105 (security events as free text), G106 (/metrics unreadable by
Prometheus) and G107 (Web Vitals posted to a path with no handler).
"""

from __future__ import annotations

import io
import json
import logging
import os
import unittest
import unittest.mock as mock
import wave

os.environ.setdefault("LLM_ENABLED", "false")
os.environ.setdefault("SPEECH_ENABLED", "false")
os.environ.setdefault("QDRANT_ENABLED", "false")
os.environ.setdefault("ANALYTICS_BACKEND", "sqlite")
os.environ.setdefault("OTEL_ENABLED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import database as db  # noqa: E402
from app.ai_disclosure import DIGITAL_SOURCE_TYPE, is_marked, mark_wav  # noqa: E402
from app.analytics import metrics  # noqa: E402
from app.client_telemetry import normalise_route  # noqa: E402
from app.logging_config import (  # noqa: E402
    REQUEST_ID,
    PIISanitizingFilter,
    RequestContextFilter,
    StructuredJsonFormatter,
)
from app.main import app  # noqa: E402

TOKEN = "m" * 40


def setUpModule() -> None:
    db.init_db()


def _format(record: logging.LogRecord) -> dict:
    for f in (RequestContextFilter(), PIISanitizingFilter()):
        f.filter(record)
    return json.loads(StructuredJsonFormatter().format(record))


def _record(msg: str, *args: object, exc_info=None, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("app.test", logging.WARNING, __file__, 1, msg, args, exc_info)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


class StructuredLogTest(unittest.TestCase):
    def test_request_id_reaches_every_record(self) -> None:
        token = REQUEST_ID.set("req-abc123")
        try:
            line = _format(_record("inside the handler"))
        finally:
            REQUEST_ID.reset(token)
        self.assertEqual(line["request_id"], "req-abc123")
        self.assertEqual(line["severity_number"], 13)
        self.assertEqual(line["severity_text"], "WARNING")

    def test_extra_fields_are_kept_and_scrubbed(self) -> None:
        line = _format(_record("guard fired", event="pii_redacted", contact="call 0772123456"))
        self.assertEqual(line["attributes"]["event"], "pii_redacted")
        self.assertNotIn("0772123456", json.dumps(line))

    def test_traceback_is_scrubbed(self) -> None:
        try:
            raise ValueError("lookup failed for email taxpayer@example.com")
        except ValueError:
            import sys

            line = _format(_record("boom", exc_info=sys.exc_info()))
        self.assertIn("ValueError", line["exception"])
        self.assertNotIn("taxpayer@example.com", line["exception"])

    def test_install_is_idempotent_and_on_root(self) -> None:
        from app.logging_config import install_logging

        first = install_logging()
        second = install_logging()
        self.assertIs(first, second)
        self.assertIn(first, logging.getLogger().handlers)
        self.assertTrue(logging.getLogger("app").propagate)


class MetricsAccessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_scrape_token_reads_metrics(self) -> None:
        with mock.patch.dict(os.environ, {"METRICS_TOKEN": TOKEN}):
            ok = self.client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
            wrong = self.client.get("/metrics", headers={"Authorization": "Bearer nope"})
        self.assertEqual(ok.status_code, 200)
        self.assertIn("text/plain", ok.headers["content-type"])
        self.assertIn("ura_qdrant_index_fresh", ok.text)
        self.assertIn(wrong.status_code, (401, 403, 503))

    def test_no_token_configured_means_no_token_access(self) -> None:
        with mock.patch.dict(os.environ, {"METRICS_TOKEN": ""}):
            resp = self.client.get("/metrics", headers={"Authorization": "Bearer "})
        self.assertIn(resp.status_code, (401, 403, 503))


class SecurityEventTest(unittest.TestCase):
    def test_denied_metrics_scrape_is_a_logged_counted_event(self) -> None:
        client = TestClient(app)
        key = 'security_events_total{event="authn_login_fail"}'
        before = metrics.snapshot()["counters"].get(key, 0)
        with mock.patch.dict(os.environ, {"METRICS_TOKEN": TOKEN, "INDEX_API_KEY": "k" * 40}), \
             self.assertLogs("app.security", level="WARNING") as logs:
            resp = client.get("/metrics", headers={"Authorization": "Bearer not-a-jwt"})
        self.assertEqual(resp.status_code, 401)
        self.assertGreaterEqual(metrics.snapshot()["counters"].get(key, 0) - before, 1)
        record = logs.records[-1]
        self.assertEqual(record.event, "authn_login_fail")
        self.assertEqual(record.http_route, "/metrics")


class ClientTelemetryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_vitals_beacon_text_plain_is_accepted(self) -> None:
        body = json.dumps(
            {
                "metrics": [
                    {"name": "LCP", "value": 1800, "rating": "good", "route": "/c/3f2a9b1c-aaaa-bbbb", "navigation_type": "navigate"},
                    {"name": "CLS", "value": 0.02, "rating": "good", "route": "/", "navigation_type": "navigate"},
                ]
            }
        )
        resp = self.client.post("/v1/telemetry/vitals", content=body, headers={"Content-Type": "text/plain;charset=UTF-8"})
        self.assertEqual(resp.status_code, 204)
        hists = metrics.snapshot()["histograms"]
        self.assertTrue(any(k.startswith('web_vitals_ms{metric="LCP",rating="good",route="/c/:id"}') for k in hists))
        self.assertTrue(any(k.startswith("web_vitals_cls{") for k in hists))

    def test_unknown_fields_and_oversize_are_refused(self) -> None:
        extra = json.dumps({"metrics": [{"name": "LCP", "value": 1, "rating": "good", "user_id": "x"}]})
        self.assertEqual(self.client.post("/v1/telemetry/vitals", content=extra).status_code, 422)
        self.assertEqual(self.client.post("/v1/telemetry/vitals", content="x" * 20000).status_code, 413)

    def test_chunked_oversize_is_refused_without_content_length(self) -> None:
        def chunks():
            for _ in range(40):
                yield b"x" * 1024

        resp = self.client.post("/v1/telemetry/errors", content=chunks())
        self.assertEqual(resp.status_code, 413)

    def test_client_error_never_carries_a_message(self) -> None:
        ok = self.client.post(
            "/v1/telemetry/errors",
            content=json.dumps({"kind": "boundary", "name": "TypeError", "digest": "123456", "route": "/"}),
        )
        self.assertEqual(ok.status_code, 204)
        with_message = self.client.post(
            "/v1/telemetry/errors", content=json.dumps({"kind": "error", "message": "taxpayer typed this"})
        )
        self.assertEqual(with_message.status_code, 422)

    def test_routes_are_bounded(self) -> None:
        self.assertEqual(normalise_route("/staff/tickets/7d1c0e2a-1b2c-4d5e?tab=1"), "/staff/tickets/:id")
        self.assertEqual(normalise_route("/a/b/c/d/e"), "/a/b/c")
        self.assertEqual(normalise_route(""), "/")


class AiDisclosureTest(unittest.TestCase):
    @staticmethod
    def _wav(frames: bytes = b"\x01\x00" * 1600) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(frames)
        return buf.getvalue()

    def test_marked_wav_keeps_its_audio(self) -> None:
        original = self._wav()
        marked = mark_wav(original, generator="orpheus")
        self.assertTrue(is_marked(marked))
        self.assertIn(DIGITAL_SOURCE_TYPE.encode(), marked)
        self.assertEqual(int.from_bytes(marked[4:8], "little"), len(marked) - 8)
        with wave.open(io.BytesIO(marked)) as w:
            self.assertEqual(w.readframes(w.getnframes()), b"\x01\x00" * 1600)
        self.assertEqual(mark_wav(marked, generator="orpheus"), marked)  # idempotent

    def test_non_wav_is_untouched(self) -> None:
        self.assertEqual(mark_wav(b"ID3\x03fake-mp3", generator="x"), b"ID3\x03fake-mp3")
        self.assertEqual(mark_wav(b"", generator="x"), b"")


if __name__ == "__main__":
    unittest.main()

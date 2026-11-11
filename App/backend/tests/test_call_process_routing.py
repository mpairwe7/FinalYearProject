"""One call process: which role this process has and which paths are call paths.

G306 (docs/plans/receptionist-call-process-2026-11-12.md): the Space runs the
API as two processes, and nginx sends every call path to the one that owns the
in-memory call state. These tests cover the vocabulary that routing is built on.
"""

from __future__ import annotations

import logging
import os
import re
import unittest
from unittest.mock import patch

from app import process_role
from app.analytics import MetricsStore, metrics
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


def _env(**values: str):
    """The environment with the role variables set to *values* (and others removed)."""
    names = (process_role.ROLE_ENV, process_role.CALLS_URL_ENV)
    cleaned = {name: value for name, value in os.environ.items() if name not in names}
    cleaned.update(values)
    return patch.dict(os.environ, cleaned, clear=True)


class ProcessRoleTests(unittest.TestCase):
    def setUp(self) -> None:
        process_role._LOGGED_INVALID_ROLES.clear()

    def test_the_role_is_read_from_the_environment(self) -> None:
        for raw, expected in (("calls", "calls"), ("chat", "chat"), (" Calls ", "calls"), ("CHAT", "chat")):
            with self.subTest(raw=raw), _env(URA_PROCESS_ROLE=raw):
                self.assertEqual(process_role.process_role(), expected)
        with _env():
            self.assertEqual(process_role.process_role(), "")
        with _env(URA_PROCESS_ROLE="  "):
            self.assertEqual(process_role.process_role(), "")

    def test_an_unknown_role_is_unset_and_logged(self) -> None:
        with _env(URA_PROCESS_ROLE="call"), self.assertLogs("app.process_role", level="ERROR") as logs:
            self.assertEqual(process_role.process_role(), "")
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(logs.records[0].levelno, logging.ERROR)
        self.assertIn("URA_PROCESS_ROLE", logs.output[0])
        self.assertIn("call", logs.output[0])

    def test_an_unknown_role_is_logged_once_per_value(self) -> None:
        with _env(URA_PROCESS_ROLE="call"), self.assertLogs("app.process_role", level="ERROR") as logs:
            for _ in range(3):
                process_role.process_role()
        self.assertEqual(len(logs.records), 1)
        with _env(URA_PROCESS_ROLE="web"), self.assertLogs("app.process_role", level="ERROR") as logs:
            process_role.process_role()
            process_role.process_role()
        self.assertEqual(len(logs.records), 1)

    def test_call_paths(self) -> None:
        for path in (
            "/v1/calls/stream",
            "/v1/admin/calls",
            "/v1/admin/calls/",
            "/v1/admin/calls/stream",
            "/v1/admin/calls/c1/claim",
            "/v1/admin/calls/c1/audio",
            "/v1/admin/officers/presence",
            "/v1/admin/officers/me/presence",
            "/admin/calls",
            "/admin/calls/metrics",
        ):
            with self.subTest(path=path):
                self.assertTrue(process_role.is_call_path(path))
        for path in (
            "/calls",
            "/v1/chat",
            "/v1/admin/flags",
            "/v1/admin/tickets/t1/presence",
            "/v1/admin/officers",
            "/v1/calls",
            "/v1/admin/callsign",
            "/v1/admin/calls-archive",
            "/x/v1/admin/calls",
            "",
        ):
            with self.subTest(path=path):
                self.assertFalse(process_role.is_call_path(path))

    def test_every_call_route_is_a_call_path_and_every_pattern_has_a_route(self) -> None:
        from app.main import app

        call_routes: list[str] = []
        for route in app.routes:
            if not isinstance(route, (Route, WebSocketRoute)):
                continue
            path = re.sub(r"\{[^}]+\}", "x", route.path)
            if (
                path.startswith(("/v1/calls", "/v1/admin/calls", "/admin/calls"))
                or ("/officers/" in path and "presence" in path)
            ):
                call_routes.append(path)
        self.assertTrue(call_routes, "no call routes found: the walk is looking at the wrong thing")
        for path in call_routes:
            with self.subTest(path=path):
                self.assertTrue(process_role.is_call_path(path), f"{path} is a call route but not a call path")

        # The three alternatives in the pattern: every one must name a real route,
        # or the pattern has drifted from the app.
        alternatives = {"calls/stream": 0, "admin/calls": 0, "admin/officers/presence": 0}
        for path in call_routes:
            matched = process_role.CALL_PATH_RE.fullmatch(path)
            if matched is None:
                continue
            body = matched.group(2)
            if body == "calls/stream":
                alternatives["calls/stream"] += 1
            elif body.startswith("admin/calls"):
                alternatives["admin/calls"] += 1
            elif body.startswith("admin/officers/"):
                alternatives["admin/officers/presence"] += 1
        for alternative, count in alternatives.items():
            with self.subTest(alternative=alternative):
                self.assertGreater(count, 0, f"no route matches the {alternative!r} alternative")

    def test_the_two_patterns_agree(self) -> None:
        # nginx sees the /api prefix; the app does not. Same paths, same answer.
        nginx = re.compile(process_role.NGINX_CALL_LOCATION)
        for suffix in ("calls/stream", "admin/calls", "admin/calls/", "admin/calls/c1/claim",
                       "admin/officers/presence", "admin/officers/me/presence",
                       "calls", "admin/flags", "admin/officers", "admin/tickets/t1/presence"):
            with self.subTest(suffix=suffix):
                expected = process_role.is_call_path(f"/v1/{suffix}")
                self.assertEqual(bool(nginx.match(f"/v1/{suffix}")), expected)
                self.assertEqual(bool(nginx.match(f"/api/v1/{suffix}")), expected)
                self.assertEqual(bool(nginx.match(f"/api/{suffix}")), expected)

    def test_the_call_process_address(self) -> None:
        with _env():
            self.assertEqual(process_role.calls_process_url(), "http://127.0.0.1:8082")
        with _env(URA_CALLS_PROCESS_URL="  "):
            self.assertEqual(process_role.calls_process_url(), "http://127.0.0.1:8082")
        with _env(URA_CALLS_PROCESS_URL="http://calls.internal:9000/"):
            self.assertEqual(process_role.calls_process_url(), "http://calls.internal:9000")
        with _env(URA_CALLS_PROCESS_URL="http://calls.internal:9000"):
            self.assertEqual(process_role.calls_process_url(), "http://calls.internal:9000")

    def test_the_nginx_location_is_the_exact_agreed_text(self) -> None:
        self.assertEqual(
            process_role.NGINX_CALL_LOCATION,
            r"^/(api/(v1/)?|v1/)(calls/stream|admin/calls(/.*)?|admin/officers/(me/)?presence)$",
        )
        self.assertEqual(
            process_role.CALL_PATH_RE.pattern,
            r"^/(v1/)?(calls/stream|admin/calls(/.*)?|admin/officers/(me/)?presence)$",
        )


def _misrouted(kind: str) -> float:
    """The current value of ``receptionist_misrouted_total{kind=...}`` (0 until it exists)."""
    key = f'receptionist_misrouted_total{{kind="{kind}"}}'
    return float(metrics.snapshot()["counters"].get(key, 0))


class ChatRoleRefusalTests(unittest.TestCase):
    """A call request that reaches the chat process is refused, never half-served (G306)."""

    @classmethod
    def setUpClass(cls) -> None:
        import app.main

        cls.client = TestClient(app.main.app)

    def test_a_call_request_reaching_the_chat_process_is_refused_421(self) -> None:
        for method, path in (
            ("GET", "/v1/admin/calls?status=live"),
            ("POST", "/v1/admin/calls/c1/claim"),
            ("GET", "/v1/admin/calls/"),
            ("GET", "/v1/admin/officers/me/presence"),
        ):
            with self.subTest(path=path), _env(URA_PROCESS_ROLE="chat"):
                before = _misrouted("http")
                with self.assertLogs("app.process_role", level="ERROR") as logs:
                    response = self.client.request(method, path)
                self.assertEqual(response.status_code, 421)
                self.assertIn("call process", response.json()["detail"])
                self.assertEqual(response.headers["X-URA-Process"], "chat")
                self.assertEqual(_misrouted("http"), before + 1)
                # The path (not the query string) is in the log, so an operator can find the nginx rule.
                self.assertIn(path.split("?")[0], logs.output[0])
                self.assertEqual(logs.records[0].levelno, logging.ERROR)

    def test_a_call_socket_reaching_the_chat_process_is_told_and_closed_1013(self) -> None:
        with _env(URA_PROCESS_ROLE="chat"):
            before_http, before_socket = _misrouted("http"), _misrouted("websocket")
            with self.assertLogs("app.process_role", level="ERROR") as logs:
                with self.client.websocket_connect("/v1/calls/stream") as socket:
                    frame = socket.receive_json()
                    with self.assertRaises(WebSocketDisconnect) as closed:
                        socket.receive_json()
            self.assertEqual(frame["type"], "error")
            self.assertEqual(frame["code"], "unavailable")
            self.assertEqual(closed.exception.code, 1013)
            self.assertEqual(_misrouted("websocket"), before_socket + 1)
            self.assertEqual(_misrouted("http"), before_http)
            self.assertIn("/v1/calls/stream", logs.output[0])

    def test_the_staff_socket_is_refused_the_same_way(self) -> None:
        with _env(URA_PROCESS_ROLE="chat"):
            with self.client.websocket_connect("/v1/admin/calls/stream") as socket:
                frame = socket.receive_json()
                with self.assertRaises(WebSocketDisconnect) as closed:
                    socket.receive_json()
        self.assertEqual(frame["code"], "unavailable")
        self.assertEqual(closed.exception.code, 1013)

    def test_chat_is_not_refused_in_the_chat_role(self) -> None:
        with _env(URA_PROCESS_ROLE="chat"):
            before = _misrouted("http")
            response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-URA-Process"], "chat")
        self.assertEqual(_misrouted("http"), before)

    def test_the_calls_role_serves_call_paths(self) -> None:
        with _env(URA_PROCESS_ROLE="calls"):
            before = _misrouted("http")
            response = self.client.get("/v1/admin/calls")
        self.assertNotEqual(response.status_code, 421)  # 401/403 without a login is the app's own answer
        self.assertEqual(response.headers["X-URA-Process"], "calls")
        self.assertEqual(_misrouted("http"), before)

    def test_a_calls_role_socket_is_served_not_refused(self) -> None:
        with _env(URA_PROCESS_ROLE="calls"):
            before = _misrouted("websocket")
            with self.client.websocket_connect("/v1/calls/stream") as socket:
                self.assertEqual(
                    dict(socket.extra_headers or []).get(b"x-ura-process"), b"calls"
                )
        self.assertEqual(_misrouted("websocket"), before)

    def test_no_header_and_no_refusal_without_a_role(self) -> None:
        with _env():
            before = _misrouted("http")
            calls = self.client.get("/v1/admin/calls")
            health = self.client.get("/health")
        self.assertNotEqual(calls.status_code, 421)
        self.assertNotIn("X-URA-Process", calls.headers)
        self.assertNotIn("X-URA-Process", health.headers)
        self.assertEqual(_misrouted("http"), before)

    def test_a_mistyped_role_is_unset_so_no_refusal(self) -> None:
        process_role._LOGGED_INVALID_ROLES.clear()
        with _env(URA_PROCESS_ROLE="web"), self.assertLogs("app.process_role", level="ERROR"):
            response = self.client.get("/v1/admin/calls")
        self.assertNotEqual(response.status_code, 421)
        self.assertNotIn("X-URA-Process", response.headers)

    def test_both_kinds_are_declared_at_zero_when_the_middleware_is_built(self) -> None:
        # A store of its own, so no refusal made by another test can leak in: what
        # is read back is what building the middleware recorded, and nothing else.
        store = MetricsStore()
        with patch.object(process_role, "metrics", store):
            process_role.ProcessRoleMiddleware(lambda scope, receive, send: None)
            process_role.declare_metrics()  # declaring again must not count anything

            counters = store.snapshot()["counters"]
            for kind in ("http", "websocket"):
                key = f'receptionist_misrouted_total{{kind="{kind}"}}'
                self.assertIn(key, counters)
                self.assertEqual(counters[key], 0)
            text = store.to_prometheus()
            self.assertIn('ura_receptionist_misrouted_total{kind="http"} 0.0', text)
            self.assertIn('ura_receptionist_misrouted_total{kind="websocket"} 0.0', text)

            # The declared series is the one a refusal then raises, from 0 to 1.
            store.inc("receptionist_misrouted_total", labels={"kind": "http"})
            counters = store.snapshot()["counters"]
            self.assertEqual(counters['receptionist_misrouted_total{kind="http"}'], 1)
            self.assertEqual(counters['receptionist_misrouted_total{kind="websocket"}'], 0)

    def test_the_middleware_is_the_outermost_layer(self) -> None:
        import app.main

        stack = app.main.app.user_middleware
        self.assertIs(stack[0].cls, process_role.ProcessRoleMiddleware)


if __name__ == "__main__":
    unittest.main()

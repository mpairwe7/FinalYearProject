"""One call process: which role this process has and which paths are call paths.

G306 (docs/plans/receptionist-call-process-2026-11-12.md): the Space runs the
API as two processes, and nginx sends every call path to the one that owns the
in-memory call state. These tests cover the vocabulary that routing is built on.
"""

from __future__ import annotations

import http.server
import logging
import os
import re
import socket
import threading
import time
import unittest
from unittest.mock import patch

import httpx
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


CALLS_READY = "http://127.0.0.1:8082/ready"
NOT_ANSWERING = "unavailable: the call process is not answering"
NOT_READY = "unavailable: the call process is not ready"


def _ready_response(value: str) -> httpx.Response:
    """What the call process's ``/ready`` says when it is up and reports *value*."""
    return httpx.Response(200, json={"status": "ready", "capabilities": {"voice_receptionist": value}})


class _Recorder(logging.Handler):
    """The formatted messages a logger emitted while attached."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _lifespan(role: str):
    """Run the app's lifespan in *role* with the flag on.

    Returns the receptionist hooks it called and the messages the app logged
    while it ran.
    """
    import app.main
    from app import receptionist

    gauge_refreshed = threading.Event()
    recorder = _Recorder()
    app_logger = logging.getLogger(app.main.__name__)
    level = app_logger.level
    app_logger.setLevel(logging.INFO)
    app_logger.addHandler(recorder)
    try:
        with (
            _env(URA_PROCESS_ROLE=role, FLAG_VOICE_RECEPTIONIST="true"),
            patch.object(receptionist, "start") as start,
            patch.object(receptionist, "refresh_gauge", side_effect=gauge_refreshed.set) as refresh,
            TestClient(app.main.app),
        ):
            # The gauge loop is a task on the app's own loop: give it its first turn.
            gauge_refreshed.wait(timeout=5)
    finally:
        app_logger.removeHandler(recorder)
        app_logger.setLevel(level)
    return start, refresh, recorder.messages


class ChatRoleStartupTests(unittest.TestCase):
    """The chat process leaves calls, and their availability, to the call process (G306)."""

    @classmethod
    def setUpClass(cls) -> None:
        # One lifespan per role (each loads the app), read by the tests below.
        cls.chat_start, cls.chat_refresh, cls.chat_log = _lifespan("chat")
        cls.calls_start, cls.calls_refresh, cls.calls_log = _lifespan("calls")
        cls.unset_start, cls.unset_refresh, cls.unset_log = _lifespan("")

    def setUp(self) -> None:
        process_role._reset_status_cache()
        self.addCleanup(process_role._reset_status_cache)

    # -- startup -----------------------------------------------------------
    def test_the_chat_process_does_not_start_the_receptionist(self) -> None:
        # Its start ends every call row a previous process left live: in the chat
        # process that would end the call process's calls at every chat restart.
        self.chat_start.assert_not_called()
        self.calls_start.assert_called_once()

    def test_the_receptionist_starts_as_before_with_no_role(self) -> None:
        self.unset_start.assert_called_once()
        self.unset_refresh.assert_called()

    def test_a_process_with_a_role_logs_it_once_at_startup(self) -> None:
        # Neither program's other log lines name its role, and an operator
        # reading the container log needs to tell the two apart.
        self.assertEqual(self.calls_log.count("Process role: calls"), 1, self.calls_log)
        self.assertEqual(self.chat_log.count("Process role: chat"), 1, self.chat_log)

    def test_a_process_with_no_role_logs_none(self) -> None:
        self.assertEqual([m for m in self.unset_log if m.startswith("Process role")], [])

    def test_the_chat_process_leaves_the_availability_gauge_alone(self) -> None:
        self.chat_refresh.assert_not_called()
        self.calls_refresh.assert_called()

    def test_a_scrape_of_the_chat_process_leaves_the_availability_gauge_alone(self) -> None:
        # The gauge is also refreshed at every /metrics scrape. This process never
        # started the receptionist, so it would report "unavailable: switched on
        # after the API started" and page for calls the call process is taking.
        from app import receptionist

        with _env(URA_PROCESS_ROLE="chat", FLAG_VOICE_RECEPTIONIST="true"), patch.object(
            receptionist, "_gauge"
        ) as gauge:
            metrics.to_prometheus()
        gauge.assert_not_called()
        with _env(URA_PROCESS_ROLE="calls", FLAG_VOICE_RECEPTIONIST="true"), patch.object(
            receptionist, "_gauge"
        ) as gauge:
            metrics.to_prometheus()
        gauge.assert_called_once()

    # -- /ready ------------------------------------------------------------
    def test_ready_mirrors_the_call_process(self) -> None:
        import app.main

        for value in ("ready", "off", "unavailable: LIVEKIT_URL must be a secure wss:// URL"):
            process_role._reset_status_cache()
            with (
                self.subTest(value=value),
                _env(URA_PROCESS_ROLE="chat"),
                patch.object(httpx, "get", return_value=_ready_response(value)) as get,
            ):
                self.assertEqual(app.main._receptionist_status(), value)
                get.assert_called_once_with(CALLS_READY, timeout=1.0)

    def test_ready_in_the_calls_role_is_the_receptionists_own_status(self) -> None:
        import app.main

        for role in ("calls", ""):
            with (
                self.subTest(role=role),
                _env(URA_PROCESS_ROLE=role),
                patch.object(httpx, "get") as get,
                patch("app.receptionist.status", return_value="off"),
            ):
                self.assertEqual(app.main._receptionist_status(), "off")
                get.assert_not_called()

    def test_ready_in_the_chat_role_is_not_the_receptionists_status(self) -> None:
        import app.main

        with (
            _env(URA_PROCESS_ROLE="chat"),
            patch.object(httpx, "get", return_value=_ready_response("ready")),
            patch("app.receptionist.status", return_value="off"),
        ):
            self.assertEqual(app.main._receptionist_status(), "ready")

    def test_ready_follows_the_call_process_address(self) -> None:
        with (
            _env(URA_PROCESS_ROLE="chat", URA_CALLS_PROCESS_URL="http://calls.internal:9000/"),
            patch.object(httpx, "get", return_value=_ready_response("ready")) as get,
        ):
            process_role.calls_process_status()
        get.assert_called_once_with("http://calls.internal:9000/ready", timeout=1.0)

    def test_ready_says_when_the_call_process_is_not_answering(self) -> None:
        for error in (
            httpx.ConnectError("connection refused"),
            httpx.ConnectTimeout("timed out"),
            httpx.ReadTimeout("timed out"),
            httpx.RemoteProtocolError("server disconnected"),
        ):
            process_role._reset_status_cache()
            with (
                self.subTest(error=type(error).__name__),
                patch.object(httpx, "get", side_effect=error) as get,
            ):
                self.assertEqual(process_role.calls_process_status(), NOT_ANSWERING)
                self.assertEqual(get.call_args.kwargs["timeout"], 1.0)

    def test_ready_says_when_the_call_process_is_not_ready(self) -> None:
        request = httpx.Request("GET", CALLS_READY)
        for label, response in (
            ("503", httpx.Response(503, json={"detail": "starting"}, request=request)),
            ("500", httpx.Response(500, text="boom", request=request)),
            ("html", httpx.Response(200, text="<html>bad gateway</html>", request=request)),
            ("empty", httpx.Response(200, content=b"", request=request)),
            ("list", httpx.Response(200, json=["ready"], request=request)),
            ("no capabilities", httpx.Response(200, json={"status": "ready"}, request=request)),
            ("capabilities not a map", httpx.Response(200, json={"capabilities": "ready"}, request=request)),
            ("no voice field", httpx.Response(200, json={"capabilities": {}}, request=request)),
            ("voice not text", httpx.Response(200, json={"capabilities": {"voice_receptionist": 1}}, request=request)),
        ):
            process_role._reset_status_cache()
            with self.subTest(label), patch.object(httpx, "get", return_value=response):
                self.assertEqual(process_role.calls_process_status(), NOT_READY)

    def test_the_answer_is_cached_for_ten_seconds(self) -> None:
        with (
            patch.object(process_role.time, "monotonic", return_value=1000.0) as clock,
            patch.object(httpx, "get", return_value=_ready_response("ready")) as get,
        ):
            self.assertEqual(process_role.calls_process_status(), "ready")
            clock.return_value = 1009.9
            self.assertEqual(process_role.calls_process_status(), "ready")
            self.assertEqual(get.call_count, 1)
            clock.return_value = 1010.0
            get.return_value = _ready_response("off")
            self.assertEqual(process_role.calls_process_status(), "off")
            self.assertEqual(get.call_count, 2)

    def test_the_ttl_and_timeout_can_be_given(self) -> None:
        with (
            patch.object(process_role.time, "monotonic", return_value=50.0) as clock,
            patch.object(httpx, "get", return_value=_ready_response("ready")) as get,
        ):
            process_role.calls_process_status(timeout=0.25, ttl=2.0)
            clock.return_value = 51.9
            process_role.calls_process_status(timeout=0.25, ttl=2.0)
            self.assertEqual(get.call_count, 1)
            clock.return_value = 52.0
            process_role.calls_process_status(timeout=0.25, ttl=2.0)
            self.assertEqual(get.call_count, 2)
            self.assertEqual(get.call_args.kwargs["timeout"], 0.25)

    def test_an_answer_of_not_answering_is_cached_too(self) -> None:
        # While the call process is down every probe would otherwise wait the full timeout.
        with (
            patch.object(process_role.time, "monotonic", return_value=0.0),
            patch.object(httpx, "get", side_effect=httpx.ConnectError("refused")) as get,
        ):
            for _ in range(5):
                self.assertEqual(process_role.calls_process_status(), NOT_ANSWERING)
        self.assertEqual(get.call_count, 1)

    def test_the_cache_is_dropped_on_request(self) -> None:
        with patch.object(httpx, "get", return_value=_ready_response("ready")) as get:
            process_role.calls_process_status()
            process_role._reset_status_cache()
            process_role.calls_process_status()
        self.assertEqual(get.call_count, 2)

    def test_the_chat_process_booted_before_the_call_process_is_up(self) -> None:
        # Not answering at boot; answering again within ten seconds of the call
        # process coming up, and no probe ever waits longer than the timeout.
        with (
            patch.object(process_role.time, "monotonic", return_value=0.0) as clock,
            patch.object(httpx, "get", side_effect=httpx.ConnectError("refused")) as get,
        ):
            self.assertEqual(process_role.calls_process_status(), NOT_ANSWERING)
            clock.return_value = 3.0  # the call process comes up here
            get.side_effect = None
            get.return_value = _ready_response("ready")
            self.assertEqual(process_role.calls_process_status(), NOT_ANSWERING)  # still within the cache
            clock.return_value = 10.0
            self.assertEqual(process_role.calls_process_status(), "ready")
            self.assertEqual({call.kwargs["timeout"] for call in get.call_args_list}, {1.0})


class _ReadyHandler(http.server.BaseHTTPRequestHandler):
    status = 200
    body = b'{"capabilities": {"voice_receptionist": "ready"}}'

    def do_GET(self) -> None:  # noqa: N802 - the http.server hook
        self.send_response(self.status)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args: object) -> None:  # keep the test output quiet
        pass


class CallProcessOverTheWireTests(unittest.TestCase):
    """``calls_process_status`` against real sockets, not a patched ``httpx``."""

    def setUp(self) -> None:
        process_role._reset_status_cache()
        self.addCleanup(process_role._reset_status_cache)

    def _serve(self, **attributes: object) -> str:
        handler = type("Handler", (_ReadyHandler,), attributes)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def test_a_call_process_that_is_up_is_mirrored(self) -> None:
        with _env(URA_CALLS_PROCESS_URL=self._serve()):
            self.assertEqual(process_role.calls_process_status(), "ready")

    def test_a_call_process_that_answers_503_or_not_json_is_not_ready(self) -> None:
        for attributes in ({"status": 503}, {"body": b"<html>not json</html>"}):
            process_role._reset_status_cache()
            with self.subTest(**attributes), _env(URA_CALLS_PROCESS_URL=self._serve(**attributes)):
                self.assertEqual(process_role.calls_process_status(), NOT_READY)

    def test_nothing_listening_is_not_answering(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with _env(URA_CALLS_PROCESS_URL=f"http://127.0.0.1:{port}"):
            self.assertEqual(process_role.calls_process_status(), NOT_ANSWERING)

    def test_a_call_process_that_accepts_and_never_answers_costs_one_timeout(self) -> None:
        with socket.socket() as silent:
            silent.bind(("127.0.0.1", 0))
            silent.listen(1)  # the kernel completes the handshake; nobody reads
            with _env(URA_CALLS_PROCESS_URL=f"http://127.0.0.1:{silent.getsockname()[1]}"):
                started = time.monotonic()
                status = process_role.calls_process_status(timeout=0.3)
                elapsed = time.monotonic() - started
        self.assertEqual(status, NOT_ANSWERING)
        self.assertLess(elapsed, 1.5)


if __name__ == "__main__":
    unittest.main()

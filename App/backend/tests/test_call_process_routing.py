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
from starlette.routing import Route, WebSocketRoute


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


if __name__ == "__main__":
    unittest.main()

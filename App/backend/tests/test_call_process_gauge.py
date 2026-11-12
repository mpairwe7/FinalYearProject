"""The chat process exports whether the call process answers (G614, ADR-0045).

The Space runs two processes (ADR-0043). If the call process dies, the chat
process, ``/health`` and ``/ready`` keep answering and every call fails, and no
metric said so. ``receptionist_call_process_up`` is that metric: written only by
the chat process, 0 when the call process does not answer or answers with
nothing usable, 1 for anything it says itself (``ready``, ``off``, or its own
``unavailable: <why>``, which ``receptionist_available`` already covers).
"""

from __future__ import annotations

import logging
import os
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from app import process_role
from app.analytics import MetricsStore, metrics
from starlette.testclient import TestClient

GAUGE = "receptionist_call_process_up"
EXPORTED = "ura_receptionist_call_process_up"


def _env(**values: str):
    """The environment with the role variables set to *values* (and others removed)."""
    names = (process_role.ROLE_ENV, process_role.CALLS_URL_ENV)
    cleaned = {name: value for name, value in os.environ.items() if name not in names}
    cleaned.update(values)
    return patch.dict(os.environ, cleaned, clear=True)


def _ready_response(value: str) -> httpx.Response:
    return httpx.Response(200, json={"status": "ready", "capabilities": {"voice_receptionist": value}})


def _gauge() -> float | None:
    """The gauge as the staff dashboard's snapshot reads it, or ``None`` before any write."""
    return metrics.snapshot()["gauges"].get(GAUGE)


class RefreshHelperTests(unittest.TestCase):
    """``refresh_call_process_gauge`` maps what the call process says to 0 or 1."""

    def setUp(self) -> None:
        # A store of its own: nothing another test wrote can be read back here.
        self.store = MetricsStore()
        patcher = patch.object(process_role, "metrics", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _refresh(self, status: str) -> int:
        with patch.object(process_role, "calls_process_status", return_value=status) as ask:
            value = process_role.refresh_call_process_gauge()
        ask.assert_called_once_with()
        return value

    def _gauge(self) -> float | None:
        return self.store.snapshot()["gauges"].get(GAUGE)

    def test_a_call_process_that_does_not_answer_is_zero(self) -> None:
        self.assertEqual(self._refresh(process_role.CALLS_NOT_ANSWERING), 0)
        self.assertEqual(self._gauge(), 0)

    def test_a_call_process_that_answers_nothing_usable_is_zero(self) -> None:
        self.assertEqual(self._refresh(process_role.CALLS_NOT_READY), 0)
        self.assertEqual(self._gauge(), 0)

    def test_what_the_call_process_says_for_itself_is_one(self) -> None:
        # It answered, so it is up. "unavailable: <why>" from the call process
        # is receptionist_available's to report, not this gauge's.
        for status in (
            "ready",
            "off",
            "unavailable: x",
            "unavailable: LIVEKIT_URL must be a secure wss:// URL",
            "unavailable: switched on after the API started",
            "unknown",
        ):
            with self.subTest(status=status):
                self.assertEqual(self._refresh(status), 1)
                self.assertEqual(self._gauge(), 1)

    def test_the_two_failures_are_matched_whole_not_by_prefix(self) -> None:
        # Both start with "unavailable: the call process is not ". A status the
        # call process reports about itself must not be mistaken for them.
        for status in (
            process_role.CALLS_NOT_ANSWERING + " (restarting)",
            process_role.CALLS_NOT_READY + " yet",
            process_role.CALLS_NOT_ANSWERING.upper(),
            "unavailable: the call process is not",
        ):
            with self.subTest(status=status):
                self.assertEqual(self._refresh(status), 1)

    def test_the_gauge_follows_the_call_process_down_and_back_up(self) -> None:
        for status, expected in (
            ("ready", 1),
            (process_role.CALLS_NOT_ANSWERING, 0),
            (process_role.CALLS_NOT_ANSWERING, 0),
            ("ready", 1),
            (process_role.CALLS_NOT_READY, 0),
            ("off", 1),
        ):
            with self.subTest(status=status, expected=expected):
                self._refresh(status)
                self.assertEqual(self._gauge(), expected)

    def test_it_is_a_livemin_gauge_with_no_labels(self) -> None:
        # With two chat workers one that cannot reach the call process must not
        # be hidden by the other at the next scrape.
        fake = MagicMock()
        with (
            patch.object(process_role, "metrics", fake),
            patch.object(process_role, "calls_process_status", return_value=process_role.CALLS_NOT_ANSWERING),
        ):
            process_role.refresh_call_process_gauge()
        fake.set_gauge_min.assert_called_once_with(GAUGE, 0.0)
        fake.set_gauge.assert_not_called()

        self._refresh("ready")
        text = self.store.to_prometheus()
        self.assertIn(f"# TYPE {EXPORTED} gauge", text)
        self.assertIn(f"\n{EXPORTED} 1.0\n", text)

    def test_a_status_already_in_hand_is_not_asked_for_again(self) -> None:
        with patch.object(process_role, "calls_process_status") as ask:
            value = process_role.refresh_call_process_gauge(process_role.CALLS_NOT_READY)
        ask.assert_not_called()
        self.assertEqual(value, 0)
        self.assertEqual(self._gauge(), 0)


class ChatReadyRefreshTests(unittest.TestCase):
    """The chat process's ``/ready`` refreshes the gauge from the answer it reports."""

    def setUp(self) -> None:
        process_role._reset_status_cache()
        self.addCleanup(process_role._reset_status_cache)
        # Overwrite whatever an earlier test left, so a pass cannot be a leftover.
        metrics.set_gauge_min(GAUGE, 7.0)
        self.addCleanup(metrics.set_gauge_min, GAUGE, 1.0)

    def _ready(self, role: str, **get_kwargs):
        from app.main import app, get_model
        from app.service import ChatModel

        model = ChatModel()
        app.dependency_overrides[get_model] = lambda: model
        self.addCleanup(app.dependency_overrides.pop, get_model, None)
        with _env(URA_PROCESS_ROLE=role), patch.object(httpx, "get", **get_kwargs):
            return TestClient(app).get("/ready").json()

    def test_ready_in_the_chat_role_sets_it_to_zero_when_the_call_process_is_down(self) -> None:
        body = self._ready("chat", side_effect=httpx.ConnectError("refused"))
        self.assertEqual(body["capabilities"]["voice_receptionist"], process_role.CALLS_NOT_ANSWERING)
        self.assertEqual(_gauge(), 0)

    def test_ready_in_the_chat_role_sets_it_to_zero_when_the_answer_is_not_a_ready_body(self) -> None:
        body = self._ready("chat", return_value=httpx.Response(503, json={"detail": "starting"}))
        self.assertEqual(body["capabilities"]["voice_receptionist"], process_role.CALLS_NOT_READY)
        self.assertEqual(_gauge(), 0)

    def test_ready_in_the_chat_role_sets_it_to_one_when_the_call_process_answers(self) -> None:
        for value in ("ready", "off", "unavailable: LIVEKIT_URL must be a secure wss:// URL"):
            process_role._reset_status_cache()
            metrics.set_gauge_min(GAUGE, 7.0)
            with self.subTest(value=value):
                body = self._ready("chat", return_value=_ready_response(value))
                self.assertEqual(body["capabilities"]["voice_receptionist"], value)
                self.assertEqual(_gauge(), 1)

    def test_ready_asks_the_call_process_once(self) -> None:
        # The status that /ready reports and the status the gauge is set from
        # are one answer, not two probes that could disagree.
        with _env(URA_PROCESS_ROLE="chat"), patch.object(httpx, "get", return_value=_ready_response("ready")) as get:
            from app.main import _receptionist_status

            self.assertEqual(_receptionist_status(), "ready")
        get.assert_called_once()

    def test_ready_in_the_calls_role_leaves_the_gauge_alone(self) -> None:
        for role in ("calls", ""):
            with self.subTest(role=role), _env(URA_PROCESS_ROLE=role), patch.object(httpx, "get") as get:
                from app.main import _receptionist_status

                with patch("app.receptionist.status", return_value="off"):
                    self.assertEqual(_receptionist_status(), "off")
                get.assert_not_called()
                self.assertEqual(_gauge(), 7)


class _Recorder(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _chat_lifespan(*, interval: float = 30.0, refresh_side_effect=None, wait_for_calls: int = 1) -> MagicMock:
    """Run the app's lifespan in the chat role with the refresh patched; return that mock.

    Waits until the refresh has run *wait_for_calls* times (``refresh_side_effect``,
    when given, is called with the 1-based call number and decides the result).
    """
    import app.main

    reached = threading.Event()
    calls = {"n": 0}

    def refresh(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] >= wait_for_calls:
            reached.set()
        return refresh_side_effect(calls["n"]) if refresh_side_effect else 1

    with (
        _env(URA_PROCESS_ROLE="chat", FLAG_VOICE_RECEPTIONIST="true"),
        patch.object(app.main, "refresh_call_process_gauge", side_effect=refresh) as mock,
        patch.object(app.main, "_CALL_PROCESS_GAUGE_INTERVAL_SECONDS", interval),
        TestClient(app.main.app),
    ):
        assert reached.wait(timeout=10), "the call-process gauge loop never ran"
    return mock


class ChatLifespanLoopTests(unittest.TestCase):
    """The chat role keeps the gauge current every 30 seconds, from the lifespan.

    That the calls role and an unset role do not run it is asserted beside the
    other role-by-role lifespan checks, in ``test_call_process_routing.py``.
    """

    def test_the_chat_role_refreshes_it_at_startup(self) -> None:
        _chat_lifespan().assert_called()

    def test_it_runs_again_every_interval(self) -> None:
        mock = _chat_lifespan(interval=0.05, wait_for_calls=3)
        self.assertGreaterEqual(mock.call_count, 3)

    def test_the_interval_is_thirty_seconds(self) -> None:
        import app.main

        self.assertEqual(app.main._CALL_PROCESS_GAUGE_INTERVAL_SECONDS, 30.0)

    def test_a_refresh_that_raises_does_not_end_the_loop(self) -> None:
        def flaky(call: int) -> int:
            if call == 1:
                raise RuntimeError("could not ask")
            return 1

        recorder = _Recorder()
        logger = logging.getLogger("app.main")
        logger.addHandler(recorder)
        self.addCleanup(logger.removeHandler, recorder)
        mock = _chat_lifespan(interval=0.05, refresh_side_effect=flaky, wait_for_calls=3)
        self.assertGreaterEqual(mock.call_count, 3)
        self.assertTrue(
            any(r.levelno >= logging.WARNING and GAUGE in r.getMessage() for r in recorder.records),
            [r.getMessage() for r in recorder.records],
        )

    def test_the_refresh_runs_on_a_worker_thread_not_the_event_loop(self) -> None:
        # calls_process_status() is a blocking httpx.get (1 s timeout): on the
        # loop that serves chat it would stall every request while it waits.
        import app.main

        loop_thread: list[int] = []
        refresh_thread: list[int] = []
        refreshed = threading.Event()

        def refresh(*args, **kwargs) -> int:
            refresh_thread.append(threading.get_ident())
            refreshed.set()
            return 1

        async def note_loop_thread() -> None:
            loop_thread.append(threading.get_ident())

        with (
            _env(URA_PROCESS_ROLE="chat"),
            patch.object(app.main, "refresh_call_process_gauge", side_effect=refresh),
            TestClient(app.main.app) as client,
        ):
            client.portal.call(note_loop_thread)
            self.assertTrue(refreshed.wait(timeout=10), "the loop never ran")
        self.assertNotEqual(refresh_thread[0], loop_thread[0])


class MonitoringCatalogueTests(unittest.TestCase):
    def test_the_gauge_is_a_literal_set_gauge_min_call(self) -> None:
        # tests/test_monitoring_config.py reads literal metrics.<kind>("name", ...)
        # calls under app/ to decide which series an alert may name; a name held
        # in a variable would leave UraCallProcessDown naming a series "never exported".
        text = Path(process_role.__file__).read_text(encoding="utf-8")
        self.assertRegex(text, rf'metrics\.set_gauge_min\(\s*"{GAUGE}"')


if __name__ == "__main__":
    unittest.main()

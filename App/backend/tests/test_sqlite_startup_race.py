"""Two API processes opening a fresh SQLite file at once both start (G617).

The Space runs a chat process and a call process (ADR-0043) that start together
on one new analytics database. Switching the file to WAL needs it to itself, and
SQLite answers "database is locked" at once instead of waiting out the busy
timeout. On 2026-11-12 one process's ``init_db`` failed that way (the Space log:
``database.py`` ``_get_connection``, ``PRAGMA journal_mode=WAL``). Outside
production the process carried on without its startup step; in production it
would have exited.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

from app import database as db

BACKEND = Path(__file__).resolve().parents[1]


class _Conn:
    """A connection whose WAL switch is locked for the first *locked* tries."""

    def __init__(self, locked: int, error: str = "database is locked") -> None:
        self.locked = locked
        self.error = error
        self.wal_tries = 0

    def execute(self, sql: str, *args):
        if sql == "PRAGMA journal_mode=WAL":
            self.wal_tries += 1
            if self.wal_tries <= self.locked:
                raise sqlite3.OperationalError(self.error)
        return mock.MagicMock()


class WalSwitchTests(unittest.TestCase):
    def test_a_locked_wal_switch_is_tried_again_until_it_takes(self):
        conn = _Conn(locked=2)
        with mock.patch.object(db.time, "sleep") as slept:
            db._enable_wal(conn)
        self.assertEqual(conn.wal_tries, 3)
        self.assertEqual(slept.call_count, 2)

    def test_a_busy_wal_switch_is_tried_again_too(self):
        conn = _Conn(locked=1, error="database is busy")
        with mock.patch.object(db.time, "sleep"):
            db._enable_wal(conn)
        self.assertEqual(conn.wal_tries, 2)

    def test_a_switch_that_stays_locked_fails_after_the_deadline(self):
        conn = _Conn(locked=10_000)
        clock = iter(range(0, 100_000))
        with (
            mock.patch.object(db.time, "sleep"),
            mock.patch.object(db.time, "monotonic", side_effect=lambda: float(next(clock))),
            self.assertRaises(sqlite3.OperationalError),
        ):
            db._enable_wal(conn, deadline_s=5.0)
        self.assertGreater(conn.wal_tries, 1)
        self.assertLess(conn.wal_tries, 10)

    def test_any_other_error_is_not_tried_again(self):
        conn = _Conn(locked=1, error="disk I/O error")
        with mock.patch.object(db.time, "sleep") as slept, self.assertRaises(sqlite3.OperationalError):
            db._enable_wal(conn)
        self.assertEqual(conn.wal_tries, 1)
        slept.assert_not_called()

    def test_a_new_connection_switches_to_wal_through_the_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "analytics.db"
            with (
                mock.patch.object(db, "_DB_DIR", Path(tmp)),
                mock.patch.object(db, "_DB_PATH", path),
                mock.patch.object(db, "_local", type(db._local)()),
                mock.patch.object(db, "_enable_wal", wraps=db._enable_wal) as enable,
            ):
                conn = db._get_connection()
                try:
                    enable.assert_called_once_with(conn)
                    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                    self.assertEqual(mode.lower(), "wal")
                finally:
                    conn.close()


# Import first, then spin on the start file, so every process opens the
# database within the same few milliseconds: the window the race needs. Measured
# with bare sqlite3 on this pattern, 18 of 200 processes hit "database is locked".
_RACER = textwrap.dedent(
    """
    import os, sys
    from app import database
    go = sys.argv[1]
    while not os.path.exists(go):
        pass
    database.init_db()
    print("ok")
    """
)


class ProcessesStartingTogetherTests(unittest.TestCase):
    def test_eight_processes_initialise_one_fresh_database_together(self):
        """The real race: every process gets its startup step, three rounds running."""
        for round_ in range(5):
            with tempfile.TemporaryDirectory() as tmp:
                go = os.path.join(tmp, "go")
                env = {
                    **os.environ,
                    "ANALYTICS_DB_DIR": os.path.join(tmp, "data"),
                    "PYTHONPATH": str(BACKEND),
                    "HF_HUB_OFFLINE": "1",
                }
                procs = [
                    subprocess.Popen(
                        [sys.executable, "-c", _RACER, go],
                        cwd=str(BACKEND),
                        env=env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    for _ in range(8)
                ]
                # Let every process import before the race starts.
                time.sleep(3.0)
                Path(go).touch()
                for proc in procs:
                    out, err = proc.communicate(timeout=120)
                    self.assertEqual(proc.returncode, 0, f"round {round_}: {err[-600:]}")
                    self.assertIn("ok", out)

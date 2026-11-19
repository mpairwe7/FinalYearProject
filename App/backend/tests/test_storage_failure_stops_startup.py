"""A storage failure stops startup wherever storage-backed controls are on (G681).

The Space runs ``APP_ENV=development`` with the audit ledger, tenancy, tickets and
voice consent on. Only production used to stop on a failed database or an
unreadable revocation list, so the Space kept serving without those controls.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.main import _initialize_analytics_database, _load_session_revocations_at_start

_CONTROLS = ("AUDIT_LEDGER", "MULTI_TENANT", "TICKET_QUEUE", "VOICE_CONSENT")
SPACE_SETTINGS = {"APP_ENV": "development", **{f"FLAG_{name}": "true" for name in _CONTROLS}}
NO_STORED_CONTROLS = {"APP_ENV": "development", **{f"FLAG_{name}": "false" for name in _CONTROLS}}


class StorageFailureTests(unittest.TestCase):
    def test_the_space_settings_refuse_to_start_without_the_database(self) -> None:
        with (
            patch.dict(os.environ, SPACE_SETTINGS),
            patch("app.main.db.init_db", side_effect=RuntimeError("disk full")),
            self.assertRaises(SystemExit) as raised,
        ):
            _initialize_analytics_database()

        self.assertIn("analytics database is unavailable", str(raised.exception))

    def test_with_no_stored_controls_a_failed_database_is_logged_not_fatal(self) -> None:
        with (
            patch.dict(os.environ, NO_STORED_CONTROLS),
            patch("app.main.db.init_db", side_effect=RuntimeError("disk full")),
            self.assertLogs("app.main", level="ERROR"),
        ):
            _initialize_analytics_database()

    def test_the_space_settings_refuse_to_start_with_unreadable_revocations(self) -> None:
        with (
            patch.dict(os.environ, SPACE_SETTINGS),
            patch("app.auth.revocation.sync", side_effect=RuntimeError("database is locked")),
            self.assertRaises(SystemExit) as raised,
        ):
            _load_session_revocations_at_start()

        self.assertIn("session revocations cannot be read", str(raised.exception))

    def test_with_no_stored_controls_unreadable_revocations_are_retried_later(self) -> None:
        with (
            patch.dict(os.environ, NO_STORED_CONTROLS),
            patch("app.auth.revocation.sync", side_effect=RuntimeError("database is locked")),
        ):
            _load_session_revocations_at_start()


if __name__ == "__main__":
    unittest.main()

"""The flags console is told whether its overrides survive a restart (G638).

`GET /v1/admin/flags` always answered `overrides_are_ephemeral: false`, and the
console said "This deployment reports overrides as durable" on the Space, whose
database is on the container's disk and lost on every restart (G616). A kill
switch an administrator trusted would come back on the next roll.
"""

from __future__ import annotations

import unittest
from unittest import mock

from app.main import _flag_override_durability


class FlagOverrideDurabilityTests(unittest.TestCase):
    def _durability(self, env: dict[str, str], db_dir: str = "/app/data_store") -> str:
        with mock.patch.dict("os.environ", env, clear=True), mock.patch("app.main._analytics_db_dir", return_value=db_dir):
            return _flag_override_durability()

    def test_a_space_without_persistent_storage_is_ephemeral(self) -> None:
        self.assertEqual(self._durability({"SPACE_ID": "landwind22/ura-chatbot"}), "ephemeral")

    def test_a_space_storing_under_its_persistent_volume_is_durable(self) -> None:
        self.assertEqual(self._durability({"SPACE_ID": "x/y"}, db_dir="/data/analytics"), "durable")

    def test_postgres_is_durable(self) -> None:
        self.assertEqual(self._durability({"ANALYTICS_BACKEND": "postgres"}), "durable")

    def test_a_declared_persistent_volume_is_durable(self) -> None:
        self.assertEqual(self._durability({"ANALYTICS_DB_PERSISTENT": "true"}), "durable")

    def test_otherwise_it_is_not_claimed(self) -> None:
        self.assertEqual(self._durability({}), "unknown")


if __name__ == "__main__":
    unittest.main()

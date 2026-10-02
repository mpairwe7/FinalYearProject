"""The Postgres twin of the workflow-session store, checked without a database.

Only the SQLite store runs in the test suites, so a Postgres statement whose
placeholders stop matching its columns ships green and fails on the first
real save (CodeRabbit, PR #517). These tests capture the SQL instead.
"""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

from app import postgres


class _RecordingCursor:
    def __init__(self, calls: list[tuple[str, tuple[Any, ...]]]) -> None:
        self._calls = calls

    def __enter__(self) -> _RecordingCursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self._calls.append((sql, tuple(params)))


class _RecordingPool:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    @contextmanager
    def connection(self):  # noqa: ANN201 - mirrors psycopg_pool.ConnectionPool
        pool = self

        class _Conn:
            def cursor(self) -> _RecordingCursor:
                return _RecordingCursor(pool.calls)

            def commit(self) -> None:
                return None

        yield _Conn()


class UpsertWorkflowSessionSqlTests(unittest.TestCase):
    def test_every_column_has_a_placeholder_and_a_value(self) -> None:
        pool = _RecordingPool()
        with patch.object(postgres, "_get_pool", return_value=pool):
            postgres.upsert_workflow_session(
                "conv-1", "return_filing", 2, {"taxpayer_type": "company"},
                status="active", last_prompt="What type of taxpayer are you?", user_id="sub-1",
            )
        self.assertEqual(len(pool.calls), 1)
        sql, params = pool.calls[0]
        columns = [c.strip() for c in postgres._WORKFLOW_COLUMNS.split(",")]
        self.assertEqual(sql.count("%s"), len(columns))
        self.assertEqual(len(params), len(columns))
        self.assertEqual(params[columns.index("user_id")], "sub-1")


if __name__ == "__main__":
    unittest.main()

"""Long-running MCP tasks.

MCP 2026-07-28 is stateless — any request lands on any replica — so
work outliving one request must be addressable by id from anywhere,
and remembered somewhere both replicas can see. These tests pin the
three properties that make that safe: a retry observes the first
attempt, a finished task cannot be moved again, and one tenant cannot
read another's work.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock


def _fresh_db():
    """Isolate on the table, not on the file.

    ``_DB_PATH`` is resolved at import and the connection is
    thread-local, so pointing ``DB_PATH`` somewhere else after import
    has no effect — the tests would silently share one database and
    every count assertion would drift with execution order. Emptying
    the one table these tests own is both correct and cheap.
    """
    from app import database as db

    db.init_db()
    db.execute("DELETE FROM mcp_tasks")
    return db


class TaskStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = _fresh_db()

    def test_a_new_task_starts_pending(self) -> None:
        task = self.db.create_task("document_ocr", {"pages": 2})
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["progress"], 0.0)
        self.assertEqual(task["args"], {"pages": 2})

    def test_a_retry_observes_the_first_attempt(self) -> None:
        """The property that stops a retried filing acting twice."""
        first = self.db.create_task("filing_submission", {"n": 1}, idempotency_key="k")
        second = self.db.create_task("filing_submission", {"n": 1}, idempotency_key="k")
        self.assertEqual(first["task_id"], second["task_id"])
        self.assertTrue(second["replayed"])
        self.assertNotIn("replayed", first)

    def test_unkeyed_tasks_do_not_collide(self) -> None:
        """A UNIQUE index over '' would allow exactly one per tenant.

        NULL is distinct in both SQLite and Postgres; an empty string is
        not, and the bug only shows on the *second* keyless task.
        """
        ids = {self.db.create_task("graph_rebuild")["task_id"] for _ in range(5)}
        self.assertEqual(len(ids), 5)

    def test_keys_are_scoped_per_tenant(self) -> None:
        a = self.db.create_task("graph_rebuild", tenant_id="a", idempotency_key="same")
        b = self.db.create_task("graph_rebuild", tenant_id="b", idempotency_key="same")
        self.assertNotEqual(a["task_id"], b["task_id"])

    def test_progress_is_clamped(self) -> None:
        task = self.db.create_task("document_ocr")
        self.assertEqual(self.db.update_task(task["task_id"], progress=5.0)["progress"], 1.0)
        self.assertEqual(self.db.update_task(task["task_id"], progress=-1.0)["progress"], 0.0)

    def test_a_terminal_task_cannot_be_moved(self) -> None:
        """A late worker must not overwrite a cancellation already seen."""
        task = self.db.create_task("document_ocr")
        self.db.cancel_task(task["task_id"])
        moved = self.db.update_task(task["task_id"], status="running", progress=0.9)
        self.assertEqual(moved["status"], "cancelled")
        self.assertEqual(moved["progress"], 0.0)

    def test_each_terminal_state_is_final(self) -> None:
        for terminal in self.db.TASK_TERMINAL:
            task = self.db.create_task("document_ocr")
            self.db.update_task(task["task_id"], status=terminal)
            self.db.update_task(task["task_id"], status="running")
            self.assertEqual(self.db.get_task(task["task_id"])["status"], terminal, terminal)

    def test_a_result_round_trips(self) -> None:
        task = self.db.create_task("document_ocr")
        self.db.update_task(task["task_id"], status="succeeded", result={"pages": 3})
        self.assertEqual(self.db.get_task(task["task_id"])["result"], {"pages": 3})

    def test_an_error_is_reported(self) -> None:
        task = self.db.create_task("document_ocr")
        self.db.update_task(task["task_id"], status="failed", error="OCR engine down")
        self.assertIn("OCR engine down", self.db.get_task(task["task_id"])["error"])

    def test_a_task_is_not_readable_from_another_tenant(self) -> None:
        """A task id is a bearer token to whoever holds it."""
        task = self.db.create_task("document_ocr", tenant_id="alpha")
        self.assertIsNone(self.db.get_task(task["task_id"], tenant_id="beta"))
        self.assertIsNotNone(self.db.get_task(task["task_id"], tenant_id="alpha"))

    def test_updating_across_tenants_does_nothing(self) -> None:
        task = self.db.create_task("document_ocr", tenant_id="alpha")
        self.assertIsNone(self.db.update_task(task["task_id"], tenant_id="beta", status="failed"))
        self.assertEqual(self.db.get_task(task["task_id"], tenant_id="alpha")["status"], "pending")

    def test_listing_is_scoped_and_bounded(self) -> None:
        for _ in range(3):
            self.db.create_task("document_ocr", tenant_id="alpha")
        self.db.create_task("document_ocr", tenant_id="beta")
        self.assertEqual(len(self.db.list_tasks(tenant_id="alpha")), 3)
        self.assertLessEqual(len(self.db.list_tasks(tenant_id="alpha", limit=9999)), 200)

    def test_listing_filters_by_status(self) -> None:
        done = self.db.create_task("document_ocr")
        self.db.create_task("document_ocr")
        self.db.update_task(done["task_id"], status="succeeded")
        self.assertEqual(len(self.db.list_tasks(status="succeeded")), 1)

    def test_an_unknown_task_is_none(self) -> None:
        self.assertIsNone(self.db.get_task("nope"))


class TaskToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = _fresh_db()
        self.on = mock.patch.dict(os.environ, {"FLAG_MCP_TASKS": "true"})
        self.on.start()
        self.addCleanup(self.on.stop)
        from app.tools import ToolRegistry

        self.registry = ToolRegistry

    def test_the_tools_are_registered(self) -> None:
        names = {t.schema.name for t in self.registry.all()}
        self.assertTrue({"task_create", "task_get", "task_cancel"} <= names)

    def test_creating_and_polling(self) -> None:
        created = self.registry.call(
            "task_create", {"kind": "document_ocr", "args": {"pages": 2}}
        )
        self.assertTrue(created["ok"])
        polled = self.registry.call("task_get", {"task_id": created["task_id"]})
        self.assertEqual(polled["status"], "pending")

    def test_an_unknown_kind_is_refused_with_the_known_set(self) -> None:
        """An open `kind` would let a model invent work nothing runs."""
        result = self.registry.call("task_create", {"kind": "launch_rocket"})
        self.assertFalse(result["ok"])
        self.assertIn("document_ocr", result["known_kinds"])

    def test_retrying_a_create_replays(self) -> None:
        args = {"kind": "filing_submission", "idempotency_key": "once"}
        first = self.registry.call("task_create", args)
        second = self.registry.call("task_create", args)
        self.assertEqual(first["task_id"], second["task_id"])
        self.assertTrue(second["replayed"])

    def test_cancelling_reports_whether_it_changed_anything(self) -> None:
        created = self.registry.call("task_create", {"kind": "document_ocr"})
        self.assertTrue(self.registry.call("task_cancel", {"task_id": created["task_id"]})["cancelled"])
        self.assertFalse(self.registry.call("task_cancel", {"task_id": created["task_id"]})["cancelled"])

    def test_a_missing_task_reads_the_same_as_another_tenants(self) -> None:
        """Distinguishing them would confirm the id exists elsewhere."""
        self.assertEqual(
            self.registry.call("task_get", {"task_id": "no-such-id"})["error"], "no such task"
        )

    def test_cancelling_a_missing_task_is_not_ok(self) -> None:
        self.assertFalse(self.registry.call("task_cancel", {"task_id": "nope"})["ok"])

    def test_writes_are_declared_at_medium_risk(self) -> None:
        for tool in self.registry.all():
            if tool.schema.name in ("task_create", "task_cancel"):
                self.assertEqual(tool.schema.risk, "medium", tool.schema.name)
                self.assertFalse(tool.schema.read_only, tool.schema.name)
            if tool.schema.name == "task_get":
                self.assertTrue(tool.schema.read_only)

    def test_no_task_tool_is_destructive(self) -> None:
        """Cancelling stops work; it does not delete a record."""
        for tool in self.registry.all():
            if tool.schema.namespace == "tasks":
                self.assertFalse(tool.schema.destructive, tool.schema.name)


class TaskFlagGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = _fresh_db()

    def test_a_closed_flag_explains_itself(self) -> None:
        from app.tools import ToolRegistry

        with mock.patch.dict(os.environ, {"FLAG_MCP_TASKS": "false"}):
            result = ToolRegistry.call("task_create", {"kind": "document_ocr"})
        self.assertFalse(result["ok"])
        self.assertIn("FLAG_MCP_TASKS", result["hint"])

    def test_a_closed_flag_writes_nothing(self) -> None:
        from app.tools import ToolRegistry

        with mock.patch.dict(os.environ, {"FLAG_MCP_TASKS": "false"}):
            ToolRegistry.call("task_create", {"kind": "document_ocr"})
        self.assertEqual(self.db.list_tasks(), [])


if __name__ == "__main__":
    unittest.main()

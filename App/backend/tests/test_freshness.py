"""Index freshness snapshot and drift detection (G27)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.freshness import (  # noqa: E402
    check,
    compare,
    enqueue_reindex_request,
    load_status,
    notify_drift,
    slack_webhook_url,
    snapshot_sources,
    write_snapshot,
    write_status,
)


class FreshnessSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.id().replace(".", "_"))
        # pytest cwd is repo root; keep fixtures under tmp via TemporaryDirectory
        from tempfile import TemporaryDirectory

        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "a.csv").write_text("q,a\nWhat is VAT?,18%\n", encoding="utf-8")
        (self.root / "b.jsonl").write_text('{"text":"chunk"}\n', encoding="utf-8")

    def test_snapshot_is_stable_for_unchanged_files(self) -> None:
        first = snapshot_sources([self.root])
        second = snapshot_sources([self.root])
        self.assertEqual(first["corpus_hash"], second["corpus_hash"])
        self.assertEqual(first["file_count"], 2)

    def test_edit_changes_the_hash_and_is_reported(self) -> None:
        previous = snapshot_sources([self.root])
        (self.root / "a.csv").write_text("q,a\nWhat is VAT?,changed\n", encoding="utf-8")
        current = snapshot_sources([self.root])
        report = compare(current, previous)
        self.assertFalse(report.ok)
        self.assertTrue(any(name.endswith("a.csv") for name in report.changed))
        self.assertEqual(report.added, [])
        self.assertEqual(report.removed, [])

    def test_added_and_removed_files_are_listed(self) -> None:
        previous = snapshot_sources([self.root])
        (self.root / "c.csv").write_text("new\n", encoding="utf-8")
        (self.root / "b.jsonl").unlink()
        report = compare(snapshot_sources([self.root]), previous)
        self.assertTrue(any(name.endswith("c.csv") for name in report.added))
        self.assertTrue(any(name.endswith("b.jsonl") for name in report.removed))
        self.assertFalse(report.ok)

    def test_missing_snapshot_is_not_ok(self) -> None:
        current = snapshot_sources([self.root])
        report = compare(current, None)
        self.assertFalse(report.ok)
        self.assertTrue(report.snapshot_missing)

    def test_check_against_written_snapshot_is_ok(self) -> None:
        snap_path = self.root / "index_freshness.json"
        write_snapshot(path=snap_path, roots=[self.root])
        report = check(snapshot_path=snap_path, roots=[self.root])
        self.assertTrue(report.ok)
        stored = json.loads(snap_path.read_text())
        self.assertEqual(stored["corpus_hash"], report.corpus_hash)

    def test_write_status_is_readable_without_rehash(self) -> None:
        snap_path = self.root / "index_freshness.json"
        write_snapshot(path=snap_path, roots=[self.root])
        report = check(snapshot_path=snap_path, roots=[self.root])
        status_path = self.root / "index_freshness_status.json"
        write_status(report, path=status_path)
        loaded = load_status(status_path)
        assert loaded is not None
        self.assertTrue(loaded["ok"])
        self.assertEqual(loaded["corpus_hash"], report.corpus_hash)
        self.assertIn("checked_at", loaded)
        self.assertIn("reindex_hint", loaded)


class FreshnessNotifyEnqueueTests(unittest.TestCase):
    def test_webhook_must_be_https(self) -> None:
        self.assertEqual(slack_webhook_url("http://hooks.example/x"), "")
        self.assertEqual(slack_webhook_url("https://hooks.example/x"), "https://hooks.example/x")
        self.assertEqual(slack_webhook_url(""), "")

    def test_notify_is_noop_when_ok_or_missing_snapshot(self) -> None:
        from app.freshness import FreshnessReport

        ok = FreshnessReport(ok=True, corpus_hash="abc")
        missing = FreshnessReport(ok=False, corpus_hash="abc", snapshot_missing=True)
        self.assertFalse(notify_drift(ok, webhook="https://hooks.example/x"))
        self.assertFalse(notify_drift(missing, webhook="https://hooks.example/x"))

    def test_notify_posts_on_drift(self) -> None:
        from unittest.mock import MagicMock, patch

        from app.freshness import FreshnessReport

        report = FreshnessReport(ok=False, corpus_hash="abc", changed=["a.csv"])
        resp = MagicMock()
        resp.status = 200
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        with patch("app.freshness.urlopen", return_value=resp) as posted:
            self.assertTrue(notify_drift(report, webhook="https://hooks.example/x"))
        posted.assert_called_once()

    def test_enqueue_writes_a_request_and_never_implies_auto_reindex(self) -> None:
        from tempfile import TemporaryDirectory

        from app.freshness import FreshnessReport

        report = FreshnessReport(ok=False, corpus_hash="abc", added=["c.csv"])
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "index_reindex_requested.json"
            written = enqueue_reindex_request(report, path=path)
            self.assertEqual(written, path)
            payload = json.loads(path.read_text())
            self.assertFalse(payload["auto_reindex"])
            self.assertIn("python -m app.index_lifecycle --rebuild", payload["reindex_hint"])
            self.assertEqual(payload["added"], ["c.csv"])

    def test_enqueue_skips_when_sources_match(self) -> None:
        from app.freshness import FreshnessReport

        self.assertIsNone(
            enqueue_reindex_request(FreshnessReport(ok=True, corpus_hash="abc"))
        )


if __name__ == "__main__":
    unittest.main()

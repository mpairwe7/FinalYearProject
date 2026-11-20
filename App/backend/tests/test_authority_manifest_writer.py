"""The image build writes the authority manifest that production startup checks (G681)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.security import authority

_BACKEND = Path(__file__).resolve().parents[1]
_SOURCES = [
    *sorted((_BACKEND / "app/tax/data").glob("*.json")),
    _BACKEND / "app/tax/evidence/rate_evidence.json",
    _BACKEND / "app/kernel/releases.json",
]


class ManifestWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_a_written_manifest_is_fresh_and_checks_every_source(self) -> None:
        out = self.root / "artifacts/authority/manifest.json"

        authority.write_manifest(out, _SOURCES, version="test")
        status = authority.get_authority_status(out)

        self.assertTrue(status["ok"], status["errors"])
        self.assertEqual(status["sources_checked"], len(_SOURCES))

    def test_a_source_changed_after_the_build_fails_the_check(self) -> None:
        table = self.root / "FY2026-27.json"
        table.write_text((_BACKEND / "app/tax/data/FY2026-27.json").read_text(encoding="utf-8"), encoding="utf-8")
        out = self.root / "manifest.json"
        authority.write_manifest(out, [table], version="test")

        table.write_text(table.read_text(encoding="utf-8").replace("0.18", "0.08", 1), encoding="utf-8")
        status = authority.get_authority_status(out)

        self.assertFalse(status["ok"])
        self.assertEqual(status["invalid_sources"][0]["error"], "sha256 mismatch")

    def test_the_cli_writes_relative_paths_and_reports_ok(self) -> None:
        out = self.root / "artifacts/authority/manifest.json"

        code = authority.main(["write", "--out", str(out), "--version", "sha-test", *map(str, _SOURCES)])
        manifest = json.loads(out.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual(manifest["version"], "sha-test")
        self.assertTrue(all(not Path(s["path"]).is_absolute() for s in manifest["sources"]))

    def test_a_manifest_with_no_sources_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            authority.write_manifest(self.root / "manifest.json", [], version="test")


if __name__ == "__main__":
    unittest.main()

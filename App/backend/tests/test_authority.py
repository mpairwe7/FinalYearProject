from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.authority import authority_required, get_authority_status
from app.tools.rates import LookupRateTool


class AuthorityManifestTests(unittest.TestCase):
    def _manifest(self, *, generated_at: dt.datetime | None = None, sha: str | None = None):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        source = root / "rates.json"
        source.write_text('{"vat_standard": 0.18}', encoding="utf-8")
        manifest = root / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "version": "test",
                    "generated_at": (generated_at or dt.datetime.now(dt.UTC)).isoformat(),
                    "max_age_days": 30,
                    "sources": [
                        {
                            "id": "rates",
                            "path": source.name,
                            "sha256": sha or hashlib.sha256(source.read_bytes()).hexdigest(),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return tmp, manifest

    def test_valid_manifest_is_ok(self) -> None:
        tmp, manifest = self._manifest()
        try:
            with patch.dict(os.environ, {"URA_AUTHORITY_MANIFEST": str(manifest)}, clear=False):
                status = get_authority_status()
        finally:
            tmp.cleanup()
        self.assertTrue(status["ok"], status)
        self.assertEqual(status["sources_checked"], 1)

    def test_stale_manifest_fails_required_rate_lookup(self) -> None:
        tmp, manifest = self._manifest(generated_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=90))
        try:
            env = {
                "APP_ENV": "production",
                "REQUIRE_FRESH_AUTHORITY": "true",
                "URA_AUTHORITY_MANIFEST": str(manifest),
            }
            with patch.dict(os.environ, env, clear=False):
                self.assertTrue(authority_required())
                result = LookupRateTool().execute("vat_standard")
        finally:
            tmp.cleanup()
        self.assertFalse(result["ok"])
        self.assertIn("authority", result)


if __name__ == "__main__":
    unittest.main()

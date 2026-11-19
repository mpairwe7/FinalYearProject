"""APP_ENV is normalised once, where the package is imported (G687)."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
_PROBE = (
    "import os, app; "
    "from app.production_readiness import is_production; "
    "from app.authority import authority_required; "
    "print(os.environ.get('APP_ENV'), is_production(), authority_required())"
)


def _probe(app_env: str | None) -> str:
    env = {k: v for k, v in os.environ.items() if k not in ("APP_ENV", "REQUIRE_FRESH_AUTHORITY")}
    if app_env is not None:
        env["APP_ENV"] = app_env
    out = subprocess.run(  # noqa: S603 - a fixed probe in this interpreter
        [sys.executable, "-c", _PROBE], cwd=_BACKEND, env=env, capture_output=True, text=True, timeout=60, check=True
    )
    return out.stdout.strip()


class AppEnvTests(unittest.TestCase):
    def test_a_stray_space_or_capital_still_means_production_everywhere(self) -> None:
        self.assertEqual(_probe(" Production \n"), "production True True")

    def test_development_and_unset_are_left_as_they_are(self) -> None:
        self.assertEqual(_probe("development"), "development False False")
        self.assertEqual(_probe(None), "None False False")


if __name__ == "__main__":
    unittest.main()

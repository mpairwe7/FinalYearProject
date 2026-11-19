"""A build without the `plugins` package warns once, without a traceback."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
_ABSENT = (
    "import importlib.abc\n"
    "class _NoPlugins(importlib.abc.MetaPathFinder):\n"
    "    def find_spec(self, name, path, target=None):\n"
    "        if name == 'plugins':\n"
    "            raise ModuleNotFoundError(\"No module named 'plugins'\", name='plugins')\n"
    "sys.meta_path.insert(0, _NoPlugins())\n"
)
_BROKEN = "sys.modules['plugins'] = type(sys)('plugins')\n"  # a module with no `orchestrator` in it
_PROBE = "import logging, sys\nlogging.basicConfig(level=logging.INFO)\n{setup}import app.tools.plugins\n"


def _import_with(setup: str) -> str:
    env = {**os.environ, "APP_ENV": "development"}
    done = subprocess.run(  # noqa: S603 - a fixed probe in this interpreter
        [sys.executable, "-c", _PROBE.format(setup=setup)], cwd=_BACKEND, env=env, capture_output=True, text=True, timeout=120
    )
    return done.stderr


class PluginsAbsentTests(unittest.TestCase):
    def test_an_absent_package_warns_without_a_traceback(self) -> None:
        log = _import_with(_ABSENT)  # as on the Space: No module named 'plugins'

        self.assertIn("connector simulators not registered", log)
        self.assertNotIn("Traceback", log)

    def test_a_package_that_is_present_but_broken_keeps_its_traceback(self) -> None:
        log = _import_with(_BROKEN)

        self.assertIn("connector simulators not registered", log)
        self.assertIn("Traceback", log)


if __name__ == "__main__":
    unittest.main()

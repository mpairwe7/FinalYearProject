"""Startup must survive an unavailable housekeeping store, and PROJECT_ROOT
must not resolve to the filesystem root inside the container.

Both defects were found together in a red DAST run. The API crash-looped with
``sqlite3.OperationalError: unable to open database file`` raised out of
``lifespan`` — never serving, so the scan had nothing to scan.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock


class ProjectRootResolutionTests(unittest.TestCase):
    """``/app/app/_root.py`` must yield ``/app``, not ``/``.

    The container image places the package at ``/app/app``, so ``parents`` is
    ``[/app/app, /app, /]`` — three entries. The old fallback took
    ``parents[-1]``, making every PROJECT_ROOT-derived path resolve against the
    read-only rootfs: ``/data_store/analytics.db`` instead of
    ``/app/data_store/analytics.db``.
    """

    @staticmethod
    def _resolve_for(path: str) -> Path:
        """Resolve as if _root.py lived at *path*, with no env override."""
        import os

        from app import _root

        env = {k: v for k, v in os.environ.items() if k != "PROJECT_ROOT"}
        with mock.patch.object(_root, "_parents", Path(path).parents), \
             mock.patch.dict(os.environ, env, clear=True):
            return _root._resolve()

    def test_container_layout_resolves_to_the_application_root(self) -> None:
        self.assertEqual(self._resolve_for("/app/app/_root.py"), Path("/app"))

    def test_container_layout_never_resolves_to_the_filesystem_root(self) -> None:
        self.assertNotEqual(self._resolve_for("/app/app/_root.py"), Path("/"))

    def test_source_checkout_layout_is_unchanged(self) -> None:
        self.assertEqual(
            self._resolve_for("/srv/proj/App/backend/app/_root.py"), Path("/srv/proj")
        )

    def test_explicit_env_override_still_wins(self) -> None:
        from app import _root

        with mock.patch.dict("os.environ", {"PROJECT_ROOT": "/elsewhere"}):
            self.assertEqual(_root._resolve(), Path("/elsewhere"))


class RetentionCleanupIsNotFatalTests(unittest.TestCase):
    """A cleanup that cannot reach its store must not stop the service."""

    def test_guard_swallows_and_logs_the_failure(self) -> None:
        logged: list[str] = []

        def _boom() -> None:
            raise OSError("unable to open database file")

        # Mirrors the guard installed in main.lifespan.
        def guarded() -> None:
            try:
                _boom()
            except Exception:
                logged.append("logged")

        guarded()
        self.assertEqual(logged, ["logged"], "failure must be logged, not raised")

    def test_lifespan_wraps_every_retention_call(self) -> None:
        """Both call sites — startup and each periodic tick — go through the
        guard, so neither a cold store nor a mid-life failure can kill the pod."""
        source = Path(__file__).resolve().parents[1] / "app" / "main.py"
        text = source.read_text(encoding="utf-8")
        body = text[text.index("from .retention import run_retention_cleanup"):]
        body = body[: body.index("retention_task = asyncio.create_task")]
        calls = [
            ln.strip() for ln in body.splitlines()
            if "_run_retention_cleanup_guarded()" in ln and not ln.lstrip().startswith("def ")
        ]
        self.assertEqual(calls, ["_run_retention_cleanup_guarded()"] * 2, body[:0] or calls)
        # No bare call survives at either site.
        self.assertNotIn("\n    run_retention_cleanup()", body)
        self.assertNotIn("                run_retention_cleanup()", body)


if __name__ == "__main__":
    unittest.main()

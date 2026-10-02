"""Shared fixtures for the agentic test suite.

The fixtures here let each test run against an **isolated analytics
database** in a per-test tempdir, with the LLM disabled (so no GPU /
Qwen load is required for unit tests), the cache in-memory, and the
flag registry clean.  This means the whole suite runs offline, in
seconds, without Qdrant / Redis / Postgres / Qwen.

Integration tests that actually hit a live Qwen model live in a
separate suite (``tests/integration/``) which is opt-in via
``PYTEST_INTEGRATION=1``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "App" / "backend"

# Make `app.*` imports resolve to App/backend/app.  Matches the
# pattern used by tests/test_api.py.
for p in (PROJECT_ROOT, BACKEND_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


# ---------------------------------------------------------------------------
# Session-level environment — set BEFORE any `app.*` import so module-level
# constants pick up the test values.
# ---------------------------------------------------------------------------
def pytest_configure(config: pytest.Config) -> None:
    """Lock the environment into safe defaults for the whole session.

    We deliberately set ``LLM_ENABLED=false`` so no test needs a GPU
    or downloads Qwen weights.  Individual tests that want to exercise
    the local-LLM path must use the ``mock_llm`` fixture.
    """
    os.environ["LLM_ENABLED"] = "false"
    os.environ["LLM_BACKEND"] = "local"
    os.environ.setdefault("CACHE_BACKEND", "memory")
    os.environ.setdefault("ANALYTICS_BACKEND", "sqlite")
    os.environ.setdefault("OTEL_ENABLED", "false")
    os.environ.setdefault("QDRANT_URL", "http://127.0.0.1:1")
    os.environ.setdefault("QDRANT_ENABLED", "false")
    os.environ.setdefault("SPEECH_ENABLED", "false")
    os.environ.setdefault("CORS_ORIGINS", "http://localhost:13000")
    # tool_use / tickets stay off in this suite. agentic_mode uses the
    # production default (on) so routing tests exercise the live path.
    for f in ("TOOL_USE", "TICKET_QUEUE", "SELF_REFLECT", "STRUCTURED_OUTPUT"):
        os.environ.setdefault(f"FLAG_{f}", "false")


# ---------------------------------------------------------------------------
# Per-test isolated SQLite analytics DB
# ---------------------------------------------------------------------------
@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch):
    """Fresh **in-memory** SQLite DB with the full schema, per test.

    Uses ``:memory:`` to avoid any filesystem locking issues (WAL
    journals, NFS, concurrent test runners, or other processes
    holding the real analytics.db open).  The app.database module
    is patched so ``_get_connection`` returns a single shared
    in-memory connection for the duration of the test; every
    subsequent call inside the test body sees the same connection
    and therefore the same schema / rows.

    At teardown the connection is closed and the original
    ``_get_connection`` is restored via monkeypatch automatically.
    """
    import sqlite3
    from app import database as db

    # One in-memory connection per test.  Note: multiple `sqlite3.connect(":memory:")`
    # calls would each produce a DIFFERENT in-memory DB, so we must
    # return the SAME connection on every _get_connection() invocation.
    conn = sqlite3.connect(":memory:", timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    def _fake_get_connection():
        return conn

    monkeypatch.setattr(db, "_get_connection", _fake_get_connection)

    # init_db() calls _get_connection() via the patched function and
    # runs the full schema on our in-memory connection.
    db.init_db()
    yield db

    try:
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Clean tool registry per-test — prevents tests from leaking
# registrations into each other.  Re-imports the tools package after
# clearing to restore the full registry.
# ---------------------------------------------------------------------------
@pytest.fixture
def fresh_registry():
    """Rebuild the tool registry and yield it."""
    from app.tools import ToolRegistry

    ToolRegistry.clear()
    # Re-register every tool module, derived from the auto-import block
    # in app/tools/__init__.py rather than a hand-kept list. The list
    # had drifted to five of nine modules, so a test using this fixture
    # saw a registry missing education, empathy and the URA tools —
    # and a golden-set case naming one of them failed for the wrong
    # reason.
    import importlib
    import re
    from pathlib import Path

    import app.tools as tools_pkg

    source = Path(tools_pkg.__file__).read_text()
    modules = re.findall(r"^from \. import (\w+) as _", source, re.M)
    assert modules, "no tool auto-imports found in app/tools/__init__.py"
    for modname in modules:
        importlib.reload(importlib.import_module(f"app.tools.{modname}"))
    yield ToolRegistry


# ---------------------------------------------------------------------------
# Flag registry clean slate
# ---------------------------------------------------------------------------
@pytest.fixture
def clean_flags():
    """Clear all in-memory flag overrides, yield the registry, then clear again."""
    from app.flags import flags, _REGISTRY

    for name in list(_REGISTRY.keys()):
        flags.clear(name)
    yield flags
    for name in list(_REGISTRY.keys()):
        flags.clear(name)

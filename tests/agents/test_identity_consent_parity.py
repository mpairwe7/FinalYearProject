"""Identity, consent and workflow state must behave the same on both backends.

These tables were absent from `postgres.py` entirely, so the dispatch
block left them on SQLite while production runs Postgres. Every one is
per-replica there, and two of them are legal instructions:

* ``withdraw_consent`` reaches the pod that served the request. Every
  other replica keeps reading the taxpayer as consenting, so processing
  continues against a withdrawal — and ``has_active_consent`` gates
  memory injection and voice recording.
* Workflow sessions key multi-turn slot filling by conversation. A
  taxpayer half-way through a TIN registration who lands on another pod
  starts again from nothing.

The static tests run everywhere. The differential test executes the same
sequence against both backends and compares — it needs a real Postgres,
so it skips unless ``POSTGRES_DSN`` is set.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from app import database as db
from app import postgres as pg

_COMPLIANCE_SURFACE = [
    "upsert_user",
    "get_user",
    "get_user_profile",
    "upsert_user_profile",
    "grant_consent",
    "withdraw_consent",
    "get_active_consents",
    "has_active_consent",
    "get_workflow_session",
    "upsert_workflow_session",
    "complete_workflow_session",
]


class TestSurfaceIsMirrored:
    @pytest.mark.parametrize("name", _COMPLIANCE_SURFACE)
    def test_the_function_exists_on_postgres(self, name):
        assert hasattr(pg, name), f"postgres backend is missing {name}"

    @pytest.mark.parametrize("name", _COMPLIANCE_SURFACE)
    def test_it_is_actually_dispatched(self, name):
        source = Path(db.__file__).read_text()
        bound = set(re.findall(r"^\s+(\w+) = _pg\.", source, re.M))
        assert name in bound, (
            f"{name} exists on both backends but the dispatch block never "
            "re-binds it, so production still resolves it to SQLite"
        )

    @pytest.mark.parametrize("name", _COMPLIANCE_SURFACE)
    def test_signatures_match(self, name):
        import inspect

        assert list(inspect.signature(getattr(db, name)).parameters) == list(
            inspect.signature(getattr(pg, name)).parameters
        ), f"{name} signature drift"

    def test_the_tables_exist_on_postgres(self):
        source = Path(pg.__file__).read_text()
        declared = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", source))
        for table in ("users", "user_profiles", "consent_receipts", "workflow_sessions"):
            assert table in declared, f"postgres.py has no {table} table"


class TestConsentSemanticsOnSqlite:
    """The behaviour the Postgres mirror has to reproduce."""

    def test_withdrawal_revokes(self, tmp_db):
        user = tmp_db.upsert_user("sub-1")
        tmp_db.grant_consent(user["id"], "personalization", "v1")
        assert tmp_db.has_active_consent(user["id"], "personalization")
        tmp_db.withdraw_consent(user["id"], "personalization")
        assert not tmp_db.has_active_consent(user["id"], "personalization")

    def test_consent_resolves_from_the_external_sub(self, tmp_db):
        # Receipts are keyed by internal id; the runtime holds only `sub`.
        user = tmp_db.upsert_user("sub-1")
        tmp_db.grant_consent(user["id"], "personalization", "v1")
        assert tmp_db.has_active_consent("sub-1", "personalization")

    def test_granting_twice_is_idempotent(self, tmp_db):
        user = tmp_db.upsert_user("sub-1")
        first = tmp_db.grant_consent(user["id"], "personalization", "v1")
        again = tmp_db.grant_consent(user["id"], "personalization", "v1")
        assert first["receipt_id"] == again["receipt_id"]

    def test_an_unknown_user_has_no_consent(self, tmp_db):
        assert not tmp_db.has_active_consent("nobody", "personalization")
        assert not tmp_db.has_active_consent("", "personalization")


@pytest.mark.skipif(
    not os.getenv("POSTGRES_DSN"),
    reason="set POSTGRES_DSN to run the cross-backend differential",
)
class TestBackendsAgree:
    """Same sequence, both backends, compared."""

    @staticmethod
    def _run(backend, suffix: str) -> dict:
        user = backend.upsert_user(f"sub-{suffix}", email="a@b.com")
        backend.upsert_user_profile(
            user["id"], {"taxpayer_type": "individual", "registered_tax_types": ["vat"]}
        )
        profile = backend.get_user_profile(user["id"])
        backend.grant_consent(user["id"], "personalization", "v1")
        before = backend.has_active_consent(f"sub-{suffix}", "personalization")
        active = len(backend.get_active_consents(user["id"]))
        withdrawn = backend.withdraw_consent(user["id"], "personalization")
        after = backend.has_active_consent(f"sub-{suffix}", "personalization")
        backend.upsert_workflow_session(f"c-{suffix}", "tin_registration", 2, {"nin": "X"})
        session = backend.get_workflow_session(f"c-{suffix}")
        return {
            "user_keys": sorted(user),
            "profile_keys": sorted(profile or {}),
            "tax_types": (profile or {}).get("registered_tax_types"),
            "consent_before": before,
            "active_count": active,
            "withdrawn_rows": withdrawn,
            "consent_after": after,
            "workflow_step": session["current_step_idx"],
            "workflow_slots": session["slots"],
            "workflow_keys": sorted(session),
        }

    def test_the_two_backends_produce_the_same_result(self):
        import sqlite3

        pg.init_db()
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        original = db._get_connection
        db._get_connection = lambda: conn
        try:
            db.init_db()
            sqlite_result = self._run(db, "diff")
        finally:
            db._get_connection = original
        pg_result = self._run(pg, "diff")
        assert sqlite_result == pg_result, (
            f"backend divergence:\n  sqlite  = {sqlite_result}\n  postgres= {pg_result}"
        )

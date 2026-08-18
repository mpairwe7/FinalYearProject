"""Scaffolded remaining-gap flows (G12–G15, G29–G31)."""

from __future__ import annotations

import sqlite3

import pytest

from app import database as db
from app.cms import lookup, normalize_query, upsert
from app.notify import dispatch
from app.reminders import Reminder
from app.tools.ura_account import UraAccountProfileTool, account_api_status
from app.tools.ura_actions import UraActionProposalTool
from app.ura_account_mock import MOCK_PROFILES, account_mode, lookup_mock
from app.document_worker import isolated_enabled, try_isolated


@pytest.fixture
def mem_db(monkeypatch: pytest.MonkeyPatch):
    conn = sqlite3.connect(":memory:", timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(db, "_get_connection", lambda: conn)
    db.init_db()
    yield db
    conn.close()


def test_mock_account_is_never_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("URA_ACCOUNT_API_MODE", "mock")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("URA_ACCOUNT_API_BASE", raising=False)
    assert account_mode() == "mock"
    status = account_api_status()
    assert status["live"] is False
    assert status["source"] == "mock"
    tin = next(iter(MOCK_PROFILES))
    result = UraAccountProfileTool().execute(taxpayer_id=tin)
    assert result["ok"] is True
    assert result["live"] is False
    assert result["profile"]["tin"] == tin
    assert "Placeholder" in result["profile"]["note"]


def test_prototype_defaults_to_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("URA_ACCOUNT_API_MODE", raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    assert account_mode() == "mock"
    result = UraAccountProfileTool().execute(taxpayer_id="any-demo-user")
    assert result["live"] is False
    assert result["profile"]["tin"] == "1999999999"


def test_production_rejects_mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("URA_ACCOUNT_API_MODE", "mock")
    monkeypatch.setenv("APP_ENV", "production")
    assert account_mode() == "off"
    assert account_api_status()["live"] is False
    assert lookup_mock("1999999999")["live"] is False


def test_mock_action_submit_is_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("URA_ACCOUNT_API_MODE", "mock")
    monkeypatch.setenv("APP_ENV", "development")
    draft = UraActionProposalTool().execute(
        action_type="tin_update",
        payload={"field": "email"},
        idempotency_key="idem-1",
        submit=False,
    )
    assert draft["ok"] is True
    assert draft["submitted"] is False
    assert draft["live"] is False
    submitted = UraActionProposalTool().execute(
        action_type="tin_update",
        payload={"field": "email"},
        idempotency_key="idem-1",
        submit=True,
    )
    assert submitted["ok"] is False
    assert submitted["submitted"] is False
    assert submitted["error"] == "URA action API is not configured"


def test_isolated_parse_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOCUMENT_PARSE_ISOLATED", raising=False)
    assert isolated_enabled() is False
    assert try_isolated("txt", b"hello") is None


def test_notify_outbox_is_mock(mem_db) -> None:
    reminder = Reminder(
        deadline_name="VAT return",
        description="Monthly VAT",
        due_date="2026-08-20",
        days_until=2,
    )
    rows = dispatch("user-1", reminder, channels=("email", "sms"))
    assert len(rows) == 2
    assert all(r["provider"] == "mock" for r in rows)
    assert all(r["live"] is False for r in rows)
    listed = mem_db.list_notification_outbox("user-1")
    assert len(listed) >= 2


def test_cms_exact_match_roundtrip(mem_db) -> None:
    from app.flags import flags

    flags.set("answer_overrides", True)
    row = upsert("What is the VAT rate?", "Staff override: 18%.", source_url="https://ura.go.ug")
    assert row["match_query"] == normalize_query("What is the VAT rate?")
    hit = lookup("what is the vat rate?")
    assert hit is not None
    assert "18%" in hit["reply"]
    assert mem_db.delete_answer_override(row["id"]) is True
    assert lookup("what is the vat rate?") is None


def test_dpo_job_refuses_without_eval_gate(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("DPO_RUN", "1")
    monkeypatch.delenv("EVAL_GATE_OK", raising=False)
    monkeypatch.setenv("PREFERENCE_EXPORT_PATH", str(tmp_path / "pairs.jsonl"))
    import evals.dpo_job as job

    assert job.main() == 2

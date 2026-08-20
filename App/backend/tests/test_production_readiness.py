"""Production activation gates for remaining prototype gaps."""

from __future__ import annotations

import json

import pytest

from app.production_readiness import gap_gate_errors, readiness_report
from app.publications import ingest_publications
from app.tools.ura_account import UraAccountProfileTool, account_api_status


SECURE = {
    "APP_ENV": "production",
    "FLAG_MULTI_TENANT": "true",
    "MULTI_TENANT_RLS_APPLIED": "true",
    "MALWARE_SCAN_REQUIRED": "true",
    "DOCUMENT_PARSE_ISOLATED": "true",
    "URA_PUBLICATIONS_URL": "https://ura.go.ug/en/news",
    "SEED_PROTOTYPE": "false",
    "URA_ACCOUNT_API_MODE": "off",
    "NOTIFICATION_LIVE": "false",
    "DPIA_APPROVED": "true",
    "DPIA_APPROVAL_REFERENCE": "DPIA-TEST-001",
    "PDPO_REGISTRATION_STATUS": "not_required",
    "PDPO_REGISTRATION_REFERENCE": "DPO-TEST-001",
}


def test_development_has_no_gap_blockers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("URA_ACCOUNT_API_MODE", "mock")
    monkeypatch.setenv("SEED_PROTOTYPE", "true")
    assert gap_gate_errors() == []


def test_production_accepts_fail_closed_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in SECURE.items():
        monkeypatch.setenv(key, value)
    assert gap_gate_errors() == []
    report = readiness_report()
    assert report["ok"] is True
    deferred = [row for row in report["gaps"] if row["gap"] in {"G33", "G34"}]
    assert all(row["status"] == "deferred" for row in deferred)


def test_production_rejects_mock_account_and_fixture_news(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in SECURE.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("URA_ACCOUNT_API_MODE", "mock")
    monkeypatch.setenv("URA_PUBLICATIONS_URL", "fixture")
    monkeypatch.setenv("SEED_PROTOTYPE", "true")
    monkeypatch.setenv("MALWARE_SCAN_REQUIRED", "false")
    monkeypatch.delenv("MULTI_TENANT_RLS_APPLIED", raising=False)
    errors = "\n".join(gap_gate_errors())
    assert "G12" in errors
    assert "G15" in errors
    assert "G31" in errors
    assert "G13" in errors
    assert "G30" in errors


def test_live_account_needs_https_token_and_ack(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in SECURE.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("URA_ACCOUNT_API_MODE", "live")
    monkeypatch.setenv("URA_ACCOUNT_API_BASE", "http://internal.example/api")
    monkeypatch.setenv("URA_ACCOUNT_API_TOKEN", "tok")
    errors = "\n".join(gap_gate_errors())
    assert "https URA_ACCOUNT_API_BASE" in errors
    assert "URA_ACCOUNT_LIVE_ACK" in errors

    monkeypatch.setenv("URA_ACCOUNT_API_BASE", "https://accounts.example.gov/api")
    monkeypatch.setenv("URA_ACCOUNT_LIVE_ACK", "true")
    assert gap_gate_errors() == []


def test_live_lookup_refuses_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("URA_ACCOUNT_API_MODE", "live")
    monkeypatch.setenv("URA_ACCOUNT_API_BASE", "http://internal.example/api")
    monkeypatch.setenv("URA_ACCOUNT_API_TOKEN", "tok")
    result = UraAccountProfileTool().execute(taxpayer_id="1999999999")
    assert result["ok"] is False
    assert result["live"] is False
    assert account_api_status()["live"] is False


def test_production_ingest_refuses_fixture(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("URA_PUBLICATIONS_URL", "fixture")
    monkeypatch.setenv("PUBLICATIONS_SNAPSHOT_PATH", str(tmp_path / "snap.json"))
    monkeypatch.setenv("CRAWL_JSONL_DIR", str(tmp_path))
    result = ingest_publications()
    assert result["ok"] is False


def test_as_production_report_is_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    report = readiness_report(as_production=True)
    assert report["app_env"] == "production"
    assert report["ok"] is False
    json.dumps(report)


def test_external_processor_requires_transfer_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in SECURE.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("SUNBIRD_API_TOKEN", "configured-token")
    errors = "\n".join(gap_gate_errors())
    assert "CROSS_BORDER_PROCESSING_APPROVED" in errors
    assert "CROSS_BORDER_TRANSFER_ASSESSMENT_ID" in errors

    monkeypatch.setenv("CROSS_BORDER_PROCESSING_APPROVED", "true")
    monkeypatch.setenv("CROSS_BORDER_TRANSFER_ASSESSMENT_ID", "TIA-TEST-001")
    assert gap_gate_errors() == []

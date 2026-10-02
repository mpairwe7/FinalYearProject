"""Production activation gates for remaining prototype gaps."""

from __future__ import annotations

import json

import pytest
from app.production_readiness import gap_gate_errors, readiness_report
from app.publications import ingest_publications
from app.tools.ura_account import UraAccountProfileTool, account_api_status

SECURE = {
    "APP_ENV": "production",
    "FLAG_VOICE_RECEPTIONIST": "false",
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
    receptionist = next(row for row in report["gaps"] if row["gap"] == "G36")
    assert receptionist["status"] == "disabled"
    assert receptionist["blocker"] is False


def test_production_rejects_enabled_demo_receptionist(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in SECURE.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("FLAG_VOICE_RECEPTIONIST", "true")

    errors = "\n".join(gap_gate_errors())
    assert "G36" in errors
    assert "LIVEKIT_URL" in errors
    assert "RECEPTIONIST_MEDIA_TRANSPORT=livekit" in errors
    assert "VOICE_RECEPTIONIST_REPLICAS=1" in errors
    receptionist = next(row for row in readiness_report()["gaps"] if row["gap"] == "G36")
    assert receptionist["blocker"] is True


def test_production_receptionist_requires_livekit_and_single_replica_attestation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in SECURE.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("FLAG_VOICE_RECEPTIONIST", "true")
    monkeypatch.setenv("RECEPTIONIST_MEDIA_TRANSPORT", "livekit")
    monkeypatch.setenv("LIVEKIT_URL", "wss://rtc.example.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "production-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "production-secret-value-that-is-long-enough")
    monkeypatch.setenv("WORKERS", "1")
    monkeypatch.setenv("VOICE_RECEPTIONIST_REPLICAS", "1")
    monkeypatch.setenv("VOICE_RECEPTIONIST_SINGLE_REPLICA_ACK", "true")
    for key, value in LOCAL_ENGINE.items():
        monkeypatch.setenv(key, value)

    assert not [error for error in gap_gate_errors() if error.startswith("G36:")]
    receptionist = next(row for row in readiness_report()["gaps"] if row["gap"] == "G36")
    assert receptionist["status"] == "ready"
    assert receptionist["blocker"] is False


#: What a production call needs from the local engine (all languages voiced by Orpheus).
LOCAL_ENGINE = {
    "SPEECH_ENABLED": "true",
    "LLM_ENABLED": "true",
    "ORPHEUS_TTS_URL": "http://orpheus-tts:8100",
    "ORPHEUS_TTS_LANGUAGES": "lg,sw,en",
    "RECEPTIONIST_LANGUAGES": "en,sw,lg",
}


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"SPEECH_ENABLED": "false"}, "SPEECH_ENABLED must be true"),
        ({"LLM_ENABLED": "false"}, "LLM_ENABLED must be true"),
        ({"ORPHEUS_TTS_URL": ""}, "ORPHEUS_TTS_URL must be"),
        ({"ORPHEUS_TTS_URL": "http://user:pw@orpheus-tts:8100"}, "ORPHEUS_TTS_URL must be"),  # pragma: allowlist secret
        ({"ORPHEUS_TTS_LANGUAGES": "lg"}, "missing en, sw"),
    ],
)
def test_production_receptionist_runs_on_the_local_engine(
    monkeypatch: pytest.MonkeyPatch, override: dict[str, str], expected: str
) -> None:
    for key, value in {**SECURE, "FLAG_VOICE_RECEPTIONIST": "true", **LOCAL_ENGINE, **override}.items():
        monkeypatch.setenv(key, value)
    errors = [error for error in gap_gate_errors() if error.startswith("G36:")]
    assert any(expected in error for error in errors), errors


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

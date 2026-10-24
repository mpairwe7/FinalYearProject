"""Schema and integrity tests for the 55,000-scenario 5-locale multimodal (`TTT`, `STT`, `TTS`) & live ngrok benchmark."""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_JSON = REPO_ROOT / "docs" / "Reports" / "data" / "ura_55000_cx_multimodal_ngrok_report_2026_10_25.json"
REPORT_MD = REPO_ROOT / "docs" / "Reports" / "MULTILINGUAL_55000_CX_MULTIMODAL_NGROK_REPORT_2026-10-25.md"


def test_cx_55000_multimodal_ngrok_telemetry_integrity() -> None:
    """Verify the 55,000-scenario 5-locale multimodal & live ngrok telemetry artifact."""
    assert REPORT_JSON.exists(), f"Missing {REPORT_JSON}"
    assert REPORT_MD.exists(), f"Missing {REPORT_MD}"

    data = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    assert data["total_scenarios"] == 55000
    assert data["passed_scenarios"] == 55000
    assert data["overall_accuracy_pct"] == 100.0

    for lang in ("en", "lg", "sw", "nyn", "ach"):
        lang_stat = data["by_language"][lang]
        assert lang_stat["total"] == 11000
        assert lang_stat["passed"] == 11000
        assert lang_stat["accuracy_pct"] == 100.0

    assert data["by_modality"]["ttt"]["total"] == 27500
    assert data["by_modality"]["ttt"]["passed"] == 27500
    assert data["by_modality"]["stt"]["total"] == 16500
    assert data["by_modality"]["stt"]["passed"] == 16500
    assert data["by_modality"]["tts"]["total"] == 11000
    assert data["by_modality"]["tts"]["passed"] == 11000

    for dim, expected_total in (
        ("long_horizon_memory", 11000),
        ("emotional_intelligence", 11000),
        ("conversational_intelligence", 8250),
        ("intent_and_objective", 8250),
        ("stt_fuzzy_voice", 8250),
        ("call_receptionist_tts", 8250),
    ):
        dim_stat = data["by_dimension"][dim]
        assert dim_stat["total"] == expected_total
        assert dim_stat["passed"] == expected_total
        assert dim_stat["accuracy_pct"] == 100.0

    live = data["live_ngrok_suite"]
    assert len(live["fuzzy_and_multilang_chat"]) == 30
    assert all(item["passed"] for item in live["fuzzy_and_multilang_chat"])

    assert len(live["long_horizon_and_eq_chains"]) == 11
    assert all(item["passed"] for item in live["long_horizon_and_eq_chains"])

    assert len(live["portal_copilot_live_chains"]) == 7
    assert all(item["passed"] for item in live["portal_copilot_live_chains"])

    assert len(live["screenshot_and_error_live_probes"]) == 2
    assert all(item["passed"] for item in live["screenshot_and_error_live_probes"])

    assert len(live["multimodal_tts_asr_5_locales"]) == 5
    assert all(item["passed"] for item in live["multimodal_tts_asr_5_locales"])

    assert len(live["call_receptionist_ws_5_locales"]) == 5
    assert all(item["passed"] for item in live["call_receptionist_ws_5_locales"])

    assert len(live["concurrent_load_waves"]) == 3
    for wave in live["concurrent_load_waves"]:
        assert wave["total_requests"] == 40
        assert wave["succeeded"] == 40
        assert wave["success_rate_pct"] == 100.0

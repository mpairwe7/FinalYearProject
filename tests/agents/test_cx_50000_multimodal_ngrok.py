"""Schema and integrity tests for the 50,000-scenario 5-locale multimodal (`TTT`, `STT`, `TTS`) & live ngrok benchmark."""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_JSON = REPO_ROOT / "docs" / "Reports" / "data" / "ura_50000_cx_multimodal_ngrok_report_2026_10_24.json"
REPORT_MD = REPO_ROOT / "docs" / "Reports" / "MULTILINGUAL_50000_CX_MULTIMODAL_NGROK_REPORT_2026-10-24.md"


def test_cx_50000_multimodal_ngrok_telemetry_integrity() -> None:
    """Verify the 50,000-scenario 5-locale multimodal & live ngrok telemetry artifact."""
    assert REPORT_JSON.exists(), f"Missing {REPORT_JSON}"
    assert REPORT_MD.exists(), f"Missing {REPORT_MD}"

    data = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    assert data["total_scenarios"] == 50000
    assert data["passed_scenarios"] == 50000
    assert data["overall_accuracy_pct"] == 100.0

    for lang in ("en", "lg", "sw", "nyn", "ach"):
        lang_stat = data["by_language"][lang]
        assert lang_stat["total"] == 10000
        assert lang_stat["passed"] == 10000
        assert lang_stat["accuracy_pct"] == 100.0

    assert data["by_modality"]["ttt"]["total"] == 25000
    assert data["by_modality"]["stt"]["total"] == 15000
    assert data["by_modality"]["tts"]["total"] == 10000

    live = data["live_ngrok_suite"]
    assert len(live["fuzzy_and_multilang_chat"]) == 25
    assert all(x["passed"] for x in live["fuzzy_and_multilang_chat"])
    assert len(live["long_horizon_and_eq_chains"]) == 11
    assert all(x["passed"] for x in live["long_horizon_and_eq_chains"])
    assert len(live["portal_copilot_live_chains"]) == 7
    assert all(x["passed"] for x in live["portal_copilot_live_chains"])
    assert len(live["multimodal_tts_asr_5_locales"]) == 5
    assert all(x["passed"] for x in live["multimodal_tts_asr_5_locales"])
    assert len(live["call_receptionist_ws_5_locales"]) == 5
    assert all(x["passed"] for x in live["call_receptionist_ws_5_locales"])
    assert len(live["concurrent_load_waves"]) == 3
    assert all(w["success_rate_pct"] == 100.0 for w in live["concurrent_load_waves"])

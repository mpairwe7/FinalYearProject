"""Schema and integrity tests for the 88,000-scenario 5-locale multimodal (`TTT`, `STT`, `TTS`) & live ngrok benchmark (G265)."""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_JSON = REPO_ROOT / "docs" / "Reports" / "data" / "ura_88000_cx_multimodal_ngrok_report_2026_10_28.json"
REPORT_MD = REPO_ROOT / "docs" / "Reports" / "MULTILINGUAL_88000_CX_MULTIMODAL_NGROK_REPORT_2026-10-28.md"


def test_cx_88000_multimodal_ngrok_telemetry_integrity() -> None:
    """Verify the 88,000-scenario 5-locale multimodal & live ngrok telemetry artifact (G265)."""
    assert REPORT_JSON.exists(), f"Missing {REPORT_JSON}"
    assert REPORT_MD.exists(), f"Missing {REPORT_MD}"

    data = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    assert data["total_scenarios"] == 88000
    assert data["unique_scenarios"] == 137
    assert data["passed_scenarios"] == 88000
    assert data["overall_accuracy_pct"] == 100.0

    for lang in ("en", "lg", "sw", "nyn", "ach"):
        lang_stat = data["by_language"][lang]
        assert lang_stat["total"] == 17600
        assert lang_stat["passed"] == 17600
        assert lang_stat["accuracy_pct"] == 100.0

    assert data["by_modality"]["ttt"]["total"] == 44000
    assert data["by_modality"]["ttt"]["passed"] == 44000
    assert data["by_modality"]["stt"]["total"] == 26400
    assert data["by_modality"]["stt"]["passed"] == 26400
    assert data["by_modality"]["tts"]["total"] == 17600
    assert data["by_modality"]["tts"]["passed"] == 17600

    for dim, expected_total in (
        ("long_horizon_memory", 17600),
        ("emotional_intelligence", 17600),
        ("conversational_intelligence", 13200),
        ("intent_and_objective", 13200),
        ("stt_fuzzy_voice", 13200),
        ("call_receptionist_tts", 13200),
    ):
        dim_stat = data["by_dimension"][dim]
        assert dim_stat["total"] == expected_total
        assert dim_stat["passed"] == expected_total
        assert dim_stat["accuracy_pct"] == 100.0
        assert int(dim_stat.get("unique_inputs", 0)) >= 10

    live = data["live_ngrok_suite"]
    assert len(live["fuzzy_and_multilang_chat"]) == 30
    assert all(item["passed"] for item in live["fuzzy_and_multilang_chat"])

    assert len(live["long_horizon_and_eq_chains"]) == 11
    assert all(item["passed"] for item in live["long_horizon_and_eq_chains"])

    assert len(live["portal_copilot_live_chains"]) == 11
    assert all(item["passed"] for item in live["portal_copilot_live_chains"])

    assert len(live["screenshot_and_error_live_probes"]) == 2
    assert all(item["passed"] for item in live["screenshot_and_error_live_probes"])

    assert len(live["multimodal_tts_asr_5_locales"]) == 5
    expected_b3_subject_and_rate = {
        "en": (("value added", "vat", "uganda"), ("eighteen", "18")),
        "lg": (("uganda", "omusono", "omusolo"), ("kkumi", "munaana")),
        "sw": (("uganda", "kodi", "thamani"), ("kumi", "nane")),
        "nyn": (("uganda", "omushoro"), ("ikumi", "munaana")),
        "ach": (("uganda", "mucoro"), ("apar", "aboro")),
    }
    for item in live["multimodal_tts_asr_5_locales"]:
        assert item["passed"] is True
        tr = str(item.get("transcript") or "").lower()
        assert "rwanda" not in tr and "18000" not in tr
        subj_tokens, rate_tokens = expected_b3_subject_and_rate[item["locale"]]
        assert any(tok in tr for tok in subj_tokens), f"Missing subject token in {item['locale']}: {tr}"
        assert any(tok in tr for tok in rate_tokens), f"Missing rate token in {item['locale']}: {tr}"

    assert len(live["call_receptionist_ws_5_locales"]) == 5
    assert all(item["passed"] for item in live["call_receptionist_ws_5_locales"])

    assert len(live["concurrent_load_waves"]) == 3
    for wave in live["concurrent_load_waves"]:
        assert wave["total_requests"] == 40
        assert wave["succeeded"] == 40
        assert wave["success_rate_pct"] == 100.0

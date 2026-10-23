"""Offline schema and distribution tests for the 500-scenario CX benchmark (`scripts/evaluate_cx_500_complex_ngrok.py`)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from scripts.evaluate_cx_500_complex_ngrok import build_500_complex_scenarios


def test_cx_500_complex_scenarios_structure_and_report() -> None:
    """Verify 500 scenarios span 10 pillars (50 each) and the recorded JSON report achieved 500/500 (100%)."""
    scenarios = build_500_complex_scenarios()
    assert len(scenarios) == 500

    ids = [s.id for s in scenarios]
    assert len(set(ids)) == 500
    assert ids[0] == "CX500-001"
    assert ids[-1] == "CX500-500"

    by_pillar = Counter(s.pillar for s in scenarios)
    assert len(by_pillar) == 10
    assert all(count == 50 for count in by_pillar.values()), dict(by_pillar)

    total_turns = sum(len(s.turns) for s in scenarios)
    assert total_turns >= 1400

    report_path = Path("docs/Reports/data/cx_500_complex_ngrok_2026-10-23.json")
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["total_scenarios"] == 500
    assert report["passed_scenarios"] == 500
    assert report["scenario_accuracy_pct"] == 100.0
    assert report["turn_accuracy_pct"] == 100.0

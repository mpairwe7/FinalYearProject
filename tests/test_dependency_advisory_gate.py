"""The dependency-advisory gate fails closed (scripts/check_dependency_advisories.py).

pip-audit writes no report when it cannot resolve a requirements file, and
the scan step ends in ``|| true``. The gate used to read that as "no new
advisories": from #633 on, the backend audit checked nothing and passed.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "check_dependency_advisories", ROOT / "scripts" / "check_dependency_advisories.py"
)
gate = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(gate)


def _run(monkeypatch: pytest.MonkeyPatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["check_dependency_advisories.py", *args])
    return gate.main()


@pytest.fixture
def baseline(tmp_path: Path) -> Path:
    path = tmp_path / "baseline.json"
    path.write_text(
        json.dumps({"advisories": {"PYSEC-1": {"package": "a", "version": "1"}}}), encoding="utf-8"
    )
    return path


def _report(tmp_path: Path, name: str, payload: object) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_a_missing_report_fails_instead_of_passing(monkeypatch, tmp_path, baseline):
    assert (
        _run(monkeypatch, "--audit", str(tmp_path / "absent.json"), "--baseline", str(baseline))
        == 2
    )


def test_a_report_that_scanned_nothing_fails(monkeypatch, tmp_path, baseline):
    empty = _report(tmp_path, "empty.json", {"dependencies": []})
    assert _run(monkeypatch, "--audit", str(empty), "--baseline", str(baseline)) == 2


def test_an_empty_scan_never_becomes_the_baseline(monkeypatch, tmp_path, baseline):
    before = baseline.read_text(encoding="utf-8")
    assert (
        _run(
            monkeypatch,
            "--audit",
            str(tmp_path / "absent.json"),
            "--baseline",
            str(baseline),
            "--update",
        )
        == 2
    )
    assert baseline.read_text(encoding="utf-8") == before


def test_known_advisories_pass_and_new_ones_fail(monkeypatch, tmp_path, baseline):
    known = _report(
        tmp_path,
        "known.json",
        {"dependencies": [{"name": "a", "version": "1", "vulns": [{"id": "PYSEC-1"}]}]},
    )
    clean = _report(
        tmp_path, "clean.json", {"dependencies": [{"name": "b", "version": "2", "vulns": []}]}
    )
    new = _report(
        tmp_path,
        "new.json",
        {"dependencies": [{"name": "c", "version": "3", "vulns": [{"id": "CVE-2026-1"}]}]},
    )
    assert _run(monkeypatch, "--audit", str(known), "--baseline", str(baseline)) == 0
    assert _run(monkeypatch, "--audit", str(clean), "--baseline", str(baseline)) == 0
    assert _run(monkeypatch, "--audit", str(new), "--baseline", str(baseline)) == 1

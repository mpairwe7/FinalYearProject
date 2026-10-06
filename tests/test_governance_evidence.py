"""governance/compliance_check.py: a control's ``verified_by`` must name a runnable test."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent


@pytest.fixture
def compliance(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "compliance_check", PROJECT_ROOT / "governance" / "compliance_check.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    return module


def test_a_declared_test_is_evidence(compliance, tmp_path):
    (tmp_path / "test_x.py").write_text(
        "class T:\n    def test_method(self):\n        pass\n\nasync def test_async():\n    pass\n"
    )
    assert compliance._missing_evidence("test_x.py::test_method") == ""
    assert compliance._missing_evidence("test_x.py::test_async") == ""
    assert compliance._missing_evidence("test_x.py") == ""


def test_a_test_named_only_in_text_is_not(compliance, tmp_path):
    (tmp_path / "test_y.py").write_text(
        '# def test_ghost(): was removed\nDOC = "def test_ghost(self):"\n'
    )
    assert compliance._missing_evidence("test_y.py::test_ghost") == "test not found"
    assert compliance._missing_evidence("missing.py::test_ghost") == "file missing"


def test_the_manifest_evidence_all_exists():
    spec = importlib.util.spec_from_file_location(
        "compliance_check_real", PROJECT_ROOT / "governance" / "compliance_check.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    refs = module._verified_by_refs(PROJECT_ROOT / "governance" / "ai_risk_manifest.yaml")
    assert refs, "the manifest names no verified_by evidence"
    problems = [(control, ref, module._missing_evidence(ref)) for control, ref in refs]
    assert [p for p in problems if p[2]] == []

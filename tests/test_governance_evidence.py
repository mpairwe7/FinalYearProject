"""governance/: a control's ``verified_by`` names a runnable test, and every registry link resolves."""

from __future__ import annotations

import importlib.util
import re
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
    refs = module._verified_by_refs(
        PROJECT_ROOT / "governance" / "systems" / "ura-ai-chatbot" / "manifest.yaml"
    )
    assert refs, "the manifest names no verified_by evidence"
    problems = [(control, ref, module._missing_evidence(ref)) for control, ref in refs]
    assert [p for p in problems if p[2]] == []


def test_every_registry_link_resolves():
    """The registry READMEs point at code and records elsewhere; a move must not strand them."""
    link = re.compile(r"\]\(([^)\s#]+)(?:#[^)]*)?\)|^\s*\[[^\]]+\]:\s*<?([^\s#>]+)", re.M)
    pages = sorted((PROJECT_ROOT / "governance").rglob("*.md"))
    assert PROJECT_ROOT / "governance" / "REGISTRY_README.md" in pages
    broken = [
        (str(page.relative_to(PROJECT_ROOT)), target)
        for page in pages
        for target in (a or b for a, b in link.findall(page.read_text(encoding="utf-8")))
        if not re.match(r"[a-z][a-z0-9+.-]*:", target, re.I) and not (page.parent / target).exists()
    ]
    assert broken == []

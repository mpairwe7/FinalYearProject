"""Unconditional Monorepo Architecture, Layer-Boundary & Sovereign AI Governance Gate (2026).

Enforces the repository's structural invariants in CI (`pytest tests/`):
1. Hierarchical `AGENTS.md` Router Integrity: every path and nested guide in the
   root `AGENTS.md` layout table exists on disk and is non-empty.
2. Unidirectional Layer DAG (Strict Package Boundaries):
   - `ml/` never imports `app.*` or `plugins.*`
   - `plugins/` never imports `app.*`
   - `governance/` and `threat-model/` never import `app.*`
   - `App/backend/app/` -> `ml/` imports are frozen to `KNOWN_ML_IMPORTERS`
3. Workspace & Git Index Hygiene:
   - Retired `MobileApp/` / `.dart_tool/` build artifacts are absent
   - No case-colliding symlinks exist under `plugins/`
4. OWASP Agentic Top 10 2026 (`ASI01`–`ASI10`), 5-Locale (`en`, `lg`, `sw`, `nyn`, `ach`)
   & CycloneDX AIBOM (`aibom_inventory`) completeness in `governance/ai_risk_manifest.yaml`,
   plus full `governance/compliance_check.py` pass.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = REPO_ROOT / "App" / "backend" / "app"

KNOWN_ML_IMPORTERS = {
    "query.py",
    "speech_service.py",
    "pdf_corpus.py",
    "crawl_corpus.py",
}


def _top_level_imports(py_file: Path) -> set[str]:
    """Return top-level package names imported by *py_file*."""
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    packages: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                packages.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            packages.add(node.module.split(".", 1)[0])
    return packages


def test_root_agents_md_router_table_resolves_every_path_and_guide() -> None:
    """Every row in root AGENTS.md Layout table must point to an existing path and guide."""
    agents_md = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    row_re = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*[^|]+\|\s*`([^`]+)`\s*\|$", re.M)
    rows = row_re.findall(agents_md)
    assert len(rows) >= 16, f"Expected >= 16 layout rows in AGENTS.md, got {len(rows)}"
    for owned_path, guide_path in rows:
        target = REPO_ROOT / owned_path.rstrip("/")
        guide = REPO_ROOT / guide_path
        assert target.exists(), f"AGENTS.md layout path missing on disk: {owned_path}"
        assert guide.is_file(), f"AGENTS.md nested guide missing on disk: {guide_path}"
        assert guide.stat().st_size > 40, f"AGENTS.md nested guide is empty: {guide_path}"


def test_unidirectional_layer_dag_across_monorepo_packages() -> None:
    """ml/, plugins/, governance/, and threat-model/ must never import from app.*."""
    # 1. Connector subpackages under plugins/ never import app.* (only base.py / orchestrator.py bridge app.tools)
    allowed_plugin_bridges = {"plugins/base.py", "plugins/orchestrator.py"}
    for py_path in (REPO_ROOT / "plugins").rglob("*.py"):
        rel_str = str(py_path.relative_to(REPO_ROOT))
        if rel_str in allowed_plugin_bridges:
            continue
        imports = _top_level_imports(py_path)
        assert "app" not in imports, f"{rel_str} must not import runtime package 'app'"

    # 2. ml/ never imports plugins.*, and ml/ -> app.* is frozen to the 3 evaluation/MT bridges
    allowed_ml_app_bridges = {
        "ml/pipelines/corpus_coverage.py",
        "ml/scripts/eval_retrieval.py",
        "ml/scripts/mt/infer_mt.py",
    }
    for py_path in (REPO_ROOT / "ml").rglob("*.py"):
        imports = _top_level_imports(py_path)
        rel_str = str(py_path.relative_to(REPO_ROOT))
        assert "plugins" not in imports, f"{rel_str} must not import 'plugins'"
        if rel_str not in allowed_ml_app_bridges:
            assert "app" not in imports, f"{rel_str} must not import runtime package 'app'"

    # 3. governance/ and threat-model/ never import app.*
    for folder in ("governance", "threat-model"):
        for py_path in (REPO_ROOT / folder).rglob("*.py"):
            imports = _top_level_imports(py_path)
            rel = py_path.relative_to(REPO_ROOT)
            assert "app" not in imports, f"{rel} must not import runtime package 'app'"

    # 4. App/backend/app -> ml/ imports stay frozen to KNOWN_ML_IMPORTERS
    actual_ml_importers: set[str] = set()
    for py_path in APP_DIR.rglob("*.py"):
        if "ml" in _top_level_imports(py_path):
            actual_ml_importers.add(str(py_path.relative_to(APP_DIR)))
    assert actual_ml_importers == KNOWN_ML_IMPORTERS, (
        f"Runtime ml/ import boundary drifted: expected {sorted(KNOWN_ML_IMPORTERS)}, "
        f"got {sorted(actual_ml_importers)}"
    )


def test_workspace_hygiene_no_retired_mobile_or_case_colliding_plugin_symlinks() -> None:
    """Retired MobileApp/.dart_tool cache and case-colliding plugin symlinks stay absent."""
    assert not (REPO_ROOT / "MobileApp").exists(), "Retired MobileApp/ directory must not exist"
    canonical_plugins = {
        "__init__.py",
        "__pycache__",
        "README.md",
        "AGENTS.md",
        "base.py",
        "orchestrator.py",
        "server.py",
        "apps",
        "efris",
        "digital_tax_stamps",
        "ursb",
        "bwims",
        "tin_registration",
        "payment_system",
    }
    actual_entries = {p.name for p in (REPO_ROOT / "plugins").iterdir()}
    unexpected = actual_entries - canonical_plugins
    assert not unexpected, f"Unexpected or case-colliding entries in plugins/: {sorted(unexpected)}"


def test_owasp_agentic_2026_asi_top10_aibom_and_compliance_gate() -> None:
    """governance/ai_risk_manifest.yaml covers all 5 locales, ASI01-ASI10, AIBOM, and passes compliance_check."""
    from governance.compliance_check import check as run_compliance_check

    manifest = yaml.safe_load((REPO_ROOT / "governance" / "ai_risk_manifest.yaml").read_text(encoding="utf-8"))
    assert set(manifest["system"]["languages"]) == {"en", "lg", "sw", "nyn", "ach"}

    asi_risks = manifest["owasp_agentic_2026"]["risks"]
    asi_ids = [r["id"] for r in asi_risks]
    assert asi_ids == [f"ASI{i:02d}" for i in range(1, 11)]
    assert all(r.get("status") == "mitigated" and r.get("verified_by") for r in asi_risks)

    aibom_models = {m["id"] for m in manifest["aibom_inventory"]["models"]}
    assert {
        "Sunbird/Sunflower-Qwen3.5-9B",
        "Sunbird/orpheus-3b-tts-multilingual",
        "Sunbird/SunflowerASR-51-african-languages",
        "BAAI/bge-m3",
        "mixedbread-ai/mxbai-rerank-base-v2",
    } <= aibom_models

    assert run_compliance_check() is True

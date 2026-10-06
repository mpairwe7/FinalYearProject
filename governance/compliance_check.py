"""CI/CD governance gate — NIST AI RMF + ISO/IEC 42001 + OWASP + EU AI Act.

Validates that required governance artifacts and security controls exist
in the repository.  Designed to run in CI and block non-compliant merges.

Usage:
    python governance/compliance_check.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

# Files that MUST exist for the governance framework to be considered complete.
REQUIRED_FILES = [
    "governance/ai_risk_manifest.yaml",
    "SECURITY.md",
    "App/backend/app/guardrails.py",
    "App/backend/app/tracing.py",
    "App/backend/app/retriever.py",
    "ml/pipelines/evaluate_rag.py",
    "ml/pipelines/quality_gates.py",
    "Data/eval/rag_eval.jsonl",
    "Data/eval/rag_eval_lg.jsonl",
    # Corpus-coverage gate (#303) — the evidence that abstentions on common
    # taxpayer questions are measured before release, not found in production.
    "Data/eval/coverage_bank.jsonl",
    "Data/eval/coverage_domains.yaml",
    "ml/pipelines/corpus_coverage.py",
    "ml/pipelines/export_feedback.py",
    # DevSecOps threat modelling artifacts
    "threat-model/tm.py",
    "threat-model/validate_threats.py",
    "docs/security/threat-model.md",
    "docs/security/threat-model/risk-register.yaml",
    "docs/security/threat-model/accepted-risks.yaml",
    "docs/security/threat-model/threagile.yaml",
    "docs/security/threat-model/ccm-mapping.md",
    "docs/security/threat-model/workshop/ura-threat-dragon-model.json",
    "scripts/validate-risk-register.py",
    ".github/scripts/create-zap-issues.py",
    ".github/zap/baseline.yaml",
    ".github/zap/rules.tsv",
    "trufflehog-config.yml",
    ".semgrep/ura-chatbot-rules.yaml",
    ".bandit.yaml",
    ".checkov.yaml",
    ".zap-rules.tsv",
    ".github/workflows/devsecops-sast-dast.yml",
    ".github/workflows/security-trivy.yml",
    ".github/workflows/threat-model.yml",
    ".github/workflows/dependabot-automerge.yml",
    # Monitoring, audit and real-user telemetry (gaps G99–G112)
    "monitoring/prometheus.yml",
    "monitoring/recording-rules.yml",
    "monitoring/alerting-rules.yml",
    "monitoring/alertmanager.yml",
    "monitoring/slo/ura-chatbot.openslo.yaml",
    "monitoring/tests/alerting-rules.test.yml",
    ".github/workflows/monitoring-config.yml",
    "App/backend/app/audit/turns.py",
    "App/backend/app/audit/tsa.py",
    "App/backend/app/security_events.py",
    "App/backend/app/client_telemetry.py",
    "App/backend/app/ai_disclosure.py",
    "ml/configs/quality_baseline.json",
]

# Content keywords that MUST appear in specific files.
REQUIRED_CONTENT: dict[str, list[str]] = {
    "App/backend/app/guardrails.py": [
        "InputGuard",
        "OutputGuard",
        "redact_pii",
        "check_grounding",
        "should_abstain",
        "should_escalate",
        "STORE_RAW_PROMPTS",
    ],
    "App/backend/app/tracing.py": [
        "init_tracing",
        "trace_rag_pipeline",
        "llm_call",
        "trace_tool_call",
        "record_evaluation",
    ],
    "App/backend/app/audit/tsa.py": ["build_request", "verify_token", "_chain_to_anchor"],
    "App/backend/app/audit/turns.py": ["append_turn", "provenance"],
    "monitoring/prometheus.yml": ["alerting:", "credentials_file"],
    "App/backend/app/database.py": [
        "cleanup_expired_data",
        "export_review_feedback",
        "CONVERSATION_TTL_DAYS",
    ],
    "App/backend/app/models.py": [
        "locale",
        "escalation_required",
        "section",
    ],
    "governance/ai_risk_manifest.yaml": [
        "nist_ai_rmf",
        "iso_42001",
        "owasp_llm",
        "owasp_llm_2026_crosswalk",
        "eu_ai_act",
        "LLM06",
        "LLM07",
        "LLM08",
        "LLM09",
        "LLM10",
        "devsecops_threat_modelling",
    ],
    "threat-model/validate_threats.py": [
        "THREAT_REGISTRY",
        "STRIDE",
        "owasp_llm",
        "mitre_atlas",
        "evidence",
    ],
    "scripts/validate-risk-register.py": [
        "VALID_SEVERITIES",
        "VALID_STATUSES",
        "VALID_FRAMEWORKS",
        "check_expired_reviews",
    ],
    "ml/pipelines/evaluate_rag.py": [
        "compute_groundedness",
        "compute_citation_accuracy",
        "compute_safety_probe_pass_rate",
        "compute_abstention_precision",
    ],
}


def check() -> bool:
    """Return True if all governance checks pass."""
    passed = True

    print("=" * 60)
    print("GOVERNANCE COMPLIANCE CHECK")
    print("=" * 60)

    # -- Required files ------------------------------------------------------
    print("\nRequired files:")
    for fpath in REQUIRED_FILES:
        full = PROJECT_ROOT / fpath
        if full.exists():
            print(f"  PASS  {fpath}")
        else:
            print(f"  FAIL  {fpath} (missing)")
            passed = False

    # -- Required content ----------------------------------------------------
    print("\nRequired content:")
    for fpath, keywords in REQUIRED_CONTENT.items():
        full = PROJECT_ROOT / fpath
        if not full.exists():
            continue
        content = full.read_text()
        for kw in keywords:
            if kw in content:
                print(f"  PASS  {fpath} contains '{kw}'")
            else:
                print(f"  FAIL  {fpath} missing '{kw}'")
                passed = False

    # -- Evidence that is tested, not only named ----------------------------
    print("\nControl evidence (verified_by):")
    for control, ref in _verified_by_refs(PROJECT_ROOT / "governance/ai_risk_manifest.yaml"):
        problem = _missing_evidence(ref)
        if problem:
            print(f"  FAIL  {control}: {ref} ({problem})")
            passed = False
        else:
            print(f"  PASS  {control}: {ref}")

    return passed


def _verified_by_refs(manifest_path: Path) -> list[tuple[str, str]]:
    """Every ``verified_by`` entry in the manifest, with the control it backs.

    A control marked implemented on the strength of a file name is a claim; a
    named test that exists is evidence a reviewer can run. Entries are
    ``path::test_name`` (a function in that file) or a plain file path.
    """
    import yaml

    refs: list[tuple[str, str]] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            label = str(node.get("control") or node.get("requirement") or "")
            for ref in node.get("verified_by") or []:
                refs.append((label, str(ref)))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(yaml.safe_load(manifest_path.read_text()))
    return refs


def _missing_evidence(ref: str) -> str:
    path, _, name = ref.partition("::")
    full = PROJECT_ROOT / path
    if not full.is_file():
        return "file missing"
    if name and f"def {name}(" not in full.read_text():
        return "test not found"
    return ""


if __name__ == "__main__":
    ok = check()
    print()
    if ok:
        print("All governance checks PASSED")
        sys.exit(0)
    else:
        print("Governance checks FAILED")
        sys.exit(1)

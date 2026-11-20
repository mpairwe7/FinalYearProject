"""CI/CD governance gate — NIST AI RMF + ISO/IEC 42001 + OWASP + EU AI Act.

Validates that required governance artifacts and security controls exist
in the repository.  Designed to run in CI and block non-compliant merges.

Usage:
    python App/governance/compliance_check.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent

# Files that MUST exist for the governance framework to be considered complete.
REQUIRED_FILES = [
    "App/governance/systems/ura-ai-chatbot/manifest.yaml",
    "App/governance/REGISTRY_README.md",
    "App/governance/OWNERS.md",
    "App/governance/systems/ura-ai-chatbot/fria.md",
    "App/governance/systems/ura-ai-chatbot/change_log.md",
    "App/governance/systems/ura-ai-chatbot/system_prompts.md",
    "App/governance/systems/ura-ai-chatbot/memory.md",
    "App/governance/monitoring/observability.md",
    "App/governance/policies/prohibited_uses.md",
    "App/governance/policies/risk_appetite.md",
    "App/governance/evidence/incident_log/README.md",
    "App/governance/evidence/red_team.md",
    "App/governance/systems/ura-ai-chatbot/agents.md",
    "App/governance/monitoring/cost_per_decision.csv",
    ".github/SECURITY.md",
    "App/backend/app/guardrails/input_checks.py",
    "App/backend/app/guardrails/output_checks.py",
    "App/backend/app/guardrails/pii.py",
    "App/backend/app/guardrails/fences.py",
    "App/backend/app/guardrails/normalize.py",
    "App/backend/app/observability/tracing.py",
    "App/backend/app/rag/retriever.py",
    "App/ml/pipelines/evaluate_rag.py",
    "App/ml/pipelines/quality_gates.py",
    "App/Data/eval/rag_eval.jsonl",
    "App/Data/eval/rag_eval_lg.jsonl",
    # Corpus-coverage gate (#303) — the evidence that abstentions on common
    # taxpayer questions are measured before release, not found in production.
    "App/Data/eval/coverage_bank.jsonl",
    "App/Data/eval/coverage_domains.yaml",
    "App/ml/pipelines/corpus_coverage.py",
    "App/ml/pipelines/export_feedback.py",
    # DevSecOps threat modelling artifacts
    "App/threat-model/tm.py",
    "App/threat-model/validate_threats.py",
    "App/docs/security/threat-model.md",
    "App/docs/security/threat-model/risk-register.yaml",
    "App/docs/security/threat-model/accepted-risks.yaml",
    "App/docs/security/threat-model/threagile.yaml",
    "App/docs/security/threat-model/ccm-mapping.md",
    "App/docs/security/threat-model/workshop/ura-threat-dragon-model.json",
    "App/scripts/validate-risk-register.py",
    ".github/scripts/create-zap-issues.py",
    ".github/zap/baseline.yaml",
    ".github/zap/rules.tsv",
    "App/security/trufflehog-config.yml",
    "App/security/.semgrep/ura-chatbot-rules.yaml",
    "App/security/.bandit.yaml",
    "App/security/.checkov.yaml",
    "App/security/.zap-rules.tsv",
    ".github/workflows/devsecops-sast-dast.yml",
    ".github/workflows/security-trivy.yml",
    ".github/workflows/threat-model.yml",
    ".github/workflows/dependabot-automerge.yml",
    # Monitoring, audit and real-user telemetry (gaps G99–G112)
    "App/monitoring/prometheus.yml",
    "App/monitoring/recording-rules.yml",
    "App/monitoring/alerting-rules.yml",
    "App/monitoring/alertmanager.yml",
    "App/monitoring/slo/ura-chatbot.openslo.yaml",
    "App/monitoring/tests/alerting-rules.test.yml",
    ".github/workflows/monitoring-config.yml",
    "App/backend/app/audit/turns.py",
    "App/backend/app/audit/tsa.py",
    "App/backend/app/observability/security_events.py",
    "App/backend/app/observability/client_telemetry.py",
    "App/backend/app/security/ai_disclosure.py",
    "App/ml/configs/quality_baseline.json",
    # Monorepo standardization & hierarchical agent governance (G208)
    "App/AGENTS.md",
    "App/ml/AGENTS.md",
    "App/monitoring/AGENTS.md",
    "App/threat-model/AGENTS.md",
    "App/plugins/AGENTS.md",
    "App/tests/test_monorepo_architecture_governance.py",
    "App/docs/adr/README.md",
    "App/docs/adr/0001-monorepo-layer-dag-and-agent-governance.md",
    "App/docs/adr/0002-five-locale-multimodal-rag-and-cxp-operating-hours.md",
]

# Content keywords that MUST appear in specific files.
REQUIRED_CONTENT: dict[str, list[str]] = {
    "App/backend/app/guardrails/input_checks.py": ["InputGuard"],
    "App/backend/app/guardrails/output_checks.py": [
        "OutputGuard",
        "redact_pii",
        "check_grounding",
        "should_abstain",
        "should_escalate",
    ],
    "App/backend/app/guardrails/pii.py": ["redact_pii_text", "STORE_RAW_PROMPTS"],
    "App/backend/app/observability/tracing.py": [
        "init_tracing",
        "trace_rag_pipeline",
        "llm_call",
        "trace_tool_call",
        "record_evaluation",
    ],
    "App/backend/app/audit/tsa.py": ["build_request", "verify_token", "_chain_to_anchor"],
    "App/backend/app/audit/turns.py": ["append_turn", "provenance"],
    "App/monitoring/prometheus.yml": ["alerting:", "credentials_file"],
    "App/backend/app/db/database.py": [
        "cleanup_expired_data",
        "export_review_feedback",
        "CONVERSATION_TTL_DAYS",
    ],
    "App/backend/app/api/models.py": [
        "locale",
        "escalation_required",
        "section",
    ],
    "App/governance/systems/ura-ai-chatbot/manifest.yaml": [
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
        "owasp_agentic_2026",
        "ASI01",
        "ASI10",
        "aibom_inventory",
    ],
    "App/threat-model/validate_threats.py": [
        "THREAT_REGISTRY",
        "STRIDE",
        "owasp_llm",
        "mitre_atlas",
        "evidence",
    ],
    "App/scripts/validate-risk-register.py": [
        "VALID_SEVERITIES",
        "VALID_STATUSES",
        "VALID_FRAMEWORKS",
        "check_expired_reviews",
    ],
    "App/ml/pipelines/evaluate_rag.py": [
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
    for control, ref in _verified_by_refs(PROJECT_ROOT / "App/governance/systems/ura-ai-chatbot/manifest.yaml"):
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
            label = str(node.get("control") or node.get("requirement") or node.get("id") or "")
            for ref in node.get("verified_by") or []:
                refs.append((label, str(ref)))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(yaml.safe_load(manifest_path.read_text()))
    return refs


def _declared_functions(source: str) -> set[str]:
    """Names of every function and method declared in *source* (parsed, not grepped).

    A ``def test_x(`` inside a comment or string is not a runnable test.
    """
    import ast

    return {
        node.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _declares(source: str, parts: list[str]) -> bool:
    """True when *source* declares the test a pytest node id names: ``test`` or ``Class::test``."""
    import ast

    if len(parts) == 1:
        return parts[0] in _declared_functions(source)
    scope, node = ast.parse(source).body, None
    for part in parts:
        node = next(
            (n for n in scope if isinstance(n, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) and n.name == part),
            None,
        )
        if node is None:
            return False
        scope = node.body
    return isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)


def _missing_evidence(ref: str) -> str:
    path, _, name = ref.partition("::")
    full = PROJECT_ROOT / path
    if not full.is_file():
        return "file missing"
    if name:
        try:
            found = _declares(full.read_text(), name.split("::"))
        except SyntaxError:
            return "file does not parse"
        if not found:
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

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
    "ml/pipelines/export_feedback.py",
    # DevSecOps threat modelling artifacts
    "threat-model/tm.py",
    "threat-model/validate_threats.py",
    ".semgrep/ura-chatbot-rules.yaml",
    ".bandit.yaml",
    ".checkov.yaml",
    ".zap-rules.tsv",
    ".github/workflows/devsecops-sast-dast.yml",
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
        "record_token_usage",
    ],
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

    return passed


if __name__ == "__main__":
    ok = check()
    print()
    if ok:
        print("All governance checks PASSED")
        sys.exit(0)
    else:
        print("Governance checks FAILED")
        sys.exit(1)

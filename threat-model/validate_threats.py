#!/usr/bin/env python3
"""Validate that all identified threats have documented mitigations.

Runs as a CI gate to ensure the threat model stays in sync with
the actual security controls implemented in the codebase.

``owasp_llm`` IDs in this registry are the **2025** Top 10 labels
(LLM01 injection … LLM10 unbounded consumption). OWASP published the
2026 edition on 2026-08-04 and **renumbered** several entries
(agency is now LLM03:2026; output handling is LLM10:2026; hidden
context is the new LLM08:2026). Coverage is still 10/10 of the 2025
list. See ``governance/ai_risk_manifest.yaml`` ``owasp_llm_2026_crosswalk``.

Usage:
    python threat-model/validate_threats.py

Exit codes:
    0 — All threats have mitigations and evidence files exist
    1 — One or more threats lack mitigations or evidence is missing
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# =============================================================================
# Threat Registry — every identified threat must have:
#   - category: STRIDE category
#   - description: what the threat is
#   - owasp_llm: OWASP LLM Top 10 mapping (if applicable)
#   - mitre_atlas: MITRE ATLAS technique (if applicable)
#   - mitigation: how it's mitigated
#   - evidence: file paths that prove the mitigation exists
#   - status: mitigated | accepted | transferred | in_progress
# =============================================================================
THREAT_REGISTRY: list[dict] = [
    {
        "id": "T01",
        "category": "Spoofing",
        "description": "Attacker impersonates legitimate API client",
        "owasp_llm": None,
        "mitre_atlas": "AML.T0052",
        "mitigation": "API key auth for sensitive endpoints, rate limiting per IP",
        "evidence": [
            "App/backend/app/main.py",
            "App/backend/app/guardrails.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T02",
        "category": "Tampering",
        "description": "Adversary modifies training data (data poisoning)",
        "owasp_llm": "LLM04",
        "mitre_atlas": "AML.T0020",
        "mitigation": "Data validation pipeline, deduplication, source allowlist, signed commits",
        "evidence": [
            "ml/pipelines/validate_data.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T03",
        "category": "Tampering",
        "description": "Malicious dependency injected via supply chain",
        "owasp_llm": "LLM03",
        "mitre_atlas": "AML.T0010",
        "mitigation": "SHA-pinned GH Actions, Dependabot, Trivy SCA, pip-audit, SBOM, license compliance",
        "evidence": [
            ".github/workflows/security-trivy.yml",
            ".github/dependabot.yml",
            ".github/workflows/devsecops-sast-dast.yml",
            ".github/workflows/threat-model.yml",
        ],
        "status": "mitigated",
    },
    {
        "id": "T04",
        "category": "Repudiation",
        "description": "User denies submitting a query; no audit trail",
        "owasp_llm": None,
        "mitre_atlas": None,
        "mitigation": "Conversation logging with timestamps, OpenTelemetry tracing",
        "evidence": [
            "App/backend/app/database.py",
            "App/backend/app/tracing.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T05",
        "category": "Information Disclosure",
        "description": "PII leaks from training data into model responses",
        "owasp_llm": "LLM02",
        "mitre_atlas": "AML.T0024.001",
        "mitigation": "OutputGuard PII redaction (UG phone, TIN, NID, email, CC, passport)",
        "evidence": [
            "App/backend/app/guardrails.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T06",
        "category": "Information Disclosure",
        "description": "Model weights exfiltrated via API probing",
        "owasp_llm": "LLM03",
        "mitre_atlas": "AML.T0044",
        "mitigation": "No direct model access endpoint; retrieval-only API; rate limiting",
        "evidence": [
            "App/backend/app/main.py",
            "App/backend/app/service.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T07",
        "category": "Denial of Service",
        "description": "Rate-limit bypass overwhelms inference endpoint",
        "owasp_llm": "LLM10",
        "mitre_atlas": "AML.T0029",
        "mitigation": "slowapi rate limiter, input length caps, request timeouts, resource limits in Docker",
        "evidence": [
            "App/backend/app/main.py",
            "docker-compose.yml",
        ],
        "status": "mitigated",
    },
    {
        "id": "T08",
        "category": "Elevation of Privilege",
        "description": "Prompt injection alters system behaviour",
        "owasp_llm": "LLM01",
        "mitre_atlas": "AML.T0051",
        "mitigation": "InputGuard regex patterns, input length limits, retrieval-only architecture",
        "evidence": [
            "App/backend/app/guardrails.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T09",
        "category": "Tampering",
        "description": "CORS misconfiguration enables CSRF attacks",
        "owasp_llm": None,
        "mitre_atlas": None,
        "mitigation": "Explicit CORS allowlist (no wildcards with credentials)",
        "evidence": [
            "App/backend/app/main.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T10",
        "category": "Information Disclosure",
        "description": "Server error messages leak internal details",
        "owasp_llm": None,
        "mitre_atlas": None,
        "mitigation": "Generic error responses, no stack traces in production",
        "evidence": [
            "App/backend/app/main.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T11",
        "category": "Spoofing",
        "description": "Attacker compromises GitHub Actions workflow",
        "owasp_llm": None,
        "mitre_atlas": "AML.T0010",
        "mitigation": "SHA-pinned actions, minimal permissions, CODEOWNERS review, SLSA provenance",
        "evidence": [
            ".github/workflows/ci-ml-pipeline.yml",
            "CODEOWNERS",
        ],
        "status": "mitigated",
    },
    {
        "id": "T12",
        "category": "Elevation of Privilege",
        "description": "Improper output handling — XSS via chatbot response",
        "owasp_llm": "LLM05",
        "mitre_atlas": None,
        "mitigation": "OutputGuard HTML/script stripping, frontend output encoding",
        "evidence": [
            "App/backend/app/guardrails.py",
            "App/frontend/src/components/Markdown.tsx",
        ],
        "status": "mitigated",
    },
    {
        "id": "T13",
        "category": "Information Disclosure",
        "description": "Secrets leaked in git history or CI logs",
        "owasp_llm": None,
        "mitre_atlas": None,
        "mitigation": "4-layer secret scanning (TruffleHog + Gitleaks + ggshield + detect-secrets)",
        "evidence": [
            ".github/workflows/secret-scanning.yml",
            ".pre-commit-config.yaml",
        ],
        "status": "mitigated",
    },
    {
        "id": "T14",
        "category": "Tampering",
        "description": "Adversarial examples in retrieval queries (embedding attacks)",
        "owasp_llm": "LLM08",
        "mitre_atlas": "AML.T0043",
        "mitigation": "Cross-encoder reranking validates semantic relevance, trusted-source-only indexing",
        "evidence": [
            "App/backend/app/retriever.py",
            "App/backend/app/indexer.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T15",
        "category": "Information Disclosure",
        "description": "System prompt extraction via crafted queries",
        "owasp_llm": "LLM07",
        "mitre_atlas": "AML.T0051.001",
        "mitigation": "InputGuard blocks 'reveal prompt' patterns; retrieval-only architecture (no system prompt)",
        "evidence": [
            "App/backend/app/guardrails.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T16",
        "category": "Denial of Service",
        "description": "Resource exhaustion via large file uploads or payloads",
        "owasp_llm": "LLM10",
        "mitre_atlas": "AML.T0029",
        "mitigation": "Pydantic input validation with max_length, Docker resource limits, read-only filesystem",
        "evidence": [
            "App/backend/app/models.py",
            "App/backend/app/documents.py",
            "docker-compose.yml",
        ],
        "status": "mitigated",
    },
    {
        "id": "T17",
        "category": "Tampering",
        "description": "Container escape or privilege escalation",
        "owasp_llm": None,
        "mitre_atlas": None,
        "mitigation": "Non-root users, CAP_DROP ALL, read-only root FS, no-new-privileges, seccomp default",
        "evidence": [
            "Dockerfile",
            "docker-compose.yml",
        ],
        "status": "mitigated",
    },
    {
        "id": "T18",
        "category": "Information Disclosure",
        "description": "Misinformation — LLM generates unfaithful answers",
        "owasp_llm": "LLM09",
        "mitre_atlas": "AML.T0048",
        "mitigation": "Runtime faithfulness scoring, calibrated abstention, low-confidence disclaimer",
        "evidence": [
            "App/backend/app/guardrails.py",
            "ml/pipelines/evaluate_rag.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T19",
        "category": "Tampering",
        "description": "IaC misconfiguration in Dockerfiles or CI workflows",
        "owasp_llm": None,
        "mitre_atlas": None,
        "mitigation": "Trivy IaC misconfig scanning, Checkov IaC analysis in CI",
        "evidence": [
            ".github/workflows/security-trivy.yml",
            ".github/workflows/devsecops-sast-dast.yml",
        ],
        "status": "mitigated",
    },
    {
        "id": "T20",
        "category": "Spoofing",
        "description": "Dependency confusion / typosquatting attack",
        "owasp_llm": "LLM03",
        "mitre_atlas": "AML.T0010",
        "mitigation": "pip-audit dependency auditing, Trivy SCA, pinned versions, SBOM generation",
        "evidence": [
            ".github/workflows/devsecops-sast-dast.yml",
            "requirements.txt",
        ],
        "status": "mitigated",
    },
    {
        "id": "T21",
        "category": "Information Disclosure",
        "description": "Mobile model extraction via APK reverse engineering",
        "owasp_llm": "LLM03",
        "mitre_atlas": "AML.T0044",
        "mitigation": "No on-device model APK; inference stays server-side. Residual risk accepted for any future mobile client.",
        "evidence": [
            "docs/capstone/week06-threat-model-security.md",
            "App/README.md",
        ],
        "status": "accepted",
    },
    {
        "id": "T22",
        "category": "Elevation of Privilege",
        "description": "Malicious PDF active content (JavaScript, Launch, embedded files) executes in the API worker",
        "owasp_llm": "LLM04",
        "mitre_atlas": None,
        "mitigation": "Fail-closed PDF guards reject encryption, JS/Launch/GoToR/XFA/embedded files before extract or OCR",
        "evidence": [
            "App/backend/app/pdf_guards.py",
            "App/backend/app/documents.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T23",
        "category": "Denial of Service",
        "description": "PDF/image resource bomb (xref objects, page dimensions, pixels, zip-slip, macros)",
        "owasp_llm": "LLM10",
        "mitre_atlas": "AML.T0029",
        "mitigation": "Header/magic checks, xref and page-edge caps, Office zip-slip/macro/encryption reject, Pillow pixel cap, OCR sidecar magic + concurrency",
        "evidence": [
            "App/backend/app/pdf_guards.py",
            "App/backend/app/documents.py",
            "App/backend/app/ocr_service.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T24",
        "category": "Elevation of Privilege",
        "description": "Indirect prompt injection via uploaded document or retrieved PDF text",
        "owasp_llm": "LLM01",
        "mitre_atlas": "AML.T0051",
        "mitigation": "scan_retrieved_text on extract and on prompt assembly; untrusted_user_document wrappers; system rule that uploads are evidence only",
        "evidence": [
            "App/backend/app/documents.py",
            "App/backend/app/guardrails.py",
            "App/backend/app/llm.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T25",
        "category": "Information Disclosure",
        "description": "Document-store IDOR — attacker fetches another session's upload by document id",
        "owasp_llm": "LLM02",
        "mitre_atlas": None,
        "mitigation": "Session/user binding, unguessable ids, TTL spool, no path built from user ids, analytics redacts attachment text",
        "evidence": [
            "App/backend/app/documents.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T26",
        "category": "Tampering",
        "description": "Polyglot upload (HTML/JS/ZIP) declared as PDF or Office to bypass type checks",
        "owasp_llm": "LLM04",
        "mitre_atlas": "AML.T0020",
        "mitigation": "PDF header must be %PDF- at start; Office ZIP must contain Content_Types + word/ or xl/; images verified by Pillow; client MIME is untrusted",
        "evidence": [
            "App/backend/app/pdf_guards.py",
            "App/backend/app/documents.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T27",
        "category": "Tampering",
        "description": "Index-time PDF poisoning — unsafe extract of a planted handbook during corpus export",
        "owasp_llm": "LLM04",
        "mitre_atlas": "AML.T0020",
        "mitigation": "Same structural PDF guards on export; SHA-256 manifest; ingest rejects hash drift; injection scrub on retrieved chunks",
        "evidence": [
            "App/backend/app/pdf_corpus.py",
            "App/backend/app/pdf_guards.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T28",
        "category": "Elevation of Privilege",
        "description": "Excessive agency — prompt-injected model invokes tools or MCP servers it was not shown",
        "owasp_llm": "LLM06",
        "mitre_atlas": "AML.T0051",
        "mitigation": "MCP policy allowlist, unknown-tool errors without registry leak, bounded ReAct (one retrieve hop), FLAG_TOOL_RAG default off",
        "evidence": [
            "App/backend/app/mcp/policy.py",
            "App/backend/app/flags.py",
        ],
        "status": "mitigated",
    },
]


def validate() -> bool:
    """Validate all threats have mitigations and evidence files exist."""
    passed = True

    print("=" * 70)
    print("THREAT MODEL VALIDATION")
    print("=" * 70)

    # -- Check all threats have required fields --
    print("\n[1/4] Checking threat registry completeness...")
    required_fields = {"id", "category", "description", "mitigation", "evidence", "status"}
    valid_statuses = {"mitigated", "accepted", "transferred", "in_progress"}
    valid_categories = {
        "Spoofing",
        "Tampering",
        "Repudiation",
        "Information Disclosure",
        "Denial of Service",
        "Elevation of Privilege",
    }

    for threat in THREAT_REGISTRY:
        tid = threat.get("id", "UNKNOWN")
        missing = required_fields - set(threat.keys())
        if missing:
            print(f"  FAIL  {tid}: missing fields {missing}")
            passed = False
        if threat.get("status") not in valid_statuses:
            print(f"  FAIL  {tid}: invalid status '{threat.get('status')}'")
            passed = False
        if threat.get("category") not in valid_categories:
            print(f"  FAIL  {tid}: invalid STRIDE category '{threat.get('category')}'")
            passed = False

    if passed:
        print(f"  PASS  All {len(THREAT_REGISTRY)} threats have complete definitions")

    # -- Check evidence files exist --
    print("\n[2/4] Checking evidence files exist...")
    for threat in THREAT_REGISTRY:
        tid = threat.get("id", "UNKNOWN")
        for epath in threat.get("evidence", []):
            full = PROJECT_ROOT / epath
            if full.exists():
                print(f"  PASS  {tid} → {epath}")
            else:
                print(f"  FAIL  {tid} → {epath} (file not found)")
                passed = False

    # -- Check STRIDE coverage --
    print("\n[3/4] Checking STRIDE category coverage...")
    covered = {t["category"] for t in THREAT_REGISTRY}
    for cat in valid_categories:
        if cat in covered:
            count = sum(1 for t in THREAT_REGISTRY if t["category"] == cat)
            print(f"  PASS  {cat}: {count} threat(s)")
        else:
            print(f"  WARN  {cat}: no threats identified (review needed)")

    # -- Check OWASP LLM Top 10 coverage --
    print("\n[4/4] Checking OWASP LLM Top 10 coverage...")
    owasp_ids = {f"LLM{i:02d}" for i in range(1, 11)}
    covered_owasp = {
        t["owasp_llm"]
        for t in THREAT_REGISTRY
        if t.get("owasp_llm")
    }
    for oid in sorted(owasp_ids):
        if oid in covered_owasp:
            threats = [
                t["id"]
                for t in THREAT_REGISTRY
                if t.get("owasp_llm") == oid
            ]
            print(f"  PASS  {oid}: mapped to {', '.join(threats)}")
        else:
            print(f"  WARN  {oid}: no threat mapped (check if applicable)")

    # -- Summary --
    print("\n" + "=" * 70)
    total = len(THREAT_REGISTRY)
    mitigated = sum(1 for t in THREAT_REGISTRY if t["status"] == "mitigated")
    accepted = sum(1 for t in THREAT_REGISTRY if t["status"] == "accepted")
    in_progress = sum(1 for t in THREAT_REGISTRY if t["status"] == "in_progress")
    print(f"Total threats: {total}")
    print(f"  Mitigated:   {mitigated}")
    print(f"  Accepted:    {accepted}")
    print(f"  In progress: {in_progress}")
    print(f"STRIDE categories covered: {len(covered)}/6")
    print(f"OWASP LLM IDs covered:     {len(covered_owasp)}/10")

    return passed


if __name__ == "__main__":
    ok = validate()
    print()
    if ok:
        print("Threat model validation PASSED")
        sys.exit(0)
    else:
        print("Threat model validation FAILED")
        sys.exit(1)

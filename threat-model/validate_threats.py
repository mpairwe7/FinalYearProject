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
            ".github/workflows/dependabot-automerge.yml",
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
        "mitigation": "InputGuard blocks reveal-prompt probes and OutputGuard scrubs signature phrases; prompt confidentiality is not treated as a security boundary (OWASP LLM08:2026)",
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
    {
        "id": "T29",
        "category": "Elevation of Privilege",
        "description": "Autonomous agent execution abuse via indirect prompt injection in National ID OCR images",
        "owasp_llm": "LLM06",
        "mitre_atlas": "AML.T0051",
        "mitigation": "Structured regex extraction isolating 14-char NIN and phone from free-form text, document classification priority gate, PII input guard validation before tool dispatch",
        "evidence": [
            "App/backend/app/vision/ocr.py",
            "App/backend/app/vision/document_classifier.py",
            "App/backend/app/service.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T30",
        "category": "Elevation of Privilege",
        "description": "SSRF and internal network probing via external connector registration (POST /v1/connectors/register)",
        "owasp_llm": "LLM06",
        "mitre_atlas": "AML.T0052",
        "mitigation": "Dynamic registration and runtime configuration are disabled (POST /v1/connectors/register and /configure return 410 Gone); a remote MCP endpoint comes only from deployment configuration (MCP_SERVER_URL_<NAMESPACE>), must be http(s) with no embedded credentials and https in production, redirects are never followed, and simulators are refused in production",
        "evidence": [
            "App/backend/app/routes/connectors.py",
            "App/backend/app/mcp/transport.py",
            "App/backend/app/mcp/chat_connectors.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T31",
        "category": "Spoofing",
        "description": "Automated credential stuffing and XSS injection across standalone enterprise system web portals",
        "owasp_llm": "LLM05",
        "mitre_atlas": "AML.T0052",
        "mitigation": "Mandatory Auth Gate on portal root, dynamic server-validated HTML5 canvas CAPTCHA with Bezier distortion curves, HTML entity escaping across all rendered database fields, non-root appuser in Dockerfile.plugins",
        "evidence": [
            "plugins/apps/common_ui.py",
            "plugins/apps/efris_app.py",
            "plugins/apps/dts_app.py",
            "Dockerfile.plugins",
        ],
        "status": "mitigated",
    },
    {
        "id": "T32",
        "category": "Tampering",
        "description": "Enterprise system state manipulation via duplicate obligations, credit-note over-crediting, or unpaid stamp activation",
        "owasp_llm": "LLM06",
        "mitre_atlas": "AML.T0020",
        "mitigation": "Cumulative credited balance subtraction, rejection of cancelled invoices, order payment validation, line controller ownership check, exact matching in TIN/URSB lookups, non-idempotent tool tagging",
        "evidence": [
            "plugins/efris/service.py",
            "plugins/digital_tax_stamps/service.py",
            "plugins/bwims/service.py",
            "plugins/tin_registration/service.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T33",
        "category": "Information Disclosure",
        "description": "Cross-tenant and cross-taxpayer data exposure in enterprise connector databases",
        "owasp_llm": "LLM02",
        "mitre_atlas": "AML.T0024",
        "mitigation": "Isolated per-system SQLite stores in data_store/, exact primary key lookups, principal binding on stock and consignment clearances, no shared connection pools",
        "evidence": [
            "plugins/efris/database.py",
            "plugins/bwims/database.py",
            "plugins/tin_registration/database.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T34",
        "category": "Spoofing",
        "description": "Unauthenticated access to a connector MCP server, and role escalation by asserting a caller identity in the request body (_meta.userRole), or by replaying a token issued for another server",
        "owasp_llm": "LLM06",
        "mitre_atlas": "AML.T0052",
        "mitigation": "Every HTTP request to an MCP server is authenticated: an audience-bound RS256 access token as an OAuth 2.1 resource server (issuer, expiry and audience checked; RFC 9728 metadata), or a deployment-managed shared token of at least 32 characters compared in constant time; a server with no credential is locked; identity in the body is honoured only on an authenticated request; credentials are per system; Origin is allow-listed and headers must agree with the body (HeaderMismatch)",
        "evidence": [
            "App/backend/app/mcp/auth.py",
            "App/backend/app/mcp/servers/wire.py",
            "App/backend/tests/test_mcp_wire_conformance.py",
            "tests/agents/test_connector_link_integration.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T35",
        "category": "Tampering",
        "description": "MCP tool poisoning, full-schema poisoning, or post-approval rug-pull descriptor mutation (SAFE-MCP SAF-T1001/SAF-T1201, OWASP MCP03/MCP08)",
        "owasp_llm": "LLM03",
        "mitre_atlas": "AML.T0110",
        "mitigation": "Canonical SHA-256 descriptor fingerprinting (compute_descriptor_sha256) over name, description, inputSchema, outputSchema, annotations, and securityMeta; inspect_descriptor_integrity poisoning scan; TTL and version-scoped descriptor pinning (_pinned_descriptors) in MCPClient",
        "evidence": [
            "App/backend/app/mcp/validation.py",
            "App/backend/app/mcp/client.py",
            "tests/agents/test_mcp_2026_october_audit.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T36",
        "category": "Elevation of Privilege",
        "description": "Cross-server MCP tool shadowing and namespace hijacking by a compromised remote MCP server (SAFE-MCP SAF-T1301, OWASP MCP09)",
        "owasp_llm": "LLM06",
        "mitre_atlas": "AML.T0073",
        "mitigation": "MCPClient._check_cross_namespace_shadowing refuses any remote tool whose name belongs to another canonical namespace in ToolRegistry; inspect_descriptor_integrity blocks cross-tool override directives",
        "evidence": [
            "App/backend/app/mcp/client.py",
            "App/backend/app/mcp/validation.py",
            "tests/agents/test_mcp_2026_october_audit.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T37",
        "category": "Elevation of Privilege",
        "description": "Indirect prompt injection, Unicode control/bidi smuggling, and credential harvesting via MCP tool responses (SAFE-MCP SAF-T1102/SAF-T1504, OWASP MCP01/MCP06)",
        "owasp_llm": "LLM01",
        "mitre_atlas": "AML.T0098",
        "mitigation": "screen_tool_result strips zero-width/bidi control characters, quarantines chat-template control tokens, covert exfiltration directives, and raw JWT/API secret leaks in MCPClient and StatelessMCPServer, and scrubs natural-language override phrases",
        "evidence": [
            "App/backend/app/mcp/validation.py",
            "App/backend/app/mcp/client.py",
            "App/backend/app/mcp/servers/wire.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T38",
        "category": "Tampering",
        "description": "Confused-deputy persistent memory implant and cross-tenant fact poisoning via MCP tool arguments (SAFE-MCP SAF-T1204/SAF-T1307, OWASP MCP02/MCP10)",
        "owasp_llm": "LLM06",
        "mitre_atlas": "AML.T0080",
        "mitigation": "memory_store and memory_discard require personalization consent or verified role; ToolRegistry.call strips argument-supplied user_id/tenant_id and injects only verified caller identity from _meta; memory_discard enforces tenant_id SQL isolation",
        "evidence": [
            "App/backend/app/tools/memory_tools.py",
            "App/backend/app/tools/__init__.py",
            "App/backend/app/mcp/transport.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T39",
        "category": "Tampering",
        "description": "Unsigned or alg=none Multi Round-Trip Request (MRTR) state forgery or stripped-signature replay across MCP turns (OWASP MCP07, RFC 8725)",
        "owasp_llm": "LLM06",
        "mitre_atlas": "AML.T0053",
        "mitigation": "mrtr.py refuses alg=none and requires MCP_REQUEST_STATE_SECRET (>=32 chars) in production; MCPClient._call_tool enforces unwrap_request_state on any non-None requestState when secret or production mode is active",
        "evidence": [
            "App/backend/app/mcp/mrtr.py",
            "App/backend/app/mcp/client.py",
            "tests/agents/test_mcp_2026_october_audit.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T40",
        "category": "Denial of Service",
        "description": "Remote MCP server oversized response memory bomb, multi-year cache-TTL poisoning, or cloud-metadata/loopback SSRF in production (OWASP MCP10/LLM10, MAESTRO L4)",
        "owasp_llm": "LLM10",
        "mitre_atlas": "AML.T0029",
        "mitigation": "HttpTransport enforces MAX_MCP_RESPONSE_BYTES (2 MiB), clamps _cache_seconds to MAX_CACHE_TTL_S (24h), and remote_url_problem rejects loopback/link-local/metadata IPs (127.0.0.1, ::1, 169.254.169.254) in production",
        "evidence": [
            "App/backend/app/mcp/transport.py",
            "tests/agents/test_mcp_2026_october_audit.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T41",
        "category": "Information Disclosure",
        "description": "A taxpayer's support case (reason, query, transcript, officer reply) read, or written to, through the login-free case routes or the chat's TIC-/KB- reference lookup by someone who does not own it (OWASP API1:2023 BOLA, G243)",
        "owasp_llm": "LLM02",
        "mitre_atlas": "",
        "mitigation": "Case routes answer to the exact ticket UUID only (_TICKET_ID_PATH_PATTERN + db.get_ticket, no search fallback); the chat reference lookup runs after the input guard and crisis check and answers only the owning subject or session; list_tickets escapes LIKE wildcards in both backends",
        "evidence": [
            "App/backend/app/main.py",
            "App/backend/app/service.py",
            "tests/test_answer_integrity_integration.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T42",
        "category": "Elevation of Privilege",
        "description": "Agentic portal task confirmation tampering, cross-session replay, step-up OAuth open-redirect (returnTo), or DOM co-pilot secret harvesting (ADR-0013, ADR-0014, OWASP ASI02/ASI03/ASI09, TPCA Cap. 343 s. 75)",
        "owasp_llm": "LLM06",
        "mitre_atlas": "AML.T0051",
        "mitigation": "4-tier portal task policy engine (portal_copilot.py), HMAC-SHA256 signed request_state bound to owner_key and idempotency_key on POST /v1/portal/tasks/execute, TPCA s.75 subject-TIN ownership verification, sanitizeReturnTo CWE-601 guard in oidcFlow.ts, and sanitize_portal_context secret scrubbing",
        "evidence": [
            "App/backend/app/portal_copilot.py",
            "App/frontend/src/lib/oidcFlow.ts",
            "tests/agents/test_portal_copilot.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T43",
        "category": "Elevation of Privilege",
        "description": "Live web research SSRF, DNS rebinding to internal/metadata IPs, or malicious statutory/MDA PDF extraction outside the ephemeral sandbox (ADR-0008, ADR-0009, OWASP ASI04/ASI05/LLM01)",
        "owasp_llm": "LLM01",
        "mitre_atlas": "AML.T0052",
        "mitigation": "Domain allowlisting (ura.go.ug, parliament.go.ug, bou.or.ug, ulii.org, etc.), follow_redirects=False with every hop re-validated, a pre-connect DNS check refusing non-public addresses (resolves_to_public_address; narrows but does not close rebinding, because the HTTP client resolves again), and isolated subprocess extraction in ephemeral_pdf_sandbox.py with structural pdf_guards.py checks",
        "evidence": [
            "App/backend/app/web_research.py",
            "App/backend/app/ephemeral_pdf_sandbox.py",
            "tests/agents/test_ephemeral_pdf_sandbox.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T46",
        "category": "Tampering",
        "description": "Indirect prompt injection through live web content: a page reachable by search steers the answer, launders a figure past the claim verifier, or attributes foreign text to an official source (ADR-0021, G310-G314)",
        "owasp_llm": "LLM01",
        "mitre_atlas": "AML.T0051.001",
        "mitigation": "Search control flow is decided from the question and the model has no search tool. Web text reaches the model only as scanned, spotlighted passages labelled with authority, evidence and age, never the prompt or verified_facts. Instruction-shaped results are dropped whole. Regex screening can be evaded; the channel boundary is what holds when it misses",
        "evidence": [
            "App/backend/app/web_research.py",
            "App/backend/app/llm.py",
            "tests/agents/test_web_evidence_boundary.py",
            "tests/agents/test_web_research_adversarial_eval.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T47",
        "category": "Information Disclosure",
        "description": "Taxpayer personal data leaving Uganda in outbound search queries or reaching web_sync_ledger keys (ADR-0021, G315)",
        "owasp_llm": "LLM02",
        "mitre_atlas": "AML.T0057",
        "mitigation": "Queries and topic keys are built from a closed vocabulary of generic tax terms, so an unknown word cannot leave. Hosted providers are external processors under the G35 cross-border gate and are refused in sovereign mode. The transfer assessment itself is outstanding (G326)",
        "evidence": [
            "App/backend/app/web_research.py",
            "App/backend/app/production_readiness.py",
            "tests/agents/test_web_evidence_boundary.py",
            "tests/agents/test_web_search_providers.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T44",
        "category": "Tampering",
        "description": "MCP cross-tool/cross-user requestState replay, idempotency_key parameter mutation replay, or Markdown-image exfiltration in tool outputs and resource URIs (ADR-0015, G250, OWASP MCP01/MCP02/MCP06)",
        "owasp_llm": "LLM06",
        "mitre_atlas": "AML.T0098",
        "mitigation": "Context- and TTL-bound HMAC-SHA256 requestState (_binding: tool_name, namespace, tenant_id, user_id, exp), user-scoped idempotency keys with canonical _mcp_args_sha256 conflict rejection (idempotency_key_conflict), pre-authz coerce_arguments_to_schema, markdown_exfiltration_link & extended secret-leak guards, and validate_resource_uri path-traversal blocking",
        "evidence": [
            "App/backend/app/mcp/mrtr.py",
            "App/backend/app/mcp/client.py",
            "App/backend/app/mcp/validation.py",
            "tests/agents/test_mcp_toolathlon_2026_october.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T45",
        "category": "Information Disclosure",
        "description": "Visual prompt injection in OCR/VLM uploads or unredacted 14-character Uganda National ID (NIN) exposure across tickets, transcripts, and TTS speech normalization (G243, G247, G248, G249, OWASP LLM01/LLM02/ASI01)",
        "owasp_llm": "LLM02",
        "mitre_atlas": "AML.T0024.001",
        "mitigation": "strip_hidden_control_and_bidi, scan_retrieved_text, and <untrusted_user_document> wrapping on OCR/VLM paths; unified 13-15 char C[MFR] NIN redaction across guardrails.py, StreamingDLPFilter, Semgrep, and gitleaks; and localized spoken redaction labels in speech_normalization.py",
        "evidence": [
            "App/backend/app/guardrails.py",
            "App/backend/app/speech_normalization.py",
            "tests/agents/test_nin_redaction.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T48",
        "category": "Elevation of Privilege",
        "description": "Autonomy gate bypass: a high-risk or critical tool classified as low by a name table, a refusal released because its audit write failed, or a gate fault treated as permission, letting a model-proposed action run without the approval its tier requires.",
        "owasp_llm": "LLM06",
        "mitre_atlas": "AML.T0053",
        "mitigation": "ToolSchema.risk is the floor of the tier and a table entry may only raise it; an unrated action is held as high; decision and record are separate, so a refusal does not depend on being written down; a gate that cannot decide fails closed except for a pure low-risk read; faults are counted and alerted (ADR-0022).",
        "evidence": [
            "App/backend/app/audit/governance_v3.py",
            "App/backend/app/tools/__init__.py",
            "tests/agents/test_autonomy_gate_contract.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T49",
        "category": "Spoofing",
        "description": "Confused deputy: an identifier taken from one tool's output chooses whose record the next tool reads, or a stored fact fills a calculator slot it does not mean, producing a confident figure on the wrong base or the wrong taxpayer.",
        "owasp_llm": "LLM06",
        "mitre_atlas": "AML.T0053",
        "mitigation": "Identity slots never bind from tool observations; a calculator slot binds only keys with the same meaning; what was filled from memory is recorded and stated in the reply (ADR-0022, G355). A model-chosen tool route gets a closed read-only list (G357).",
        "evidence": [
            "App/backend/app/agents/graphs/main_graph.py",
            "App/backend/app/agents/tiebreak.py",
            "tests/agents/test_reasoner_integrity.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T50",
        "category": "Repudiation",
        "description": "A turn or action leaves no evidence or false evidence: a failed or abandoned streamed turn with no audit row, a tool that raised with none, a call recorded as proposed after it ran, or an attestation that passes because it supplied the missing guard itself.",
        "owasp_llm": None,
        "mitre_atlas": None,
        "mitigation": "Every turn writes one row with a disposition and what generated it; a failed tool call is recorded; the record states the decision taken rather than re-evaluating the gate; a turn that served model text must show a guard stage to attest (ADR-0022).",
        "evidence": [
            "App/backend/app/service.py",
            "App/backend/app/audit/turns.py",
            "App/backend/app/turn_pipeline.py",
            "tests/agents/test_turn_terminal_contract.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T51",
        "category": "Denial of Service",
        "description": "Cascading loss of service with no error: a half-open breaker wedged by a probe that never reported, a down reasoner costing every turn its deadline, translation blocking the event loop for every stream on a worker, an unbounded turn, or an abandoned tool loop still spending and acting.",
        "owasp_llm": "LLM10",
        "mitre_atlas": "AML.T0029",
        "mitigation": "The half-open probe is a lease; each tier has its own breaker; one deadline bounds every stage; a cancel scope stops the tool loop; blocking work runs off the event loop; transitions, lost probes and degraded answers are alerted (ADR-0022).",
        "evidence": [
            "App/backend/app/resilience.py",
            "App/backend/app/service.py",
            "tests/agents/test_fault_tolerance_contract.py",
        ],
        "status": "mitigated",
    },
    {
        "id": "T52",
        "category": "Tampering",
        "description": "An unverified or contradicted tax figure reaches the taxpayer: streamed before the guards read it, served by the graph path with no guards, or invented where the sources hold no figure of that kind.",
        "owasp_llm": "LLM09",
        "mitre_atlas": "AML.T0048",
        "mitigation": "FigureReleaseGate holds a stream from its first figure until verified; every graph answer passes the output guards; the figure lock runs on every transport and enforces on the reasoning tier. Enforcement on other tiers awaits shadow evidence (G360), so this is mitigated in part.",
        "evidence": [
            "App/backend/app/guardrails.py",
            "App/backend/app/agents/evaluator.py",
            "tests/agents/test_reasoner_integrity.py",
            "tests/agents/test_graph_orchestrator_contract.py",
        ],
        "status": "in_progress",
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

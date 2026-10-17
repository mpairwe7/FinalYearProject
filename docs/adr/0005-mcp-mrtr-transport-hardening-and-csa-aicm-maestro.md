# ADR-0005: MCP Multi Round-Trip State (MRTR) & HTTP Transport Hardening, 5-Locale Tool-RAG Parity, and CSA AICM v1.1 + MAESTRO 7-Layer Governance

## Status

Accepted — 2026-10-17

## Context and Problem Statement

A second-pass October 2026 QA, security, and AI governance audit across `App/backend/app/mcp/mrtr.py`, `App/backend/app/mcp/transport.py`, `App/backend/app/mcp/tool_rag.py`, `docs/security/threat-model/ccm-mapping.md`, and `docs/capstone/sbom-cyclonedx.json` against **RFC 8725**, **OWASP MCP07/MCP10**, **CSA AI Controls Matrix (AICM v1.1)**, and **CSA MAESTRO 7-Layer Threat Modeling** identified four remaining gaps:

1. **Fail-Open `alg: "none"` & Stripped-Signature `requestState` in Multi Round-Trip Requests (`mrtr.py`, `OWASP MCP07`, `RFC 8725`)**: In production, `unwrap_request_state` accepted `alg: "none"` if `MCP_REQUEST_STATE_SECRET` was unset or short, and `MCPClient._call_tool` skipped `unwrap_request_state` if a caller omitted both `"alg"` and `"sig"` from a `request_state` dictionary.
2. **Unbounded Remote MCP Response Size, Unclamped `ttlMs` & Production Loopback/Metadata SSRF (`HttpTransport`, `OWASP MCP10/LLM10`, `MAESTRO L4`)**: `HttpTransport._request` had no maximum response byte cap, `_cache_seconds` had no upper bound on server-supplied `ttlMs`, and `remote_url_problem` did not block loopback/link-local metadata IPs (`127.0.0.1`, `::1`, `169.254.169.254`) in production.
3. **5-Locale Parity Gap in `ToolRAGSelector` (`tool_rag.py`)**: `_MULTILINGUAL_TAX_SYNONYMS` included Luganda (`lg`) and Kiswahili (`sw`) tax stems but lacked Runyankole (`nyn`) and Acholi (`ach`) stems required by `ADR-0002`.
4. **CSA AICM v1.1, MAESTRO 7-Layer & CycloneDX 1.6 ML-BOM Drift (`ccm-mapping.md`, `sbom-cyclonedx.json`, `ai_risk_manifest.yaml`)**: `docs/capstone/sbom-cyclonedx.json` contained a dangling `"qwen2.5-3b-instruct"` reference and outdated model list, and `ccm-mapping.md` / `ai_risk_manifest.yaml` lacked explicit CSA AICM v1.1 (`AA`, `AIS`, `MS`, etc.) and MAESTRO `L1`–`L7` crosswalks.

## Decision Outcome

We implemented a 4-phase closure (`G211`):

1. **Fail-Closed MRTR State Signing & Verification**:
   - `mrtr.py` requires `MCP_REQUEST_STATE_SECRET` ($\ge 32$ chars) and rejects `alg: "none"` in production; `MCPClient._call_tool` enforces `unwrap_request_state` on any non-null `request_state` when a secret or production mode is active.
2. **`HttpTransport` Payload Cap, TTL Clamping & Production Loopback/Metadata IP Block**:
   - Added `MAX_MCP_RESPONSE_BYTES` (2 MiB default) and `MAX_CACHE_TTL_S` (86,400s / 24h) in `App/backend/app/mcp/transport.py`, and rejected loopback, unspecified, and link-local metadata IPs in `remote_url_problem` when `APP_ENV=production`.
3. **5-Locale (`en`, `lg`, `sw`, `nyn`, `ach`) `ToolRAGSelector` Parity**:
   - Added Runyankole (`omushoro`, `emishoro`, `okushashura`, `okubara`, `omushahara`, `ebyensharo`) and Acholi (`mucoro`, `culu`, `kwano`, `mucara`, `otwel`, `biacara`) tax stems to `_MULTILINGUAL_TAX_SYNONYMS` in `App/backend/app/mcp/tool_rag.py`.
4. **CSA AICM v1.1 + MAESTRO `L1`–`L7` & CycloneDX 1.6 ML-BOM Synchronization**:
   - Added `T39`–`T40` to `threat-model/validate_threats.py` (`40/40` threats) and `M-AGT-08`–`M-AGT-09` to `docs/security/threat-model/risk-register.yaml` (`34` risks).
   - Synchronized `docs/security/threat-model/ccm-mapping.md`, `docs/capstone/sbom-cyclonedx.json` (v2.0.0 with verified `bom-ref` graph), and `csa_aicm_maestro_2026` (`MAESTRO-L1`–`MAESTRO-L7`) in `governance/ai_risk_manifest.yaml`.

## Consequences

- **Positive**: Eliminates unsigned MRTR state forgery, bounds remote MCP memory/cache consumption, extends token-overlap Tool-RAG to all 5 supported Ugandan locales, and aligns threat modeling and ML-BOM artifacts with CSA AICM v1.1 and MAESTRO `L1`–`L7`.
- **Negative / Trade-offs**: Production deployments using MRTR elicitations must configure `MCP_REQUEST_STATE_SECRET` ($\ge 32$ characters).

## Confirmation

Validated continuously in CI by:
- `tests/agents/test_mcp_2026_october_audit.py`
- `tests/test_monorepo_architecture_governance.py`
- `python3 threat-model/validate_threats.py`
- `python3 scripts/validate-risk-register.py`
- `python3 governance/compliance_check.py`

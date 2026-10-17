# ADR-0004: MCP Security (OWASP MCP01–10 / SAFE-MCP), MITRE ATLAS v5.x Threat Modeling & AI Governance Synchronization

## Status

Accepted — 2026-10-17

## Context and Problem Statement

A comprehensive October 2026 QA, security, and governance audit across `App/backend/app/mcp/`, `threat-model/`, `docs/security/threat-model/`, and `governance/` against the **OWASP MCP Top 10 (2026)**, **SAFE-MCP Attack Matrix**, **OWASP MAESTRO Multi-Agent Threat Modeling Guide**, and **MITRE ATLAS v5.x (2026.09)** identified five cross-cutting security and threat-modeling gaps:

1. **Cross-Server Tool Shadowing (`OWASP MCP09`, `SAFE-MCP SAF-T1301`, `AML.T0073`)**: A remote MCP server bound to namespace $A$ could advertise a tool name registered in `ToolRegistry` under canonical namespace $B$, hijacking calls intended for $B$, or embed cross-tool override instructions (`"instead of calling X..."`).
2. **Unicode Control & Bidirectional Smuggling (`OWASP MCP03/MCP06`, `AML.T0051.001`)**: Zero-width characters (`U+200B`–`U+200F`, `U+2060`, `U+FEFF`) and bidirectional override controls (`U+202A`–`U+202E`, `U+2066`–`U+2069`) inside tool descriptions or tool outputs could evade regex-based poisoning/injection checks.
3. **Descriptor Pinning Lifecycle (`OWASP MCP08`, `SAFE-MCP SAF-T1201`)**: Process-wide tool descriptor pins needed namespace + `serverVersion` + TTL (`MCP_DESCRIPTOR_PIN_TTL_S`) scoping and a staff/admin re-pin control (`MCPClient.clear_descriptor_pins`) so legitimate remote server redeployments can rotate cleanly without weakening mid-session rug-pull detection.
4. **Threat Model & Risk Register Drift (`threat-model/validate_threats.py`, `tm.py`, `risk-register.yaml`, `accepted-risks.yaml`)**: `THREAT_REGISTRY` (`T01`–`T34`) and `risk-register.yaml` lacked explicit coverage of MCP tool poisoning/rug-pull (`AML.T0110/AML.T0081`), cross-server tool shadowing (`AML.T0073`), tool-response credential harvesting (`AML.T0098`), and confused-deputy memory implant (`AML.T0080`); `accepted-risks.yaml` still listed `T-LLM-04` as accepted after `risk-register.yaml` marked it `Mitigated`; and `tm.py` / `threagile.yaml` referenced superseded model names.
5. **AI Risk Manifest Coverage (`governance/ai_risk_manifest.yaml`)**: Needed explicit `owasp_mcp_top10_2026` (`MCP01`–`MCP10`) and `mitre_atlas_agentic_2026` crosswalks with runnable `verified_by` test references.

## Decision Outcome

We implemented a 4-phase security, threat-model, and governance closure (`G210`):

1. **Cross-Namespace Shadowing & Unicode Control Defense**:
   - Added `MCPClient._check_cross_namespace_shadowing` to block any remote transport from advertising or serving a tool name owned by another canonical namespace in `ToolRegistry`.
   - Added `_UNICODE_CONTROL_RE`, `_BIDI_OVERRIDE_RE`, and `cross_tool_shadowing_directive` to `App/backend/app/mcp/validation.py` so `inspect_descriptor_integrity` and `screen_tool_result` detect bidi smuggling and zero-width obfuscated prompt overrides.
2. **TTL & Version-Scoped Descriptor Pinning + Staff Re-Pin API**:
   - Scoped `_pinned_descriptors` by `(namespace, tool_name) -> (sha256, expires_at, server_version)` with `MCP_DESCRIPTOR_PIN_TTL_S` (default 3600s) and added `MCPClient.clear_descriptor_pins(namespace, user_role)` restricted to `ura_staff` and `ura_admin`.
3. **Executable Threat Model & Risk Register Synchronization**:
   - Added `T35`–`T38` to `threat-model/validate_threats.py` and `M-AGT-04`–`M-AGT-07` to `docs/security/threat-model/risk-register.yaml` and `docs/security/threat-model.md`.
   - Updated `threat-model/tm.py` and `docs/security/threat-model/threagile.yaml` to reflect `Sunbird/Sunflower-Qwen3.5-9B`, `BAAI/bge-m3`, `mixedbread-ai/mxbai-rerank-base-v2`, `mcp_integrity_gate`, and the 169-PDF / 45-FAQ corpus.
   - Reconciled `docs/security/threat-model/accepted-risks.yaml` with `risk-register.yaml` and enforced bidirectional parity in `scripts/validate-risk-register.py`.
4. **Sovereign AI Governance Manifest (`governance/ai_risk_manifest.yaml`)**:
   - Added `owasp_mcp_top10_2026` (`MCP01`–`MCP10`) and `mitre_atlas_agentic_2026` with runnable `verified_by` test references validated by `governance/compliance_check.py`.

## Consequences

- **Positive**: 100% alignment across `App/backend/app/mcp/`, `threat-model/`, `docs/security/threat-model/`, and `governance/ai_risk_manifest.yaml` under OWASP MCP Top 10 (2026), SAFE-MCP, MAESTRO, and MITRE ATLAS v5.x.
- **Negative / Trade-offs**: Every new `"Accepted"` risk in `risk-register.yaml` must have a matching entry in `accepted-risks.yaml` (and vice versa), enforced by `scripts/validate-risk-register.py` in CI.

## Confirmation

Validated continuously in CI by:
- `python3 threat-model/validate_threats.py`
- `python3 scripts/validate-risk-register.py`
- `python3 governance/compliance_check.py`
- `tests/agents/test_mcp_2026_october_audit.py`
- `tests/test_monorepo_architecture_governance.py`

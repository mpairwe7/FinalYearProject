# Architecture Decision Records (MADR 4.0 Catalog)

This directory contains **Markdown Architectural Decision Records (MADR 4.0)** governing the URA AI Taxpayer Assistant monorepo, runtime boundaries, multilingual RAG/speech stack, and sovereign AI governance controls.

## ADR Index

| ADR | Title | Status | Date | Validated By |
| :--- | :--- | :---: | :---: | :--- |
| [ADR-0001](0001-monorepo-layer-dag-and-agent-governance.md) | Unidirectional Monorepo Layer DAG, Hierarchical `AGENTS.md` & OWASP ASI 2026 + CycloneDX AIBOM Gate | Accepted | 2026-10-17 | `tests/test_monorepo_architecture_governance.py`, `governance/compliance_check.py` |
| [ADR-0002](0002-five-locale-multimodal-rag-and-cxp-operating-hours.md) | 5-Locale (`en`, `lg`, `sw`, `nyn`, `ach`) Multimodal Speech/RAG Parity & CXP Operating Hours (`07:00–23:00 EAT`) SLA Calendar | Accepted | 2026-10-17 | `tests/agents/test_agentic_voice_2026_enhancements.py`, `App/backend/tests/test_officer_hours.py` |
| [ADR-0003](0003-mcp-tool-integrity-and-2026-statutory-corpus.md) | MCP Tool Descriptor Integrity, Rug-Pull Pinning & 2024–2027 Statutory Corpus Integration | Accepted | 2026-10-17 | `App/backend/tests/test_mcp_2026_october_audit_and_corpus.py`, `tests/agents/test_mcp_2026_october_audit.py` |
| [ADR-0004](0004-mcp-security-threat-model-and-ai-governance-2026.md) | MCP Security (OWASP MCP01–10 / SAFE-MCP), MITRE ATLAS v5.x Threat Modeling & AI Governance Synchronization | Accepted | 2026-10-17 | `threat-model/validate_threats.py`, `scripts/validate-risk-register.py`, `governance/compliance_check.py` |
| [ADR-0005](0005-mcp-mrtr-transport-hardening-and-csa-aicm-maestro.md) | MCP Multi Round-Trip State (MRTR) & HTTP Transport Hardening, 5-Locale Tool-RAG Parity, and CSA AICM v1.1 + MAESTRO 7-Layer Governance | Accepted | 2026-10-17 | `tests/agents/test_mcp_2026_october_audit.py`, `tests/test_monorepo_architecture_governance.py` |
| [ADR-0006](0006-backend-resilience-security-headers-and-pool-hardening.md) | Backend Setup Hardening — CircuitBreaker Single-Probe HALF_OPEN, Global JSON Payload Ceiling, OWASP REST Security Headers & Graceful Pool Shutdown | Accepted | 2026-10-17 | `App/backend/tests/test_resilience.py`, `tests/agents/test_mcp_2026_october_audit.py` |
| [ADR-0007](0007-sovereign-local-reasoning-tier-and-code-owned-tax-logic.md) | Sovereign Two-Tier Local Reasoning (`Qwen/Qwen3.8-27B-FP8` + `Sunbird/Sunflower-Qwen3.5-9B`), Code-Owned Tax Scenario Planner & Deterministic Figure Lock | Accepted | 2026-10-20 | `App/backend/tests/test_sovereign_mode.py`, `tests/agents/test_model_endpoints.py`, `tests/agents/test_scenario_planner.py`, `tests/agents/test_polarity_and_figure_lock.py` |

## Rules

- Every architectural, protocol, model-stack, or boundary change must add or update an ADR in this directory and register it in the table above.
- `tests/test_monorepo_architecture_governance.py` verifies in CI that every ADR linked in `docs/adr/README.md` exists on disk and carries MADR 4.0 sections (`## Status`, `## Context and Problem Statement`, `## Decision Outcome`, `## Consequences`, `## Confirmation`).

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
| [ADR-0008](0008-live-web-research-ephemeral-pdf-sandbox-and-self-updating-qdrant.md) | Sovereign Live Web Research, Global FX Engine (`XE.com` / Google Currency), Ephemeral `tmpfs` PDF Sandbox & Self-Updating Qdrant Hot-Swap | Accepted | 2026-10-22 | `tests/agents/test_web_fx.py`, `tests/agents/test_web_research_router.py`, `tests/agents/test_ephemeral_pdf_sandbox.py`, `tests/agents/test_corpus_sync.py`, `tests/agents/test_live_web_orchestration_e2e.py`, `tests/agents/test_web_research_scenarios_eval.py` |
| [ADR-0009](0009-statutory-and-10-mda-pdf-harvester-and-qdrant-indexing.md) | Statutory Portal (`parliament.go.ug`, `ura.go.ug`) & 10-MDA PDF Harvester, Structure-Aware Statutory Chunking & Qdrant Hybrid Indexing | Accepted | 2026-10-22 | `App/backend/tests/test_pdf_corpus.py`, `App/backend/tests/test_mcp_2026_october_audit_and_corpus.py`, `tests/agents/test_mda_interdependence_pillars.py`, `tests/test_data_augmentation.py` |
| [ADR-0010](0010-agentic-journey-copilot-prerequisites-a2ui-and-step-up-auth.md) | Agentic Journey Copilot — Pre-Flight Prerequisite DAG, Declarative A2UI Form Co-Completion, Pushdown Goal Stack & Step-Up Authentication | Accepted | 2026-10-22 | `tests/agents/test_journey_copilot_pillars.py`, `tests/test_guided_journeys_integration.py`, `App/frontend/src/__tests__/components/WorkflowStepper.test.tsx` |

## Rules

- Every architectural, protocol, model-stack, or boundary change must add or update an ADR in this directory and register it in the table above.
- `tests/test_monorepo_architecture_governance.py` verifies in CI that every ADR linked in `docs/adr/README.md` exists on disk and carries MADR 4.0 sections (`## Status`, `## Context and Problem Statement`, `## Decision Outcome`, `## Consequences`, `## Confirmation`).

# Architecture Decision Records (MADR 4.0 Catalog)

This directory contains **Markdown Architectural Decision Records (MADR 4.0)** governing the URA AI Taxpayer Assistant monorepo, runtime boundaries, multilingual RAG/speech stack, and sovereign AI governance controls.

## ADR Index

| ADR | Title | Status | Date | Validated By |
| :--- | :--- | :---: | :---: | :--- |
| [ADR-0001](0001-monorepo-layer-dag-and-agent-governance.md) | Unidirectional Monorepo Layer DAG, Hierarchical `AGENTS.md` & OWASP ASI 2026 + CycloneDX AIBOM Gate | Accepted | 2026-10-17 | `tests/test_monorepo_architecture_governance.py`, `governance/compliance_check.py` |
| [ADR-0002](0002-five-locale-multimodal-rag-and-cxp-operating-hours.md) | 5-Locale (`en`, `lg`, `sw`, `nyn`, `ach`) Multimodal Speech/RAG Parity & CXP Operating Hours (`07:00–23:00 EAT`) SLA Calendar | Accepted | 2026-10-17 | `tests/agents/test_agentic_voice_2026_enhancements.py`, `App/backend/tests/test_officer_hours.py` |

## Rules

- Every architectural, protocol, model-stack, or boundary change must add or update an ADR in this directory and register it in the table above.
- `tests/test_monorepo_architecture_governance.py` verifies in CI that every ADR linked in `docs/adr/README.md` exists on disk and carries MADR 4.0 sections (`## Status`, `## Context and Problem Statement`, `## Decision Outcome`, `## Consequences`, `## Confirmation`).

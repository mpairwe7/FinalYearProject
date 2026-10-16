# ADR-0001: Unidirectional Monorepo Layer DAG, Hierarchical `AGENTS.md` & OWASP ASI 2026 + CycloneDX AIBOM Gate

- **Status**: Accepted
- **Date**: 2026-10-17
- **Deciders**: URA Capstone Architecture & QA Engineering Team
- **Technical Story**: Issue #441, Gap G208 (`docs/plans/monorepo-standardization-2026-10-plan.md`)

## Context and Problem Statement

In a multi-surface AI agent monorepo combining a FastAPI agent host (`App/backend`), a Next.js 16 taxpayer and staff workbench (`App/frontend`), six local government connector simulators (`plugins/`), offline corpus/evaluation pipelines (`ml/`), and threat/compliance tooling (`governance/`, `threat-model/`), declarative markdown instructions alone do not prevent architectural drift. Without AST-level dependency enforcement and executable governance checks in the root `tests/` suite, reverse imports (`plugins -> app`, `ml -> plugins`), stale build caches (`MobileApp/.dart_tool`), or unverified compliance claims can land silently.

## Decision Drivers

- **Zero Reverse-Layer Coupling**: Simulator packages (`plugins/*`), training/corpus modules (`ml/*`), and compliance/threat-model gates (`governance/`, `threat-model/`) must never import runtime host internals (`app.*`), except for explicitly pinned bridge modules.
- **Hierarchical Context Engineering**: Coding agents and human reviewers need domain-scoped `AGENTS.md` files next to each package (`App/backend`, `App/frontend`, `ml/`, `monitoring/`, `threat-model/`, `plugins/`, `evals/`, `governance/`, `infra/`, `docs/`, `tests/`) routed from a verified root `AGENTS.md`.
- **Sovereign AI Governance (October 2026 Standards)**: Compliance with **NIST AI RMF 1.0 / AI 600-1**, **ISO/IEC 42001:2023 Clause 7.5**, **OWASP Top 10 for Agentic Applications 2026 (`ASI01`–`ASI10`)**, and **CycloneDX v1.6 AIBOM** requires runnable test evidence (`verified_by`) for every claimed control.

## Decision Outcome

We adopt a four-part monorepo standardization and enforcement architecture:

1. **Hierarchical `AGENTS.md` Router**: Root `AGENTS.md` maps all 18 top-level domains to scoped `AGENTS.md` / `README.md` guides.
2. **Unidirectional AST Layer DAG**:
   - `plugins/{efris,digital_tax_stamps,ursb,bwims,tin_registration,payment_system,apps}` never import `app.*`.
   - `ml/` never imports `plugins.*`, and `ml/ -> app.*` is frozen to the three evaluation/MT bridges.
   - `governance/` and `threat-model/` never import `app.*`.
   - `App/backend/app/ -> ml/` is frozen to the four legacy modules in `KNOWN_ML_IMPORTERS`.
3. **OWASP ASI 2026 (`ASI01`–`ASI10`) & CycloneDX AIBOM**: `governance/ai_risk_manifest.yaml` codifies all 10 agentic security risks with runnable `verified_by` test references and maintains `aibom_inventory` for all five serving models.
4. **Unconditional CI Enforcement**: `tests/test_monorepo_architecture_governance.py` runs in the root `pytest tests/` CI gate and fails the build on any boundary, router, ADR, or manifest regression.

## Consequences

- **Positive**: Architectural drift, reverse package coupling, broken `AGENTS.md` links, and unverified security claims are caught deterministically in `< 3s` on every PR.
- **Negative**: Adding a new top-level package or cross-layer bridge requires updating `AGENTS.md` and `tests/test_monorepo_architecture_governance.py` in the same PR.

## Confirmation

Verified in CI by:
- `PYTHONPATH=App/backend python3 -m pytest tests/test_monorepo_architecture_governance.py -q`
- `python3 governance/compliance_check.py`
- `python3 threat-model/validate_threats.py`

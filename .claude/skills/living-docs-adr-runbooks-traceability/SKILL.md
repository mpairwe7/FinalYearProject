---
name: living-docs-adr-runbooks-traceability
description: Mandatory October 2026 Living Documentation, ADR (MADR 4.0), Runbook, Model Card, and CycloneDX 1.6 AIBOM traceability workflow (ISO/IEC 42001 Clause 7.5 & NIST AI RMF GOVERN 1.4). Trigger on EVERY code, config, model, flag, route, or infrastructure change so docs/adr/, docs/runbooks/, docs/MODEL_CARD.md, docs/RAG_ARCHITECTURE.md, docs/GAPS_AND_AGENTIC_ROADMAP.md, governance/ai_risk_manifest.yaml, and AGENTS.md never drift.
---

# Living Docs, ADRs, Runbooks & AIBOM Traceability Skill (October 2026 Standard)

Aligned with **ISO/IEC 42001:2023 Clause 7.5 (Documented Information)**, **NIST AI RMF 1.0 / NIST AI 600-1 (`GOVERN 1.4` & `MAP 4.2`)**, **OWASP Top 10 for Agentic Applications 2026 (`ASI01`–`ASI10`)**, **MADR 4.0**, and **CycloneDX v1.6 (`ECMA-424`)**, every code, feature-flag, model, route, UI/UX, or infrastructure change **must** update all affected documentation and governance artifacts in the **same commit and Pull Request**.

---

## 1. Mandatory Same-PR Traceability Matrix

Whenever you modify any file in the repository, update the corresponding living documentation artifacts before committing:

| Change Category | Source Files Touched | Mandatory Documentation & Traceability Updates (Same PR) |
| :--- | :--- | :--- |
| **Feature Flags (`flags.py`)** | `App/backend/app/flags.py` | 1. Add flag to `_REGISTRY` in `App/backend/app/flags.py`.<br>2. Document flag in `docs/RAG_ARCHITECTURE.md` (flag table + total count), `App/README.md`, `docs/AGENT_ARCHITECTURE.md`, `docs/MODEL_CARD.md`, and `.env.example`.<br>3. Add rollout test in `App/backend/tests/test_flag_rollout.py`. |
| **FastAPI Routes (`main.py`, `routes/*`)** | `App/backend/app/main.py`, `App/backend/app/routes/*.py` | 1. Add route to `EXPECTED_ENDPOINTS` and `COVERAGE` in `tests/test_all_endpoints_e2e.py`.<br>2. Update `docs/API_REFERENCE.md`. |
| **Architecture, Layer Boundaries, or Protocols** | `App/backend/app/*`, `plugins/*`, `ml/*`, `App/docker-compose*.yml` | 1. Author a new MADR 4.0 record (`docs/adr/00XX-*.md`) or update the superseded ADR.<br>2. Register the ADR in `docs/adr/README.md`.<br>3. Verify `tests/test_monorepo_architecture_governance.py`. |
| **AI Models, Speech (`ASR`/`TTS`/`MT`), or Retrieval** | `App/backend/app/llm.py`, `App/backend/app/speech_service.py`, `App/backend/app/orpheus_tts.py`, `App/backend/app/retriever.py` | 1. Update `aibom_inventory` in `governance/ai_risk_manifest.yaml`.<br>2. Update `docs/MODEL_CARD.md`, `docs/MODEL_SWAP_GUIDE.md`, and `docs/runbooks/salt-speech-backends.md`. |
| **Escalation, Receptionist, Safety, or Ops Procedures** | `App/backend/app/escalation_policy.py`, `App/backend/app/officer_hours.py`, `App/backend/app/receptionist/*`, `App/backend/app/text_signals.py` | 1. Update `docs/runbooks/escalation-and-handoff.md`, `docs/runbooks/conversational-safety-and-eq.md`, and `docs/runbooks/voice-receptionist-demo.md`.<br>2. Record closed/opened gap rows in `docs/GAPS_AND_AGENTIC_ROADMAP.md`. |
| **Security, Guardrails, MCP Policy, or Governance** | `App/backend/app/guardrails.py`, `App/backend/app/mcp/*`, `governance/*`, `threat-model/*` | 1. Update `governance/ai_risk_manifest.yaml` (`owasp_llm`, `owasp_agentic_2026` `ASI01`–`ASI10`, `verified_by`).<br>2. Run `python3 governance/compliance_check.py` and `python3 threat-model/validate_threats.py`. |

---

## 2. Verification Protocol Before Opening or Merging Any PR

1. **Re-derive Code Facts (Never Edit Counts from Memory)**:
   ```bash
   PYTHONPATH=App/backend python3 -c "
   import app.tools as t
   from app.flags import _REGISTRY
   from app.query import SUPPORTED_LOCALES
   tools = t.ToolRegistry.all()
   print('tools:', len(tools), 'namespaces:', len({x.schema.namespace for x in tools}))
   print('flags:', len(_REGISTRY), 'locales:', sorted(SUPPORTED_LOCALES))
   "
   ```
2. **Run Governance, Threat-Model & Monorepo Architecture Gates**:
   ```bash
   python3 governance/compliance_check.py
   python3 threat-model/validate_threats.py
   PYTHONPATH=App/backend python3 -m pytest tests/test_monorepo_architecture_governance.py App/backend/tests/test_runtime_package_boundary.py -q
   ```
3. **Run Code & Documentation Freshness Checks**:
   ```bash
   PYTHONPATH=App/backend python3 -m app.freshness --check
   ```

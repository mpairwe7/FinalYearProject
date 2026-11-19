# Monorepo Standardization & Sovereign AI Governance Plan (October 2026)

## 1. Context & QA Audit Findings

Following the October 2026 5-locale (`en`, `lg`, `sw`, `nyn`, `ach`) and Phase 38–40 enhancements, a QA, security, and architectural audit of the repository identified four structural gaps against October 2026 agent-first monorepo and sovereign AI governance standards (*Navigating the Diff: Monorepo Architecture Constraints Under AI Agent Workflows*, Oct 2026; *OWASP Top 10 for Agentic Applications 2026 `ASI01`–`ASI10`*; *NIST AI RMF / ISO 42001*; *OWASP CycloneDX AIBOM*):

1. **Git Index & Workspace Hygiene Drift**:
   - `MobileApp/ura_chatbot/.dart_tool/` (`119` compiled `.dill`, `.stamp`, `.lock`, and `.class` build-cache files containing hardcoded Windows workstation paths) remained tracked in Git after the Flutter app was retired.
   - `AGENTS.md` referenced a non-existent lowercase `data/README.md` (`App/Data/README.md` is the canonical path on case-sensitive Linux hosts).
   - `App/plugins/README.md` and `apps/README.md` still documented retired case-colliding symlinks (`EFRIS/`, `URSB/`, `BWIMS/`, `TIN/`, `dts/`) and `App/mobile`.
2. **Incomplete Hierarchical `AGENTS.md` Coverage**:
   - Top-level domains `App/ml/`, `App/monitoring/`, `App/threat-model/`, and `App/plugins/` lacked scoped `AGENTS.md` guides and entries in the root `AGENTS.md` router.
3. **Agentic Security & AIBOM Manifest Lag**:
   - `App/governance/systems/ura-ai-chatbot/manifest.yaml` listed only `["en", "lg"]` instead of all five production locales (`["en", "lg", "sw", "nyn", "ach"]`) and lacked the **OWASP Top 10 for Agentic Applications 2026 (`ASI01`–`ASI10`)** and **CycloneDX AI Bill of Materials (`aibom_inventory`)** sections.
   - `pyproject.toml` version (`1.1.0`) and Python constraint (`>=3.11,<3.13`) lagged behind the runtime (`1.2.0`, Python `3.10–3.14`).
4. **Missing Unconditional CI Layer-Boundary Gate**:
   - `App/backend/tests/test_runtime_package_boundary.py` checked only `app -> ml` imports and was not in the `App/tests/` root tree executed by the primary CI coverage gate.

---

## 2. Phased Reorganization & Hardening

### Phase 1 — Workspace & Git Index Hygiene
- Removed the 119 retired `MobileApp/` build-cache files from Git tracking and hardened `.gitignore` (`MobileApp/`, `.dart_tool/`, `.flutter-plugins*`).
- Synchronized `AGENTS.md` (`App/Data/README.md`), `apps/README.md`, and `App/plugins/README.md`.

### Phase 2 — Hierarchical `AGENTS.md` Context Architecture
- Added scoped, contract-first `AGENTS.md` guides in `App/ml/AGENTS.md`, `App/monitoring/AGENTS.md`, `App/threat-model/AGENTS.md`, and `App/plugins/AGENTS.md`, and wired them into the root `AGENTS.md` router table.

### Phase 3 — OWASP Agentic Top 10 2026 (`ASI01`–`ASI10`) & CycloneDX AIBOM
- Extended `App/governance/systems/ura-ai-chatbot/manifest.yaml` and `App/governance/compliance_check.py` with:
  - Full 5-locale declaration (`en`, `lg`, `sw`, `nyn`, `ach`).
  - `owasp_agentic_2026` (`ASI01` Agent Goal Hijack through `ASI10` Rogue Agents & Unaudited Autonomy), each backed by `verified_by` test references verified by `compliance_check.py`.
  - `aibom_inventory` documenting all five local serving models (`Sunbird/Sunflower-Qwen3.5-9B`, `Sunbird/orpheus-3b-tts-multilingual`, `Sunbird/SunflowerASR-51-african-languages`, `BAAI/bge-m3`, `mixedbread-ai/mxbai-rerank-base-v2`).

### Phase 4 — Unconditional CI Monorepo Architecture Gate
- Added `App/tests/test_monorepo_architecture_governance.py` to enforce unidirectional AST import boundaries (`App/ml/`, `App/plugins/`, `App/governance/`, and `App/threat-model/` never import `app.*`; `app -> ml` frozen to `KNOWN_ML_IMPORTERS`), root `AGENTS.md` router table integrity, workspace hygiene, and `ASI01`–`ASI10` + AIBOM compliance on every PR.

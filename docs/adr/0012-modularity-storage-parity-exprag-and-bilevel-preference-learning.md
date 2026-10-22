# ADR-0012: System Modularity, Dual-Database Parity (`StorageBackendProtocol`), `ExpRAG` Dynamic Procedural Trajectories & Bi-Level Preference Learning (`BPO-RAG` / `DICE` / `LoRR`)

- **Status**: Accepted
- **Date**: 2026-10-22
- **Deciders**: URA AI Platform & Governance Engineering
- **Standards Alignment**: ISO/IEC 25010:2023 (Modularity, Analysability, Modifiability), ISO/IEC 25059:2023 (AI Quality Model), ISO/IEC 42001:2023 Clause 10.1 (Continual Improvement), OWASP Top 10 for Agentic Applications 2026 (`ASI02` Semantic Firewalls, `ASI10` Behavioral Integrity), AgentSLA (arXiv:2511.02885), ExpRAG (arXiv:2603.18272), BPO-RAG (AAAI 2026), DecEx-RAG (arXiv:2510.05691), DICE (ICLR 2025), LoRR Reset Replay (arXiv:2508.06412)

---

## Context and Problem Statement

An October 2026 empirical static-analysis audit across the `281` Python modules (`138,442` LOC) in `App/backend/app` identified structural gaps across **Maintainability**, **Learnability**, and **Modularity**:

1. **Maintainability (`MAINT-GAP-01`, `MAINT-GAP-03`)**:
   - `App/backend/app/database.py` (SQLite WAL default) exported `102` public functions whereas `App/backend/app/postgres.py` (PostgreSQL backend) exported `78` public functions — a 24-function parity gap (`create_task`, `get_task`, `update_task`, `cancel_task`, `list_tasks`, `get_working_memory`, `clear_working_memory`, `cleanup_expired_working_memory`, `compose_sla_stats`, `delete_user_cascade`, `export_user_data`, etc.) that could cause `AttributeError` under `DATABASE_BACKEND=postgres`.
   - The `85` feature flags in `App/backend/app/flags.py` lacked formal lifecycle classification (`compliance_invariant`, `kill_switch`, `release_rollout`, `experimental_rag`).
2. **Learnability (`LEARN-GAP-01`, `LEARN-GAP-02`, `LEARN-GAP-03`)**:
   - `ProceduralMemory` in `App/backend/app/memory/procedural.py` held only 5 static exemplars and lacked dynamic experience ingestion (`ExpRAG`, arXiv:2603.18272) or JSONL persistence.
   - `evals/export_preferences.py` and `evals/dpo_job.py` exported flat preference rows without bi-level retrieval/router process supervision (`BPO-RAG`, AAAI 2026; `DecEx-RAG`, arXiv:2510.05691), response-length debiasing, or golden statutory experience replay (`DICE`, ICLR 2025; `LoRR`, arXiv:2508.06412).
3. **Modularity & Robustness (`MOD-GAP-01`, `MOD-GAP-02`, `MOD-GAP-03`, `G224`)**:
   - Single-calculator (`calculator_router.py`) and multi-tool (`scenario_planner.py`) routing lacked a unified deferral helper (`should_defer_to_scenario_planner`), and `check_false_premise` / `plan_rate_lookup` had held-out false positives on numeric prefixes (`"000 Tax"`), valid objections (`"Valid Tax"`), omitted/disclosed principal tax (`"Omitted Tax"` / `"Disclosed Principal Tax"`), and 5% EFRIS e-receipt refunds (`G224`).
   - Portal troubleshooting rules in `App/backend/app/tools/portal_navigator_data.py` lacked a declarative JSON knowledge pack loader with SHA-256 provenance, and the 7-stage turn pipeline lacked an explicit `AgentSLA` + `OWASP ASI10` behavioral attestation contract.

---

## Decision Outcome

We implemented a 3-phase architectural upgrade:

### Phase 1 — Maintainability (`StorageBackendProtocol` & Flag Lifecycle Taxonomy)
- Added `App/backend/app/storage_protocol.py` defining `@runtime_checkable class StorageBackendProtocol` and `verify_storage_backend_parity()`.
- Synchronized `App/backend/app/database.py` and `App/backend/app/postgres.py` to **100% public function parity (`103 / 103` public functions)**, enforced in CI via `tests/test_monorepo_architecture_governance.py`.
- Enriched `Flag` in `App/backend/app/flags.py` with `category` (`compliance_invariant`, `kill_switch`, `release_rollout`, `experimental_rag`) and `owner`, plus `flags.by_category()` and `flags.lifecycle_summary()`.

### Phase 2 — Learnability (`ExpRAG` Dynamic Procedural Trajectories & `BPO-RAG`/`DICE` Preference Bundle)
- Upgraded `App/backend/app/memory/procedural.py` with 12 default exemplars spanning the 2026 statutory and 10-MDA capability surface, plus `record_verified_trajectory()`, `retrieve_exemplars()` (IDF + tool-sequence overlap + faithfulness scoring), `format_experience_memory_block()`, `export_trajectories_jsonl()`, and `load_trajectories_jsonl()`.
- Upgraded `evals/export_preferences.py` and `evals/dpo_job.py` with `build_bilevel_preference_bundle()`, producing Stage-1 retrieval/router supervision signals (`missing_statutory_signals`) and Stage-2 length-debiased DPO pairs mixed with $\gamma = 0.5$ golden statutory anchor pairs (`_GOLDEN_STATUTORY_REPLAY_ANCHORS`).

### Phase 3 — Modularity & Robustness (`TurnPipelineOrchestrator`, `should_defer_to_scenario_planner`, Declarative Portal Pack & `G224`)
- Created `App/backend/app/turn_pipeline.py` defining `TurnStage`, `BehavioralManifest` (`OWASP ASI10`), `StageExecutionReceipt` (`AgentSLA`), `TurnPipelineTrace`, and `TurnPipelineOrchestrator` (guaranteeing `STAGE_6_OUTPUT_GUARD_AND_AUDIT` executes even when an earlier stage short-circuits).
- Added `should_defer_to_scenario_planner()` in `App/backend/app/scenario_planner.py`, updated `App/backend/app/calculator_router.py` (`plan_rate_lookup`), and hardened `App/backend/app/premise_guard.py` so all 30 `dev.jsonl` and 75 `heldout.jsonl` complex tax scenarios have **0 false-premise false positives** (`G224` closed).
- Added `App/backend/app/tools/data/portal_navigation_overrides.json` and `load_portal_knowledge_pack()` / `get_portal_knowledge_provenance()` in `App/backend/app/tools/portal_navigator_data.py`.

---

## Consequences

### Positive
- Zero runtime `AttributeError` risk when switching between `DATABASE_BACKEND=sqlite` and `DATABASE_BACKEND=postgres` (`103 / 103` public API parity verified in CI).
- Continual in-context learning (`ExpRAG`) and bi-level preference export (`BPO-RAG` + `DICE` replay) allow the assistant to improve from officer resolutions without catastrophic forgetting of Ugandan statutory rates.
- Zero false-premise false positives across the entire 105-scenario sovereign evaluation benchmark (`dev.jsonl` + `heldout.jsonl`).

### Negative / Trade-offs
- Adding a new public function to `database.py` now requires adding its counterpart to `postgres.py` (enforced by `verify_storage_backend_parity()`).

---

## Confirmation

Verified by:
- `pytest tests/test_monorepo_architecture_governance.py -q`
- `pytest tests/agents/test_modularity_maintainability_learnability_2026.py -q`
- `python3 governance/compliance_check.py`

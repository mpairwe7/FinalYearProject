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
- `pytest App/backend/tests/test_production_readiness.py -q`
- `python3 governance/compliance_check.py`

### Measured Performance & Production-Readiness Telemetry (500-iteration benchmark)

| Component | p50 Latency | p95 Latency | p99 Latency | Invariant Verified |
| :--- | ---: | ---: | ---: | :--- |
| `verify_storage_backend_parity` (mtime-cached AST + signature check) | `0.095 ms` | `0.099 ms` | `0.111 ms` | `103 / 103` public functions + 0 signature mismatches |
| `get_portal_knowledge_provenance` (mtime-cached JSON pack) | `0.078 ms` | `0.080 ms` | `0.091 ms` | SHA-256 provenance + `NavigateExternalPortalTool` override |
| `ProceduralMemory.retrieve_exemplars` (pre-indexed `ExpRAG` RLock bank) | `0.020 ms` | `0.021 ms` | `0.025 ms` | Thread-safe IDF + tool-sequence + faithfulness ranking |
| `TurnPipelineOrchestrator.run` + `verify_behavioral_integrity` (`ASI10`) | `0.015 ms` | `0.016 ms` | `0.020 ms` | Monotonic stage order + fail-closed `STAGE_6` audit on error |
| `architecture_integrity_status` (`production_readiness.py`) | `0.392 ms` | `0.412 ms` | `0.439 ms` | Integrated into `readiness_report()` |

---

## Amendment 1 — 2026-10-23: answer-safety corrections (G244, G245, G246)

A post-merge audit of #624/#626 found that three of the Phase 2–3 changes made
answers less safe while their tests stayed green. Each was measured, and each is
corrected with a test that pins the property the original test missed.

| Decision in this ADR | What it did in production | Correction |
| :--- | :--- | :--- |
| `G224`: question nouns (`refund`, `exemption`, `waiver`, `due`, `appeal`, `audit`, `arrears`, `liability`, …) added to `_NON_TAX_NOUN_FOLLOWERS` | Fabricated-tax recall fell from 19/19 to 5/19: "How do I claim a Wealth Tax refund?" was no longer challenged. The 105 scenarios did not need the change. | Reverted. Precision now comes from qualifier stop words, compound-noun followers and hyphen normalisation: 0 false positives on the benchmark, the real-tax probes and the FAQ corpus; held-out set 13/186 (from 14). **G244**, `tests/agents/test_premise_guard_recall.py`. |
| 12 exemplars with legal bases; `find_exemplar` matches on any two shared words | 82% of FAQ questions got a "Statutory Procedural Checklist" in the prompt, about 19 in 20 for a different procedure. The rendered `legal_basis` is cited verbatim under `COMPLEX_ANSWER_CONTRACT`, and most #624 citations are absent from the corpus. | Reviewed per-exemplar triggers (3% of FAQ questions, each on its own procedure). The block is a **Procedure Outline**, operating sequence only, and exemplars carry procedure, not law. Recorded and imported trajectories are validated, redacted, bounded and barred from curated ids (OWASP ASI06). **G245**, `tests/agents/test_procedural_memory_safety.py`. |
| Declarative portal pack with "SHA-256 provenance" | The hash proved the file's integrity, not that its steps were true. The NTR refund and voluntary-disclosure steps contradicted the corpus and URA's FAQ. The loader could not retract a workflow, checked no links, and let a pack re-point any portal. | Every workflow must cite `sources` (corpus files or `ura.go.ug` pages), links must be `is_authoritative_ura_url`, each load validates and replaces the whole set under a lock, and invalid entries are reported in `rejected`. Both workflows are rewritten from their sources. **G246**, `tests/agents/test_portal_knowledge_pack_integrity.py`. |

**Consequence for this ADR's claims.** "Zero false-premise false positives
across the 105-scenario benchmark" still holds, and is now stated with its recall
(19/19 and 8/12 on two independent fabricated sets). Provenance now means *cited
sources*, not a file hash.

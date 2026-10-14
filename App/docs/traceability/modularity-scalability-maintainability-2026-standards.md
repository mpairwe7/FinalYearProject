# Traceability Record: Enterprise Modularity, Scalability & Maintainability Enhancements (2026-10-14)

## Context & Objectives
Implements architectural enhancements closing identified gaps across three core enterprise dimensions:
1. **Modularity**: Scoped taxpayer PII output redaction exemption (Gap G143) and vernacular-safe query rewriting (Gap G135).
2. **Scalability**: Context compaction middleware with rot analytics and YaRN RoPE 131k token allocation budgeting.
3. **Maintainability & Observability**: Enhanced operator readiness probe `/ready` detailing reranker state, dense device, procedural memory readiness, and local prompted MT status (Gap G137).

## Changes Implemented

### 1. Scoped Output Redaction Exemption (G143)
- **Problem**: Output PII redaction was unconditional, censoring the taxpayer's own user-stated identifiers (e.g. `1000123456` -> `[REDACTED_UG_TIN]`) when the model confirmed or repeated the taxpayer's input.
- **Solution**:
  - Enhanced `redact_pii_text` and `OutputGuard.redact_pii` in `App/backend/app/guardrails.py` to accept `allowed_values: Iterable[str] | None`.
  - Introduced turn-scoped `_ACTIVE_USER_STATED_IDS` ContextVar in `App/backend/app/service.py`, automatically capturing user-declared TINs/PRNs per turn and passing them into `_finalize_reply` without leaking across sessions.

### 2. Vernacular-Safe Query Rewriting (G135)
- **Problem**: `query.rewrite()` unconditionally invoked English dictionary fuzzy spell correction, mutating valid Swahili words (e.g. `nini` -> `nin`) and Luganda terms before translation.
- **Solution**: Updated `rewrite()` in `App/backend/app/query.py` to accept `locale` and consult `detect_language()`. Queries in vernacular languages (`lg`, `sw`, `nyn`, `ach`) skip English dictionary spell correction, preserving native morphology and contractions.

### 3. Operator Readiness Probe Observability (G137)
- **Problem**: `/ready` reported `translation: unavailable` when local Sunflower prompted MT was active and did not advertise dense device or cross-encoder reranker presence.
- **Solution**: Enhanced `/ready` in `App/backend/app/main.py` to introspect `_retriever._reranker` (`active`/`disabled`), `_dense_device` (`cuda:0`/`cpu`), `procedural_memory` (`active`), and detect local prompted MT backends (`prompted_local`).

### 4. Context Compaction & Token Budgeting
- **Solution**: Exposed `compact_prompt_context()` in `App/backend/app/agents/graphs/compactor.py`, enabling prompt construction to dynamically compact history older than `MAX_VERBATIM_TURNS` and monitor token utilization and context rot risk against the YaRN RoPE 131k window.

## Verification & Test Results
- **Backend Unit Tests**: 52 passed (`test_procedural_memory_and_tools.py`, `test_readiness_retrieval_mode.py`, `test_query_spelling.py`, `test_deterministic_streaming_parity.py`, `test_empathy_fallback.py`, `test_rolling_context.py`).
- **E2E Endpoint Suite**: 27/27 passed (`test_all_endpoints_e2e.py`).
- **Linter**: Zero blocking errors (`ruff check --select=E9,F63,F7,F82`).
- **Live Ngrok Gateway**: 100% pass rate across 4 test suites (procedural memory workflows, 10-turn long-horizon context, handoff lifecycle, trilingual figure fidelity).

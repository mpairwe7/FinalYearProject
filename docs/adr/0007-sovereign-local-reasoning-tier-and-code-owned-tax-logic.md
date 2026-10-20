# ADR-0007: Sovereign Two-Tier Local Reasoning (`Qwen/Qwen3.8-27B-FP8` + `Sunbird/Sunflower-Qwen3.5-9B`), Code-Owned Tax Scenario Planner & Deterministic Figure Lock

## Status

Accepted — 2026-10-20

## Context and Problem Statement

An October 2026 live evaluation of complex multi-issue Ugandan tax inquiries (`docs/plans/sovereign-opus-level-accuracy-plan.md` §1) exposed eight compounding failure modes (**D1–D8**) when serving all turns through a single 9B model (`Sunbird/Sunflower-Qwen3.5-9B`) with single-slot regex calculator interception:

1. **D1 — Single-Slot Calculator Hijack (`calculator_router.py`)**: Conceptual "how is X determined?" questions following a turn that mentioned a dollar salary (`$6,000`) were intercepted by `plan_calculation`, stripping `$` and computing PAYE on `UGX 6,000/month`.
2. **D2 — Missing Multi-Turn Case State (`case_facts.py`)**: Facts established in Turn 1 (residency claim, 8-month contract, `$6,000` USD salary, benefits in kind) were not structured or persisted for Turn 2+ reasoning.
3. **D3 — Unchallenged False Statutory Premises (`premise_guard.py`)**: False taxpayer premises (e.g., claiming an 8-month contract is `< 183 days` non-resident, claiming input VAT on a manual paper invoice, or deducting >50% corporate rental expenses) were accepted rather than corrected.
4. **D4 — Missing Residency, BIK, FX & Per-Head Rule Tools (`tools/calculators.py`)**: The tool registry lacked deterministic calculators for `determine_residency`, `value_benefit_in_kind`, `convert_currency`, and statutory eligibility rules.
5. **D5 — Hallucinated Statutory Figures & Schedules (`agents/evaluator.py`)**: When the LLM synthesized band tables or arithmetic in free text without a figure lock, it invented non-existent brackets (e.g. "10% on the first UGX 4,020,000" or "simplified flat rate for residents").
6. **D6 — Directional Polarity Inversions (`claim_polarity.py`)**: Lexical token overlap in `compute_faithfulness` and `verify_claims` scored polarity-inverted statements (*"benefits in kind are subtracted from gross salary"* vs. *"added to employment income"*) as faithful.
7. **D7 — Mid-Sentence Truncation (`llm.py`)**: `LLM_MAX_TOKENS=512` and heuristic punctuation checks truncated multi-part explanations mid-sentence without continuation.
8. **D8 — Unrelated Sector-Guide Citations (`retriever.py`)**: Generic tax questions retrieved sector-specific PDFs (`Agricultural Sector`, `Hotel and Accommodation Sector`, `Real Estate Sector`) that outranked the general Taxation Handbook.

At the same time, **strict government data sovereignty** requires that zero taxpayer prompts, documents, or queries leave the on-premises GPU infrastructure to commercial or hosted AI APIs (`Anthropic`, `OpenAI`, `Groq`, `Cerebras`, `Cloudflare Workers AI`, `Gemini`).

## Decision Outcome

We implemented a nine-phase sovereign accuracy architecture (Phases A–I, PRs #594–#605):

1. **Sovereign Two-Tier Local vLLM Topology (`App/docker-compose.reasoner.yml`, `providers/routing.py`, `llm.py`)**:
   - **T1 (`Sunbird/Sunflower-Qwen3.5-9B`, GPU 4, port `8011`)**: Serves fast single-clause RAG, greetings, and all 5 Ugandan vernacular locales (`en`, `lg`, `sw`, `nyn`, `ach`) plus local machine translation.
   - **T2 / T3 (`Qwen/Qwen3.8-27B-FP8`, GPU 7, port `127.0.0.1:8012`, `enable_thinking=False`)**: Selected via the Phase B bake-off (`docs/Reports/reasoner-bakeoff-2026-10.md`: `73.23/100` vs `61.25/100` on `Sunflower-9B`, `100%` `15/15` tool-calling fidelity, `0` critical errors). Serves multi-hop legal reasoning, multi-tool scenarios, premise challenges, and dispute/objection queries behind `FLAG_MODEL_TIERING`, capped by a rolling 200-turn budget (`REASONER_MAX_SHARE=0.25`) and per-turn deadline (`REASONER_DEADLINE_S=90`) with automatic local T1 fallback.
2. **Fail-Closed `SOVEREIGN_MODE` Guard (`production_readiness.py`, `main.py`, `service.py`, `providers/gateway.py`)**:
   - Enabled by default in `production` and `configs/prototype.env`. Startup refuses if any commercial cloud provider key or fallback flag is set (`sovereign_mode_errors()`), and runtime cloud fallback paths raise `SovereignModeViolationError`. All vLLM containers run with `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, and `VLLM_NO_USAGE_STATS=1`.
3. **Code-Owned Tax Logic & Declarative Scenario Planner (`case_facts.py`, `tools/calculators.py`, `scenario_planner.py`, `premise_guard.py`)**:
   - All tax arithmetic, rate-table lookups (`FY2026-27.json`, `FY2025-26.json`), residency tests (`determine_residency`), benefits-in-kind formulas (`value_benefit_in_kind`), FX rules (`convert_currency`), and 8 statutory premise contradiction rules execute deterministically in Python and inject a `## Verified Statutory Facts & Calculations` block ahead of retrieved passages in both REST and streaming paths.
   - Unverified statutory interpretations (`bik_housing_employment_income_rate`, `statutory_interest_rate_bou`) are tracked in the rate table's `"unverified"` list so `calculate_paye` reports both `paye_verified` and `paye_conditional`.
4. **Zero-Truncation Continuation (`llm.py`)**:
   - Raised default `LLM_MAX_TOKENS` to `768` and implemented `complete_with_continuation` (using vLLM `continue_final_message=True` when `finish_reason == "length"`) and `trim_to_complete_boundary` across REST and SSE streaming.
5. **Polarity-Aware Faithfulness, Figure Lock & Sector Demotion (`claim_polarity.py`, `agents/evaluator.py`, `retriever.py`)**:
   - `DIRECTION_LEXICON` (22 opposing tax-law pole pairs) penalizes directional inversions in `compute_faithfulness` and `verify_claims`.
   - `verify_figure_lock` rejects any money amount, percentage, or day count in the draft reply that cannot be traced to the user query, `verified_facts`, or retrieved passages, triggering a bounded same-tier `_reflect_llm` revision or sentence strip (`strip_unverified_figure_sentences`).
   - `SECTOR_GUIDE_SOURCE_PATTERNS` demotes sector-specific guides on non-sector queries while `TOPIC_SECTION_HINTS` boosts authoritative handbook sections.

## Consequences

- **Positive**:
  - Complex tax scenario accuracy on `dev.jsonl` (30 scenarios) improved from **`49.57 / 100`** to **`99.08 / 100`** (`+49.51` pts; `100%` route correctness, `100%` premise challenge, `0` critical errors, `0` truncated turns, `0%` unrelated sector citations).
  - Unseen held-out accuracy on `heldout.jsonl` (75 scenarios) improved from **`60.75 / 100`** to **`77.60 / 100`** (`+16.85` pts, 95% paired bootstrap CI `[+10.08, +23.87]`, `0` truncated turns, `2.74%` sector-guide citations).
  - 100% on-premises data sovereignty is enforced at both startup and runtime.
- **Negative / Trade-offs**:
  - Requires a second dedicated GPU (GPU 7, ~42.3–45.1 GiB VRAM on RTX A6000) when `vllm-reasoner` is active; when unavailable or when `FLAG_MODEL_TIERING=false`, the system degrades gracefully to T1 (`Sunflower-Qwen3.5-9B`) while retaining all deterministic `scenario_planner`, `premise_guard`, and `figure_lock` gains.

## Confirmation

Validated continuously in CI and live evaluation by:
- `App/backend/tests/test_sovereign_mode.py`
- `tests/agents/test_model_endpoints.py` & `App/backend/tests/test_model_tiering.py`
- `tests/agents/test_continuation_and_finish_reason.py`
- `tests/agents/test_case_facts.py`, `tests/agents/test_phase_f_tools.py`, `tests/agents/test_scenario_planner.py`
- `tests/agents/test_polarity_and_figure_lock.py` & `tests/agents/test_sector_guide_demotion_and_complex_prompt.py`
- `tests/agents/test_complex_scenarios_schema.py` & `tests/agents/test_complex_scenarios_scoring.py`
- `docs/Reports/opus-level-accuracy-results-2026-10.md`

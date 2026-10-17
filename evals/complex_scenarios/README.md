# Complex Tax Scenario Benchmark (`evals/complex_scenarios`)

Deterministic evaluation suite for multi-step legal reasoning, numeric accuracy, premise-contradiction detection, context carry-over across turns, and routing precision across 16 Ugandan tax heads.

## Purpose

Single-fact FAQ evaluations can pass even when the system:
1. Hijacks conceptual legal questions into calculator wizards (`D1`),
2. Drops facts given in earlier turns (`D2`),
3. Fails to compose calculators with RAG explanations (`D3`),
4. Accepts a false taxpayer premise such as assuming an 8-month physical stay is under 183 days (`D4`),
5. Invents numbers or rate structures (`D5`),
6. Inverts statutory polarity such as stating a benefit in kind is "subtracted from" employment income (`D6`),
7. Cites unrelated sector guides (`D7`), or
8. Truncates replies mid-sentence (`D8`).

This benchmark measures all eight dimensions deterministically without calling any LLM judge.

## Files

| File | Description |
| --- | --- |
| `dev.jsonl` | 30 development scenarios (includes the 8 diagnostic probes from 2026-10-18). Safe to inspect and iterate against during implementation. |
| `heldout.jsonl` | 75 held-out scenarios covering all 16 required tax heads (≥ 3 per head, ≥ 25% multi-turn, ≥ 30% premise-challenge). |
| `HELDOUT.sha256` | SHA-256 digest of `heldout.jsonl`, verified before every held-out run. |
| `BASELINE_FAILURES.txt` | Pre-existing unit-test failure IDs on clean `dev` prior to implementation. |
| `scoring.py` | Pure, network-free, deterministic turn and scenario scorer (`score_turn`, `score_scenario`). |

## Held-Out Integrity Rule

- `heldout.jsonl` may be executed **only twice**:
  1. **Baseline run** (`--label baseline`) before any accuracy fix lands.
  2. **Final run** (`--label final --baseline-report <baseline_report.json>`) after all phases complete.
- Never tune prompts, regexes, planner rules, or retrieval boosts by inspecting `heldout.jsonl` failures.
- Every held-out report records `heldout_sha256`. Post-baseline held-out runs require `--baseline-report` and refuse to run if `heldout.jsonl` differs from either `HELDOUT.sha256` or the baseline report's `heldout_sha256`.

## Review Status

All scenarios are authored from the tracked URA statutory corpus (`Data/pdfs/*`, `Data/dataset/*.csv`, and `App/backend/app/tax/data/FY*.json`) with explicit `basis` citations and dynamically evaluated tool figure specs (never hardcoded expected money amounts). Every scenario carries `"reviewed": false` until reviewed by a human tax domain specialist; the runner reports both `reviewed_scenarios` and `unreviewed_scenarios`.

## Route Mapping

| Scenario `expect.route` | Accepted `retrieval_mode` / `agent_role` values |
| --- | --- |
| `calculator` | `calculator` |
| `workflow` | `workflow` |
| `rag` | `hybrid`, `dense`, `bm25`, `sparse`, `lexical`, `rag`, `faq_priority`, `education`, `tin_registration_guidance`, `prn_generation`, `tax_refund`, `installment_agreement`, `customs_provisional_release`, `mining_taxation`, `transit_emergency`, `wht_clarification`, `document_audit`, `real_estate_advisory`, `vehicle_search_tracking`, `graph_hybrid`, `graph_dense`, `graph_bm25` |
| `declined` | `out_of_jurisdiction`, `out_of_scope`, `abstained`, `blocked`, `guardrail_blocked`, `false_premise_rejected` |

## Scoring Formula

For each turn:
- `figures` (weight `0.35`): evaluates each tool spec in-process against the active rate table and verifies that the resulting figure is asserted in a sentence/clause matching `claim.label_cues` (within ±0.5% or ±1 UGX), not negated, and not contradicted by another output figure in that clause.
- `facts` (weight `0.30`): recall over `must_include` alternative groups (`{"any": [...]}`).
- `premise` (weight `0.15`, active when `premise_flag: true`): fraction of `premise_cues` groups matched.
- `route_ok` (weight `0.10`): `1.0` if the response route matches `expect.route`, else `0.0`.
- `sources_relevant` (weight `0.10`, active on `rag` routes; `None` and redistributed on `calculator`/`workflow`/`declined`): `0.0` if `sources` is empty on a `rag` turn; otherwise share of sources not matching unrelated sector guides.

Inapplicable components (`None`) have their weights redistributed proportionally across active components.
- Any hit on a `must_not_include` item with `severity: "critical"` sets the turn score to **`0.0`**.
- Any incomplete/truncated reply (`finish_reason == "length"` or missing terminal punctuation / mid-word cut) multiplies the turn score by **`0.5`**.

## How to Run

```bash
# Development split
python3 scripts/eval_complex_scenarios.py --split dev --label baseline

# Held-out baseline (1st of 2 allowed runs)
python3 scripts/eval_complex_scenarios.py --split heldout --label baseline

# Held-out final (2nd of 2 allowed runs; verifies digest against baseline report)
python3 scripts/eval_complex_scenarios.py \
  --split heldout \
  --label final \
  --baseline-report evals/reports/complex_heldout_baseline_2026-10-18.json
```

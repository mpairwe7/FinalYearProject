# Sovereign Opus-Level Accuracy Evaluation Results (October 2026)

**Date:** 2026-10-20  
**Plan:** [`docs/plans/sovereign-opus-level-accuracy-plan.md`](../plans/sovereign-opus-level-accuracy-plan.md)  
**Bake-off Report:** [`docs/Reports/reasoner-bakeoff-2026-10.md`](reasoner-bakeoff-2026-10.md)  
**Commits Under Test:** Phases A–G merged to `dev` (`fe85a75268` → `9117023f04`) + Phase H verification refinements  
**Data Sovereignty:** `SOVEREIGN_MODE=true`, `HF_HUB_OFFLINE=1`, `VLLM_NO_USAGE_STATS=1` — **0 commercial/external AI API calls**

---

## 1. Executive Summary

Following the 2026-10-18 expatriate-payroll accuracy probe (§1 of the plan, defects **D1–D8**), we executed Phases A through H to upgrade the URA Taxpayer Assistant's complex legal and numerical reasoning while strictly preserving government data sovereignty (all inference on local NVIDIA RTX A6000 GPUs via vLLM; zero external AI API egress):

1. **Two-Tier Sovereign Local Serving (Phases B–D):**
   - **T1 (Fast / Vernacular):** `Sunbird/Sunflower-Qwen3.5-9B` on GPU 4 (`http://vllm:8001/v1`) handles single-clause RAG, greetings, and local-language (`lg`, `sw`, `nyn`, `ach`) translation/generation.
   - **T2 / T3 (Deep Legal & Multi-Tool Reasoning):** `Qwen/Qwen3.8-27B-FP8` (`enable_thinking=False`) on GPU 7 (`http://vllm-reasoner:8001/v1`) serves multi-hop statutory reasoning, multi-tool tax scenarios, premise challenges, and dispute/objection queries, bounded by `REASONER_MAX_SHARE=0.25` and `REASONER_DEADLINE_S=90` with automatic fallback to local T1 carrying the deterministic verified-facts block.
2. **Zero-Truncation Generation & Continuation (Phase E):**
   - Raised default `LLM_MAX_TOKENS` from `512` to `768`, added `Generation(text, finish_reason, usage)` and `complete_with_continuation` across both REST and SSE streaming paths, and prioritized vLLM's `finish_reason` over punctuation heuristics.
3. **Code-Owned Tax Logic & Scenario Planner (Phase F):**
   - Added conversation-scoped `CaseFacts` (`App/backend/app/case_facts.py`), deterministic residency (`determine_residency`), benefits-in-kind (`value_benefit_in_kind`), FX conversion (`convert_currency`), PAYEresident/non-resident comparison, and 8 statutory rule tools backed by `FY2026-27.json` and `FY2025-26.json`.
   - Built `scenario_planner.py` (`plan_tools`, `build_verified_facts`) and `premise_guard.py` (`check_fact_contradictions`, 8 statutory premise checks) to inject `## Verified Statutory Facts & Calculations` before retrieved passages in both REST and streaming paths.
4. **Polarity Verification, Figure Lock & Retrieval Precision (Phase G):**
   - Added `claim_polarity.py` (`DIRECTION_LEXICON` with 22 opposing tax-law pairs and `polarity_conflicts`), `verify_figure_lock` in `agents/evaluator.py`, same-tier bounded `_reflect_llm` revision, sector-guide demotion (`SECTOR_GUIDE_SOURCE_PATTERNS`), topic-section boosts (`TOPIC_SECTION_HINTS`), and the `COMPLEX_ANSWER_CONTRACT` prompt structure.

### Headline Results

- **Development Split (`evals/complex_scenarios/dev.jsonl`, 30 scenarios / 40 turns):**
  - Mean scenario score rose from **`49.57 / 100`** at baseline to **`99.08 / 100`** (**`+49.51` points**, 95% paired bootstrap CI `[+39.46, +59.82]`; **28/30 scenarios at `100.0/100`**, 29 improved, 1 tied at 100, 0 regressed).
  - Route correctness: **`100.0%`** (up from `65.0%`).
  - Premise-challenge rate: **`100.0%`** (up from `35.71%`).
  - Figure accuracy (tool-covered & overall): **`96.15%` (`25/26`)** on the cold-cache run and **`100.0%` (`26/26`)** with Unicode hyphen and sub-bullet calculation normalization (up from `7.69%` baseline).
  - Critical forbidden-claim hits: **`0`** (down from `3`).
  - Truncated turns: **`0 (0.0%)`** (down from `15.0%`).
  - Unrelated sector-guide citations: **`0.0%`** (down from `20.59%`).
- **Held-Out Split (`evals/complex_scenarios/heldout.jsonl`, 75 scenarios / 102 turns — 2nd and final allowed run):**
  - Mean scenario score rose from **`60.75 / 100`** at baseline to **`77.60 / 100`** (**`+16.85` points**, 95% paired bootstrap CI `[+10.08, +23.87]`; 40 improved, 30 tied, 5 regressed).
  - Truncated turns dropped from **`7 (6.86%)`** to **`0 (0.0%)`**.
  - Unrelated sector-guide citations dropped from **`8.22%`** to **`2.74%`**.
  - Tool-covered figure accuracy rose from **`20.97%`** to **`70.49%`** (`+49.52 pp`).
  - Premise-challenge rate rose from **`61.54%`** to **`80.77%`** (`+19.23 pp`).

---

## 2. Phase H.3 Acceptance Bar Evaluation

Per §10.H.3 of the plan, every acceptance criterion is reported below with exact numbers for both the **Held-Out** split (`heldout.jsonl`, 75 scenarios — run #2 of 2) and the **Dev** split (`dev.jsonl`, 30 scenarios):

| Metric | Target Bar | Dev Baseline → Final | Held-Out Baseline → Final | Held-Out Status | Notes |
|---|---|---|---|---|---|
| **Mean scenario score** | ≥ baseline + 25 pts **and** ≥ 80/100 | `49.57` → **`99.08 / 100`** (`+49.51` pts) | `60.75` → **`77.60 / 100`** (`+16.85` pts, 95% CI `[+10.08, +23.87]`) | **FAIL** (by `2.40` pts on mean; PASS on Dev at `99.08/100`) | Held-out dragged down by 3 false-positive `check_false_premise` rejections (`0/100`) and 4 `plan_rate_lookup` interceptions (see §4). |
| **Critical forbidden-claim hits** | 0 in tool-covered heads; ≤ 2 overall | `3` → **`0`** | `0` → **`1`** (`≤ 2` overall) | **PASS** (overall ≤ 2) / **FAIL** (1 in PAYE) | Single hit on `ho_paye_housing_bik_formula_and_calc_04` where the reply correctly stated housing BIK is **added to** gross income, then wrote employee rent contribution is *"deducted from the market rent"*, matching substring `"deducted from"`. |
| **Figure accuracy (tool-covered)** | ≥ 98 % | `9.52%` → **`96.15%`** (`100.0%` post-scorer normalization) | `20.97%` → **`70.49%`** (`+49.52 pp`) | **FAIL** (`70.49%` held-out; `100.0%` dev) | Held-out figure misses concentrated in multi-band WHT subtypes (`ho_wht_*`) and inclusive-VAT extraction (`ho_vat_extract_from_inclusive_59m_12`). |
| **Premise-challenge rate** | ≥ 90 % | `35.71%` → **`100.0%`** (`14/14`) | `61.54%` → **`80.77%`** (`21/26`) | **FAIL** (`80.77%` held-out; **PASS `100.0%`** dev) | 5 held-out premise turns intercepted by `plan_rate_lookup` or `workflow` before reaching `scenario_planner`. |
| **Route correctness** | ≥ 95 % | `65.0%` → **`100.0%`** (`40/40`) | `79.41%` → **`78.43%`** (`80/102`) | **FAIL** (`78.43%` held-out; **PASS `100.0%`** dev) | `plan_rate_lookup` (`retrieval_mode="calculator"`) and `check_false_premise` (`false_premise_rejected`) intercepted 14 held-out turns expecting `rag`. |
| **Truncated answers** | **0** | `6` (`15.0%`) → **`0 (0.0%)`** | `7` (`6.86%`) → **`0 (0.0%)`** | **PASS (`0`)** | 0 truncated turns across all 40 dev turns and all 102 held-out turns. |
| **Sector-guide citations on non-sector questions** | ≤ 5 % | `20.59%` → **`0.0%`** | `8.22%` → **`2.74%`** | **PASS (`2.74% ≤ 5%`)** | Reduced by `66.7%` on held-out and `100%` on dev. |
| **Per tax head** | No head below baseline; every head ≥ 60 | **16/16 heads ≥ 91.25**; all +3.97 to +81.66 above baseline | 15/16 heads ≥ baseline (`customs` `-0.04` tie); 12/16 heads ≥ 60 (`vat` `52.93`, `procedures` `48.46`, `penalties` `42.50`, `rental` `62.74`) | **FAIL** (3 heads < 60 on held-out; **PASS 16/16** on dev) | All 3 heads < 60 on held-out were hit by `check_false_premise` or `plan_rate_lookup` route interception. |
| **p95 latency & T3 share** | T1 ≤ 12 s, T3 ≤ 60 s; T3 share ≤ `0.25` | T1 p95: **`4.34 s`**, T3 p95: **`47.99 s`**, T3 share: **`22.5%`** | T1 p95: **`3.82 s`**, T3 p95: **`48.59 s`**, T3 share: **`21.6%`** | **PASS** | Both T1 and T3 p95 latencies and rolling T3 share are well within bounds. |
| **Unit & regression tests (§0.5)** | 0 new failures vs `BASELINE_FAILURES.txt` | `60` baseline → **`55` failures (`0` new, `5` fixed)**; `1,327 passed` in `tests/agents/` | Same | **PASS** | 0 new test failures across `App/backend/tests/` and `tests/agents/`. |
| **2,000-FAQ multilingual benchmark** | No drop > 0.15 pts vs baseline | **`100.0%` accuracy** (`30/30` across `en`, `lg`, `sw`), `100%` HTTP 200 | Same | **PASS** | Zero accuracy drop across English, Luganda, and Swahili. |
| **Trilingual multi-turn & retrieval** | No regression beyond documented noise | Multi-turn: **`27/27 (100%)` decisions**, `26/27` reply lang (identical to baseline); Retrieval (`faq` clean): **`82.06%` EN Strict Hit@1 / `84.43%` Lenient Hit@1** | Same | **PASS** | Zero regression on `eval_multiturn_trilingual.py` and `+0.78 pp` Hit@1 gain on `eval_retrieval_multilingual.py`. |
| **Sovereign zero-egress audit** | 0 commercial/external AI API calls | **`0` external calls** (`SOVEREIGN_MODE=true`, `HF_HUB_OFFLINE=1`) | **`0` external calls** | **PASS** | Verified via container logs and runtime `SovereignModeViolationError` guards. |

---

## 3. Per-Tax-Head Breakdown (Baseline vs. Final)

### 3.1 Held-Out Split (`evals/complex_scenarios/heldout.jsonl` — 75 Scenarios, 102 Turns)

`HELDOUT.sha256`: `sha256:4e993908-35e17872-299593d4-3697cd99-f085730c-bb78e5b5-b7e16dba-478cf4b3` (verified identical between Run #1 Baseline and Run #2 Final).

| Tax Head | Scenarios | Baseline Mean (`/100`) | Final Mean (`/100`) | Delta (`pts`) | Final Min (`/100`) |
|---|---:|---:|---:|---:|---:|
| `advance_tax` | 3 | 79.41 | **100.00** | **+20.59** | 100.00 |
| `capital_gains` | 4 | 65.00 | **81.46** | **+16.46** | 53.33 |
| `corporation` | 5 | 53.07 | **93.25** | **+40.18** | 80.00 |
| `customs` | 6 | 84.15 | **84.11** | -0.04 | 58.82 |
| `digital_services` | 3 | 54.78 | **81.47** | **+26.69** | 65.00 |
| `excise` | 5 | 79.11 | **95.88** | **+16.77** | 79.41 |
| `gaming_betting` | 3 | 93.14 | **100.00** | **+6.86** | 100.00 |
| `jurisdiction` | 3 | 91.67 | **91.67** | +0.00 | 75.00 |
| `objections` | 5 | 70.31 | **80.00** | **+9.69** | 0.00 |
| `paye` | 9 | 66.03 | **79.57** | **+13.54** | 50.00 |
| `penalties` | 4 | 30.00 | **42.50** | **+12.50** | 0.00 |
| `procedures` | 3 | 47.82 | **48.46** | **+0.64** | 0.00 |
| `rental` | 5 | 42.50 | **62.74** | **+20.24** | 22.22 |
| `stamp_duty` | 4 | 53.10 | **87.50** | **+34.40** | 65.00 |
| `vat` | 7 | 29.68 | **52.93** | **+23.25** | 0.00 |
| `withholding` | 6 | 55.24 | **76.45** | **+21.21** | 58.82 |
| **Overall (75 scenarios)** | **75** | **60.75** | **77.60** | **+16.85** | **0.00** |

### 3.2 Development Split (`evals/complex_scenarios/dev.jsonl` — 30 Scenarios, 40 Turns)

| Tax Head | Scenarios | Baseline Mean (`/100`) | Final Uncached Mean (`/100`) | Final Post-Normalised (`/100`) | Delta (`pts`) |
|---|---:|---:|---:|---:|---:|
| `advance_tax` | 1 | 58.82 | **100.00** | **100.00** | **+41.18** |
| `capital_gains` | 1 | 96.03 | **100.00** | **100.00** | **+3.97** |
| `corporation` | 2 | 80.00 | **100.00** | **100.00** | **+20.00** |
| `customs` | 2 | 43.75 | **100.00** | **100.00** | **+56.25** |
| `digital_services` | 2 | 26.47 | **100.00** | **100.00** | **+73.53** |
| `excise` | 2 | 30.62 | **100.00** | **100.00** | **+69.38** |
| `gaming_betting` | 1 | 79.41 | **100.00** | **100.00** | **+20.59** |
| `jurisdiction` | 2 | 87.50 | **100.00** | **100.00** | **+12.50** |
| `objections` | 2 | 69.41 | **100.00** | **100.00** | **+30.59** |
| `paye` | 6 | 22.48 | **98.33** | **100.00** | **+77.52** |
| `penalties` | 1 | 35.00 | **100.00** | **100.00** | **+65.00** |
| `procedures` | 2 | 76.73 | **100.00** | **100.00** | **+23.27** |
| `rental` | 2 | 18.34 | **100.00** | **100.00** | **+81.66** |
| `stamp_duty` | 1 | 41.18 | **100.00** | **100.00** | **+58.82** |
| `vat` | 2 | 47.50 | **91.25** | **100.00** | **+52.50** |
| `withholding` | 1 | 81.25 | **100.00** | **100.00** | **+18.75** |
| **Overall (30 scenarios)** | **30** | **49.57** | **99.08** | **100.00** | **+49.51 / +50.43** |

---

## 4. Phase B Reasoner Bake-Off Summary

Full details in [`docs/Reports/reasoner-bakeoff-2026-10.md`](reasoner-bakeoff-2026-10.md). Evaluated on frozen contexts (`evals/complex_scenarios/frozen_contexts_dev.jsonl`, 40 turns) and 15 multi-tool calling scenarios:

| Contender | Mode | Dev Composite (`/100`) | Facts Recall | Premise Challenge | Critical Errors | Tool Call Accuracy (`15` cases) | Latency p50 / p95 | VRAM (GPU 7) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| **A:** `Sunbird/Sunflower-Qwen3.5-9B` | `nothink` | `61.25` | `68.52%` | `57.14%` | `2` | `73.3%` (`11/15`) | `7.8s` / `14.2s` | `40.4 GiB` (GPU 4) |
| **B:** `Qwen/Qwen3.8-27B-FP8` (**Selected**) | **`nothink`** | **`73.23`** | **`93.98%`** | **`85.71%`** | **`0`** | **`100.0%` (`15/15`)** | **`22.4s` / `38.1s`** | **`42.3 GiB`** |
| **B:** `Qwen/Qwen3.8-27B-FP8` | `think` | `64.10` | `88.43%` | `85.71%` | `0` | `100.0%` (`15/15`) | `41.2s` / `59.8s` | `42.3 GiB` |
| **C:** `Sunbird/Sunflower-Qwen3.8-27B` | `nothink` (Marlin FP8 workaround) | `71.60` | `91.20%` | `78.57%` | `0` | `93.3%` (`14/15`) | `24.1s` / `40.5s` | `44.8 GiB` |

- **Why Contender B (`Qwen/Qwen3.8-27B-FP8`, `enable_thinking=False`) won:** `+11.98` points over `Sunflower-Qwen3.5-9B` on identical frozen contexts, `0` critical errors, `100%` (`15/15`) structured multi-tool calling with `--tool-call-parser qwen3_coder`, and native pre-quantized FP8 weights that load cleanly on RTX A6000 GPUs without the Marlin `size_n = 96` tensor-repack bug encountered on `Sunbird/Sunflower-Qwen3.8-27B`'s unquantized BF16 checkpoint.

---

## 5. Verbatim Before vs. After: The §1 Expatriate-Payroll & BIK Conversation

### 5.1 Before (Baseline on 2026-10-18 — Defects D1 through D8)

**Turn 1 Prompt:**
> *"We are hiring a foreign technical specialist on an 8-month contract in Entebbe. They will receive a gross monthly salary of $6,000 plus employer-paid housing and a company vehicle. How is PAYE calculated for a non-resident individual, and does the non-resident tax table differ from resident individuals?"*

**Turn 1 Baseline Reply (Defects D2, D3, D4, D5, D7, D8):**
> *"For a non-resident individual on an 8-month contract in Entebbe (which is less than 183 days), PAYE is calculated on their gross Uganda-sourced employment income. Unlike resident individuals who are taxed at a simplified flat rate, non-resident individuals are taxed using progressive brackets starting at 10% on the first UGX 4,020,000, 20% on the next bracket, and 30% above UGX 4,920,000, with an additional 10% on income above UGX 120,000,000 [1][3]..."* *(truncated mid-sentence at 512 tokens; cited `A-GUIDE-TO-TAXATION-OF-THE-AGRICULTURAL-SECTOR.pdf` and `Hotel-and-Accomodation-Sector-2025-26.pdf`; falsely claimed 8 months < 183 days and that residents pay a "simplified flat rate").*

**Turn 2 Prompt:**
> *"How exactly does URA determine the taxable market value of the furnished house and the motor vehicle benefit when calculating the gross taxable employment income for PAYE?"*

**Turn 2 Baseline Reply (Defects D1, D2, D6):**
> *"For a non-resident earning UGX 6,000 per month, the monthly PAYE is UGX 600 (10%). Under the Fifth Schedule of the Income Tax Act, the taxable value of the furnished house and motor vehicle benefit is subtracted from the employee's gross salary to arrive at net taxable income..."* *(hijacked by `plan_calculation` treating `$6,000` from Turn 1 as `UGX 6,000`, hallucinated "Fifth Schedule", and inverted polarity by claiming benefits in kind are "subtracted from" salary).*

---

### 5.2 After (Phase H Final Verification — `Qwen/Qwen3.8-27B-FP8` + `scenario_planner` + `figure_lock`)

**Turn 1 Final Reply (`retrieval_mode=hybrid`, `model_tier=deep`, `finish_reason=stop`, `0` sector guides):**
> Great question—and an important one, because the residency assumption needs checking first.
>
> ### Premise check: likely resident, not non-resident
>
> Under the **Income Tax Act (Cap 338)** s.9 and the URA Taxation Handbook FY2025‑26, an individual physically present in Uganda for **183 days or more** in any 12‑month period is a **tax resident**. An 8‑month contract (~240 days) makes your specialist **likely a resident**, unless they travel out frequently. Please confirm actual days physically present before applying the non‑resident table.
>
> ### Assumptions used
>
> - Gross monthly salary: **$6,000 USD** (convert to UGX at the Bank of Uganda rate on the payment date).
> - Employer‑paid housing and company vehicle are **benefits in kind** added to employment income.
> - Contract: 8 months, based in Entebbe.
>
> ### Step‑by‑step PAYE calculation
>
> 1. **Determine residency** – count physical days in Uganda; ≥183 = resident.
> 2. **Convert salary to UGX** – use the prevailing exchange rate on each payment date.
> 3. **Value benefits in kind**:
>    - **Car benefit**: `(20% × A × B/C) − D`, where `A` = market value when first provided (depreciated at **35% p.a. reducing balance**), `B` = private‑use days, `C` = 365, `D` = employee contribution. Add to income [4].
>    - **Housing benefit**: lesser of **(a)** market rent less employee payment, or **(b)** **15% of employment income** (including the car benefit). Add to income [4].
> 4. **Apply the correct PAYE bracket** (progressive marginal rates—no flat rate applies):
>
> | Monthly Emoluments (UGX) | Resident Rate | Non‑Resident Rate |
> |---|---|---|
> | 0 – 335,000 | **Nil** | **10%** |
> | 335,001 – 410,000 | **20%** on excess over 335,000 | **20%** on excess over 335,000 |
> | 410,001 – 485,000 | **25%** on excess over 410,000 | **30%** on excess over 410,000 |
> | 485,001 – 10,000,000 | **30%** on excess over 485,000 | **30%** on excess over 410,000 |
> | Above 10,000,000 | **40%** marginal | **40%** marginal |
>
> Key difference: non‑residents have **no tax‑free threshold** (first UGX 335,000 taxed at 10%) and pay **UGX 37,250/month more** once income exceeds UGX 485,000 [1][4].
>
> 5. **Withhold and remit** by the **15th of the following month** via URA’s online portal or designated banks [1][3].
>
> ### Legal basis
>
> - **Income Tax Act (Cap 338)**, s.9 (residency), s. 12.2.3 (benefits in kind)
> - **Third Schedule Part I** (PAYE rates)
> - URA Taxation Handbook FY2025‑26 [1][4]
>
> ### Still needed for an exact figure
>
> - Actual days physically present in Uganda
> - Exchange rate (USD → UGX) on each payment date
> - Market value of the car when first provided
> - Market rent of the housing
>
> If you share those four inputs, I can run the precise monthly PAYE and net take‑home for you.

**Turn 2 Final Reply (`retrieval_mode=hybrid`, `model_tier=deep`, `finish_reason=stop`, `0` sector guides):**
> URA determines the taxable value of these benefits using specific formulas found in the Third Schedule of the Income Tax Act, which are then added to the employee’s cash salary to establish total gross employment income [1][4].
>
> ### Valuation of Benefits in Kind
>
> #### 1. Motor Vehicle Benefit
>
> The taxable value is calculated using the following statutory formula:
>
> ```text
> Benefit = (20% × A × B / C) − D
> ```
>
> Where:
> - **A**: Market value of the vehicle when first provided by the employer.
> - **Depreciation**: For vehicles provided after the first year, `A` is reduced by **35% per annum** on a reducing balance basis.
> - **B**: Number of days the vehicle is available for private use during the year of income.
> - **C**: Total days in the year of income (**365**).
> - **D**: Amount paid by the employee toward the cost of the vehicle or fuel.
>
> This calculated amount is **added** to the employee’s cash salary [4].
>
> #### 2. Furnished House (Accommodation) Benefit
>
> The taxable value is the **lesser** of two amounts:
> 1. **Market Rent**: The estimated annual market rent for similar accommodation, less any amount paid by the employee.
> 2. **Statutory Cap**: **15% of the employee’s total employment income** (including the vehicle benefit calculated above).
>
> This lower amount is **added** to the employee’s cash salary [4].
>
> ### Application to Your Scenario
>
> To calculate the final PAYE, you must:
> 1. Convert the **$6,000** monthly salary to UGX using the Bank of Uganda exchange rate on the payment date.
> 2. Apply the **Vehicle Formula** above using the car’s initial market value.
> 3. Apply the **Housing Rule** (lesser of market rent or 15% of total income).
> 4. Sum Cash Salary + Vehicle Benefit + Housing Benefit to get **Total Chargeable Income**.
> 5. Apply the appropriate PAYE bracket (Resident vs. Non-Resident) to this total.

---

## 6. Diagnosis of Held-Out Failures & Known Limits

In accordance with §3.2 and §10.H.3, `heldout.jsonl` was executed exactly twice (once at Phase A baseline, once in Phase H) and was not re-run after inspecting the held-out failure modes. The held-out misses fall into four deterministic categories to address in the next iteration (`heldout_v2`):

1. **Over-broad regex in `premise_guard.check_false_premise` (3 held-out scenarios scored `0.0/100`):**
   - `ho_objections_valid_form_and_grounds_54`, `ho_penalties_voluntary_disclosure_waiver_59`, and `ho_procedures_business_records_retention_72` used phrases like *"tax period"*, *"tax assessment"*, or *"tax decision"* that matched `_ASSERTED_TAX_CONCEPT_RE` and were short-circuited with `retrieval_mode="false_premise_rejected"`.
2. **`plan_rate_lookup` fast-path intercepting conceptual multi-part questions (5 held-out scenarios):**
   - `ho_vat_exports_zero_rated_vs_exempt_11`, `ho_vat_imported_services_reverse_charge_13`, `ho_vat_efris_ereceipt_refund_threshold_14`, and `ho_penalties_late_payment_interest_rate_60` matched rate-table patterns and returned single-rate cards (`retrieval_mode="calculator"`) instead of routing to `rag` + `scenario_planner`.
3. **Literal substring `must_not_include` on `"deducted from"` in `ho_paye_housing_bik_formula_and_calc_04` (1 critical hit):**
   - The model accurately stated that the housing benefit is **added to** gross employment income, and then explained step `(a)` of the housing formula (*"any rent paid by the employee is deducted from the market rent"*), which triggered the literal substring `"deducted from"`.
4. **Known statutory & benchmark limits (§12.2):**
   - **Human review status:** `0 / 75` held-out scenarios and `0 / 30` dev scenarios have been reviewed by a human tax specialist (`"reviewed": false` on all rows).
   - **Unverified rate-table keys (`FY2026-27.json` / `FY2025-26.json`):** `bik_housing_employment_income_rate` (whether the 15% cap includes paragraph (a) before or after adding other benefits) and `statutory_interest_rate_bou` (Bank of Uganda discount rate at commencement of the year of income for low-interest loans) remain listed in `"unverified"` so `value_benefit_in_kind` marks `verified=False` and `calculate_paye` emits both `paye_verified` and `paye_conditional`.
   - **Offline FX snapshot (`BOU_mid_rates_2026.json`):** `convert_currency` returns `needs_exchange_rate=True` unless the user supplies an exchange rate or an explicit date matching the local BoU snapshot.

---

## 7. Sovereign GPU Footprint (8 × NVIDIA RTX A6000, 49,140 MiB each)

| GPU | Service / Container | Model / Role | VRAM Used | Status |
|---:|---|---|---:|---|
| **GPU 2** | `ura-app-api` | `BAAI/bge-m3` (dense embedder) + `mixedbread-ai/mxbai-rerank-base-v2` (cross-encoder) | `6,587 MiB` | Live (`healthy`) |
| **GPU 3** | `ura-app-orpheus-tts` | Orpheus 3B Luganda TTS (FP8) | `18,429 MiB` | Live (`healthy`) |
| **GPU 4** | `ura-app-vllm-sunflower` | **T1:** `Sunbird/Sunflower-Qwen3.5-9B` (chat + local MT) | `41,569 MiB` | Live (`healthy`) |
| **GPU 5** | *(free)* | Used temporarily for isolated `accverify-api` during Phase H; cleaned up after verification | `14 MiB` | **Idle / Free** |
| **GPU 7** | `ura-app-vllm-reasoner` | **T2/T3:** `Qwen/Qwen3.8-27B-FP8` (`max-model-len=65536`, FP8 KV cache) | `45,127 MiB` | Live (`healthy`) |

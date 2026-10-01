# Plan: Frontier Tax Reasoning — Rental Income Pooling, Reflection Resilience, and Canned Fallback Elimination

Status: **Ready for Implementation**  
Branch: `dev-2.0`  
Target: Achieving Gemini 2.5 Pro / Claude 3.7 Sonnet / Opus-Class Response Fidelity on Complex Uganda Tax Scenarios.

---

## 1. Problem Diagnosis & Evidence

### 1.1 The Test Failure
When the following real-world advisory scenario was submitted to the URA Assistant on `dev-2.0`:
> *"I own a multi-story building in Kampala generating UGX 90M annually—the upper floors are residential apartments (UGX 40M) and the ground floor is commercial shops (UGX 50M). How is my rental income tax calculated as an individual? Does the UGX 2,820,000 threshold and 20% expense deduction apply across both, or are they treated separately?"*

The system returned:
```markdown
Here's the most relevant guidance I found in official URA sources:

For an individual resident landlord:

Determine the total annual gross rental income (R) from all properties;
Deduct the statutory tax-free threshold of UGX 2,820,000 to determine chargeable income (Chargeable income = R - UGX 2,820,000) with NO other expense deductions allowed;
Calculate tax payable at twelve percent (12%) on the chargeable income. For example, on gross rent of UGX 6,000,000, deduct UGX 2,820,000 to get chargeable income of UGX 3,180,000, yielding rental tax payable of UGX 381,600 (12% of UGX 3,180,000).
If you get stuck at any step, URA is happy to help: visit https://ura.go.ug...
```

### 1.2 Root-Cause Breakdown
Tracing the pipeline execution revealed three distinct failures:
1. **Un-pooled Multi-Stream Rental Calculations in Draft Generation:**
   The base model tried to calculate taxes for the two streams separately (UGX 40M and UGX 50M) and deducted the UGX 2,820,000 threshold twice (yielding UGX 10,123,200 total tax). It failed to synthesize that individual landlords must pool all properties into a single gross annual rental figure (UGX 90M) and deduct the threshold once (UGX 10,461,600). It also omitted addressing the taxpayer's question regarding the repealed 20% expense deduction.
2. **Lexical Claim Verifier Triggering Revision:**
   Because the un-pooled numbers (`UGX 4,461,600` and `UGX 5,661,600`) did not exist verbatim in the retrieved URA handbook passages, `claim_verifier.py` scored the draft low and returned `decision: revise`.
3. **`_reflect_llm` 15-Second Socket Timeout:**
   In `App/backend/app/service.py:4166`, `_reflect_llm` had a hardcoded `timeout=15.0`. On a 72B parameter model (`Qwen2.5-72B-Instruct-AWQ`), a reflection call with context passages takes ~30–45 seconds. The call timed out silently, returning `""`.
4. **Destructive Canned FAQ Dump Fallback:**
   When `_reflect_llm` returned empty, the Response Judge fell back to `_build_grounded_revision()`. This function prepended `"Here's the most relevant guidance I found in official URA sources:"` and dumped the verbatim raw FAQ text with the canned UGX 6M example, completely discarding the user's specific scenario.

---

## 2. Technical Architecture & Solutions

### 2.1 Solution 1: Pre-Evaluate Deterministic Rental Calculations with Multi-Property Pooling
* **File:** `App/backend/app/service.py` (`_evaluate_calculation_context`)
* **Logic:**
  - Inspect query for rental tax context and money amounts.
  - When multiple rental figures appear (e.g. `40M residential` + `50M commercial` or `total 90M`), detect whether individual rental tax is being queried.
  - Compute the statutory rental tax deterministically using `app/tools/calculators.py:calculate_rental_tax`:
    - Total Gross Rent $R = \text{UGX } 90,000,000$.
    - Tax-Free Threshold $= \text{UGX } 2,820,000$ (applied once).
    - Chargeable Income $= 90,000,000 - 2,820,000 = \text{UGX } 87,180,000$.
    - Tax Payable at 12% $= 87,180,000 \times 0.12 = \text{UGX } 10,461,600$.
  - Inject this verified statutory arithmetic into the LLM context under `## Verified Statutory Calculation (Tool Ground Truth)` so the Master Brain formats the exact numbers into a clean tabular response.

### 2.2 Solution 2: Explicit Statutory Instructions in `SYSTEM_PROMPT`
* **File:** `App/backend/app/llm.py` (`SYSTEM_PROMPT`)
* **Additions:**
  - **Individual Rental Income Pooling:** Under Section 5(3) of the *Income Tax Act*, an individual's rental income from all properties (residential, commercial, land) is **pooled together** into a single total annual gross rental income.
  - **Single Threshold Application:** The statutory tax-free threshold of **UGX 2,820,000** applies **once per individual taxpayer per tax year**, never per property, per building, or per floor.
  - **Repealed Expense Deductions:** Explicitly state that individual landlords are entitled to **zero expense deductions** (the previous 20% expense deduction was repealed by the Income Tax Amendment Act; only corporate landlords retain expense deductions capped at 50%).
  - **Statutory Rate:** Flat rate of **12%** on chargeable income exceeding UGX 2,820,000.

### 2.3 Solution 3: Resilient Reflection (`_reflect_llm`) & Suppressing Canned FAQ Dumps
* **File:** `App/backend/app/service.py` (`_reflect_llm`, `_evaluate_response_judge`)
* **Implementation:**
  - Increase `_reflect_llm` timeout from `15.0s` to `60.0s`.
  - For user queries that present custom figures or situational scenarios (e.g. `UGX 90M`, `40M`, `50M`), **do not** replace the draft with `_build_grounded_revision()` (which dumps irrelevant FAQ text).
  - If reflection fails or times out, retain the structured model draft with an explicit disclaimer or advice note rather than erasing the user's figures with a canned example.

---

## 3. Step-by-Step Implementation Plan

### Step 1: Implement Rental Pooling in `_evaluate_calculation_context` (`service.py`)
- Enhance `_evaluate_calculation_context()` to handle rental calculations where the user provides separate property income streams (residential + commercial).
- Extract all rental amounts, sum them if separate components are stated, and execute `calculate_rental_tax`.
- Format a verified statutory calculation card in prompt context.

### Step 2: Update System Prompt Standards in `llm.py`
- Add Section 5(3) *Income Tax Act* rental pooling standards to `SYSTEM_PROMPT`.
- Detail the single UGX 2,820,000 annual threshold rule and the repeal of the 20% expense deduction for individuals.
- Require presenting multi-property rental scenarios as a clean breakdown table:
  | Stream / Category | Gross Annual Rent | Threshold Deducted | Chargeable Income | Tax Rate | Tax Payable |
  | :--- | :--- | :--- | :--- | :--- | :--- |

### Step 3: Enhance `_reflect_llm` and Response Judge Fallbacks (`service.py`)
- Set `timeout=60.0` in `_reflect_llm`.
- Update `_evaluate_response_judge`: when `has_money_amount(message)` is True, suppress `_build_grounded_revision` so canned examples do not overwrite user-specific calculations.

### Step 4: Verification & Testing
- Run test suite:
  ```bash
  PYTHONPATH=App/backend python3 -m pytest tests/agents/test_frontier_reasoning_pipeline.py -q
  ```
- Test live scenario against `https://recreational-fact-handbook-foods.trycloudflare.com/`:
  - Verify that UGX 90M is pooled.
  - Verify that UGX 2,820,000 is subtracted once.
  - Verify that the 12% calculation yields UGX 10,461,600.
  - Verify that the repeal of the 20% expense deduction is clearly explained.
  - Verify that zero canned UGX 6M FAQ text appears.

---

## 4. Rollout & Commit Strategy
1. Commit the plan file `docs/plans/frontier-tax-reasoning-rental-pooling-and-reflection-plan.md` to `dev-2.0` and push to remote.
2. Implement the changes in `service.py` and `llm.py`.
3. Commit and push the implementation to `dev-2.0`.
4. Restart `ura-app-api-dev2` and verify the live endpoint.

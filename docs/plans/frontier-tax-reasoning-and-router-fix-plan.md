# Plan: Frontier Tax Reasoning & High-Precision Pipeline — Achieving Opus/Gemini 3.8-Class Accuracy

Status: **Specification Ready for Implementation.**  
Authored: October 2026.  
Target Environment: `dev-2.0` (FastAPI backend + Next.js frontend + vLLM on multi-GPU server).

---

## 0. Ground Rules & Invariants for the Implementing Session

1. **Working Directory & Python Path:**
   - Runtime code lives under `App/backend/app` (imported as `app`). Never change or move `App/backend` or `App/frontend`.
   - Run backend tests from repo root using:
     ```bash
     PYTHONPATH=App/backend python3 -m pytest App/backend/tests tests/agents tests/chaos -q
     ```
2. **Invariants (from `AGENTS.md` and `App/backend/AGENTS.md`):**
   - **Zero Regression on 2,000-FAQ Benchmark:** All existing FAQ regressions, golden sets, and vernacular routing (English, Luganda, Swahili) must continue to pass (baseline ≥ 99.85%).
   - **Endpoint Manifest Contract:** If any FastAPI route is modified or added, update `tests/test_all_endpoints_e2e.py` (`EXPECTED_ENDPOINTS`, `COVERAGE`, and locked endpoint counts).
   - **Feature Flag Governance:** Any new feature flag must be registered in `App/backend/app/flags.py`, documented in `docs/RAG_ARCHITECTURE.md`, and reflected in `.env.example`.
   - **Secrets Safety:** Never commit, log, or echo credentials. Use `SecretStr` for API keys.
3. **Investigation Origin:**
   - Prompt testing on `dev-2.0` revealed that complex tax queries are corrupted or intercepted prematurely by rigid regex heuristics, aggressive spell-checking, and naive false-premise blocks before reaching the LLM, or are choked by a 512-token limit and single-tier 14B model.

---

## 1. Executive Summary & Problem Diagnosis

### 1.1 The Live Test Failures

During live evaluation on `dev-2.0`, five representative complex tax questions were submitted to the assistant. Every single one encountered severe failure modes:

| Query Scenario | User Input | Actual Response | Root Cause Mechanism |
|---|---|---|---|
| **1. EFRIS & Input VAT** | VAT distributor (UGX 600M turnover) bought goods (UGX 40M) with manual paper invoice. Can input VAT be claimed? Penalties? | *"I know deadlines can be stressful... What is the amount in UGX? (e.g. 1,000,000 or 1m — tell me if it already includes VAT)"* | **Regex Interception:** `calculator_router.py:649` saw money amounts + "VAT" keyword, bypassed the calculation verb check, and forced a calculator wizard. `text_signals.py:160` matched "penalties" to inject empathy. |
| **2. Mixed-Use Rental Tax** | UGX 90M gross: UGX 40M residential + UGX 50M commercial shops. How is rental tax calculated for an individual? | Repeated canned UGX 6M FAQ example; truncated mid-sentence: `Chargeable income = UGX 6,000,0001]`. Ignored user's 90M/40M/50M amounts. | **Token Limit & Capacity:** `LLM_MAX_TOKENS=512` cut generation off mid-sentence. Model failed to synthesize multi-property pooling rules. |
| **3. WHT Default Assessment & Objection** | Administrative default assessment of UGX 50M for WHT. 30% payment rule on disputed tax? | *"Under current Ugandan tax laws... there is no official 'Disputes Tax'. The URA does not administer or impose such a tax..."* | **Cascading Corruption:** `query.py` spellchecker turned *"disputed tax"* into *"dispute tax"*. `premise_guard.py` flagged "disputes tax" as a nonexistent statutory tax and rejected the entire query. |
| **4. Imported Digital Services (SaaS)** | Ugandan company pays $4,000/mo to Irish SaaS provider. WHT and reverse-charge VAT rates? | Hallucinated a physical customs formula (`CIF + CIF x Duty Rate`), was caught by the Response Judge, and replaced with unrelated FAQ bullets about hotels and schools. | **Noisy Flat Retrieval & Naive Revision:** Flat dense retrieval lacked legal graph joins; Response Judge's `_build_grounded_revision` blindly dumped raw text excerpts. |
| **5. Non-Resident PAYE & Benefits in Kind** | Foreign specialist on 8-month contract ($6,000/mo + house + car). Non-resident PAYE calculation? | *"Under current Ugandan tax laws... there is no official 'Non Resident Tax'. The URA does not administer or impose such a tax..."* | **False Premise Regex Over-Extraction:** The phrase *"non-resident tax table"* matched `(?P<modifier>.*?)\s+tax`. The guard treated "non-resident" as a tax kind and aborted. |

---

## 2. Target Architecture: Reaching Opus / Gemini 3.8 Quality

To reach frontier-class reasoning and precision, the system must transition from **brittle heuristic interception** to **capability-tiered semantic reasoning**:

```
                   ┌────────────────────────────────────────┐
                   │           Taxpayer Message             │
                   └───────────────────┬────────────────────┘
                                       │
                         [ Clean Spell-Check / AST ]
                         (No destructive word-mutations)
                                       │
                                       ▼
                   ┌────────────────────────────────────────┐
                   │        Disambiguation Router           │
                   │  - Explicit Calculation verbs only     │
                   │  - Semantic Intent & Question Scope    │
                   └───────┬────────────────────────┬───────┘
                           │                        │
       [Pure Arithmetic Calculation Ask]   [Complex Legal / Informational Query]
                           │                        │
                           ▼                        ▼
                ┌──────────────────────┐  ┌────────────────────────────────────┐
                │ MCP Calculator /     │  │     Statutory Multi-Hop RAG        │
                │ Guided Wizard Flow   │  │  - Qdrant Dense (BGE-M3)           │
                └──────────────────────┘  │  - BM25 Sparse Lexical             │
                                          │  - Statutory Knowledge Graph Legs  │
                                          └─────────────────┬──────────────────┘
                                                            │
                                                            ▼
                                          ┌────────────────────────────────────┐
                                          │   Capability-Tiered Model Engine   │
                                          │   (select_tier in service.py)      │
                                          │  - T1: Sunflower-14B (Fast/FAQ/MT) │
                                          │  - T3: Frontier Reasoning Tier     │
                                          │    (Gemini 2.5/Pro or Llama-70B)   │
                                          │  - Max tokens: 1,536 - 2,048       │
                                          └─────────────────┬──────────────────┘
                                                            │
                                                            ▼
                                          ┌────────────────────────────────────┐
                                          │      Targeted Response Judge       │
                                          │  - No franken-text dump fallbacks  │
                                          │  - Bounded prompt reflection       │
                                          └────────────────────────────────────┘
```

---

## 3. Implementation Steps: Phase-by-Phase

### Phase 0: Emergency Heuristic & Guard Hotfixes

Fix the five acute bugs that caused immediate failure during testing.

#### 0.1 Fix Calculator Verb Bypass in `App/backend/app/calculator_router.py`
- **Location:** `App/backend/app/calculator_router.py:649-655`
- **Current Defect:**
  ```python
  if not _CALC_VERB_RE.search(text) and not (
      has_money_amount(text) and (
          _DEFINITIONAL_OPENER_RE.search(text)
          or detect_calculator_intent(text) is not None
      )
  ):
      return None
  ```
  Any sentence mentioning money and a tax name (e.g. "turnover of UGX 600M... input VAT") bypasses the calculation verb check.
- **Remedy:**
  Require an explicit calculation verb (`calculate`, `compute`, `how much`, `work out`) or a direct computation prompt before routing to a calculator. Informational questions about legal provisions, penalties, or input tax claims must return `None`:
  ```python
  # Informational legal inquiry guard:
  _LEGAL_INQUIRY_RE = re.compile(
      r"\b(can\s+i|eligible|claim\b.*\b(?:input|vat|tax)|penalt\w*|disallow\w*|require\w*|obligat\w*|procedure|statut\w*|act\b)\b",
      re.IGNORECASE,
  )
  if _LEGAL_INQUIRY_RE.search(text) and not _CALC_VERB_RE.search(text):
      return None
  ```
  Remove `or detect_calculator_intent(text) is not None` from the unconditional amount-bypass clause.

#### 0.2 Fix Distress Pattern Over-Triggering in `App/backend/app/text_signals.py`
- **Location:** `App/backend/app/text_signals.py:159-166`
- **Current Defect:**
  `r"urgent|asap|...|penalt|\bfines?\b|\bfined\b|audit|enforcement..."` matches any inquiry regarding statutory penalties or audit timelines.
- **Remedy:**
  Refine the urgency regex to require actual temporal/emotional pressure, e.g. `\b(?:under\s+audit|being\s+audited|facing\s+penalt\w+|incurred\s+penalt\w+|threatened\s+with)\b`, rather than the mere statutory noun `penalties` or `fine`.

#### 0.3 Fix Destructive Query Rewriting in `App/backend/app/query.py`
- **Location:** `App/backend/app/query.py` in `correct_spelling()` and `expand_abbreviations()`
- **Current Defect:**
  - `_fuzzy_correct_word` has a narrow vocabulary; valid words like `"disputed"` (len 8) have distance 1 to `"dispute"` (len 7) and are incorrectly mutated.
  - `expand_abbreviations` blindly replaces `WHT` with `Withholding Tax (WHT)`, resulting in `Withholding Tax Withholding Tax (WHT))`.
- **Remedy:**
  - Add standard grammatical inflections and common English words (`disputed`, `consulting`, `contracting`, `registered`, `assessed`) to `_COMMON_ENGLISH_WORDS` or `_ALL_CORRECTABLE_VOCAB`.
  - Fix abbreviation expansion to use word boundaries and avoid expanding if already preceded or followed by the expanded term:
    `re.sub(r"(?<!Withholding Tax )\bWHT\b(?!\))", "Withholding Tax (WHT)", query)`

#### 0.4 Fix False Premise Guard False Positives in `App/backend/app/premise_guard.py`
- **Location:** `App/backend/app/premise_guard.py:206-262`
- **Current Defect:**
  - `_CANDIDATE_PATTERNS` matches `(?P<modifier>.*?)\s+(?P<kind>tax|duty...)`. When the word after "tax" is a noun (e.g. `tax table`, `tax return`, `tax bracket`, `tax rate`, `tax credit`), it incorrectly extracts candidate tax concepts like "non resident tax".
  - Missing legitimate concepts: `"dispute"`, `"disputes"`, `"non-resident"`, `"non resident"`.
- **Remedy:**
  - Add negative lookahead so "tax" followed by noun collocations is ignored:
    ```python
    _NON_TAX_NOUN_FOLLOWERS = re.compile(
        r"^(table|tables|bracket|brackets|rate|rates|band|bands|return|returns|"
        r"law|laws|act|acts|code|codes|assessment|assessments|credit|credits|"
        r"invoice|invoices|receipt|receipts|officer|officers|portal|head|heads)\b",
        re.IGNORECASE,
    )
    ```
    If `_NON_TAX_NOUN_FOLLOWERS.match(target[match.end("kind"):].strip())`, do not extract as a candidate tax concept.
  - Add `"dispute"`, `"disputes"`, `"non resident"`, `"non-resident"`, `"input"`, `"input vat"` to `_LEGITIMATE_TAX_MODIFIERS`.

---

### Phase 1: Output Generation Budget & Context Expansion

#### 1.1 Increase Output Generation Limits
- **Location:** `App/backend/app/service.py` and environment configuration.
- **Problem:**
  `LLM_MAX_TOKENS=512` causes comprehensive legal analyses to be chopped off mid-sentence (as observed in Question 2: `UGX 6,000,0001]`).
- **Implementation:**
  - Update default `LLM_MAX_TOKENS=1536` (with `LLM_CONTEXT_WINDOW=8192` or `16384`) in:
    - `App/backend/app/service.py`
    - `configs/prototype.env`
    - `.env.example`
    - `docker-compose.yml` / `docker-compose.gpu-salt.yml`
  - In `_call_llm_with_deadline()`, allow full token generation up to 1,536 tokens for RAG answers.

#### 1.2 Improve Synthesis Prompt for Multi-Part Questions
- In `App/backend/app/service.py:7260` (system prompt construction):
  - Instruct the model to address all specific numbers, scenarios, and taxpayer classifications provided by the user.
  - Require explicit statutory sections where available (e.g. VAT Act Section 16, 24, 28; Income Tax Act Section 5, 83, 86; Tax Procedures Code Act Section 23, 24).
  - Explicitly forbid cutting off or substituting generic FAQ amounts when the user provided concrete figures.

---

### Phase 2: Frontier Capability-Tiered Model Routing (Activating Tier 3)

The repository already defines `ModelTier` (T0, T1, T2, T3) and `select_tier()` in `App/backend/app/providers/routing.py`, but `service.py` currently only logs the decision and uses the same 14B model for all generation.

#### 2.1 Wire Active Model Selection in `service.py`
- **Location:** `App/backend/app/service.py:7863` and `_call_llm_with_deadline()`
- **Implementation:**
  - When `flags.is_enabled("model_tiering")` is true:
    - Route complex queries (multi-hop, objections/disputes, legal synthesis, high-value corporate inquiries) to **ModelTier.T3**.
    - For Tier 3, use the configured frontier provider:
      1. **Primary Cloud Tier:** Gemini 2.5 Flash / Pro (via `app/providers/gateway.py` using `GEMINI_API_KEY`) or Cloudflare Llama 3.3 70B (via `workers_ai_chat`).
      2. **Local Frontier Tier:** Deploy a 32B or 70B model on the three idle GPUs (GPU 2, 3, 6 — each having ~48 GB free VRAM, e.g. Qwen2.5-32B-Instruct or Llama-3.3-70B-Instruct-GPTQ).
    - For Tier 1 (fast lookups, simple definitions, vernacular translations): maintain `Sunflower-14B-FP8`.

#### 2.2 Define Tier 3 Reasoning Criteria in `select_tier`
- Elevate to T3 when any of the following apply:
  - Query contains multiple statutory concepts (e.g. both WHT and VAT, or income tax and PAYE).
  - Query involves cross-border / non-resident transactions.
  - Query concerns tax dispute / objection / assessment procedures.
  - Query contains complex financial numbers requiring step-by-step statutory reconciliation.

---

### Phase 3: Statutory Knowledge Graph Fusion (Multi-Hop Joins)

Enable the embedded graph in `App/backend/app/graph/` to provide the statutory connections that flat retrieval misses.

#### 3.1 Activate Graph Edge Retrieval for Compositional Questions
- **Location:** `App/backend/app/graph/query.py` and `App/backend/app/retriever.py`
- **Key Joins to Index & Query:**
  1. `Electronic Services (Cross-Border)` $\rightarrow$ `VAT Act s.16 (Reverse Charge)` + `Income Tax Act s.86 (15% WHT)`.
  2. `EFRIS Non-Compliance` $\rightarrow$ `VAT Act s.24 / s.28 (Disallowance of Input Tax without Fiscal Receipt)`.
  3. `Rental Income (Individual)` $\rightarrow$ `Income Tax Act s.5(3) (12% flat rate on gross above UGX 2,820,000; zero expense deductions)`.
  4. `Tax Dispute Objection` $\rightarrow$ `Tax Procedures Code Act s.23 (45-day deadline) + s.24 (30% deposit rule)`.
  5. `Non-Resident Employment` $\rightarrow$ `Income Tax Act Third Schedule, Part II (Non-resident PAYE graduated rates starting at 10% from shilling 1)`.

#### 3.2 Enable Graph Fusion Behind `FLAG_GRAPH_FUSION`
- Ensure graph-retrieved nodes format structured statutory provenance cards into the LLM context alongside hybrid vector/BM25 chunks.

---

### Phase 4: Modernizing the Response Judge & Eliminating Franken-Text Fallbacks

#### 4.1 Replace `_build_grounded_revision` with Targeted Reflection
- **Location:** `App/backend/app/service.py:5556` and `4111-4170`
- **Current Defect:**
  When claim verification returns `revise`, `_build_grounded_revision` concatenates disconnected sentence fragments from whatever chunks were retrieved, creating bizarre replies (e.g. talking about hotel income and schools when the user asked about Irish SaaS).
- **Implementation:**
  - Instead of string concatenation, if the draft is flagged for missing citations or unsupported claims, trigger a **bounded one-turn reflection call**:
    ```python
    revised = self._reflect_llm(
        query=message,
        draft_reply=reply,
        unsupported_claims=claim_report.get("unsupported_claims", []),
        passages=hits,
    )
    ```
  - If the model cannot ground the specific claim, instruct it to explicitly state the statutory boundary rather than substituting unrelated FAQ excerpts.

---

### Phase 5: Verification & End-to-End Test Suite

To prove that the pipeline has reached the target quality level, verify all 5 test scenarios and run regression gates.

#### 5.1 Unit Tests for Regressions & Fixed Pattern Guards
Create `tests/agents/test_frontier_reasoning_pipeline.py`:
- Test that `calculator_router.py` does not intercept complex legal questions containing numbers.
- Test that `text_signals.py` does not treat statutory terms like "penalties" as emotional urgency.
- Test that `query.py` does not corrupt "disputed", "consulting", or acronyms.
- Test that `premise_guard.py` permits "non-resident tax table", "disputes tax", and "input VAT".

#### 5.2 End-to-End Golden Scenarios to Verify
1. **Scenario 1 (EFRIS & Input VAT):** Confirms that an unregistered or manual paper tax invoice from an EFRIS-mandated supplier **disallows** input tax credit under the VAT Act, explains the supplier's penalty under Section 62B of the Tax Procedures Code Act, and guides the taxpayer on portal reporting.
2. **Scenario 2 (Rental Income: Residential & Commercial):** Correctly aggregates gross rental income (UGX 40M + UGX 50M = UGX 90M), subtracts the annual statutory tax-free threshold of UGX 2,820,000, applies the flat 12% rate without expense deductions, and explains why residential and commercial income are pooled for individual landlords.
3. **Scenario 3 (Objection & 30% Dispute Rule):** Details the 45-day deadline to lodge an objection under Section 23 of the Tax Procedures Code Act, clarifies the requirement to pay 30% of the tax assessed or the undisputed tax (whichever is greater) pending objection, and explains the 90-day deemed decision rule.
4. **Scenario 4 (Cross-Border SaaS / Digital Services):** Correctly identifies 15% non-resident Withholding Tax (Section 86) and 18% reverse-charge VAT on imported services (Section 16), explaining filing via Form DT-1008.
5. **Scenario 5 (Non-Resident PAYE):** Details that non-residents are taxed from the first shilling without a tax-free threshold (10% on first UGX 4,020,000, 20% on next, 30% on excess + 10% surcharge above UGX 10M/month), and explains benefit-in-kind valuation for housing and motor vehicles under the Fifth Schedule of the Income Tax Act.

#### 5.3 Full Regression Run
Execute:
```bash
PYTHONPATH=App/backend python3 -m pytest App/backend/tests tests/agents -q
python3 -m app.freshness --check
```
Verify that all 2,000 multilingual FAQs continue to pass at ≥ 99.85%.

---

## 4. Rollout Strategy & Risk Mitigation

1. **Feature-Flagged Delivery:**
   - Deploy Phase 0 (Regex & Guard fixes) directly to fix live false-rejection bugs.
   - Deploy Phase 1 & 2 behind `FLAG_MODEL_TIERING=true` and `FLAG_GRAPH_FUSION=true`.
2. **Latency & Cost Monitoring:**
   - Ensure Tier 1 handles ≥ 85% of high-volume FAQ traffic (sub-500ms p50 latency).
   - Ensure Tier 3 handles the ≤ 15% of complex legal reasoning cases where high precision and legal depth are required.
3. **Rollback Plan:**
   - Setting `FLAG_MODEL_TIERING=false` instantly reverts to single-model generation.
   - Setting `FLAG_GRAPH_FUSION=false` instantly reverts to standard hybrid vector + BM25 retrieval.

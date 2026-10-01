# Plan: Sovereign Hybrid Architecture ("Master Tax Brain" Qwen-2.5-72B + Sunflower-14B) on `dev-2.0`

**Status:** Implemented & Verified on `dev-2.0`  
**Target Branch:** `dev-2.0` (independent from `dev`, no comparison or merge)  
**Deployment Profile:** 100% On-Premise, Air-Gapped, Sovereign URA Infrastructure  
**Core Objective:** Upgrade the URA Taxpayer Chatbot from brittle, vague, regex-constrained text generation to frontier-grade response quality (clear visual Markdown tables, bulleted criteria, formatted monetary amounts, and seamless multi-turn conversational follow-ups) while strictly guaranteeing zero external data leakage and 100% factual grounding in official URA datasets.

---

## 1. Executive Summary & Core Constraints

### 1.1 The Problem Being Solved
Under the current baseline, the chatbot frequently generates vague, poorly structured answers, struggles with multi-turn follow-ups (e.g., asking *"What if my turnover is 150m instead?"*), encounters glitches when displaying prices or numbers, and suppresses tables or headings.

Our root-cause investigation revealed this is **not a data availability issue**, but an architectural bottleneck:
1. **Zero-Thought Bottleneck**: The local model runs with `/no_think` and `enable_thinking=False`, forcing immediate first-token emission without internal planning or self-verification.
2. **Brittle Heuristic Follow-up Rewriting**: Follow-up questions rely on regex keyword matching (`\b(it|this|that)\b`), which regularly fails on natural conversational queries, causing the retriever to pull irrelevant documents.
3. **Restrictive Prompting**: Rules 6 & 17 in the system prompt explicitly command the model to write only 1–2 plain sentences and ban Markdown tables and headings.
4. **Figure Cross-Check Lock**: The system extracts up to 12 figures and forbids the model from outputting any figure not verbatim in the list, paralyzing calculation or bracket interpolation.
5. **Regex Collisions on Numbers**: The TIN redaction regex (`\b1\d{9}\b`) censors 10-digit currency values (e.g., `1,000,000,000` / 1 Billion UGX), and aggressive text "unsmashing" scripts mangle decimal percentages (e.g., `1.5%`).

### 1.2 Non-Negotiable Sovereign Constraints
* **Government Entity Security**: URA is a government entity subject to the *Uganda Data Protection and Privacy Act (2019)* and national cybersecurity sovereignty. **No taxpayer data, session queries, financial metrics, or internal documents may be transmitted to external cloud APIs** (OpenAI, Anthropic, Google Gemini, Cloudflare, etc.).
* **Strict URA Knowledge Base Grounding**: All statutory facts, procedures, tax rates, forms, and deadlines must originate exclusively from the indexed URA knowledge base (Qdrant dense vectors + BM25 sparse index).
* **Strict Abstention Mandate**: If the URA knowledge base does not contain the required statutory ground truth, the model **must abstain clearly and politely**, referring the taxpayer to official URA channels (`https://ura.go.ug` or toll-free `0800 117 000 / 0800 217 000`) rather than hallucinating or speculating.

---

## 2. Hardware Allocation & GPU Strategy

### 2.1 Current Hardware State (8x NVIDIA RTX A6000, 48 GB VRAM each)

| GPU Index | Device Name | Current Workload | VRAM Used / Total | Status for this Project |
| :--- | :--- | :--- | :--- | :--- |
| **GPU 0** | NVIDIA RTX A6000 | Other Project (VLLM EngineCore) | ~23.8 GB / 48 GB | In Use (DO NOT TOUCH) |
| **GPU 1** | NVIDIA RTX A6000 | Other Project (VLLM EngineCore) | ~26.9 GB / 48 GB | In Use (DO NOT TOUCH) |
| **GPU 2** | NVIDIA RTX A6000 | `ura-app-api` (bge-m3 + mxbai-rerank-v2) | ~11.1 GB / 48 GB | **Active (Project Backend)** |
| **GPU 3** | NVIDIA RTX A6000 | **None (Idle)** | **12 MiB / 48 GB** | **RESERVED FOR MASTER BRAIN (TP Slice 0)** |
| **GPU 4** | NVIDIA RTX A6000 | `ura-app-orpheus-tts` (Orpheus Voice Sidecar) | ~18.1 GB / 48 GB | **Active (Project Voice TTS)** |
| **GPU 5** | NVIDIA RTX A6000 | `ura-app-vllm-sunflower` (Sunflower-14B) | ~25.5 GB / 48 GB | **Active (Project Vernacular Specialist)** |
| **GPU 6** | NVIDIA RTX A6000 | Other Project (Triton Server) | ~341 MiB / 48 GB | In Use (DO NOT TOUCH) |
| **GPU 7** | NVIDIA RTX A6000 | **None (Idle)** | **12 MiB / 48 GB** | **RESERVED FOR MASTER BRAIN (TP Slice 1)** |

### 2.2 MANDATORY EXECUTION STOP-GATE
> ⚠️ **CRITICAL INSTRUCTION FOR THE EXECUTING SESSION**:
> When running the commands to launch the Master Brain, run `nvidia-smi -i 3,7` first.
> **If GPU 3 or GPU 7 is NO LONGER IDLE (i.e. another process is utilizing memory or compute on them), STOP IMMEDIATELY.**
> Do **not** attempt to kill other processes, and do **not** attempt to launch on occupied GPUs. **Stop where you are and prompt the user** to make the decision on how to proceed.

### 2.3 Master Brain Model Selection & Serving Profile
* **Primary Target**: `Qwen/Qwen2.5-72B-Instruct-AWQ` (or `Qwen2.5-72B-Instruct-FP8`).
  * Total weights size: ~43 GB.
  * Serving configuration: vLLM with Tensor Parallelism `tp=2` distributed across **GPU 3 and GPU 7** (approx. 24 GB allocated per GPU, leaving ample headroom for KV cache and 8,192 context window).
  * Port: `8002` (internal Docker network: `ura-app-vllm-master:8002`).
* **Fallback Profile (Emergency Single-GPU Option)**:
  * If for any reason only one idle GPU is available, the fallback configuration is `Qwen/Qwen2.5-32B-Instruct-AWQ` on a single GPU (`GPU 3`), utilizing ~22 GB VRAM.

### 2.4 Parallel Side-by-Side Deployment (Zero Conflict with `dev`)
To allow `dev-2.0` to run, test, and be evaluated simultaneously without touching or restarting the existing `dev` services running on ngrok (`https://struttingly-nongeological-briella.ngrok-free.dev`), `dev-2.0` operates in an isolated stack:
* **Project Namespace**: Run under Docker Compose project `-p dev2` (e.g. `docker compose -p dev2 -f App/docker-compose.dev2.yml up -d`).
* **Non-Conflicting Port Bindings**:
  * **Frontend**: Port **`3034`** (instead of `dev`'s `3032`).
  * **Backend API**: Port **`8084`** (instead of `dev`'s `8083`).
  * **Master Brain vLLM**: Port **`8002`** (runs on GPUs 3 & 7; leaves GPU 5 on port `8011` for `dev`).
* **Independent Public Access Tunnel**:
  * Run a concurrent tunnel for `dev-2.0` on port `3034`:
    * Recommended: `cloudflared tunnel --url http://localhost:3034` (immediate public HTTPS URL, zero interference with active ngrok session).
    * Or a secondary ngrok tunnel profile for port `3034`.

---

## 3. Hybrid Sovereign Architecture: "Master Tax Brain" + "Vernacular Specialist"

To provide both frontier-grade reasoning and native fluency in Ugandan national and indigenous languages, the architecture couples two specialized on-premise engines:

```
                            [ Taxpayer Client ]
                                     │
                             (HTTP / WebSocket)
                                     ▼
                      [ FastAPI Gateway: ura-app-api ] (GPU 2)
                                     │
           ┌─────────────────────────┴─────────────────────────┐
           ▼                                                   ▼
[ Vernacular Specialist ]                           [ Contextual Rewriter ]
 Sunflower-14B (GPU 5)                               Qwen-2.5-72B (GPUs 3 & 7)
 - Auto-detects local language                       - Translates follow-up to
   (Luganda, Runyankole, Acholi, Swahili)              canonical tax search query
 - Provides high-fidelity local translations         - Preserves conversation entities
           │                                                   │
           └─────────────────────────┬─────────────────────────┘
                                     ▼
                    [ Hybrid Retrieval Engine ] (GPU 2)
                     - Dense ANN Search: Qdrant (bge-m3)
                     - Sparse Search: BM25 on URA Law & FAQs
                     - Rank Fusion: RRF + mxbai-rerank-base-v2
                     - Hard Abstention Gate if relevance < threshold
                                     │
                                     ▼
                  [ Master Tax Brain: Qwen-2.5-72B ] (GPUs 3 & 7)
                  - Internal Scratchpad (<thought>): planning & verification
                  - Deterministic Tax Calculators (app/tools/calculators.py)
                  - Rich Markdown Synthesis: Pipe tables, bullets, callouts
                  - Source Grounding & [1],[2] Citation Binding
                                     │
                                     ▼
                           [ Response Pipeline ]
                  - PII Guard (sanitized, non-colliding)
                  - Localizer (renders English or vernacular via Sunflower)
                  - Direct Stream to Taxpayer
```

### 3.1 Responsibilities Split
1. **Master Tax Brain (`Qwen-2.5-72B-Instruct` on GPUs 3 & 7)**:
   * Acts as the primary synthesis engine for all English and statutory queries.
   * Performs internal step-by-step reasoning (scratchpad).
   * Determines information presentation: selects Markdown pipe tables for tax bands, bullet points for requirements, and callouts for deadlines.
   * Interacts with local deterministic tax tools for mathematical calculations.
   * Asserts strict grounding against retrieved URA passages.
2. **Vernacular Specialist (`Sunflower-14B` on GPU 5)**:
   * Retained as the sovereign localization and translation expert for Ugandan indigenous languages.
   * Translates incoming Luganda/Acholi/Runyankole queries into canonical tax terms for retrieval, and renders the Master Brain's grounded English replies back into the taxpayer's dialect when requested.

---

## 4. Implementation Details: Solutions (A) to (E)

### 4.1 Solution (A): Internal Scratchpad & Reasoning Effort
* **Objective**: Allow the model to think before emitting tokens so it can plan visual presentation, verify calculations, and check passage citations.
* **Backend Changes (`App/backend/app/llm.py`)**:
  * Remove the `/no_think` prefix from the prompt template.
  * Enable the reasoning envelope: Instruct the model to use an internal planning block:
    ```markdown
    <thought>
    1. Intent analysis: What is the taxpayer asking, and what context was established in previous turns?
    2. Retrieval verification: Do the retrieved passages contain the exact answer? If not, plan abstention.
    3. Math & tools: Are there amounts or tax rates? Calculate using statutory rules.
    4. Presentation structure: Does this require a table (e.g. tax bands/rates) or bullet points (e.g. procedures)?
    </thought>
    ```
  * In the streaming pipeline (`App/backend/app/service.py` and `App/backend/app/llm.py`), implement a streaming thought filter that captures and logs `<thought>...</thought>` on the server for auditing while yielding only the clean, final answer to the taxpayer's chat interface.

### 4.2 Solution (B): LLM-Based Contextual Query Rewriter
* **Objective**: Replace brittle regex-based coreference resolution with an intelligent 1-shot prompt that converts conversational follow-ups into explicit search queries.
* **Code Modification (`App/backend/app/query.py`)**:
  * Deprecate the fragile regex chains in `rewrite_with_history()`.
  * Add a dedicated, ultra-fast rewriter prompt routed to the local model:
    ```python
    REWRITE_PROMPT = """Given the recent conversation history and a follow-up user query, rewrite the query into an independent, explicit search query about Uganda tax law and procedures.
If the query is already self-contained, return it unchanged.
Do NOT answer the question. Return ONLY the rewritten query text.

Examples:
History:
User: What is presumptive tax in Uganda?
Assistant: Presumptive tax is a simplified tax regime for small businesses with turnover below UGX 150 million...
Follow-up: What if it's 150m?
Rewritten: What is the tax rate and filing requirement in Uganda for business annual turnover of 150 million UGX?

History:
User: How do I register for a TIN?
Assistant: You can apply online via the URA web portal...
Follow-up: What documents do I need for that?
Rewritten: What documents are required for individual TIN registration in Uganda?

History: {history_summary}
Follow-up: {query}
Rewritten:"""
    ```
  * When `FLAG_QUERY_REWRITE` is enabled, this produces high-precision search queries that ensure Qdrant and BM25 retrieve the exact statutory sections.

### 4.3 Solution (C): Eliminate Figure Cross-Check & Wire Deterministic Calculators
* **Objective**: Remove the static 12-figure bottleneck and enable dynamic, accurate calculations.
* **Code Modifications (`App/backend/app/llm.py` and `App/backend/app/service.py`)**:
  * **Remove the Blocking Instruction**: In `extract_statutory_context()`, remove the sentence: *"Do not state a figure that is not in this list, and do not attribute one to a passage it is not listed against."* Change the figure block into a helpful reference list of statutory rates without forbidding interpolation or arithmetic.
  * **Wire Deterministic Calculators**:
    * The repository already contains complete, verified tax engines in `App/backend/app/tools/calculators.py` (`calculate_paye`, `calculate_presumptive_tax`, `calculate_vat`, `calculate_withholding_tax`, `calculate_rental_tax`).
    * In `service.py`, ensure that when a query contains income, salary, or turnover figures, the router evaluates and executes the pure calculator first, passing the exact calculation results into the prompt context for the Master Brain to format into a clean tabular breakdown.

### 4.4 Solution (D): Prompt Revamp for Modern Markdown Presentation
* **Objective**: Instruct the model to formulate answers with professional structure (tables, bullet points, bold key terms) inspired by frontier models.
* **System Prompt Overhaul (`App/backend/app/llm.py`)**:
  * **Delete Conflicting Rules**: Remove Rule 6 and Rule 17 (which restrict factual answers to 1–2 plain sentences and prohibit tables/lists).
  * **Add Modern Presentation Guidelines**:
    ```markdown
    ## Presentation & Formatting Standards
    1. Lead with the Bottom Line: Always state the direct answer or payable amount in the very first sentence.
    2. Markdown Tables for Comparisons: Whenever presenting tax bands, rate thresholds, payment schedules, or comparisons between resident and non-resident rates, ALWAYS format them as a clean Markdown pipe table:
       | Category / Threshold | Tax Rate / Amount | Statutory Reference |
       | :--- | :--- | :--- |
    3. Structured Bullet Points: Use bold title prefixes for checklists, requirements, and conditions (e.g., "- **Valid National ID**: ...", "- **Turnover Limit**: ...").
    4. Formatted Currency: Quote all monetary values with proper comma separation and currency symbols (e.g. `UGX 150,000,000`, never raw unformatted digits like `1500000000`).
    5. Clean Citations: Keep [1], [2] citation markers placed directly next to the factual statement or table row they support.
    ```

### 4.5 Solution (E): Fix PII & Post-Processing Regex Conflicts
* **Objective**: Prevent the redaction of legitimate currency numbers and stop the corruption of decimal values or lists.
* **Backend Fixes (`App/backend/app/guardrails.py`)**:
  * Fix the Uganda TIN regex:
    ```python
    # Before: r"\b1\d{9}\b" (matches any 10-digit number starting with 1, e.g. 1000000000 UGX)
    # After: Require strict TIN context or non-currency boundary
    ("ug_tin", re.compile(r"(?i)(?:\bTIN\s*[:#-]?\s*|\bTax Identification Number\s*[:#-]?\s*)\b(1\d{9})\b|\b1\d{2}[-\s]\d{3}[-\s]\d{4}\b")),
    ```
  * Fix list unsmashing regexes in `OutputGuard.normalize_structure`: Ensure that decimal numbers like `1.5%` or statutory sections like `Section 5.1` are not split into numbered list items.
* **Frontend Fixes (`App/frontend/src/store/useChatStore.ts`)**:
  * In `normalizeAssistantResponse()`, refine the regexes that separate sentences before numbers to ensure they do not break currency formats (`UGX 500,000. 10% penalty`) or decimal values.

---

## 5. Dead Code Removal & Simplification Plan

To keep `dev-2.0` clean, maintainable, and free of unnecessary technical debt, the following legacy and dead code components will be removed or refactored:

1. **Obsolete Regex Rewriting Heuristics**:
   * Prune the fragile, multi-layered regex rules in `App/backend/app/query.py` (lines 758–860) once the LLM contextual rewriter is active.
2. **Redundant Thinking Filter Duplication**:
   * Consolidate the multiple conflicting implementations of `_strip_reasoning_preamble`, `looksLikeThinking`, and `THINKING_SIGNALS` across `guardrails.py`, `service.py`, and `useChatStore.ts` into a single, standardized server-side stream filter.
3. **Dead Cloud-Fallback Scaffolding**:
   * Clean up lingering references to deprecated cloud fallbacks (`gemini_live.py`, `gemini_stall.py` remnants, and unconfigured CF gateway chains) that violate sovereign on-premise policies.
4. **Duplicate List & Bullet Cleaners**:
   * Synchronize `OutputGuard.normalize_structure` with frontend `normalizeAssistantResponse` so that the frontend simply renders clean standard Markdown (via `react-markdown`) rather than running destructive regex mutations over incoming text.

---

## 6. Step-by-Step Implementation Roadmap for Next Session

When starting the implementation in a new session on branch `dev-2.0`, follow these steps sequentially:

### Phase 1: Environment & GPU Check
1. Run `nvidia-smi -i 3,7`.
2. **Verify that GPU 3 and GPU 7 have 0% compute utilization and < 50 MiB VRAM.**
   * *If occupied*: STOP and ask the user for directions.
   * *If idle*: Proceed to launch the vLLM container for Qwen-2.5-72B-Instruct.
3. Create compose overlay or service configuration `App/docker-compose.gpu-qwen72b.yml` defining the `ura-app-vllm-master` container mapped to `CUDA_VISIBLE_DEVICES=3,7`.

### Phase 2: Core Guardrail & Regex Sanitation (`guardrails.py` + `useChatStore.ts`)
1. Edit `App/backend/app/guardrails.py`:
   - Update `_PII_PATTERNS` so `ug_tin` requires TIN context and does not capture 10-digit currency numbers (`1000000000`).
   - Fix decimal and currency protections in `normalize_structure`.
2. Edit `App/frontend/src/store/useChatStore.ts`:
   - Prevent regex corruption of decimal figures (`1.5%`) and monetary numbers.

### Phase 3: Prompt & Presentation Revamp (`llm.py`)
1. Edit `SYSTEM_PROMPT` in `App/backend/app/llm.py`:
   - Remove `/no_think`.
   - Remove Rules 6 & 17 (conciseness bans on tables/headings).
   - Add Markdown table and structured presentation guidelines.
2. Edit `extract_statutory_context()` in `llm.py`:
   - Remove the restriction forbidding output of figures not on the static list.
3. Update `_build_messages()` to support the internal scratchpad `<thought>` tag.

### Phase 4: Conversational Rewriter & Calculator Wiring (`query.py`, `service.py`)
1. In `App/backend/app/query.py`:
   - Implement the fast LLM-based query rewriter for multi-turn history resolution.
2. In `App/backend/app/service.py`:
   - Wire incoming numerical scenarios to `App/backend/app/tools/calculators.py` so mathematical outputs are fed into the prompt context for tabular presentation.

### Phase 5: Dead Code Removal & Codebase Cleanup
1. Prune deprecated regex rules in `query.py`.
2. Remove redundant thinking-signal regexes from `useChatStore.ts`.
3. Simplify stream handlers in `service.py` to yield clean tokens.

### Phase 6: Verification & Testing
1. Run backend unit tests:
   ```bash
   PYTHONPATH=App/backend python3 -m pytest App/backend/tests tests/agents -q
   ```
2. Run frontend unit tests:
   ```bash
   cd App/frontend && bun run test
   ```
3. Test end-to-end multi-turn scenarios:
   - **Test 1 (Table Presentation)**: *"What are the current PAYE tax bands and rates for residents?"* (Verify output contains a Markdown table with correct rates).
   - **Test 2 (Multi-turn Follow-up)**: Turn 1: *"What is presumptive tax?"* -> Turn 2: *"What if my turnover is 150m instead?"* (Verify Turn 2 retrieves the correct turnover tier and answers directly).
   - **Test 3 (Large Number Formatting)**: *"What is the threshold for mandatory VAT registration?"* (Verify output prints `UGX 150,000,000` with commas and without `[REDACTED_UG_TIN]`).
   - **Test 4 (Strict Abstention)**: Ask an unrelated question not in URA laws. (Verify polite refusal directing to `https://ura.go.ug` with zero hallucinations).

---

## 7. Plan Sign-off Checklist
- [x] Branch `dev-2.0` created and verified up-to-date with `origin/dev`.
- [x] Idle GPUs identified (GPU 3 & GPU 7) with explicit stop-gate instructions.
- [x] Sovereign on-premise requirement preserved (zero external API data exposure).
- [x] Strict URA knowledge base grounding and abstention rules enforced.
- [x] Detailed solutions (A through E) specified with exact code areas.
- [x] Dead code cleanup section included to ensure `dev-2.0` remains clean.
- [x] Execution roadmap structured for immediate, unambiguous implementation in a fresh session.

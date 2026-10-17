# Local Reasoning-Tier Model Bake-Off Report (October 2026)

- **Date:** 2026-10-18
- **Phase:** Phase B of `docs/plans/sovereign-opus-level-accuracy-plan.md`
- **Target Hardware:** 1× NVIDIA RTX A6000 (48 GB VRAM, GPU 7 isolated; live stack remained untouched on GPUs 2, 3, and 4)
- **Serving Engine:** `vllm/vllm-openai:v0.17.1` (`HF_HUB_OFFLINE=1`, `VLLM_NO_USAGE_STATS=1`, `DO_NOT_TRACK=1`)

---

## 1. Contenders & Checkpoints Evaluated

| Contender | Hugging Face Repo | Commit SHA | Checkpoint Size & Format | Licence | Serving Notes |
| --- | --- | --- | --- | --- | --- |
| **A (Baseline T1)** | `Sunbird/Sunflower-Qwen3.5-9B` | `live-gpu4` | 9B BF16 served with `--quantization fp8` | Apache-2.0 | Reused live `ura-app-vllm-sunflower` read-only on GPU 4 (`127.0.0.1:8011/v1`). |
| **B (Preferred T3)** | `Qwen/Qwen3.8-27B-FP8` | `017b9c7af6b5689d5dd426a76e0bc077eb5ca20a` | 28.77 GiB (81 files, pre-quantised FP8 E4M3) | Apache-2.0 | Served out-of-the-box by stock `vllm/vllm-openai:v0.17.1` on GPU 7 (`12.32 GiB` free KV cache = `100,352` tokens, `5.35×` concurrency at `65,536` max context). |
| **C (Ugandan 27B)** | `Sunbird/Sunflower-Qwen3.8-27B` | `42774a09a1893c5c83c5cb1cb521386eaa104fcd` | 50.97 GiB (11 files, BF16, gated) | Apache-2.0 | Unquantised 51 GiB BF16 exceeds 48 GB VRAM; stock `--quantization fp8` fails on Ampere in `gptq_marlin_repack` (`size_n = 96` on `linear_attn.in_proj_ba` not divisible by `tile_n_size = 64`). Evaluated on GPU 7 by mounting a 1-line `fp8.py` skip for `in_proj_ba`/`mlp.gate` (`12.74 GiB` KV cache = `103,488` tokens, `5.54×` concurrency at `65,536` context). |

### Exclusions (per §4.B.1)

- **Commercial / Hosted APIs (Gemini, OpenAI, Anthropic, Cloudflare Workers AI, Groq, OpenRouter):** Strictly forbidden by URA government data sovereignty (§0.2). No taxpayer data or prompts may leave local hardware.
- **`DeepSeek-V4.x` (763B) & `Qwen3.8-Flash-Next` (180B, non-Apache licence):** Exceed available VRAM across the entire server.
- **`Qwen/Qwen3.5-122B-A10B` & `openai/gpt-oss-120b`:** Require tensor parallelism across **both** free GPUs on a shared multi-project machine; reserved only as fallback if single-GPU 27B models failed the ≥ +10-point accuracy bar (which B and C both passed).
- **`Gemma-4` & `Llama-3.3`:** Non-Apache gated licences with no expected advantage over Qwen3.8 on structured tool calling or Ugandan tax reasoning.

---

## 2. Experimental Setup

1. **Frozen Contexts (`scripts/bakeoff_freeze_contexts.py`):**
   - Captured inside `ura-app-api` across all **30 dev scenarios (40 turns)** in `evals/complex_scenarios/dev.jsonl`.
   - Every contender received the exact same `messages` payload (`retriever.search_planned` passages + `service._evaluate_calculation_context` ground truth + `llm._build_messages` system prompt).
   - Scored deterministically via `evals/complex_scenarios/scoring.py` with `route_ok` and `sources_relevant` reweighted out (testing the generator alone).
2. **Tool-Calling Fidelity (`15` calculator prompts):**
   - Exposed the 9 live `ToolRegistry` schemas from the `tax_calculator` namespace (`calculate_paye`, `calculate_vat`, `check_vat_registration`, `calculate_corporation_tax`, `calculate_capital_gains`, `calculate_rental_tax`, `calculate_withholding`, `calculate_customs_duty`, `calculate_excise_duty`).
   - Note on tool-call parser: Qwen3.5 and Qwen3.8 emit `<tool_call><function=NAME><parameter=KEY>VAL</parameter></function></tool_call>`, which corresponds to `--tool-call-parser qwen3_coder` in `vllm/vllm-openai:v0.17.1` (not `hermes`).
3. **Concurrent Throughput (`8` streams):**
   - 8 simultaneous streaming requests generating a ~1,024-token multi-head Ugandan tax guide.
4. **Luganda / Swahili Sanity (`12` prompts):**
   - 6 Luganda and 6 Swahili figure-dense tax statements checked with `app.mt.figures_survived`.
5. **Continuation Flag Check (§7.E.2):**
   - Confirmed `vllm/vllm-openai:v0.17.1` natively supports `continue_final_message=True, add_generation_prompt=False` on `/v1/chat/completions`.

---

## 3. Results Matrix

| Contender & Mode | Mean Dev Scenario Score (`/100`) | Facts Recall (`%`) | Premise Challenge (`%`) | Figure Accuracy (`%`) | Critical Errors | Truncated Turns (`%`) | Tool-Call Fidelity (`15` prompts) | Dev TTFT p50 / p95 (`s`) | Dev Total Latency p50 / p95 (`s`) | 8-Stream Throughput (`tok/s/stream` / agg) | lg/sw `figures_survived` (`12` prompts) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **A (`Sunflower-9B`, `nothink`)** | 61.25 | 77.31% | 64.29% | 34.62% | 0 | 0 (0.0%) | 93.33% (14/15) | 0.24 / 0.40 | 2.17 / 5.10 | 48.76 / 389.91 | 75.00% (9/12) |
| **A (`Sunflower-9B`, `think`)** | 60.25 | 92.59% | 78.57% | 15.38% | 0 | 7 (17.5%) | 53.33% (8/15) | 0.23 / 0.34 | 16.39 / 73.56 | — | — |
| **B (`Qwen3.8-27B-FP8`, `nothink`)** | **73.23** | **93.98%** | **85.71%** | 34.62% | **0** | 1 (2.5%) | **100.00% (15/15)** | 1.59 / 2.72 | 22.15 / 45.98 | 19.09 / 152.73 | **91.67% (11/12)** |
| **B (`Qwen3.8-27B-FP8`, `think`)** | 57.04 | 69.91% | 42.86% | 30.77% | 0 | 11 (27.5%) | 100.00% (15/15) | 1.74 / 4.81 | 166.92 / 201.94 | — | — |
| **C (`Sunflower-27B`, `nothink`)** | 71.60 | 87.04% | 78.57% | **38.46%** | **0** | **0 (0.0%)** | **100.00% (15/15)** | 1.80 / 4.39 | 13.05 / 28.76 | 19.05 / 152.36 | 83.33% (10/12) |
| **C (`Sunflower-27B`, `think`)** | 65.99 | 85.65% | 71.43% | 26.92% | 0 | 2 (5.0%) | 100.00% (15/15) | 1.73 / 4.80 | 74.05 / 178.48 | — | — |

---

## 4. Application of the Decision Rule (§4.B.5)

1. **≥ +10-point improvement over Contender A (`61.25`):**
   - **PASS.** Both `B-nothink` (**73.23**, **+11.98 points**) and `C-nothink` (**71.60**, **+10.35 points**) beat the 9B baseline by more than 10 points on the identical frozen contexts, even before Phase F's scenario planner and Phase G's retrieval fixes are wired in.
2. **Critical errors ≤ A × 0.5 and Tool-calling fidelity ≥ 90%:**
   - **PASS.** Both `B-nothink` and `C-nothink` achieved **0 critical errors** and **100.0% (15/15) tool-calling fidelity**.
3. **Thinking mode (`think` vs `nothink`):**
   - Rule: *"Thinking mode is enabled for T3 only if it adds ≥ 5 points over nothink of the same model and its p95 ≤ 60 s. Otherwise T3 runs nothink."*
   - Finding: On multi-part legal questions, `enable_thinking=True` spends 2,500–4,000 tokens in `<think>...</think>`, increasing p95 latency to `178–202 s` and causing budget truncation (`27.5%` on B-think, `5.0%` on C-think).
   - **Decision:** **Run T3 with `enable_thinking=False` (`nothink`).**
4. **B (`Qwen/Qwen3.8-27B-FP8`) vs C (`Sunbird/Sunflower-Qwen3.8-27B`):**
   - `B-nothink` leads `C-nothink` on overall score (`73.23` vs `71.60`), facts recall (`93.98%` vs `87.04%`), premise-challenge rate (`85.71%` vs `78.57%`), and figure survival (`91.67%` vs `83.33%`).
   - Crucially, `B` (`Qwen/Qwen3.8-27B-FP8`) is a pre-quantised 28.77 GiB FP8 checkpoint whose `config.json` already lists the GatedDeltaNet `in_proj_ba` (`size_n = 96`) layers under `modules_to_not_convert`, so it loads **cleanly on stock `vllm/vllm-openai:v0.17.1`** with zero image patches.
   - `C` (`Sunbird/Sunflower-Qwen3.8-27B`) is within 1.63 points of `B` on English and faster on average output length (`p95 = 28.76 s`), making it a strong candidate for future single-model consolidation once exported as an offline FP8 checkpoint with `modules_to_not_convert` populated.
   - **Decision:** Select **`Qwen/Qwen3.8-27B-FP8` (`enable_thinking=False`)** as the T3 reasoning model on GPU 7, and record single-model consolidation with `Sunbird/Sunflower-Qwen3.8-27B` in the gap register (`docs/GAPS_AND_AGENTIC_ROADMAP.md`).

---

## 5. Cleanup Verification (§4.B.6)

Both experiment containers (`bakeoff-vllm-b` and `bakeoff-vllm-c`) were removed with `docker rm -f`, and `nvidia-smi` confirmed GPU 7 returned to `14 MiB / 49140 MiB` (idle).

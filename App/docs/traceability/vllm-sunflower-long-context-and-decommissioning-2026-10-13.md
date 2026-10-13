# Traceability Record — vLLM Sunflower-14B-FP8 Long-Context Enhancement (131K YaRN) and Non-Sunflower vLLM Decommissioning (2026-10-13)

> **Date:** 2026-10-13  
> **Scope:** Model Inference Architecture, vLLM Services, RoPE Scaling (YaRN), Docker Compose Overlays, Offline RAG (FAISS) Verification, Image Cleanup  
> **Status:** Shipped and Verified

---

## 1. Executive Summary & Purpose

This release streamlines the LLM serving architecture across the repository by:
1. **Decommissioning Obsolete vLLM Flows**: Removed legacy and duplicate vLLM services that did not serve `Sunbird/Sunflower-14B-FP8` (specifically `vllm-quantized` and `App/docker-compose.local-qwen.yml`), while strictly preserving `ura-app-vllm-master` (`Qwen2.5-72B-Instruct-AWQ` on `dev2` stack).
2. **Enhancing Sunflower-14B-FP8 Context Window (32K → 131K via YaRN)**: Enhanced `App/Model/Sunflower-14B-FP8` model configuration and vLLM runtime settings to utilize modern YaRN (Yet another RoPE extensioN) RoPE scaling, extending the effective context length from native 32K/40K up to **131,072 tokens (131K / 128K)**.
3. **Applying Latest vLLM Production Standards**: Configured chunked prefill (`--enable-chunked-prefill`), automatic prefix caching (`--enable-prefix-caching`), batched token headroom (`--max-num-batched-tokens 16384`), and Hermes tool-call parsing (`--tool-call-parser hermes`) in Docker compose manifests.
4. **Backend Token Budget Alignment**: Updated `LLM_CONTEXT_WINDOW` in `App/backend/app/llm.py` and `.env.example` to default to `131072`, allowing `_fit_to_context` to dynamically allocate up to the full 131K window served by vLLM.
5. **Verifying FAISS Offline Retrieval**: Confirmed the production offline RAG pipeline (`offline_rag.py`, `offline_bundle.py`, `scripts/export_offline_bundle.py`) remains intact for on-device and air-gapped deployments.
6. **Docker Resource & Image Deduplication**: Removed stale duplicate image layers and tags (`app-api:pre-*`, `app-frontend:pre-*`, `ura-chatbot-*`), reclaiming >40 GB of host disk space.

---

## 2. Structural & Architectural Changes

### 2.1 YaRN RoPE Scaling for Sunflower-14B-FP8 (`config.json`)
* **Background**: The Sunflower-14B-FP8 base causal language model (`Qwen3ForCausalLM`) was originally initialized with `max_position_embeddings: 40960` and null `rope_scaling`, despite its tokenizer supporting up to 131,072 tokens.
* **Enhancement**: Configured standard YaRN RoPE scaling in `App/Model/Sunflower-14B-FP8/config.json`:
  ```json
  "max_position_embeddings": 131072,
  "rope_scaling": {
    "factor": 4.0,
    "original_max_position_embeddings": 32768,
    "rope_type": "yarn",
    "type": "yarn"
  },
  "rope_theta": 1000000
  ```
  - `original_max_position_embeddings`: 32,768 (native context window).
  - `factor`: 4.0 (32,768 × 4.0 = 131,072 tokens).
  - Compatible with both `transformers` (which resolves `rope_type` or `type`) and vLLM (which calculates `derived_max_model_len = original_max_position_embeddings * factor`).

### 2.2 vLLM Compose Enhancement (`local-sunflower.yml` & `docker-compose.yml`)
* Updated `vllm` service in `App/docker-compose.local-sunflower.yml` and root `docker-compose.yml`:
  - Image pinned to modern `vllm/vllm-openai:v0.8.5`.
  - Added `--max-model-len 131072` (or `${SUNFLOWER_MAX_MODEL_LEN:-131072}`).
  - Added `--enable-prefix-caching` for sub-second TTFT on multi-turn conversations and static RAG context.
  - Added `--enable-chunked-prefill` and `--max-num-batched-tokens 16384` to prevent OOM spikes during long prompt prefill.
  - Set `--gpu-memory-utilization 0.85` for KV cache headroom under large context windows.
  - Retained `--enable-auto-tool-choice --tool-call-parser hermes` for function calling.
  - Exposed `LLM_CONTEXT_WINDOW=${LLM_CONTEXT_WINDOW:-131072}` in the `api` service environment.

### 2.3 Decommissioning Non-Sunflower vLLM Overlays
* **Removed**:
  - `vllm-quantized` service removed from `docker-compose.yml`.
  - `vllm-quantized` scrape target removed from `monitoring/prometheus.yml`.
  - `App/docker-compose.local-qwen.yml` completely deleted.
  - `vllm` override block removed from `App/docker-compose.coverage-gpu.yml`.
* **Retained**:
  - `ura-app-vllm-master` (`Qwen2.5-72B-Instruct-AWQ` on port 8002 in `App/docker-compose.dev2.yml`) preserved as the dedicated master brain service.
  - `vllm` (`ura-vllm` / `ura-app-vllm-sunflower`) running `Sunbird/Sunflower-14B-FP8`.

### 2.4 FAISS Offline Retrieval Verification
* Confirmed `App/backend/app/offline_rag.py` and `App/backend/app/offline_bundle.py` remain fully active:
  - FAISS `IndexFlatIP` on-device vector search.
  - ONNX `bge-m3` embedder fallback.
  - Offline sync endpoints (`/v1/offline/status`, `/v1/offline/bundle`, `/v1/offline/sync`).

### 2.5 Inference Memory & Time Controls Verification
* **Token Budget & Bounds (`max_new_tokens`)**:
  - `max_new_tokens=LLM_MAX_TOKENS` (512 tokens default) enforced across local and vLLM inference paths, preventing unbounded generation loops.
  - Bounded prompt and output budgets calculated via `_fit_to_context` (`App/backend/app/llm.py`).
* **KV-Cache Retention (`use_cache=True`)**:
  - Enabled `"use_cache": true` in `App/Model/Sunflower-14B-FP8/config.json`.
  - Passed `use_cache=True` explicitly in `_model.generate(...)` calls across `_local_generate`, `_local_translate`, `_local_generate_with_tools`, and `classify_choice`.
  - Enabled prefix caching (`--enable-prefix-caching`) on vLLM serving containers.
* **Post-Generation Memory Reclamation (`torch.cuda.empty_cache()`)**:
  - Wired in `finally:` blocks after `_model.generate(...)` execution in `_local_generate`, `_local_translate`, `_local_generate_with_tools`, and `classify_choice`, immediately returning unused reserved memory to CUDA allocator.
* **Inference Timeouts & Deadlines**:
  - HTTP client timeout bounded by `VLLM_HTTP_TIMEOUT` (60s default, overridable).
  - Streaming generation bounded by turn deadlines (`_VOICE_LLM_DEADLINE_S = 3.5s` on voice turns; circuit breakers trip on consecutive timeouts).

### 2.6 Long-Horizon Agent State & Context Compaction Upgrades
* **Context Compaction Middleware (`compactor.py`)**:
  - `compact_tool_observation()`: Prunes observation arrays to top-3 and nested dicts to top-5, while preserving statutory fields (`payable`, `tax_type`, `effective_rate`, `status`) and errors, bounding payload footprint to $\le 512$ tokens.
  - `compact_messages()` & `node_context_compactor`: Compresses historical dialogue beyond the verbatim window ($N \le 4$ turns) into structured AST summary strings (`[Earlier: ...]`).
* **State Schema v2 (`state.py`)**:
  - Added `GoalSpec` (`goal_id`, `description`, `status`, `sub_tasks`) for multi-step goal tracking.
  - Added `TaxpayerActionRequest` (`action_type`, `description`, `payload`, `requires_consent`, `authorized`) for HITL financial actions.
  - Added schema versioning (`schema_version=2`) with backwards-compatible deserialization for legacy v1 checkpoints.
* **Human-in-the-Loop & Durable Resumption (`runtime.py` & `main_graph.py`)**:
  - `node_human_gate`: Interrupts execution with `outcome=GraphOutcome.WAITING_USER` when statutory actions require consent.
  - `resume()` and `aresume()`: Loads saved checkpoints, updates authorization payload, and resumes execution seamlessly from the interrupted continuation node.

---

## 3. Verification & Test Results

| Test Suite | Result | Notes |
| :--- | :--- | :--- |
| `App/backend/tests/test_prompt_budget.py` | **7/7 Passed** | Validates `_fit_to_context` dynamic window budgeting |
| `App/backend/tests/test_tool_budget.py` | **10/10 Passed** | Validates schema budgeting and fitting against model window |
| `tests/agents/test_graph.py` | **34/34 Passed** | Main agent graph routing, synthesis, and reflection |
| `tests/agents/test_graph_checkpointer.py` | **4/4 Passed** | Durable checkpointer integration and round-trip |
| `tests/agents/test_long_horizon_graph.py` | **15/15 Passed** | Compactor, HITL human gate, goal tracking, and resumption |
| `tests/test_monitoring_config.py` | **106/106 Passed** | Validates Prometheus jobs and alerting queries post-cleanup |
| `tests/test_all_endpoints_e2e.py` | **27/27 Passed** | Full API contract validation |
| `App/backend/tests/test_api_endpoints.py` | **66/66 Passed** | All core and offline endpoints pass |
| `tests/agents` | **1104/1104 Passed** | Agentic loop, connectors, tools, and supervisor regression-free |
| Transformers YaRN Parameter Check | **Passed** | `inv_freq` shape `[64]`, `attention_scaling: 1.1386`, `model_max_length: 131072` |

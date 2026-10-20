# Runbook: Sovereign Reasoning Tier (`vllm-reasoner`) & Complex Tax Scenario Evaluation

**Related ADR:** [ADR-0007](../adr/0007-sovereign-local-reasoning-tier-and-code-owned-tax-logic.md)  
**Compose Overlay:** `App/docker-compose.reasoner.yml`  
**Evaluation Harness:** `evals/complex_scenarios/` & `scripts/eval_complex_scenarios.py`

---

## 1. Architecture Overview

The URA Taxpayer Assistant operates a sovereign two-tier local vLLM serving topology:

| Tier | Service / Container | Model | Default GPU | Port | Role |
|---|---|---|---:|---|---|
| **T1** (`fast`) | `vllm` (`ura-app-vllm-sunflower`) | `Sunbird/Sunflower-Qwen3.5-9B` | GPU 4 | `0.0.0.0:8011->8001` | Fast single-clause RAG, greetings, and all 5 Ugandan vernacular locales (`en`, `lg`, `sw`, `nyn`, `ach`) + local MT |
| **T2 / T3** (`agentic` / `deep`) | `vllm-reasoner` (`ura-app-vllm-reasoner`) | `Qwen/Qwen3.8-27B-FP8` (`enable_thinking=False`) | GPU 7 | `127.0.0.1:8012->8001` | Multi-hop statutory reasoning, multi-tool tax scenarios, premise challenges, and dispute/objection queries |

All inference runs offline (`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `VLLM_NO_USAGE_STATS=1`, `DO_NOT_TRACK=1`) with `SOVEREIGN_MODE=true`.

---

## 2. Pre-Flight GPU Check

Before starting or restarting `vllm-reasoner`, verify that the target GPU (default **GPU 7**) is free (`< 100 MiB` used):

```bash
nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu --format=csv
```

`Qwen/Qwen3.8-27B-FP8` with `--max-model-len 65536`, `--kv-cache-dtype fp8`, and `--gpu-memory-utilization 0.90` requires **~42.3–45.1 GiB VRAM** on an NVIDIA RTX A6000 (`49,140 MiB`).

---

## 3. Bring-Up, Health Check & Bring-Down of `vllm-reasoner`

### 3.1 Start `vllm-reasoner` Alongside the Live Stack

```bash
cd App
docker compose \
  -f docker-compose.yml \
  -f docker-compose.local-retrieval.yml \
  -f docker-compose.local-sunflower.yml \
  -f docker-compose.gpu-salt.yml \
  -f docker-compose.connectors.yml \
  -f docker-compose.reasoner.yml \
  up -d vllm-reasoner
```

### 3.2 Health & Warmup Checks

Wait for the container health check (`GET /health` + `GET /v1/models`) to report `healthy` (~90–150 seconds for weight loading and CUDA graph capture):

```bash
docker inspect ura-app-vllm-reasoner --format '{{.State.Health.Status}}'
curl -sf http://127.0.0.1:8012/v1/models | python3 -m json.tool
```

Send a 1-token warmup request so vLLM compiles FlashInfer/Triton JIT kernels before serving user or benchmark traffic:

```bash
curl -sf http://127.0.0.1:8012/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen/Qwen3.8-27B-FP8","messages":[{"role":"user","content":"Hi"}],"max_tokens":4,"chat_template_kwargs":{"enable_thinking":false}}'
```

### 3.3 Rollback / Disable T3 Tiering Without Downtime

To fall back immediately to single-model T1 (`Sunbird/Sunflower-Qwen3.5-9B`) without restarting `ura-app-api`:

1. **Via runtime feature flag:** Set `FLAG_MODEL_TIERING=false` (or patch `/v1/admin/flags/model_tiering`).
2. **Via environment variable:** Unset `REASONER_BASE_URL` (when `REASONER_BASE_URL` is empty, `ENDPOINTS[ModelTier.T2]` and `ENDPOINTS[ModelTier.T3]` automatically resolve to the T1 endpoint).
3. **If `vllm-reasoner` is unreachable or exceeds `REASONER_DEADLINE_S`:** `ChatModel._generate_en` automatically catches the timeout/error, increments `reasoner_fallback_total`, and falls back to local T1 with the deterministic `## Verified Statutory Facts & Calculations` block intact.

To stop `vllm-reasoner` and free GPU 7:

```bash
docker stop ura-app-vllm-reasoner
nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv
```

---

## 4. Running the Complex Tax Scenario Benchmark

### 4.1 Held-Out Integrity Rule (`HELDOUT.sha256`)

- **`evals/complex_scenarios/dev.jsonl` (30 scenarios, 40 turns):** Used for iterative development, prompt tuning, and regression debugging. Run as often as needed.
- **`evals/complex_scenarios/heldout.jsonl` (75 scenarios, 102 turns):** Protected by `evals/complex_scenarios/HELDOUT.sha256` (`4e99390835e17872299593d43697cd99f085730cbb78e5b5b7e16dba478cf4b3`).
  - May be executed **at most twice** per held-out version: once at baseline (`--label baseline`) and once at final evaluation (`--label final --baseline-report <path>`).
  - Never inspect individual held-out prompts to tune regexes, prompts, or rules. If a held-out bar is missed, diagnose on `dev.jsonl` and author a fresh `heldout_v2.jsonl` for the next evaluation cycle.

### 4.2 Running `dev` and `heldout` Evaluations

```bash
# 1. Schema and deterministic scorer unit tests (offline, < 2s)
PYTHONPATH=App/backend:. pytest \
  tests/agents/test_complex_scenarios_schema.py \
  tests/agents/test_complex_scenarios_scoring.py -q

# 2. Live dev split evaluation (30 scenarios)
PYTHONPATH=App/backend:. python3 scripts/eval_complex_scenarios.py \
  --base-url http://127.0.0.1:8083 \
  --split dev \
  --label dev-check \
  --concurrency 1

# 3. Held-out split evaluation (requires --baseline-report when label != baseline)
PYTHONPATH=App/backend:. python3 scripts/eval_complex_scenarios.py \
  --base-url http://127.0.0.1:8083 \
  --split heldout \
  --label final \
  --baseline-report evals/reports/complex_heldout_baseline_2026-10-18.json \
  --concurrency 1
```

---

## 5. Running a Reasoner Model Bake-Off

Before swapping `REASONER_MODEL` to a new open-weight checkpoint:

1. **Freeze dev contexts** from the running API container so all candidate models see identical retrieved passages and `verified_facts`:
   ```bash
   docker exec -i ura-app-api /opt/venv/bin/python - < scripts/bakeoff_freeze_contexts.py
   ```
2. **Run `scripts/bakeoff_reasoner.py`** against the candidate vLLM endpoint on a free GPU:
   ```bash
   PYTHONPATH=App/backend:. python3 scripts/bakeoff_reasoner.py \
     --base-url http://127.0.0.1:8021/v1 \
     --model <HuggingFace-Model-ID> \
     --modes nothink think \
     --out evals/reports/bakeoff_<model_slug>.json
   ```
3. Compare composite score, facts recall, premise-challenge rate, critical errors, 15-case tool-calling accuracy, and p50/p95 latency against `docs/Reports/reasoner-bakeoff-2026-10.md`.

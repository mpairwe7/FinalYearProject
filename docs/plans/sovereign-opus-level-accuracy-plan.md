# Plan: Sovereign "Opus-level" Answer Accuracy — local reasoning tier, code-owned tax logic, truncation fix, held-out evaluation

Status: **Ready for implementation.** Authored 2026-10-18 against `dev` @ `f416521416`.
Audience: a **new** coding session (any model) with no memory of the conversation that produced this plan. Everything you need is in this file; where it says "read X first", do that before editing X.

> **Supersedes** the model-tier parts of `docs/plans/frontier-tax-reasoning-and-router-fix-plan.md` (its Phase 2 sends hard questions to cloud Gemini / Cloudflare — **forbidden** here, see §0.2) and its Phase 1.1 token numbers. Several facts in that older plan are **wrong** (see §11.3). Keep its Phase 0 guard fixes that already landed (e.g. `_LEGAL_INQUIRY_RE` in `calculator_router.py`) — do not revert them.

---

## Table of contents

0. Ground rules (read fully before touching anything)
1. Why this plan exists — the evidence
2. Target architecture
3. Phase A — Branch, baseline, and the **held-out complex-scenario set** (written BEFORE any fix)
4. Phase B — Model bake-off **before** switching (Qwen3.8-27B-FP8 vs alternatives)
5. Phase C — Serve the reasoning model + sovereign-mode guard
6. Phase D — Multi-endpoint LLM client and real tier routing
7. Phase E — Truncation fix (root cause, not heuristics)
8. Phase F — Code-owned tax reasoning (works for *any* complex question, not just the one we found)
9. Phase G — Remaining "why it falls short" fixes (faithfulness polarity, figure lock, retrieval, prompt)
10. Phase H — Final evaluation on the held-out set + regression gates + report
11. Phase I — Docs, governance, delivery, cleanup
12. Appendices — legal facts (quoted from the corpus), wrong facts never to reproduce, scenario schema, command cheat-sheet

---

## 0. Ground rules

### 0.1 Repository rules you must obey (from `AGENTS.md`, `App/backend/AGENTS.md`, `evals/AGENTS.md`, `docs/AGENTS.md`)

- Runtime code lives in `App/backend/app` (imported as `app`). **Never move `App/backend` or `App/frontend`.**
- Every change goes on a feature branch → PR against `dev` → CI green → address **every CodeRabbit comment** (threads *and* review-body nitpicks) → merge. Never push to `dev`/`main` directly.
- Commits: conventional commits, author `mpairwe7`, **no `Co-Authored-By` trailers** (also pass an explicit `commit_message` when squash-merging through the API, otherwise GitHub adds one).
- Use the real GitHub CLI at `~/bin/gh` (bare `gh` is a stub). Check `~/bin/gh auth status`; switch with `~/bin/gh auth switch --user mpairwe7` only if needed.
- A new flag must land in **all three**: `App/backend/app/flags.py`, `docs/RAG_ARCHITECTURE.md`, `.env.example`.
- New FastAPI routes must be added to `tests/test_all_endpoints_e2e.py` (`EXPECTED_ENDPOINTS` + `COVERAGE`). This plan adds **no** routes; if you add one, update that file.
- Answers are generated in **English** and localized in exactly one place (`localize_reply`). Every translation goes through `app/mt.py` (cache + `figures_survived`). Do not add a second translation path.
- A new model client must wrap its HTTP call in `tracing.llm_call(...)` and report the provider's `usage`. Metrics go through `analytics.metrics` only, with bounded label values.
- Do not default-on `hyde`, `graph_fusion`, `tool_rag`, `tool_use`.
- `evals/` gates are deterministic: **never call a hosted LLM from an eval job**.
- After any behaviour change run the two doc skills described in `docs/AGENTS.md` (`refresh-docs`, `living-docs-adr-runbooks-traceability`); new architectural decisions get an ADR in `docs/adr/` indexed in `docs/adr/README.md`.

### 0.2 Hard constraint: data sovereignty (this is a URA / government project)

- **No commercial or hosted AI API may receive taxpayer data or prompts** — not for generation, not for judging, not for evaluation, not as a fallback. That excludes Gemini, OpenAI, Anthropic, Cloudflare Workers AI / AI Gateway, OpenRouter, Together, Groq, Hugging Face Inference, etc.
- All models run **locally** on this machine's GPUs through vLLM, from weights cached on disk (`HF_HUB_OFFLINE=1` at serve time).
- Downloading open weights from Hugging Face is allowed (no taxpayer data leaves). Reading public pages (e.g. Bank of Uganda exchange rates, Hugging Face model cards) is allowed.
- The live api container currently has `FLAG_CLOUDFLARE_FALLBACK=false` and `LLM_FALLBACK_BACKEND=` (empty) but **commercial keys are still present in its environment** (`GEMINI_API_KEY`, `CF_AIG_TOKEN`, `CLOUDFLARE_API_TOKEN` are set). Phase C adds a guard so a sovereign deployment refuses to start with any commercial path enabled.

### 0.3 Secrets

- Never print, echo, `cat`, `grep -A/-B/-C` around, or commit secrets (`.env*`, tokens, `~/.cache/huggingface/token`, `~/.config/gh/hosts.yml`). Report only "VAR: set/unset".
- **Never run `docker compose config`** (it prints resolved secrets). Never `grep` compose files or `.env` with context flags — this leaked an `HF_TOKEN` once already.
- Gated model downloads use the **already-cached Hugging Face login** (`huggingface-cli download …` / `hf download …`); never pass a token on the command line.
- Stage files by explicit name; never `git add -A`; never commit `.env*`, `*.pem`, `*.key`, `*token*`, `*credentials*`.

### 0.4 Shared machine etiquette (8× RTX A6000 48 GB, shared with other projects)

- Before using a GPU: `nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv`. As of 2026-10-18 this project uses **GPU 2** (api), **GPU 3** (Orpheus TTS), **GPU 4** (vLLM Sunflower-9B); **GPU 5 and GPU 7 were free** (GPU 6 nearly free). Prefer **GPU 7** for the new model; never take a card another process is using.
- Never kill processes by pattern (`pkill`, `killall`, `docker rm $(docker ps …)` with filters). Record the PIDs / container names you start and stop only those.
- **The user's live stack must keep running** (`ura-app-api`, `ura-app-vllm-sunflower`, `ura-app-frontend`, `ura-app-qdrant`, `ura-app-redis`, `ura-app-orpheus-tts`, `ura-app-connectors`, compose project `app`, served at `https://struttingly-nongeological-briella.ngrok-free.dev`). Experiments run in **separately named containers**. Only Phase I's deploy step touches the live stack.
- When you finish a GPU experiment: `docker rm -f <the container names you started>` and confirm in `nvidia-smi` that the card returned to its prior usage.
- Other agents (codex / opencode / another Claude) sometimes work in this checkout. Before starting and before each push: `ps -eo pid,user,cmd | grep -E "claude|codex|opencode" | grep -v grep`, `git status`, `git log --oneline -5`.

### 0.5 How to run the backend tests

The repo's `.venv` no longer exists; the sandbox Python is 3.10. Run tests in the api image:

```bash
cd /home/developer/Mpairwe7/FinalYearProject
docker run --rm --entrypoint bash -v "$PWD/App/backend:/src:ro" \
  -e APP_ENV=development -e ANALYTICS_DB_DIR=/home/appuser/data_store \
  app-api:gpu -lc 'mkdir -p /home/appuser/work && cp -a /src/. /home/appuser/work/ \
   && cd /home/appuser/work \
   && python3 -m pip install -q --target /home/appuser/pylibs pytest "prometheus-client>=0.26.0" \
   && PYTHONPATH=/home/appuser/work:/home/appuser/pylibs /opt/venv/bin/python -m pytest tests/ -q -p no:cacheprovider' \
  2>&1 | tail -40
```

There is a **pre-existing failure baseline** (fixtures the container lacks; it was 53 failed / 7 errors on 2026-09-23). Record today's baseline in Phase A and **diff against it**: you are responsible only for failures that are new. Pipe probe scripts in over stdin (`docker run -i … < script.py`); a read-only mount of the scratchpad fails with EACCES.

---

## 1. Why this plan exists — the evidence

On 2026-10-18 the live system was probed with a realistic expatriate-payroll case. Full notes: memory `accuracy-eval-2026-10-18`. Reproduce with `POST https://struttingly-nongeological-briella.ngrok-free.dev/api/v1/chat` and JSON `{"message": "...", "conversation_id": "<unique>"}`, header `ngrok-skip-browser-warning: 1`. The path is `/api/v1/chat` (Next rewrites `/api/:path*` to the backend).

**Turn 1:** *"We are hiring a foreign technical specialist on an 8-month contract in Entebbe. They will receive a gross monthly salary of $6,000 plus employer-paid housing and a company vehicle. How is PAYE calculated for a non-resident individual, and does the non-resident tax table differ from resident individuals?"*

**Turn 2 (same conversation):** *"How exactly does URA determine the taxable market value of the furnished house and the motor vehicle benefit when calculating the gross taxable employment income for PAYE?"*

| # | Defect observed | Where it comes from |
|---|---|---|
| D1 | Turn 2 was hijacked by the PAYE **calculator wizard** ("What is your gross monthly salary?") in 0.5 s, with no retrieval and no sources. The same happens when turn 2 is asked on its own. | `calculator_router.plan_calculation`: `_CALC_VERB_RE` matches "calculating", `detect_calculator_intent` → `paye`, no amount → `missing=["monthly_gross"]` → `service._maybe_handle_calculator` starts workflow `calc_paye`. `_LEGAL_INQUIRY_RE` does not cover "how (exactly) does URA determine/value/treat". |
| D2 | The wizard ignored the conversation: it assumed **resident** although turn 1 said non-resident, and re-asked the salary turn 1 had given ($6,000). | `plan_calculation(message)` sees only the current message; there is no per-conversation **case-facts** state. |
| D3 | Turn 1 never computed PAYE, although `calculate_paye` supports `residency="non_resident"` and answers in 0.6 s. The $ amount was never converted. Benefits in kind were ignored. | The RAG path never composes tools. `extract_amounts` does not parse USD. There is no FX source. |
| D4 | It never questioned the "non-resident" premise. **8 months ≈ 240 days ≥ 183 days ⇒ almost certainly resident.** When asked directly, the bot answered: *"Since the specialist is visiting for only 8 months, they do not meet the 183-day requirement… non-resident"* — wrong arithmetic, although its own citation [5] stated the ≥183-day rule correctly. | 9B generator (`Sunbird/Sunflower-Qwen3.5-9B`) failing multi-step reasoning. No code-owned residency test. |
| D5 | Made-up comparisons: *"resident individuals use a simplified flat rate"*, *"Next 665,000 UGX"* (that band is 75,000 wide). | 9B generator. Nothing locks figures or claims to tool output. |
| D6 | Housing benefit answer: *"This value is **subtracted** from the employee's gross salary"* — backwards (the benefit is **added** to employment income). **Faithfulness scored it 1.0.** | `retriever.compute_faithfulness` counts token overlap ≥ 50 % per sentence and is polarity-blind. `claim_verifier.verify_claims` is overlap-based (`deterministic_overlap_v1`). |
| D7 | Turn 1 cited the **agriculture** and **hotel** sector guides for an employment question. | `retriever.apply_preference_boost` boosts tax type/FY only; sector guides are never demoted for non-sector questions. |
| D8 | The user's copy of turn 1 was **cut off mid-sentence** ("…for the current fiscal"). | `LLM_MAX_TOKENS=512` (live env), and `llm._vllm_generate` **discards `finish_reason`**. `service._looks_truncated` is punctuation-only, and its only recovery is the cloud chain (forbidden and disabled), so the truncated text is served. The streaming path (`/v1/chat/stream`) has no recovery at all. |

What worked: deterministic calculators are exact (`calculate_paye` 22,000,000/month → non-resident 7,725,500, resident 7,688,250; checked by hand against `tax/data/FY2026-27.json`). Single-fact retrieval (the car-benefit formula) was mostly right.

**Diagnosis:** two classes of problem.
1. **Plumbing** that code fixes cheaply: routing, context carry-over, tool composition, truncation, figure and polarity checks, retrieval ranking.
2. **Generator capability**: a 9B model cannot reliably do multi-step legal reasoning. Fix this with a **stronger local model for hard English turns**, and by **moving every rule that has a number, threshold, date or legal test into code** so the model explains results instead of inventing them.

---

## 2. Target architecture

```
taxpayer message (+ conversation id)
        │
        ▼
[1] Case-facts extractor (code) ──► CaseFacts (per conversation, persisted; merges every turn)
        │                              residency facts, days present, salary+currency, benefits,
        │                              entity type, turnover, amounts, tax heads mentioned, …
        ▼
[2] Intent router (code, fixed)
        ├─ pure calculation ask, all slots known (incl. from CaseFacts) ──► calculator (T0, no LLM)
        ├─ calculation ask, slot missing and not in CaseFacts ─────────► wizard asks ONLY for the missing slot
        └─ explanation / legal / compound question ─────────────────────► [3]
        ▼
[3] Scenario planner (code): CaseFacts + question → list of applicable deterministic tools
        (residency test, BIK valuers, PAYE with benefits, residency comparison, FX, VAT, WHT, rental, …)
        → run them → "Verified Statutory Facts & Calculations" block (+ legal_basis strings)
        → premise/contradiction findings (e.g. "user says non-resident but 240 days ≥ 183")
        ▼
[4] Retrieval (hybrid, with sector-guide demotion + topic-section boost)
        ▼
[5] Tier selection (code): select_tier(route, query, case facts, #tools, multi-hop, locale…)
        T0 deterministic  │  T1 Sunflower-Qwen3.5-9B (GPU 4)  │  T3 reasoner Qwen3.8-27B-FP8 (GPU 7)
        ▼
[6] Generation on the chosen LOCAL endpoint; finish_reason honoured (continuation on "length")
        ▼
[7] Verification (code): figure lock (every number ∈ tool outputs ∪ user input ∪ passages),
    polarity/direction check, claim support; one bounded revision on the SAME tier; never cloud
        ▼
[8] localize_reply (unchanged; Sunflower MT for lg/sw) → audit → response
```

Principle: **the model writes, code decides.** Any figure, threshold, legal test or deadline in an answer must come from a tool result or a retrieved passage. The model's job is to choose and compose tools (T3), explain, and ask clarifying questions.

---

## 3. Phase A — Branch, baseline, and the held-out complex-scenario set

**Do this before any fix.** A held-out set written after the fixes measures nothing.

### A.1 Branch and environment check
```bash
cd /home/developer/Mpairwe7/FinalYearProject
git fetch origin && git checkout dev && git pull --ff-only
git checkout -b feat/complex-scenario-eval
ps -eo pid,user,cmd | grep -E "claude|codex|opencode" | grep -v grep
nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv
```

### A.2 Record the unit-test baseline
Run §0.5 on clean `dev` and save the list of failing test ids to `evals/complex_scenarios/BASELINE_FAILURES.txt` (ids only, no output). Later phases must not add new failures.

### A.3 Write the complex-scenario set

Create `evals/complex_scenarios/` containing:
- `README.md` — purpose, schema, how to run, the held-out rule, the review status.
- `dev.jsonl` — **~30 scenarios** you may look at and tune on.
- `heldout.jsonl` — **~75 scenarios** you may run **only twice**: once at baseline (Phase A.5) and once at the end (Phase H). Never tune on it.
- `HELDOUT.sha256` — `sha256sum heldout.jsonl > HELDOUT.sha256`, committed in the same PR. If the held-out file changes after the baseline run, the comparison is void; you must say so in the final report.

**Coverage.** At least these tax heads, each with ≥ 3 held-out and ≥ 1 dev scenario. At least 25 % must be multi-turn (2–4 turns that depend on earlier facts), and at least 30 % must contain a false or questionable premise.

| Tax head | Scenario ideas (vary numbers, phrasing, persona) |
|---|---|
| PAYE / employment | residency tests (days, permanent home, 122-day average, July–June spanning); BIK: car (incl. 35 % depreciation in later years), housing (lesser-of rule), domestic staff, utilities, low-interest loan (> 1M), debt waiver, furniture; secondary employment; terminal benefits (25 % relief); foreign-currency salary; resident vs non-resident difference |
| VAT | registration threshold; input-tax claim without an EFRIS e-invoice; imported services reverse charge; exports zero-rating; mixed supplies apportionment; deemed supplies |
| Withholding tax | 6 % domestic; 15 % non-resident service contracts (and "an employment contract is NOT a service contract"); exemption certificates; dividends, interest, royalties, management fees |
| Rental income | individual pooling of all properties, threshold applied once, no expense deduction; company landlords (expense cap); joint ownership |
| Corporation / business income | 30 % rate; start-up exemption (citizen, capital cap, years); branch repatriation tax; presumptive tax thresholds |
| Capital gains | individual vs company disposal; business asset vs personal asset |
| Customs | CIF valuation hierarchy; EAC CET bands; used-vehicle environmental levy by age; import WHT; infrastructure levy; temporary importation; passenger baggage allowance |
| Excise duty / DTS | per-unit excise (sugar, cement, fuel); mobile-money withdrawal; DTS on unstamped goods |
| Stamp duty | property transfer; motor-vehicle transfer |
| Objections & appeals | objection window; deposit on disputed tax (30 % rule); Tax Appeals Tribunal deadline; default assessments |
| Penalties & interest | late filing penal tax (monthly rate and minimum); late-payment interest; EFRIS penalties |
| Digital services / non-resident e-commerce | DST on non-residents; imported SaaS (WHT vs VAT reverse charge) |
| Gaming & betting | rate; WHT on winnings |
| Advance tax on vehicles | passenger (per seat) vs goods (per tonne) |
| Procedures | TIN, TCC, return-filing deadlines (PAYE by the 15th, etc.) |
| Out-of-scope / jurisdiction | Kenya or Tanzania tax asked of the URA bot (must decline / redirect) |

**Authoring rules** (these prevent the "harness speaks system vocabulary" trap, where probes phrased like the code sail through the gate they test):
1. Write messages as a real taxpayer would: plain words, messy numbers ("6k dollars", "UGX 1.5m", "about 90 million a year"), sometimes misspellings. **Never** copy wording from regexes, tool names, FAQ questions, or the corpus.
2. **Never hardcode an expected money figure.** Express expected figures as a **tool call spec** that the scorer evaluates against the current rate table at run time (schema in §12.4). For scenarios that need a tool that does not exist yet (BIK, residency), still write the spec; the scorer marks it `tool_missing` at baseline. That is the honest baseline.
3. Expected facts must be checkable: `must_include` uses alternatives (`{"any": ["resident", "183 days"]}`); `must_not_include` lists known-wrong claims (e.g. `"subtracted from"` for BIK, `"flat rate"` for resident PAYE).
4. For every legal fact you encode, add a `basis` field quoting the corpus passage (file + section) it comes from. **Use only the corpus** (`App/Data/pdf_jsonl/*.jsonl`, `Data/dataset/*.csv`) and `App/backend/app/tax/data/FY*.json`. If you cannot find a source, leave the fact out.
5. Put the 8 probes from §1 / memory `accuracy-eval-2026-10-18` in **dev**, not held-out: we have already looked at them.
6. Set `"reviewed": false` on every scenario. Tell the user that a human with tax knowledge should review the held-out expectations; the final report must state how many were reviewed.

### A.4 Build the harness

1. `evals/complex_scenarios/scoring.py` — a pure, deterministic scorer, importable by tests, with no network and no LLM:
   - `score_turn(expect, response, tool_runner) -> TurnScore` with these components:
     - `route_ok`: the expected route (`calculator` / `workflow` / `rag` / `declined`) matches `response["retrieval_mode"]` or `agent_role` (mapping documented in the README).
     - `figures`: for each figure spec, run the tool through `tool_runner` (`ToolRegistry.call` in-process, against the current rate table), extract the expected value, and search the reply for it (tolerance ±0.5 % and ±1 UGX; accept "UGX 7,725,500", "7.73 million", "Shs 7,725,500"). Score = hits / specs. If the tool is missing → `tool_missing` (counts as a miss).
     - `facts`: recall over `must_include` groups.
     - `forbidden`: count of `must_not_include` hits. **Any hit on a `severity: critical` item fails the turn.**
     - `premise`: when `expect.premise_flag` is true, the reply must challenge the premise (keywords from the scenario's `premise_cues`).
     - `complete`: the reply is not truncated. Use `finish_reason` when exposed (Phase E adds `response.debug.finish_reason` behind `EVAL_DEBUG=1`); otherwise the reply must end with terminal punctuation and must not end mid-word.
     - `sources_relevant`: share of cited sources whose filename does not match `GUIDE-TO-TAXATION-OF-THE-.*-SECTOR` / `Hotel-and-Accomodation` unless the scenario names that sector.
     - `latency_s`.
   - Turn score = 0.35·figures + 0.30·facts + 0.15·premise(if applicable) + 0.10·route_ok + 0.10·sources_relevant. Then 0 if any critical forbidden hit, ×0.5 if incomplete. When no figure spec exists, its weight is redistributed proportionally. Scenario score = mean over turns.
   - Unit tests: `tests/agents/test_complex_scenarios_scoring.py` (fixture replies covering each component, including the D6 "subtracted" reply → critical fail).
2. `scripts/eval_complex_scenarios.py` — the live runner:
   - Args: `--base-url` (default the ngrok URL), `--split dev|heldout`, `--out evals/reports/complex_<split>_<label>_<date>.json`, `--concurrency 2`, `--label`.
   - One fresh `conversation_id` per scenario. Turns are sent in order on `/api/v1/chat` (non-streaming; Phase E keeps REST and streaming at parity).
   - Outputs a per-scenario JSON, a per-tax-head table, an overall score, a "critical errors" list, latency p50/p95, and a truncation rate.
   - When `--split heldout` is used, it refuses to run unless `sha256sum` matches `HELDOUT.sha256`.
3. `tests/agents/test_complex_scenarios_schema.py` — validates both JSONL files: schema, unique ids, coverage table satisfied, every legal fact has `basis`, held-out hash matches.

### A.5 Baseline runs (the "before" numbers)
```bash
python3 scripts/eval_complex_scenarios.py --split dev     --label baseline
python3 scripts/eval_complex_scenarios.py --split heldout --label baseline   # 1st of the 2 allowed held-out runs
```
Also run the existing regression measurements and keep their outputs (they define "no regression"):
`scripts/evaluate_2000_faqs_ngrok.py`, `scripts/eval_multiturn_trilingual.py`, `scripts/eval_retrieval_multilingual.py` (read each script's `--help` first; they target the live ngrok stack).

**Deliverable / PR #1:** `test(evals): add held-out complex-scenario set, scorer and live runner`. Commit the sets, the scorer, the runner, the tests and the baseline report JSONs. No behaviour change.

---

## 4. Phase B — Model bake-off **before** switching

Goal: choose the T3 reasoning model with evidence. The user's preferred candidate is **Qwen/Qwen3.8-27B-FP8**. Run the comparison before wiring anything into production.

### B.1 Contenders (verified on Hugging Face 2026-10-18)

| Id | Model | Size / licence | Notes |
|---|---|---|---|
| **A** | `Sunbird/Sunflower-Qwen3.5-9B` (live, GPU 4) | 9B, Apache-2.0 | Baseline. Reuse the live endpoint read-only (`http://localhost:8011/v1`; it is mapped `8011:8001`). |
| **B** | `Qwen/Qwen3.8-27B-FP8` | 27.8B dense, Apache-2.0, released 2026-08 | **Preferred.** Same architecture (`Qwen3_5ForConditionalGeneration`, `model_type: qwen3_5`) as the live Sunflower, so `vllm/vllm-openai:v0.17.1` should serve it unchanged. ~29 GB of FP8 weights fit on one A6000. |
| **C** | `Sunbird/Sunflower-Qwen3.8-27B` | 27.4B, Apache-2.0, **gated**, fine-tuned from Qwen3.8-27B (2026-09-21) | Ugandan-language fine-tune at 27B. If its English reasoning ≈ B, one model could later serve **both** roles and free GPU 4. Serve with `--quantization fp8` (on-the-fly, like the live 9B). Needs access granted to the cached HF account; if access is denied, skip C and record that. |
| **D** (optional) | `Qwen/Qwen3.5-35B-A3B-FP8` | 36B MoE (3B active), Apache-2.0 | Speed reference: ~3–4× faster decode, weaker multi-step reasoning expected. |

Excluded, with reasons (put these in the report): DeepSeek-V4.x (763B) and Qwen3.8-Flash-Next (180B, licence "other") do not fit; Gemma-4 and Llama-3.3 have gated, non-Apache licences and no expected advantage; Qwen3.5-122B-A10B / gpt-oss-120b need **both** free GPUs on a shared machine (consider them only if B/C fail the bar).

Each of B and C is measured in **two modes**: `enable_thinking=false` and `enable_thinking=true` with a capped budget (B.4).

### B.2 Download weights (no token on the command line)
```bash
export HF_HOME=/mnt/nas1/caches/huggingface_cache          # 2.1 TB free as of 2026-10-18
hf download Qwen/Qwen3.8-27B-FP8                           # or: huggingface-cli download …
hf download Sunbird/Sunflower-Qwen3.8-27B                  # gated: uses the cached login; on 401/403 skip C
hf download Qwen/Qwen3.5-35B-A3B-FP8                       # optional (D)
```
Record each snapshot path (`$HF_HOME/hub/models--<org>--<name>/snapshots/<sha>`) and the commit sha in the report.

### B.3 Serve one contender at a time on GPU 7, in an isolated container

Do not use the project compose files for the bake-off (that risks the `api`/`vllm` DNS-alias trap and touching the live stack). Use a standalone `docker run`:

```bash
GPU=7   # confirm free first with nvidia-smi
SNAP=<snapshot path from B.2>
NAME=bakeoff-vllm-b          # unique name per contender; record it
docker run -d --name "$NAME" --gpus "device=$GPU" --ipc=host \
  -e HF_HUB_OFFLINE=1 -e VLLM_NO_USAGE_STATS=1 -e DO_NOT_TRACK=1 \
  -v "$SNAP":/model:ro -p 127.0.0.1:8021:8001 \
  vllm/vllm-openai:v0.17.1 \
  --model /model --served-model-name bakeoff \
  --port 8001 --max-model-len 65536 --gpu-memory-utilization 0.90 \
  --max-num-seqs 8 --kv-cache-dtype fp8 --dtype auto \
  --limit-mm-per-prompt '{"image": 0, "video": 0}' \
  --enable-prefix-caching --enable-chunked-prefill \
  --enable-auto-tool-choice --tool-call-parser hermes \
  --reasoning-parser qwen3
# For C (unquantised checkpoint) add: --quantization fp8
```
- Check the model card for the recommended `--tool-call-parser` / `--reasoning-parser` for Qwen3.8. If v0.17.1 rejects a flag, drop it and note this. If v0.17.1 cannot load the model at all, try the newest `vllm/vllm-openai` tag **older than two weeks**, and record the version.
- Wait for `curl -s 127.0.0.1:8021/v1/models` to return 200. In `docker logs "$NAME" | grep -i "maximum concurrency"`, record KV capacity. If OOM, lower `--max-model-len` to 32768.

### B.4 Model-only comparison on frozen contexts (a fair test of the generator alone)

1. **Freeze contexts once** (the same for every contender). For each **dev** scenario turn, capture inside the live api container the exact `messages` the system would send to the LLM: the passages from `retriever.search_planned(...)` and the prompt from `llm._build_messages(...)`, plus `_evaluate_calculation_context(query)` output. Write a small script `scripts/bakeoff_freeze_contexts.py` and pipe it via `docker exec -i ura-app-api /opt/venv/bin/python - < script.py`. Save to `evals/complex_scenarios/frozen_contexts_dev.jsonl`. This file is **gitignored** if it contains anything beyond public corpus text; check before committing.
2. `scripts/bakeoff_reasoner.py --endpoint http://127.0.0.1:8021/v1 --model bakeoff --mode {nothink,think} --label B-nothink …`:
   - Sends each frozen `messages` list to `/chat/completions` with the production sampling parameters (`LLM_TEMPERATURE`, `top_p` 0.95, `min_p`, penalties from `app/llm.py`), `max_tokens=1536` (nothink) or `max_tokens=4096` (think, total, including reasoning), and `chat_template_kwargs={"enable_thinking": <mode>}`.
   - Records reply, `finish_reason`, `usage`, TTFT and total latency (use streaming for TTFT), and the reasoning-token count.
   - Scores with `evals/complex_scenarios/scoring.py` (`route_ok` and `sources_relevant` are not applicable here; reweight).
3. **Add a 12-question lg/sw sanity set** (use `evals/multiturn_trilingual/scenarios.jsonl` turns) for contender C only. Measure the English answer quality and, separately, C's translation quality vs A (spot-check plus `mt.figures_survived`). This only informs the "one model for both roles" follow-up.
4. **Tool-calling fidelity:** 15 dev prompts where the correct action is a calculator call. Give the model the real tool schemas from `ToolRegistry` (`tax_calculator` namespace) and measure the % of valid JSON tool calls with correct arguments. This matters most for T3.
5. **Throughput:** 8 concurrent streams of a 1,000-token answer; record tokens/s per stream and p95 latency.

### B.5 Decision rule (write the numbers into the report and apply this rule mechanically)

- Primary metric: mean dev scenario score (model-only). Secondary: critical-error count, tool-call fidelity, p95 latency (nothink), truncation rate.
- Pick the contender/mode with the highest primary score **subject to**: critical errors ≤ baseline A's critical errors × 0.5, tool-call fidelity ≥ 90 %, p95 total latency ≤ 45 s (nothink) for a 1,000-token answer.
- **Thinking mode** is enabled for T3 only if it adds ≥ 5 points over nothink of the same model **and** its p95 ≤ 60 s. Otherwise T3 runs nothink. Later, Phase D can enable thinking only for the hardest sub-class.
- B vs C: choose C only if it is within 2 points of B on English and ≥ B on tool fidelity. Otherwise choose B and file "evaluate C as single-model consolidation" as a gap-register item.
- If **no** contender beats A by ≥ 10 points, stop and report to the user before Phase C. The code phases (E–G) still proceed; they do not depend on the model.

### B.6 Report and cleanup
- Write `docs/Reports/reasoner-bakeoff-2026-10.md`: setup (GPU, vLLM version, flags, snapshot shas), the table per contender × mode, the decision and the reasons, and the exclusions.
- `docker rm -f bakeoff-vllm-b bakeoff-vllm-c …` (only the names you started); confirm GPU 7 is back to idle in `nvidia-smi`.
- **PR #2:** `docs(report): reasoner bake-off and model decision` (report + bake-off scripts).

---

## 5. Phase C — Serve the reasoning model + sovereign-mode guard

### C.1 Compose service
Add a new overlay `App/docker-compose.reasoner.yml` (do not edit `docker-compose.local-sunflower.yml`'s `vllm` service). Model it on that file's `vllm` service, with these differences:

- service name **`vllm-reasoner`**, `container_name: ura-app-vllm-reasoner`, port `127.0.0.1:8012:8001`
- `--served-model-name ${REASONER_MODEL:-Qwen/Qwen3.8-27B-FP8}` (or the B.5 winner), `--max-model-len ${REASONER_MAX_MODEL_LEN:-65536}`, `--gpu-memory-utilization ${REASONER_GPU_MEM_UTIL:-0.90}`, `--max-num-seqs ${REASONER_MAX_NUM_SEQS:-8}`, `--kv-cache-dtype fp8`, the parsers chosen in B.3, plus `--quantization fp8` only when the checkpoint is not already FP8
- volume `${REASONER_LOCAL_MODEL_DIR:?…}:/model:ro`; `NVIDIA_VISIBLE_DEVICES` / `device_ids` = `${REASONER_GPU_ID:-7}`
- environment `HF_HUB_OFFLINE=1`, **`VLLM_NO_USAGE_STATS=1`, `DO_NOT_TRACK=1`** (also add these two to the existing `vllm` service: vLLM otherwise sends anonymous usage stats by default)
- the same healthcheck, with `start_period: 8m`
- an `api` block adding `REASONER_BASE_URL=http://vllm-reasoner:8001/v1`, `REASONER_MODEL=…`, `FLAG_MODEL_TIERING=true`, and `depends_on: vllm-reasoner: condition: service_healthy`

Document the bring-up command in a runbook (Phase I). Do **not** bring it up against the live stack yet. Verify it with the isolated recipe in §10.2.

### C.2 Sovereign-mode guard
- New setting `SOVEREIGN_MODE` (env, default `false` in development, **`true` in the production profile and in `configs/prototype.env`**). Document it in `.env.example`.
- Startup check in `app/production_readiness.py` (it already hosts production gap gates; follow its pattern): when `SOVEREIGN_MODE=true`, **fail startup** if any of these is true:
  - `flags.is_enabled("cloudflare_fallback")`
  - `LLM_FALLBACK_BACKEND` non-empty
  - `LLM_PRIMARY_BACKEND` is not local
  - any configured LLM base URL (`VLLM_BASE_URL`, `REASONER_BASE_URL`) resolves to a non-private host. Use a hostname allowlist: compose service names, `localhost`, RFC 1918 addresses.
  - any commercial key env var (`GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `CF_AIG_TOKEN`, `CLOUDFLARE_API_TOKEN`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`) is set. **Log only the variable name**, never its value.
- Also add a runtime assertion in `service._llm_cloud_fallback` (and any other path that calls `gateway.workers_ai_chat` or Gemini) that returns `""` immediately when `SOVEREIGN_MODE` is on. Defence in depth.
- Tests: `App/backend/tests/test_sovereign_mode.py` — each forbidden condition raises; the clean config passes; the error message contains the var name and not the value.
- Tell the user (do not do it yourself) that the commercial keys should be removed from the live stack's env file before enabling `SOVEREIGN_MODE` there.

**PR #3:** `feat(infra): local reasoning-tier vLLM service and sovereign-mode startup guard`.

---

## 6. Phase D — Multi-endpoint LLM client and real tier routing

Today `providers/routing.select_tier` is only **logged** (`service.py` ≈ line 10240: `trace_ctx["model_tier"]`). Every generation goes to the single `LLM_MODEL` @ `VLLM_BASE_URL`. `MODEL_SLOTS` still names Qwen3-8B / 30B-A3B / 235B-A22B, which are not served anywhere.

### D.1 `ModelEndpoint` (read `app/llm.py` lines 1–200 and 1030–1560 first)
- In `app/providers/routing.py` add:
  ```python
  @dataclass(frozen=True)
  class ModelEndpoint:
      base_url: str
      model: str
      thinking: bool = False
      max_tokens: int = 768
      timeout_s: float = 120.0
  ENDPOINTS: dict[ModelTier, ModelEndpoint]  # built from env at import: T1 → VLLM_BASE_URL/LLM_MODEL, T3 → REASONER_BASE_URL/REASONER_MODEL
  ```
  Keep the existing comment's dataflow principle: the URL comes only from this env-built dict, **never from request data**.
  - T2: points to the T3 endpoint with `thinking=False` (the old "agentic MoE" slot is not served). Update the `ModelTier` docstrings and the `model_tiering` flag description in `flags.py` + `docs/RAG_ARCHITECTURE.md` to the real models.
  - If `REASONER_BASE_URL` is unset, T2/T3 fall back to the T1 endpoint and the reason string says so. The flag-off behaviour must be unchanged.
- Thread an optional `endpoint: ModelEndpoint | None = None` through `llm._vllm_generate`, `async_vllm_generate`, `_vllm_chat_completion`, `_vllm_generate_stream`, `generate`, `generate_stream`, `generate_with_tools`. `None` = today's behaviour (`LLM_MODEL` @ `VLLM_BASE_URL`). Use `endpoint.model` in the body **and** in `llm_call("chat", model, "vllm")` so traces and token usage are attributed correctly.
- `_served_context_window()` / `_count_prompt_tokens()` / `_fit_to_context()` cache **per base URL** (today they are global). The tokenizer endpoint must be the same server as the generation endpoint.
- `chat_template_kwargs={"enable_thinking": endpoint.thinking}`. With thinking on, read `message.reasoning_content` (reasoning parser) and **never** surface it to the user; keep `strip_thought` as a safety net.

### D.2 Tier selection that actually uses the signals
- In `service.py`, where `select_tier(...)` is called (≈ line 10240), also pass `query=rewritten`, `multi_hop=<planner says ≥ 2 tools or ≥ 2 tax heads>`, `evaluator_rejected=<on revision>`. Then **use** `tier_decision`: map it to `ENDPOINTS[tier]` and pass `endpoint=` into the generation call for this turn (REST `_generate_en`, streaming core, and the tool loop).
- Extend `select_tier`'s promotions (keep the existing ones): promote to T3 when the scenario planner (Phase F.6) produced ≥ 1 tool result **and** the question also asks "how/why/explain"; when CaseFacts has a premise contradiction; or when `ConversationEntities.tax_topic_keys` has ≥ 2 heads.
- **Locales:** `ADAPTER_BOUND_LOCALES` pins lg/sw/nyn/ach to T1 because of old LoRA adapters. Read `_generate_en` and `localize_reply` first. If (as `App/backend/AGENTS.md` states) the answer is generated in English and then localized via `mt`, then a hard lg/sw question may use T3 for the **English generation** while localization stays on Sunflower. Implement this behind a new flag `reasoner_for_translated_locales` (default **off**). Turn it on only after `scripts/eval_multiturn_trilingual.py` shows no regression.
- Budget: keep `budget_exhausted` semantics. Add env `REASONER_MAX_SHARE` (default 0.25). If the rolling share of T3 turns in the last 200 turns exceeds it, cap at T2 (same endpoint, thinking off). Use the existing metrics facade.
- Deadline: env `REASONER_DEADLINE_S` (default 60). On timeout, fall back to T1 **with the same verified-facts block** (local, never cloud) and log `model_tier_fallback_total{reason="deadline"}`.

### D.3 Tests
- `tests/agents/test_model_endpoints.py`: env → `ENDPOINTS` mapping; unset reasoner → fallback; the URL never comes from request input; per-endpoint context-window cache; the `model` field in the request body equals the endpoint's model (mock the HTTP client).
- Extend the existing `select_tier` tests with the new promotions, and confirm lg/sw stays T1 while the new flag is off.

**PR #4:** `feat(llm): per-tier local model endpoints and active tier routing behind model_tiering`.

---

## 7. Phase E — Truncation fix

Root cause (D8): `max_tokens` too small, and `finish_reason` is thrown away; the only recovery is the forbidden cloud chain.

### E.1 Surface `finish_reason`
- Add `@dataclass(frozen=True) class Generation: text: str; finish_reason: str | None; usage: dict | None` in `app/llm.py`. Add `_vllm_generate_ex(...) -> Generation`; make `_vllm_generate(...)` a thin wrapper that returns `.text`, so existing callers keep working. Do the same for `async_vllm_generate_ex`.
- Streaming: `_stream_tokens` already sets `call.finish_reason`. Expose it to the caller: the generator's `return` value, or a mutable holder passed in. Read the streaming core in `service.stream_llm_tokens` and use the least invasive option.

### E.2 Continuation instead of truncation
- New helper `llm.complete_with_continuation(messages, endpoint, max_rounds=2, deadline)`:
  - Call once. If `finish_reason == "length"`, issue a continuation request: append `{"role": "assistant", "content": partial}` and call with `continue_final_message=True, add_generation_prompt=False` (vLLM chat-completions extra params; check v0.17.1 supports them by running a test request against the bake-off container. Fallback: a user message "Continue exactly where you stopped. Do not repeat anything."). Concatenate and repeat up to `max_rounds` while the deadline allows.
  - If still `length` after the last round, **trim to the last complete sentence/list item/table row** and append one line: "_(This answer was shortened — ask me to continue for the rest.)_". **Never** serve a mid-word cut.
- Streaming parity: when a stream ends with `finish_reason == "length"`, start a continuation stream and keep yielding tokens into the same SSE response. The client sees one continuous answer.

### E.3 Budgets
- Per tier: T1 `max_tokens` 768 (env `LLM_MAX_TOKENS`, raise the default from 512), T3 `REASONER_MAX_TOKENS` 1536 (nothink) / 4096 total (think). Respect `_fit_to_context`.
- Raise the live env `LLM_MAX_TOKENS` from 512 to 768 in `App/docker-compose.local-sunflower.yml` (the `api` block), `configs/prototype.env`, `.env.example`.

### E.4 Replace the punctuation heuristic as the decision maker
- `service._looks_truncated` stays only as a fallback when `finish_reason` is unavailable (non-vLLM backends). In `_call_llm_with_deadline` (≈ line 1048–1190), decide on `finish_reason` first. Delete the "try cloud fallback because it looks truncated" branch when `SOVEREIGN_MODE` is on; replace it with the E.2 continuation.
- Note: `_REPLY_TERMINATORS` accepts `:` and `-`, so a reply cut after "the following:" passes today. `finish_reason` fixes this; keep the heuristic for the fallback only.

### E.5 Eval visibility
- When env `EVAL_DEBUG=1` (never in production; reject it in `production_readiness` when `APP_ENV=production`), include `debug: {finish_reason, continuation_rounds, model_tier, endpoint_model}` in the chat response. The scorer uses it for `complete`.

### E.6 Tests
- Mocked vLLM returns `length` then `stop` → stitched answer, 2 calls. Always `length` → trimmed at a sentence boundary + note. Streaming → one continuous SSE answer. `_looks_truncated` is not consulted when `finish_reason` exists. Sovereign mode → no cloud call.

**PR #5:** `fix(llm): honour finish_reason with bounded continuation; never serve mid-word truncation`.

---

## 8. Phase F — Code-owned tax reasoning (general, not just the failing case)

Generalisation strategy: **(1)** a generic per-conversation *facts* layer, **(2)** a generic *planner* that maps facts + question to tools, **(3)** rules as **data** in the rate table with `legal_basis`, and **(4)** an explicit "no tool covers this" disclosure. New tax rules then become data plus a small tool, not new router special cases.

Read first: `app/calculator_router.py` (whole file), `app/tools/calculators.py` (`CalculatorTool`, `PAYECalculator`, `CALCULATOR_TOOLS`, `_require_scalar`, `_require_bands`, `apply_bands`), `app/tax/tables.py` (`_load_table` validation), `app/tax/data/FY2026-27.json` and `FY2025-26.json`, `app/context_manager.py` (`ConversationEntities`, `extract_conversation_entities`, `RollingContextManager`), `app/premise_guard.py`, `service._maybe_handle_calculator` (≈ line 8620), `service._evaluate_calculation_context` (≈ line 997), and `tests/test_calculator_router.py` / `tests/test_tax_calculators.py`.

### F.1 CaseFacts — per-conversation structured facts (fixes D2, enables everything else)
- New module `app/case_facts.py`:
  ```python
  @dataclass
  class Money: amount: Decimal; currency: str  # "UGX" | "USD" | "EUR" | "GBP" | "KES" …
  @dataclass
  class CaseFacts:
      residency_claim: Literal["resident","non_resident"] | None   # what the user SAID
      days_in_uganda: int | None          # derived from "8-month contract", "240 days", dates
      period_start: date | None; period_end: date | None
      permanent_home_in_uganda: bool | None
      gov_employee_posted_abroad: bool | None
      monthly_salary: Money | None; annual_salary: Money | None
      benefits: list[BenefitFact]          # kind ∈ {car, housing, furniture, domestic_staff, utilities, meals, loan, debt_waiver, other} + known params
      entity_type: Literal["individual","company","partnership","trust"] | None
      annual_turnover: Money | None
      tax_heads: list[str]                 # reuse context_manager topic keys
      amounts: list[Money]                 # every amount, with the phrase it came from
      provenance: dict[str, int]           # field → turn index it came from
  def extract_case_facts(message: str, prior: CaseFacts | None) -> CaseFacts  # merge; later explicit statements override earlier ones
  ```
- Parsing must handle `$6,000`, `USD 6k`, `6,000 dollars`, `UGX 1.5m`, `1.5 million shillings`, `Shs 900,000`, "per month/monthly/a month", "per year/annual", "8-month contract", "for 8 months", "240 days", "from March to October 2026". Reuse `calculator_router.extract_amounts` / `parse_ugx_amount` for UGX; add currency detection beside them, not a second UGX parser.
- Persistence: store per conversation the same way `calculator_router.remember_calculation` does (Redis via `_redis_store()`, with TTL, falling back to in-process). Key: `case_facts:{conversation_id}`, tenant-scoped like existing workflow keys. Clear on the existing conversation-reset paths.
- Privacy: CaseFacts are conversation-scoped working data. Include them in the existing `/v1/me` export/erasure (see memory `account-management-ids`: export/erasure cover chat history + memory), and redact them from logs (amounts are fine, names/TINs are not).
- Tests: `tests/agents/test_case_facts.py` — parsing table (≥ 40 phrasings across en, plus the lg/sw amount words already handled by `number_words.py`), cross-turn merge, override, provenance.

### F.2 Router: explanation vs calculation (fixes D1 for all heads)
- In `calculator_router.py` add `_EXPLANATION_RE`: phrases asking **how a rule works** — `how (exactly )?(does|do|is|are|would) (URA|the law|it|they) (determine|value|valuate|treat|work out|assess|compute|calculate|apply)`, `what is the (formula|basis|rule|method)`, `how is .* (valued|determined|treated|taxed)`, `explain`, `why`, `what counts as`, `is .* taxable`, plus the lg/sw equivalents that already exist for calc verbs. If it matches and the message carries **no amount of its own**, `plan_calculation` returns `None` → RAG/planner path.
- **Guard against the rewritten-query trap:** call the guard on both `message` and the abbreviation-expanded `rewritten` query. `service._maybe_handle_calculator` already calls `plan_calculation(message) or plan_calculation(rewritten)`, so the guard must return `None` for both. Test it with expanded forms.
- Tests (`tests/test_calculator_router.py`): turn 2 of §1 verbatim → `None`; ≥ 15 paraphrased explanation questions across PAYE/VAT/WHT/rental/customs → `None`; ≥ 15 genuine calculation asks (with and without amounts) still plan.

### F.3 Wizard reads CaseFacts (fixes D2)
- In `_maybe_handle_calculator`, before deciding `missing`, fill the plan's params from CaseFacts (salary → `monthly_gross` after FX, residency → from the **residency tool's result** when the days are known, else the user's claim). Only ask for what is still missing. When residency comes from CaseFacts, the assumption line must say so ("using the 8-month contract you mentioned → resident").
- Feed CaseFacts into the existing `zero_effort_slot_prefill` path too, where it applies; keep that flag's semantics.

### F.4 New deterministic tools (each a `CalculatorTool` subclass in `app/tools/calculators.py`, registered in `CALCULATOR_TOOLS`, exposed via the `tax_calculator` MCP namespace automatically)

All parameters, percentages and thresholds come from the rate table (`_require_scalar` / new `_require_rule`). **No literals in code.** Every result carries `rate_keys` and the `legal_basis` text, like the existing calculators.

1. **`determine_residency`** — inputs: `days_in_uganda` (in the relevant 12-month window), `permanent_home` (bool|None), `avg_days_current_and_two_prior` (number|None), `gov_employee_posted_abroad` (bool|None), optional `period_start`/`period_end` to explain the July–June year-of-income span. Output: `status` (`resident`/`non_resident`/`undetermined`), `tests` (each test with pass/fail/unknown and its threshold), `missing_facts`, `years_of_income_affected`. Rule data in the table: `residency_days_threshold` = 183, `residency_avg_days_threshold` = 122, `year_of_income_end` = "06-30". Legal basis: §12.1(a).
2. **`value_benefit_in_kind`** — `kind` ∈ {car, housing, domestic_staff, meals, utilities, low_interest_loan, debt_waiver, property_or_services, other}. Rules per §12.1(b):
   - car: `20% × A × B/C − D`, where A = market value when first provided, **depreciated at 35 % reducing balance for each subsequent year** (input `years_since_first_provided`), B = days available for private use in the year of income, C = days in the year of income, D = employee payment. The tool returns the annual value and a per-month value for PAYE.
   - housing: lesser of (a) market rent − employee payment, (b) 15 % of employment income for the year (the handbook says "including the amount referred to in paragraph (a)"). **Implement exactly as the handbook text reads, put the full quoted text in `notes`, and add the key to the table's `unverified` list** until a human confirms the interpretation. While unverified, the tool must say so in its output (the table loader already surfaces `unverified` keys).
   - domestic staff: total employment income paid to the assistants − employee payment
   - meals/refreshment/entertainment: cost to employer − employee payment
   - utilities: cost to employer − employee payment
   - low-interest loan (only when the loan > UGX 1,000,000): interest at the statutory rate (BoU discount rate at the start of the year of income — a rate-table key, **unverified** until sourced) − interest paid
   - debt waiver: amount waived
   - property/services (incl. furniture): market value − employee payment
   - de-minimis: non-cash benefits < UGX 10,000/month are excluded (rate key)
3. **`calculate_paye`** (extend, do not fork): add optional `benefits: list[{kind, monthly_value}]` (values usually come from `value_benefit_in_kind`) added to `monthly_gross` before the bands. Add `compare_residency: bool` → also return the other residency's PAYE and the monthly difference. Keep the output backwards-compatible: existing keys unchanged, new keys added.
4. **`convert_currency`** — inputs: amount, currency, date (optional). Data: new `app/tax/data/fx/` snapshot (e.g. `BOU_mid_rates_2026.json`: monthly BoU mid-rates for USD, EUR, GBP, KES, with `source`, `retrieved_on`). Populate it from the Bank of Uganda's published rates; if you cannot obtain official figures, ship the file empty and make the tool return `ok: false, needs: "exchange_rate"`. The assistant then **asks the user for the rate** — never guess one. A user-supplied rate always wins. Search the corpus for the ITA currency-conversion rule (BoU mid-rate) and cite it in `legal_basis`; if not found, mark it unverified.
5. Add **at least one** rule tool for each head that has scenario coverage but no tool, prioritising by held-out *dev* failures: e.g. `objection_requirements` (deadline days, deposit share from `objection_timeline_days`, the TAT deadline), `penalty_late_filing`, `vat_input_claim_eligibility` (EFRIS e-invoice condition), and `wht_non_resident_service` (15 %, exclusion of employment contracts). Each follows the same data-driven pattern.

Rate-table changes go in **both** `FY2026-27.json` and `FY2025-26.json` where the rule applied in that year, each key with `legal_basis` and `notes`. Run the loader tests. If tool-count / Tool-RAG tests enumerate tools (`grep -rn "CALCULATOR_TOOLS\|tool_rag" App/backend/tests`), update them, including the 5-locale Tool-RAG descriptions if the registry requires them (added in #590).

Tests: `tests/test_tax_calculators.py` — every formula with hand-computed expectations **computed in the test from the table values** (not literals), the edge cases (0 days, exactly 183, 182, loan of exactly 1,000,000, the de-minimis boundary), and the unverified surfacing.

### F.5 Premise and contradiction checks (fixes D4 for all heads)
- In `app/premise_guard.py` add `check_fact_contradictions(facts: CaseFacts, tool_results: dict) -> list[Contradiction]`. Start with these rules and keep them data-driven (a list of small rule functions):
  - the user claims non-resident but `determine_residency` says resident, or the reverse
  - the user says "below the VAT threshold" but `annual_turnover` ≥ `vat_registration_threshold_annual`
  - the user expects an expense deduction for individual rental income
  - the user says WHT applies to an employment contract (non-resident service-contract rule)
- Each contradiction becomes a required bullet in the verified-facts block ("Your premise: X. Under <basis>, Y."). The prompt (G.4) requires the answer to address it first.
- **Do not** reintroduce the old false-premise over-extraction (the older plan's D5 bug: "non-resident tax table" read as a fake tax). Keep `check_false_premise` as is; this is a separate function.

### F.6 Scenario planner + verified-facts block (fixes D3, generalises D1/D4)
- New `app/scenario_planner.py`: `plan_tools(question: str, facts: CaseFacts) -> list[ToolCallSpec]`. Use declarative rules (a table of `when(facts, question) -> tool spec`):
  - days known or residency mentioned → `determine_residency`
  - a salary is known and PAYE is mentioned or implied → `convert_currency` (if not UGX) → for each benefit `value_benefit_in_kind` → `calculate_paye(benefits, residency from the tool, compare_residency=True when residency is asked about)`
  - rental amounts → `calculate_rental_tax` (reuse the existing pooling logic **moved out of** `_evaluate_calculation_context` into this planner, then delete the duplicate)
  - turnover + VAT → `check_vat_registration`; imported services → `wht_non_resident_service` + VAT reverse-charge rule, … (one rule per tool from F.4)
- Missing inputs produce `needs` entries instead of guessed values. With partial inputs, still run what can run and list what is missing.
- `service` integration: replace `_evaluate_calculation_context(query)` with `build_verified_facts(question, facts)`. It runs the plan through `get_client().call_tool(...)` (the MCP path used today) and renders one block:
  ```
  ## Verified Statutory Facts & Calculations (tool ground truth — use these figures verbatim)
  - Residency: RESIDENT — present ~240 days ≥ 183-day test (basis: …). User premise "non-resident" is contradicted.
  - Car benefit: UGX … / month (20% × A × B/C − D; A=…, B=…, C=…, D=0) (basis: …)
  - Housing benefit: UGX … / month (lesser of market rent … and 15% of employment income …) [interpretation unverified]
  - PAYE (resident, FY2026-27): UGX … / month on chargeable UGX …; non-resident would be UGX … (difference UGX …)
  - Still needed for an exact figure: market value of the car; market rent of the house; exchange rate on payment date.
  ```
  Inject it wherever `_evaluate_calculation_context` output is injected today (REST and streaming), **before** the passages.
- **Out-of-coverage disclosure:** if the question is about a tax head with no planner rule, or the planner returns nothing for a numeric question, add to the block: "No verified calculator covers <topic>; answer only from the passages, state any figure's source, and recommend confirming with URA." This is the generalisation safety net.
- **T3 tool loop (optional, behind `tool_use` which stays default-off):** when the planner returns nothing but the tier is T3, allow `llm.generate_with_tools(..., endpoint=T3)` restricted to the `tax_calculator` + `rates` namespaces. The figure lock (G.2) still applies.

Tests: `tests/agents/test_scenario_planner.py` — the §1 case end-to-end with mocked tools; one case per tax-head rule; the missing-inputs path; the out-of-coverage path; no duplicate rental logic left in `service.py`.

**PRs #6–#8** (keep each reviewable): `feat(tax): CaseFacts extraction and persistence`; `feat(tax): residency, benefit-in-kind, FX and per-head rule tools (data-driven)`; `feat(chat): scenario planner, verified-facts block, contradiction checks; explanation-vs-calculation routing`.

---

## 9. Phase G — Remaining "why it falls short" fixes

### G.1 Faithfulness that sees polarity (fixes D6)
- `retriever.compute_faithfulness` (≈ line 1840) and `claim_verifier.verify_claims` (≈ line 194) are overlap-only. Add `app/claim_polarity.py`:
  - a curated **direction lexicon** of opposing pairs that matter in tax: added/included ↔ subtracted/deducted/excluded/exempt; taxable ↔ exempt/not taxable; resident ↔ non-resident; allowed/claimable ↔ disallowed/not claimable; before ↔ after; increase ↔ decrease; lesser/lower ↔ greater/higher; must ↔ need not; within N days ↔ after N days
  - `polarity_conflicts(sentence, passages, verified_facts) -> list[Conflict]`: for a reply sentence that shares the content tokens of a supporting passage/fact sentence but uses the **opposite** term of a pair, report a conflict.
- Wire it into both: `compute_faithfulness` sets a conflicting sentence to **ungrounded**; `verify_claims` adds a `contradicted_claims` entry, and the decision becomes `revise`.
- Unit tests: the D6 sentence vs the handbook passage → conflict; a true paraphrase → none; ≥ 20 pairs.

### G.2 Figure lock (fixes D5 and generalises it)
- Extend the existing money-answer verification (`evaluator_optimizer` flag, "verify money-bearing answers by deterministic recomputation") rather than adding a parallel mechanism. Read its implementation first (`grep -n "evaluator_optimizer" -r App/backend/app`).
- Rule: every number in the reply that is a money amount, percentage, day count or band edge must appear in (tool outputs ∪ the verified-facts block ∪ the user's own message ∪ cited passages), after normalising formats (`7,725,500` = `7.73m` within ±0.5 %; `30%` = `0.3`). Unmatched numbers → `revise` with the list of offending numbers.
- Revision: one bounded reflection call **on the same tier's endpoint** (`_reflect_llm`, ≈ line 4817; give it `endpoint=` and a 60 s timeout). The instruction lists the offending numbers/claims and the verified block. If the revision still fails, **remove the offending sentences** and append "Some figures could not be verified; please confirm with URA." **Never** fall back to `_build_grounded_revision`'s canned FAQ dump for questions that carry user figures (older-plan finding; check whether that already landed).
- Turn `evaluator_optimizer` on for T3 turns once the held-out dev split shows ≥ 0 regression. Document it.

### G.3 Retrieval: sector-guide demotion + topic-section boost (fixes D7)
- In `retriever.apply_preference_boost` (≈ line 462) and `query.extract_retrieval_preferences` (≈ line 1382):
  - add `prefer["demote_sector_guides"] = True` unless the query names a sector (agriculture/farming, hotel/accommodation/tourism, mining, petroleum, construction, NGO, transport …). Demote hits whose `source` matches the sector-guide filename patterns (`GUIDE-TO-TAXATION-OF-THE-.*-SECTOR`, `Hotel-and-Accomodation`, …) by **−0.06**. A soft demotion, never a filter. Put the pattern list in one constant.
  - add topic → preferred-section hints: employment/PAYE/BIK/residency → handbook sections containing "Employment Income", "Benefits in Kind", "Resident" and `ura_employment_income_faqs.csv` (+0.05); VAT → `vat` FAQ and VAT Act sections, and so on. Drive it from a small dict.
- Measure with `scripts/eval_retrieval_multilingual.py` and the `Data/eval/rag_eval.jsonl` gate. **Any Hit@1 drop > 1 point fails the change; tune the numbers or drop the change.** (Memory: BM25 retuning made things worse before; measure, don't assume.)

### G.4 Prompt for complex answers
- In the system-prompt builder (`llm._build_messages` / `SYSTEM_PROMPT` in `app/llm.py`), add a **complex-answer contract**, used only when the verified-facts block is present or the tier is T3:
  1. Start with a 1–2 sentence direct answer.
  2. If the block lists a premise contradiction, address it first.
  3. "Assumptions" — list the facts used (and their source turn).
  4. Step-by-step with the **verified figures verbatim**; never compute new figures yourself.
  5. Legal basis — cite the `legal_basis` strings and passage markers.
  6. "What I still need for an exact figure" — from `needs`.
  7. Answer **every** sub-question in the user's message (enumerate them first internally).
- Keep the existing tone/EI behaviour (`tone_hint`, `text_signals.py`); courtesy sentences stay unscored.
- Remove the instruction pattern that produced "consult URA for exact figures" boilerplate when the verified block contains the exact figure. Keep the contact footer behaviour as it is.

**PRs #9–#10:** `fix(verify): polarity-aware faithfulness and figure lock with same-tier revision`; `fix(retrieval): demote sector guides for non-sector questions; topic-section boosts; complex-answer prompt contract`.

---

## 10. Phase H — Final evaluation

### H.1 Unit and regression tests
Run §0.5. **No new failures vs `BASELINE_FAILURES.txt`.** Also run `cd App/frontend && bun run test` if anything in the frontend changed (nothing should).

### H.2 Live verification beside the user's stack (isolated)
Follow memory `isolated-verification-api`. In summary:
- standalone compose project with a **unique service name** (e.g. `accverify-api`), not `api`, and not joined to the live network by base compose files
- **never start `qdrant-indexer`**; mount the Qdrant state read-only and share the live Qdrant for reads
- private Redis DBs 8/9 (`FLUSHDB` both afterwards, after checking the keys are yours), `SPEECH_ENABLED=false`, port bound to `127.0.0.1`
- its own GPU for the api (check `nvidia-smi`); T1 points at the live `vllm:8001`; T3 at `ura-app-vllm-reasoner` started from `docker-compose.reasoner.yml` on GPU 7 under its own project name
- env built from the live api's non-secret settings (drop secret-shaped names) plus `SOVEREIGN_MODE=true`, `FLAG_MODEL_TIERING=true`, `EVAL_DEBUG=1`, and the new flags under test

Then:
```bash
python3 scripts/eval_complex_scenarios.py --base-url http://127.0.0.1:<port> --split dev --label final
python3 scripts/eval_complex_scenarios.py --base-url http://127.0.0.1:<port> --split heldout --label final   # 2nd and last held-out run
```
Re-run the regression scripts from A.5 against the isolated api.

### H.3 Acceptance bar (report each line as PASS/FAIL with numbers)

| Metric (held-out) | Bar |
|---|---|
| Mean scenario score | ≥ baseline + 25 points **and** ≥ 80/100 |
| Critical forbidden-claim hits | 0 in tool-covered heads; ≤ 2 overall |
| Figure accuracy where a tool covers the head | ≥ 98 % |
| Premise-challenge rate (premise scenarios) | ≥ 90 % |
| Route correctness | ≥ 95 % |
| Truncated answers (`finish_reason` length served or mid-word) | **0** |
| Sector-guide citations on non-sector questions | ≤ 5 % |
| Per tax head | no head below its baseline; every head ≥ 60 |
| p95 latency | T1 ≤ 12 s, T3 ≤ 60 s; share of T3 turns ≤ `REASONER_MAX_SHARE` |
| 2,000-FAQ benchmark | no drop > 0.15 points vs baseline |
| Trilingual multi-turn + multilingual retrieval | no regression beyond each script's documented noise |

If a bar fails: report it honestly with the per-scenario failures. Fixing it means tuning on **dev** only; the held-out set may not be run a third time. If more work is needed, author a fresh held-out v2 for the next round.

### H.4 Report
`docs/Reports/opus-level-accuracy-results-2026-10.md`: baseline vs final per metric and per tax head, the bake-off summary, examples (the §1 conversation before/after verbatim), known limits (heads without tools, unverified rate keys, the unreviewed held-out expectations count), and the GPU footprint.

---

## 11. Phase I — Docs, governance, delivery, cleanup

### I.1 Documentation (same PRs as the code, per `docs/AGENTS.md`)
- **ADR** `docs/adr/0007-sovereign-local-reasoning-tier-and-code-owned-tax-logic.md` (MADR 4.0, like 0006), indexed in `docs/adr/README.md`. Decision: local Qwen3.8-27B-FP8 (or the bake-off winner) as T3 on GPU 7; Sunflower-9B stays T1 + MT; no commercial APIs; rules as data; the figure lock.
- `docs/MODEL_CARD.md`: both models, the tiers, the sovereign constraint, the evaluation results.
- `docs/RAG_ARCHITECTURE.md`: new flags (`reasoner_for_translated_locales`, any others), `SOVEREIGN_MODE`, updated `model_tiering` text, the verified-facts block, the figure lock, the sector demotion.
- `docs/MODEL_SWAP_GUIDE.md`: how to swap the T3 model (env vars, compose overlay, bake-off script).
- New runbook `docs/runbooks/reasoning-tier-and-complex-eval.md`: bring-up/down of `vllm-reasoner`, GPU choice, health checks, how to run the bake-off and the complex-scenario eval, the held-out rule, rollback (`FLAG_MODEL_TIERING=false`).
- `docs/GAPS_AND_AGENTIC_ROADMAP.md`: add G213+ entries (the last id is G212) for D1–D8, each linked to its PR, plus follow-ups: housing-rule interpretation review, the BoU discount rate source, single-model consolidation (contender C), heads still without tools, human review of the held-out expectations.
- `governance/ai_risk_manifest.yaml` and the AIBOM / model inventory (search `grep -rln "Sunflower-Qwen3.5-9B" governance docs`): add the reasoner model with licence, sha and hosting = local.
- `.env.example`, `configs/prototype.env`: new env vars (`REASONER_*`, `SOVEREIGN_MODE`, `EVAL_DEBUG` documented as dev-only, `LLM_MAX_TOKENS=768`).
- `evals/AGENTS.md` and `evals/README.md`: add the complex-scenario set row.

### I.2 PR order (each: branch → PR to `dev` → CI → CodeRabbit → merge)
1. evals + baseline · 2. bake-off report · 3. reasoner service + sovereign guard · 4. tier endpoints · 5. truncation · 6. CaseFacts · 7. tools · 8. planner/routing/contradictions · 9. verification · 10. retrieval + prompt · 11. final report + docs sweep.
After each push, re-check CodeRabbit threads **and** review-body comments (they do not show in `gh pr checks`). CI jobs gated on `refs/heads/main` do not run on `dev`; green `dev` ≠ green `main`. If a PR opens with no CI runs, push an empty commit.

### I.3 Deploy to the user's live stack (only after H.3 passes and the user agrees)
- The images bake the code: rebuild `app-api:gpu` from the merged `dev`, then bring up the overlay chain the live stack uses **plus** `docker-compose.reasoner.yml`. The live chain (from container labels) is `-f docker-compose.yml -f docker-compose.local-retrieval.yml -f docker-compose.local-sunflower.yml -f docker-compose.gpu-salt.yml -f docker-compose.connectors.yml`, compose project `app`, run from `App/`. **Ask the user before recreating their containers.** Remind them to remove the commercial keys from the env file before setting `SOVEREIGN_MODE=true`.
- Live smoke: re-run the §1 two-turn conversation and 5 dev scenarios against the ngrok URL; paste the before/after into the report.

### I.4 Cleanup
- Remove every bake-off/verification container you started (`docker rm -f <names>`), the private Redis DBs, and temp env files. Then `nvidia-smi` must show **zero** VRAM held by your experiment containers (the user's live stack excluded).
- Leave GPU 7 running `ura-app-vllm-reasoner` **only if** it was deployed to the live stack in I.3.

---

## 12. Appendices

### 12.1 Legal facts to encode — quoted from the corpus (verify against the file before use)

Note: `App/Data/*` (including `App/Data/pdf_jsonl/`) is **gitignored generated state**. The tracked sources are under `Data/` (`Data/pdfs`, `Data/dataset`, …). On a fresh checkout, seed `App/Data` from `Data/` (see `Data/README.md` and `docs/runbooks/corpus-coverage.md`) before quoting from it or measuring retrieval. In `basis` fields, cite the tracked source file name.

**(a) Residency** — `App/Data/pdf_jsonl/TAXATION-HANDBOOK-FY-2025-26-1.jsonl` (definitions):
> "A resident individual is a person who has a permanent home in Uganda; or is present in Uganda: for a period of 183 days or more in any twelve (12) months period that commences or ends during the year of income; or during the year of income and in each of the two preceding years of income, for periods averaging 122 days in each such year of income; or is an employee or official of the government of Uganda posted abroad during the year of income."
> "Year of Income — means the period of twelve months ending on June 30 …"

Also `Data/dataset/ura_exchange_of_information_faqs.csv` (AEOI residency FAQ). Resident persons are taxed on worldwide income, non-residents on Uganda-source income (`doing_business_in_uganda_taxation.pdf`). Section numbering: the corpus cites the Income Tax Act as **Cap 338** (2023 revised edition) in FY2025-26 documents and **Cap 340** in older ones. Cite the Cap the source uses.

**(b) Benefits in kind** — same handbook, §12.2.3 "Valuation of Benefits in Kind":
> General rule: "the value of a benefit in kind is the fair market value of the benefit on the date it is taken into account for tax purposes less any amount paid by the employee for the benefit."
> 1. Motor vehicle: "(20% × A × B/C) – D … A is the market value of the motor vehicle at the time when it was first provided for the private use of the employee, depreciated on a reducing balance basis at a rate of 35% per annum for the subsequent years; B is the number of days in the year of income in which the motor vehicle was used or available for use for private purposes …; C is the number of days in the year of income; D is any payment made by the employee for the benefit."
> 2. Domestic assistants: "the total employment income paid to the domestic assistants, reduced by any payment made by the employee."
> 3. Meals, refreshment or entertainment: "the cost … to the employer less any payment by the employee."
> 4. Utilities: "the cost of the utility to the employer less any payment by the employee."
> 5. "Low interest on loans exceeding one million shillings – … the difference between the interest paid by the employee and the interest payable using the statutory interest rate (Bank of Uganda discount rate at commencement of the year of Income)."
> 6. "Debt waiver – The benefit is the amount of the debt waived."
> 7. "Transfer or use of property or provision of services (Furniture and transportation) – The benefit is the market value of the property or services less any payments by the employee."
> 8. Accommodation/housing: "the lesser of: a) the market rent of the accommodation or housing reduced by any payment made by the employee for the benefit; or b) 15% of the employment income, including the amount referred to in paragraph (a), paid by the employer to the employee for the year of income …"
> 9. "Any other benefit – … market value of the benefit less any payments by the employee."
> Relief: "Any non-cash benefits whose value is less than Shs. 10,000 a month" are excluded.

Benefits are **added** to employment income (`ura_employment_income_faqs.csv`: employment income includes "benefits in kind"; housing benefits "form part of taxable employment income and are subject to PAYE").

**(c) PAYE bands (monthly), `App/backend/app/tax/data/FY2026-27.json`** — read them from the table at run time; never copy them into code or tests.
- Resident (Income Tax (Amendment) Act 2026, s.20(a), Schedule 4 Part I): nil to 335,000; 20 % to 410,000; 25 % to 485,000; 30 % to 10,000,000; 40 % marginal above (= 30 % + 10 % additional).
- Non-resident (Cap 338 Schedule 4 Part II, not amended in 2026): 10 % to 335,000; 20 % to 410,000; 30 % to 10,000,000; 40 % marginal above.
- Consequence (useful as a check): above UGX 485,000/month a non-resident pays exactly **UGX 37,250/month more** than a resident (48,500 + 30 % × 75,000 = 71,000 vs 33,750).
- The FY2025-26 table differs (the resident nil band ended at 235,000). Use the table for the fiscal year in question.

**(d) Other corpus facts used by planner rules** — find and quote each from the corpus when you write the rule: WHT on non-resident service contracts at 15 %, and "A Ugandan-sourced service contract does not include an employment contract" (`Withholding-Tax-FY-2024-25-1.pdf`); rental pooling / threshold / no individual expense deduction (rate-table keys `rental_tax_individual*`); objection timeline and TAT deadline (rate-table keys `objection_timeline_days`, `tax_appeals_tribunal_deadline_days`); PAYE due by the 15th of the following month (`paye_due_date_monthly`).

### 12.2 Unverified items (keep them in the table's `unverified` list until a human confirms)
- the housing (b) "15 % of employment income including (a)" interpretation
- the statutory interest rate for low-interest loans (BoU discount rate at the start of the year of income) — the value and its source
- the FX snapshot values and the conversion-rule citation
- anything you could not quote from the corpus

### 12.3 Wrong facts — never reproduce them (several appear in older plans or in bot answers)
- ✗ "Non-residents are taxed 10 % on the first UGX 4,020,000" — the 4,020,000 figure is the **annual resident** nil threshold (335,000 × 12); the non-resident first band is 10 % on 0–335,000 **per month**.
- ✗ "BIK valuation is in the Fifth Schedule" — use the handbook §12.2.3 / ITA employment-income provisions as cited by the corpus.
- ✗ "8 months is less than 183 days."
- ✗ "Housing benefit is subtracted from gross salary."
- ✗ "Residents use a simplified flat rate."
- ✗ "Route hard questions to Gemini / Cloudflare Llama-70B / any hosted model" (forbidden, §0.2).
- ✗ `MODEL_T3=Qwen3-235B-A22B` — not served and does not fit the hardware.

### 12.4 Scenario JSONL schema (one object per line)
```json
{
  "id": "paye_expat_benefits_resident_premise_01",
  "split": "heldout",
  "tax_head": "paye",
  "difficulty": "hard",
  "reviewed": false,
  "description": "Expat on 8-month contract with car + house; user assumes non-resident.",
  "turns": [
    {
      "message": "We're bringing in a foreign engineer for about 8 months in Entebbe, paying 6k dollars a month plus a house and a car. How do we do PAYE for a non-resident, and is their table different?",
      "expect": {
        "route": "rag",
        "premise_flag": true,
        "premise_cues": [{"any": ["resident", "183"]}],
        "must_include": [
          {"any": ["183 days", "183-day"]},
          {"any": ["benefit in kind", "benefits in kind", "BIK"]},
          {"any": ["exchange rate", "convert"]}
        ],
        "must_not_include": [
          {"text": "flat rate", "severity": "critical"},
          {"text": "less than 183", "severity": "critical"}
        ],
        "figures": [
          {"tool": "calculate_paye", "params": {"monthly_gross": "${FX:USD:6000}", "residency": "resident"}, "field": "paye", "required": false}
        ],
        "basis": ["TAXATION-HANDBOOK-FY-2025-26-1 › definitions › resident individual"]
      }
    },
    {
      "message": "and how does URA actually value the furnished house and the car for PAYE?",
      "expect": {
        "route": "rag",
        "must_include": [
          {"any": ["20%", "20 per cent"]},
          {"any": ["lesser of", "lower of"]},
          {"any": ["15%"]},
          {"any": ["added to", "included in", "part of"]}
        ],
        "must_not_include": [
          {"text": "subtracted from", "severity": "critical"},
          {"text": "What is your gross monthly salary", "severity": "critical"}
        ],
        "basis": ["TAXATION-HANDBOOK-FY-2025-26-1 › 12.2.3 Valuation of Benefits in Kind"]
      }
    }
  ]
}
```
- `${FX:USD:6000}` is resolved by the scorer via `convert_currency` when available. Otherwise the figure is skipped as `fx_unavailable` (`required: false`).
- `route` values: `calculator` (retrieval_mode `calculator`), `workflow` (`workflow`), `rag` (`hybrid`/`dense`/`bm25`/…), `declined` (jurisdiction or out-of-scope reply). The mapping lives in `scoring.py`.

### 12.5 Command cheat-sheet
```bash
# live probe (one turn)
python3 - <<'EOF'
import json, urllib.request, uuid
u = "https://struttingly-nongeological-briella.ngrok-free.dev/api/v1/chat"
b = json.dumps({"message": "…", "conversation_id": "probe-" + uuid.uuid4().hex[:8]}).encode()
r = urllib.request.Request(u, data=b, headers={"content-type": "application/json", "ngrok-skip-browser-warning": "1"})
print(json.dumps(json.load(urllib.request.urlopen(r, timeout=240)), indent=1)[:4000])
EOF
# which GPUs are free
nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv
# which project containers run where
for c in ura-app-vllm-sunflower ura-app-api ura-app-orpheus-tts; do docker inspect -f "$c {{range .HostConfig.DeviceRequests}}{{.DeviceIDs}}{{end}}" $c; done
# show env var NAMES only (never values)
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' ura-app-api | cut -d= -f1 | sort
```

### 12.6 Definition of done (whole plan)
- All 11 PRs merged to `dev` with green CI and resolved CodeRabbit comments.
- The held-out acceptance bar (§10.3) passes, or the report states exactly which lines fail and why.
- The §1 conversation, replayed live, gives: "likely resident (≈240 days ≥ 183)"; PAYE computed for resident with the non-resident comparison (difference UGX 37,250/month above 485k); the car and housing benefit rules with their direction (added); an explicit request for the car's market value, the house's market rent and the exchange rate (or the BoU rate used, with its date); no truncation; no sector-guide citations; and turn 2 answered as an explanation, not a wizard.
- No commercial AI endpoint is reachable from a `SOVEREIGN_MODE=true` deployment.
- Docs, ADR, runbook, model card, gap register and risk manifest are updated; experiment GPUs are released.

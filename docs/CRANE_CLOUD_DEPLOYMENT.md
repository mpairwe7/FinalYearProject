# Crane Cloud Deployment — URA Chatbot (App)

This document covers deploying the **URA Chatbot** (`App/`) to the
[Crane Cloud](https://cranecloud.io) RENU cluster — the same platform on which
the sibling capstone apps (Musawo, Magezi, HustleCoach) already run.

Crane Cloud has two operational constraints that shape the deployment shape:

1. **No persistent volumes** — anything stateful (Qdrant data, model
   weights, BM25 indexes, knowledge-base PDFs) must be baked into the
   image at build time.
2. **One service per app** — Crane Cloud routes a single public URL to
   a single container exposing one port. Multi-container Compose stacks
   (which the App uses locally) collapse into one image with
   `supervisord` orchestrating the processes.

The compose stack in `App/docker-compose.yml` (api + frontend + qdrant + redis)
is therefore **not** what Crane Cloud runs. Crane Cloud uses a dedicated
`Dockerfile.cranecloud` that bundles `nginx + uvicorn + Next.js standalone + a
loopback sparse Qdrant sidecar` under `supervisord`.

---

## 1. Production summary

> **Last verified:** 2026-05-25 (initial WS smoke against live URL — all
> Phase 0–6 frames assert green, TTLB 557 ms)

| Field | Value |
|---|---|
| Public URL | `https://ura-chatbot-6318a1b5.renu-01.cranecloud.io` |
| WS endpoint | `wss://ura-chatbot-6318a1b5.renu-01.cranecloud.io/v2/chat/stream` |
| Crane Cloud project | URA Chatbot (`10c4afe6-8083-422e-bf96-339991891de8`) |
| Crane Cloud app ID | `b01219c6-9555-41e2-84c3-f15d764fb938` |
| Image (verified) | `landwind/ura-chatbot:cranecloud-test` (public, 675 MB) |
| Container port | `8080` (nginx) → internal `8081` (uvicorn) + `3000` (Next.js standalone) |
| Cluster | RENU (`9e81a70e-8460-4e5d-b0a8-17abcac30f68`) |
| Source | `App/` subtree of `github.com/mpairwe7/FinalYearProject` |
| LLM backend | `vllm` (external) or `groq` fallback (free tier) |
| Retrieval | Embedded sparse Qdrant (BM25 vectors, versioned alias) |

> **Why not run Qwen3-8B locally on Crane Cloud?** Crane Cloud pods do not
> attach GPUs and the default RAM budget (~4–8 GB) cannot host an 8 B
> parameter model even at 4-bit quantization. The App ships two
> non-local LLM paths that satisfy this — `LLM_BACKEND=vllm` (point at a
> separately hosted vLLM endpoint) or a Groq/Claude API fallback added
> for the Crane Cloud build.

---

## 2. Architecture: local vs Crane Cloud

```
LOCAL (docker compose --profile dev up in App/)
┌────────────┐    ┌────────────┐    ┌──────────┐    ┌────────┐
│  Next.js   │───▶│  FastAPI   │───▶│  Qdrant  │    │ Redis  │
│  :3032     │    │  :8083     │    │  :6333   │    │ :6379  │
└────────────┘    └────────────┘    └──────────┘    └────────┘
       └──────── docker bridge: app-network ────────────────┘

CRANE CLOUD (single container, port 8080)
┌────────────────────────────────────────────────────────────┐
│ supervisord                                                │
│  ├─ nginx :8080 ──┬── proxy_pass /api/* → uvicorn :8081    │
│  │                └── proxy_pass /     → node :3000        │
│  ├─ qdrant :6333 (loopback, sparse-only BM25 collection)  │
│  ├─ uvicorn :8081 (FastAPI, sparse retrieval, LLM dispatch)│
│  └─ node :3000    (Next.js standalone build)               │
│                                                            │
│ Baked-in: Qdrant storage, BM25 state, knowledge base       │
│ External: vLLM endpoint OR Groq API for LLM generation     │
└────────────────────────────────────────────────────────────┘
```

The local stack is multi-container for fast iteration (Qdrant restarts
quickly, Redis is shared between rate-limit and semantic cache). The
Crane Cloud stack collapses these because the platform cannot run
multi-container deployments and has no persistent volumes.

---

## 3. The Crane Cloud Dockerfile (`App/Dockerfile.cranecloud`)

Modeled on `Musawo/Dockerfile.cranecloud.optimized` (which reduced the
sibling image from 5.5 GB to 1.2 GB). Three stages:

| Stage | Base | Purpose |
|---|---|---|
| 1. Python builder | `python:3.11-slim-bookworm` | Install backend deps into `/opt/venv`, aggressive cleanup (`__pycache__`, `tests/`, `*.dist-info`) |
| 2. Frontend builder | `oven/bun:1.3.12-slim` | `bun install --frozen-lockfile && bun run build` (Next.js standalone output) |
| 3. Runtime | `python:3.11-slim-bookworm` | Add nginx + supervisord + Node 20, copy `/opt/venv` and Next.js `.next/standalone`, bake BM25 state and knowledge base into `/app` |

Build the image locally:

```bash
cd App
docker build -t landwind/ura-chatbot:latest -f Dockerfile.cranecloud .

# Verify size — should be <1.5 GB after cleanup
docker images landwind/ura-chatbot:latest --format '{{.Size}}'
```

Test the bundled image before pushing:

```bash
docker run --rm -p 8080:8080 \
  -e LLM_BACKEND=groq \
  -e GROQ_API_KEY=gsk_your_key \
  -e GROQ_MODEL=llama-3.3-70b-versatile \
  landwind/ura-chatbot:latest

curl -sS http://localhost:8080/health           # nginx → uvicorn /health
curl -sS http://localhost:8080/api/v1/speech/health
curl -sS http://localhost:8080/ -o /dev/null -w '%{http_code}\n'  # frontend 200
```

Push:

```bash
docker push landwind/ura-chatbot:latest
```

---

## 4. nginx routing (baked into the image)

`/etc/nginx/sites-available/default` inside the image:

```nginx
server {
    listen 8080;

    # SSE streaming chat — disable proxy buffering so tokens flush
    location ~ ^/(v1/chat/stream|api/v1/chat/stream)$ {
        proxy_pass http://127.0.0.1:8081;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
        proxy_set_header Connection "";
        proxy_http_version 1.1;
    }

    # WebSocket voice streaming — Phase 23/28
    location ~ ^/(v1|v2)/voice/chat/stream$ {
        proxy_pass http://127.0.0.1:8081;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }

    # WebSocket text chat — Phase 29 (FLAG_WS_CHAT).  Mirrors the voice
    # WS block: must come BEFORE the catch-all backend route below so
    # that the Upgrade headers are honoured.  60-min session cap matches
    # the OpenAI Responses-API WS cap and the server-side enforcement.
    location = /v2/chat/stream {
        proxy_pass http://127.0.0.1:8081;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    # All other backend paths (including /api/* for the Next.js proxy)
    location ~ ^/(health|ready|metrics|classify|classify/batch|tags|faq/|v1/|v2/|api/) {
        rewrite ^/api/(.*)$ /$1 break;
        proxy_pass http://127.0.0.1:8081;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }

    # Frontend — Next.js standalone server
    location / {
        proxy_pass http://127.0.0.1:3000;
    }

    gzip on;
    gzip_types text/plain application/json application/javascript text/css;
}
```

Two routing styles are accepted:

- `https://ura-chatbot.../v1/chat` — direct backend (compat with sibling apps)
- `https://ura-chatbot.../api/v1/chat` — Next.js proxy style (what the
  shipped frontend bundle calls)

This dual routing lets the same image work whether the client is the
bundled UI or an external integrator hitting the bare API.

---

## 5. Environment variables on Crane Cloud

Set these in the Crane Cloud app dashboard → **Environment variables**:

| Key | Example value | Required | Purpose |
|---|---|---|---|
| `APP_ENV` | `production` | yes | Triggers `_validate_production_env()` in `main.py` |
| `PORT` | `8081` | yes | Backend port behind nginx |
| `LOG_LEVEL` | `info` | yes | App logger level |
| `LLM_BACKEND` | `vllm` or `groq` | yes | Never `local` on Crane Cloud — no GPU |
| `VLLM_BASE_URL` | `https://vllm.example.com/v1` | if `vllm` | OpenAI-compatible endpoint |
| `VLLM_API_KEY` | `<secret>` | if vLLM requires it | Forwarded as Bearer token |
| `GROQ_API_KEY` | `gsk_...` | if `groq` | Free tier, ~500 tok/s |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | if `groq` | Or `qwen/qwen3-32b` |
| `QDRANT_ENABLED` | `false` | yes | Skip Qdrant init; force BM25 fallback |
| `BM25_STATE_PATH` | `/app/Model/bm25_state.json` | yes | Baked-in BM25 index |
| `DATA_DIR` | `/app/Data/dataset` | yes | Knowledge base CSVs |
| `CORS_ORIGINS` | `https://ura-chatbot-<hash>.renu-01.cranecloud.io` | yes | Strict origin allowlist |
| `RATE_LIMIT` | `30/minute` | yes | slowapi rate limit |
| `INDEX_API_KEY` | `<openssl rand -hex 32>` | yes | Protects `/v1/index`, `/v1/evaluate` |
| `STORE_RAW_PROMPTS` | `false` | yes | NDPA §19 data minimisation |
| `LLM_TRUST_REMOTE_CODE` | `false` | yes | OWASP LLM03 supply-chain |
| `SPEECH_ENABLED` | `true` | enabled in this deployment | Cloud (Sunbird) speech mode; see §7. `false`/unset → speech routes 503. |
| `SUNBIRD_API_TOKEN` | `<token>` | required if speech on | Cloud ASR/TTS/MT for Ugandan languages (§7.1). |
| `FLAG_VOICE_CONSENT` | `true` | required if speech on | NDPA consent gate; blocks boot if speech on + `APP_ENV=production` without it (§7.1). |
| `FLAG_WS_CHAT` | `false` | optional | Enable `/v2/chat/stream` WebSocket text chat (Phase 29). See §13. |
| `WS_CONFIRM_HMAC_SECRET` | `<openssl rand -hex 32>` | if `FLAG_WS_CHAT=true` | HMAC secret for HITL tool-call confirmation tokens. Required when WS chat is enabled in production. |
| `LLM_TOOL_MAX_ITER` | `10` | optional | Per-turn agentic tool-call cap (1–20). Default 10. |
| `AGENTIC_TURN_DEADLINE_S` | `120` | optional | Per-turn deadline in seconds (0 disables). Default 120. |
| `WS_CHAT_MAX_PER_USER` | `5` | optional | Max concurrent WS chat sockets per user_id. Default 5. Per-replica only — see §13. |

**Production gate**: with `APP_ENV=production` set, the app refuses to
start if `AUTH_DEV_SECRET` is still the default, if `CORS_ORIGINS`
contains `localhost`, if `LLM_TRUST_REMOTE_CODE=true`, or if `INDEX_API_KEY`
is the dev placeholder. See `_validate_production_env()` in `App/backend/app/main.py`.

To deploy without OIDC initially (closed-beta usage with the operator key),
set:

```
FLAG_AUTH_REQUIRED=false
AUTH_ALG=HS256
AUTH_DEV_SECRET=<openssl rand -hex 32>
```

When OIDC is wired up later, flip to `RS256` and add `OIDC_ISSUER`,
`OIDC_AUDIENCE`, `OIDC_JWKS_URL`.

---

## 6. Retrieval on Crane Cloud (embedded Qdrant)

The image starts a loopback Qdrant sidecar with `QDRANT_ENABLED=true` and a
sparse-only collection. The retriever computes the BM25 query vector from the
state embedded in the collection, avoiding the dense model and its GPU-sized
dependencies. If the sidecar becomes unavailable after start-up, the existing
keyword fallback remains available and `/ready` reports `degraded`.

Knowledge base files baked into the image:

```
/app/Data/dataset/*.csv          # source FAQ CSVs (the JSONL is exported from these)
/app/Data/faq_jsonl/*.jsonl      # canonical FAQ corpus — what actually seeds Qdrant
/app/Data/teacher_qa/*.jsonl     # normalised teacher-QA pairs
/app/Data/pdf_jsonl/*.jsonl      # hierarchical PDF chunks
/app/Data/crawl_jsonl/*.jsonl    # crawled-page chunks
/app/Model/bm25_state.json       # BM25 posting list + IDF, rewritten by the index build
```

The 500 MB of source PDFs and crawl pages are **not** in the image — only the
derived JSONL is, which is why the build sets `CORPUS_TRUST_MANIFEST=true` and
skips the source-hash comparison alone.

The Dockerfile performs the safe build during image creation: it creates a
versioned candidate, validates source-hash and retrieval canaries, then promotes
the image's alias. Prepare the generated JSONL inputs before each release:

```bash
cd App/backend
PYTHONPATH=. python -m app.indexer --export-faq-jsonl
PYTHONPATH=. python -m app.indexer --export-pdf-jsonl     # optional, minutes
PYTHONPATH=. python -m app.indexer --export-crawl-jsonl   # optional, seconds
```

**Do not fit the sparse state on its own.** BM25 token ids are assigned in
first-seen order, so a state file fitted separately from the vectors it is paired
with produces a desynced inverted index. `build_index` stamps the corpus hash
into the collection and `HybridRetriever._verify_bm25_binding` disables sparse
retrieval when the two disagree — so a standalone refit degrades search silently
rather than failing loudly at build time.

Do not commit generated `Model/bm25_state.json`: the Docker build derives it
from the release corpus and embeds a matching copy in Qdrant.

### 6.1 Vectorize is preferred over the sidecar — and must fall back to it

`initialize()` ranks backends: Qdrant-with-dense, then Cloudflare Vectorize,
then **sparse-only Qdrant**, then keyword. The baked collection is sparse-only
(`vectors: {}`, one `sparse` vector), so on any deployment where Cloudflare is
configured the retriever deliberately picks Vectorize over the sidecar — a
dense-only index beats BM25 alone. `/ready` then reports `retrieval_mode:
"vector"`, which is **not** a fault: it does not mean Qdrant failed.

What *was* a fault is what happened next. `search()` treated `_vectorize_mode`
as an unconditional early return, so when Vectorize produced nothing — open
circuit, exhausted neuron budget, failed request, or an index that answers no
query — the caller got `[]` and degraded to keyword search over the FAQ CSVs,
while the sparse-only Qdrant collection `initialize()` had deliberately kept
alive sat healthy in the same container holding the whole corpus.

Both CPU deployments were observed doing exactly this: `/ready` reporting
`vector` while every real query answered `retrieval_mode: keyword` from 499 FAQ
rows instead of 7,600+ documents. Nothing surfaced it, because the backend had
been *selected* — the health field reports selection, not whether it ever served
a query. `search()` now falls through to the Qdrant path when Vectorize returns
nothing, at no measured latency cost (a failed Vectorize call returns
immediately; the sidecar answers in single-digit ms).

**Operational lever.** Setting `DENSE_FALLBACK_BACKEND=none` (also `off`,
`disabled`, `false`, `0`) skips the Vectorize tier entirely, so `initialize()`
settles straight onto the sparse-only sidecar. Use it on a deployment whose
Vectorize index is unseeded or whose Cloudflare egress is unreliable — it needs
only an env change and a restart, not a new image.

**Reading the health field.** `/ready` distinguishes four states, and only the
first two mean dense retrieval is live:

| `retrieval_mode` | Meaning |
| :--- | :--- |
| `hybrid` | Qdrant with a dense vector — dense + BM25 + rerank |
| `vector` | Cloudflare Vectorize — dense-only, client-side lexical re-score |
| `sparse` | Sparse-only Qdrant — BM25 over the full corpus, no dense half |
| `keyword` | No retriever — FAQ CSVs only. **Degraded.** |

Images built before ~13 Aug 2026 report only `hybrid`/`keyword` and will say
`hybrid` while actually serving sparse-only; the four-way field is newer.

### 6.2 Build gate: `verify-embedded-stores.sh`

Neither embedded store is fail-closed at runtime, and that is deliberate — a
degraded pod beats a dead one. `wait-for-qdrant.sh` starts the backend anyway on
timeout, and `cache.py` falls back to in-process memory when Redis is
unreachable. The cost is that a broken store produces **no startup failure to
notice**: the Space served keyword-only answers over 499 FAQ rows for a full
minute after a roll before anyone spotted it, and the response cache silently
returned `None` for weeks.

`App/deploy/cranecloud/verify-embedded-stores.sh` moves both failures to build
time. It runs as the last `RUN` in the Dockerfile, after the operational `ENV`
block so it checks the values the image will really use, and fails the build on:

| Fault | Message |
| :--- | :--- |
| Collection promoted with no FAQ rows | `the collection holds ZERO faq_jsonl points` |
| FAQ corpus only partly loaded | `only N faq_jsonl points indexed, below the floor of M` |
| Dangling alias, or no collection | `neither an alias nor a collection named '…' exists` |
| FAQ JSONL missing from the image | `no FAQ JSONL corpus at /app/Data/faq_jsonl` |
| `redis-server` not installed | `redis-server is not installed in this image` |
| Redis rejects supervisord's flags | `redis-server did not start with the flags supervisord uses` |
| Cache and rate-limit sharing one db | `… share a keyspace; they must be separate databases` |
| `maxmemory` unset or wrong policy | `maxmemory is unset` / `expected 'allkeys-lru'` |

The FAQ floor defaults to **90% of the rows on disk** (`FAQ_INDEX_MIN_ROWS`
overrides it). It is a floor rather than an equality check because ingest drops
exact-duplicate questions — currently 508 rows on disk against 499 indexed.

It is also runnable against a live pod, which is the quickest post-roll check
that retrieval is not silently degraded:

```bash
kubectl exec <pod> -- /usr/local/bin/verify-embedded-stores.sh
```

When Qdrant is already serving it verifies that instance in place. When it has
to start one — the build case — it serves from a throwaway copy of the storage,
because starting Qdrant materialises its sparse segment files (~1 MB on disk
growing to ~36 MB) and every touched file would otherwise land in the image
layer.

---

## 7. Speech on Crane Cloud

> **Status (last verified 2026-06-17):** speech is **ENABLED** in the live
> deployment in "Cloud (Sunbird)" mode. `GET /v1/speech/health` →
> `{"enabled":true,"status":"ready"}`. All STT/TTS/MT/voice-chat checks pass
> for English and Luganda — see §7.3.

The local Whisper/Piper stack is intentionally **not** shipped on Crane
Cloud (no GPU, image bloat). The 296 MB image carries no local ASR/TTS
models, so every speech call is delegated to cloud providers. Two modes:

| Mode | Setup | Behavior |
|---|---|---|
| Off | `SPEECH_ENABLED=false` (or unset → `app.state.speech=None`) | `/v1/asr`, `/v1/tts`, `/v1/translate`, `/v1/voice/chat` all return **503** (`get_speech_model`, `main.py:471`); text chat unaffected |
| Cloud (Sunbird) | `SPEECH_ENABLED=true` + `SUNBIRD_API_TOKEN` (+ `FLAG_VOICE_CONSENT=true`, `USE_DOH=true`) | ASR/TTS delegated to Sunbird AI; MT to Gemini (via Cloudflare AI Gateway) or Sunbird NLLB |

### 7.1 Required environment variables

| Key | Value | Why |
|---|---|---|
| `SPEECH_ENABLED` | `true` | Builds the `SpeechModel` at startup; without it every speech route 503s. |
| `SUNBIRD_API_TOKEN` | `<token>` | Primary Sunbird account — cloud ASR/TTS/MT for Ugandan languages. `SUNBIRD_API_URL` defaults to `https://api.sunbird.ai`. |
| `SUNBIRD_FALLBACK_API_TOKEN` | `<token>` | Optional second Sunbird account for resilience — a request that fails on the primary token is automatically retried on this one (`sunbird._post`). `is_available()` is true if **either** token is set. |
| `FLAG_VOICE_CONSENT` | `true` | NDPA consent gate. **Hard-required when `SPEECH_ENABLED=true` AND `APP_ENV=production`** (`_validate_production_env`, `main.py:258`) — the app refuses to boot otherwise. With it on, anonymous `/v1/asr` & `/v1/voice/chat` calls must send header `X-Voice-Consent: true` (`main.py:312`). |
| `USE_DOH` | `true` | RENU pods have no upstream DNS; the DoH resolver (1.1.1.1) is what lets the pod resolve `api.sunbird.ai`. Already set for the LLM egress path. |

MT/translation additionally uses the **Gemini** path when these are present
(they already are in this deployment): `FLAG_CLOUDFLARE_FALLBACK=true`,
`TRANSLATE_FALLBACK_BACKEND=gemini`, `GEMINI_API_KEY`, `GEMINI_MODEL`,
`CLOUDFLARE_ACCOUNT_ID`, `CF_AIG_GATEWAY`, `CF_AIG_TOKEN`. Gemini is **not**
an STT/TTS provider — it only serves translation.

### 7.2 Setting the env vars via the Crane Cloud API (operational gotcha)

Env vars can be set in the dashboard, or via the REST API. Two traps:

1. **Omit the `image` field when updating only env vars.** A `PATCH /apps/{id}`
   that includes an `image` (even the *current, unchanged* tag) makes Crane
   Cloud re-validate the tag against Docker Hub's web API
   (`hub.docker.com/v2/namespaces/landwind/repositories/ura-chatbot/tags/<tag>`).
   When that lookup is unreachable/lagging the whole PATCH fails with
   **HTTP 500 `Max retries exceeded ... hub.docker.com`** and nothing is
   applied. Sending **only** `{"env_vars": {...}}` skips image validation and
   succeeds, leaving the running image untouched.
2. **Send the complete merged env dict.** `GET /apps/{id}` first
   (env lives at `data.apps.env_vars`), merge your keys into that dict, and
   PATCH the whole thing — partial PATCHes don't reliably overwrite existing
   keys, and a bare replacement would drop the other ~19 vars and break the
   production gate.

```bash
# token via POST /users/login {email,password} -> data.access_token
# (See §13 for the login flow. From a network where api.cranecloud.io has a
#  valid cert; the RENU edge serves a *.renu-01 cert, so a sandbox may need to
#  TLS to a *.renu-01 host and route with `Host: api.cranecloud.io`.)
curl -sf -X PATCH "https://api.cranecloud.io/apps/$APP_ID" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"env_vars": { <full merged dict incl. SPEECH_ENABLED/SUNBIRD_API_TOKEN/FLAG_VOICE_CONSENT> }}'
# NOTE: no "image" key — that is deliberate.
```

The env change bumps the deployment revision and triggers a rolling pod
restart on its own (no image change needed). Poll `/v1/speech/health` until
`{"enabled":true,"status":"ready"}`.

### 7.3 Audit / QA verification

Run the speech smoke against the live URL (companion to `live_smoke.sh`,
which covers only text chat):

```bash
BACKEND_URL=https://ura-chatbot-6318a1b5.renu-01.cranecloud.io \
  bash App/scripts/live_speech_smoke.sh
```

It exercises `/v1/speech/health`, TTS (en+lg), STT round-trip (en+lg),
`/v1/translate` (both directions), and `/v1/voice/chat` (en+lg), asserting
HTTP 200, `error=null`, audio that decodes to valid WAV, and
language-appropriate transcripts. Expected serving backends:

| Route | Expected `backend` |
|---|---|
| `/v1/tts` en | `edge_tts` (`en-US-AriaNeural` neural — requires `edge-tts` in the image, added 2026-06) |
| `/v1/tts` lg | `sunbird_cloud` (native speaker 248) |
| `/v1/asr` en & lg | `sunbird_cloud` |
| `/v1/translate` en↔lg | `gemini_flash` (Gemini via CF AI Gateway) |
| `/v1/voice/chat` en | asr `sunbird_cloud`, tts `edge_tts` |
| `/v1/voice/chat` lg | asr `sunbird_cloud`, mt `gemini_flash`, tts `sunbird_cloud` |

**Known caveats (not failures):**
- **English TTS uses edge-tts neural voices.** English synthesizes with
  `edge_tts` (`en-US-AriaNeural` by default; override with `SPEECH_EN_EDGE_VOICE`).
  This needs the `edge-tts` package in the image (added 2026-06 via
  `requirements-cranecloud.txt`). If its egress to Microsoft fails it falls
  back to Sunbird's non-English voice — which mispronounces acronyms (an early
  round-trip rendered "VAT" as "fertilizer") — so **watch the `backend` field**:
  `edge_tts` = good, `sunbird_cloud` for English = the degraded fallback.
  Luganda TTS uses Sunbird's native speaker 248 directly (good quality).
- **Latency is network-bound.** Single STT/TTS calls run ~5–8 s (incl. Sunbird
  modal cold start); a full `/v1/voice/chat` round-trip ~30–34 s. Do **not**
  advertise sub-1s targets.

Voice WebSocket endpoints (`/v1/voice/chat/stream`, `/v2/voice/chat/stream`)
require `FLAG_VOICE_STREAMING=true` and a working `SpeechModel`; they work in
Sunbird cloud mode but are similarly network-bound.

### 7.4 English STT/TTS via Cloudflare Workers AI (optional)

Route **English** speech to Cloudflare Workers AI (Luganda stays on Sunbird —
the CF audio models are English-strong). Models are **env-configurable**, so you
can swap them (e.g. to Deepgram) without code changes. For English the TTS chain
is **Aura-2-en → MeloTTS → edge_tts → Sunbird** and STT is **whisper-large-v3-turbo
→ Sunbird** — every tier is retained for resilience (circuit-breaker + budget
gated), degrading down the chain on failure.

| Env | Default | Notes |
|---|---|---|
| `FLAG_CLOUDFLARE_FALLBACK` | `true` | master switch for all CF fallbacks |
| `STT_FALLBACK_BACKEND` | `workers_ai` | enables CF STT for English |
| `STT_FALLBACK_MODEL` | `@cf/openai/whisper-large-v3-turbo` | or `@cf/deepgram/nova-3`, `@cf/deepgram/flux` |
| `TTS_FALLBACK_BACKEND` | `workers_ai` | enables CF TTS for English |
| `TTS_FALLBACK_MODEL` | `@cf/deepgram/aura-2-en` | **primary** English TTS — Deepgram Aura-2, context-aware/natural (returns MP3) |
| `TTS_FALLBACK_MODEL_2` | `@cf/myshell-ai/melotts` | resilience fallback — MeloTTS (returns WAV) |

Request/response shapes differ by model family and are handled in
`app/providers/gateway.py` (`workers_ai_stt`/`workers_ai_tts`): original
`@cf/openai/whisper` takes raw bytes; `whisper-large-v3-turbo` takes JSON
`{"audio": base64}`; MeloTTS JSON `{prompt,lang}` → base64 WAV; Deepgram Aura
JSON `{text}` → binary MP3. ("grok-voice" is **not** a Cloudflare model.)

Verify: `python -m pytest backend/tests/test_providers.py -k "CfWorkersTts or CfGatewayDispatch"`
(mocked) and, for a real round-trip,
`CF_LIVE_TEST=1 python -m pytest backend/tests/test_providers.py -k CfLive` —
or the speech smoke, where the English backend then reports `cf_workers_ai`.

---

## 8. Deploy procedure

```bash
# 1. From a clean checkout, run the local release gate first
cd App
bash scripts/release_gate.sh

# 2. Rebuild BM25 state (Section 6) and commit the updated JSON

# 3. Build and push the Crane Cloud image
docker build -t landwind/ura-chatbot:latest -f Dockerfile.cranecloud .
docker push landwind/ura-chatbot:latest

# 4. Crane Cloud dashboard: create/update app
#    - Image: landwind/ura-chatbot:latest
#    - Port: 8080
#    - Env vars: as per Section 5
#    - Replicas: 1 (scale up once SLOs are validated)

# 5. After deploy lands, run the live smoke against the public URL
BACKEND_URL=https://ura-chatbot-<hash>.renu-01.cranecloud.io \
FRONTEND_URL=https://ura-chatbot-<hash>.renu-01.cranecloud.io \
bash App/scripts/live_smoke.sh

# 5b. If speech is enabled (§7), also run the speech smoke (STT/TTS/MT/voice)
BACKEND_URL=https://ura-chatbot-<hash>.renu-01.cranecloud.io \
bash App/scripts/live_speech_smoke.sh
```

The `live_smoke.sh` script (see `App/scripts/live_smoke.sh`) verifies:

- `GET /health` → `{"status":"alive"}`
- `GET /ready` → `model_loaded=true` (will be `status:"degraded"` because Qdrant is off — accept this)
- `POST /v1/chat` "I need a TIN" → workflow route, `tin_registration` flow
- `POST /v1/chat` "What is a TIN?" → RAG route with citations
- `POST /v1/chat/stream` → SSE metadata + token + done events
- Same set proxied via `/api/*` (Next.js rewrites)

> The smoke asserts `model="${EXPECTED_MODEL}"`, defaulting to
> `Sunbird/Sunflower-14B-FP8` (the default `LLM_MODEL`, served via
> `LLM_BACKEND=vllm` — see docs/MODEL_SWAP_GUIDE.md). Deploying a different
> model: set `EXPECTED_MODEL` to match what vLLM is actually serving rather
> than hardcoding a new value in the script — that hardcoding is exactly
> what broke this assertion the last time the default model changed.

---

## 9. Verified endpoints

After deploy, these should all return 200:

| Path | Response shape |
|---|---|
| `GET /health` | `{"status":"alive","version":"1.2.0"}` |
| `GET /ready` | `{"status":"degraded","model_loaded":true,"retrieval_mode":"keyword"}` |
| `POST /v1/chat` | `ChatResponse` with `reply`, `citations`, `retrieval_mode` |
| `POST /v1/chat/stream` | SSE: `metadata`, `token`*, `grounding`, `done` |
| `GET /v1/speech/health` | `{"enabled":true,"status":"ready"}` (Sunbird cloud mode; §7). `{"status":"unavailable"}` when speech is off. |
| `GET /tags` | List of FAQ tags |
| `GET /docs` | Swagger UI |
| `GET /` | Next.js chat UI |
| `GET /api/v1/chat` (via UI proxy) | Same as `/v1/chat` |

Admin paths (`/v1/admin/*`, `/v1/me/*`, `/metrics`, `/v1/evaluation/*`)
require either a verified staff JWT or the `INDEX_API_KEY` operator
token — they will 401 anonymously.

---

## 10. Operational notes

**Logs**: nginx, uvicorn, and the Next.js node process all log to stdout
via `supervisord`. View with `kubectl logs -f <pod>` or the Crane Cloud
log pane.

**Scaling**: the image is immutable at runtime (no Qdrant writes, no local DB
mutations). Scale horizontally by raising the replica count in the
dashboard. The slowapi rate limiter falls back to in-process buckets on
Crane Cloud (no Redis), so per-IP limits are enforced per replica only
— set a global edge limit if needed.

**Health probes**: configure Crane Cloud's readiness probe at
`/ready` and liveness at `/health` (both port 8080). The 5-minute boot
window allows for backend model + tokenizer warm-up; set `startupProbe`
generously.

**Updating the knowledge base**: rebuild BM25 state (Section 6), commit,
rebuild image, push, redeploy. Crane Cloud has no `/v1/index` workflow
because the underlying BM25 file is read-only at runtime.

**Cost surface**: free Crane Cloud tier covers the container. The only
paid surfaces are:
- vLLM endpoint (if you're hosting one) — separate provider
- Groq API (free tier sufficient for capstone demo loads)
- Sunbird AI cloud speech (free tier for low volume)

---

## 11. Rollback

```bash
# List recent image tags on Docker Hub
docker images landwind/ura-chatbot --format "{{.Tag}}\t{{.CreatedAt}}"

# Pin to a known-good tag and update the Crane Cloud app
# (Dashboard → Image → set tag → Redeploy)
```

Because the image is fully self-contained (no out-of-band DB migrations),
rollback is single-step: change the image tag in the dashboard. Each image
contains its own versioned Qdrant collection and alias, so older images retain
their original knowledge-base snapshot.

---

## 12. WebSocket text chat (Phase 29 — `/v2/chat/stream`)

The `FLAG_WS_CHAT` capability adds a persistent, agentic-events-aware
WebSocket transport at `/v2/chat/stream` alongside the existing SSE
endpoint.  See `App/docs/ws_chat_protocol.md` for the wire protocol.

**Production prerequisites (enforced by `_validate_production_env()`):**

1. `FLAG_AUTH_REQUIRED=true` — anonymous WebSocket chat is refused in
   production.  Closed-beta operator-key access is *not* a substitute;
   wire OIDC (`AUTH_ALG=RS256` + `OIDC_*`) before enabling WS chat in
   production.
2. `WS_CONFIRM_HMAC_SECRET` set to a stable value — used to sign
   single-use confirmation tokens for HITL tool calls.  A per-process
   random fallback exists for dev, but in production it would break
   cross-replica confirmation flows; the validator refuses to start
   without it.
3. The nginx routing block for `/v2/chat/stream` must include
   `Upgrade`/`Connection` headers (see §4) — otherwise the upgrade fails
   with 101 missing.

**Per-replica state caveats** (acceptable for closed-beta scale, flag
for SLO planning if you scale up):

- `WS_CHAT_MAX_PER_USER` cap is enforced via an in-process dict.  Each
  replica counts independently; the global cap is `replicas * cap`.
- Consumed-confirmation-token tracking is also in-process.  A user
  cannot replay a confirm token on the same socket; reconnecting to a
  different replica resets the consumed set, but the HMAC's `exp`
  field (5 min default) and the tool's own idempotency key still
  prevent action duplication.
- For multi-replica enforcement of either, plumb the state through
  Redis under the existing `REDIS_URL` env — out of scope for this
  doc.

**SSE remains the default**: `FLAG_WS_CHAT=false` (the registry default)
ships only the SSE endpoint.  The WS endpoint is dark until explicitly
enabled.

**Smoke check after deploy**:

```bash
# Minimal WS handshake (requires wscat: npm i -g wscat)
wscat -c "wss://ura-chatbot-<hash>.renu-01.cranecloud.io/v2/chat/stream" \
      -H "Authorization: Bearer $(STAFF_TOKEN)"
# Send:
> {"type": "session_start", "locale": "en"}
# Expect:
< {"type":"session_ready","session_id":"...","capabilities":{...}}
```

---

## 13. Automated CI/CD pipeline (ported from MLOPS_V1)

The repo ships two GitHub Actions workflows that mirror the polished
direct-curl pattern from `mpairwe7/MLOPS_V1/.github/workflows/`:

| Workflow | File | Triggers | What it does |
|---|---|---|---|
| Build & push | `.github/workflows/ura-chatbot-build-push.yml` | push to dev/main, `v*` tag, manual dispatch | Builds `Dockerfile.cranecloud`, pushes to `landwind/ura-chatbot:<tag>` on Docker Hub, then dispatches the deploy workflow (dev / `v*` only). |
| Crane Cloud deploy | `.github/workflows/ura-chatbot-deploy-cranecloud.yml` | dispatched by build, or manual | `POST /users/login` → `PATCH /apps/<id>` → poll `/health` for 10 min (RENU intermittently takes far longer than the usual ~90 s cold start for an unchanged image). Direct REST API — no `cranecloud` CLI, no Python keyring shim. |
| Redeploy retry loop | `.github/workflows/ura-chatbot-cc-redeploy-retry.yml` | cron every 3h, manual dispatch | Retries a blocked image roll while Crane Cloud's control plane can't resolve `hub.docker.com` (its tag-validation DNS quirk): probes the live app for the target build, dispatches the deploy workflow if stale, and on success opens a notification issue and **disables itself**. Re-enable + bump `TARGET_TAG` for future blocked rolls. |
| HF Space keepalive | `.github/workflows/hf-space-keepalive.yml` | cron hourly, manual dispatch | Keeps `landwind22/ura-chatbot` from sleeping/pausing: checks the Space runtime stage via the HF API (secret `HF_TOKEN`) and restarts it when paused/errored, then pings `/v1/speech/health` so the free-tier inactivity timer keeps resetting. |

### Crane Cloud REST API contract used by the deploy workflow

See MLOPS_V1/`docs/22-crane-cloud-deployment.md` for the canonical table.
URA Chatbot uses just two endpoints:

| Endpoint | Method | Purpose |
|---|---|---|
| `/users/login` | `POST` | `{email, password}` → `{data: {access_token, id}}` |
| `/apps/{id}` | `PATCH` | `Authorization: Bearer <token>`, body `{image: "landwind/ura-chatbot:<tag>"}` |

> **Crane Cloud diffs by image *string*, not Docker manifest digest.**
> PATCHing a moving tag like `:latest` is a no-op for pod rollover. The
> build workflow always tags `sha-<short>` so the deploy step PATCHes a
> string that changes on every push — same trick MLOPS_V1 uses.

### Required GitHub secrets

Matches the `CRANE_CLOUD_*` convention already used in `mpairwe7/OptiscanAI`
and `MLOPS_V1`, so values can be copied across repos verbatim:

| Secret | Source / format | Notes |
|---|---|---|
| `DOCKERHUB_USERNAME` | `landwind` | Public namespace, OK to set directly. |
| `DOCKERHUB_TOKEN` | Docker Hub PAT, `dckr_pat_…` | Must have `repo:write` scope on `landwind/ura-chatbot`. |
| `CRANE_CLOUD_EMAIL` | lowercase operator email | Same value used in OptiscanAI / MLOPS_V1. |
| `CRANE_CLOUD_PASSWORD` | operator password | Same value used in OptiscanAI / MLOPS_V1. |
| `CRANE_CLOUD_URA_APP_ID` | `b01219c6-9555-41e2-84c3-f15d764fb938` | UUID of the URA Chatbot app provisioned 2026-05-25. |

### Setting secrets safely (no-leak pattern)

Use `--body` omitted so `gh` reads from stdin; pipe via `!` so the value
stays in your terminal and never enters the assistant transcript:

```bash
! gh secret set CRANE_CLOUD_EMAIL       -R mpairwe7/FinalYearProject
! gh secret set CRANE_CLOUD_PASSWORD    -R mpairwe7/FinalYearProject
! gh secret set CRANE_CLOUD_URA_APP_ID  -R mpairwe7/FinalYearProject
! gh secret set DOCKERHUB_TOKEN         -R mpairwe7/FinalYearProject
# paste value, then Ctrl-D for each
```

### Manual one-off redeploy (bypassing CI)

If you need to roll a freshly-pushed image without waiting for CI, the
exact same flow runs from a workstation:

```bash
export CRANE_CLOUD_API=https://api.cranecloud.io
export CRANE_EMAIL='…'        # lowercase email
export CRANE_PASSWORD='…'
export APP_ID=b01219c6-9555-41e2-84c3-f15d764fb938

TOKEN=$(curl -sf -X POST "$CRANE_CLOUD_API/users/login" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$CRANE_EMAIL\",\"password\":\"$CRANE_PASSWORD\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['access_token'])")

curl -sf -X PATCH "$CRANE_CLOUD_API/apps/$APP_ID" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"image":"landwind/ura-chatbot:sha-abc1234"}'
```

---

## 14. Related docs

- `docs/DEPLOYMENT.md` — full Docker Compose production deployment (the local/server path)
- `docs/LOCAL_DEVELOPMENT.md` — local dev setup for App
- `docs/APP_FLOWS.md` — request lifecycle: browser → Next.js → FastAPI → LLM
- `MLOPS_V1/docs/22-crane-cloud-deployment.md` — canonical reference for the Crane Cloud REST API and the direct-curl deploy pattern this workflow mirrors
- Sibling references: `Musawo/docs/crane-cloud-deployment.md`,
  `HustleCoach/docs/crane-cloud-deployment.md`,
  `Magezi/docs/crane-cloud-deployment.md` — same RENU cluster, simpler stacks

---

## 15. Alternate pipeline — Hugging Face Docker Space

A second, Crane-Cloud-independent deployment of the **same image** — useful when
the Crane Cloud control plane is unavailable (e.g. its Docker Hub image
validation is failing, §7.2). Definition lives in `App/deploy/hf-space/`.

| Field | Value |
|---|---|
| Space | `landwind22/ura-chatbot` (HF account `landwind22`) |
| App URL | `https://landwind22-ura-chatbot.hf.space` |
| SDK | `docker` — `Dockerfile` is `FROM landwind/ura-chatbot:<sha>`; HF pulls and runs it (no rebuild) |
| Port | `app_port: 8080` in `README.md` (the image's nginx) |

**How it works:** HF Docker Spaces build any Dockerfile; ours just references the
prebuilt Docker Hub image, so there is **no separate build** — bump the `FROM`
tag in `App/deploy/hf-space/Dockerfile` to roll the Space.

**Secrets & config:** runtime config mirrors the Crane Cloud app's env, set as
**Space secrets/variables** (Settings, or run `App/deploy/hf-space/replicate_secrets.py`
to copy the live Crane Cloud env, and `App/deploy/hf-space/set_fallback_secret.py`
to set the Sunbird fallback token). Overrides vs Crane Cloud: `USE_DOH=false` (HF
has native DNS) and `CORS_ORIGINS` = the Space URL. Model-routing IDs (`CF_LLM_*`,
`STT_/TTS_FALLBACK_*`) are set as non-secret Space **variables**. Both helpers are
stdlib-only and print key names only, never values.

**Frame-embedding fix (§7.4 note):** the frontend originally sent
`X-Frame-Options: DENY`, which blanked the HF Space *page* (it embeds the app in an
iframe; the direct `*.hf.space` URL always rendered). `next.config.mjs` now drops
`X-Frame-Options` and uses CSP `frame-ancestors 'self' https://huggingface.co
https://*.hf.space` (env `FRAME_ANCESTORS`; `"'none'"` restores strict no-embed).
Headers are baked at Next.js build time, so this required a rebuild — not a runtime
env change.

**Verify** exactly as for Crane Cloud:

```bash
BACKEND_URL=https://landwind22-ura-chatbot.hf.space \
  bash App/scripts/live_speech_smoke.sh
```

Verified 2026-06-17 (live image `sha-c2722c4`): TTS en → `cf_workers_ai`
(`@cf/deepgram/aura-2-en`, MP3; MeloTTS/edge_tts/Sunbird as fallbacks), TTS lg →
`sunbird_cloud` (speaker 248), STT en/lg → `sunbird_cloud`, translate →
`gemini_flash`, voice/chat (en+lg) full pipeline. Sunbird modal cold starts make
the first call of each kind slow (voice/chat ~30–45 s; the compound English call
can exceed HF's proxy timeout → 504 — real clients should use streaming).

---

## 16. Model routing strategy

Per-task model selection follows **Best capability → cost → resilience**, declared
in `App/backend/app/providers/routing.py` (env-overridable model IDs) and recorded
on `/metrics` as `model_usage_total{task,model}` + `model_fallback_total{task,from,to,reason}`.

| Task | Primary → fallbacks |
|---|---|
| Reasoning / RAG / summarization (LLM) | Gemini 2.5 Flash → CF `llama-3.3-70b-instruct-fp8-fast` → CF `qwq-32b` (local/vLLM stays primary when present) |
| Translation (en↔lg) | Gemini 2.5 Flash → CF Llama (prompted) → Sunbird NLLB → local MT → Qwen3 |
| Luganda STT | Sunbird → CF `whisper-large-v3-turbo` (Gemini-audio deferred) |
| English STT / TTS | STT `whisper-large-v3-turbo`; TTS `aura-2-en` → `melotts` → edge_tts → Sunbird — see §7.4 |
| Embedding | CF `bge-m3` (the index's vector space) → degrade to BM25 keyword |

Env knobs (defaults apply if unset): `CF_LLM_MODEL`, `CF_LLM_FALLBACK_MODEL`,
`CF_LLM_FAST_MODEL`, plus the existing `LLM_FALLBACK_BACKEND=gemini`,
`TRANSLATE_FALLBACK_BACKEND=gemini`, `FLAG_CLOUDFLARE_FALLBACK=true`, and the speech
model envs (§7.4). Cloud tiers **self-skip** when their flag/keys are absent, so
local/GPU deploys fall through to offline tiers; breakers + budget + keyword
degradation keep it resilient when a cloud tier is down.

**Catalog substitutions** (spec models absent from this account's Workers AI catalog):
Llama 405B / Command R+ / Qwen2.5-72B → `@cf/qwen/qwq-32b`; "Gemini 3.5 Flash" →
Gemini 2.5 Flash; "Sunbird 2" → the existing Sunbird API. **Embedding caveat:** a
Vectorize index is bound to ONE embedding model's vector space, so a *different*
embed model is not a valid fallback — resilience is retry `bge-m3` → BM25 keyword.

**Grok voice (evaluated 2026-06, not adopted):** xAI's Grok STT/TTS APIs exist and
Cloudflare AI Gateway added a `grok` provider, but the gateway routes Grok **chat
completions only** — voice (STT/TTS/`/v1/realtime`) needs **direct `api.x.ai`** calls
(paid: ~$0.10–0.20/hr STT, $4.20/1M-char TTS), a separate `XAI_API_KEY`, and has no
Luganda. The gateway-native, free-tier-friendly premium English voice
(`@cf/deepgram/aura-2-en`) was chosen instead.

---

## 17. Deployment state & change log (audit — 2026-06-17)

Snapshot after this session's changes.

| Pipeline | Image | Status |
|---|---|---|
| HF Space `landwind22/ura-chatbot` | `sha-c2722c4` | **Live** — model routing + CF speech + Aura-2 English TTS + iframe fix |
| Crane Cloud app `b01219c6-…` | `sha-0d7cd2f` (older) | Speech enabled; routing/CF-TTS env **staged**, applies when its image deploy recovers (Docker-Hub validation outage, §7.2) |

**Per-task models (live)** — see §16:
- English: STT `@cf/openai/whisper-large-v3-turbo`; TTS `@cf/deepgram/aura-2-en` → MeloTTS → edge_tts → Sunbird.
- Luganda: STT/TTS Sunbird (TTS speaker 248); CF whisper-turbo as the STT net.
- Translation en↔lg: Gemini 2.5 Flash → CF Llama-3.3-70B → Sunbird NLLB → local MT → Qwen3.
- LLM reasoning: Gemini → CF Llama-3.3-70B → QwQ-32B (local/vLLM primary when present).
- Embedding: bge-m3 → BM25 keyword.

**Resilience:** Sunbird primary + fallback account (`SUNBIRD_API_TOKEN` / `SUNBIRD_FALLBACK_API_TOKEN`, `sunbird._post`); per-channel circuit breakers + free-tier budget guards; cloud tiers self-skip when unconfigured.

**Observability:** `model_usage_total{task,model}` + `model_fallback_total{task,from,to,reason}` on `/metrics` (admin-gated).

**Change log / PRs (this session):** #116 speech enable + edge-tts English · #117 HF Docker Space pipeline · #118 CF English STT/TTS · #119 Sunbird fallback account · #120 `set_fallback_secret.py` helper · #121 model-routing policy + observability · #122 HF iframe `frame-ancestors` fix · (Aura-2-en TTS primary + `CF_LLM_*` env = config-only, no PR).

**Ops gotchas:** (1) change CC env during the Docker-Hub outage via an **env-only PATCH** — omit the `image` field (§7.2); (2) **build-push is path-filtered** — docs/`hf-space/`-only merges don't build a new image; (3) HF/security headers are **baked at build** (the frame fix needed a rebuild, not an env change); (4) Grok voice not gateway-routable (see above).

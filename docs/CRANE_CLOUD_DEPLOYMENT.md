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
`Dockerfile.cranecloud` that bundles `nginx + uvicorn + Next.js standalone`
under `supervisord` and falls back to BM25 keyword retrieval (no Qdrant).

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
| Retrieval | BM25 keyword (Qdrant unavailable on Crane Cloud) |

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
│  ├─ uvicorn :8081 (FastAPI, BM25 retrieval, LLM dispatch)  │
│  └─ node :3000    (Next.js standalone build)               │
│                                                            │
│ Baked-in: BM25 state, knowledge base, no Qdrant            │
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

## 6. Retrieval on Crane Cloud (no Qdrant)

The App's `HybridRetriever` always tries Qdrant first; if Qdrant is
unavailable, `ChatModel._simple_search` (keyword overlap) is used. With
`QDRANT_ENABLED=false`, the Qdrant client is never instantiated and
`/ready` returns `status: "degraded"` (still 200) — search uses BM25
keyword matching over the in-memory FAQ index plus the BM25 sparse
posting list loaded from `BM25_STATE_PATH`.

Knowledge base files baked into the image:

```
/app/Data/dataset/*.csv          # FAQ corpus (URA forms, FAQs, calendar)
/app/Data/pdfs/*.pdf             # URA legal/regulatory PDFs (optional)
/app/Model/bm25_state.json       # Pre-built BM25 posting list + IDF
```

Rebuild the BM25 state before each release:

```bash
cd App/backend
PYTHONPATH=. python -c "
from app.indexer import DATA_DIR, PDF_DIR, ingest_csvs, ingest_pdfs
from app.retriever import BM25SparseEncoder, BM25_STATE_PATH
docs = ingest_csvs(DATA_DIR) + ingest_pdfs(PDF_DIR)
enc = BM25SparseEncoder()
enc.fit([d['text'] for d in docs])
enc.save(BM25_STATE_PATH)
print('BM25 state saved to', BM25_STATE_PATH, 'docs=', len(docs))
"
```

Commit the resulting `Model/bm25_state.json` so the Crane Cloud build
picks it up automatically.

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
| `SUNBIRD_API_TOKEN` | `<token>` | Cloud ASR/TTS/MT for Ugandan languages. `is_available()` is just `bool(token)`. `SUNBIRD_API_URL` defaults to `https://api.sunbird.ai`. |
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

> The smoke asserts `model="Qwen/Qwen3-8B"`. When `LLM_BACKEND=vllm`
> with a non-Qwen model, this assertion will fail — patch the smoke
> assertions or override `LLM_MODEL` to match what vLLM is serving.

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

**Scaling**: the image is stateless (no Qdrant writes, no local DB
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

Because the image is fully self-contained (no out-of-band DB migrations,
no Qdrant index versioning), rollback is single-step: change the image
tag in the dashboard. The BM25 state lives inside each image so older
images keep their original knowledge base snapshot.

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
| Crane Cloud deploy | `.github/workflows/ura-chatbot-deploy-cranecloud.yml` | dispatched by build, or manual | `POST /users/login` → `PATCH /apps/<id>` → poll `/health` for 5 min. Direct REST API — no `cranecloud` CLI, no Python keyring shim. |

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

**Secrets:** runtime config mirrors the Crane Cloud app's env, set as **Space
secrets** (Settings → Secrets, or run `App/deploy/hf-space/replicate_secrets.py`,
which copies the live Crane Cloud env). Overrides vs Crane Cloud: `USE_DOH=false`
(HF has native DNS) and `CORS_ORIGINS` = the Space URL.

**Verify** exactly as for Crane Cloud:

```bash
BACKEND_URL=https://landwind22-ura-chatbot.hf.space \
  bash App/scripts/live_speech_smoke.sh
```

Verified 2026-06-17: TTS en → `edge_tts` (`en-US-AriaNeural`, MP3), TTS lg →
`sunbird_cloud` (speaker 248), STT en/lg → `sunbird_cloud`, translate →
`gemini_flash`, voice/chat (en+lg) full pipeline. Sunbird modal cold starts make
the first call of each kind slow (voice/chat ~30–45 s).

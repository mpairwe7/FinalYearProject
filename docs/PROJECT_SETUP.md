# Project Setup Guide

## Overview

URA Chatbot is an AI-powered customer service assistant for Uganda Revenue Authority. This guide covers complete project setup from scratch.

## Prerequisites

### Required Software

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.11+ | Backend, ML pipelines |
| uv | 0.7+ | Python package manager (replaces pip) |
| Node.js | 20+ | Frontend runtime |
| Bun | 1.1+ | Frontend package manager & JS runtime |
| Docker | 24+ | Containerization |
| Git | 2.40+ | Version control |
| GitHub CLI | 2.40+ | Workflow management |

### Installation Commands

```bash
# Ubuntu/Debian
sudo apt update && sudo apt install -y python3.11 nodejs docker.io git

# Install uv (Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install Bun (frontend package manager)
curl -fsSL https://bun.sh/install | bash

# Install GitHub CLI
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null
sudo apt update && sudo apt install gh
```

## Project Setup

### 1. Clone Repository

```bash
git clone https://github.com/mpairweLandwind/FinalYearProject.git
cd FinalYearProject
```

### 2. Python Environment

```bash
# Create virtual environment and install dependencies
uv venv
source .venv/bin/activate  # Linux/Mac
# or: .\.venv\Scripts\activate  # Windows

# Install dependencies
uv pip install -r requirements.txt
uv pip install -r App/backend/requirements.txt
```

### 3. Frontend Setup

```bash
cd App/frontend
bun install
cd ../..
```

### 4. Secret Scanning & Pre-commit Hooks

```bash
# One-command setup: installs TruffleHog, ggshield, Gitleaks, detect-secrets
bash scripts/setup-secret-scanning.sh

# Or manually:
pip install pre-commit detect-secrets ggshield
pre-commit install --hook-type pre-commit --hook-type pre-push
```

This installs 4-layer defence-in-depth secret scanning (see [SECURITY.md](../SECURITY.md#secret-scanning-defence-in-depth)):
1. **TruffleHog v3** — verified credential detection (800+ detectors)
2. **ggshield** — GitGuardian ML-based detection (requires `GITGUARDIAN_API_KEY`)
3. **Gitleaks v8** — regex + entropy with custom URA rules (`.gitleaks.toml`)
4. **detect-secrets** — baseline-aware entropy scanner (`.secrets.baseline`)

### 5. Environment Configuration

```bash
# Copy example environment file
cp .env.example .env

# Edit with your credentials
nano .env
```

Required environment variables:
```env
# Hugging Face
HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxxx

# Kaggle (for training)
KAGGLE_USERNAME=your_username
KAGGLE_API_TOKEN=your_api_key

# DockerHub
DOCKERHUB_USERNAME=your_username
DOCKERHUB_TOKEN=your_token

# Frontend Docker Image
DOCKER_IMAGE_FRONTEND=landwind/ura-chatbot-frontend

# Qdrant Vector Store (hybrid retrieval)
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=              # Optional, for Qdrant Cloud
QDRANT_COLLECTION=ura_knowledge_base_v1

# LLM Generation (Qwen3-8B, ~16 GB auto-download)
LLM_MODEL=Qwen/Qwen3-8B
LLM_ENABLED=true
LLM_DEVICE=auto              # auto|cpu|cuda
LLM_TORCH_DTYPE=auto         # float16|bfloat16|float32|auto
LLM_TEMPERATURE=0.2
LLM_MAX_TOKENS=512

# Semantic Cache
CACHE_ENABLED=true
CACHE_THRESHOLD=0.92         # Cosine similarity threshold
CACHE_TTL_SECONDS=3600       # 1 hour
CACHE_MAX_SIZE=1000

# Corrective RAG
CORRECTIVE_RAG_ENABLED=true
CORRECTIVE_RAG_THRESHOLD=0.3

# Rate Limiting
RATE_LIMIT=30/minute

# Guardrails & Privacy
STORE_RAW_PROMPTS=false      # Set true only for debugging
ABSTENTION_THRESHOLD=0.15    # Min score before abstaining
ESCALATION_THRESHOLD=0.25    # Faithfulness below which to flag for human review
GROUNDING_THRESHOLD=0.3      # Score below which to append disclaimer
CONVERSATION_TTL_DAYS=7      # Auto-purge conversation data
FEEDBACK_TTL_DAYS=90         # Auto-purge feedback data

# Authentication (Phase 14 — off by default)
AUTH_ALG=HS256                # HS256 (dev) or RS256 (prod OIDC)
AUTH_DEV_SECRET=dev-insecure-change-me  # Shared secret for HS256
OIDC_ISSUER=                  # OIDC issuer URI (RS256)
OIDC_AUDIENCE=ura-chatbot     # OIDC audience
OIDC_JWKS_URL=                # JWKS endpoint (RS256)
OIDC_ROLE_CLAIM=              # Optional dot-path override, e.g. realm_access.roles

# Speech Pipeline (Phase 16 — requires speech models)
SPEECH_ENABLED=true
SPEECH_ASR_BACKEND=auto       # auto|sherpa|transformers|mock
SPEECH_TTS_BACKEND=auto       # auto|sherpa|piper|mock
SPEECH_MT_BACKEND=prompted    # auto|onnx|transformers|prompted|mock

# Sunbird AI Cloud Fallback (Ugandan languages)
SUNBIRD_API_URL=https://api.sunbird.ai
SUNBIRD_API_TOKEN=            # Required for cloud speech/translation

# Analytics Database
ANALYTICS_BACKEND=sqlite      # sqlite|postgres
POSTGRES_DSN=                 # e.g. postgresql://user:pw@host/db  # pragma: allowlist secret

# vLLM Backend (alternative to local transformers)
LLM_BACKEND=local             # local|vllm
VLLM_BASE_URL=http://vllm:8001/v1

# CORS & Security
CORS_ORIGINS=http://localhost:3300  # Comma-separated, no wildcard

# Feature Flags (all FLAG_<NAME> env vars, see flags.py)
FLAG_CORRECTIVE_RAG=true      # Re-retrieve on low quality
FLAG_SEMANTIC_CACHE=true      # Cache similar queries
FLAG_QUERY_REWRITE=true       # Spell/abbrev/coreference
FLAG_RERANKER=true            # Cross-encoder reranking
FLAG_WORKFLOWS=true           # Guided multi-step workflows

# Voice-First Streaming (Phase 23)
FLAG_VOICE_STREAMING=false    # WebSocket streaming voice chat (VAD + barge-in)
FLAG_VOICE_CONSENT=false      # Enforce voice-specific consent checks
VOICE_VAD_ENERGY_THRESHOLD=0.015  # RMS threshold for speech detection
VOICE_VAD_SILENCE_MS=600      # Silence before utterance end
VOICE_VAD_MIN_SPEECH_MS=250   # Min speech duration to process
VOICE_STORE_RAW_AUDIO=false   # Never store raw audio by default
FLAG_HANDOFF_SUMMARIES=true   # Human triage packets
FLAG_TOOL_USE=false           # LLM tool-calling (calculators, rates)
FLAG_AGENTIC_MODE=false       # Supervisor routing
FLAG_AUTH_REQUIRED=false      # Enforce JWT authentication
FLAG_MEMORY_ENABLED=false     # Consent-gated personalization
FLAG_AUDIT_LEDGER=false       # Hash-chained audit log
FLAG_VOICE_ENABLED=false      # Mobile voice features

# Observability (opt-in)
OTEL_ENABLED=false
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
```

## Running the Application

### Option 1: Docker Compose (Recommended)

```bash
# Production mode
docker compose up -d api

# Development mode (with hot reload)
docker compose --profile dev up api-dev

# View logs
docker compose logs -f api
```

> Note: The `trainer` container runs as a non-root user (UID 1000). Ensure `./artifacts` is writable on the host before running training profile.

### Option 2: Manual Start

```bash
# Terminal 1: Backend API (port 8887)
cd App/backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8887

# Terminal 2: Frontend (port 3300)
cd App/frontend
bun run dev

# Terminal 3: Gradio App (optional)
python App/app.py
```

### Option 3: Gradio Only

```bash
# Simple classifier demo
python App/classifier.py
```

## Sign-In and Sign-Up (OIDC)

`/admin` and `/agent` need a staff identity; the assistant itself needs none.
The backend **verifies** tokens but never issues them, so there is no password
form — signing in is an OAuth 2.1 authorization-code redirect with PKCE S256 to
your identity provider (public client, no secret in the bundle).

### The three routes

| Route | What it does |
| --- | --- |
| `/signin` | Starts the redirect. Also hosts the opt-in dev-token panel. |
| `/signup` | The same redirect with a registration hint, so "create an account" reaches the provider's own registration screen. |
| `/signin/callback` | Completes the code exchange for both, then routes by role. |

Registration is not a second flow: `src/lib/oidcFlow.ts` builds one
authorization request and adds `prompt=create` ("Initiating User Registration
via OpenID Connect 1.0", understood by Entra ID, Google and Curity) together
with Auth0's older `screen_hint=signup`. Providers ignore parameters they do not
recognise, so both are always sent and no build-time switch is needed. **The
provider must have self-registration enabled** or it will simply show its login
screen: Keycloak → Realm settings → Login → *User registration*; Auth0 →
Authentication → Database → *Disable Sign Ups* must be off.

After the exchange, the callback sends staff to the tool their role can open
(`ura_staff` → `/agent`, admin/auditor → `/admin`). Everyone else goes to the
`returnTo` the flow recorded, which only `/signup` sets — so a taxpayer who
registers from the assistant lands back in the assistant, and a non-staff
sign-in, which records nothing, falls back to `/` rather than a dashboard that
would refuse them.

Both entry points are reachable from the assistant without going looking for
them: the sidebar's account block carries Sign in, Sign up and Settings; the
header carries the pair while signed out, though below 720px only *Sign up*
stays (the header still has to fit the brand, language and theme, and the
sidebar carries both anyway); and the landing hero says what an account adds.
Nothing above those is gated — questions, document checks and voice all work
signed out.

### Wiring a provider

The frontend needs three build-time variables and the backend four at runtime:

The app discovers the provider's endpoints from
`<issuer>/.well-known/openid-configuration`, so it works with any compliant
provider — no vendor URL layout is assumed.

```bash
# Frontend (inlined at BUILD time — a rebuild is required to change them)
NEXT_PUBLIC_OIDC_ISSUER=https://idp.example.gov/realms/ura
NEXT_PUBLIC_OIDC_CLIENT_ID=ura-chatbot
NEXT_PUBLIC_OIDC_SCOPE="openid profile email"   # optional, this is the default
NEXT_PUBLIC_OIDC_AUDIENCE=                      # required for Auth0 (see below)

# Backend (read at process start)
AUTH_ALG=RS256
OIDC_ISSUER=https://idp.example.gov/realms/ura
OIDC_AUDIENCE=ura-chatbot
OIDC_JWKS_URL=https://idp.example.gov/realms/ura/protocol/openid-connect/certs
```

Three things are easy to get wrong and each fails in a way that does not name
its own cause:

- **`connect-src` must allow the provider.** The callback exchanges its code
  directly with the provider's token endpoint, which is the one browser call
  that does not go through the `/api/*` rewrite. `next.config.mjs` derives that
  origin from `NEXT_PUBLIC_OIDC_ISSUER` automatically — but it reads the value at
  **server start**, while the client bundle inlines it at **build time**. Set it
  in both places or sign-in fails with an opaque `NetworkError`.
- **The access token needs the right `aud`.** Keycloak does not add your client
  to the audience by default; without an audience mapper the backend rejects
  every token with `audience mismatch`. Redirect URIs must also list
  `<app-origin>/signin/callback` exactly.
- **Roles are read from wherever your provider puts them.** Keycloak sends
  `realm_access.roles`, Entra ID and Okta commonly send `roles` or `groups`;
  only our dev tokens use a flat `role`. All of these are probed by default. Use
  `OIDC_ROLE_CLAIM` (a dot-path, e.g. `realm_access.roles`) if yours differs.
  Role names must match `ura_staff` / `ura_admin` / `ura_auditor` — hyphens and
  group-path prefixes are normalised, anything unrecognised resolves to `public`
  and the dashboards refuse it.

### Auth0

Auth0 works, but two of its defaults will otherwise fail in ways that do not name
their cause:

- **You must pass an audience, or the access token is opaque.** Auth0 only issues
  a verifiable JWT when the authorization request names a registered API. Create
  an API (Applications → APIs) and set its identifier as
  `NEXT_PUBLIC_OIDC_AUDIENCE` (and as the backend's `OIDC_AUDIENCE`). Without it
  the backend rejects every sign-in with `malformed token`, because an opaque
  token is not a JWT at all.
- **Roles need a claim the backend can find.** Either enable RBAC on the API with
  "Add Permissions in the Access Token" (lands in `permissions`, probed by
  default), or add a Login Action setting a namespaced claim and point
  `OIDC_ROLE_CLAIM` at it — the claim NAME contains dots, which is handled:

  ```js
  // Auth0 Action (Login → Post Login)
  exports.onExecutePostLogin = async (event, api) => {
    api.accessToken.setCustomClaim("https://ura.go.ug/roles", event.authorization?.roles ?? []);
  };
  ```
  ```
  OIDC_ROLE_CLAIM=https://ura.go.ug/roles
  ```

Register the app as a **Single Page Application** (public client, PKCE) with
`<app-origin>/signin/callback` in Allowed Callback URLs and `<app-origin>` in
Allowed Web Origins. Role names must still be `ura_admin` / `ura_staff` /
`ura_auditor`.

> **Watch the issuer string.** It is
> `https://<tenant>.<region>.auth0.com/` — the backend compares `iss`
> byte-for-byte, so a missing trailing slash rejects every token as
> `issuer mismatch`. A *trailing space* is worse: it is invisible in a CI
> variable or `.env`, and turns the discovery URL into
> `…auth0.com%20/.well-known/…`, which the browser reports only as
> `NetworkError`. Both are now trimmed defensively in
> `src/lib/oidc.ts` and at every `NEXT_PUBLIC_OIDC_*` read, but set them
> cleanly anyway. Verify with:
>
> ```bash
> curl -s https://<tenant>/.well-known/openid-configuration | jq -r .issuer
> ```
>
> and use that exact string for `OIDC_ISSUER`.

### Verifying locally against a real Keycloak

```bash
docker run -d --name ura-kc -p 8180:8080 \
  -v "$PWD/realm.json:/opt/keycloak/data/import/realm.json:ro" \
  quay.io/keycloak/keycloak:26.0 start-dev --import-realm --http-port=8080
```

The realm needs a public client (`publicClient: true`,
`pkce.code.challenge.method: S256`), an `oidc-audience-mapper` adding the client
to `aud`, realm roles named as above, and users holding them. Point both the
frontend and backend at `http://127.0.0.1:8180/realms/<realm>` — the issuer must
match byte-for-byte on both sides, so do not mix `localhost` and `127.0.0.1`.

### Dev-token fallback

Where no provider is configured, `/signin` can accept a locally minted token:

```bash
NEXT_PUBLIC_DEV_SIGNIN=true   # frontend: reveals the panel
AUTH_ALG=HS256                # backend
AUTH_DEV_SECRET=<shared secret>
```

```bash
cd App/backend && python -c \
  "from app.auth.jwt_auth import make_dev_token; print(make_dev_token('dev-user', role='ura_admin'))"
```

## Settings

`Settings` opens from the sidebar's account block, from the header's overflow
menu, and from the landing page once signed in. It is one dialog with five tabs,
and every control writes state something already reads — there are no
preferences stored for their own sake:

| Tab | Controls | Written to |
| --- | --- | --- |
| General | Theme, response language | `lib/theme` (`ura-theme`), `useChatStore.locale` — the same values the header's theme button and language menu use |
| Voice | Narrate replies; a narration voice **per language**, each with its own preview | page narration state, `useVoiceStore.voiceByLocale` (read by every `/v1/tts` call site, keyed on the active locale) |
| Tax profile | Display name, taxpayer type, industry, answer detail, record language, registered tax heads | `GET`/`PUT /v1/me/profile` — needs an account, and says so when signed out |
| Privacy & data | Anonymous analytics; consent receipts; download or delete local conversations; export or erase account data | `ura_analytics_consent`, `/v1/me/consents` (grant/withdraw), `useChatStore`, `GET /v1/me/export`, `DELETE /v1/me` |
| Account | Identity, role, tenant, provider subject; sign in / sign up / sign out; operations links for staff | `GET /v1/me`, the token store |

### Narration voices

A voice belongs to the language it speaks, so the choice is per language, not
one global setting. Sunbird's catalog tags are language-scoped and the backend
refuses one from another language rather than synthesising the wrong one —
`sunbird.resolve_tts_voice` validates every requested speaker against that
locale's catalog and falls back to its default. `speech_service.resolve_edge_voice`
does the same for the English edge-tts voices, and both providers' lists come
from the same functions the endpoint advertises, so an offered voice is always
one synthesis will use.

That second resolver was missing at first: the picker sent a voice, `synthesize`
accepted it, and `_synthesize_edge_tts` ignored it, so all four English voices
played as `en-US-AriaNeural`. Nothing in the suite caught it because every
frontend spec mocks `/api/**` and the backend tests stopped at the layer above
the edge call. `scripts/probe_deploy.py` exists because of this — it drives a
running deployment unmocked, which is what found it.

`GET /v1/speech/voices` serves the catalog; the client does not hardcode it,
because only the backend knows whether this deployment can reach Sunbird at all.
A deployment without a Sunbird key still lists the English (edge-tts) voices and
says plainly that the Ugandan languages will fall back to an English speaker.

| Language | Voices | Provider |
| --- | --- | --- |
| English | 3 edge-tts neural + Sunbird's last-resort `salt_eng_0001` | edge-tts serves English first |
| Luganda | 8 | Sunbird, native speakers |
| Acholi | 5 | Sunbird, native speakers |
| Runyankole | 5 | Sunbird, native speakers |
| Swahili | 2 | Sunbird, native speakers |

Every tag was confirmed against the live `/tasks/audio/speech` before being
offered — 21 requests, 21 with fetchable audio, 0 rejected. Re-verify before
adding one: an unusable tag is not a loud failure but a voice a person can
select and never hear, because the request 400s and the chain degrades to an
English voice reading Luganda. The voices are numbered rather than given persona
names; the catalog exposes opaque tags and nothing about the speakers, so a name
and a character would be invented.

### Speaking to the assistant

Speech has one home: the composer. Two controls sit there and nothing else in
the chrome duplicates them.

| Control | What it does |
| --- | --- |
| **Dictate** (mic) | Fills the composer with what you say. Does not send. |
| **Enter voice mode** (waveform) | Holds a spoken conversation — records, sends, and reads the reply back. Carries narration with it. |

**Dictation transcribes live.** Words appear as they are spoken rather than
after you stop, because the recognizer runs with `interimResults` and
`continuous` both on. Interim guesses render and are replaced when the engine
commits them, so a mishearing is visible while it is still worth restarting.
`continuous` is what keeps the mic alive across the pause between two
sentences: the engines end sessions on silence anyway, so `onend` restarts one
whenever the person has not tapped stop. Dictating into a half-typed question
appends to it — the composer's contents when dictation started are the base
every result is joined onto, so a session never erases what was already there,
and sending mid-utterance ends the session rather than letting a late result
refill the box.

Where the browser has no Speech API — Firefox, and Chromium on some platforms —
dictation records and posts to `/v1/asr` instead. That path cannot stream, so
the mic shows a distinct *Transcribing* state while the audio is in flight
rather than pretending to still be listening. It is not the red recording pulse
and it does not accept taps: the audio is already uploaded, so a second tap
would cancel nothing.

The v2 WebSocket (`/v2/voice/chat/stream`) does emit `partial_transcript`, and
would give the server path the same live behaviour. It is not wired in, because
it is gated behind the `native_voice` flag, which defaults off and needs a
CosyVoice2 model that is not in the deploy image — wiring it today would ship a
live-transcription path that is inert in production.

Two things the header used to carry and no longer does. The **"Voice ready"
pill** reported a backend detail continuously in the corner of every screen; it
earned its space only in the seconds after a failure, and the composer's own
controls already go quiet and explain themselves when speech is unreachable,
which is where someone is looking when it matters. The **voice-overlay mic**
was a second, differently-shaped entry into speech a few centimetres from the
composer's — two mics in one viewport, neither saying which was which. Removing
it left `VoiceChat.tsx` with no entry point; `VoiceFirstChat.tsx` and
`VoiceVisionMode.tsx` never had one. All three still exist on disk but are no
longer mounted.

Two deliberate omissions: `useVoiceStore` also persists `autoBargeIn`,
`silenceTimeout` and `accentProfile`, which nothing currently reads, so they are
not offered — a switch that changes nothing is worse than a missing one. And
withdrawing `personalization` consent purges the memory built under it
server-side (UDPA 2019: a withdrawal must stop the processing), which is why that
row warns before it is used.

`DELETE /v1/me` is the right-to-erasure call: it removes every PII-bearing row
except the hash-chained audit ledger, which records that the erasure happened.
The dialog confirms first, then signs the person out, since the account behind
the token no longer exists.

This is **not** authentication — anyone with the secret can mint one, and the
panel says so on screen. `make_dev_token` refuses to run under
`APP_ENV=production`; leave `NEXT_PUBLIC_DEV_SIGNIN` unset on any real deploy.

## Project Structure

```
FinalYearProject/
├── .github/
│   └── workflows/           # CI/CD pipelines
│       ├── ci-ml-pipeline.yml
│       ├── frontend-deploy.yml
│       ├── kaggle-training.yml
│       └── secret-scanning.yml  # 4-layer secret scanning
│
├── App/                     # Application Code
│   ├── app.py              # Full Gradio app (HF Spaces)
│   ├── classifier.py       # Simple classifier demo
│   ├── backend/            # FastAPI REST API (v1.3.0)
│   │   ├── app/
│   │   │   ├── main.py     # 50+ API routes, SSE streaming, CORS, rate limiting, auth
│   │   │   ├── models.py   # Pydantic v2 schemas (citations, escalation, speech, export)
│   │   │   ├── service.py  # 12-stage RAG pipeline orchestrator + agentic routing
│   │   │   ├── llm.py      # Qwen3-8B local generation + vLLM backend + tool-calling
│   │   │   ├── query.py    # Query rewriting (abbreviations, spelling, coreference)
│   │   │   ├── cache.py    # Semantic response cache (memory or Redis backend)
│   │   │   ├── corrective_rag.py # Corrective re-retrieval + clarification
│   │   │   ├── retriever.py # Hybrid retrieval (bge-m3+BM25+RRF+rerank+circuit breaker)
│   │   │   ├── indexer.py  # PDF/CSV ingestion → Qdrant
│   │   │   ├── guardrails.py # OWASP LLM Top 10 2025 (input/output/indirect injection)
│   │   │   ├── tracing.py  # OpenTelemetry GenAI 2025 semconv tracing
│   │   │   ├── database.py # SQLite + WAL + 11 tables + data retention TTLs
│   │   │   ├── postgres.py # PostgreSQL analytics backend (opt-in)
│   │   │   ├── analytics.py # Prometheus-compatible metrics + middleware
│   │   │   ├── speech_service.py # ASR (Whisper) + TTS (Piper) + MT + streaming extensions
│   │   │   ├── sunbird.py  # Sunbird AI cloud fallback (Ugandan languages)
│   │   │   ├── voice_stream.py # Streaming voice engine (VAD, VoiceSession, barge-in)
│   │   │   ├── voice_ws.py    # WebSocket handler for /v1/voice/chat/stream
│   │   │   ├── voice_consent.py # Voice consent, audit log, retention policy
│   │   │   ├── offline_rag.py # FAISS offline retrieval fallback
│   │   │   ├── accent_detector.py # Prosodic accent classifier
│   │   │   ├── flags.py    # Feature flag registry (45 flags, env-backed)
│   │   │   ├── resilience.py # Circuit breaker (exponential backoff)
│   │   │   ├── pdf_export.py # Branded PDF conversation/tax export
│   │   │   ├── evaluation.py # RAG evaluation harness (8 metrics)
│   │   │   ├── auth/       # JWT auth (HS256 dev / RS256 prod OIDC)
│   │   │   │   ├── jwt_auth.py      # Token verification + JWKS cache
│   │   │   │   ├── dependencies.py  # FastAPI DI (current_user, require_role)
│   │   │   │   └── models.py        # AuthUser, UserProfile, ConsentReceipt
│   │   │   ├── agents/     # Supervisor + specialist routing
│   │   │   │   ├── supervisor.py    # Query router (7 routes)
│   │   │   │   ├── state.py         # AgentRoute, RouteDecision enums
│   │   │   │   └── graphs/          # LangGraph orchestration (scaffolded)
│   │   │   ├── tools/      # LLM tool-calling framework
│   │   │   │   ├── __init__.py      # Tool base class + ToolRegistry
│   │   │   │   ├── calculators.py   # VAT, PAYE, capital gains, customs
│   │   │   │   ├── rates.py         # Tax rate lookups
│   │   │   │   ├── calendar.py      # Filing deadlines, fiscal year
│   │   │   │   ├── escalate.py      # Human escalation tool
│   │   │   │   └── rag_tool.py      # Knowledge base search tool
│   │   │   ├── workflows/  # Guided multi-step workflow engine
│   │   │   │   ├── registry.py      # WorkflowSession state machine
│   │   │   │   ├── loader.py        # YAML definition loader
│   │   │   │   ├── slots.py         # Slot validators (TIN, email, phone, date)
│   │   │   │   └── flows/           # YAML workflow definitions
│   │   │   ├── memory/     # Personalization memory (consent-gated)
│   │   │   │   ├── service.py       # Unified memory interface
│   │   │   │   ├── semantic.py      # User facts with decay
│   │   │   │   ├── episodic.py      # Conversation summaries
│   │   │   │   ├── working.py       # Transient session state
│   │   │   │   ├── extractor.py     # Fact extraction from turns
│   │   │   │   └── decay.py         # Time-based fact decay
│   │   │   ├── audit/      # Immutable audit ledger (UDPA compliance)
│   │   │   │   ├── ledger.py        # Hash-chained append-only log
│   │   │   │   ├── verifier.py      # Chain integrity verification
│   │   │   │   └── merkle.py        # Merkle tree proofs
│   │   │   ├── voice_stream.py      # Streaming voice engine (VAD, VoiceSession, barge-in)
│   │   │   ├── voice_ws.py         # WebSocket handler for streaming voice chat
│   │   │   ├── voice_consent.py    # Voice consent, audit log, retention policy
│   │   │   ├── offline_rag.py      # FAISS offline retrieval fallback
│   │   │   └── accent_detector.py  # Prosodic accent classifier (5 UG profiles)
│   │   ├── tests/          # Backend test suite
│   │   └── requirements.txt
│   └── frontend/           # Next.js 16 + React 19 UI
│       ├── src/
│       │   ├── app/        # App Router pages (chat, analytics, evaluation)
│       │   ├── components/ # 23 components (10 charts, 13 UI)
│       │   ├── hooks/      # useSpeech, useVoiceWebSocket, useAnalyticsDashboard
│       │   ├── services/   # voiceService, voiceWebSocket, analyticsApi
│       │   └── store/      # Zustand (useChatStore, useAnalyticsStore)
│       ├── e2e/            # Playwright E2E + a11y (axe-core)
│       ├── public/         # PWA assets + service worker (sw.js)
│       ├── package.json
│       └── next.config.mjs # CSP, security headers, API proxy
│
├── Data/                    # Training Data
│   ├── dataset/            # CSV FAQ files (41 files)
│   ├── pdfs/               # Reference PDFs (45 files)
│   ├── TTT/                # Translation corpus
│   └── lgaudio/            # Audio files
│
├── governance/              # Compliance & Risk
│   ├── compliance_check.py # CI gate (NIST/ISO/OWASP/EU AI Act)
│   └── ai_risk_manifest.yaml
│
├── ml/                      # ML Pipeline Code
│   ├── configs/
│   │   └── training_config.yaml  # Includes rag_quality_gates
│   ├── pipelines/
│   │   ├── train.py
│   │   ├── evaluate.py
│   │   ├── evaluate_rag.py       # RAG evaluation (8 metrics)
│   │   ├── export_feedback.py    # Feedback → JSONL for tuning
│   │   ├── validate_data.py
│   │   ├── quality_gates.py
│   │   └── push_to_hub.py
│   ├── scripts/
│   │   ├── prepare_kaggle_notebook.py
│   │   ├── export_tpu_ready_data.py
│   │   ├── monitor_kaggle.py
│   │   └── process_kaggle_output.py
│   └── huggingface/
│
├── Model/                   # Trained Models
│   ├── tag_classifier.joblib
│   ├── label_encoder.joblib
│   └── manifest.json
│
├── Results/                 # Training Outputs
│   ├── metrics/            # JSON metrics
│   ├── plots/              # PNG visualizations
│   └── reports/            # CSV reports
│
├── Notebooks/               # Jupyter Notebooks
│   └── ura-training.ipynb
│
├── tests/                   # Unit Tests
│   ├── test_ml_pipeline.py
│   └── test_api.py
│
├── docs/                    # Documentation
│
├── Dockerfile              # API container
├── Dockerfile.ml           # Training container
├── docker-compose.yml      # Service orchestration
├── requirements.txt        # Python dependencies
└── pyproject.toml          # Python tooling config
```

## API Endpoints (50+ routes)

### Health & Readiness
```bash
curl http://localhost:8887/health
curl http://localhost:8887/ready       # checks model + Qdrant
curl http://localhost:8887/metrics     # Prometheus text format
```

### Classification
```bash
curl -X POST http://localhost:8887/classify \
  -H "Content-Type: application/json" \
  -d '{"text": "How do I register for TIN?"}'
```

### Chat (Sync)
```bash
curl -X POST http://localhost:8887/v1/chat \
  -H "Content-Type: application/json" \
  -H "X-Session-ID: my-session" \
  -d '{"message": "What is VAT rate in Uganda?", "locale": "en"}'
```

### Chat (SSE Streaming)
```bash
curl -N -X POST http://localhost:8887/v1/chat/stream \
  -H "Content-Type: application/json" \
  -H "X-Session-ID: my-session" \
  -d '{"message": "What is VAT rate in Uganda?", "locale": "en"}'
```

### Speech Pipeline
```bash
# Transcribe audio (ASR)
curl -X POST http://localhost:8887/v1/asr \
  -H "Content-Type: application/octet-stream" \
  --data-binary @audio.pcm

# Text-to-Speech (TTS)
curl -X POST http://localhost:8887/v1/tts \
  -H "Content-Type: application/json" \
  -d '{"text": "Welcome to URA", "language": "en"}'

# Voice chat (ASR → LLM → TTS compound pipeline)
curl -X POST "http://localhost:8887/v1/voice/chat?language=en&tts_enabled=true" \
  -H "Content-Type: application/octet-stream" \
  --data-binary @audio.pcm
```

### Streaming Voice Chat (Phase 23 — WebSocket)
```bash
# Connect via websocat (install: cargo install websocat)
websocat ws://localhost:8887/v1/voice/chat/stream

# Send session_start config (JSON text frame):
# {"type":"session_start","language":"en","sample_rate":16000,"vad_sensitivity":"medium"}

# Then stream binary PCM16 audio chunks (20ms frames)
# Server responds with: transcript_final, reply_text, binary TTS audio, latency_report

# Voice audit log (admin)
curl http://localhost:8887/v1/admin/voice_audit?days=30&limit=50
```

### Feedback & Analytics
```bash
# Submit feedback
curl -X POST http://localhost:8887/v1/feedback \
  -H "Content-Type: application/json" \
  -d '{"message_id": "abc", "rating": "up", "user_query": "...", "bot_reply": "..."}'

# Dashboard data
curl http://localhost:8887/v1/analytics/dashboard?days=30
```

### Admin (Ticket Queue)
```bash
curl http://localhost:8887/v1/admin/tickets?status=open&limit=10
curl http://localhost:8887/v1/admin/tickets/stats
```

### Identity & Consent (requires JWT)
```bash
curl http://localhost:8887/v1/me -H "Authorization: Bearer <token>"
curl http://localhost:8887/v1/me/consents -H "Authorization: Bearer <token>"
```

## Running Tests

```bash
# All tests
pytest tests/ -v

# With coverage
pytest tests/ -v --cov=ml --cov=App/backend --cov-report=html

# Specific test file
pytest tests/test_api.py -v
```

## ML Pipeline Commands

### Train Model Locally
```bash
python ml/pipelines/train.py \
  --config ml/configs/training_config.yaml \
  --output-dir Model
```

### Evaluate Model
```bash
python ml/pipelines/evaluate.py \
  --model-path Model \
  --output-dir Results
```

### Validate Data
```bash
python ml/pipelines/validate_data.py
```

### Check Quality Gates
```bash
python ml/pipelines/quality_gates.py
```

### Evaluate RAG Pipeline
```bash
# English eval set
python -m ml.pipelines.evaluate_rag --eval-set Data/eval/rag_eval.jsonl

# Luganda eval set
python -m ml.pipelines.evaluate_rag --eval-set Data/eval/rag_eval_lg.jsonl
```

### Run Governance Check
```bash
python governance/compliance_check.py
```

### Index Documents into Qdrant
```bash
# Incremental upsert
python -m App.backend.app.indexer

# Recreate collection from scratch
python -m App.backend.app.indexer --recreate
```

### Export Production Feedback
```bash
python -m ml.pipelines.export_feedback
# Outputs: retriever_negatives.jsonl, regression_candidates.jsonl
```

### Push to Hugging Face
```bash
python ml/pipelines/push_to_hub.py \
  --model-path Model \
  --repo-id mpairweLandwind/ura-chatbot
```

## Triggering CI/CD Workflows

### Via GitHub CLI
```bash
# Authenticate
gh auth login

# List workflows
gh workflow list

# Run ML pipeline with training
gh workflow run ci-ml-pipeline.yml -f run_training=true -f deploy_model=true

# Run Kaggle training
gh workflow run kaggle-training.yml -f notebook=ura-training

# Run explicit TPU mode and skip EDA in data-ingestion stage
gh workflow run kaggle-training.yml \
  -f notebook=ura-training \
  -f accelerator=tpu \
  -f run_data_eda=false

# Run explicit GPU mode
gh workflow run kaggle-training.yml \
  -f notebook=ura-training \
  -f accelerator=gpu

# View run status
gh run list --workflow=ci-ml-pipeline.yml
gh run view <run-id> --log
```

### Via GitHub UI
1. Go to repository → Actions tab
2. Select workflow
3. Click "Run workflow"
4. Fill in parameters
5. Click "Run workflow"

## Troubleshooting

### Port Already in Use
```bash
# Find process using port 8000
lsof -i :8000
# Kill it
kill -9 <PID>
```

### Docker Issues
```bash
# Reset Docker
docker compose down -v
docker system prune -f
docker compose up --build
```

### Python Import Errors
```bash
# Ensure virtual environment is active
source .venv/bin/activate

# Reinstall dependencies
uv pip install -r requirements.txt --reinstall
```

### Bun/Node Issues
```bash
# Clear cache and reinstall
cd App/frontend
rm -rf node_modules bun.lockb
bun install
```

## Testing

### Backend Tests
```bash
# Run all tests with coverage (must be >= 80%)
pytest tests/ -v --cov=ml --cov=App/backend --cov-report=term-missing --cov-fail-under=80
```

### Frontend Tests
```bash
cd App/frontend

# Unit & component tests (Vitest + React Testing Library)
bun run test

# With coverage report (V8, thresholds enforced)
bun run test:coverage

# E2E smoke tests (Playwright — starts dev server automatically)
bun run test:e2e

# Accessibility audit (axe-core WCAG 2.1 AA)
bun run test:a11y
```

### Flutter Mobile Tests
```bash
cd MobileApp/ura_chatbot
flutter test --coverage --reporter expanded
```

### Load Testing
```bash
# k6 SLO validation (requires running API at localhost:8887)
k6 run tests/load/k6-chat-slo.js
```

## Operational Scripts

```bash
# Validate environment before deployment
python scripts/validate_env.py --env production

# AI red team evaluation (50 adversarial prompts, NIST AI 600-1)
python scripts/ai_red_team.py --api-url http://localhost:8887

# Bias & fairness audit (language + taxpayer parity)
python scripts/bias_fairness_audit.py --api-url http://localhost:8887

# Incident response simulation (3 AI-specific playbooks)
python scripts/incident_response_sim.py --api-url http://localhost:8887

# Disaster recovery test (Qdrant snapshot + SQLite backup)
bash scripts/dr_test.sh

# Carbon footprint tracking
python scripts/carbon_tracker.py --task training --duration 3600
```

## Monitoring Stack

```bash
# Start Prometheus + Grafana + Jaeger
docker compose --profile monitoring up -d

# Prometheus:  http://localhost:9090
# Grafana:     http://localhost:3001  (admin / ura2026)
# Jaeger:      http://localhost:16686
```

## Next Steps

1. Read [MLOps Workflows](mlops-workflows.md) for CI/CD details
2. Review [Data Schema](data-schema-and-eval.md) for data model (11 tables)
3. Review [RAG Architecture](RAG_ARCHITECTURE.md) for the 12-stage pipeline
4. Review [Agent Architecture](AGENT_ARCHITECTURE.md) for tool-calling and supervisor routing
5. Review [Model Swap Guide](MODEL_SWAP_GUIDE.md) for LLM/embedding/reranker alternatives
6. Configure GitHub secrets for deployment
7. Set up Hugging Face Space for the Gradio app
8. Review [Model Card](MODEL_CARD.md) for EU AI Act compliance
9. Review [PIA](capstone/PIA.md) for NDPA 2019 privacy assessment

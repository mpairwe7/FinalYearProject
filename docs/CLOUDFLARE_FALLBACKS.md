# Cloudflare free-tier + Gemini fallbacks

Graceful, free-tier fallbacks so the CPU-only Crane Cloud deployment degrades
*usefully* instead of dropping to keyword-only retrieval / no speech. Everything
is gated behind `FLAG_CLOUDFLARE_FALLBACK` and fires **only when the primary is
down or over budget** — when off, behaviour is unchanged.

The live deployment baseline (why this exists): `/ready` reports
`retrieval_mode: keyword` (no dense vectors — no GPU), `/v1/speech/health` is
`unavailable`. The headline win is restoring **hybrid retrieval** with no GPU by
using Cloudflare Workers AI's `@cf/baai/bge-m3` (the *exact* 1024-dim embedder
the corpus was built with) + Vectorize.

## Architecture

A single provider layer routes all inference through **one Cloudflare AI Gateway**
(caching, rate-limiting, retries, observability) and is gated by per-channel
circuit breakers + Redis budget guards (reusing `resilience.CircuitBreaker`).

| Module (`app/providers/`) | Role |
|---|---|
| `config.py` | `CloudSettings` (pydantic `SecretStr`); `is_*_configured()` gates |
| `gateway.py` | shared httpx client; **two-credential** AI Gateway headers; Workers AI embed/chat/STT + Gemini |
| `breakers.py` | per-channel `CircuitBreaker`s |
| `budget.py` | Workers AI daily-neuron + Gemini RPM guards (Redis, in-proc fallback) |
| `vectorize.py` | Vectorize query → retriever hit-dict shape |
| `r2.py` | R2 (S3) get/put/exists (lazy `boto3`) |

**Secret handling** (enforced in `gateway.py`): keys are `SecretStr`, read only
when building headers, never logged. Cloudflare uses `Authorization: Bearer <CF
token>` + `cf-aig-authorization: Bearer <gateway token>` (separate headers);
Gemini uses `x-goog-api-key`. **No key is ever placed in a URL query string.**

## Provisioning (free tier — verify current limits at setup)

1. **Cloudflare account** → `CLOUDFLARE_ACCOUNT_ID`.
2. **API token** (scopes: Workers AI Read, Vectorize Edit, R2 Edit, AI Gateway Edit) → `CLOUDFLARE_API_TOKEN`.
3. **AI Gateway** (AI → AI Gateway; enable caching + rate-limit) → `CF_AIG_GATEWAY` (slug) + `CF_AIG_TOKEN`.
4. **Vectorize**: `wrangler vectorize create ura-kb-bge-m3 --dimensions 1024 --metric cosine` → `VECTORIZE_INDEX`.
5. **R2 bucket** + S3 access key/secret → `R2_*` (10 GB free).
6. **Gemini** key from <https://aistudio.google.com/apikey> → `GEMINI_API_KEY` (free tier is RPM-limited; capped by `GEMINI_RPM`).
7. **Re-index the corpus into Vectorize** (embeds via Workers AI bge-m3 — no local torch). *CLI pending (Phase 1 follow-up);* until then upsert the 729 chunks with `wrangler vectorize insert` using vectors from `gateway.workers_ai_embed`, metadata `{text, source, page, section, tag, chunk_id}`.

Set values in the gitignored `.env` (template: `.env.example`) for local dev, or
as Crane Cloud deployment env. To activate on Crane Cloud:

```bash
FLAG_CLOUDFLARE_FALLBACK=true
DENSE_FALLBACK_BACKEND=workers_ai        # restores hybrid retrieval
LLM_FALLBACK_BACKEND=gemini              # (Phase 2)
TRANSLATE_FALLBACK_BACKEND=gemini        # (Phase 3) Luganda via Gemini 2.5 Flash
STT_FALLBACK_BACKEND=workers_ai          # (Phase 4)
```

## Status

- **Phase 1 — restore hybrid retrieval: DONE.** `providers/` package + `retriever`
  Vectorize fallback (`_init_vectorize_mode` / `_search_vectorize`). Once Vectorize
  is populated + the env is set, `/ready` flips `keyword` → `hybrid`.
- **Phase 2 — LLM fallback: DONE.** `service._llm_cloud_fallback` /
  `_stream_cloud_fallback` wired into `_call_llm_with_deadline` + `stream_llm_tokens`,
  keyed off `_LLM_CIRCUIT` (Gemini → Workers AI).
- **Phase 3 — Gemini 2.5 Flash Luganda translation: DONE.** `SpeechModel._gemini_translate`
  inserted as a tier in `_do_translate` (between local MT and Sunbird).
- **Phase 4 — STT: DONE** (`SpeechModel._cf_whisper_transcribe`, Workers AI Whisper
  tier ⑤ after Sunbird). **TTS R2 cache: deferred** (perf optimization; speech is off
  on the live profile).
- **Phase 5 — prod-validation guard: DONE** (`_validate_production_env` requires the
  CF/Gemini creds when `FLAG_CLOUDFLARE_FALLBACK=true` in prod). **Deferred provisioning
  bits:** Vectorize re-index CLI, R2 `bm25_state.json` durability, offline bundles in R2,
  `/ready` breaker/budget surfacing.

All flag-gated (`FLAG_CLOUDFLARE_FALLBACK`, default off). 17 provider/retriever/LLM/
translation unit tests; full `App/backend` suite green; blocking ruff clean.

### Deferred (follow-up, mostly provisioning-time)
1. **Vectorize re-index CLI — DONE** (`App/backend/scripts/reindex_vectorize.py`).
   Reuses the indexer's chunk loaders + `deterministic_point_id`, embeds the 729
   chunks via Workers AI bge-m3 (no torch), and upserts with `wrangler vectorize
   insert`. Run once authenticated:
   `set -a; . .env; set +a; python scripts/reindex_vectorize.py --create`
   then `… --verify "What is the VAT rate?"`.
2. **R2 `bm25_state.json` durability** — only matters for a *Qdrant* deployment that
   loses its volume; the Crane Cloud target uses Vectorize mode (lexical re-score, no
   bm25_state needed), so this is niche.
3. **TTS R2 audio cache** + **offline bundles in R2** — optimizations.

## Verification

- Unit tests (no keys): `pytest tests/test_providers.py` — gating, two-credential
  headers (no key in URL), Vectorize reshaping, budget caps, retriever Vectorize mode.
- Live (after keys): Vectorize embed→upsert→query round-trip; a Gemini Luganda
  translation; a Workers AI chat — confirm AI Gateway analytics shows cache hits on repeats.
- Live Crane Cloud re-test: redeploy with the flag on; assert `/ready` →
  `retrieval_mode: hybrid` and `/v1/chat` still answers when the primary LLM is forced down.

## Operations runbook & audit

### API-token scopes (and the gotcha we hit)
The single `CLOUDFLARE_API_TOKEN` must carry **all** of these account permissions:
**Workers AI → Read**, **Vectorize → Edit**, **AI Gateway → Read**, and **R2 → Edit**
(R2 only if the R2 features are used). `CLOUDFLARE_ACCOUNT_ID` must match the token's account.

> **Audit gotcha (2026-06-09):** Workers AI and Vectorize are *separate* scopes on the
> token. A token can succeed at embeddings (Workers AI) yet be **rejected for Vectorize
> with HTTP 403 / Cloudflare error `10000`**. If Vectorize 403s, the fix is to add
> **Vectorize → Edit to the *same* token that is in `.env`** (a common mistake is editing
> a different token, or not saving) — not to regenerate it. Token permission changes can
> take a minute or two to propagate; if it's still 403 after ~3–4 minutes it's the wrong
> token, not propagation.

### Audit-time verification (loads creds without printing them)
```bash
set -a; . .env; set +a                                   # load creds; values never printed
cd App/backend
# 1) providers see config (booleans only)
PYTHONPATH=. python -c "from app.providers import config as c; \
print('cf', c.is_cloudflare_configured(), 'vec', c.is_vectorize_configured(), 'gem', c.is_gemini_configured())"
# 2) wrangler identity (account, not the token)
wrangler whoami
# 3) Workers AI scope — expect a 1024-dim vector
PYTHONPATH=. python -c "from app.providers import gateway as g; print('embed dim', len(g.workers_ai_embed(['probe'])[0]))"
# 4) Vectorize scope — expect HTTP 200 / success:true (403 + 10000 ⇒ missing Vectorize Edit)
PYTHONPATH=. python -c "import httpx; from app.providers import gateway as g, config as c; \
s=c.get_cloud_settings(); \
r=httpx.get(f'https://api.cloudflare.com/client/v4/accounts/{s.cloudflare_account_id}/vectorize/v2/indexes', headers=g.cf_api_headers(), timeout=15); \
print(r.status_code, r.json().get('success'))"
# 5) populate + smoke
PYTHONPATH=. python scripts/reindex_vectorize.py --create
PYTHONPATH=. python scripts/reindex_vectorize.py --verify "What is the VAT rate in Uganda?"
```

### Activation state (current, for the audit trail)
- **Code:** merged to `dev` (PR #95, 2026-06-08) — providers package + Phases 1–5 + re-index CLI. Flag default **off**.
- **Provisioning:** keys present in `.env`; Workers AI + AI Gateway verified working; **Vectorize index not yet populated** — the re-index is blocked on the token's Vectorize scope (see gotcha above). Until the index is populated, `DENSE_FALLBACK_BACKEND=workers_ai` returns no hits, so the deployment stays `retrieval_mode: keyword`.
- **Free-tier envelope** (guards in `providers/budget.py`, silent degrade on exhaustion):
  Workers AI ~10k neurons/day (re-index ≈ 729 + per-query embeds), Gemini `GEMINI_RPM`
  (default 10), Vectorize (729 vectors), R2 (10 GB). All usage flows through one AI
  Gateway, so its cache further cuts neuron/RPM spend.

### Security review points
- Every key is a pydantic `SecretStr`; the value is read only inside the `gateway._*_headers`
  builders and is never logged. `.env` is gitignored; `.env.example` documents the vars with no values.
- AI Gateway uses two **separate** headers (`Authorization` + `cf-aig-authorization`); Gemini uses
  `x-goog-api-key`. **No credential is ever placed in a URL query string** — asserted in
  `tests/test_providers.py::GatewayTest`.
- Every fallback is gated by `FLAG_CLOUDFLARE_FALLBACK` + a per-channel `CircuitBreaker` +
  budget guard, so an outage or over-budget condition degrades to the existing tier rather
  than erroring.

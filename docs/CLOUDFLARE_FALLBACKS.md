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

```
FLAG_CLOUDFLARE_FALLBACK=true
DENSE_FALLBACK_BACKEND=workers_ai        # restores hybrid retrieval
LLM_FALLBACK_BACKEND=gemini              # (Phase 2)
TRANSLATE_FALLBACK_BACKEND=gemini        # (Phase 3) Luganda via Gemini 2.5 Flash
STT_FALLBACK_BACKEND=workers_ai          # (Phase 4)
```

## Status

- **Phase 1 — restore hybrid retrieval: DONE.** `providers/` package + `retriever`
  Vectorize fallback (`_init_vectorize_mode` / `_search_vectorize`), unit-tested.
  Once Vectorize is populated + the env is set, `/ready` flips to `hybrid`.
- **Phase 2** — LLM fallback (Gemini/Workers AI) in `service._call_llm_with_deadline` / `stream_llm_tokens`: pending.
- **Phase 3** — Gemini 2.5 Flash Luganda translation in `speech_service._do_translate` / `llm.translate_text`: pending.
- **Phase 4** — Workers AI Whisper STT + R2 TTS cache in `speech_service`: pending.
- **Phase 5** — R2 `bm25_state.json` durability, prod-validation block, Vectorize re-index CLI, offline bundles in R2, `/ready` breaker/budget surfacing: pending.

## Verification

- Unit tests (no keys): `pytest tests/test_providers.py` — gating, two-credential
  headers (no key in URL), Vectorize reshaping, budget caps, retriever Vectorize mode.
- Live (after keys): Vectorize embed→upsert→query round-trip; a Gemini Luganda
  translation; a Workers AI chat — confirm AI Gateway analytics shows cache hits on repeats.
- Live Crane Cloud re-test: redeploy with the flag on; assert `/ready` →
  `retrieval_mode: hybrid` and `/v1/chat` still answers when the primary LLM is forced down.

# ADR-0036: Sunbird first for answer generation, Workers AI behind it, Gemini removed

## Status

Accepted — 2026-11-09

## Context and Problem Statement

On 2026-11-09 every answer on the HF Space was a retrieved passage with the notice "I could not
write a tailored answer just now, so this is the closest passage I found". The Space logs showed
no successful generation since the container started, because every tier failed:

| Tier | Failure | Cause |
|---|---|---|
| Gemini `gemini-3.8-flash` (the cloud primary) | 403 Forbidden | The API key carries an IP-address restriction (`PERMISSION_DENIED`, `API_KEY_IP_ADDRESS_BLOCKED`, confirmed with the `.env` key). The Space's shared, changing egress addresses can never be allowlisted. |
| Workers AI through the Crane Cloud relay | 502 Bad Gateway | The relay's own call to Cloudflare failed. Cloudflare itself served the account: the token verified active, and two models answered when called directly. |
| Local vLLM | connection refused | The Space has no local model, by design. |

An API key that only works from listed IP addresses is unsuitable as the primary for a host
without fixed addresses. The project owner asked for Gemini to be removed as the primary, with
Sunbird AI and Cloudflare as the generators.

A second finding while building this: **both Sunbird accounts are suspended**. Every endpoint
answers 403 `ACCOUNT_SUSPENDED`, from here and from the Space, including the speech and
translation endpoints the Space already uses.

## Decision Outcome

The cloud chain (`service._llm_cloud_fallback`) becomes **Sunbird, then Workers AI**.

### Sunbird first
- Sunflower through Sunbird's OpenAI-compatible endpoint: `POST /tasks/chat/completions`, model
  `sunflower-9b` (`SUNBIRD_LLM_MODEL`), via `sunbird.chat_completion`.
- It shares `sunbird._post`, so the fallback account, retries and quota cooldown apply as they do
  to speech.
- It receives the full governed prompt that the local Sunflower gets (`llm._build_messages`,
  including the Qwen `/no_think` directive), and thinking text is stripped from the reply.
- Its own breaker, `SUNBIRD_LLM_BREAKER` (3 failures, 30 s to 600 s back-off), stops a suspended
  or failing account from costing a round trip on every turn.

### Locales
- **Sunbird serves every locale.** It is trained on Luganda, Runyankole, Acholi and Swahili.
- **Workers AI keeps the locale policy.** It never answers the Ugandan languages
  (`cloud_generation_allowed_for`).
- **Call turns reach neither** (`cloud_models_allowed`, `app.providers.scope`).

### Workers AI second
- Unchanged chain: Llama 3.3 70B, then Mistral Small 3.1, then Llama 4 Scout.
- When the relay answers 5xx or cannot be reached, `gateway.workers_ai_chat` now calls Cloudflare
  directly: the AI Gateway first, then `api.cloudflare.com`.
- A relay 4xx is about the request itself, so it is raised rather than retried.

### Gemini out of generation
- The Gemini branch and `GEMINI_MAX_OUTPUT_TOKENS` are gone.
- `LLM_FALLBACK_BACKEND=gemini` is read as `sunbird`, with a single warning per process. The
  Space, still configured `gemini`, therefore gets the new chain without a secret change.

### Settings and diagnosis
- Startup validation no longer requires a Gemini key for answers. It reports a `sunbird` backend
  that has neither a Sunbird token nor Cloudflare credentials.
- Sunbird refusals now log their `error_code` (for example `ACCOUNT_SUSPENDED`), not just 403.

## Consequences

- **Good:** generation no longer depends on a key that cannot work from the Space.
- **Good:** the Ugandan languages now get a generated answer from a model trained on them,
  instead of retrieval only.
- **Good:** when the relay fails, a direct Cloudflare call gets a chance.
- **Good:** less processing goes to a foreign hyperscaler for answer text.
- **Bad (until fixed outside the code):** Sunbird serves nothing while both accounts are suspended.
  Workers AI then carries English alone. On the Space that works only if Cloudflare is reachable,
  directly or through the relay. `.env.example` records TLS failures from the Space to
  `gateway.ai.cloudflare.com`; whether `api.cloudflare.com` works from there is measured after
  deploy. If neither works, answers stay passages with the disclosure.
- **Bad:** the Ugandan languages have no generator while Sunbird is suspended, because Workers AI
  must not answer them.
- **Unchanged:** Gemini still backs the FAQ judge (`FAQ_JUDGE_MODEL`) and the translation and TTS
  fallbacks, all on the same IP-restricted key, so those fail too (G538).

## Confirmation

- **Tests** (Gemini is asserted never to generate, even when configured):
  - `tests/agents/test_sunbird_primary_llm_2026_11.py`:
    - a suspended account opens the breaker after three calls while Workers AI keeps serving;
    - call turns never reach Sunbird;
    - Sunbird keeps the Qwen directive and Workers AI gets it stripped;
    - thinking text is stripped;
    - the retired setting warns once;
    - a relay 5xx or transport failure falls back to a direct call, and a 4xx does not;
    - the error code is logged.
  - `App/backend/tests/test_gemini_routing.py`: Luganda is served by Sunbird and never by Workers
    AI, even when Sunbird fails.
  - `test_providers.py`, `test_citation_normalisation.py`, `test_production_hardening.py`,
    `tests/test_fallback_integration.py` and `tests/agents/test_transparency_contract.py`.
- **Sabotage pass:** eleven controls were each disabled in turn, and ten failed a test. The
  eleventh, a defensive copy of the messages, could not be observed and was removed.
- **After deploy:** the Space logs show `LLM via Sunbird` or `LLM via Workers AI` lines, not only
  breaker failures. A fresh English question returns generated prose without the passage notice.
  Until the Sunbird suspension is lifted, expect `ACCOUNT_SUSPENDED` once per breaker window.

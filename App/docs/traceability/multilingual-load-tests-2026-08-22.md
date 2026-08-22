# Multilingual load / spike / stress / volume run — 2026-08-22

Traceability for the capacity + correctness run against the local GPU stack
exposed over ngrok. Continues issue
[#304](https://github.com/mpairwe7/FinalYearProject/issues/304) (capacity
envelope + SLOs) and adds the cross-language dimension its predecessor
lacked. Companions: `capacity-envelope-2026-08-19.md` (English-only, k6),
`local-gpu-salt-ngrok-2026-08-22.md` (the stack itself),
`docs/runbooks/salt-speech-backends.md`.

Harness: `tests/load/ngrok_multilang_suite.py`. Unlike
`tests/load/k6-chat-slo.js` it asserts on *which tier served each request*
(`retrieval_mode`, TTS/ASR `backend`, reported `locale`), not just on a 200.

## 1. What this run was for

`k6-chat-slo.js` sends `locale: "en"` on every request and measures latency
only. It cannot answer the three questions that actually matter for a
trilingual assistant: does the model still generate real text under load,
does auto language detection still fire, and does a Luganda question still
get a Luganda answer when the box is saturated?

## 2. Stack under test

Same stack as `local-gpu-salt-ngrok-2026-08-22.md` **plus the GPU fix below**
— all requests via `https://struttingly-nongeological-briella.ngrok-free.dev`.

| Layer | Value |
|---|---|
| LLM | `Sunbird/Sunflower-14B-FP8` via vLLM `v0.8.5`, GPU 2 |
| ASR | `Sunbird/asr-whisper-large-v3-salt`, **cuda:0** |
| TTS | `Sunbird/spark-tts-salt` + BiCodec, **cuda:0** |
| Retrieval | Qdrant `v1.19.0`, `ura_knowledge_base`, 729 points, dense+sparse |
| Dense / rerank | `BAAI/bge-m3` **cuda:0** / `mxbai-rerank-base-v2` **cuda:0** |
| Cache + limiter | Redis 7.4, db0 semantic cache, db1 slowapi |
| Rate limit | shipped `30/minute`; raised to `10000/minute` for §5 only |

## 3. The GPU fix this run depended on

Everything below was blocked behind one wrong pin. `requirements.txt` pins
`torch==2.12.1`, whose only wheel bundles CUDA 13, so
`torch.cuda.is_available()` was **False** and Whisper-SALT, Spark-TTS-SALT,
bge-m3 and the reranker all silently ran on CPU.

The repo previously recorded this as a driver problem needing a host
upgrade. It is not. CUDA has *minor version compatibility*: any cu12x wheel
runs on any >= 525 driver, and only the 12.x -> 13.x major step needs a new
driver branch. Proof, two containers on this same host at the same time:

| Container | torch | `cuda.is_available()` |
|---|---|---|
| `vllm/vllm-openai:v0.8.5` | 2.6.0+cu124 | **True** |
| api, before fix | 2.12.1+cu130 | False |

`Dockerfile.gpu` now pins a matched `torch==2.11.0` / `torchaudio==2.11.0`
cu128 pair (GPU image only — `requirements.txt` is untouched, the CPU image
is unaffected). That also retires the `rm _torchaudio*.so` workaround: the
pair matches, so the real compiled extension ships.

| Measurement | CPU fallback | cu128 GPU |
|---|---|---|
| Luganda `/v1/tts` (Spark-TTS-SALT) | ~150 s | **8.6 s** |
| Whisper-SALT ASR | n/a | rtf **0.20–0.44** |
| Cold hybrid `/v1/chat` | **127.7 s** | ~1–5 s |
| API container start → healthy | ~360 s | **195 s** |

## 4. Subsystem activation

Each check asserts the tier that served the request, because this deployment
degrades silently in every direction (Qdrant → Vectorize, Redis → in-process
dict, Whisper-SALT → LoRA adapters, Spark-TTS-SALT → edge-tts).

| Check | Result |
|---|---|
| Qdrant `/ready` | `retrieval_mode: hybrid` |
| Qdrant grounding (en) | `hybrid`, **2 sources** |
| Redis | `RedisSemanticCache connected`, db0 populates, db1 limiter |
| `tts_lg` | **`spark_tts_salt`**, voice `spark_salt_lg`, 8.6 s |
| `tts_sw` | **`spark_tts_salt`**, voice `spark_salt_sw`, 7.5 s |
| `tts_en` | `edge_tts` — Spark ships no English speaker id |
| `asr_lg` | **`whisper_salt`**, rtf 0.27 |
| `asr_sw` | **`whisper_salt`**, rtf 0.23 |

TTS→ASR round trip (Spark-TTS-SALT synthesises, Whisper-SALT transcribes its
own audio) — both models proven live, and the audio intelligible to the
other rather than merely non-silent:

| Locale | Spoken | Transcribed |
|---|---|---|
| lg | `Omusolo gwa VAT mu Uganda guli ku bitundu kkumi na munaana.` | `Omusolo gwa vata mu Uganda guli ku bitundu kkumi na munaana.` |
| sw | `Kodi ya VAT nchini Uganda ni asilimia kumi na nane.` | `Kodi ya Vata nchini Uganda ni asilimia kumi na nane.` |

Only "VAT" degrades (→ "vata"), as an English acronym inside Bantu prosody.

## 5. Capacity results

All phases via the tunnel, mixed en/lg/sw, `RATE_LIMIT=10000/minute`. Two
runs: **run 1** before the language fixes, **run 2** after all of §6 and §7.

### Run 2 — final (after all fixes)

| Phase | Profile | Req | Errors | p50 | p95 | p99 | max | rps |
|---|---|---|---|---|---|---|---|---|
| load | 4 VUs, 3 min | 90 | **0%** | 9.25 s | 12.78 s | 14.85 s | 14.85 s | 0.48 |
| spike | 1→20→1 VUs | 216 | **0%** | 7.16 s | 14.81 s | 19.63 s | 20.29 s | 1.93 |
| stress | 2→4→8→16→32 VUs | 455 | **0%** | 5.66 s | 22.91 s | 25.63 s | 27.79 s | 2.02 |
| volume | 6 VUs × 4 min, long multi-clause | 277 | **0%** | 5.28 s | 12.42 s | 12.47 s | 12.53 s | 1.11 |

**1038 requests, zero 5xx, zero timeouts, no degradation to 32 concurrent
users** — and **100% of requests reported the right locale AND answered in
the expected language, at every concurrency level** (en 347/347, lg 343/343,
sw 348/348).

### Run 1 — before the language fixes, for comparison

| Phase | Req | Errors | p50 | p95 | max | Luganda locale correct |
|---|---|---|---|---|---|---|
| load | 114 | 0% | 5.81 s | 15.50 s | 37.27 s | 16/38 |
| spike | 181 | 0% | 6.23 s | 22.62 s | 26.74 s | 23/53 |
| stress | 458 | 0% | 4.62 s | 23.67 s | 35.50 s | 71/164 |
| volume | 202 | 0% | 10.79 s | 11.86 s | 16.32 s | reply 38/63 |

Both runs held zero errors; the tail also tightened (worst-case max 37.3 s →
14.9 s at 4 VUs, 35.5 s → 27.8 s at 32 VUs) because reply localization stopped
making a blocking cloud call on every non-English turn.

Against NFR-01 (p95 ≤ 3 s) the stack **misses at every concurrency level**
for generative answers — consistent with the 2026-08-19 finding that one
A6000 already misses 3 s from 4 concurrent. Deterministic paths are far
inside it (English p95 **0.95–5.62 s**, mostly cache/calculator hits).

### Rate limiter (shipped config)

528 requests at 12 VUs against the shipped `30/minute`: **88 × 200, 440 ×
429**, service healthy throughout and fully recovered after the window
rolled. The limiter works — but note *what* it limits: slowapi keys on
`get_remote_address`, and `TRUSTED_PROXY_HOSTS` defaults to loopback only,
so the frontend container is not a trusted proxy and **every public request
is attributed to one container IP**. All tunnel traffic shares a single
30/minute bucket regardless of how many distinct callers there are.

## 6. Language findings

### 6a. Auto language detection — fixed, 43% → 100%

`detect_language()` ran the Runyankole/Acholi/Swahili marker patterns first
and returned on a hit, so lingua was never consulted. Luganda and Runyankole
share the `oku-`/`omu-`/`eby-` prefixes `_NYN_PREFIXES` matches, so Luganda
sentences returned `nyn` — and since `nyn` ∉ `SUPPORTED_LOCALES`,
service.py's gate declined to promote the locale and **answered in English**.

| Phase | Luganda locale correct | Kiswahili | English |
|---|---|---|---|
| functional (before) | **3/6** | 6/6 | 6/6 |
| load (before) | 16/38 | 42/42 | 34/34 |
| spike (before) | 23/53 | 76/76 | 52/52 |
| stress (before) | 71/164 | 138/138 | 156/156 |
| functional (**after**) | **6/6** | 6/6 | 6/6 |

Stable at ~43% across 255 Luganda requests — a property of the sentence, not
of load. lingua calls the same six `lg` at 0.91–0.998 confidence. The fix
lets lingua pre-empt the markers only for a locale it actually knows and is
confident about; nyn/ach still detect correctly (verified).

### 6b. Replies now localised — was English for lg/sw, fixed

After 6a, detection was right but the answer often was not: with `locale: lg`
correctly reported, the `calculator` path returned English verbatim, and in
run 1's volume phase only 38/63 lg and 34/66 sw replies were in-language.

Cause was in `localize_reply()`, which called `sunbird.translate_from_english`
directly — cloud-only, so every non-English turn made a blocking network call
that returned **HTTP 429** once the run had any volume. Its docstring
justified Sunbird-only on the grounds that the generation model produced
repetition loops ("kozesa kozesa kozesa…"); that was a property of the old
prompt shape (see 6c), not of the model. It now tries the local model first
(`REPLY_MT_BACKEND`, default `local_first`), keeping Sunbird as fallback and
the existing collapsed-response length guard as the safety net.

Run 2: **1038/1038 replies in the expected language**, all locales, all
concurrency levels.

### 6c. Translation quality — fixed via the model card's own prompt shape

`llm.translate_text()` used a generic "you are a professional translator"
system prompt with the bare text as the user turn. Sunflower-14B's
[model card](https://huggingface.co/Sunbird/Sunflower-14B) documents the
opposite: a fixed Sunflower persona system prompt with the directive in the
**user** turn — `"Translate from Luganda to English: ..."`. The difference is
not cosmetic; the old shape returned the SOURCE language on some inputs,
leaving a retrieval query exactly as unsearchable as before.

The card's shape alone still **answered** interrogatives instead of
translating them. `"EFRIS kye ki era ani alina okugikozesa?"` came back as an
invented definition of EFRIS — refugee data, funds remittance, bribery
reporting, a different hallucination each run — and temperature made no
difference (tested 0.0 and 0.3). Adding *"it may be a question — translate
the question itself, do not answer it"* fixed it:

| Input | Before | After |
|---|---|---|
| `EFRIS kye ki era ani alina okugikozesa?` | "…a system that enables the registration of all firearms and their owners." | **"What is EFRIS and who should use it?"** |
| `Bibonerezo ki ebiriwo bw'olwawo…` | (source language) | **"What are the penalties for late submission of tax returns?"** |
| `Kiwango cha kodi ya VAT nchini Uganda…` | (Swahili, untranslated) | **"What is the Value Added Tax rate in Uganda?"** |
| en→lg `What is the VAT rate in Uganda?` | repetition loops | **"Mu Uganda omusolo gwa VAT guli ku bitundu 18 ku buli kikumi."** |

7 of 8 bank items now translate faithfully. Decoding is greedy — the same
input producing a different invented answer each call is the failure mode
being guarded against; `_vllm_generate` gained per-call sampling overrides.
Residual: the Swahili EFRIS item still expands the acronym rather than
translating it.

Two plumbing bugs found on the way, both fixed:

1. Both retrieval paths called `sunbird.translate_to_english` **directly**, so
   one cloud timeout took out both. Measured: `/tasks/translate` used its full
   30 s and returned nothing (single account, no same-account retry — #298).
   Consolidated onto `query.translate_query_for_retrieval`, local-first and
   configurable via `RETRIEVAL_MT_BACKEND`, verifying the output is English.
2. `llm.translate_text()` was **dead on every vLLM deployment** — it went
   straight to the in-process Transformers model, and `_load_model()` returns
   early under `LLM_BACKEND=vllm` by design, so it logged "transformers/torch
   not installed" with both installed. Fixed to dispatch on `LLM_BACKEND`.

### 6c-bis. lg/sw hybrid retrieval still abstains — open

Translation is now correct end to end: `english_retrieval_query()` returns
`'What is EFRIS and who should use it?'` for the Luganda input, and that exact
English question grounds (`hybrid`, 2 sources). The Luganda request
nonetheless still returns `abstained` / `no_retrieval_results`. The
translate-then-retrieve second pass (`HybridRetriever._merge_translated_leg`,
G18) is reached and its `translate_retrieve` flag is enabled, so the remaining
gap is in how that leg's hits are merged or gated, not in translation. Not
closed here — see #302.

### 6d. Abstentions are cached

An abstained answer is stored in the semantic cache, so a question keeps
abstaining after the underlying cause is fixed until db0 is flushed. This
masked the retrieval fixes above during testing.

## 7. Proxy timeout fixed

Next's rewrite proxy defaults to `proxyTimeout: 30000` and answers a bare
`Internal Server Error`. Measured at exactly **30.009 s** against the
frontend directly (so: not ngrok). A successful 36.7 s backend answer
surfaced to the caller as a 500 — a misreported failure. Only the
non-streaming `/v1/chat` is affected; `/v1/chat/stream` sends headers
immediately, which is why the UI never hit it. Now
`experimental.proxyTimeout`, default 180 s, `PROXY_TIMEOUT_MS` to override.

## 8. Reproducing

```bash
cd App && GPU_ID=2 docker compose \
  -f docker-compose.yml -f docker-compose.local-sunflower.yml \
  -f docker-compose.gpu-salt.yml up -d --build

python3 tests/load/ngrok_multilang_suite.py --phase subsystems
python3 tests/load/ngrok_multilang_suite.py --phase functional,ratelimit

# capacity phases need the limiter raised, or they measure only the limiter
docker compose ... up -d --no-deps api   # with RATE_LIMIT=10000/minute
python3 tests/load/ngrok_multilang_suite.py --phase load,spike,stress,volume \
  --out results.json
```

Flush `db0` between correctness runs (§6d).

## 9. Still open

- lg/sw hybrid retrieval abstains where English grounds, now with translation
  proven correct — so the gap is in the G18 merge/gate (#302).
- Swahili "EFRIS ni nini" still expands the acronym rather than translating it.
- p95 misses NFR-01's 3 s for generative answers at every concurrency (#304).
- One 30/minute bucket for all tunnel traffic — set `TRUSTED_PROXY_HOSTS`.
- No local English TTS voice; English falls to cloud edge-tts.
- Spark-TTS-SALT output still has no perceptual/listening verification.

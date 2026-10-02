# Local GPU stack + SALT speech + ngrok — verified 2026-08-22

> **Historical record — two conclusions below have since been overturned.**
> §3 accepts CPU fallback for both SALT tiers and §7 calls GPU execution
> "blocked on this host's driver ceiling". That diagnosis was wrong: it was a
> wheel problem, not a driver problem. `Dockerfile.gpu` now pins a matched
> `torch`/`torchaudio` 2.11.0+cu128 pair and both tiers run on the GPU on this
> same host. `docker-compose.gpu-salt.yml` sets `cuda:0`, not `cpu`. See
> `local-gpu-salt-ngrok-2026-09-04.md` for the current bring-up and numbers.
> Everything else here — the overlay's purpose, the read-write NAS cache fix,
> the tunnel recipe — still holds.

Traceability for the single-GPU local deployment that bakes in both
Sunbird SALT speech backends (`Sunbird/asr-whisper-large-v3-salt`,
`Sunbird/spark-tts-salt`) alongside the local `Sunflower-14B-FP8` LLM,
Redis, and Qdrant, exposed publicly over the existing ngrok reserved
domain. Companion docs: `docs/runbooks/salt-speech-backends.md` (model
behavior, known bugs, prior isolated verification),
`App/docs/traceability/capacity-envelope-2026-08-19.md` (Sunflower-14B-FP8
load numbers on this same hardware class).

## 1. Intent

`docker-compose.local-sunflower.yml` (LLM) and the SALT backends
(`App/backend/app/spark_tts_salt.py`, `speech_service.py`) had each been
verified independently, but never brought up together as one running
stack, GPU-pinned, and reached from outside the host. This run does that:
one command, one named GPU, every component real (no mocks/stubs), public
HTTPS access, and the actual request/response evidence kept here rather
than only in scrollback.

## 2. Stack under test

| Layer | Value |
|---|---|
| Date | 2026-08-22 |
| Host | shared 8x NVIDIA RTX A6000 48 GiB sandbox; driver reports CUDA 12.2 |
| Pinned GPU | **GPU 2** (`GPU_ID=2`), selected idle at the time via `nvidia-smi` |
| Compose overlay order | `docker-compose.yml` → `docker-compose.local-sunflower.yml` → `docker-compose.gpu-salt.yml` |
| API image | built from `Dockerfile.gpu` (repo root) — bakes in the pinned-commit Spark-TTS BiCodec checkout; see that file's own header comments for the torch/torchaudio CUDA-generation mismatch it works around |
| LLM | local on-disk `App/Model/Sunflower-14B-FP8`, served by `vllm/vllm-openai:v0.8.5` as `Sunbird/Sunflower-14B-FP8`, real GPU (Marlin FP8 kernel) |
| ASR | `Sunbird/asr-whisper-large-v3-salt`, `WHISPER_SALT_DEVICE=cpu` |
| TTS | `Sunbird/spark-tts-salt` + BiCodec, `SPARK_TTS_DEVICE=cpu` |
| Qdrant | `qdrant/qdrant:v1.19.0`, collection `ura_knowledge_base` |
| Redis | `redis:7.4-alpine` |
| Tunnel | ngrok reserved domain `struttingly-nongeological-briella.ngrok-free.dev` → `localhost:3032` (frontend) |

Bring-up:

```bash
cd App
GPU_ID=2 docker compose \
  -f docker-compose.yml -f docker-compose.local-sunflower.yml \
  -f docker-compose.gpu-salt.yml up -d --build
```

`docker-compose.gpu-salt.yml` is the new overlay this run added (see its
own header comment for the full rationale of each override); the two
things it does that the base + `local-sunflower` files don't:

1. Builds `api` from `Dockerfile.gpu` instead of the base `Dockerfile`, so
   Spark-TTS-SALT's `SPARK_TTS_REPO_DIR` checkout is actually present.
2. Pins both GPU consumers (`api`, `vllm`) to `${GPU_ID:-2}` via
   `NVIDIA_VISIBLE_DEVICES` and `deploy.resources.reservations.devices`.

## 3. Decision: CPU fallback for both SALT tiers, accepted deliberately

`Dockerfile.gpu` bakes `SPARK_TTS_DEVICE=cuda` / `WHISPER_SALT_DEVICE=cuda`
by default. On this host that raises at `load()` instead of degrading
gracefully — `docs/runbooks/salt-speech-backends.md` already documents why:
`App/backend/requirements.txt` pins `torch==2.12.1`, whose only published
Linux wheel bundles CUDA 13.0-generation runtime libs, and this host's
driver reports CUDA 12.2 (`RuntimeError: The NVIDIA driver on your system
is too old (found version 12020)`). This is a host driver ceiling, not a
config bug — no compose/Dockerfile knob fixes it (see `Dockerfile.gpu`'s
own header for why a build-arg attempt at this was tried and reverted).

Given that constraint, the explicit choice for this run was: **enable both
SALT tiers anyway, accept CPU fallback**, rather than leaving them disabled.
`docker-compose.gpu-salt.yml` overrides both device vars to `cpu` so the
tiers load in their documented degraded mode instead of failing at
container start. The LLM (vLLM, its own image/torch build) and dense
retrieval embeddings are unaffected — real GPU 2 the whole time.

## 4. Fix carried by this run: `bge-m3`/reranker not in the read-only NAS cache

The base compose file mounts the shared NAS HF cache read-only
(`HF_HUB_OFFLINE=1`). `BAAI/bge-m3` (dense retriever) and
`mixedbread-ai/mxbai-rerank-base-v2` were not already present in it, so
with the base config dense retrieval silently fell back to the Cloudflare
Vectorize path instead of local Qdrant — not what "use qdrant vectordb"
calls for. `docker-compose.gpu-salt.yml` remounts the same NAS path
read-write and sets `HF_HUB_OFFLINE=0` for `api` only, letting the one-time
fetch persist to the shared cache instead of re-downloading on every future
run. Confirmed after the fix:

```
HybridRetriever ready (url=http://qdrant:6333 collection=ura_knowledge_base
  dense_device=cuda:0 rerank=True reranker_device=cpu)
```

## 5. Verified smoke flow (ngrok, anonymous)

```bash
curl -sS https://struttingly-nongeological-briella.ngrok-free.dev/api/health
curl -sS -X POST https://struttingly-nongeological-briella.ngrok-free.dev/api/v1/chat \
  -H "Content-Type: application/json" -H "X-Session-ID: gpu-salt-smoke" \
  -d '{"message":"How do I register for a TIN?","locale":"en"}'
curl -sS https://struttingly-nongeological-briella.ngrok-free.dev/api/v1/speech/health
curl -i https://struttingly-nongeological-briella.ngrok-free.dev/api/v1/admin/tickets/stats   # stays 401
```

All four passed: real Sunflower-14B-FP8 chat responses (including
tool-calling/calculator retrieval_mode), local Qdrant-backed retrieval
(`retrieval_mode: hybrid`), both SALT model tiers reporting ready, and the
admin route staying protected through the tunnel.

## 6. Spark-TTS-SALT live request — slow, not broken

A real (non-English) `/v1/tts` request through the tunnel:

```bash
curl -X POST https://struttingly-nongeological-briella.ngrok-free.dev/api/v1/tts \
  -d '{"text":"Omusolo gwa EFRIS gusasulwa gutya?","language":"lg"}' --max-time 90
```

returned `Internal Server Error` client-side at ~30.6s. **The request had
not actually failed.** Container logs (timestamps below) show it was still
generating server-side, and completed successfully after the client had
already given up:

```
12:52:51.537  app.spark_tts_salt: Loading Spark-TTS-SALT 'Sunbird/spark-tts-salt' (device=cpu)
12:53:23.529  app.spark_tts_salt: Spark-TTS-SALT ready (device=cpu, sample_rate=16000)
12:55:19.104  INFO 172.29.0.6:57660 - "POST /v1/tts HTTP/1.1" 200 OK
```

~32s cold model load + CPU-bound LLM-half generation past that put total
latency at roughly 2.5–3 minutes — consistent with the CPU-fallback
slowness `docs/runbooks/salt-speech-backends.md` already documents, just
now measured through the full container + tunnel path rather than in
isolation. The client-side error was an intermediary (curl's own
`--max-time`, or the ngrok free-tier edge) giving up before that, not an
application crash or a Spark-TTS-SALT failure. **Operator note:** a client
consuming this tier under CPU fallback needs a timeout budget of several
minutes, not the ~30–90s that suffices for every other endpoint in this
stack; this is a real UX gap on CPU fallback, not just a test artifact —
worth a raised `SUNBIRD`-style timeout or an async job pattern if this tier
is ever exposed to real users on hardware without a matching driver.

## 7. What this run does not close out

- **Perceptual verification** of Spark-TTS-SALT output is still open —
  `docs/runbooks/salt-speech-backends.md` §"Still not done" already flags
  this; this run adds a real end-to-end latency measurement, not a listening
  pass.
- **GPU-execution verification** of both SALT tiers is still blocked on this
  host's driver ceiling specifically, not on the code — see §3. Re-run on a
  host with a CUDA-13-generation-compatible driver to get a real GPU number
  for `SPARK_TTS_DEVICE=cuda`/`WHISPER_SALT_DEVICE=cuda`.
- This run mounted the NAS HF cache read-write only for `api`
  (`docker-compose.gpu-salt.yml`); the base file's read-only mount is
  otherwise unchanged and remains correct for hosts where `bge-m3`/reranker
  are already warmed.

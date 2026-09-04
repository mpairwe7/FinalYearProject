# Local GPU stack + SALT speech + ngrok — verified 2026-09-04

Bring-up record for the fully-local single-GPU deployment: `Sunflower-14B-FP8`
via vLLM, `Sunbird/asr-whisper-large-v3-salt`, `Sunbird/spark-tts-salt`,
`BAAI/bge-m3` + cross-encoder reranker, Redis and Qdrant — every one of them
on the pinned card, reached over the project's reserved ngrok domain.

Supersedes the CPU-fallback conclusions of
`local-gpu-salt-ngrok-2026-08-22.md` (§3, §7). Companion:
`docs/runbooks/salt-speech-backends.md`.

## 1. Stack

| Layer | Value |
|---|---|
| Date | 2026-09-04 |
| Host | shared 8x NVIDIA RTX A6000 48 GiB; driver reports CUDA 12.2 |
| Pinned GPU | **GPU 7** (`GPU_ID=7`), chosen by `scripts/select_free_gpu.sh` |
| Overlay order | `docker-compose.yml` → `local-retrieval.yml` → `local-sunflower.yml` → `gpu-salt.yml` |
| API image | `app-api:gpu`, rebuilt from `Dockerfile.gpu` — `torch 2.11.0+cu128`, `torchaudio 2.11.0+cu128` |
| LLM | `App/Model/Sunflower-14B-FP8` served by `vllm/vllm-openai:v0.8.5` as `Sunbird/Sunflower-14B-FP8` |
| ASR | `Sunbird/asr-whisper-large-v3-salt`, `WHISPER_SALT_DEVICE=cuda:0` |
| TTS | `Sunbird/spark-tts-salt` + BiCodec, `SPARK_TTS_DEVICE=cuda:0` |
| Retrieval | Qdrant `v1.19.0`, alias `ura_knowledge_base_jsonl_active`, dense + reranker both `cuda:0` |
| Tunnel | `struttingly-nongeological-briella.ngrok-free.dev` → `localhost:3032` (frontend) |

```bash
cd App
GPU_ID=7 docker compose \
  -f docker-compose.yml -f docker-compose.local-retrieval.yml \
  -f docker-compose.local-sunflower.yml -f docker-compose.gpu-salt.yml \
  up -d --build

ngrok http 3032 --url=struttingly-nongeological-briella.ngrok-free.dev
```

The ngrok agent is a separate long-lived process, not part of the stack; it
survives `compose down` and serves 502 until the frontend is back.

## 2. Fix this run carried: the serving profile was starting with the LLM and speech switched off

`docker-compose.local-retrieval.yml` is an **indexing** profile — it sets
`LLM_ENABLED: "false"` and `SPEECH_ENABLED: "false"` so a corpus rebuild never
pays to load them. Nothing later in the chain turned them back on, so every
`api` container brought up with this four-file overlay — including the run
that had been live for the previous 39 hours — was serving with:

- `LLM_ENABLED=false` — vLLM was running, healthy, holding ~28 GB of the card,
  and never being called. Answers came from the retrieval/workflow path only.
- `SPEECH_ENABLED=false` — `SpeechService` short-circuits to
  `backend="disabled"` before constructing anything, so neither SALT tier was
  ever loaded and `/v1/tts` + `/v1/asr` answered without touching a model.

Nothing failed loudly; both containers reported healthy throughout. Fixed by
restating both flags in `docker-compose.gpu-salt.yml` — the serving profile,
and last in the chain — for the same reason it already restates `QDRANT_*`:
a preceding overlay's default is not a safe default for this one.

## 3. Verified on GPU

`app-api:gpu`, run directly against GPU 7:

```
torch 2.11.0+cu128   torchaudio 2.11.0+cu128
cuda_available True  NVIDIA RTX A6000
sparktts + BiCodecTokenizer import OK
_torchaudio .so present: True
```

Container startup — every tier on `cuda:0`, nothing degraded:

```
HybridRetriever ready (url=http://qdrant:6333
  collection=ura_knowledge_base_jsonl_active
  dense_device=cuda:0 rerank=True reranker_device=cuda:0)
ChatModel initialised – hybrid (Qdrant) mode,
  LLM (Sunbird/Sunflower-14B-FP8) gen, 41 tags, 14 workflows
Loading Whisper-SALT 'Sunbird/asr-whisper-large-v3-salt' (device=cuda:0)
Loading Spark-TTS-SALT 'Sunbird/spark-tts-salt' (device=cuda:0)
SpeechModel warm-up: {'en': 'edge_tts', 'lg': 'spark_tts_salt', 'sw': 'spark_tts_salt'}
```

Cold start ≈ 7 min total: vLLM ~3.5 min (of which `torch.compile` 80.8s, KV
cache 73,136 tokens, max concurrency 17.86x at 4,096 tokens/request), then
`api` ~3.5 min (Whisper-SALT 64s, Spark-TTS-SALT 65s).

**Occupancy: 45.4 GB of 49.1 GB on GPU 7**, which includes ~5.9 GB belonging
to another tenant already on the card. ~3.7 GB headroom — enough to serve, not
enough to benchmark alongside. Put vLLM on a second card or lower
`SUNFLOWER_GPU_MEM_UTIL` (default 0.70) before running load tests here.

## 4. Smoke flow through the tunnel (anonymous)

| Call | Result |
|---|---|
| `/api/health` | `{"status":"alive","version":"1.2.0"}` |
| `/api/v1/chat` "How do I register for a TIN?" | `model=Sunbird/Sunflower-14B-FP8`, `retrieval_mode=workflow`, 0.6s |
| `/api/v1/chat` "What is EFRIS?" | `retrieval_mode=hybrid`, 4 local sources, grounded answer, 2.6–3.8s |
| `/api/v1/chat` Luganda TIN question | `model=Sunbird/Sunflower-14B-FP8`, 3.3s |
| `/api/v1/speech/health` | `{"status":"ready","enabled":true,...}` |
| `/api/v1/tts` Luganda | `backend=spark_tts_salt`, `voice=spark_salt_lg`, **4.3s** for 2.7s of audio |
| `/api/v1/tts` English | `backend=edge_tts`, 0.4s |
| `/api/v1/asr` Luganda | `backend=whisper_salt`, **0.72s**, RTF 0.27 |
| `/api/v1/admin/tickets/stats` | `401` — stays protected through the tunnel |

Hybrid sources returned for "What is EFRIS?", all from the local Qdrant alias:
`ura_edition_02_teeny_faqs.csv`, `THE-EFRIS-HANDBOOK-2024-25-2.pdf`,
`A-guide-to-taxation-of-the-Hotel-and-Accomodation-Sector-2025-26.pdf`,
`ura.go.ug-EFRIS - Uganda Revenue Authority.pdf`.

### TTS → ASR round trip

Synthesized `"Omusolo gwa EFRIS gusasulwa gutya?"` with Spark-TTS-SALT and fed
the resulting PCM straight back to Whisper-SALT:

```
"Omusolo gwa eifalisi kusasulwa gutya?"   language: "lg"
```

Only drift is the acronym coming back phonetically — what a speaker saying
EFRIS aloud sounds like. This is the first machine evidence in this project
that Spark-TTS-SALT's Luganda output is *intelligible*, not merely
well-formed; the human listening pass the runbook asks for is still open.

### Two API shapes that are easy to get wrong

- **`/v1/asr` is not a multipart upload.** Body is raw PCM (int16 LE or
  float32, mono); `sample_rate` and `language` are query parameters. Posting a
  `.wav` makes the RIFF header and multipart boundaries get read as audio, and
  Whisper hallucinates a fluent sentence out of the noise rather than
  erroring — during this run a `-F file=@…` call returned the confident
  transcript `"*Eldad is getting mad at his mom*"`. Strip the header first.
  `language` is genuinely optional; auto-detect returned the same transcript.
- **`/v1/tts` containers differ by locale.** English (`edge_tts`) returns
  **MP3** at 24 kHz; the SALT locales return **RIFF WAV** at 16 kHz.

## 5. Open, not closed by this run

- **`app-qdrant-backup-1` is permanently `unhealthy` while working
  correctly.** Its healthcheck GETs `http://127.0.0.1:8000/health` inside its
  own container, but that service is a snapshot job, not an HTTP server, so
  the probe always gets `Connection refused`. The job itself is fine — it
  wrote a 108 MB snapshot of the active alias during this run. The
  healthcheck looks copied from `api`. Pre-existing; nothing depends on this
  service's health condition, so it blocks nothing.
- **Perceptual verification of Spark-TTS-SALT** by a human listener — the
  round trip above narrows it but does not replace it.
- **English TTS still leaves the host**: `edge_tts` is a network call.
  Spark-TTS-SALT ships no English speaker id and no local piper voice is
  present in `artifacts/`, so `en` is the one locale this "fully local" stack
  does not serve locally.

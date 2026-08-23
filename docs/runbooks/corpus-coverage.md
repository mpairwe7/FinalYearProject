# Runbook — corpus coverage against real taxpayer questions

Operator guide for issue #303. The harness is
`ml/pipelines/corpus_coverage.py`; the gate that makes it binding is
`tests/test_corpus_coverage_gate.py` plus the **Corpus coverage gate** step in
`.github/workflows/ci-ml-pipeline.yml`.

## What problem this solves

Every other retrieval gate in this repository scores rows the corpus was built
from. `App/backend/tests/test_retrieval_regression_gate.py` asks each indexed
FAQ its own question; `ml/pipelines/evaluate_rag.py` scores
`Data/eval/rag_eval.jsonl`. Both answer *"does retrieval still return what it
returned yesterday"*. Neither can answer *"is there a common taxpayer question
the corpus cannot answer at all"* — by construction, every question they ask
already has an answer indexed.

That blind spot is how the objections gap reached production: the only indexed
sentence on the subject was "Yes. You may lodge an objection if dissatisfied
with an assessment", so self-retrieval was perfect while a taxpayer asking
"how do I object to a tax assessment I disagree with?" got the abstention copy.

This harness probes the other way round, from a curated bank of questions
phrased the way contact-centre callers phrase them.

## The three files

| File | What it is |
| --- | --- |
| `Data/eval/coverage_bank.jsonl` | The question bank. One row per question, with `en`/`lg`/`sw` phrasings and `expect_any` — the facts a correct answer has to carry. |
| `Data/eval/coverage_domains.yaml` | Domain registry: which corpus CSVs answer for each domain, that domain's floor, and its review record. |
| `ml/pipelines/corpus_coverage.py` | The harness. Three modes; see below. |

## Statuses

| Status | Meaning |
| --- | --- |
| `answered` | A grounded reply carrying one of `expect_any`. This is the only thing that counts as coverage. |
| `weak` | Something was retrieved, but none of the expected facts are in it — usually the wrong FAQ, answered confidently. |
| `abstained` | Nothing retrieved. |
| `deflected` | The product deliberately answered something else — a clarifying question, or an escalation to an officer. Counts against coverage; listed under its own status so policy escalation is distinguishable from a miss. |
| `skipped` | Never asked. Corpus mode skips non-English questions unless `--translate` is passed, because the indexed corpus is English. |
| `error` | The probe itself failed (transport, missing translation, a voice stage returning an error). |

## Modes

### `--mode corpus` (the CI gate)

Offline. Drives the keyword + priority + filter path in `app.service` — what
production serves from when Qdrant and the LLM are absent. No network, no
model, no vector store; runs in seconds.

```bash
cd App/backend
PYTHONPATH=.:$PWD/../.. LLM_ENABLED=false QDRANT_ENABLED=false SPEECH_ENABLED=false \
  python -m ml.pipelines.corpus_coverage --languages en --fail-under-floor
```

English only by default, and that is deliberate: the corpus is English, so a
Luganda or Kiswahili figure measured here is a figure about the translator, not
about the corpus. Pass `--translate` to translate before retrieving (needs a
reachable MT backend) if you want the corpus-side Luganda number.

### `--mode api` (release evidence)

Drives a running deployment over `POST /v1/chat`, so the figure covers the
whole pipeline — hybrid retrieval, the LLM, guardrails, localisation. This is
where the Luganda and Kiswahili numbers come from.

```bash
cd App/backend
PYTHONPATH=.:$PWD/../.. python -m ml.pipelines.corpus_coverage \
  --mode api --base-url http://127.0.0.1:8083 --languages en,lg,sw \
  --timeout 180 --output ../../Results/eval/coverage_api.json
```

Budget roughly 10–20 s per probe against a local GPU stack: the full bank in
three languages is 315 probes, about 100 minutes.

With `--output`, the report is rewritten after every bank entry, so an
interrupted sweep still leaves a usable partial file — check `meta.complete`
and `meta.probes_done` before quoting a figure from one.

### `--mode voice` (spoken pipeline)

Each question is synthesized with `POST /v1/tts`, the audio is posted to
`POST /v1/voice/chat`, and the reply is scored the same way — so a domain that
answers in text and abstains in voice shows up as a gap even though the corpus
is identical. Four model calls per probe, so it samples one question per
domain per language unless told otherwise.

```bash
cd App/backend
PYTHONPATH=.:$PWD/../.. python -m ml.pipelines.corpus_coverage \
  --mode voice --base-url http://127.0.0.1:8083 --languages en,lg,sw \
  --sample-per-domain 1 --timeout 240 --output ../../Results/eval/coverage_voice.json
```

The report adds a voice block: TTS backend and voice actually used per
language, ASR backend, mean transcript recall, empty transcripts, and replies
that came back with no audio. Transcript recall is round-trip fidelity of
synthesized speech, **not** a WER claim against human speech — for that, use
the Common Voice Luganda clips under `Data/lgaudio/`.

Two things to check in that block before believing the numbers:

**Which voice actually spoke.** Sunbird's native `salt_lug_0001` (Luganda) and
`waxal_swa_0006` (Kiswahili) are the voices this product should be heard in,
and the chain tries them first — but the first call to
`/tasks/audio/speech` is a cold start that overruns `SUNBIRD_TIMEOUT`, and
`sunbird._post` does not retry the same account. With only one account token
configured (`SUNBIRD_API_TOKEN` unset, `SUNBIRD_FALLBACK_API_TOKEN` set) there
is nothing to fail over to, so Luganda silently degrades to the English
`en-KE-ChilembaNeural` edge-tts stand-in. `docker-compose.coverage-gpu.yml`
raises the timeout to 90 s for local runs; the real fix is the second token
(issue #298). If `tts_voices` shows `en-KE-ChilembaNeural` for `lg`, the run
measured the stand-in, not the product.

**What format the audio is in.** Sunbird returns a real RIFF WAV; edge-tts
returns MP3 — in a field `SynthesizeResponse` documents as "Base64-encoded WAV
bytes". The runtime image has no ffmpeg, pydub, PyAV or soundfile, so
`speech_service._decode_container` cannot decode MP3, WebM or OGG and logs
"Could not decode container audio — treating as raw PCM". Anything that
arrives compressed therefore transcribes to noise, including the browser's own
`audio/webm;codecs=opus` recordings. Measured: a Common Voice clip that
transcribes correctly as WAV returns `"."` when posted as WebM/Opus.

## Bringing up a local GPU stack to run the api/voice modes

`App/docker-compose.coverage-gpu.yml` pins the two GPU consumers to specific
devices, which matters on a shared box. Pick idle GPUs first
(`bash scripts/select_free_gpu.sh`).

```bash
cd App
QWEN_GPU_ID=3 API_GPU_ID=4 docker compose \
  -f docker-compose.yml -f docker-compose.local-qwen.yml -f docker-compose.coverage-gpu.yml \
  up -d qdrant redis vllm api
```

vLLM takes ~5 minutes to load Qwen3-8B and compile; the API is ready when
`curl -s localhost:8083/ready` reports `"status": "ready"`. Check
`curl -s localhost:8083/v1/speech/health` before a voice run — `asr_backend`
and `tts_backend` are the *configured* values, so confirm real ones with a
single probe (`POST /v1/tts` should report `backend: edge_tts` or
`sunbird_cloud`, never `mock`). **Tear it down when
you are finished** — this is a shared machine:

```bash
cd App && docker compose -f docker-compose.yml -f docker-compose.local-qwen.yml \
  -f docker-compose.coverage-gpu.yml down
```

## Floors, and how to change one

Each domain's `floor` sits one bank question below its measured rate — the
resolution a four-to-six-question domain actually has. `overall_floor` gates
the whole bank, which has the resolution the per-domain figures lack.

A floor may be **raised** freely once a gap is closed. **Lowering one requires
a reason in the commit message**, because the only legitimate reason is that
the bank grew harder, not that the corpus got worse.

To re-measure after a corpus change:

```bash
cd App/backend
PYTHONPATH=.:$PWD/../.. LLM_ENABLED=false QDRANT_ENABLED=false SPEECH_ENABLED=false \
  python -m ml.pipelines.corpus_coverage --languages en --output /tmp/coverage.json
```

## Adding corpus content

The gate fails if a new `ura_*_faqs.csv` is not listed under a domain in
`coverage_domains.yaml`, and if a domain has no question in the bank. So the
order is:

1. Add the CSV to `Data/dataset/`.
2. Add its filename to a domain's `sources` in `coverage_domains.yaml`
   (or add a new domain with a `floor` and a `review` block).
3. Add at least one question to `coverage_bank.jsonl` in all three languages,
   with `expect_any` naming the facts the answer must carry.

## URA domain-owner sign-off

Each domain carries a `review` block: `by`, `role`, `date`. `role:
ura_domain_owner` is the only value that counts as URA sign-off.

Every run prints `pending_ura_signoff`, and the CI step summary names the
domains still waiting. **No domain has been signed off yet** — the whole bank
is engineering-drafted from URA contact-centre themes and needs a domain owner
to confirm the questions are the ones taxpayers actually ask and that
`expect_any` names the right facts. The Luganda and Kiswahili phrasings need a
native speaker in the same pass.

Once a domain is signed off, set its `review` block and re-run. When every
domain is signed off, add `--require-ura-signoff` to the CI step and delete
`test_domains_still_awaiting_ura_sign_off_are_reported_not_hidden`'s assertion
that the pending list still covers everything.

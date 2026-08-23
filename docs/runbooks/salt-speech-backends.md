# Runbook — Sunbird SALT speech backends

Operator guide for the two Sunbird SALT models wired into
`App/backend/app/speech_service.py`: ASR (`Sunbird/asr-whisper-large-v3-salt`)
and TTS (`Sunbird/spark-tts-salt`).

## ASR — `Sunbird/asr-whisper-large-v3-salt`

One `whisper-large-v3` fine-tune covering English, Luganda, Kiswahili,
Runyankole and Acholi in a single checkpoint (published WER: eng 0.018, lug
0.142) — a strict upgrade over `WHISPER_ADAPTER_*`, which this deployment
only ever populated for Luganda, so English and Kiswahili silently ran
through the Luganda-merged adapter (see issue #303's audit).

**On by default.** It is the top ASR tier (`⓪` in `_transcribe_chain`), tried
before the LoRA adapters, Sherpa, faster-whisper and every cloud fallback.
Set `WHISPER_SALT_ENABLED=false` to opt out.

### The repo is gated

Unlike `openai/whisper-small`, this HuggingFace repo requires an authorized
account. `HF_TOKEN` must be set (already true for this project's `.env`) and
belong to an account with granted access — otherwise loading raises
`GatedRepoError`, is caught, logged at `INFO`, and the chain falls through to
`WHISPER_ADAPTER_*` / Sherpa / faster-whisper exactly as if the flag were off.
In the deployed containers this works automatically: `docker-compose.yml`'s
`env_file: - .env` already propagates `HF_TOKEN`.

### The model card's language-token table decodes to the wrong strings — force the ids anyway

The model card's own usage example (`SALT_LANGUAGE_TOKENS_WHISPER`) lists a
forceable special-token id for every language the fine-tune covers, lug/nyn/ach
included, alongside en/sw. Direct inspection
(`WhisperProcessor.from_pretrained(...).tokenizer.get_added_vocab()`) shows
this checkpoint's special-token vocabulary tops out at id 50358 (`<|yue|>`,
Cantonese) — the last of Whisper's ORIGINAL 99 languages. No Ugandan-language
token was ever added. The ids the model card lists for lug (50355), nyn
(50354) and ach (50357) decode on this tokenizer to Bashkir, Hausa and
Sundanese — unrelated languages that happen to sit in that id range.

The first pass at this concluded the table must be stale and stopped forcing
anything for lg/nyn/ach, passing `language=None` (the model's own detection)
instead. **That was wrong**, caught by testing rather than by inspection
alone. A controlled comparison — same process, same loaded model instance,
same two Luganda Common Voice clips, only the `language=` argument changed —
showed forcing id 50355 matches or beats `None`:

```
common_voice_lg_23830019.wav
  None            [153s]: 'Yaritasaana kugoba mukazi oyo kuba baali bavudde wala.'
  forced id 50355 [103s]: 'Yali tasaana kugoba mukazi oyo kuba baali bavudde wala.'   <- correct
common_voice_lg_23830023.wav
  None            [153s]: 'Ekibala kino kiwooma nnyo era nze ndowooza nti tukigule.'
  forced id 50355 [131s]: 'Ekibala kino kiwooma nnyo era nze ndowooza nti tukigule.'  <- identical
```

A separate check ruled out "forcing never mattered": forcing the
*confirmed-correct* `en` token on the same Luganda audio produced visibly
worse text (`'Yaritasanga kugoba mukazi oyo kuba abari bavudde wala.'`) than
either of the above. Forcing does something; id 50355 is the right thing to
force for Luganda despite what it decodes to.

**Conclusion, and what to trust:** the fine-tune's continued pretraining most
likely repurposed these specific base-Whisper language-token *embeddings* as
stand-ins for its own custom languages, without extending the tokenizer's
vocabulary — the numeric id carries fine-tuned meaning even though decoding
it back to a string still shows the stale Whisper-base label. `en` (50259)
and `sw` (50318) are trusted because they decode correctly AND sit in
Whisper's untouched original table. `lg` (50355) is trusted because it was
A/B-tested and measurably helps. `nyn` (50354) and `ach` (50357) are set
from the model card's table on the strength of the `lg` result and the
shared training run, but were **not independently tested** — no Runyankole
or Acholi audio was available in this sandbox. If either sounds wrong in
production, that assumption is the first thing to revisit — with the same
kind of controlled A/B, not by reverting to `None` on inspection alone (that
already produced one wrong conclusion here) and not by re-trusting the
model card's framing of these ids either (that was never the right framing
to begin with — the ids work despite it, not because of it).

### Faster inference in production (2026-08-20)

The in-process path (`_transcribe_whisper_salt` in `speech_service.py`) is
not the bottleneck — it already does the right things (`torch.no_grad()`,
`float16` on CUDA, correct device placement, now also explicit
`attn_implementation="sdpa"` rather than relying on transformers' implicit
kernel auto-selection). **The actual cost is running it on CPU at all.**
Measured on this project's dev sandbox (a separate, driver-matched
torch 2.6.0+cu124 environment — see the driver-mismatch note just below for
why that had to be a different environment from this project's own pinned
torch==2.12.1) against `common_voice_lg_23830019.wav`, forcing language id
50355 (see above), 3-run mean after a warmup call, same clip and forced-id
the language-token A/B test above already measured on CPU:

| Device | Mean latency | Output |
| --- | ---: | --- |
| CPU (measured above, "forced id 50355") | 103s | `'Yali tasaana kugoba mukazi oyo kuba baali bavudde wala.'` |
| CUDA (torch 2.6.0+cu124, this sandbox) | **0.84s** | identical text — same correctness, ~122x faster |

Model load is a separate, one-time-per-process cost (68.8s here) paid once
at server startup, not per request — irrelevant to steady-state latency.
Re-attempting the CPU side of this specific comparison live, today, on this
shared sandbox did not even complete a single call within 10 minutes before
being killed — consistent with "CPU is unusably slow for this model," just
a worse number than the 103s on record above, plausibly from heavier CPU
contention today on a shared 8-GPU machine (several GPUs were at 100%
utilization from other jobs at the time). Either number supports the same
conclusion: this is not a close call, and the fix is not a code change.

That CPU number is not a code problem — it is what happens when
`torch.cuda.is_available()` is `False`. On this exact sandbox, it is: the
driver reports CUDA 12.2 (`nvidia-smi`'s top field), and
`App/backend/requirements.txt` hard-pins `torch==2.12.1`, whose Linux wheel
(from default PyPI — see the long comment in `Dockerfile.gpu` on why an
`--extra-index-url` can't override this) bundles CUDA 13.0-generation
runtime libs that this driver is too old to run. No error, no crash —
`torch.cuda.is_available()` just silently returns `False` and every request
falls back to CPU. **Before deploying this image anywhere and expecting GPU
speed, verify the target host's driver actually supports it:**

```bash
docker run --rm --gpus all --entrypoint python ura-chatbot-api:gpu \
  -c "import torch; print(torch.cuda.is_available())"
```

If that prints `False`, the fix is a newer NVIDIA driver on the host (or
waiting for `requirements.txt`'s torch pin to move to a version the host's
driver already supports) — not a build-time flag. `Dockerfile.gpu` briefly
carried a `TORCH_CUDA_TAG` build-arg meant to let operators target an
older-driver host by picking a different `--extra-index-url`; it was built
and tested and turned out to change nothing, because pip/uv can only
resolve `torch==2.12.1` from wherever a matching build actually exists
(only default PyPI has one), regardless of which extra index is offered.
Downgrading torch to a version with a real cu121/cu124-tagged build was
rejected as a fix for this one file — it would diverge this image's torch
version from the rest of the fleet with untested consequences for every
other torch-dependent code path (Whisper-SALT, Spark-TTS-SALT, general LLM
loading), a bigger change than "make ASR faster" should carry.

**vLLM's native Whisper support was evaluated and rejected for this model,
in this vLLM version.** `vllm/vllm-openai:v0.8.5` advertises
`WhisperForConditionalGeneration` in its model registry and an
OpenAI-compatible `/v1/audio/transcriptions` endpoint (`--task
transcription`, falls back to the V0 engine — "`--task transcription` is
not supported by the V1 Engine"). Tested directly against a running
container, not assumed from docs, and found broken two independent ways:

1. The stock image lacks `vllm[audio]` (`librosa` missing) — any request
   that reaches audio decoding fails with `ImportError: Please install
   vllm[audio] for audio support`.
2. The `language=` parameter rejects even its own advertised valid values.
   Tested `language=lg` (not recognized — expected), `language=Bashkir` and
   `language=English` (both **rejected despite being listed as valid
   options in the endpoint's own error message**). No language — including
   the ones this deployment actually needs to force per the section above —
   can be successfully passed to this endpoint in this vLLM version.

Given both bugs, and that forcing a specific language id is not optional
for this deployment's lg/nyn/ach accuracy (see the A/B test above), vLLM's
transcription serving path is not currently viable here. The recommendation
is the in-process path this deployment already runs, on a host whose driver
actually supports the pinned torch wheel's CUDA generation — confirmed
with the snippet above before relying on it.

## TTS — `Sunbird/spark-tts-salt`

Local neural TTS for `lg`/`sw`/`ach`/`nyn`, wired at
`App/backend/app/spark_tts_salt.py` and tried FIRST — local before any API —
in `_synthesize_uncached`'s tier `⓪` for those four locales, ahead of
Sunbird's cloud-native voice, which now runs only if this tier is
unavailable (unloaded) or fails; tier `①.75` gives it the same priority for
the one locale (`lg`) that has a *local Piper voice on paper* but whose pack
may be absent from a slim image. Cloud stays as a fallback deliberately —
see the "Ordering, not exclusion" comment at the top of
`_synthesize_uncached` — degraded audio beats no audio if this tier's
external checkout is ever removed or broken on a given deployment.

`SPARK_TTS_ENABLED` defaults to **on** (`true`) — matching Whisper-SALT now
that both are verified end to end, see below — but that alone changes
nothing on a deployment that has not also done the checkout in "Enabling it"
below: `load()` still raises immediately on a missing `SPARK_TTS_REPO_DIR`,
so this tier is silently absent (falls through to Sunbird cloud, exactly the
pre-existing behaviour) until an operator actually provides it. The two
reasons this needed a real GPU pass before it could be trusted, unlike the
ASR model above:

1. It needs BiCodec — an audio tokenizer/detokenizer that ships in a
   *second* repository, `github.com/SparkAudio/Spark-TTS` (Apache-2.0), which
   has **no `setup.py`/`pyproject.toml`** — confirmed by cloning it. It is
   not `pip install`-able, so it cannot be a normal `requirements.txt` entry,
   and vendoring ~3,800 lines of third-party inference code into this
   repository is a supply-chain/license decision that needs its own review,
   not something to fold into an unrelated change silently — see "Enabling
   it" below for the checkout-based alternative actually used.
2. The model card states "Requires CUDA for inference... no 4-bit/8-bit
   support verified". This has since been run on a real GPU — see
   "Verification status" below — but was an open question at the time this
   tier was first wired, which is why it shipped off by default then.

### Enabling it

**Baked in (2026-08-20), for GPU deployments — `Dockerfile.gpu`.** A
separate Dockerfile at the repo root does everything below automatically at
build time: pinned-commit checkout, its own dependency stack, and the
torchaudio fix in the next section. `SPARK_TTS_ENABLED=true`,
`SPARK_TTS_REPO_DIR=/opt/spark-tts`, `SPARK_TTS_DEVICE=cuda` are already set
in the image.

```bash
docker build -f Dockerfile.gpu -t ura-chatbot-api:gpu .
docker run --gpus all -p 8000:8000 --env-file .env ura-chatbot-api:gpu
```

It is a separate Dockerfile rather than folded into the base one because
the base image's actual target (CPU-only / CraneCloud-style deployment) has
no use for the extra ~3,800 lines of third-party code or its dependency
stack (torchaudio, einx, einops, omegaconf, soundfile, soxr) — real image
weight and supply-chain surface a non-GPU deployment shouldn't carry. See
the file's own header comment for the driver-compatibility caveat that
applies to any GPU Dockerfile in this project, not just this one.

**Manual (non-Docker, or a Dockerfile of your own):**

```bash
# 1. Clone the codec package — pin the commit; it is not versioned any other way.
git clone https://github.com/SparkAudio/Spark-TTS /opt/spark-tts
git -C /opt/spark-tts checkout 2f1ea9082400547242641f5271b6f941c9f439d1

# 2. Its own dependencies (separate from this project's requirements.txt —
#    do not add these there; they are only needed on a deployment that
#    opts into this tier).
pip install -r /opt/spark-tts/requirements.txt
#   einops==0.8.1 einx==0.3.0 omegaconf==2.3.0 safetensors==0.5.2
#   soundfile==0.12.1 soxr==0.5.0.post1 torchaudio==2.5.1 ...

# 3. Point the app at the checkout and turn the tier on.
export SPARK_TTS_ENABLED=true
export SPARK_TTS_REPO_DIR=/opt/spark-tts
```

`SPARK_TTS_CODEC_REPO` (default `unsloth/Spark-TTS-0.5B`) is resolved via a
normal `huggingface_hub.snapshot_download` at load time — no extra step
needed for that half.

### The torchaudio/CUDA mismatch, and why `Dockerfile.gpu` deletes a file to fix it

Only relevant if you install sparktts's dependencies *alongside* this
project's own pinned `torch==2.12.1` instead of following spark-tts's own
`requirements.txt` exactly as shown above (which pins a self-consistent
`torch==2.5.1`/`torchaudio==2.5.1` pair and does not hit this). That is
what `Dockerfile.gpu` does, deliberately — installing spark-tts's
`requirements.txt` verbatim would downgrade the project's own torch and
transformers.

Installing bare `torchaudio` (unpinned) alongside the project's pinned
torch looks like it should resolve to a matching build, but doesn't: torch
resolves to `2.12.1` (from default PyPI — the only index with a build for
that exact pinned version, bundling CUDA 13.0-generation runtime libs; see
the "Faster inference in production" note above for why `--extra-index-url`
can't change this), while torchaudio resolves to `2.11.0+cu128` (from
PyTorch's own wheel index, since torchaudio has no competing default-PyPI
wheel — that index caps out at 2.11.0, with no build yet compiled against
CUDA 13.0). Both installs are individually "correct" by their own
resolver's logic; the pair is binary-incompatible anyway, because
`--extra-index-url` + an unpinned package doesn't guarantee both sides
resolve from the same source.

The symptom: importing `sparktts` (transitively: `torchaudio`) raises
`OSError: libcudart.so.12: cannot open shared object file` — torchaudio's
compiled extension (`_torchaudio*.so`) hard-`dlopen`s a CUDA 12 runtime
library this image never installs. Providing that library just uncovers
torchaudio's own explicit next-layer check: `RuntimeError: Detected that
PyTorch and TorchAudio were compiled with different CUDA versions. PyTorch
has CUDA version 13.0 whereas TorchAudio has CUDA version 12.8.` No
torchaudio release compiled against CUDA 13.0 exists yet — not a version
typo, a real currently-unresolvable-by-pinning gap upstream.

**The fix ships in the image rather than fighting the version mismatch:**
`torchaudio/_extension/utils.py`'s `_load_lib()` is explicitly documented
to return `False` (a supported "extension unavailable" degraded mode,
written for pex-style deployments) when the compiled `.so` file is simply
*absent* — it only raises when the file is present but fails to load. So
`Dockerfile.gpu`'s builder stage deletes it:

```dockerfile
RUN rm -f /opt/venv/lib/python3.11/site-packages/torchaudio/lib/_torchaudio*.so
```

Confirmed this doesn't regress anything sparktts needs for this project's
usage: `torchaudio` is used in exactly two places in the `sparktts`
package — `utils/audio.py`'s `highpass_biquad()` (only reachable from the
voice-cloning/reference-audio `tokenize()` path, which
`app.spark_tts_salt.py`'s `synthesize()` never calls), and
`bicodec.py`'s `init_mel_transformer()`, which builds a
`torchaudio.transforms.MelSpectrogram` unconditionally in `BiCodec.__init__`
(so it IS in the load path) but is never referenced by
`BiCodec.detokenize()` itself — confirmed by reading `detokenize()`, which
only touches `quantizer`/`speaker_encoder`/`prenet`/`decoder`. And even
where it is exercised, `MelSpectrogram`'s constructor and forward pass are
pure torch tensor ops (STFT + matmul) — not backed by the compiled
extension at all. Verified directly in the built image with the `.so`
removed: `torchaudio` imports, `MelSpectrogram` constructs and runs
`forward()` correctly, `sparktts.models.audio_tokenizer.BiCodecTokenizer`
and `sparktts.models.bicodec.BiCodec` both import cleanly, and — see
"Verification status" below — `BiCodecTokenizer` construction and a real
`detokenize()` forward pass against the real (public) `unsloth/Spark-TTS-
0.5B` weights run correctly end to end on CUDA.

Re-run that verification if `App/backend/requirements.txt`'s torch pin ever
moves — this fix is sound for the `2.12.1`/`2.11.0+cu128` pairing, not
guaranteed for whatever comes next.

### Speaker ids — confirmed for four locales, not five

The model card's training table lists six speaker ids across six languages;
four map onto this deployment's locales and are the defaults in
`spark_tts_salt.SPARK_TTS_SPEAKER_IDS`:

| Locale | Speaker id | Gender |
| --- | ---: | --- |
| `lg` (Luganda) | 248 | F |
| `sw` (Kiswahili) | 246 | M |
| `ach` (Acholi) | 241 | F |
| `nyn` (Runyankole) | 243 | F |

The model card's architecture section separately claims "English (Ugandan
accent)" as a seventh covered language, but the training table has no
speaker id for it. **No id is guessed for English** — the same mistake
already caught and reverted for the ASR model's language tokens is not
repeated here. An English request simply has no entry in
`SPARK_TTS_SPEAKER_IDS`, `speaker_id_for("en")` raises `KeyError`, and the
tier is skipped — English keeps using edge-tts (§`SPEECH_EN_EDGE_VOICE`).
Override any id via `SPARK_TTS_SPEAKER_{LG,SW,ACH,NYN}` if a operator later
confirms a different or additional one.

### Verification status (2026-08-19)

Run end to end on a real GPU against the real weights (not just import-checked):
`app.spark_tts_salt.load()` loaded both halves (LLM ~29s, BiCodec ~47-80s
cold), and `SparkTtsSalt.synthesize()` produced non-trivial audio for all
four mapped locales — peak 0.72-0.99, RMS 0.07-0.08, 3-3.7s for short
sentences, none silent or clipped-flat. `en` correctly raised
`SparkTtsUnavailable` rather than guessing a speaker id (no id is published
for it — see the speaker-id table above).

Two real bugs surfaced by this run and fixed in `spark_tts_salt.py`, neither
visible from reading the model card — both present in its own
`generate_speech()` example too, which is why they survived a first reading:

1. **Missing device placement.** `semantic_ids`/`global_ids` were built via
   bare `torch.tensor(...)` (CPU by default) and hit `detokenize()` — which
   runs on `self.device` — without ever being moved there. Fixed with
   `.to(self.device)` on both.
2. **Wrong tensor rank for `global_ids`.** The model card's example applies
   `.unsqueeze(0)` twice. `BiCodecTokenizer.detokenize()` (the method
   actually called) does its own internal `unsqueeze(1)` before handing off
   to the inner model, because its documented contract is already 2D
   (`batch_size, global_dim`) — the second `.unsqueeze(0)` in the example
   produces one dimension too many, and fails deep inside an `einx` codebook
   lookup with a shape-contradiction error that gives no hint the actual
   problem is rank, not value. Fixed to a single `.unsqueeze(0)`.

**Still not done — perceptual verification.** Nobody has *listened* to the
output. The waveform statistics above rule out silence, dead air, and gross
clipping, but not wrong pronunciation, wrong speaker, or bad prosody.
Generated WAVs from the verification run are not part of this repo (ephemeral
scratch output); regenerate and listen before relying on this tier for
anything user-facing:

1. Bring it up on a real GPU (`SPARK_TTS_DEVICE=cuda`, or leave unset — it
   autodetects).
2. Synthesize a handful of sentences per locale and **listen to them** —
   confirm the language matches the speaker id, prosody is intelligible, and
   the 8-second training-context limit (per the model card's own stated
   limitation) doesn't truncate a full URA-length answer sentence.
3. Run it through `ml/pipelines/corpus_coverage.py --mode voice` alongside
   the existing Sunbird-cloud / edge-tts tiers for a side-by-side comparison
   on the same question bank (see `docs/runbooks/corpus-coverage.md`).

### Verification status — `Dockerfile.gpu` bake-in (2026-08-20)

The 2026-08-19 run above verified `spark_tts_salt.py`'s logic in an
isolated venv with a driver-matched torch/torchaudio pair. This pass
verifies the same code baked into the actual shipped image, which pins a
different (project-standard) torch version — the reason the torchaudio fix
above was needed at all. Layered verification, each step on the real
`ura-chatbot-api:gpu` image unless noted:

1. **Imports.** `sparktts.models.audio_tokenizer.BiCodecTokenizer` and
   `sparktts.models.bicodec.BiCodec` both import cleanly with the `.so`
   removed. `torchaudio.transforms.MelSpectrogram` both constructs and runs
   `forward()` correctly (pure tensor ops, as expected).
2. **Gated-repo access.** `app.spark_tts_salt.load()` run inside the image
   with `HF_TOKEN` wired via `--env-file` (matching how
   `docker-compose.yml` actually delivers it in production) successfully
   downloaded and loaded the gated `Sunbird/spark-tts-salt` LLM half —
   confirms the project's existing `HF_TOKEN` also covers this repo, not
   just the ASR model.
3. **Real CUDA execution, blocked on THIS host only by the pre-existing
   driver mismatch** (see "Faster inference in production" above) —
   `load()` reached `model.to("cuda")` and failed with the same
   documented `RuntimeError: The NVIDIA driver on your system is too old`,
   not a new failure mode.
4. **Closing the loop:** ran the exact pinned-commit `sparktts` source
   `Dockerfile.gpu` bakes in — `BiCodecTokenizer` construction and a real
   `detokenize()` forward pass, real weights (`unsloth/Spark-TTS-0.5B`,
   public) — in the separate driver-matched environment from step 1 of the
   2026-08-19 run, since that's the only way to get genuine CUDA on this
   sandbox. Passed: construction 77.3s, `detokenize()` 1.43s, output shape
   `(16000,)`, peak 0.253, RMS 0.030 — non-silent, no error. (Dummy
   in-range token ids, not real LLM output — this step verifies the
   BiCodec/torchaudio code path specifically, not end-to-end pronunciation;
   that's what the 2026-08-19 run above already covers with real generated
   tokens.)

Net: the torchaudio fix is verified sound, gated-repo access is verified
working through the image's actual credential path, and the only
remaining gap to a fully green run *inside this exact image* is a host
with a driver new enough for `torch==2.12.1` — the same, already-documented
prerequisite as everywhere else in this file. Re-run step 4 against the
image directly (not the separate venv) the next time this is verified on a
host that actually has one.

### Full-stack live verification — GPU-pinned, ngrok-exposed (2026-08-22)

Both tiers above have now been brought up together with the rest of the
stack (Redis, Qdrant, local Sunflower-14B-FP8 via vLLM), pinned to a single
named GPU, and reached over the project's public ngrok tunnel — not just
verified in isolation as in the two runs above. Full record, including the
new `App/docker-compose.gpu-salt.yml` overlay and a real (not dummy-token)
Spark-TTS-SALT `/v1/tts` request's timing under CPU fallback:
`App/docs/traceability/local-gpu-salt-ngrok-2026-08-22.md`.

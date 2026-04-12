# URA Chatbot Speech Pipeline (2026)

End-to-end Luganda <-> English speech stack targeting mobile offline deployment.

## Overview

```
              ----------------------- MOBILE DEVICE -----------------------

  Microphone
      |
      v
  +-----------+        +------+         +-----+        +------------+
  | Silero    |  chunk | Asr  |  text   | Lang|  en    |  MT        |
  | VAD       +------->+ (sh) +-------->+ ID  +------->+ student    |
  | ONNX MIT  |  200ms | erpa-|         |     |  lg    | MADLAD-400 |
  +-----------+        | onnx)|         |     +---+    | INT8 Apache|
                       +------+         +-----+   |    +------+-----+
                                                  |           |
                                                  v           v
                                             +--------+  +---------+
                                             | Gemma  |  |  MT     |
                                             | 2B     |  | student |
                                             | GGUF   |  | (reply) |
                                             | Q4_K_M |  +----+----+
                                             +---+----+       |
                                                 |            |
                                                 v            v
                                            +----+----+  +--------+
                                            | Sentence|  | TTS    |
                                            | chunker +->+ (Piper |
                                            +---------+  | / VITS)|
                                                         +---+----+
                                                             |
                                                             v
                                                         Speaker
```

All components run **on-device**. No network calls after model download.

## Stack by component

| Stage | Model | License | Runtime | Size |
|---|---|---|---|---|
| VAD | Silero VAD | MIT | sherpa-onnx | 2 MB |
| ASR (en) | Whisper-small | MIT | sherpa-onnx | ~220 MB |
| ASR (lg) | Whisper-small fine-tune on Common Voice + WAXAL | MIT (derived) | sherpa-onnx | ~220 MB |
| ASR (sw) | Whisper-small (multilingual base) | MIT | sherpa-onnx | shared |
| ASR (nyn) | Whisper-small fine-tune on WAXAL nyn (132k samples) | MIT (derived) | sherpa-onnx | shared |
| ASR (ach) | Whisper-small fine-tune on WAXAL ach (114k samples) | MIT (derived) | sherpa-onnx | shared |
| Lang-ID | lingua-py + trigram LID (5 languages) | Apache-2.0 / MIT | native + Dart | ~5 MB |
| MT (backbone) | MADLAD-400-3B | **Apache-2.0** | transformers (server) | 3 B params |
| MT (mobile student) | distilled T5-small | Apache-2.0 | onnxruntime | ~350 MB |
| LLM | Gemma-2-2B-it | Gemma Terms (commercial OK) | MediaPipe LLM | ~1.6 GB |
| TTS (en) | Piper en_US-lessac-medium | MIT | sherpa-onnx | ~70 MB |
| TTS (lg) | VITS trained on WAXAL lug_tts (2,020 samples) | CC-BY-SA-4.0 | sherpa-onnx | ~100 MB |
| TTS (sw) | MMS-TTS Swahili | CC-BY-NC-4.0 | sherpa-onnx | ~40 MB |
| TTS (nyn) | VITS trained on WAXAL nyn_tts (1,990 samples) | CC-BY-SA-4.0 | sherpa-onnx | ~100 MB |
| TTS (ach) | VITS trained on WAXAL ach_tts (2,030 samples) | CC-BY-SA-4.0 | sherpa-onnx | ~100 MB |

**Total mobile bundle budget:** 2.6 GB (see `ml/configs/mobile_bundle.yaml`).

## Training data sources (by priority)

| # | Source | HuggingFace ID | Languages | Type | Size | License |
|---|---|---|---|---|---|---|
| 1 | **Sunbird SALT** | `Sunbird/salt` | en/lg/sw/nyn/ach + 5 | Parallel text + ASR + TTS | 25k sentences | CC-BY-SA-4.0 |
| 2 | **Google WAXAL** | `google/WaxalNLP` | nyn/ach/lug + 18 | ASR (344k) + TTS (6k) | 11k+ hours | CC-BY-SA-4.0 |
| 3 | **Mozilla Common Voice** | `mozilla-foundation/common_voice_17_0` | lg | ASR | ~80 validated hours | CC0-1.0 |
| 4 | **JW300** | `opus/JW300` | en↔lg/sw/nyn/ach | Parallel MT | ~100k pairs/pair | CC0 (contested) |
| 5 | **OPUS Mozilla-I10n** | `opus/Mozilla-I10n` | en↔ach | Parallel MT | 24k pairs | Open |
| 6 | **OPUS Tatoeba** | `opus/Tatoeba` | en↔lg/sw | Short parallel | Variable | CC-BY-2.0 |
| 7 | **Masakhane LAFAND-MT** | `masakhane/lafand-mt` | en↔lg/sw | MT benchmark | ~50k | CC-BY-NC-4.0 |
| 8 | **FLORES-200** | `facebook/flores` | nyn/ach/lg/sw/en | MT eval | ~1k/lang | CC-BY-SA-4.0 |
| 9 | **Google FLEURS** | `google/fleurs` | lg/sw | ASR eval | ~12h/lang | CC-BY-4.0 |
| 10 | **URA FAQs** | Project-internal | en (+ translated) | QA pairs | 47 CSV files | Proprietary |
| 11 | **URA PDFs** | Project-internal | en | Corpus | 47 documents | Proprietary |
| 12 | **URA website crawl** | `Data/crawl/` | en | Web pages | Ongoing | Public |

Download all: `python -m ml.scripts.data_aug.dataset_downloader --output-dir Data/online_corpora`

## Commercial-safety policy

No component may ship under a CC-BY-NC license. Enforcement lives in
`ml/scripts/speech/export_mobile_speech.py` which refuses to deploy any
asset whose license is not in the allowlist at
`ml/configs/mobile_bundle.yaml::license_allowlist`.

Explicitly excluded (and why):

* `facebook/mms-tts-lug` — CC-BY-NC-4.0 (non-commercial only)
* `facebook/nllb-200-*`  — CC-BY-NC-4.0
* `facebook/seamless-m4t-*` — research-only variants are CC-BY-NC

We make up the gap with:

* Whisper + fine-tune for ASR (all 5 languages) — MIT derived
* MADLAD-400 (Apache-2.0) for MT backbone
* VITS trained on WAXAL TTS data (CC-BY-SA-4.0) for lg/nyn/ach — **no custom recording needed**
* Sunbird SALT (CC-BY-SA-4.0) for parallel text + ASR augmentation
* Google WAXAL (CC-BY-SA-4.0) for ASR + TTS training data

## Latency budget (per turn)

| Stage | Target (ms) | Measured on reference device (Pixel 6a) |
|---|---|---|
| VAD + ASR partials | < 200 |  TBD |
| ASR final | < 1500 |  TBD |
| Lang-ID | < 20 |  TBD |
| MT (input) | < 400 |  TBD |
| LLM first token | < 500 |  TBD |
| LLM full reply | < 3000 |  TBD |
| MT (output) | < 400 |  TBD |
| TTS first chunk | < 300 |  TBD |
| **Total round trip** | **< 5000** |  TBD |

Benchmarks are run by `ml/pipelines/benchmark_mobile.py` and reported in
`Results/metrics/mobile_benchmark.json`.

## Flutter mobile integration

The Flutter app at `MobileApp/ura_chatbot/` implements the mobile
client for this pipeline:

| Component | Flutter file | Status |
|---|---|---|
| VAD | `lib/core/speech/vad.dart` | RmsVad default; Silero planned |
| ASR | `lib/core/speech/asr/whisper_onnx_engine.dart` | Whisper Small INT8 |
| ASR fallback | `lib/core/speech/asr/native_asr_engine.dart` | OS STT (EN only) |
| ASR routing | `lib/core/speech/asr/asr_router.dart` | Locale-based selection |
| Domain glossary | `lib/core/speech/domain_glossary.dart` | 28 URA terms |
| LID | `lib/core/speech/lid.dart` | Trigram en/lg/mixed |
| TTS | `lib/core/speech/tts/mms_vits_engine.dart` | MMS-TTS VITS (EN+LG) |
| TTS cache | `lib/core/speech/tts/audio_cache.dart` | 50 MB LRU disk |
| AI Act | `lib/core/compliance/ai_act.dart` | Art. 50 + watermark |
| Model mgmt | `lib/core/speech/model_manager.dart` | On-demand + SHA-256 |
| Consent | `lib/features/consent/` | Per-purpose + audit ledger |
| Eval | `assets/speech/eval/gold.json` | 20-sample gold set |

Model bundles in `assets/speech/manifest.json` (5 bundles, ~240 MB).
Tests: 31 passing across 6 test files.

## Failure modes and fallbacks

| Failure | Fallback |
|---|---|
| ASR unavailable | `AsrRouterException` → SnackBar with download prompt |
| ASR low confidence | Domain glossary post-fixups applied automatically |
| Lang-ID low confidence | Default to user-selected locale |
| MT unavailable | Gemma-2-2B prompted translation (slower) |
| LLM unavailable | Backend API (requires network) |
| TTS unavailable | SnackBar "TTS model not available" — text-only reply |
| Barge-in | TTS cancelled when VAD detects new speech |
| Consent withdrawn mid-recording | VAD handler re-checks; stops capture |
| App backgrounded | WidgetsBindingObserver stops capture on pause |

## Training pipeline (offline, one-time per model)

```
Data/TTT/           -> mt_loaders.py       -> finetune_mt.py     -> distill_mt.py -> export_mt_onnx.py
Data/lgaudio/       -> asr_loaders.py      -> train_luganda.py   -> export_asr_onnx.py
Data/speech/tts/    -> train_luganda_vits.py                     -> export_tts_onnx.py
                                                                    |
                                                                    v
                                                        export_mobile_speech.py
                                                                    |
                                                                    v
                                          MobileApp/ura_chatbot/{android,ios}/
```

Every stage supports `--dry-run` and refuses to overwrite existing
artifacts without `--force`. Quality gates enforce WER / BLEU / chrF
/ RTF thresholds from `ml/configs/training_config.yaml`.

## Web client speech architecture

The Next.js frontend at `App/frontend/` provides full bilingual speech on
the web, complementing the mobile offline stack above.

```
Browser (Next.js)
      |
      v
  +-------------------+
  | MediaRecorder      |   getUserMedia({ audio: 16 kHz mono })
  | -> PCM16 @ 16 kHz  |   downsample + encode on AudioContext
  +--------+-----------+
           |
           | POST /v1/voice/chat  (raw PCM body + query params)
           v
  +--------+-----------+
  | FastAPI backend     |
  |  1. ASR  (Whisper)  |   -> transcript
  |  2. MT   (lg->en)   |   -> English text  (skipped if en)
  |  3. LLM  (Gemma)    |   -> reply text
  |  4. MT   (en->lg)   |   -> Luganda reply (skipped if en)
  |  5. TTS  (Piper)    |   -> WAV audio
  +--------+-----------+
           |
           | JSON: { transcript, reply, reply_audio_base64, latencies }
           v
  +--------+-----------+
  | AudioContext         |   decode base64 WAV -> createBufferSource -> play
  | stopPlayback()       |   barge-in: cancel current source on new input
  +---------------------+
```

### Web speech modes

| Mode | Input | Output | When |
|---|---|---|---|
| **Text chat** | Keyboard | Text + Listen button | Default |
| **Browser Speech API** | Mic via SpeechRecognition | Text (client-side ASR) | Voice mode off, browser supports API |
| **Voice mode** | Mic via MediaRecorder | Text + auto-narrated TTS audio | Voice mode toggle on |
| **Auto-narrate** | Any input mode | Every reply played aloud | Auto-narrate toggle on |

### Web fallback chain

| Feature | Primary | Fallback |
|---|---|---|
| Speech input | Browser SpeechRecognition API | MediaRecorder -> `/v1/asr` (server ASR) |
| TTS playback | `/v1/tts` -> AudioContext decode | Text-only (no audio) |
| Voice chat | `/v1/voice/chat` compound endpoint | Separate `/v1/asr` + `/v1/chat` + `/v1/tts` calls |
| Language | Auto-detected by server lang-ID | Explicit locale from UI toggle |

### Web API endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/v1/voice/chat` | POST | Compound: audio in -> ASR -> MT -> LLM -> MT -> TTS -> audio+text out |
| `/v1/asr` | POST | Standalone ASR (raw PCM body) |
| `/v1/tts` | POST | Standalone TTS (JSON text -> base64 WAV) |
| `/v1/translate` | POST | Standalone MT (JSON text -> translated text) |
| `/v1/speech/health` | GET | Health check for speech pipeline readiness |

See `docs/API_REFERENCE.md` for full request/response schemas.

### Web latency budget (per voice turn, server-side)

| Stage | Target (ms) | Notes |
|---|---|---|
| Audio capture + upload | < 500 | Depends on recording length + network |
| ASR | < 2000 | Whisper-small on CPU/GPU |
| MT (input, if Luganda) | < 500 | MADLAD-400 or prompted |
| LLM | < 3000 | Gemma-2B (GPU) or RAG+FAQ (CPU) |
| MT (output, if Luganda) | < 500 | Same as input MT |
| TTS + response transfer | < 1500 | Piper ONNX + base64 encode |
| **Total round trip** | **< 8000** | vs. 5000 ms mobile target |

### Frontend implementation

* **Voice service:** `App/frontend/src/services/voiceService.ts`
  * `AudioRecorder` — MediaRecorder -> 16 kHz PCM16 via AudioContext downsampling
  * `playAudioBase64()` / `stopPlayback()` — AudioContext-based WAV playback with barge-in
  * API wrappers: `voiceChat()`, `transcribe()`, `synthesize()`, `translate()`, `checkSpeechHealth()`
* **UI wiring:** `App/frontend/src/app/page.tsx`
  * Listen button on every assistant response
  * Voice mode toggle (MediaRecorder -> server pipeline)
  * Auto-narrate toggle (auto-play TTS on every reply)
  * Speech health indicator (polls `/v1/speech/health` on mount)
  * Recording pulse animation with accessibility (focus-visible, WCAG AA contrast, reduced-motion)
* **Backend service:** `App/backend/app/speech_service.py`
  * `SpeechModel` singleton with lazy model loading
  * Thread-safe init with `threading.Lock`, circuit breakers per operation
  * WAV encoding: PCM16 @ 22050 Hz, correct 32768 scaling

## References

* `ml/configs/speech_config.yaml`      — canonical speech pipeline config
* `ml/configs/mobile_bundle.yaml`      — deployable mobile bundle manifest
* `ml/docs/OFFLINE_GUARANTEES.md`      — privacy and offline posture
* `ml/docs/NEXT_STEPS.md`              — deferred tasks requiring data / compute
* `ml/docs/model_cards/`               — per-model cards
* `ml/docs/data_cards/`                — per-dataset cards
* `App/frontend/src/services/voiceService.ts` — web voice service
* `App/backend/app/speech_service.py`  — backend speech model singleton
* `App/backend/app/main.py`           — speech API endpoint handlers

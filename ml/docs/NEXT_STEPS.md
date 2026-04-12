# Next Steps — Speech Pipeline (2026)

Honest punch list of what still needs to happen AFTER the scaffolding in
this branch lands. Everything here requires external data, compute, or
human labor that cannot be done in the scaffolding pass.

## Blocking (before first mobile bundle can ship)

1. **Luganda voice data collection**
   * Script: `Data/speech/tts/lg_voice/README.md` documents the recording
     protocol.
   * Need: single adult native speaker, 3-4 hours of clean speech in a
     quiet room, ~500-1000 URA-themed prompts.
   * Consent: `CONSENT.yaml` with a revocable, versioned agreement.
   * Post-collection: run `ml/scripts/tts/train_luganda_vits.py` on GPU.

2. **Common Voice Luganda download**
   * `python -m ml.scripts.data_aug.asr_loaders` can already consume the
     existing `Data/lgaudio/` sample, but production needs the full
     Mozilla Common Voice snapshot.
   * Source: `https://commonvoice.mozilla.org/en/datasets` (CC0-1.0).
   * Place under `Data/common_voice_lg/`.

3. **Whisper fine-tune on Luganda**
   * Once Common Voice lg is in place:
     ```
     python -m ml.scripts.asr.train_luganda \
         --common-voice Data/common_voice_lg \
         --target mobile
     ```
   * Needs: 1x GPU (24 GB), ~6-10 hours.
   * Gate: WER <= 0.25 on held-out Luganda test set.

4. **MADLAD-400 download + fine-tune**
   * `python -m ml.scripts.mt.download_models`
   * `python -m ml.scripts.mt.finetune_mt`
   * Gate: BLEU >= 15 (en->lg), >= 20 (lg->en).
   * Needs: 1x GPU (24 GB), ~4-8 hours.

5. **Teacher -> student distillation**
   * `python -m ml.scripts.mt.distill_mt`
   * Produces the mobile-sized MT model.
   * Needs: 1x GPU, ~4 hours.

6. **Backtranslation augmentation**
   * `python -m ml.scripts.mt.backtranslate`
   * Generates synthetic Lg->En pairs from monolingual Luganda text.
   * Iterative: re-run `finetune_mt.py` after each backtranslation pass.

## Blocking (mobile integration)

7. **sherpa-onnx native plugin**
   * The Dart layer is complete; the Android + iOS bridges are documented
     but not yet committed.
   * Android: follow
     `MobileApp/ura_chatbot/android/app/src/main/cpp/sherpa/README.md`.
   * iOS: follow
     `MobileApp/ura_chatbot/ios/Runner/Speech/README.md`.
   * Pin versions: `sherpa-onnx:1.11.x`.

8. **MediaPipe + sherpa-onnx co-existence**
   * Existing LLM uses MediaPipe LLM Inference API.
   * New speech stack adds sherpa-onnx + onnxruntime.
   * Verify Android Gradle + iOS Podfile integrate without ABI conflicts.

9. **Asset bundle size check**
   * Current budget: 2.6 GB total.
   * If the Luganda Whisper fine-tune + MT student push over this,
     downgrade Gemma to IQ3_M or Llama-3.2-1B per the existing
     `mobile_offline` target in `scripts/fine_tune_gemma.py:82-88`.

## Non-blocking (quality of life)

10. **Common Voice lg test-set eval**
    * Populate `Data/speech/asr_eval_lg.jsonl` with real transcripts.
    * Run `python -m ml.pipelines.evaluate_speech`.

11. **Native speaker MOS study**
    * Recruit 5-10 native Luganda speakers.
    * Use `ml/pipelines/evaluate_tts.py --mos-collection` to emit a CSV
      for rater scoring.
    * Target: MOS >= 3.5.

12. **COMET-kiwi reference-free MT eval**
    * Uncomment `unbabel-comet>=2.2.0` in `requirements.txt`.
    * Run `python -m ml.pipelines.evaluate_mt` — will auto-pick up COMET.

13. **Code-switching test set**
    * Record ~100 short utterances mixing Luganda + English words
      (common in Kampala speech).
    * Add to `Data/speech/asr_eval_codeswitch.jsonl`.
    * Measure WER on code-switched vs mono-language.

14. **Red-team voice corpus**
    * `Data/eval/redteam_corpus.jsonl` already exists (text).
    * Use `ml/scripts/tts/infer_tts.py` to synthesize each prompt as
      audio, then run `python -m ml.pipelines.redteam_voice`.

15. **Continual on-device personalization**
    * Future work: LoRA adapter fine-tune on-device per user for
      pronunciation / vocabulary adaptation.
    * 2026 trend, not 2026 requirement.

16. **Full-duplex conversational mode**
    * Requires moshi-style interleaved ASR+TTS; current design is turn-
      based with barge-in only. Consider Kyutai Moshi / Gemini Live APIs
      (when commercial-safe equivalents exist).

## Recently completed (web speech pipeline)

The following web speech features are implemented, audited, and verified:

- **Compound voice chat endpoint** (`POST /v1/voice/chat`) — full
  audio-in/audio-out pipeline with per-stage latency tracking, Prometheus
  metrics, input validation, and circuit breakers.
- **Individual speech endpoints** (`/v1/asr`, `/v1/tts`, `/v1/translate`,
  `/v1/speech/health`) — rate-limited, metrics-instrumented, with error
  counters and latency histograms.
- **Frontend voice service** (`App/frontend/src/services/voiceService.ts`)
  — AudioRecorder (MediaRecorder -> PCM16 @ 16 kHz), AudioContext playback,
  all API wrappers with timeout + error detail parsing.
- **Voice UI** (`App/frontend/src/app/page.tsx`) — Listen button on every
  assistant bubble, voice mode toggle, auto-narrate toggle, recording pulse
  animation, speech health indicator. Race-condition guards (isTransitioning,
  ref clearing, playback state functional updater). Unmount cleanup.
- **Accessibility** — WCAG AA contrast ratios, focus-visible on all
  interactive elements, reduced-motion disables all animations, mobile
  responsive voice controls.
- **Backend hardening** — thread-safe SpeechModel with `threading.Lock`,
  shutdown guard, correct PCM16 WAV scaling (32768), logged TTS fallback
  chain, safe citation parsing.
- **Documentation** — `SPEECH_PIPELINE.md` web architecture section,
  `API_REFERENCE.md` speech endpoints + data models + SDK examples.

## Web-specific next steps

21. **Browser compatibility testing**
    * Web Speech API (SpeechRecognition) is Chrome/Edge only in 2026.
    * MediaRecorder fallback to server ASR covers Firefox/Safari.
    * Test matrix: Chrome 130+, Firefox 128+, Safari 18+, Edge 130+.

22. **Streaming TTS (SSE audio chunks)**
    * `SynthesizeRequest.streaming` field is accepted but unused.
    * Implement sentence-chunked TTS streaming via SSE for lower
      time-to-first-audio on long replies.

23. **WebSocket voice chat**
    * Current voice chat uses HTTP POST (batch audio).
    * For real-time conversational feel, implement WebSocket-based
      streaming ASR with partial results + incremental TTS.

24. **Web audio visualization**
    * Add waveform / frequency analyzer during recording.
    * Use `AnalyserNode` from Web Audio API.

25. **Offline web (Service Worker)**
    * Cache the TTS model in a Service Worker for offline narration.
    * Requires sherpa-onnx WASM build (upstream supports this).

## Recently completed (2026 production gates)

The following pipelines are implemented and wired into CI. They run with
`--soft-fail` until real calibration/benchmark artefacts are produced by
a full training run.

- **Calibration pipeline** (`ml/pipelines/calibrate.py`) — ECE, MCE,
  Brier score, temperature scaling, coverage-vs-accuracy curves,
  recommended abstention threshold. Per-language and per-category slices.
- **Tokenizer audit** (`ml/pipelines/audit_tokenizer.py`) — measures
  Luganda vs English fertility ratio, UNK rate, piece-length distribution.
  Falls back to whitespace proxy when `transformers` is unavailable.
- **Inference benchmark** (`ml/scripts/benchmark_inference.py`) — desktop
  proxy for on-device timing. Measures tokens/sec, TTFT, peak RSS across
  6 representative prompts. `--synthetic` mode for CI without model weights.
- **Model card generator** (`ml/pipelines/generate_model_card.py`) — 13
  required sections, HF Hub YAML frontmatter, EU AI Act Art. 10/13
  compliant. Self-validates via `validate()`.
- **Production quality gates** (`ml/pipelines/quality_gates.py --family
  production`) — aggregates RAG, calibration, safety, tokenizer, benchmark,
  mobile size, and model card into a single pass/fail with blocking vs
  advisory severity.
- **Reproducibility** (`ml/scripts/repro.py`) — deterministic seed pinning
  (Python/NumPy/PyTorch/transformers) + environment snapshot (git SHA,
  platform, packages, CUDA) embedded in every artefact.
- **CI job** (`production-gates` in `ci-ml-pipeline.yml`) — runs tokenizer
  audit, synthetic benchmark, model card generation, and production quality
  gates after RAG evaluation. Gates the deploy stage.

### To promote from soft-fail to hard gate

1. Run a full training + export cycle to produce real artefacts.
2. ~~Populate `Results/confidence_scores.jsonl` for calibration.~~
   Done — `evaluate_rag.py` now emits per-sample confidence scores
   automatically; `calibrate.py` consumes them in CI.
3. Run `benchmark_inference.py` without `--synthetic` on a real GGUF.
4. ~~Wire a safety eval harness to produce `safety_evaluation_results.json`.~~
   Done — `evaluate_safety.py` runs 33 red-team prompts through the
   mock/llm/api backend; wired into CI production-gates job.
5. Remove `--soft-fail` from the `production-gates` CI job
   (once steps 1 and 3 produce real data).

## Infrastructure

17. **GPU budget request**
    * Estimate: ~30 GPU-hours for the full Luganda training + distillation
      + voice training.
    * Options: Kaggle (existing `kaggle-training.yml` workflow), Colab,
      or local workstation.

18. **Storage budget**
    * Common Voice lg: ~1 GB
    * SALT-ASR: ~2 GB (if used)
    * Luganda voice recordings: ~500 MB
    * Model checkpoints: ~20 GB
    * Total project disk impact: ~25 GB additional

19. **CI compute**
    * Production gates run in CI with synthetic/proxy modes (no GPU needed).
    * Real training stays on Kaggle / manual workstation runs.

20. **Model-card + data-card reviews**
    * Model card is now auto-generated by `generate_model_card.py` and
      validated by `quality_gates.py --family production`.
    * Data cards are templated (see `ml/docs/model_cards/`, `ml/docs/data_cards/`).
    * After each real training run, re-generate the model card from fresh
      artefacts — it will pick up the latest metrics automatically.

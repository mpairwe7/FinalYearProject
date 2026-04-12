# Offline Guarantees + Privacy Posture (2026)

The speech pipeline is designed so that **audio never leaves the device**.
This document enumerates the guarantees, how they are enforced, and how to
verify them.

## Scope

Applies to the following components when the app is in "offline speech"
mode (the default on mobile):

* Microphone capture
* VAD gating
* ASR (Whisper / sherpa-onnx)
* Language identification
* MT (MADLAD-400 student / onnxruntime)
* LLM (Gemma-2-2B GGUF / MediaPipe)
* TTS (Piper / Custom VITS)
* Audio playback

## Guarantees

1. **No audio bytes leave the device.** Mic PCM buffers live only in the
   `AsrService` queue and are discarded once the final hypothesis is
   produced. They are not logged, cached on disk, or sent anywhere.
2. **No transcripts leave the device** unless the user explicitly opts in
   to feedback sharing (controlled by the existing `STORE_RAW_PROMPTS`
   flag on the backend — see `App/backend/app/service.py`).
3. **No model weights are downloaded at runtime.** All ASR/MT/TTS/LLM
   assets are bundled with the app by
   `ml/scripts/speech/export_mobile_speech.py`. The app never fetches a
   model on the fly.
4. **No telemetry is sent from the speech path** unless the user enables
   analytics. The existing `AnalyticsMiddleware` (see `analytics.py`) only
   sees HTTP-layer metrics — it has no view into the raw audio stream.
5. **All speech persistence (if any) is encrypted.** When the app caches
   speech for debugging or replay, it uses the platform keychain / keystore
   for the encryption key.

## Enforcement points

| Layer | Enforcement | File |
|---|---|---|
| Dart | `SherpaChannel` is the only path from Dart to native; no HTTP client in the speech package | `lib/core/speech/*.dart` |
| Android | `android/app/src/main/cpp/sherpa/` has no `network` permission imports | `android/.../cpp/sherpa/README.md` |
| iOS | Swift bridge has no `URLSession` imports | `ios/Runner/Speech/README.md` |
| Backend | Speech endpoints accept audio as bytes and never persist; the `SpeechModel` singleton keeps no history | `App/backend/app/speech_service.py` |
| Licenses | Bundle exporter refuses CC-BY-NC; only commercial-safe licenses pass | `ml/scripts/speech/export_mobile_speech.py` |

## Verification procedure

1. **Network audit (manual).**
   * Put the test device in airplane mode.
   * Reboot, launch the app, exercise the full speech turn (mic -> reply).
   * Confirm no user-visible errors and a successful audio reply.
   * Repeat with MitM proxy (mitmproxy / Charles) on the same Wi-Fi —
     confirm zero traffic on the speech endpoints.

2. **Static analysis.**
   ```
   grep -rE 'http[s]?://|URLSession|HttpClient|http.MainClient' MobileApp/ura_chatbot/lib/core/speech
   # Should return nothing.
   grep -rE 'network' MobileApp/ura_chatbot/android/app/src/main/cpp/sherpa
   # Should return nothing.
   ```

3. **Bundle license audit.**
   ```
   python -m ml.scripts.speech.export_mobile_speech --dry-run | jq '.components[].license' | sort -u
   # Every returned license MUST be in the allowlist in mobile_bundle.yaml.
   ```

4. **Red-team voice audit.**
   ```
   python -m ml.pipelines.redteam_voice
   # All adversarial prompts must produce refusals and NO network traffic.
   ```

5. **Privacy tests in CI.** The `speech-quality-gates` job in
   `.github/workflows/ci-ml-pipeline.yml` runs the dry-run bundle export
   and the redteam harness on every PR.

## What is NOT offline

These paths deliberately remain online-capable for when the user opts in:

* **Backend RAG chat** (`/v1/chat`) — uses the server-side vector store and
  LLM; audio is irrelevant here.
* **Feedback submission** — when the user taps "thumbs up/down" on a
  reply, metadata is sent to the backend. Audio is **not** included.
* **Model updates** — pushed via the normal app-store channel, not
  runtime downloads.

## Web client speech privacy posture

The Next.js web frontend at `App/frontend/` sends audio to the backend
for processing. This is a fundamentally different privacy model from the
mobile app, which processes audio entirely on-device.

**What travels over the network (web):**

| Data | Endpoint | Notes |
|---|---|---|
| Raw PCM audio (user speech) | `POST /v1/asr` or `/v1/voice/chat` | Sent as request body |
| Synthesized WAV (reply audio) | Response from `/v1/tts` or `/v1/voice/chat` | base64-encoded in JSON |
| Translated text | `POST /v1/translate` | JSON only, no audio |

**What is NOT stored on the server:**

* Raw audio bytes are never persisted to disk or database.
* Audio is held in memory only during the inference call and discarded
  after the response is sent.
* Transcribed text IS logged (with PII redaction) to the conversation
  database, same as typed chat messages.

**Enforcement points (web):**

1. **CORS** — hardened (no wildcard, explicit origins, no credentials).
2. **Permissions-Policy** — `microphone=(self)` on the frontend.
3. **Rate limiting** — 30/minute per IP on all speech endpoints.
4. **Audio size cap** — 16 MiB per request (circuit breaker for abuse).
5. **HTTPS** — `Strict-Transport-Security: max-age=63072000` enforced.
6. **No raw audio logging** — `STORE_RAW_PROMPTS` flag does not apply to
   audio; only redacted text transcripts are logged.

**Browser microphone consent:**

* The browser prompts for microphone access (getUserMedia).
* The web app cannot access the microphone without explicit user action.
* The speech health check (`/v1/speech/health`) does not require audio.
* Voice mode and auto-narrate are opt-in toggles (default off).

## Legal / compliance notes

* **Consent.** On first launch, the app asks for microphone consent. It
  also discloses that audio is processed locally. The consent form is
  versioned and stored in `CONSENT.yaml` (see existing Phase-14 consent
  framework under `App/backend/app/auth/`).
* **Data subject rights.** Under Uganda's Data Protection and Privacy Act
  2019, users may request deletion of any stored data. Since the speech
  pipeline does not persist audio, this is already a no-op for the speech
  path; the existing `/v1/me/*` endpoints handle chat history deletion.
* **License audit.** Every on-device model carries a license field (see
  `ml/configs/mobile_bundle.yaml`). This file is the source of truth for
  compliance reviews.

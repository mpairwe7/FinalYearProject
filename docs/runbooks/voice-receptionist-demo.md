# Runbook — Simulated AI Phone Receptionist ("Call URA")

Operator and demo runbook for the simulated AI telephone receptionist powered by
Pipecat 1.11, Whisper-SALT per-word acoustic confidence scoring, ClarifyGate terminology
disambiguation, and staff browser audio bridge takeover.

---

## 1. Overview & Architecture

The URA receptionist simulates a toll-free customer support line (`0800 117 000`)
directly within the web platform:

```
Taxpayer browser (CallScreen)                       Staff browser (/calls)
 mic → AudioWorklet PCM16 16k ─┐                     lobby WS  ← call.started / transfer_requested / ended
 PCM player ← PCM16 16k ───────┤                     live WS   ← snapshot + turn events (per call, opt-in)
                               │ WS /v1/calls/stream  audio WS  ⇄ officer mic / caller audio (bridged mode)
                               ▼                                  ▲
 FastAPI (single worker, flag voice_receptionist) ─────────────────┘
  receptionist/ws.py: auth, consent, caps → CallRoom (in-process registry)
   Pipecat Pipeline (PipelineWorker):
     transport.input()  [FastAPIWebsocketTransport + BrowserCallSerializer]
     → CallerAudioTap        (bridged: forward caller PCM to officer leg)
     → VADProcessor          (Silero; broadcasts VADUser{Started,Stopped}SpeakingFrame)
     → LivePartialTranscriptTap (re-decodes the utterance-so-far every 0.8 s → `final:false` captions)
     → UraWhisperSTT         (SegmentedSTTService → SpeechModel.transcribe(with_words=True))
     → TranscriptTap         (stash word probs in CallState, `final:true` caption events)
     → user_aggregator       (LLMContextAggregatorPair; turns.py: VAD opens a turn while the assistant
                              is quiet, 2 transcribed words barge in while it talks; the turn closes
                              RECEPTIONIST_TURN_TIMEOUT_S after VAD's 0.5 s stop — Smart Turn v3
                              covers neither Luganda nor Swahili)
     → UraReceptionistBrain  (custom LLM service: intents → ClarifyGate → ChatModel.generate → transfer)
     → UraSpeechTTS          (TTSService → SpeechModel.synthesize → PCM16 16k; Luganda streams
                              from the Orpheus sidecar when ORPHEUS_TTS_URL is set)
     → transport.output()
   Observers: CallMetricsObserver (turn latency, barge-ins)
   OfficerLeg: officer PCM → caller socket (bypasses pipeline); energy-VAD segments → STT → "officer" turns
   On end: summary.py (always English; Sunflower first for Luganda calls, Gemini first otherwise)
           + metrics.py → store.py (voice_calls / voice_call_turns)
```

That is the single-engine cascaded call. `RECEPTIONIST_ENGINE=gemini_live` swaps the
middle for Gemini Live speech-to-speech, and `FLAG_RECEPTIONIST_LANGUAGE_DETECTION`
runs both engines side by side and moves the call between them by language — see
**§6 Multilingual calls**.

---

## 2. Configuration & Environment Variables

All switches and thresholds are configured via environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `FLAG_VOICE_RECEPTIONIST` | `false` | Master feature flag for call socket, audio bridge, and staff Calls page |
| `FLAG_VOICE_CONSENT` | `true` | Enforces explicit voice consent dialog prior to audio processing |
| `FLAG_TICKET_QUEUE` | `true` | Enforces human queue oversight; transfers fail safely if disabled |
| `WORKERS` | `1` | **Required single worker** — call room state and bridge sockets live in-memory |
| `RECEPTIONIST_CLARIFY_THRESHOLD` | `0.55` | Word acoustic probability below which ClarifyGate assesses candidates |
| `RECEPTIONIST_MAX_CLARIFY_ATTEMPTS` | `2` | Number of failed clarification attempts before auto-transferring |
| `RECEPTIONIST_TRANSFER_TIMEOUT_S` | `90` | Seconds to hold for available officer before promising a callback |
| `RECEPTIONIST_MAX_CALL_S` | `900` | Hard ceiling for one call connection (15 minutes) |
| `RECEPTIONIST_FILLER_AFTER_MS` | `450` | Latency threshold before playing filler token (pool of short tokens) |
| `RECEPTIONIST_MAX_SPOKEN_SENTENCES` | `3` | Spoken truncation limit before prompting *"Would you like more detail?"* |
| `RECEPTIONIST_TTS_VOICE` | `en-KE-AsiliaNeural` | Verified East-African English edge-tts speaker |
| `RECEPTIONIST_ENGINE` | `cascaded` | Conversational engine: `cascaded` (Silero+Whisper+RAG+EdgeTTS) or `gemini_live` |
| `GEMINI_LIVE_MODEL` | `models/gemini-2.5-flash-native-audio-latest` | Multimodal speech-to-speech model used when `RECEPTIONIST_ENGINE=gemini_live` |
| `GEMINI_LIVE_VOICE` | `Aoede` | Gemini voice ID (`Aoede`, `Charon`, `Fenrir`, `Kore`, `Puck`) |
| `RECEPTIONIST_LIVE_PARTIALS` | `true` | Streams interim transcripts of the caller's own speech back to their screen |
| `RECEPTIONIST_PARTIAL_INTERVAL_S` | `0.8` | Seconds between interim re-decodes of the utterance in progress |
| `RECEPTIONIST_TURN_TIMEOUT_S` | `1.0` | Silence after VAD's 0.5 s stop window before the cascaded turn closes |
| `RECEPTIONIST_CLARIFY_THRESHOLD_<LANG>` | global value | Per-language word-probability threshold (e.g. `_LG`) |
| `RECEPTIONIST_CLARIFY_REPEAT_<LANG>` | `false` for `LG`, else `true` | Whether "please repeat that word" is asked in that language |
| `RECEPTIONIST_ALLOW_EDGE_STANDIN_LG` | `false` | Allow an English edge-tts voice to read a Luganda line on a call |
| `FLAG_RECEPTIONIST_LANGUAGE_DETECTION` | `false` | Multilingual calls — §6 |

---

## 3. Acoustic & Hardware Requirements

1. **Headsets are mandatory on both machines during live demos.**
   Bridging caller and officer audio in the same acoustic space without headsets
   creates microphone feedback loops. Both parties must use headphones with
   hardware or software echo cancellation.
2. **GPU host required for Whisper-SALT per-word log-probabilities.**
   CUDA acceleration computes transition scores via `compute_transition_scores`
   without CPU stalls.

---

## 4. Starting the Stack on GPU Host

```bash
cd App && GPU_ID=2 docker compose \
  -f docker-compose.yml \
  -f docker-compose.local-retrieval.yml \
  -f docker-compose.local-sunflower.yml \
  -f docker-compose.gpu-salt.yml up -d --build
```

Verify startup in container logs:
```bash
docker compose logs api | grep "Voice receptionist"
# Voice receptionist schema initialized
```

Expose using ngrok:
```bash
ngrok http 8000
```

---

## 5. End-to-End Demo Script

### Step 1: Taxpayer Initiates Call
1. In the taxpayer chat header, click the **Phone** icon ("Call URA").
2. The consent dialog appears:
   *"This call will be recorded and transcribed to assist URA officers and improve taxpayer support."*
3. Click **Accept & Call**.
4. The synthesizer plays two PBX dialing ring pulses.
5. The AI answers (always in English, whatever the chat language is — §6):
   *"Hi, thanks for contacting URA. I'm your assistant today. Ask your question in your preferred language — English, Luganda or Swahili — and I'll give you the answer in that language. How can I help you today?"*
   The recording notice is the consent dialog in step 3; the greeting no longer repeats it.

### Step 2: Knowledge Retrieval (Happy Path)
1. Caller asks: *"How do I register for an instant TIN?"*
2. Filler plays after 1 second if retrieval takes longer: *"Let me check that for you."*
3. Assistant speaks the verified URA statutory guidance within ~3-4 seconds.
4. Spoken text is trimmed to 4 sentences and acronyms are pronounced cleanly ("T-I-N", "U-R-A").
5. The call screen keeps a running chat transcript of both sides — nothing scrolls away,
   and it auto-follows the newest line unless the caller has scrolled back to read.
   The whole thread sits in an `aria-live` log region for screen-reader assistance.
6. The caller's own words appear **while they are still speaking**: Whisper-SALT is a
   segmented recognizer, so `LivePartialTranscriptTap` re-decodes the growing utterance
   every `RECEPTIONIST_PARTIAL_INTERVAL_S` and sends each hypothesis as a `final:false`
   caption (dashed bubble + caret). The turn's real transcript replaces it in place when
   VAD closes the turn. Partial decodes cost 0.22 s (1 s of speech) to 0.63 s (4 s) on one
   RTX A6000 with the model resident, and stop the moment the turn closes, so they never
   contend with the answer behind them.

### Step 3: Clarification ("Did you mean...?")
1. Caller indistinctly slurs a domain term: *"I want to know about group mid-port."*
2. Whisper-SALT scores "mid-port" at probability `0.31` (< 0.55 threshold).
3. `ClarifyGate` matches the Metaphone key against the URA lexicon + "group" bigram boost (`0.925` score).
4. AI asks: *"Excuse me, did you say **import**?"*
5. Caller responds: *"Yes, import."*
6. AI confirms: *"So if I got it right, you're asking about **group import** — is that right?"*
7. Caller says: *"Yes."*
8. AI answers the corrected query directly from the knowledge base.

### Step 4: Transfer to Human Officer
1. Caller says: *"I want to talk to an officer."* (or clicks **Talk to an officer**).
2. AI announces: *"I'm connecting you to a URA officer, please hold."*
3. Status changes to **Transferring** with a ticket reference generated via `_maybe_create_ticket`.
4. Staff on `/calls` receives an incoming transfer banner with a pulsing alert pill.

### Step 5: Officer Takeover & Live Audio Bridge
1. Officer opens `/calls` and clicks **Take Call**.
2. Caller hears a two-note join chime and status updates to *"Officer {Name} joined"*.
3. The AI goes silent. Caller PCM audio streams directly to the officer headset.
4. Officer audio streams directly to the caller socket.
5. `EnergyVAD` segments officer speech, transcribes turns, and records them in the live transcript.

### Step 6: Hang-up & Performance Evaluation
1. Either side clicks **End call** (or hangs up).
2. Background task runs `summary.py`:
   Generates a redacted structured JSON summary with subject, key facts, and resolution.
3. Performance metrics are recorded in `voice_calls`:
   Containment, turn latencies (p50/p95), ASR word confidence, barge-ins, and officer rating.
4. Officer submits a 1-5 star review and note.
5. The **Performance Overview** tab updates SLO gauge cards and KPI aggregates.

---

## 6. Multilingual calls (English, Luganda, Swahili)

Behind `FLAG_RECEPTIONIST_LANGUAGE_DETECTION` (needs `FLAG_VOICE_RECEPTIONIST`, a
`GEMINI_API_KEY`, Whisper-SALT, and — for a native Luganda voice — the Orpheus sidecar).
Taxpayers routinely select English in the chat and then speak Luganda, so a call does
not trust the selection: it opens in English and follows what the caller *says*.

```
 transport.input() → CallerAudioTap → LanguageSentinel ─► ParallelPipeline ─► ClientEventOutlet → transport.output()
                                        │ (own Silero VAD,   ├─ gemini_live: EngineGate → user agg → OfficerRequestBridge
                                        │  SALT language id, │    → GeminiLiveLLMService → transcript tap → OutputHoldGate
                                        │  never adds audio  │    → assistant agg → EngineGate          (English, Swahili)
                                        │  latency)          └─ cascaded: EngineGate → VAD → partials → Whisper-SALT
                                        ▼                         → user agg → brain → UraSpeechTTS/Orpheus
                              LanguagePolicy → LanguageRouter        → assistant agg → EngineGate          (Luganda)
                              (lock / switch / hold / re-ask / events)
```

| Language | Engine | Hears with | Answers with | Voice |
|---|---|---|---|---|
| English | Gemini Live | Gemini | RAG tool (`query_ura_tax_knowledge`, retrieval in English) | Gemini (`GEMINI_LIVE_VOICE`) |
| Swahili | Gemini Live | Gemini | same tool — Gemini translates the question in and the answer out | Gemini |
| Luganda | cascaded | Whisper-SALT (`lg` token) | `ChatModel.generate(locale="lg")` → Sunflower | Orpheus-3B `salt_lug_0001` |

**How the language is decided** (`receptionist/language.py`, all unit-tested):

- Every utterance is scored by `SpeechModel.identify_language` — Whisper-SALT's
  language token, one encoder pass (tens of ms). Short or unsure utterances are also
  decoded as text, for explicit requests and code-switching.
- Utterances under `RECEPTIONIST_LID_MIN_SPEECH_S` (1.5 s) never decide ("Hello",
  "Gyebale ko", "yes").
- The first content utterance locks the call. Moving off English there needs a vote
  of at least `RECEPTIONIST_LID_HYSTERESIS_CONFIDENCE` (0.70); a weaker one leaves the
  call open and the next utterance decides.
- Locked calls switch on one vote ≥ `RECEPTIONIST_LID_SWITCH_CONFIDENCE` (0.90), two
  in a row ≥ 0.70, or one ≥ 0.70 whose text has at least three words of the new
  language and none of the current one.
- Code-switched Luganda ("Nsaba okumanya ku TIN yange") counts as Luganda when the
  text has two Luganda words (more than Swahili ones) and the acoustics lean Bantu —
  P(lg) ≥ 0.35, or P(lg)+P(sw) ≥ 0.5 (SALT often hears mixed Luganda as Swahili), or
  the call is already in Luganda. Sunflower handles mixed speech; Gemini does not know
  Luganda.
- Explicit requests switch at once: "Can we speak Luganda?", "Tuyinza okwogera
  Oluganda?", "Naomba tuongee Kiswahili", "speak English".
- The caller can pin the language from the chip on the call screen; after that only a
  spoken request moves it.

**What the caller experiences:** before the language is locked, Gemini's reply to the
first question is held (`OutputHoldGate`, at most `RECEPTIONIST_LID_HOLD_TIMEOUT_MS`,
600 ms) until the vote lands. English: released, usually before Gemini has produced
any audio. Luganda: the switch waits for the end of the caller's turn
(`RECEPTIONIST_TURN_TIMEOUT_S` of silence — votes are cast on every 0.4 s VAD segment,
and a long question is several), Gemini's reply stays pinned meanwhile, then it is
discarded, the call moves to the cascaded engine and the *whole* turn is transcribed
in Luganda and answered after a Luganda filler — the caller never repeats it. After
the lock nothing is held; a switch interrupts whatever is playing (the browser flushes
its player on `{"type":"interrupt"}`).

In a Luganda or Swahili call the clarification gate only ever confirms URA acronyms
("Nsonyiwa, ogambye VAT?"), never the language's own words, and reads a loanword the
local way: "Vati" and "tiini" are VAT and TIN.

**Protocol additions on `WS /v1/calls/stream`:**

| Direction | Message | Meaning |
|---|---|---|
| client → server | `call_start.preferred_locale` | the chat's language — a hint for staff and metrics only |
| server → client | `call_ready.language_detection`, `.languages`, `.language` | show the language chip; the opening language |
| server → client | `{"type":"language","language":"lg","source":"auto"\|"explicit"\|"override","confidence":0.93}` | the call's language is now … |
| client → server | `{"type":"set_language","language":"sw"}` | pin the call to a language |

Staff see a language badge on the queue row and the case header, "Luganda caller" on
the handoff brief, the switch as a `Language: Luganda (detected, 93%)` note in the
transcript, and language tiles on the metrics card. Summaries are always English.

**Bring-up** (GPU 4 for the stack, GPU 7 for the voice — check `nvidia-smi` first):

```bash
cd App && GPU_ID=4 ORPHEUS_GPU_ID=7 ORPHEUS_TTS_URL=http://orpheus-tts:8100 \
  FLAG_RECEPTIONIST_LANGUAGE_DETECTION=true docker compose \
  -f docker-compose.yml -f docker-compose.local-retrieval.yml \
  -f docker-compose.local-sunflower.yml -f docker-compose.gpu-salt.yml \
  --profile multilingual up -d --build
docker logs ura-app-orpheus-tts 2>&1 | grep "sidecar ready"
docker logs <api> 2>&1 | grep "Receptionist phrase pre-warm"   # {'lg': {'cached': N, 'failed': 0}}
```

Orpheus runs weight-only FP8 (`ORPHEUS_QUANTIZATION=fp8`): at bf16 an RTX A6000
generates at only ~0.96× real time, which leaves no headroom; FP8 measured 234 ms to
first audio at 0.63× real time (`evals/reports/orpheus_tts_2026-09-24_*.json`).

**Before any demo:** the Luganda and Swahili lines in `receptionist/phrases.py` and the
call-screen strings in `lib/i18n/{lg,sw}.ts` are drafts. Have a native speaker of each
check them, and have 3–5 Luganda speakers rate the Orpheus samples blind
(`evals/orpheus_tts/listening_sheet.csv`; the speaker key is kept out of git).

**Troubleshooting**

| Symptom | Look at |
|---|---|
| Luganda answers in an English accent, or silent | `ORPHEUS_TTS_URL` unset or sidecar down (`Orpheus TTS unreachable` in api logs); `RECEPTIONIST_ALLOW_EDGE_STANDIN_LG=false` drops the English stand-in on purpose |
| English caller moved to Luganda | api log `Language vote lg p=…` lines; raise `RECEPTIONIST_LID_HYSTERESIS_CONFIDENCE` or `RECEPTIONIST_LID_MIN_SPEECH_S` |
| First English answer feels late | `language.held_ms_p95` in the call metrics; lower `RECEPTIONIST_LID_HOLD_TIMEOUT_MS` |
| Call never leaves English | `SpeechModel.identify_language` returning `whisper_salt_unavailable` — Whisper-SALT not loaded |


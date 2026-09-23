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
     → user_aggregator       (LLMContextAggregatorPair; Silero VAD + Smart Turn v3; barge-in)
     → UraReceptionistBrain  (custom LLM service: intents → ClarifyGate → ChatModel.generate → transfer)
     → UraSpeechTTS          (TTSService → SpeechModel.synthesize → PCM16 16k)
     → transport.output()
   Observers: CallMetricsObserver (turn latency, barge-ins)
   OfficerLeg: officer PCM → caller socket (bypasses pipeline); energy-VAD segments → STT → "officer" turns
   On end: summary.py (Gemini flash-lite / fallback) + metrics.py → store.py (voice_calls / voice_call_turns)
```

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
5. The AI answers:
   *"Hello, you've reached URA. I'm the virtual assistant; this call is transcribed so an officer can help if needed. How can I help you?"*

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

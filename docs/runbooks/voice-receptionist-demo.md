# Runbook — Simulated AI Phone Receptionist ("Call URA")

Operator and deployment runbook for the browser voice receptionist powered by
Pipecat 1.12, self-hosted LiveKit WebRTC media, Whisper-SALT confidence scoring,
ClarifyGate terminology disambiguation, and staff takeover.

---

## 1. Overview & Architecture

The URA receptionist simulates a toll-free customer support line (`0800 117 000`)
inside the web platform. In production, LiveKit carries caller, agent and officer
media; authenticated application WebSockets carry only call setup and control:

```
Taxpayer browser ─┐                                         ┌─ Staff browser
 mic + speaker   │      LiveKit WebRTC room (encrypted)    │ mic + speaker
 call controls ──┼───────────────┬─────────────────────────┼─ call controls
                 │               │ agent audio/data        │
                 └───────────────┴─────────────────────────┘
                                 │
FastAPI (one worker and one replica; process-local call actor)
  /v1/calls/stream: auth, consent, rate limits, room token + JSON controls
  /v1/admin/calls/{id}/audio: staff role/claim checks + short-lived room token
   Pipecat LiveKitTransport:
     room audio → CallerAudioTap (only caller reaches AI; officer turns are captioned)
     → VADProcessor          (Silero; broadcasts VADUser{Started,Stopped}SpeakingFrame)
     → LivePartialTranscriptTap (re-decodes the utterance-so-far every 0.8 s → `final:false` captions)
     → UraWhisperSTT         (SegmentedSTTService → SpeechModel.transcribe(with_words=True))
     → TranscriptTap         (stash word probs in CallState, `final:true` caption events)
     → user_aggregator       (LLMContextAggregatorPair; turns.py: VAD opens a turn while the assistant
                              is quiet, 2 transcribed words barge in while it talks; the turn closes
                              RECEPTIONIST_TURN_TIMEOUT_S after VAD's 0.5 s stop — Smart Turn v3
                              covers neither Luganda nor Swahili)
     → UraReceptionistBrain  (custom LLM service: intents → ClarifyGate → ChatModel.generate → transfer;
                              integrates GPU dense BGE-M3 + DBSF fusion + GPU mxbai-rerank with clean
                              single-sigmoid probabilities, G126–G132)
     → UraSpeechTTS          (TTSService with push_start_frame=True, push_stop_frames=True →
                              SpeechModel.synthesize → PCM16 16k; streams from the local Orpheus
                              sidecar for the languages in ORPHEUS_TTS_LANGUAGES and emits
                              TTSStartedFrame/TTSStoppedFrame so BotStoppedSpeakingFrame fires
                              immediately without the 3.0s BOT_VAD_STOP_FALLBACK_SECS delay, G249)
     → LiveKit audio/data output
   Observers: CallMetricsObserver (turn latency, barge-ins)
   On end: summary.py (always English; local Sunflower, or a deterministic summary if vLLM is down)
           + metrics.py → store.py (voice_calls / voice_call_turns)
```

This is still a browser simulation, not a carrier-connected telephone service.
The LiveKit media path is production-capable, but active call coordination,
staff ownership and reconnect timers remain process-local. Production therefore
requires exactly one API worker and replica plus the explicit single-replica
acknowledgement. A process restart or node loss ends active calls; this is not
HA and sticky routing does not make it HA. Horizontal scaling stays blocked until
call actors/events move to shared durable state and failover is exercised.

The backend mints five-minute, room- and identity-scoped LiveKit JWTs only after
taxpayer authentication/consent or staff role/claim checks. The API secret never
leaves the backend. Browser media does not traverse the application WebSocket.
Browser participant grants allow only microphone tracks and no client-published
data (observers cannot publish); the backend agent alone publishes call events.
Officer transfer removes the old participant and revokes live media permissions
before requeueing, and the room is closed during call teardown. Stale or unissued
identities are removed as soon as the server reports a reconnect. The allowlist
is only consulted when a participant joins, so the backend also removes
participants whose access ends while they are connected: a supervisor listener
when their console closes, an officer whose reconnect grace lapses (which also
frees the call's officer media seat for the next claimant — before this, the
lapsed identity refused the next officer's audio with 4409), and an officer
whose claim lapsed or whose caller hung up while their media was still joining.
Those removals are best effort and logged by a digest of the identity; a
transfer instead refuses to proceed (503) if removal fails. Self-hosted
LiveKit does not invalidate already issued JWTs: the five-minute TTL is an
additional exposure bound, not immediate revocation. If the threat model
requires strict pre-join revocation, use LiveKit Cloud or do not enable the
receptionist until an equivalent self-hosted token-revocation control is in
place.
Production readiness checks for a secure LiveKit URL, credentials, the LiveKit
transport selection, one worker/replica and the operator acknowledgement — and,
since every call now runs on the local engine, that it can: `SPEECH_ENABLED` and
`LLM_ENABLED` on, `ORPHEUS_TTS_URL` set, and every language in
`RECEPTIONIST_LANGUAGES` voiced by Orpheus (`ORPHEUS_TTS_LANGUAGES`).
`python -m app.production_readiness --as-production` reports those G36 checks.
Browser sockets send credentials in their first application message (never in
the URL), and staff/audio sockets validate browser `Origin` against
`CORS_ORIGINS`. Neither protects the credential in transit: that is TLS's job,
so in production the application's own WebSockets must be `wss://` end to end
as well as LiveKit's (see the rollout checklist below). The browser accepts a
`ws://` LiveKit URL only when the page itself is plain `http:` (a local demo);
on an HTTPS page it refuses, and it takes call events over the data channel
only from the backend agent's identity (`ura-agent-…`).

That is the whole engine, and every call language runs on it: Whisper-SALT,
Sunflower on vLLM against Qdrant, and the Orpheus voice, all from locally cached
Hugging Face weights on the GPU stack. `FLAG_RECEPTIONIST_LANGUAGE_DETECTION` adds a
language sentinel that moves a call between English, Luganda and Swahili on this same
engine — see **§6 Multilingual calls**. The Gemini Live speech-to-speech engine was
removed on 2026-09-30. Since G122 (2026-10) **no hosted model is used anywhere on a
call**. The officer brief, the call summary, the Luganda query bridge and the Luganda
answer run on Sunflower only, with deterministic fallbacks when vLLM is down. A call
turn's English or Swahili answer comes from the chat service, which is held to local
models for that turn whatever chat is configured to use: no Gemini, Workers AI or
Sunbird API, and no FAQ judge (`app/providers/scope.py`).

---

## 2. Configuration & Environment Variables

All switches and thresholds are configured via environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `FLAG_VOICE_RECEPTIONIST` | `false` | Master feature flag for call socket, audio bridge, and staff Calls page |
| `RECEPTIONIST_MEDIA_TRANSPORT` | `websocket` | Local/demo default; production requires `livekit` |
| `LIVEKIT_URL` | unset | Public `wss://` endpoint for the self-hosted LiveKit deployment |
| `LIVEKIT_API_KEY` | unset | Server-only LiveKit API key; store in the deployment secret manager |
| `LIVEKIT_API_SECRET` | unset | Server-only LiveKit API secret; never expose to browser/build args |
| `VOICE_RECEPTIONIST_REPLICAS` | unset | Must be `1` until shared call actor state is implemented |
| `VOICE_RECEPTIONIST_SINGLE_REPLICA_ACK` | `false` | Explicitly acknowledge active calls end if the API process is lost |
| `FLAG_VOICE_CONSENT` | `true` | Enforces explicit voice consent dialog prior to audio processing |
| `FLAG_TICKET_QUEUE` | `true` | Enforces human queue oversight; transfers fail safely if disabled |
| `WORKERS` | `4` in the Docker image | Explicitly set `1` for the receptionist — active call state is process-local |
| `RECEPTIONIST_CLARIFY_THRESHOLD` | `0.55` | Word acoustic probability below which ClarifyGate assesses candidates |
| `RECEPTIONIST_MAX_CLARIFY_ATTEMPTS` | `2` | Number of failed clarification attempts before auto-transferring |
| `RECEPTIONIST_TRANSFER_TIMEOUT_S` | `90` | Seconds to hold for available officer before promising a callback |
| `RECEPTIONIST_HOLD_UPDATE_S` | `30` | Seconds between *"Thank you for holding…"* lines while the caller waits for an officer, and between reassurances while an officer has them on hold; `0` turns them off |
| `CXP_TIMEZONE` | `Africa/Kampala` | Time zone of the officers' working hours (`app/officer_hours.py`) |
| `CXP_OPEN_HOUR` / `CXP_CLOSE_HOUR` | `7` / `23` | Whole hours the desk opens and closes (the CXP desk closes at 11 PM). A close at or before the open runs past midnight; `0`/`24` never closes |
| `CXP_WORKING_DAYS` | every day | Days a shift starts on, e.g. `mon,tue,wed,thu,fri` |
| `CXP_CLOSED_DATES` | unset | `YYYY-MM-DD,…` with no shift (public holidays) |
| `RECEPTIONIST_BRIEF_EVERY_TURNS` | `3` | Caller turns between rolling rebuilds of the officer's brief (always rebuilt on transfer) |
| `RECEPTIONIST_CLAIM_TIMEOUT_S` | `20` | How long an officer's "Take call" holds the call before their audio must join |
| `RECEPTIONIST_MAX_CALL_S` | `900` | Hard ceiling for one call connection (15 minutes). A value that is set but not a positive number stops startup and fails the G36 gate instead of falling back |
| `RECEPTIONIST_FILLER_AFTER_MS` | `450` | Latency threshold before playing filler token (pool of short tokens) |
| `RECEPTIONIST_MEMORY_MIN_ASR_CONF` | `0.6` | A call turn heard below this mean word probability is answered but not written to long-term memory (a mishearing must not become a fact about the caller; needs `FLAG_MEMORY_ENABLED` and personalization consent to matter) |
| `RECEPTIONIST_MAX_SPOKEN_SENTENCES` | `3` | Spoken truncation limit before prompting *"Would you like more detail?"* (yes reads on; an officer offer takes its place, one question a turn) |
| `RECEPTIONIST_TTS_VOICE` | `en-KE-AsiliaNeural` | edge-tts speaker for English when Orpheus does not voice it (a fallback) |
| `ORPHEUS_TTS_URL` | unset (`http://orpheus-tts:8100` in compose) | The local Orpheus voice sidecar |
| `ORPHEUS_TTS_LANGUAGES` | `lg` (`lg,sw,en` in the GPU overlay) | Languages Orpheus voices; the rest fall to Spark-TTS-SALT (sw, lg), then edge-tts |
| `ORPHEUS_TTS_MAX_CHARS` | `120` | Longest text sent to Orpheus in one request; longer text is voiced in pieces cut at sentence, clause, then word breaks. The sidecar stops at 16.98 s of audio (`ORPHEUS_MAX_TOKENS` 1400) and spoken digits run to ~0.12 s a character |
| `RECEPTIONIST_LOCAL_BARGE_IN` | `true` | The sentinel's VAD stops the assistant when the caller talks over it, before Whisper has transcribed the two words the engine's own rule waits for (turn off on a device whose speaker leaks into the mic) |
| `RECEPTIONIST_BARGE_IN_MIN_S` | `0.4` | Speech past the VAD onset (itself 0.2 s) that counts as talking over the assistant |
| `RECEPTIONIST_LIVE_PARTIALS` | `true` | Streams interim transcripts of the caller's own speech back to their screen |
| `RECEPTIONIST_PARTIAL_INTERVAL_S` | `0.8` | Seconds between interim re-decodes of the utterance in progress |
| `VOICE_TRANSCRIPT_TTL_DAYS` | `90` | Retention for voice turns and non-ticketed call records |
| `RECEPTIONIST_TURN_TIMEOUT_S` | `1.0` | Silence after VAD's 0.5 s stop window before the cascaded turn closes (standard fallback) |
| `RECEPTIONIST_SLOW_PAUSE_MS` | `600` | Silence after each sentence once the caller has asked the assistant to slow down (the local voices have no speed control) |
| `RECEPTIONIST_IDLE_REPROMPT_S` | `12` | Caller silence, counted from the end of the assistant's speech, before it asks "Are you still there?"; `0` turns silence handling off |
| `RECEPTIONIST_IDLE_REPROMPTS` | `1` | How many such checks before a still-silent call is ended (end reason `caller_idle`) and its call slot freed |
| `RECEPTIONIST_CALL_REVIEW` | `true` | The AI asks "was this call helpful?" — once a call, only after it answered something — at goodbye, or while a caller who asked for an officer is transferred. Audited on `/admin/quality` (AI on calls) |
| `RECEPTIONIST_REVIEW_ANSWER_TIMEOUT_S` | `10` | How long the AI waits for the answer to its goodbye review question before saying goodbye (recorded as no answer) |
| `RECEPTIONIST_CLARIFY_THRESHOLD_<LANG>` | global value | Per-language word-probability threshold (e.g. `_LG`) |
| `RECEPTIONIST_CLARIFY_REPEAT_<LANG>` | `false` for `LG`, else `true` | Whether "please repeat that word" is asked in that language |
| `RECEPTIONIST_ALLOW_EDGE_STANDIN_LG` | `false` | Allow an English edge-tts voice to read a Luganda line on a call |
| `FLAG_RECEPTIONIST_LANGUAGE_DETECTION` | `false` | Multilingual calls — §6 |
| `FLAG_HYBRID_SPEECH_S2S` | `false` | Hybrid S2S + Cascaded Speech Router (`receptionist/hybrid_s2s.py`) |
| `FLAG_VOICE_AGENTIC_WORKFLOWS` | `false` | Spoken confirmation token binding & guided workflows on live calls. A confirmed action is reported done only when its executor reports success (fail-closed, G177) |
| `FLAG_EMOTION_AWARE_PROSODY` | `false` | Acoustic prosody & caller affect conditioning (`receptionist/prosody.py`). Affect needs the caller's words: loudness or pace alone names nothing (see `conversational-safety-and-eq.md`) |
| `PROSODY_ACOUSTIC_ONLY_AFFECT` | `false` | Restores affect labels from loudness or pace alone — for a validation study only |
| `FLAG_SPOKEN_FIGURE_GUARD` | `false` | Dictation pauses & spoken figure verification (`receptionist/spoken_figure_guard.py`) |
| `FLAG_OFFICER_VOICE_COPILOT` | `false` | Live ASR + English router view assist on the officer console during bridged calls |
| `FLAG_CONTINUOUS_RELATIONSHIP_MEMORY` | `false` | Longitudinal `ResolutionCase` checkpointing & bidirectional `Voice <-> Web` carryover |
| `ORPHEUS_TTS_FIRST_CLAUSE_MAX_CHARS` | unset (`55` when set) | Tighter character cap on the opening clause to reduce Orpheus first-audio TTFB |
| `VOICE_SEMANTIC_ENDPOINT_MIN_SILENCE_MS` | `320` | Minimum semantic endpointing silence for syntactically complete turns |
| `VOICE_SEMANTIC_ENDPOINT_NUMERIC_SILENCE_MS` | `1100` | Extended silence tolerance when caller is mid-number (TIN/PRN) or mid-conjunction |
| `FLAG_LOW_BANDWIDTH_VOICE_CODEC` | `false` | Negotiates 8 kHz μ-law / 12 kbps Opus wire transcoding in `BrowserCallSerializer` (`receptionist/serializer.py`) and caption-first degradation on 2G/3G links (`receptionist/low_bandwidth.py`) |
| `FLAG_CALL_DROP_RESUME` | `false` | Enables HMAC-signed caller drop-resume ticket (`RECEPTIONIST_CALLER_RECONNECT_GRACE_S=45`) and shared `call_drop_snapshot` recovery |
| `FLAG_EQ_MULTIMODAL_FUSION` | `false` | Fuses multi-turn lexical distress, native vernacular hardship (`lg`/`sw`/`nyn`/`ach`), and acoustic prosody into `AffectiveState` + affect-attuned 450 ms voice fillers |
| `FLAG_WORKFLOW_TANGENT_RESUME` | `false` | Answers mid-workflow policy side-questions and resumes the parked workflow step |
| `FLAG_AUTONOMOUS_OUTCOME_VERIFICATION` | `false` | Binds missing calculator/tool parameters from consented `verified_slot_bank`, `SemanticMemory`, and prior DAG receipts |
| `FLAG_OFFICER_PREFERENCE_CAPTURE` | `false` | Captures PII-redacted edit diffs between AI co-pilot drafts and final officer replies for DPO/ORPO export |

Every call runs up to `RECEPTIONIST_MAX_CALL_S` whatever its language: no stage has a
provider session window any more, and Pipecat's own idle cancel (300 s without speech
or transcripts in the pipeline) is off in both pipelines — while an officer has the
call its audio bypasses the pipeline, and the single-language one once cut every
officer call at five minutes, recorded as a caller hang-up (G193). See Pipecat's
[transport selection guide](https://docs.pipecat.ai/client/concepts/choosing-a-transport)
before designing a production rollout. The current browser WebSocket audio path
is demo-only; Pipecat recommends WebRTC for client-to-server voice, while
WebSockets are suited to server-to-server or text-only use.

Supervisor “AI-only calls” is an operational routing measure (at least one AI
answer, no officer transfer, and no timeout/error). It does not assert that the
taxpayer's issue was resolved; resolution needs a separate explicit outcome.
With multi-tenancy enabled, historical calls are tenant-filtered. Ticket history
is omitted because the current ticket table has no tenant key and cannot be
filtered safely by `user_id` alone.

### Production LiveKit rollout

Treat this as a controlled browser-voice deployment, not a telco launch. Before
enabling the feature:

1. Deploy LiveKit on a supported host/network with a public DNS name and trusted
   TLS certificate. Put the WebSocket signaling endpoint behind TLS on `443`;
   permit the documented LiveKit TCP/UDP media ports from client networks. For
   restrictive networks, configure TURN/TLS and its certificate as well. Use
   LiveKit's [self-host deployment guide](https://docs.livekit.io/transport/self-hosting/deployment/)
   and [firewall/port matrix](https://docs.livekit.io/transport/self-hosting/ports-firewall/)
   as the source of truth for the selected LiveKit release.
2. Configure LiveKit with Redis on a private network, external-IP discovery that
   matches the host's routable address, production API credentials, capacity
   limits, and monitoring. Do not use the development `livekit-server --dev`
   mode, placeholder keys, or an untrusted/self-signed certificate.
3. Store `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` in the secret manager and
   inject them only into the backend runtime. Configure `LIVEKIT_URL=wss://…`,
   `RECEPTIONIST_MEDIA_TRANSPORT=livekit`, `WORKERS=1`,
   `VOICE_RECEPTIONIST_REPLICAS=1`, and
   `VOICE_RECEPTIONIST_SINGLE_REPLICA_ACK=true`. The acknowledgement explicitly
   accepts active-call loss on API process failure; it is not a failover control.
   Run the Orpheus voice sidecar beside the API on its own GPU and set
   `ORPHEUS_TTS_URL` and `ORPHEUS_TTS_LANGUAGES=lg,sw,en`; the gate refuses a
   production receptionist without them, because the only other local voice
   (Spark-TTS-SALT) takes several seconds a sentence.
4. Serve the web app and API over HTTPS only, and make the ingress refuse or
   redirect plain HTTP — including the WebSocket upgrades on
   `/v1/calls/stream`, `/v1/admin/calls/stream` and
   `/v1/admin/calls/{call_id}/audio`. Each of those sockets carries the user's
   bearer token in its first message and returns a LiveKit room token; `Origin`
   checks stop cross-site use, not eavesdropping. Where TLS terminates at a
   proxy, keep the proxy-to-API hop on a private network. Verify with the
   browser devtools that every socket opens as `wss://`.
5. Keep `FLAG_AUTH_REQUIRED`, `FLAG_MULTI_TENANT`, `FLAG_AUDIT_LEDGER`, and
   `FLAG_TICKET_QUEUE` enabled, and satisfy the remaining global production gates
   in `docs/PRODUCTION_GATES.md`. Confirm tenant RLS and voice consent behavior
   against the deployed databases and identity provider.
6. Run the production gate before enabling the feature:

   ```bash
   APP_ENV=production PYTHONPATH=App/backend python3 -m app.production_readiness
   PYTHONPATH=App/backend python3 -m pytest App/backend/tests/test_receptionist_livekit.py App/backend/tests/test_receptionist_claims.py -q
   ```

   The gate validates app-side values only. Separately verify TLS trust, DNS,
   UDP reachability, TURN from a restricted client network, token expiry, caller
   and officer audio isolation, microphone-only publishing, consent rejection,
   transfer revocation/hold/end, officer reconnect and a lapsed reconnect
   (the next officer must be able to take the call), a supervisor listener
   being removed when their console closes, and audio on iOS Safari (the call
   screen shows "Tap here to hear the call" when the browser holds audio back)
   in a browser game day before opening access to taxpayers.
7. Enable the flag only after those checks and monitor LiveKit room/participant
   counts, ICE/TURN failures, call setup time, media disconnects, Pipecat pipeline
   errors, API saturation, and transfer queue outcomes. Keep a tested rollback
   that turns `FLAG_VOICE_RECEPTIONIST` off.

The current application supports one API process for active calls. Do not set
multiple Kubernetes replicas, multiple ASGI workers, or scale-out autoscaling for
the receptionist; LiveKit itself can scale independently, but it does not make
the application's in-memory claims, call state, event hub or timers shared.
Adding shared actor/event state, restart recovery, and a tested media/session
reconnect policy is a prerequisite for HA or horizontal API scaling. LiveKit
tokens are restricted to one room and participant and expire after five minutes;
they authorize room entry, not an active participant's maximum call duration.

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
cd App && GPU_ID=2 VLLM_GPU_ID=5 ORPHEUS_GPU_ID=4 docker compose \
  -f docker-compose.yml \
  -f docker-compose.local-retrieval.yml \
  -f docker-compose.local-sunflower.yml \
  -f docker-compose.gpu-salt.yml \
  --profile multilingual up -d --build
```

Verify startup in container logs:
```bash
docker compose logs api | grep "Voice receptionist"
# Voice receptionist schema initialized
```

Expose using ngrok:
```bash
./scripts/manage_ngrok.sh start
# Publishes local frontend (port 3032 -> API :8083 proxy) over the enterprise domain:
# https://struttingly-nongeological-briella.ngrok-free.dev
```

### Tearing the stack down — leave no VRAM behind

```bash
python3 scripts/cleanup_gpu_processes.py --dry-run   # list what would be released
python3 scripts/cleanup_gpu_processes.py             # release it, then verify
```

The script frees everything this project holds on the GPUs and then re-queries
`nvidia-smi`; it exits non-zero (`STILL_ON_GPU`) if anything of this project is
still resident, and also when Docker cannot be queried, so a failed lookup is
never reported as clean. It covers what a plain `docker compose down` misses:

- profile-gated services (`orpheus-tts` needs `--profile multilingual`);
- orphan containers from other compose chains (`ura-app-connectors`, the dev2 stack);
- host processes started from agent scratchpad worktrees.

Containers are matched by their compose working-dir label (`~/Mpairwe7`), not by
the process's path: a container process reports a container-side cwd such as
`/app`, which a path match would skip as external. Other projects' containers and
processes on the shared box are never touched. Stopping the stack ends the ngrok
upstream (`502`) until it is brought up again.

---

## 5. End-to-End Demo Script

### Step 1: Taxpayer Initiates Call
1. In the taxpayer chat header, click the **Phone** icon ("Call URA").
2. The consent dialog appears:
   *"This call will be recorded and transcribed to assist URA officers and improve taxpayer support."*
3. Click **Accept & Call**.
4. The synthesizer plays two PBX dialing ring pulses.
5. The AI answers (always in English, whatever the chat language is — §6):
   *"Hi, thanks for contacting URA. I'm your AI assistant today. Ask your question in your preferred language — English, Luganda, Swahili, Runyankole or Acholi — and I'll give you the answer in that language. You can ask for an officer at any time. How can I help you today?"*
   It says it is an AI, and that a person is one request away, before anything else.
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
   - Words whose top lexicon candidate already matches the transcribed word (`cand_term == clean_target.lower()`, e.g., `"VAT"` with subword probability `< 0.55`) or context-repaired English homophones (`"standard vet rate"` $\rightarrow$ `"standard VAT rate"`, `"for a teen"` $\rightarrow$ `"for a TIN"`) are skipped during low-confidence assessment (`G249`), continuing to any remaining low-confidence words in the utterance.
4. AI asks: *"Excuse me, did you say **import**?"*
5. Caller responds: *"Yes, import."*
6. AI confirms: *"So if I got it right, you're asking about **group import** — is that right?"*
7. Caller says: *"Yes."*
8. AI answers the corrected query directly from the knowledge base.
   - If instead of a short yes/no or 1–4 word replacement term the caller asks a complete new question on the next turn (e.g., *"How do I register if I have no TIN?"*), `UraReceptionistBrain` supersedes the open clarification state and answers the new question directly (`G249`).

### In-call requests the assistant handles itself

Short requests — eight words or fewer, so a question that merely contains one
of these words is answered, not obeyed — in English, Luganda or Swahili:

| The caller says | The assistant |
|---|---|
| "Could you repeat that?", "Kiddemu", "Rudia tena" | repeats its last answer |
| "Speak slower", "Yogera mpola", "Ongea polepole" | says it will slow down; every later sentence is followed by `RECEPTIONIST_SLOW_PAUSE_MS` of silence |
| "Goodbye", "End the call", "Bye", "That's all, thank you", "Weeraba", "Kwaheri" | if the AI has answered something on this call, first asks *"Before you go, was this call helpful to you?"*, stores the answer (helped / partly / didn't help / unclear, with what was said), says *"Thank you for your feedback. Goodbye."* and ends the call; otherwise says the closing line, waits until it has played, then ends the call (`end_reason=caller_voice_hangup`). Silence after the review question for `RECEPTIONIST_REVIEW_ANSWER_TIMEOUT_S` is recorded as no answer and the call ends with the usual goodbye; a new question instead is answered, and the review is not asked again |
| nothing, for `RECEPTIONIST_IDLE_REPROMPT_S` | asks "Are you still there?"; after `RECEPTIONIST_IDLE_REPROMPTS` such checks, says goodbye and ends the call (`caller_idle`) |
| "Yes", "Tell me more", "Weeyongere", "Endelea" after *"Would you like more detail?"* | reads the rest of the answer, three sentences at a time |
| "No", "That's all" after either question | *"No problem. What else can I help you with?"* |

### When a call reaches an officer

The AI answers every call; officers get only the calls it hands over. On
LiveKit (production) the AI joins the call's room as the agent participant; on
the local stack the same pipeline runs over the call socket. Either way the
caller hears the AI first.

| Trigger | What happens | `transfer_reason` |
|---|---|---|
| The caller asks for a person, however it is put: "Can I speak with an agent?", "Put me through to someone", or just "Officer" / "Agent, please" (Luganda *omukozi*, Swahili *afisa*); or taps **Talk to an officer** | Transferred at once | `caller_requested` |
| …and **no officer is signed in** (presence; a lookup error counts as signed in) | No hold for nobody: *"No officer is available to take your call right now, so an officer will call you back…"*, the reference on screen, the ticket kept for the officers. The screen never shows a wait | callback `no_officer_online` |
| …**outside the officers' working hours** (`CXP_*`, default 07:00–23:00 EAT) with nobody signed in | *"Our officers are off duty at this hour, so an officer will call you back when they are next on duty…"*; the hours and the reference on screen (times are never spoken). An officer working late still takes the call | callback `outside_working_hours` |
| A dispute, objection, appeal or legal question | Transferred at once | `tax_dispute` (or the chat's reason) |
| Nothing in the knowledge base, or the lookup fails or takes over 25 s | Transferred at once | `no_knowledge_match`, `system_error`, `timeout` |
| Clarification fails `RECEPTIONIST_MAX_CLARIFY_ATTEMPTS` times | Transferred | `clarification_failed` |
| A weakly grounded answer — the chat's response judge rates it `low`, or the result's own confidence is 0.35–0.50 — a soft abstain, or the first answer after the risk monitor marks the call **at risk** (distress, repeated question…). How clearly the caller was *heard* is not part of this (the clarify gate and the risk monitor handle that; G181) | Answered, then *"Would you like to speak to an officer about this?"*: yes transfers, no keeps the AI on the call, anything else is a new question and the offer lapses. An at-risk call is offered once | `offer_accepted` |
| The caller speaks of ending their life (English, *okwetta*, *kujiua*) | Before anything else, and never read as a goodbye: the crisis lines in full, with the emergency numbers in words, the counselling line on screen (0800 21 21 21), then *"Would you like me to connect you to a URA officer as well?"*. On yes, or at once if they also asked for a person, an **urgent** transfer; on no, the numbers stay on screen and the AI stays with them. The ticket carries no crisis text | `safety_concern` |
| The same, said **while waiting for an officer** | The crisis lines at once (numbers on screen) and *"I've asked for an officer to come to you first. Please stay on the line."* The call and its ticket go to **urgent**; the lobby gets `call.priority_raised`, which puts the call first and brings back a dismissed alert without touching its wait or an officer's claim, and the call's own channel gets `priority`, so the workspace of whoever takes it shows *"Safety concern: crisis lines given…"*. Before G175 the line was recorded and nothing was said | `safety_concern` |

Nobody takes a call the AI is still handling: the claim API answers 403 and
the console offers no **Take over**. Only officers (`ura_staff`) work calls at
all; administrators audit the service and are refused every call-desk action
(since 2026-10-13, `docs/plans/admin-service-quality-audit-plan.md`). Every AI question is the only
question in its turn, so "yes" can only mean one thing. A "yes" or "okay" said
over an answer, before its question was heard, is a backchannel: the question
is asked again, and nothing is acted on. An at-risk caller who talked over the
officer offer and moved on is offered again with the next answer.

A second request for a person while the call is already waiting, or while an officer
has it, changes nothing: one ticket, one alert, one countdown (G178). The
spoken-confirmation step behind `FLAG_VOICE_AGENTIC_WORKFLOWS` tells the caller an
action is done only when the action's executor reported success; otherwise it says it
could not be completed on the call and offers an officer (G177).

### Step 4: Transfer to Human Officer
1. Caller says: *"I want to talk to an officer."* (or clicks **Talk to an officer**).
2. AI announces: *"I'm connecting you to a URA officer, please hold."* If it has answered
   something on this call, it asks instead: *"I'm connecting you to a URA officer now. While
   you wait, were my answers helpful so far?"* — the transfer is already under way. An answer
   before the officer joins is stored (`call_ai_review_transfer`) and thanked (*"Thank you.
   Please stay on the line; an officer will be with you shortly."*); the officer joining first,
   or the caller hanging up, records no answer. Never asked on a crisis, safety or failure transfer.
3. Status changes to **Transferring**, with the reference (`TIC-` and the ticket's first 8 characters) on the caller's screen.
4. Staff on `/calls` receives an incoming transfer banner with a pulsing alert pill: the
   topic and priority come from the handoff packet ("My account balance" → *Account
   question*, high), and the queue row shows **Waiting for officer**. The brain goes
   through `receptionist/transfer.py`, which also stores `topic`, `priority` and
   `transfer_requested_at` on the call.
5. Every `RECEPTIONIST_HOLD_UPDATE_S` (30 s) the caller hears *"Thank you for holding. An
   officer will be with you shortly."*, unless an officer has taken the call and is joining.
   If nobody takes it within `RECEPTIONIST_TRANSFER_TIMEOUT_S` (90 s), the caller hears
   *"All our officers are busy, so an officer will call you back. Meanwhile, I can still
   help with other questions."*, their reference appears on screen as `TIC-XXXXXXXX` (never
   read out: the voice cannot say a ticket id), the call returns to the AI, and it is
   marked as owed a callback (`needs_callback`, reason `no_officer_available`; lobby
   `call.transfer_timed_out`) — a **Callback** chip on its row. An officer who takes the
   call at the last moment is waited for; the caller is not told everyone is busy while
   an officer is joining. Their **place in the queue** shows on screen from the start
   (*"Place in queue: 2"*; a `queue` message, never a transcript line), and once they are
   first they hear *"You're next in line."* with a hold line — said once. A caller who would
   rather not wait taps **Call me back instead**, or says so ("call me back", Luganda
   *munkubire essimu*, Swahili *nipigie simu*, Runyankole *onteerere esimu*, Acholi *goy cim
   bota*): the wait ends as a callback (`caller_requested_callback`) and the AI stays on the
   line. Replay check: `scripts/replay_call_audio.py --only 11_officer`
   (about 2 minutes).
   The same countdown runs for **every** wait, not only the AI's transfer: when an officer
   passes the call on, or their audio drops and does not come back within
   `RECEPTIONIST_OFFICER_RECONNECT_GRACE_S`, the call goes back in the queue with its topic
   and priority, and the caller gets the same hold lines and, in the end, the callback.
   Each wait has its own countdown (`CallState.queue_epoch`): an officer joining stops it,
   and an old one can never time out a newer wait (G179, G192).
6. With `FLAG_TICKET_QUEUE` off nothing is promised: the caller is told to call the
   toll-free line, and the number appears on screen.

### Step 5: Officer Takeover & Live Audio Bridge (the Call Desk)
1. On **any** staff page the officer gets a transfer alert (bottom right): wait ring,
   topic, language, reason, ticket — plus a chime (if **Sound on**), the tab title
   `(1) Caller waiting — …`, and a desktop notification when the tab is hidden. The call
   bar at the top shows Live / Waiting counts. Run **Check mic** once per shift.
2. **Preview** opens `/calls?call=…`: the AI brief (why a person is needed, what was asked,
   what the AI already said, what is open, details given, mood, a suggested first line) —
   every line links (`↗7`) to the transcript turn it came from. The brief rebuilds every
   `RECEPTIONIST_BRIEF_EVERY_TURNS` caller turns and on transfer.
3. **Take call** (or `A`) claims it — first officer wins; a second gets *"Taken by Officer
   …"*. The claim holds the caller for `RECEPTIONIST_CLAIM_TIMEOUT_S`, the microphone is
   asked for, then the audio joins: the caller's player is cleared and they hear *"You're
   now connected to Officer {Name}."* in the call's language. Officers take the calls the
   AI hands over; a call the AI is still handling cannot be taken.
4. The AI goes silent: caller audio goes to the officer only, and anything the AI was
   still saying is dropped. Officer audio streams directly to the caller socket.
5. `EnergyVAD` segments officer speech, transcribes turns, and records them in the live transcript.
6. The call follows the officer: open a ticket mid-call and a dock at the bottom of every
   staff page keeps the timer, **Mute** (`M`), **End** (`E`) and **Back to call**. Leaving
   the page asks first.
7. **Hold** tells the caller *"Please hold."* in the call's language (their screen also plays
   a tone), then *"Thank you for holding. Your officer will be back with you shortly."* every
   `RECEPTIONIST_HOLD_UPDATE_S` until the officer resumes — which says nothing: the officer
   speaks.

### Step 6: Hang-up & Performance Evaluation
1. Either side ends it: the officer's **End call** plays *"Thank you for calling URA.
   Goodbye."* and then hangs the caller up; the caller can hang up at any time. Before
   ending, the officer asks *"Were you satisfied with the help I gave you today?"* (the
   End-call confirm reminds them).
2. Background task runs `summary.py`:
   Generates a redacted structured JSON summary with subject, key facts, and resolution.
3. Performance metrics are recorded in `voice_calls`:
   AI-only routing share (not confirmed resolution), turn latencies (p50/p95), ASR word confidence, barge-ins, and officer rating.
4. The officer's wrap-up records **how satisfied the caller said they were** (required:
   one of five faces, or "The caller didn't say"), stored as `call_officer_csat` and
   audited on `/admin/quality` (Officer calls). Rating the AI's handling before the
   transfer (1-5 stars and a note) is optional and offered only when the AI answered
   something.
5. The **Performance Overview** tab updates SLO gauge cards and KPI aggregates.

---

## 6. Multilingual calls (English, Luganda, Swahili)

Behind `FLAG_RECEPTIONIST_LANGUAGE_DETECTION` (needs `FLAG_VOICE_RECEPTIONIST`,
Whisper-SALT, Sunflower on vLLM, and — for a fast native voice in all three languages —
the Orpheus sidecar). No cloud API key is needed.
Taxpayers routinely select English in the chat and then speak Luganda, so a call does
not trust the selection: it opens in English and follows what the caller *says*.

```
 transport.input() → CallerAudioTap → LanguageSentinel → VAD → partials → Whisper-SALT → user agg
                                        │ (own Silero VAD,     → UraReceptionistBrain → UraSpeechTTS/Orpheus
                                        │  SALT language id,   → assistant agg → ClientEventOutlet → transport.output()
                                        │  never adds audio latency)
                                        ▼
                              LanguagePolicy → LanguageRouter
                              (lock / switch / claim + re-ask the turn / barge-in / events)
```

| Language | Hears with | Answers with | Voice (fallbacks) |
|---|---|---|---|
| English | Whisper-SALT (`en`) | `ChatModel.generate(locale="en")` → Sunflower on vLLM, Qdrant | Orpheus `salt_eng_0001` (edge-tts) |
| Swahili | Whisper-SALT (`sw`) | `ChatModel.generate(locale="sw")`, question through `normalize_swahili_tax_query` | Orpheus `waxal_swa_0006` (Spark-TTS-SALT 246, edge-tts) |
| Luganda | Whisper-SALT (`lg`) | cross-lingual bridge → Sunflower | Orpheus `salt_lug_0001` (Spark-TTS-SALT 248) |

Measured on the GPU stack on 2026-09-30, Orpheus FP8 on its own A6000: first audio
0.27–0.31 s in all three languages, faster than real time; played back through
Whisper-SALT the same lines came back with WER 0.00 (en), 0.18 (sw), 0.00 (lg).
Orpheus reads English acronyms as words ("TIN" was heard as "our team"), which is the
next thing to fix in the text sent to it.

**Spark-TTS-SALT speakers** (Sunbird's model card and SALT docs): 241 Acholi (F),
242 Ateso (F), 243 Runyankore (F), 245 Lugbara (F), 246 Swahili (M), 248 Luganda (F);
prompt `"{speaker_id}: {text}"`, temperature 0.8, top-k 50, top-p 1.0, float32. English
(Ugandan accent) is in its training data but has no documented speaker id, which is
why English falls back to edge-tts rather than Spark. `SPARK_TTS_SPEAKER_<LANG>`
overrides a mapping; keep one utterance under about 8 s, the length it was trained on.

**How the language is decided** (`receptionist/language.py`, all unit-tested):

- Every utterance is scored by `SpeechModel.identify_language` — Whisper-SALT's
  language token, one encoder pass (tens of ms). Short or unsure utterances are also
  decoded as text, for explicit requests and code-switching.
- Utterances under `RECEPTIONIST_LID_MIN_SPEECH_S` (1.5 s) never decide ("Hello",
  "Gyebale ko", "yes").
- The first content utterance with a vote of at least
  `RECEPTIONIST_LID_HYSTERESIS_CONFIDENCE` (0.70) locks the call — in whichever
  language, English included; a weaker vote leaves the call open and the next
  utterance decides.
- Locked calls switch on one vote ≥ `RECEPTIONIST_LID_SWITCH_CONFIDENCE` (0.90); on one
  ≥ 0.70 whose text has at least three words of the new language and none of the
  current one; or when `RECEPTIONIST_LID_HYSTERESIS_TURNS` (2) of the last
  `RECEPTIONIST_LID_HYSTERESIS_WINDOW` (3) content votes are for the new language at
  ≥ `RECEPTIONIST_LID_SUPPORT_CONFIDENCE` (0.50) — not "in a row": on a real caller's
  phone audio every third Luganda utterance came back English, and a strict run
  never formed.
- Code-switched Luganda ("Nsaba okumanya ku TIN yange") counts as Luganda when the
  text has two Luganda words (more than Swahili ones) and the acoustics lean Bantu —
  P(lg) ≥ 0.35, or P(lg)+P(sw) ≥ 0.5 (SALT often hears mixed Luganda as Swahili), or
  the call is already in Luganda. Sunflower handles mixed speech.
- Explicit requests switch at once: "Can we speak Luganda?", "Tuyinza okwogera
  Oluganda?", "Naomba tuongee Kiswahili", "speak English".
- The caller can pin the language from the chip on the call screen; after that only a
  spoken request moves it.

**What the caller experiences:** nothing is held — one engine answers, in the call's
current language. When a vote moves the call, the switch waits for the end of the
caller's turn (`RECEPTIONIST_TURN_TIMEOUT_S` of silence — votes are cast on every 0.4 s
VAD segment, and a long question is several). The router *claims* that turn, so the
brain drops the transcript it got in the old language instead of answering it; then
whatever is playing stops, the *whole* turn is transcribed in the new language and
answered after a filler in that language — the caller never repeats it, and hears one
answer. A switch interrupts whatever is playing (the browser flushes its player on
`{"type":"interrupt"}`).

In a Luganda or Swahili call the clarification gate only ever confirms URA acronyms
("Nsonyiwa, ogambye VAT?"), never the language's own words, and reads a loanword the
local way: "Vati" and "tiini" are VAT and TIN.

**Protocol additions on `WS /v1/calls/stream`:**

| Direction | Message | Meaning |
|---|---|---|
| client → server | `call_start.preferred_locale` | the chat's language — a hint for staff and metrics only |
| client → server | `call_start.parent_conversation_id`, `call_start.chat_session` | the chat the call was started from, and (signed out) the chat session that owns it — see *Calls started from the chat* below |
| server → client | `call_ready.language_detection`, `.languages`, `.language` | show the language chip; the opening language |
| server → client | `{"type":"language","language":"lg","source":"auto"\|"explicit"\|"override","confidence":0.93}` | the call's language is now … |
| client → server | `{"type":"request_callback"}` | while waiting: end the wait as a callback (the **Call me back instead** button) |
| server → client | `{"type":"queue","position":2}` | the waiting caller's place in the queue (same tenant, unclaimed, priority then wait) |
| client → server | `{"type":"set_language","language":"sw"}` | pin the call to a language — any of `RECEPTIONIST_LANGUAGES` (en, sw, lg, nyn, ach by default), on the WebSocket and LiveKit paths alike (`config.call_language`; the LiveKit loop once dropped nyn/ach, G182) |

**Calls started from the chat (G122).** A call started from the chat sends the chat's
conversation id. The server carries that chat's task and a short English account of it
into the call only when the caller owns the chat: their account when signed in, or the
browser's chat session when signed out. A conversation id alone carries nothing. The
account is coarse, never the taxpayer's own words: the tax topics named, the taxpayer
type, the figures mentioned, and the current task. The call starts on the chat's task,
so a short follow-up keeps it. The account is stored on the call
(`voice_calls.parent_conversation_id`, `chat_context`) and given to the officer's brief
as context, never as a turn. A Luganda question is turned into its English search query
together with the call's last two exchanges (in English) and that account, so
"kiki ekyetaagisa?" ("what is needed?") is read as a follow-up.

Staff see a language badge on the queue row and the case header, "Luganda caller" on
the handoff brief, the switch as a `Language: Luganda (detected, 93%)` note in the
transcript, and language tiles on the metrics card. Summaries are always English.

**Bring-up** (a free card for the stack, another for the voice — check `nvidia-smi`
first; the GPU overlay already sets `ORPHEUS_TTS_LANGUAGES=lg,sw,en`):

```bash
cd App && GPU_ID=4 ORPHEUS_GPU_ID=7 ORPHEUS_TTS_URL=http://orpheus-tts:8100 \
  FLAG_RECEPTIONIST_LANGUAGE_DETECTION=true docker compose \
  -f docker-compose.yml -f docker-compose.local-retrieval.yml \
  -f docker-compose.local-sunflower.yml -f docker-compose.gpu-salt.yml \
  --profile multilingual up -d --build
docker logs ura-app-orpheus-tts 2>&1 | grep "sidecar ready"
docker logs <api> 2>&1 | grep "Receptionist phrase pre-warm"   # {'en': {...}, 'sw': {...}, 'lg': {'cached': N, 'failed': 0}}
```

Orpheus runs weight-only FP8 (`ORPHEUS_QUANTIZATION=fp8`): at bf16 an RTX A6000
generates at only ~0.96× real time, which leaves no headroom; FP8 measured 234 ms to
first audio at 0.63× real time (`evals/reports/orpheus_tts_2026-09-24_*.json`).

**Replay check** (`scripts/replay_call_audio.py`, twenty scenarios): besides the
language events, each turn declares the language its answer must be in and fails if
the receptionist's own word lists place it in another — a Swahili caller answered in
English passed every other check. `12_silent_caller` says nothing after the greeting
and expects the call to end. `13`–`16` check escalation: a worried caller is offered
an officer and says yes (transferred) or no (stays with the AI); "Officer, please"
alone transfers; a caller in crisis hears the crisis lines, emergency numbers in
words, and is transferred on yes. `17_yes_over_the_answer_is_asked_again` says "Yes,
please" over the worried caller's answer, before its officer offer is heard: the
assistant stops, asks the question again and transfers no one. `18_crisis_while_waiting`
says it wants to end its life while in the queue: crisis lines at once, the call stays first.
`19_no_officer_online_is_a_callback_at_once` and `20_call_me_back_while_waiting` cover G180 and
G186. A transfer with nobody signed in is a callback at once, so the queue scenarios are marked
`staffed` and run only with `--staffed`, after marking an officer available on the server
(`presence.upsert_presence("replay_officer", status="available")`, with
`RECEPTIONIST_PRESENCE_TTL_S` raised for the run); the nobody-online ones run without it. A scenario can require text in a reply
(`expect_reply`), text said while lingering, and a status that must never come. The harness receives audio at the demo socket's pace,
twice real time, so it can start talking during a pause *between* greeting sentences;
that shows up as one barge-in on a first turn and is an artefact of the harness, not
something a caller listening at normal speed triggers.

Run the test suite against the live WebSocket endpoint:
```bash
python3 scripts/replay_call_audio.py --ws ws://127.0.0.1:8083/v1/calls/stream
```
*Validated 2026-10-01 on GPU stack (`evals/reports/call_replay_2026-10-01.json`): 17/17 passed (100%), median first-audio latency ~1,320 ms, barge-in cutoff < 1,000 ms.*
*Re-run 2026-10-16 on an isolated GPU stack with today's models (SunflowerASR-51, Sunflower-Qwen3.5-9B, Orpheus FP8): 14/18 on R1 (`evals/reports/call_replay_2026-10-16_r1_isolated.json`). Scenarios 2, 8, 13 and 14 fail the same way on the code before R1 (`…_dev_baseline_ab.json`): a Luganda answer over the 25 s cap, the synthetic Luganda officer request misheard, and the closing question voiced late (G198) — an environment that has moved since 2026-10-01, not a regression.*

**Before any demo:** the Luganda and Swahili lines in `receptionist/phrases.py` and the
call-screen strings in `lib/i18n/{lg,sw}.ts` are drafts. Have a native speaker of each
check them, and have 3–5 Luganda speakers rate the Orpheus samples blind
(`evals/orpheus_tts/listening_sheet.csv`; the speaker key is kept out of git).

**Troubleshooting**

| Symptom | Look at |
|---|---|
| Luganda answers in an English accent, or silent | `ORPHEUS_TTS_URL` unset or sidecar down (`Orpheus TTS unreachable` in api logs); `RECEPTIONIST_ALLOW_EDGE_STANDIN_LG=false` drops the English stand-in on purpose. `GET /v1/speech/health` reports `status: degraded` and `orpheus: cooldown` while the sidecar is down; Luganda then uses Spark-TTS-SALT and does not wait out the Orpheus timeout |
| Luganda TIN question comes back as VAT, or “ttiimu” / “mu ora” never hits the TIN passages | Whisper-SALT can be confident and still wrong (`ttiimu`, `timu`, `okuva mu ora` on `lg_tin.wav`). ClarifyGate asks before answering. Before Qdrant, `repair_asr_entities` rewrites those to `TIN` and `mu URA`. Bare “era” stays “and” |
| Luganda “how much VAT / vati” is answered from a passage instead of the calculator | The call tries the chat calculator on the repaired transcript before retrieval. `vati` and `okwewandiisa` open `check_vat_registration` / `calculate_vat`. A later “what about 2 million?” stays on that tool |
| An answer stops mid-word, or runs on as babble | `docker logs ura-app-orpheus-tts`: a `synth … audio_s=16.98` line is a request that hit the sidecar's cap. Text longer than `ORPHEUS_TTS_MAX_CHARS` is already split, so lower that value. Babble on short text usually comes from digit strings, which Orpheus cannot read (G84; the contact footer is no longer spoken for this reason) or from asides such as "(18%)" or "(FY2026-27)" that `clean_text_for_speech` does not yet rewrite |
| English answer read by an American voice | Orpheus is not voicing English: `ORPHEUS_TTS_LANGUAGES` lacks `en`, or the sidecar is down (`GET /v1/speech/health` → `orpheus: cooldown`), so edge-tts took the line |
| Caller heard two answers to one question after a language switch | api log should show `Dropping a turn the language router is re-asking`; if not, the router's turn claim did not reach the brain (`brain.turn_claimed` is wired in `multilingual.py`) |
| English caller moved to Luganda | api log `Language vote lg p=…` lines; raise `RECEPTIONIST_LID_HYSTERESIS_CONFIDENCE` or `RECEPTIONIST_LID_MIN_SPEECH_S` |
| First answer feels late | `latency.turn_to_audio_ms_p50` in the call metrics; Sunflower's time on the vLLM card; the filler plays after `RECEPTIONIST_FILLER_AFTER_MS` |
| Call never leaves English | `SpeechModel.identify_language` returning `whisper_salt_unavailable` — Whisper-SALT not loaded; `Language vote` lines show what each utterance voted |
| Call ended on its own after the caller went quiet | Silence handling: `RECEPTIONIST_IDLE_REPROMPT_S` after the assistant stopped it asked "Are you still there?", and after another such silence ended the call (`end_reason=caller_idle`, api log `Ending call … the caller has been silent`). Raise it, or `0` turns it off |
| A Swahili or Luganda answer arrives in English | api log `reply localization to sw collapsed the answer; serving English` (or `failed`): the translation came back short or empty, so the English answer was served. Replies are translated one paragraph at a time; a paragraph that still comes back cut short fails the whole reply rather than shipping a fragment |
| Caller cannot talk over the assistant | api log `Caller talking over the assistant` (the sentinel's VAD stopped it); if absent, the caller's speech never reached the server over the playback: try headphones (the browser's echo canceller can mute a caller while the speaker plays) |
| Assistant cuts itself off mid-sentence | its own voice leaking from speaker to mic: `RECEPTIONIST_LOCAL_BARGE_IN=false`, or raise `RECEPTIONIST_BARGE_IN_MIN_S` |
| Stale or unjoined call stuck in queue | In `/calls`, an officer (`ura_staff`; administrators are refused desk actions) can click **Terminate call** (`POST /v1/admin/calls/{call_id}/end`). It ends the call through `CallRoom.end()` — countdown cancelled, media room closed, pipeline stopped, row `ended` — releases queue locks (`end_reason="officer_terminated_waiting"` or `officer_terminated_stale`) and refreshes the live lobby board. Before G176 it answered 500 on a call whose officer had dropped off, and skipped that teardown |
| No voice/speech heard in browser, or `The AudioContext encountered an error` in console | Fixed in G249 (`PR #650`): `PCMPlayer` (`App/frontend/src/services/pcmPlayer.ts`) guards initialization with a singleton `initPromise` and `pendingChunks` FIFO queue, expands `pcm-player-worklet.js` to a 60s circular buffer (`16000 * 60`), falls back to an inline `Blob` `AudioWorklet` (and scheduled `AudioBufferSourceNode` queue) when external worklet scripts return `text/html` behind ngrok/proxies (`blob:` permitted in `script-src`/`worker-src` in `next.config.mjs`), and `useCall.ts` separates `dialToneCtxRef` from `audioCtxRef` (`getToneContext()`) |
| Multi-turn call conversation stalls after greeting/Turn 1 or repeats *"Excuse me, did you say vat?"* | Fixed in G249 (`PR #650`): `UraSpeechTTS` (`tts.py`) enables `push_start_frame=True` and `push_stop_frames=True` so `TTSStoppedFrame` immediately triggers `BotStoppedSpeakingFrame` without the 3.0s `BOT_VAD_STOP_FALLBACK_SECS` window; `ClarifyGate.assess` (`clarify.py`) skips exact lexicon matches (`cand_term == clean_target.lower()`) and context-repaired homophones; and `UraReceptionistBrain` (`brain.py`) supersedes open clarification when the caller asks a complete new question and strips written-chat follow-up footers in `_spoken_answer` |

# Plan: Luganda + Swahili receptionist with automatic language detection

Status: **implemented on `feat/multilingual-receptionist`, behind
`FLAG_RECEPTIONIST_LANGUAGE_DETECTION` (default off).** Written 2026-09-24; the
decision log (§12) and the as-built notes (§13) record what was measured and
where the build departed from the plan. Operator guide:
`docs/runbooks/voice-receptionist-demo.md` §6.

---

## 1. Goal

The receptionist answers in English, Luganda and Swahili, with a native-sounding
voice in each, and follows the language the caller actually speaks, not the one
they selected in the UI. Taxpayers routinely select English and then speak Luganda.

### Required behaviour

1. **Every call starts in English**, with the greeting: *"Hi, thanks for
   contacting URA. I'm your assistant today. Ask your question in your preferred
   language — English, Luganda or Swahili — and I'll give you the answer in that
   language. How can I help you today?"* (the plan left naming the languages
   optional; they are named — §12).
2. **The first real question decides the language**, and that same question is
   answered in it — the caller never repeats it.
3. **Mid-call switches are followed**, without flip-flopping on code-switched
   Luganda full of English tax terms ("TIN", "VAT", "PAYE").
4. **Explicit requests switch immediately** ("Can we speak Luganda?", "Tuyinza
   okwogera Oluganda?", "Naomba tuongee Kiswahili").
5. **The caller can override** the detected language from the call screen.
6. **Officers always get English** summaries, and see which language the call was in.

### Latency targets (from the moment the caller stops speaking)

| Situation | Target |
|---|---|
| English / Swahili turn after the language is locked | No added latency vs today |
| First content turn (detection pending) | ≤ 300 ms added, p95 |
| Luganda turn, first audio (filler allowed) | ≤ 1.0 s p50 filler, ≤ 3.0 s p50 answer |
| Mid-call switch | New-language answer on the same turn the switch is detected |

## 2. Engines and language support (as found)

| Engine | Where | Languages |
|---|---|---|
| Gemini Live speech-to-speech | `receptionist/gemini_live.py` | English, Swahili (not Luganda) |
| Cascaded: Silero VAD → Whisper-SALT → RAG brain → TTS | `receptionist/pipeline.py` | any; Luganda via Sunflower |

Whisper-SALT transcribes en/lg/sw (read-speech WER en 1.8%, lg 14.2%, sw 6.9%).
Luganda TTS before this work: Spark-TTS-SALT ≈ 4.3 s/sentence, Sunbird cloud ≈ 7.2 s,
and an English edge voice as the last resort. Smart Turn v3 covers neither Luganda
nor Swahili.

## 3. Target architecture

```
 caller mic → transport.input() → CallerAudioTap → LanguageSentinel → EngineSwitch (ParallelPipeline)
                                                     │                 ├─ Gemini branch (en, sw) … OutputHoldGate
                                                     ▼                 └─ Cascaded branch (lg) … UraSpeechTTS → Orpheus
                                               LanguagePolicy → LanguageRouter → ClientEventOutlet → transport.output()
```

| Language | Engine | STT | Brain | Voice |
|---|---|---|---|---|
| en | Gemini Live | Gemini | RAG via `query_ura_tax_knowledge` | Gemini (`GEMINI_LIVE_VOICE`) |
| sw | Gemini Live | Gemini | same tool, retrieval in English | Gemini speaking Swahili |
| lg | Cascaded | Whisper-SALT `lg` | `ChatModel.generate(locale="lg")` → Sunflower | Orpheus-3B `salt_lug_0001` |

`RECEPTIONIST_ENGINE_BY_LANGUAGE=en:gemini_live,sw:gemini_live,lg:cascaded`.

Detection policy: greetings and short utterances (< 1.5 s) never decide; the first
content utterance locks; locked calls switch on one vote ≥ 0.90 or two in a row
≥ 0.70; P(lg) ≥ 0.35 plus Luganda words in the text counts as Luganda; explicit
requests switch at once; an on-screen choice holds for the call.

## 4. Phase 0 — measure before building

- **0A Language identification.** `evals/language_id/` (FLEURS, SALT, AfriSpeech
  accented English; 1.2 s / 3 s / full crops; 16 kHz and 8 kHz) scored by
  `scripts/eval_language_id.py` with methods A (SALT language token, one decoder
  step — `SpeechModel.identify_language`), B (unforced decode → text LID), C (A
  overruled by text when unsure), D (MMS-LID-126, CC-BY-NC, demo only), E
  (decode forced in each language, best mean log-prob). Gate: ≥ 95% per class on
  clips ≥ 1.5 s, mixed → lg ≥ 85%, en→lg false switch ≤ 3%, added p95 ≤ 150 ms;
  PARTIAL if met only ≥ 3 s; else method D; else Plan B (§8).
- **0B Orpheus Luganda voice.** `scripts/bench_orpheus_tts.py`, sidecar in
  `App/backend/orpheus_sidecar`. Gate: TTFA p50 ≤ 800 ms and naturalness ≥ 3.5.
- **0C Gemini Swahili voice.** 10 Swahili questions rated by 3–5 Swahili speakers;
  mean ≥ 3.5 keeps Swahili on Gemini, else `sw:cascaded`.

## 5. Phase 1 — Swahili on Gemini Live

Multilingual system instruction (`build_gemini_system_instruction`); RAG tool always
retrieves in English and logs in the caller's language; single-engine calls track
the language from Gemini's own transcription; the greeting is spoken "exactly".

## 6. Phase 2 — Luganda on the cascaded engine with Orpheus

Orpheus sidecar and client (`app/orpheus_tts.py`) as the first TTS tier for `lg`,
streamed into the call; no English stand-in for Luganda on a call; STT, TTS and live
partials read the language per utterance; fixed phrases pre-rendered at startup;
speech-timeout turn-taking with a two-word barge-in guard; all receptionist lines
localised (`receptionist/phrases.py`); Luganda clarification confirms acronyms only;
officer requests heard in all three languages.

## 7. Phase 3 — detection and switching

`language.py` (policy, pure), `sentinel.py`, `router.py`, `hold_gate.py`,
`multilingual.py`; flag `receptionist_language_detection`; `set_language` from the
client; `language` events to the caller and the staff hub.

## 8. Plan B (only if 0A is NO-GO)

Explicit, keyword-based choice: the greeting invites the caller to name a language;
`RECEPTIONIST_LID_METHOD=keyword` skips acoustic identification and acts on spoken
requests only; the call-screen chip becomes the primary switch.

## 9. Phase 4 — staff, summaries, metrics, frontend

English summaries (Sunflower first for Luganda calls) with `language` and
`languages_used`; `language.*` call metrics; language badge, handoff line, switch
notes and metric tiles for staff; the caller's language chip and menu.

## 10. Tests and verification

Backend: `test_receptionist_{language_policy,phrases,sentinel,router,hold_gate,engine_switch,language_metrics}.py`,
`test_speech_language_id.py`, `test_orpheus_tts.py`, and additions to the brain,
clarify, Gemini Live, serializer and summary suites. Frontend:
`__tests__/components/CallLanguage.test.tsx`. End to end:
`scripts/replay_call_audio.py` (eight scenarios over `WS /v1/calls/stream`).

## 11. Risks

| Risk | Mitigation |
|---|---|
| SALT can transcribe Luganda when told but may not *detect* it | Phase 0A measured it (§12); hysteresis; explicit requests; Plan B |
| Accented English read as Luganda | en→lg false-switch rate measured; 0.70 floor to leave English on the first vote |
| GPU memory | Orpheus on its own card (`ORPHEUS_GPU_ID`); language id reuses the loaded SALT model |
| Gemini mangles figures in Swahili | instruction to copy figures verbatim; 0C figure check; `sw:cascaded` fallback |
| Unreviewed Luganda/Swahili phrases | native-speaker review before any demo |
| Luganda audio sent to Gemini before lock | covered by the consent notice; the inactive branch gets no audio after lock |

## 12. Decision log

| Date | Gate | Result | Decision |
|---|---|---|---|
| 2026-09-24 | 0A Language ID | Per-*language* gate (≥ 95% each class) **not met by any method** on clips ≥ 1.5 s: A en 93.2 / sw 89.7 / lg 98.7%, p95 68 ms, en→lg 0%; C 95.4 / 89.7 / 99.0%; D (MMS) en 70.1% with en→lg 6.8%; B and E slower and worse. Per-*engine* (what the caller hears — Luganda vs Gemini), the runtime policy R scores en 100% / sw 97.8% / lg 97.7%, en→lg **0/368**. Mixed → lg: R 81.7% (gate 85%), MMS 90% | **GO on method A + the runtime policy (R)**, judged per engine (§13). MMS rejected: 7% of English callers switched to Luganda, and CC-BY-NC. Plan B not needed. Below gate and left to recorded clips: mixed → lg, and Swahili labelled English (harmless — same engine) |
| 2026-09-24 | 0B Orpheus Luganda voice | Latency **GO**: FP8 TTFA p50 234 ms, RTF 0.63 (bf16: 355 ms, RTF 0.96); 2 callers 265 ms / 0.69. Naturalness: **not yet rated** | Orpheus FP8 is the Luganda voice; `salt_lug_0001` until listeners pick |
| 2026-09-24 | 0C Gemini Swahili voice | **Not run** — needs Swahili raters and a live Gemini key | Plan default kept (`sw:gemini_live`); `sw:cascaded` is one env var |
| 2026-09-24 | ServiceSwitcher spike | Can host a whole `Pipeline`, but its gates filter only frames going *into* a service and *up* out of it: an inactive engine's late reply still flows downstream to the speaker | Custom `EngineGate` at both ends of each branch in a `ParallelPipeline` (§7.4 fallback); `test_receptionist_engine_switch.py` pins the property |
| 2026-09-24 | Greeting wording | — | Languages named in the greeting |

## 13. As built — findings and deviations

### What the measurements changed

- **Language id is judged by the engine it picks, not only the label.** English and
  Swahili both run on Gemini Live, which follows the caller's language by itself;
  the decision that changes what a caller hears is Luganda vs the rest. So the
  report shows both: the plan's per-language table and the engine-level one.
- **The runtime policy is what gets measured** (method R in
  `scripts/eval_language_id.py`): the SALT vote, the sentinel's text decode, and a
  fresh `LanguagePolicy`'s first-utterance verdict — the same modules a call runs.
- **The code-switching rule was wrong and too narrow**, found by the synthetic mixed
  set: it did not lift a weak `lg` vote, and SALT often hears code-switched Luganda
  as *Swahili*. It now also fires when the acoustics say "Bantu" (P(lg)+P(sw) ≥ 0.5)
  and the text has at least two Luganda words and more Luganda than Swahili words;
  Luganda interrogatives/copulas (`mmeka`, `bimeka`, `eri`…) were added to the list.
  Mixed → lg went from 66% to 82% under the runtime policy.
- **Orpheus runs FP8.** At bf16 it generates at 0.96× real time on an A6000 — no
  margin; weight-only FP8 gives 0.63× with no measurable loss in round-trip
  intelligibility (Whisper-SALT CER 10.2% vs 9.6%).
- **Latency of the vote.** Method A's vote alone is p95 68–80 ms. The runtime
  policy also decodes text on short or unsure segments (R p95 658 ms), but that runs
  beside Gemini, not in front of it: Gemini's reply is held only if it is ready
  first, for at most `RECEPTIONIST_LID_HOLD_TIMEOUT_MS` (600 ms).

### What the end-to-end replay changed

`scripts/replay_call_audio.py` plays synthetic callers (Orpheus, speakers the
receptionist never uses) into `WS /v1/calls/stream` on the full local stack. Each
fix below came from a scenario that failed:

- **Switch at the end of the caller's turn, not mid-sentence.** The sentinel votes on
  every VAD segment (0.4 s stop); a long Luganda question is several. Switching on
  the first segment re-asked half a question while the caller was still speaking.
  Votes are still cast per segment, but a switch that changes engine (or re-asks on
  the cascaded one) now waits for `RECEPTIONIST_TURN_TIMEOUT_S` of silence and is
  re-asked with the whole turn's audio. While it waits, Gemini's reply is pinned in
  the hold gate (no timeout), so nothing in the wrong language plays.
- **A switch interruption is a barrier.** The interruption that silences the engine
  being left used to race the re-ask: it could arrive after the filler and flush it,
  or bump the brain's generation and drop the answer. The router now waits (≤ 0.5 s)
  for the target engine to report that the interruption has passed, and a
  switch-interruption no longer counts as a barge-in.
- **Gemini's caller transcript goes upstream.** `GeminiLiveLLMService` pushes the
  caller's transcription *up*, so the tap after it never saw one: Gemini calls had no
  caller turns in the transcript. `GeminiCallerTap`, placed before the service,
  records them.
- **The engine being left may not act.** While a switch waits for the turn to end,
  Gemini still hears the caller. Hearing Luganda, it called `request_human_officer`
  ("caller speaking Luganda"): its spoken reply was held and discarded, but the ticket
  and the "transferring" status were real, and the Luganda answer never came. Gemini's
  tools now check `LanguageRouter.engine_may_act` and do nothing while the call is
  leaving it; the instruction also says never to request an officer over language.
- **One confident text confirmation is enough to leave Luganda.** Luganda → English
  needed two turns: the first English vote landed just under the one-vote bar. A vote
  ≥ 0.70 whose text has ≥ 3 words of the new language and none of the current one now
  switches at once.
- **A switch drops what Gemini said while held.** Returning to English, Gemini's
  caption opened with "I see you are speaking Luganda. I will…" — the start of the
  reply that had been held and discarded, left in the transcript tap's buffer. The
  switch interruption now clears it.
- **Gemini is told what our language id heard** (`caller_language` in the tool
  result): Gemini alone once answered an English caller in Swahili, and rewrote the
  greeting. The greeting is now said verbatim.
- **Hanging up leaked the call slot.** Nothing cancelled the pipeline task when the
  socket closed; the sixth call in a row got HTTP 403. `ws.py` now cancels on
  `on_client_disconnected` / `on_session_timeout`.
- **Clarification in Luganda asked about the wrong acronym.** "VAT" said the Luganda
  way is transcribed "Vati"; "did you say VATA?" came back, because VAT and VATA tied
  and the tie fell to set order. The lexicon now undoes Bantu epenthesis (final
  vowel, doubled vowels) before matching acronyms and breaks ties by name. Clarify
  also never asks about the call language's own words (Swahili "nini" is a letter
  from NIN).

**Result (2026-09-24, local stack: api + Sunflower on one A6000, Orpheus FP8 on
another; Gemini Live over the internet): 8/8 scenarios pass, on two consecutive
full runs of the code first shipped** (the table is that run; the report file now
holds the later ten-scenario run below). Milliseconds from the end of the
caller's turn; "answer" is when the reply's *final* caption arrives, i.e. after the
whole reply has been synthesised, so it overstates when the caller starts hearing it.

| Scenario | Turn | First audio | Filler | Answer caption | Language events |
|---|---|---|---|---|---|
| 1 English stays English | en TIN / en VAT | 4150 / 11562 | — / — | 18055 / 20310 | en (auto) |
| 2 Selected English, speaks Luganda | lg TIN | 1264 | 1257 | 26964 | lg (auto); KB timed out → apology + transfer |
| 3 Swahili on Gemini | sw TIN | 10054 | — | 35160 | sw (auto) |
| 4 Luganda then English | lg VAT / en TIN | 1254 / 5002 | 1252 / — | 2816 / 20301 | lg (auto) → clarify "VAT?", then en (auto) |
| 5 Code-switched Luganda stays | lg TIN / lg VAT | 1284 / — | 1277 / — | 5077 / — | lg (auto); KB escalated, no flip |
| 6 Explicit request | en TIN / "Swahili, please" | 8734 / 8642 | — / — | 21436 / 15232 | en (auto), sw (explicit) |
| 7 On-screen Luganda holds | en VAT | 1840 | 1834 | 5890 | none (pinned lg) |
| 8 Transfer from Luganda | lg VAT / lg "a person" | 1245 / 2476 | 1239 / — | 2207 / 2472 | lg (auto) → clarify "VAT?" → transferring |

Against §1's targets: the Luganda filler is ≤ 1.3 s everywhere (first audio p50
1.25 s), but the Luganda *answer* misses 3.0 s p50 — the cascaded chat path (hybrid
retrieval + Sunflower generation + claim verification) alone takes 4–13 s, and once
passed the brain's 25 s limit (scenario 2: apology, then transfer). In the run before
this one the same scenarios got full Luganda answers (scenario 4: 21 s to the final
caption; scenario 5: 9.4 s and 6.2 s). Every switch lands on the turn it was detected
on, both directions. What happens *after* the switch varies run to run with the
synthetic clip's transcript — "Vati" may be clarified ("did you say VAT?") or
answered; `lg_tin` is heard as "ttiimu", which the knowledge base escalates (and the
answer cache replays) — which is retrieval behaviour, not language logic; scenario 8
opens on `lg_vat` for that reason.

### First live test (2026-09-24, a real Luganda caller over the ngrok tunnel)

Two complaints: Gemini answered Luganda with "I can only assist in English and
Swahili", and talking over the assistant did not stop it. The api log of the calls
showed why.

- **A weak English vote locked the call in English.** The first content question
  ("Rwandakuyango na sema ya baama.", 2.4 s — Luganda the language token could not
  place) voted English at P 0.63 and locked English: the lock needed no confidence
  at all for the opening language. It now needs `RECEPTIONIST_LID_HYSTERESIS_CONFIDENCE`
  (0.70) whichever language it is.
- **Luganda never won twice in a row.** On this caller's phone audio the language
  token gave clear Luganda only P(lg) 0.52–0.78, with every second or third Luganda
  utterance voted *English* (0.71–0.92). "Two in a row ≥ 0.70" never formed, and each
  English vote wiped the run. A locked call now moves when 2 of its last 3 content
  votes are for the new language at ≥ 0.50 — replayed on this caller's votes it moves
  on the third Luganda utterance; English speech never had Luganda as its top vote in
  the eval (0/368), so English callers keep their protection.
- **Gemini is now a second listener.** It hears the same audio and knows Luganda; it
  was the one saying so ("English and Swahili only"). It now calls
  `hand_over_to_luganda` instead, and the router moves the call (after the caller's
  turn) and re-asks the turn in Luganda (`LanguagePolicy.report`). Isolated — the
  sentinel unable to decide (`RECEPTIONIST_LID_MIN_SPEECH_S=99`) — 4/4 Luganda calls
  moved on Gemini's report alone, 6.8–9.9 s to the first Luganda audio: a backstop
  behind the sentinel, not a replacement. The caller's spoken "Can we go to Luganda"
  (transcribed "Ingolian") is the kind of request only Gemini can still catch.
- **Barge-in: Gemini's VAD was set to hear speech onsets poorly.** The caller talked
  over two answers for 2–3 s; the sentinel's VAD heard every word, Gemini played on —
  its start-of-speech sensitivity was `LOW`. It is now `HIGH` (Gemini Live's default,
  `GEMINI_LIVE_START_SENSITIVITY`), and the call's own VAD backs it: after
  `RECEPTIONIST_BARGE_IN_MIN_S` (0.4 s) of the caller talking over Gemini, the router
  interrupts playback, and `InterruptedReplyMute` drops the rest of the reply Gemini
  is still streaming until its end marker (Gemini's own interrupt ends the mute).
- **Over the greeting itself, the caller's speech never reached the server.** In both
  calls the first speech the sentinel heard began within ~0.3 s of the greeting
  ending — the browser's echo canceller keeps the microphone down while the
  speaker plays. Nothing server-side can hear that; headphones do, and the runbook
  says so. The greeting's wording is §1's requirement, so it is unchanged, though at
  ~12 s it is the longest stretch a caller must talk over.

Measured with the replay harness's new barge-in scenarios (the caller starts 2 s into
the greeting or into an answer; `bot_stop_ms` = caller onset → assistant silent), and
with the barge-in turn attenuated as a double-talking echo canceller would:

| Setting | Level | Gemini greeting (n) | Luganda answer (n) |
|---|---|---|---|
| Before (`LOW`, no local barge-in) | 0 dB | 907 ms (1) | 1766 ms (1) |
| Before | −12 dB | 781 ms; **missed — 4158 ms** (2) | — |
| After (`HIGH` + local barge-in) | 0 dB | 750 ms (1) | 1707 ms (1) |
| After | −12 / −20 dB | 687–1110 ms, 4/4 | 1697–1760 ms, 4/4 |
| Gemini forced `LOW`, local barge-in only | −12 dB | 835–1152 ms, 3/3 | — |

All ten scenarios pass on the fixed build (`call_replay_2026-09-24.json`).

### Bugs found on the way (pre-existing, fixed)

- **Raw PCM sniffed as MP3.** `_decode_audio_bytes` takes any buffer starting `FF Ex`
  for an MPEG frame; a quiet −1 sample is `FF FF`. 7% of the language-id clips start
  that way, and libsndfile then decodes garbage. Everything on the call path now
  hands `transcribe` a WAV (`speech_service.pcm16_to_wav`); `identify_language`
  reads PCM16 directly.
- **Gemini transfers never reached an officer.** The `request_human_officer` tool
  called the keyword-only `_maybe_create_ticket` positionally; the TypeError was
  swallowed, a fake `TICK-…` id used, and no lobby event sent. Both engines now share
  `receptionist/transfer.py`.
- **The "Talk to an officer" button did nothing on Gemini calls** (no processor
  handled `RequestOfficerFrame`); `OfficerRequestBridge` turns it into a request
  Gemini acts on.
- **`RequestOfficerFrame` had no frame id** (a hand-written `__init__` skipped the
  dataclass `__post_init__`), which a `ParallelPipeline` needs.
- **Gemini's RAG tool never logged a turn.** It passed `sources` as a list to a sqlite
  column; the binding error was swallowed. It now passes JSON, as the chat path does.

### Deviations from the plan

| Plan | Built | Why |
|---|---|---|
| `startCall()` sends `locale: "en"` | sends the chat language as `locale` and `preferred_locale` | with the flag off, `locale` is still the call's language; with it on the backend opens in English regardless |
| Keep `user_turn_stop_timeout` at 5 s | kept at 20 s | the code already had 20 s; Whisper-SALT's segmented transcript of a long Luganda turn can take seconds |
| `MinWordsUserTurnStartStrategy` as a barge-in guard | VAD opens turns only while the assistant is quiet; two words from the live partials interrupt it | with a segmented STT, min-words alone would only fire after the caller finished |
| First content utterance locks on any vote | moving off English needs ≥ 0.70 on the first vote | measured: P(lg) never exceeded 0.35 on English clips, and the floor halves Swahili→Luganda errors vs 0.5 |
| Text LID via `query.detect_language` | local word lists (`language.lexical_hits`) | `detect_language` may call Sunbird over the network inside a call turn |
| Developer message to Gemini on a switch | realtime text (`InputTextRawFrame`) | Pipecat's Gemini Live service does not forward context updates mid-session |
| `ServiceSwitcher` | `EngineGate` pairs in a `ParallelPipeline` | §12 |
| `mms` as a runtime LID method | not implemented | the runtime policy met the engine-level gate on method A |

### Open issues (measured, not fixed here)

- **Luganda answers miss the 3.0 s p50 target.** The filler holds (≤ 1.3 s), but the
  cascaded chat path takes 4–13 s (Sunflower generation up to 9 s, claim verification
  up to 2.6 s). That path is shared with text chat and was not changed by this work.
- **With the semantic cache on, a cascaded answer sometimes waits a further 4–11 s
  after `generate` logs its result**, before the brain speaks it. Not reproduced with
  `FLAG_SEMANTIC_CACHE=false` (0.7 s), and asyncio debug mode shows no blocked event
  loop, so the time is spent inside `generate`'s worker thread after its log line
  (cache write, personalization, audit). Language-independent; needs a profile.
- **Gemini Live latency varies 3–14 s to first audio** on this network, with or without
  detection, including the reply to an explicit language request (which the router
  does not interrupt or re-ask). Same image, same clips, flag off vs on
  (`call_replay_2026-09-24_flag_{off,on}.json`):

  | First audio (ms) | Flag off | Flag on |
  |---|---|---|
  | English, n = 6 | p50 7235 (2721–9299) | p50 6009 (2685–14178) |
  | Swahili, n = 3 | p50 10226 (8077–12709) | p50 9391 (4995–14541) |

  Gemini's own spread is far wider than the 300 ms budget in §1, so the comparison
  alone cannot prove that bound. The hold gate measures detection's own cost
  directly: in all six flag-on calls `language.held_ms_p95` was **0 ms** — the vote
  (p95 451–864 ms) always landed before Gemini had produced any reply (≥ 2.7 s).
- Gemini's socket once closed with 1007 ("audio content type not supported") a few
  seconds after a switch away from it; Pipecat reconnected on the first retry.

### Still needs people

- Native-speaker review of every Luganda and Swahili line (`receptionist/phrases.py`,
  `lib/i18n/{lg,sw}.ts`) — marked unreviewed in code.
- The Orpheus listening test (`evals/orpheus_tts/`) and the Gemini Swahili rating (0C).
- Recorded code-switched clips and clips through the real call path (0A).

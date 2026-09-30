# Traceability record — local-only phone receptionist (2026-09-30)

> **PR:** #519 (`feat/receptionist-local-only`, squash-merged as 2d38926388); follow-up
> fixes on `fix/voice-reply-language`.
> **Stack:** local GPU compose stack (`docker-compose.yml` + `local-retrieval.yml` +
> `local-sunflower.yml` + `gpu-salt.yml`): api on GPU 2, Sunflower-14B-FP8 on vLLM
> on GPU 5, Orpheus-3B FP8 on GPU 4; Qdrant and Redis local.
> **Replaces** an earlier version of this file whose figures were measured on a build
> that answered statutory questions from hardcoded text (see "What was removed"): those
> numbers measured the hardcoded cache, not the receptionist, and are withdrawn.

## What changed

| Area | Change | Commit |
|---|---|---|
| Engine | Gemini Live decommissioned. English, Swahili and Luganda run on the local engine (Whisper-SALT → Sunflower against Qdrant → Orpheus). A language switch re-asks the caller's turn on the same engine; the router claims that turn so it is answered once. The sentinel's VAD barge-in now stops the local engine. | 5e89f4e3a8 |
| Voice | Orpheus speaks English (`salt_eng_0001`); the GPU overlay voices `lg,sw,en`. Spark-TTS-SALT (Sunbird speakers 246 sw / 248 lg; no English speaker id) and edge-tts remain fallbacks. | da8d1cb9d9 |
| Brief / summary | Local Sunflower first for every call; Gemini only when vLLM is down. | c3ac42bc59 |
| Swahili | ASR entity repairs (NIN, PRN, EFRIS, WHT), Swahili tax-term normalisation for retrieval, Swahili call metrics. | 08d6398db9, 9e819ee181 |
| G64 | Replies translated one paragraph at a time; a truncated paragraph serves English, never a fragment. | 91fa2c79f4 |
| G77 | A silent caller is checked on after `RECEPTIONIST_IDLE_REPROMPT_S` (12 s), then the call ends (`caller_idle`); re-checked before hanging up. | db1fa46791, b7ceec1e7c |
| G78 | Spoken answers keep a numbered procedure whole; a URL is dropped with its "at". | (in 07ac5c99e1), f491060354 |
| G79 | Production gate: speech, LLM and an Orpheus voice for every call language. | (in 07ac5c99e1) |
| In-call requests | "Repeat that", "speak slower" (a `RECEPTIONIST_SLOW_PAUSE_MS` pause after each sentence — the local voices have no speed control), "goodbye" (heard before the call ends). Short requests only (≤ 8 words). | 646596ccfb, bd59e83cbf |
| Web voice | `/v1/voice/chat` and the voice WebSocket translate through `generate()` (Swahili included) and speak at most three sentences; the browser stops the playing clip on interrupt. | f55eeca728 |
| Privacy | Caller words masked in the language-vote log; the brain no longer logs the conversation at INFO. | b7ceec1e7c |
| Voice language (G85) | A reply is voiced in the language it is written in (`reply_locale`): an English fallback after a failed translation no longer gets the Luganda or Swahili voice, on `/v1/voice/chat` or the voice socket. | fd840d6613 |
| Voice socket (G86) | Only list markers are read as steps; "reference number is 123." keeps its 123. | fd840d6613 |
| Orpheus length (G82) | Text over `ORPHEUS_TTS_MAX_CHARS` (120) is voiced in pieces, so nothing is cut off at the sidecar's 16.98 s cap. | fbafc78518 |
| Spoken asides (G83) | The calculator's "18% (18%)", "(VAT / omusolo gwa VAT / …)" and "(FY2026-27)" are spoken once and plainly; the VAT answer no longer runs on as babble. | bfaf2973ce |
| Contact footer (G84, partly) | Not spoken: Orpheus loops on its phone numbers in every spelling tried. The written answer keeps it. | a1b797296b |
| Harness | A stress probe needs its figure as a standalone number, and each token must start a word (synonyms allowed). The voice stage needs half its keywords, not just any reply. The voice-socket check waits for the whole spoken reply. | 5ed319f323, b48aa4217d, 54f10f5aa8 |

## What was removed (owner decision, 2026-09-30)

- **Hardcoded "canonical" tax answers** (call brain, voice WebSocket, `/v1/voice/chat`):
  fixed text with an invented source and `faithfulness_score: 1.0`, bypassing retrieval,
  the rate table and claim checks. Figures now come from the knowledge base and the rate
  table (`late_payment_interest_monthly_rate` = 0.02, TPCA s.39).
- **0.35 s end-of-turn heuristic** (`AdaptiveSpeechTimeoutStopStrategy`): it closed the
  caller's turn early on any "complete-looking" transcript and cut off callers who pause
  mid-sentence. Synthetic replay callers never pause, so replays could not show it.
- **Speculative prefetch / PCM cache / per-sentence text frames** from an earlier
  uncommitted attempt: prefetch ran the full `generate()` (tickets, workflow starts) on
  half-heard partials; per-sentence frames without spaces merged sentences in the TTS
  aggregator.

## Verification

Measured on the local GPU stack with the follow-up build (`fix/voice-reply-language` at b48aa4217d), 2026-09-30.

| Check | Result |
|---|---|
| Backend suite (in the `app-api:gpu` image) | 2480 passed. The failing set is identical to `dev` (50 failed, 7 errors, all fixtures the image lacks); nothing new. CI's blocking ruff set is clean. |
| Call replay, 12 scenarios | 12/12 (`evals/reports/call_replay_2026-09-30_followup.json`). The English VAT answer is now spoken as "…is 18 percent, for the 2026 to 2027 financial year". |
| UX, control and accuracy suite | 7/7 (was 6/7). The voice-socket check now waits for the audio and received 4 chunks (`receptionist_ux_control_accuracy_report.json`). |
| Stress: text accuracy | 8/9 under the stricter check (figure required as a standalone number). The one miss is pre-existing, not caused by this change: the Swahili penalty question is answered with criminal fines (faithfulness 0.00) instead of 2% a month. The old check passed it by finding "2" inside "2,000,000". Recorded under G81. |
| Stress: concurrency | 24 requests at c=12: 100% success, 8.70 req/s, p95 1379 ms. |
| Stress: voice (`/v1/voice/chat`) | 3/3. Audio is now full length: English ≈ 31 s, Luganda ≈ 30 s, Swahili ≈ 22 s. All three were exactly 16.98 s (the cap) before. |
| Orpheus clips since the deploy | 48 checked: none at the 16.98 s cap, and no runaway (more than 0.2 s of audio per character). The VAT sentence is 7.9 s and transcribes cleanly; it was 17.0 s of babble. |

## Open

- **G80** — Orpheus reading of spelled acronyms in Luganda needs a native listener.
- **G84** — a number inside an answer (the toll-free line when asked for, a TIN read back) is still
  at risk: Orpheus loops on digit strings.
- **G81** — web chat's Swahili retrieval translation has no tax-term normalisation.
- Luganda answers still take several seconds (three Sunflower calls in the bridge); the
  filler covers the first second.
- Human-only: native-speaker review of the Luganda and Swahili lines in `phrases.py`.
- The replay harness receives audio at the demo socket's pace (2× real time), so it can
  start talking in a pause between greeting sentences — one barge-in on a first turn is
  a harness artefact, not caller behaviour.

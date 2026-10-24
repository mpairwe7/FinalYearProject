# ADR-0002: 5-Locale (`en`, `lg`, `sw`, `nyn`, `ach`) Multimodal Speech/RAG Parity & CXP Operating Hours (`07:00–23:00 EAT`) SLA Calendar

- **Status**: Accepted (Amended 2026-10-25 — PR #649, PR #650, PR #657, PR #658)
- **Date**: 2026-10-17 (Updated 2026-10-25)
- **Deciders**: URA Capstone Architecture & CX Engineering Team
- **Technical Story**: Issue #441, Gaps G174, G180, G186, G189, G195–G199, G249, G253 (`docs/plans/receptionist-cx-audit-2026-10-plan.md`, `docs/Reports/MULTILINGUAL_50000_CX_MULTIMODAL_NGROK_REPORT_2026-10-24.md`)

## Context and Problem Statement

Uganda Revenue Authority taxpayers interact across text chat, voice narration, and the live WebSocket/LiveKit call receptionist in five primary languages: **English (`en`)**, **Luganda (`lg`)**, **Kiswahili (`sw`)**, **Runyankole (`nyn`)**, and **Acholi (`ach`)**. Furthermore, while the AI assistant operates 24/7, live URA Contact Experience (CXP) officers work `07:00–23:00 EAT` (`Africa/Kampala`). Previously, escalations after `23:00 EAT` promised immediate live officer handoffs, placed voice callers on a 90-second hold queue when nobody was on duty, and counted overnight closed hours (`23:00–07:00 EAT`) against officer SLA breach timers.

## Decision Drivers

- **5-Locale End-to-End Parity**: Every taxpayer surface — UI dictionary (`App/frontend/src/lib/i18n/`), English-pivot RAG (`TraSe`), figure-guarded machine translation (`mt.py`), neural TTS (`Sunbird/orpheus-3b-tts-multilingual`), neural ASR (`Sunbird/SunflowerASR-51-african-languages`), and call receptionist phrases (`receptionist/phrases.py`) — must support `en`, `lg`, `sw`, `nyn`, and `ach`.
- **Honest After-Hours Escalation & Callback**: Never place a voice caller on hold when no officer is online or outside `07:00–23:00 EAT`, and never tell a chat user at night that an officer is looking at their conversation right now.
- **Fair Business-Hours SLA Accounting**: Officer first-response and next-reply breach clocks must pause while CXP is closed (`23:00–07:00 EAT`).

## Decision Outcome

1. **5-Locale Multimodal Stack**:
   - `Sunbird/Sunflower-Qwen3.5-9B` (`262,144` native context window) serves English-pivot RAG generation and prompted local MT with `#NMBR...#` sentinel figure protection (`mt.protect_figures` / `mt.figures_survived`).
   - `Sunbird/orpheus-3b-tts-multilingual` serves 5-locale neural speech synthesis with catalog speaker validation (`orpheus_tts.speaker_for`), paired with `Sunbird/SunflowerASR-51-african-languages` (`whisper_salt`) for 5-locale ASR.
2. **Timezone-Aware Officer & CXP Operating Hours (`07:00–23:00 EAT`)**:
   - `app/officer_hours.py` and `app/escalation_policy.py` (`evaluate_cxp_schedule`, `business_hours_elapsed_seconds` behind `cxp_business_hours`) evaluate the `Africa/Kampala` `07:00–23:00 EAT` window with active officer presence override (`count_active_officers() > 0`).
   - Outside working hours (when no officer is online), `POST /v1/escalate`, `EscalateToHumanTool`, auto-escalated chat turns, and `UraReceptionistBrain._transfer` queue the ticket/callback immediately (`after_hours_cxp_closed` / `outside_working_hours`), disclose CXP hours and the next shift in all 5 locales, and pause SLA breach clocks until `07:00 EAT`.

## Consequences

- **Positive**: 100% pass rate across the **40,000-scenario 5-locale (`en`, `lg`, `sw`, `nyn`, `ach`) multimodal (`TTT`, `STT`, `TTS`) benchmark** and **166-probe live ngrok stress/load suite** (`docs/Reports/MULTILINGUAL_40000_CX_MULTIMODAL_NGROK_REPORT_2026-10-17.md`).
- **Negative**: Time-of-day-sensitive integration tests must pin `CXP_OPEN_HOUR` / `CXP_CLOSE_HOUR` (or `CXP_FORCE_AFTER_HOURS`) so assertions remain deterministic regardless of when CI runs.

## Confirmation

Verified by:
- `PYTHONPATH=App/backend python3 -m pytest App/backend/tests/test_officer_hours.py App/backend/tests/test_ticket_ops_sla.py App/backend/tests/test_receptionist_brain.py App/backend/tests/test_receptionist_clarify.py tests/agents/test_agentic_voice_2026_enhancements.py tests/agents/test_cx_50000_multimodal_ngrok.py tests/test_answer_integrity_integration.py -q`
- `scripts/benchmark_40000_cx_multimodal_ngrok.py` and `scripts/benchmark_50000_cx_multimodal_ngrok.py`

## Amendment 1 (2026-10-24 — PR #649 & PR #650, Gap G249)

1. **50,000-Scenario 5-Locale Multimodal (`TTT`, `STT`, `TTS`) & 173-Probe Live Ngrok Benchmark (`PR #649`)**:
   - Expanded the 5-locale (`en`, `lg`, `sw`, `nyn`, `ach`) evaluation matrix to **50,000 scenarios** (`10,000` per locale; `25,000 TTT`, `15,000 STT`, `10,000 TTS`) including Phase 42 Portal Task Execution & Stuck-User DOM Co-Pilot (`ADR-0014`), achieving `50,000 / 50,000` (`100.0%`, `2,178.8 scenarios/s`) and `173 / 173` (`100.0%`) live ngrok probes (`docs/Reports/MULTILINGUAL_50000_CX_MULTIMODAL_NGROK_REPORT_2026-10-24.md`).
   - Replaced dynamic chunk lazy-loading of `CallScreen` in `App/frontend/src/app/page.tsx` with a static import and explicit voice-recording consent disclosure (`call.consentTitle`, `call.consentText`) across `/` and `/portal/[portalKey]`.
2. **Call Receptionist Browser Audio Pipeline & Multi-Turn Conversation Continuity (`PR #650`, `G249`)**:
   - **Browser Audio Playback (`PCMPlayer` & `pcm-player-worklet.js`)**: Added a singleton `initPromise` guard, `pendingChunks` FIFO queue, non-blocking `void this.ctx.resume()`, inline `Blob` `AudioWorklet` fallback (`blob:` allowed in `script-src`/`worker-src` CSP), scheduled `AudioBufferSourceNode` fallback queue, 60-second circular worklet buffer (`16000 * 60`), and dedicated `dialToneCtxRef` vs. lazy `getToneContext()` (`useCall.ts`).
   - **Pipecat 1.12 TTS Turn Lifecycle (`UraSpeechTTS`)**: Enabled `push_start_frame=True` and `push_stop_frames=True` so `TTSStartedFrame` and `TTSStoppedFrame` are emitted on every turn, flushing trailing buffered audio and firing `BotStoppedSpeakingFrame` immediately instead of holding `_bot_speaking=True` for `BOT_VAD_STOP_FALLBACK_SECS = 3.0s`.
   - **Multi-Turn Clarification & Spoken Answer Hygiene (`ClarifyGate`, `UraReceptionistBrain`, `lexicon.py`)**: Skipped exact lexicon matches (`cand_term == clean_target.lower()`) and context-repaired English homophones (`standard vet rate -> standard VAT rate`, `for a teen -> for a TIN`) while continuing to assess remaining low-confidence words; superseded open clarification state when a caller asks a complete new question on the next turn (even when the question contains `"no"` mid-sentence, e.g., *"How do I register if I have no TIN?"*); and stripped written-chat follow-up footers (`You might also want to know:...`, `If you get stuck at any step...`) in `_spoken_answer`.

## Amendment 2 (2026-10-25 — PR #657 & PR #658, Gap G253)

1. **Multi-Turn Detail Retention & Bidirectional Pace Control (`brain.py`, `state.py`, `phrases.py`)**:
   - Added `CallState.last_more_detail` so truncated statutory sentences survive intervening repeat or pace-adjustment turns when the caller subsequently asks *"tell me more"* / *"more detail"*, while preserving the invariant that an active officer offer (`offer_officer=True`) treats *"tell me more"* as a new question.
   - Added 5-locale (`en`, `lg`, `sw`, `nyn`, `ach`) normal-pace voice patterns (`_SPEED_NORMAL_PATTERNS`) and `normal_ack` confirmations so callers can restore regular speaking speed (`speech_rate_slow = False`) by voice or screen control.
2. **Multimodal On-Screen Controls, EN 301 549 v4.1.1 Clause 6.2 Real-Time Text (RTT) & WCAG 2.2 AA (`serializer.py`, `ws.py`, `brain.py`, `clarify.py`, `lexicon.py`, `CallScreen.tsx`, `useCall.ts`, `useCallStore.ts`, `callSocket.ts`)**:
   - Added `CallControlFrame` (`repeat`, `slower`, `normal_speed`, `more_detail`, `accept_offer`, `decline_offer`) and `CallTextTurnFrame` (`text_turn`, up to 500 chars) across `BrowserCallSerializer`, LiveKit `_forward_control`, and the non-Pipecat fallback loop in `ws.py`, with a per-connection sliding-window rate guard (`_allow_control_frame`, 15 frames / 10 s) and immediate cancellation of in-flight LLM generation on barge-in (`_cancel_active_generation`).
   - Added `OutputTransportMessageUrgentFrame` `call_state` broadcasting (`phase: "listening" | "thinking" | "speaking"`, `pending_offer` covering `pending_offer`, `ClarifyState` in `"term"`/`"confirm"`, `pending_confirm_challenge`, and `review_state == "asking_end"`), Stage 2 (`repeat`) multi-word tail-token resolution + Stage 3 (`confirm`) explicit negative inline term replacement (`"No, export"`) in `ClarifyGate.resolve`, and `edit_sim` preservation in `lexicon.py` when `jellyfish` is absent.
   - Added the 5-locale quick-control toolbar, inline **Type on call** form (with `boolean` send acknowledgement, auto-focus, and form-level `Escape` dismissal), `role="status" aria-live="polite"` phase announcements, `:focus-visible` keyboard focus rings, and decorative section-emoji stripping in `speech_normalization.py`.

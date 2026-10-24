# ADR-0016: Chat Voice Mode — Transcript-First Turns over the Text Stream, and a Live-Feedback Dock

- **Date:** 2026-10-24
- **Owner:** Cloake Tech Limited — Frontend & Voice UX
- **Changes:** how the web chat's voice mode sends a turn. `POST /v1/voice/chat` itself is unchanged and stays for single-request clients.

## Status

Accepted

## Context and Problem Statement

The owner reported that voice mode gave no feedback during a conversation. Each point below was reproduced on `dev` at `ede0c8756c`.

1. **Entering voice mode did nothing visible.** The toggle changed the composer's placeholder and drew a ring on itself. The microphone still needed a second tap, on the dictation button, whose tooltip says "Dictate".
2. **A spoken turn was one silent request.** The recording went to `/v1/voice/chat`, which runs speech recognition and the whole, non-streamed generation before it answers. The transcript and the reply arrived together 2–20 s later, and nothing on screen moved in between. On the landing page nothing showed at all, because the pending bubble only rendered inside a conversation that already existed. Stop could not reach the request.
3. **A turn sent itself without warning**, after a 1.2 s pause.
4. **Nothing said whose turn it was.** While the reply was read, the only sign was the narration bar.
5. **Narration never read a typed question's answer.** The rule fired when a turn was *added*, and a streamed reply's turn is added empty, before the answer arrives.

Voice inside a chat now works the same way across the assistants people use. ChatGPT's voice runs in the chat with a live transcript and streamed text. Gemini Live's 2026 redesign moved it into a bar at the bottom of the conversation. LiveKit's Agents UI drives its visualizers from listening, thinking and speaking states. The common pattern has four parts:

- one labelled state at a time ("animation without state labels confuses users");
- a visualizer that follows the live audio;
- the transcript in the conversation as soon as it is known, and the answer streamed;
- a single control that always takes the floor back.

The relevant WCAG 2.2 criteria are 4.1.3 Status Messages, 1.4.2 Audio Control, 1.4.1 Use of Color, 2.5.8 Target Size and 2.3.3 Animation from Interactions.

## Decision Outcome

1. **Transcribe first, then answer over the text stream.** A finished recording is posted to `POST /v1/asr?domain=tax`, which applies the same `repair_asr_entities` that `/v1/voice/chat` uses. What it heard becomes the taxpayer's turn at once, flagged `spoken` so the bubble says "Spoken". It is then sent through `sendMessage` to `POST /v1/chat/stream`, with `locale` set to the language heard and `locale_explicit: true`, which is what `/v1/voice/chat` did. Once the answer has finished arriving it is read aloud, and with auto-send on the mic opens again.
   - *Rejected: pending UI only, keeping `/v1/voice/chat`.* The transcript would still arrive with the reply, so the main gap would remain.
   - *Rejected: a new SSE variant of `/v1/voice/chat`.* It would keep one request and the `voice` metrics channel. But it adds a route and a second consumer of the chat stream inside a 2,400-line page, duplicating phase, reveal, Stop, citation, connector and fallback handling.
   - *Chosen: compose the two existing endpoints.* The voice turn reuses the whole text path unchanged.
2. **A dock in the composer's place (`VoiceModeDock`).** While voice mode is on, the dock replaces the composer.
   - It names one phase at a time: opening, listening, hearing you, sending when you stop, got it, the answer's current step, getting ready to speak, speaking, paused, tap to talk.
   - It draws the call's orb. `CallOrb` is now shared, with an optional `state` and a `readLevels` reader, and its styles moved to `orb.css`, which the component imports itself. The orb is teal while the taxpayer has the floor, navy while the assistant does, and carries a gold arc while the answer is worked on.
   - It says back what was heard, then shows the sentence being read.
   - It has one primary control whose job follows the phase (send now, stop, interrupt and talk, start talking), plus mic-off and End.
3. **An end-of-turn countdown.** `EndOfTurnDetector.silenceProgress` reports how far the current pause has gone towards ending the turn. The dock draws it as a ring that closes round the send button and opens again if the taxpayer goes on talking.
4. **Entering opens the mic, leaving lets go.** Entering voice mode opens the microphone straight away and moves focus into the dock. End, or Escape when nothing else is running, stops listening and reading and drops a question not yet sent. An answer already being written carries on as text. Opening a call, or an officer taking the chat, also ends voice mode. Voice mode is disabled without `MediaRecorder`, which it needs.
5. **Narration reads a reply once it has finished arriving.** Each reply is read once, and only if it arrived after narration was switched on, so opening an old conversation does not read it out. A reply the reader stopped is never read.
6. **Live levels stay outside React.**
   - The mic level comes from the speech-band dBFS that the end-of-turn watcher already measures (`onLevel`).
   - The reply's level comes from an analyser on the playback path (`readPlaybackLevel`).
   - Both, and the countdown, reach the dock through the level bus, so none of them re-render the conversation.
7. **Announcements wait for the mic to close.** State changes are announced politely (`role="status"`), but never while the microphone is open, where a screen reader's voice would be recorded into the turn; the mic's own tones say it opened. The steps of an answer are one announcement, not four.

## Consequences

- **Positive:**
  - The taxpayer sees their words as soon as the recogniser returns them, instead of after the whole reply has been generated.
  - The answer streams with its steps, and Stop works for a voice turn.
  - The landing page gives way to the conversation as soon as the turn is sent.
  - Each state is said in words in five languages, and the orb, the card's edge and the label all show whose turn it is.
  - Narration now reads typed questions' answers too.
- **Negative / Trade-offs:**
  - A voice turn is two requests (`/v1/asr`, then `/v1/chat/stream`) instead of one: one more round trip, and one more call against the per-route rate limit (30/min each).
  - Chat voice turns are counted on the stream's metrics channel (`sse`), not `voice`. `speech_voice_chat_latency_s` no longer sees them, though `speech_asr_*` still does. An escalation raised from a voice turn carries the web case channel, not `voice_ws`.
  - The vernacular lexicon normalisation that `/v1/voice/chat` applied to the whole message now applies where the text path applies it, in the English retrieval query (`english_retrieval_query`), exactly as for a typed Luganda, Swahili, Runyankole or Acholi turn.
  - The new lg/sw/nyn/ach strings are drafts awaiting native-speaker review, like the rest of those dictionaries.
- **Found on the way (G258):** on a phone the fixed dock passes taps through (`pointer-events: none`) and only the composer took them back, so the reading bar's buttons, and at first the dock's, were dead. Both now take taps back.
- **Still open:** barge-in by voice while a reply is read (G256), speaking an answer while it streams (G257), the chat's speech repair missing VAT homophones (G259, found live), and the confidence-gated check before sending (G203).
- **Live check:** `docs/runbooks/chat-voice-mode-live-check.md` (`scripts/probe_voice_mode.mjs`). On 2026-10-24, warm, what was heard was on screen 0.42 s after the turn ended and the answer was complete 1.4 s after that.

## Confirmation

Validated by:
- `App/frontend/src/__tests__/components/VoiceModeDock.test.tsx`: every phase's words and control, no announcement while the mic is open, focus, the countdown, the heard text, pause and resume, the orb's floor, and locale.
- `App/frontend/src/__tests__/components/CallOrb.test.tsx`, `services/playbackLevel.test.ts`, `services/audioLevelBus.test.ts`, `services/endOfTurn.test.ts` (the countdown, plus `onProgress` and `onLevel`), `hooks/useVoiceEscape.test.tsx`, `components/ChatInput.test.tsx`.
- `App/frontend/e2e/voice.spec.ts`, `voice.mobile.spec.ts`, `languages.spec.ts` and `voice.integration.spec.ts`, which run in `.github/workflows/voice-e2e.yml`. They cover the dock, transcript-first turns, the `/v1/asr` and `/v1/chat/stream` request contract, Stop, Escape, the self-sending turn and listen-again.
- `tests/test_monorepo_architecture_governance.py` (MADR sections and ADR registry).

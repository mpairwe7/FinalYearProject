# Chat voice and speech UX — audit and phased fixes (2026-10)

Scope: speech in the **main web chat** — dictation, voice mode, read-aloud and
narration — and the person's control while it is listening or talking. The
call receptionist has its own plan (`receptionist-cx-audit-2026-10-plan.md`).
Gap rows G200–G206 in `docs/GAPS_AND_AGENTIC_ROADMAP.md`.

## What exists (and is good)

Dictation streams interim results and appends to what was typed; voice mode
sends on a pause (`services/endOfTurn.ts`) and is hands-free both ways; opening
the mic silences a reply; a stop-generation button; honest "didn't catch that"
versus "unavailable"; per-language narration voices. The three voice overlays
(`VoiceChat`, `VoiceFirstChat`, `VoiceVisionMode`) are unmounted (no entry
point) — out of scope.

## Gaps

| Gap | Finding | Standard |
|---|---|---|
| G200 | Read-aloud was play/stop on the reply's own button only; with narration on, replies read themselves and nothing on screen paused them | WCAG 2.2 SC 1.4.2 Audio Control |
| G201 | Every voice control and voice error was English, and a failed voice turn showed the server's raw error | WCAG 2.2 SC 3.1.2 (language of the content a person reads); error identification, SC 3.3.1 |
| G202 | A read-aloud that failed was silent: analytics only | WCAG 2.2 SC 4.1.3 Status Messages |
| G203 | Voice mode answers what it heard, with no chance to correct a mishearing | — (needs the server's ASR confidence) |
| G204 | No keyboard way to stop narration or cancel a recording | WCAG 2.2 SC 2.1.1 Keyboard; `aria-keyshortcuts` |
| G205 | The Settings dialog is English in every locale | SC 3.1.2 |
| G206 | No playback speed (second-language listeners slow speech down) | — |

## Phases

### V1 — Control while it talks (this change)

`NarrationBar` (pause/resume, stop, the sentence being heard; its targets are
44 px, above SC 2.5.8's 24 px minimum), `useVoiceEscape`, the failure notice
with a retry, and the voice strings in five languages — the existing
`message.listen*` keys are used at last, plus 16 new ones. Pause suspends the
playback `AudioContext`, so the streamed sentences already queued wait with it:
no re-request, no lost place.

### V2 — Hearing right, at the listener's pace

G203: the server returns the transcript's confidence; below a threshold, voice
mode puts the transcript in the composer to check and send, instead of
answering a mishearing. G206: playback speed, pitch-preserving — that needs an
`HTMLAudioElement` player (`preservesPitch`) in place of buffer sources, which
also gives Media Session (lock-screen and headset) controls; iOS autoplay rules
make it a change to verify on devices, so it is its own phase.

### Later

G205 (the Settings dialog in five languages) with the other Settings sections.

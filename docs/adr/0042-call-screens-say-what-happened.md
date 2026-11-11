# ADR-0042: The call screens say what happened — refusals a browser can read, a time limit announced on every transport, and no microphone left open

## Status

Accepted, 2026-11-11. Phase 4 of the
[11 November call receptionist review](../plans/receptionist-assurance-2026-11-11.md);
it follows [ADR-0041](0041-receptionist-starts-safely-fails-visibly.md). It
closes G601–G606 and G608–G611 and mitigates G607.

## Context and Problem Statement

Phases 1 to 3 were about the server. This phase is about the two screens a
call runs on, the taxpayer's call dialog and the officer's console, and the
messages that reach them.

- **The microphone outlived the call** (G601). Hanging up while the browser's
  permission prompt was open, then allowing it, left the microphone recording
  with nothing to stop it; a late answer could stream into the next call; a
  late refusal put an error on the ended screen. The officer's side had the
  same fault, and its leaked recorder gave the officer's next call no audio.
- **The time limit was announced on one transport, to nobody** (G602). The
  server sent `duration_warning` on the LiveKit path only, no client read it,
  and the in-browser path ended calls at the ceiling without a word.
- **A refused microphone ended a LiveKit call** (G603), although a translated
  "listen and type instead" message existed for exactly that case.
- **The officer's device check leaked a microphone** when run twice or closed
  during its prompt (G604).
- **Every way a call ended looked like success** (G605): "Thank you for calling
  URA", with no way to call again. And the server's "calls are off" (1001) and
  "all lines busy" (1013) were sent before the socket was accepted, so a
  browser only ever saw 1006, and the staff console, which hides its call
  layer on 1001, retried for good.
- **The officer's live transcript stopped without a sign and never came back**
  (G606); a lobby outage was visible nowhere; the lobby reset its reconnect
  backoff on a socket that had merely opened.
- **The AI and tone notice was English in three languages, and call errors
  were English in all five** (G607).
- **The call dialog was `aria-modal` but never took, kept or returned focus**
  (G608).
- **A failed un-mute showed the microphone as live**, and mute, officer name
  and ticket carried over to the next call (G609).
- **The presence heartbeat offered an officer to callers** while their media
  reconnected, while they held a claim, and while the call was ending (G610).
- **"Mark done" on a callback and the call review failed silently** (G611).

## Decision Outcome

**A refusal is accepted, explained, then closed.** The call, lobby and officer
audio sockets accept first, send an `error` frame with a `code` where there is
a caller to read it, and close with the real code (1001 calls off, 1013 busy).
Only a page from another site is still refused before accepting, and told
nothing.

**Every error the caller sees is translated from a code.** Error frames carry
`code` (`unavailable`, `busy`, `signin`, `consent_required`, `bad_request`), and
the screen maps codes and close codes to `call.error.*` strings in the caller's
language. The server's English `detail` is never shown. A call that ended
normally (the server said `ended`) thanks the caller; any other ending says why
and offers to call again.

**The time limit is announced on every transport.** One task per call
(`ws._call_clock`) sends `duration_warning` a minute before the ceiling to the
caller's screen and the officer's live stream, then ends the call at the
ceiling. That end was missing on the fallback path. The caller's screen shows
it in the call thread, which screen readers announce. The officer's workspace
shows a notice.

**Work a call started is dropped if the call is over by the time it finishes.**
The caller's hook counts calls; a microphone granted for a call that has ended
is stopped at once and sends nothing; a refusal for one is ignored. The
officer's session does the same with its socket identity.

**A refused microphone keeps the LiveKit call.** The call stays connected and
muted with the listen-and-type message, and pressing the microphone tries
again. A failed mute or un-mute is reverted on screen on both sides.

**Staff streams say when they are live.** The server sends `{"type": "ready"}`
once a staff stream is signed in and subscribed. The lobby resets its backoff
and reports itself open only then. The officer's per-call transcript reconnects
with a backoff, except after a final close or once the call has ended, and
shows a notice while it is down. The console bar shows "Live call updates
paused" on every staff page while the lobby is reconnecting.

**Presence follows the call.** The console reports `on_call` from claiming to
ending, and the server also refuses `available` while the officer is the
call's reconnecting officer or holds its claim.

**The call dialog manages focus** (`useDialogFocus`): it takes focus on
opening, keeps Tab inside, moves focus into the new view when the call starts,
and returns it to the opener on close. Escape still does not end a live call.

Also: the device check stops an earlier microphone and drops one granted after
the dialog closed; mark-done and the review form show their errors; the
Luganda micFallback string lost a stray leading space.

**Corrected in review, before merge:** a mute change overtaken by a newer one
no longer decides what the screen shows; a caller who listened and typed
without a microphone is thanked at the end rather than shown the microphone
notice; the transcript says when it stopped for good (sign in again) as well
as when it is reconnecting, and a reconnect drops the half-heard caption; the
lobby stays marked as paused until the server says it is ready again; the
device check opens fresh after a finished check; Shift+Tab from the focused
call card stays in the dialog.

## Consequences

- A browser now sees 101 then a close for "calls off", where it saw an HTTP
  403. The probe in the Space runbook changes accordingly: 101 followed by
  close code 1001 means calls are off.
- A call that drops mid-way now shows an error and a call-again button instead
  of the thank-you. Callers whose connection is poor will see that more often;
  it is the truth.
- Seven new interface keys in five languages, plus the tone notice in three:
  31 translated strings. The Luganda, Runyankole, Acholi and Swahili ones were
  written for this change from the existing strings' vocabulary and have **not
  been reviewed by a first-language speaker**. G607 stays open until they are.
- The time-limit warning is shown, not spoken: no spoken line exists for it in
  the five languages yet.
- An officer whose call ended with an error, rather than a normal end, still
  reports their chosen availability: the console cannot tell from its state
  alone whether a wrap-up is owed.
- No new route, flag or model. One new staff-stream frame (`ready`), one new
  call-socket frame for the caller (`duration_warning`, now on every
  transport), a `code` field on error frames.

## Confirmation

Backend: `App/backend/tests/test_receptionist_call_screens.py` (refusals in
accept order with codes, the call clock on a real store, the lobby ready frame,
the presence guard through the real route). Frontend:
`src/__tests__/hooks/useCall.screens.test.tsx` drives the real `useCall` and
`CallSocket` over a fake WebSocket with the microphone, player and LiveKit
replaced; `src/__tests__/components/officerCallScreens.test.tsx` covers the
device check, the live transcript, the lobby, the heartbeat and the failing
actions; the call dialog's focus is in `CallScreen.test.tsx`; the officer
session's late microphone and failed mute in `officerCallSession.test.ts`; the
strings in `lib/i18n.test.ts`. Each failed before its fix.

Two officer-session tests asserted that the microphone stopped when a call
ended. They passed only on stops left over from earlier tests: in both, the
microphone finished starting after the call ended, which is G601. They now let
the microphone start first.

Not verified here: a real browser's permission prompt, focus order with a
screen reader, or the strings with first-language speakers.

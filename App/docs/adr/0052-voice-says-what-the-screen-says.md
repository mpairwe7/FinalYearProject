# ADR-0052: Voice says what the screen says

## Status

Accepted, 2026-11-13. Phase 5 of the
[13 November intelligent UI integration audit](../plans/intelligent-ui-integration-audit-2026-11-13.md)
(G645–G649). The service owner asked for the audit's fixes to be applied phase by
phase in the session that made it.

## Context and Problem Statement

Voice mode replaces the composer with a dock and reads answers aloud. A listener
hears what the screen does not repeat:

- a reading that skipped a sentence or was cut off counted as complete, and voice
  mode reopened the microphone as if the answer had been heard (G645);
- an answer's reliability caveat ("Verify with URA", the human-review banner) and a
  failed or interrupted answer were never spoken; the dock said replies are read
  aloud (G646);
- the composer footer, the only always-visible notice that an AI is answering, is
  hidden in voice mode (G647; EU AI Act Art. 50(1), in application since 2 August
  2026; the Commission's July 2026 guidelines expect a spoken disclosure at the
  start of a voice interaction);
- a spoken question still being transcribed was sent into whichever chat was open
  when transcription finished (G648, breaking ADR-0018's binding);
- "Take a photo" on desktop called `getUserMedia` under the site's own
  `Permissions-Policy: camera=()` (G649).

## Considered Options

For the spoken disclosure: speak it before opening the microphone on entering
voice mode, or with the session's first answer. Before the microphone adds a delay
to the action the taxpayer just took and risks the microphone hearing the notice.
Chosen: prefix the first answer read aloud in each voice session, and show the
notice in the dock throughout.

## Decision Outcome

1. `speakStreamed` reports `complete`: the server's `done` line arrived, it
   reported no failed piece, and every piece played. It stays registered until
   the last piece has been heard, so a Stop after the closing line still counts as
   a stop. Otherwise the page shows `narration.partial` (in the dock, and in the
   read-aloud bar outside voice mode), announces it, and does not reopen the
   microphone. A reading that could not play at all is `failed`, distinct from
   `ended`, and does not reopen it either.
2. `needsSpokenCaveat` (score under 0.6, judge below `high`, escalation; not an
   abstention or an officer's message) puts `narration.caveat` before the spoken
   answer (`spokenReply`), so a listener who stops a long answer early has heard it; an interrupted answer in voice mode puts `message.incomplete` in the
   dock.
3. The dock shows `voiceDock.aiNotice`; the first answer read aloud after entering
   voice mode starts with `voiceDock.aiSpoken`. If that reading is not heard to the
   end (failed, stopped, cut off, a skipped piece), the next answer says it again.
4. A spoken turn records the chat it was spoken in, giving a blank chat its id
   when the turn starts; if another is open when it is transcribed or about to
   send, it is dropped with `voice.conversationChanged`.
5. "Take a photo" uses the capture file input everywhere.

## Consequences

- Hands-free use pauses after a partial reading: the taxpayer taps to talk again.
- A doubted answer is longer to hear by one sentence.
- The in-page camera component remains only in the unmounted `VoiceVisionMode`.
- The new strings in four languages are machine-assisted and unreviewed (G275).

## Confirmation

Each test was watched failing before the fix; G648's was also run against the
code with the check removed and failed:

- `App/frontend/src/__tests__/services/speechStream.test.ts`: a Stop after the
  closing line, while audio still plays, is a stop and not a complete reading.
- `App/frontend/src/__tests__/components/NarrationBar.test.tsx`: a partial reading
  is said in the read-aloud bar outside voice mode.
- `App/frontend/e2e/voice.spec.ts`: the AI notice is said on the next answer after
  a reading that failed; a turn spoken in a blank chat is not sent into a new chat
  opened meanwhile.
- `App/frontend/src/__tests__/services/speechStream.test.ts`: complete only with the
  closing line, no server-reported failure and no skipped piece.
- `App/frontend/src/__tests__/lib/spokenCaveat.test.ts`: which answers need the caveat;
  the AI notice and the caveat come before the answer (`spokenReply`).
- `App/frontend/src/__tests__/components/VoiceModeDock.test.tsx`: the notice in the
  reader's language.
- `App/frontend/e2e/voice.spec.ts`: the first answer is spoken with the disclosure; a
  doubted answer with its caveat; a turn transcribed across a chat switch is not
  sent.
- `App/frontend/src/__tests__/components/ChatInput.test.tsx`: the capture input on a
  wide screen, no `getUserMedia`.

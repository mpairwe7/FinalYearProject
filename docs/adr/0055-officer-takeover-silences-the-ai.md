# ADR-0055: Once an officer has the call, the caller hears and sees only the officer

## Status

Accepted, 2026-11-13. Phase 9 of the
[11 November call receptionist review](../plans/receptionist-assurance-2026-11-11.md).
It follows [ADR-0053](0053-sessions-can-be-revoked.md) (phase 8, part D) and
amends the handoff of [ADR-0039](0039-call-handoff-and-caller-safety.md) and
the hang-up paths of [ADR-0047](0047-wrap-ups-owed-while-live-and-livekit-control-limits.md).
It fixes G657. The service owner asked for the investigation, then approved all six
changes, on 2026-11-13, in the session that made them. Amended the same day after
review: the bridge re-checks the call after each of its awaits; the gate holds the AI
back once the call has ended as well as while bridged; a call that ended during the
greeting starts no pipeline.

## Context and Problem Statement

The defect was found by signing in to the Space as the test officer and driving a
real handoff with a scripted caller. The timings below come from those runs and
the Space's own log (Pipecat 1.15, the WebSocket transport the Space uses).

**The AI's speech outlived the handoff.** Pipecat makes the AI's speech faster
than it plays: the whole 23 s greeting was synthesised in 0.3 s and queued in the
output transport, which plays it out in real time. The messages the AI sends after
a line (a caption, the transfer's `status: transferring`, its `queue` place) wait
in the same queue behind it. A caller asked for an officer 4.5 s into the greeting;
an officer joined 3 s later and stayed on for 32 s:

| Time | What the caller got |
|---|---|
| 6.1 s | Asks for an officer. The greeting plays on; nothing on screen changes. |
| 9.2 s | Connected to the officer. |
| 9.2–24.8 s | The rest of the AI's greeting, over the officer's voice. |
| 24.8–29.5 s | The AI: "I'm connecting you to a URA officer, please hold." |
| 29.5 s | `status: transferring` and `queue position=1`: the screen leaves the officer for the queue and stays there. |

The bridge silenced the AI in two ways, and neither reached that queue. Mode
`bridged` closes `OfficerOutputGate`, which drops the AI's replies as they arrive
at it; this speech had passed it while the call still waited. And the desk sent
the caller's player `{"type": "interrupt"}`, which drops the audio the browser
holds; the server kept sending the rest.

**Asking for an officer did not interrupt the AI.** A call control or a typed
question cuts the AI off. A request for an officer waited behind whatever the AI
was saying, so the caller had no answer and no waiting screen for up to 20 s.

**The officer audio socket's `hangup` did not end the call properly.** The console
never sends it (End is `POST /v1/admin/calls/{id}/end`), but the socket accepts it,
and the LiveKit control socket answers the same message with the desk's End.
On the audio socket it called `CallRoom.end()` directly: no check that the sender
was on the call, no closing line, no word to the caller. And on the WebSocket
transport `CallRoom.end()` could not stop the caller's pipeline. It stops a runner
it can see; the LiveKit path registered its runner, but this path ran it inline. The
caller stayed connected 60 s after the officer hung up, hearing the AI's leftover
speech, then saw the stale waiting screen for a call already recorded as ended.

**A revocation by the wrong id did nothing.** `/v1/me` shows an internal
`user_id` beside `external_id`, the token's `sub`. Revocations (ADR-0053) are kept
by `sub`; `POST /v1/admin/users/{internal id}/revoke` answered 200 and refused no
token. Found while checking ADR-0053 live with the test administrator.

## Considered Options

For the queued speech:

1. **Interrupt the whole pipeline from its source** (`PipelineTask.queue_frame`),
   as the caller's own LiveKit `interrupt` does. Rejected. The interruption
   reaches the output transport after a variable delay, and on LiveKit the
   officer's joining line is queued straight into the output transport
   (`transport.send_audio`): an interruption arriving after it would wipe it. On the
   way it also passes the brain, which counts it as the caller barging in.
2. **Clear the output transport itself, and wait until it has.** Chosen. The
   interruption goes into the output transport's own queue, ahead of anything
   queued there after it, and Pipecat calls back once it is handled.
3. **Drop the AI's frames only as they are written to the caller's socket.**
   Deterministic on the WebSocket transport, but LiveKit has no such writer.
   Kept as a second layer, not as the fix.

For the officer's socket hang-up: remove the message (no client of ours sends it),
or send it through the desk's End. The second was chosen: one way for an officer to
end a call, as the LiveKit control socket already does.

## Decision Outcome

1. **The bridge clears the AI's queued output on the server**
   (`desk._interrupt_ai_output`). After the mode turns `bridged`, and before the
   caller's player is told to drop its audio and the joining line plays, the bridge
   queues an `InterruptionFrame` into `room.transport.output()` with a callback, and
   waits for it, at most `_AI_SILENCE_TIMEOUT_S` (1 s). Pipecat's output transport
   then drops its queued audio and the interruptible messages behind it. On the
   WebSocket transport it also writes `{"type": "interrupt"}` to the caller before
   the callback, so that cannot land after the joining line and cut it. A pipeline
   that does not answer is logged, and the officer joins anyway. A call with no
   pipeline is unchanged. The call can move on during any of the bridge's awaits,
   each send to the caller included (the caller hangs up, the officer presses End):
   after every one, a call no longer bridged to this officer, or ending, gets
   nothing more: no joining line, no `bridged` status, no `call.bridged` lobby
   event.
2. **The AI's frames are held back once the wait is over, at the gate and at the
   socket.** `hold_gate.is_stale_wait_message` marks a `queue` place or a
   `status: transferring` that arrives when the call is not `transferring`.
   `OfficerOutputGate` drops these, as well as the AI's replies once the call is
   bridged or ended (`CallRoom.end()` closes the language router before it stops
   the pipeline, and on LiveKit nothing behind the gate holds them back).
   `BrowserCallSerializer`, which runs as each frame is written to the caller's
   socket, drops the AI's audio and captions once the call is `bridged` or `ended`,
   and drops stale waiting messages in any mode. The officer's audio, captions and
   desk lines go to the socket directly, never through either.
3. **The brain sends the waiting screen only if the call still waits.** After the
   transfer notice, `_open_transfer` sends `status: transferring` and the queue
   place, and starts the countdown, only if the call is still `transferring`.
4. **Asking for an officer interrupts the AI**, as a call control does: the
   generation is superseded, the answer in progress is cancelled, and an
   `InterruptionFrame` goes downstream before the transfer notice. Only a
   request that opens the transfer does this; a second request does not cut off
   the hold.
5. **The officer's socket hang-up is the desk's End.** `OfficerLeg` sends
   `{"type": "hangup"}` to `desk.end(call_id, officer_id, speech_model)`. The
   officer must be on the call. The caller hears the closing line and then
   `status: ended`, and the officer owes a wrap-up. The call ends as
   `officer_ended`; the `officer_hangup` reason is no longer produced.
6. **On the WebSocket transport, ending the call stops its pipeline.**
   `ws._run_pipeline` runs the runner as `room.pipeline_runner_task`, as the
   LiveKit path does. `CallRoom.end()` cancels it, and Pipecat's transport then
   closes the caller's socket. A call that ended during the greeting, before
   there was a runner to cancel, starts none. Every server-side end already tells the caller
   `status: ended` first or closes the socket itself.
7. **A revocation resolves an internal id to its `sub`**
   (`main._session_subject`). An id that is a user row's internal id is replaced
   by that row's `external_id`. Anything else (a `sub`, one not seen yet, or any id
   while the user table cannot be read) is used as given. The answer's `user_id`
   and the audit record name the `sub` that was revoked.

## Consequences

- An officer joining a call silences the AI on the server, not only in the
  browser. Their joining line can start up to `_AI_SILENCE_TIMEOUT_S` later when
  the pipeline is slow to confirm; Pipecat confirms in milliseconds.
- A caller who asks for an officer hears the AI stop and the handoff line at once,
  and sees the waiting screen at once. An answer the AI was giving is abandoned.
  That is the request's intent, as with a typed question.
- The WebSocket transport now closes the caller's socket when the server ends a
  call, as LiveKit's path does. The caller's screen already hung up on
  `status: ended`; a client that did not now sees the close.
- The `officer_hangup` value of `ura_receptionist_calls_ended_total{reason}`
  stays in the label set for history, but new calls do not use it: an officer's
  hang-up from either socket or the console counts as `officer_ended`.
- An administrator can revoke with either id `/v1/me` shows. A mistyped id is
  still taken as a `sub` and answers 200. The answer's `user_id` shows what was
  revoked, so a typo is visible.
- On LiveKit, one audio chunk (40 ms) being written when the interruption lands
  can still reach the caller: it has no socket writer to hold it back.

## Confirmation

**`App/backend/tests/test_receptionist_takeover_silence.py`.** Each test failed
before the change, or is a guard that passes before and after:

- the bridge interrupts the output transport and waits for it before the joining
  line; an output that never answers delays the officer only by the timeout and is
  logged; one that fails does not stop the officer joining; a call that ends while
  the AI is silenced, while the interrupt or the `bridged` status is being sent, or
  whose End is pressed during the joining line, is not announced as bridged, and
  one that completes is announced once;
- the socket writer holds back the AI's audio and captions once bridged or ended,
  and stale waiting messages in any mode; it lets a waiting caller's audio, queue
  place and status through, and the interrupt and other messages in every mode;
- a transfer notice overtaken by the officer sends no waiting screen and starts no
  countdown; a caller still waiting gets both;
- asking for an officer pushes the interruption first, supersedes the generation
  and cancels the answer in progress; asking again while waiting, or while
  bridged, pushes nothing;
- `CallRoom.end()` stops a pipeline run by `ws._run_pipeline`; a call that ended
  during the greeting starts no runner;
- **with Pipecat 1.15 itself** (CI's receptionist job): the real FastAPI WebSocket
  transport and `OfficerOutputGate`, fed the AI's speech queued in real time and
  then the waiting screen. A waiting caller gets both. After the bridge, the
  queued speech stops within one chunk, the waiting screen never arrives, and the
  caller gets the interrupt. The output transport's own clearing does this even
  without the mode check behind it. Frames the AI sends after the bridge stop at
  the gate, which holds the AI's replies back once the call is bridged or ended
  and passes them while it waits or talks to the AI. Ending the call closes the caller's socket from the server.

**`App/backend/tests/test_receptionist_frame_limits.py`.** The officer's socket
hang-up ends the call as `officer_ended`, the caller hears the closing line and gets
`status: ended`, and the officer owes a wrap-up. A hang-up from an officer who is
not on the call ends nothing.

**`App/backend/tests/test_session_revocation.py`.** Revoking by the internal id
revokes the `sub`: the token is refused, and the answer and the audit name the
`sub`. When the user table cannot be read, the id is revoked as given.

**On the Space**, after the roll, as the test officer: the scripted handoff with a
32 s bridge and the socket hang-up run again (see the
[runbook](../runbooks/voice-receptionist-demo.md#checking-an-officer-handoff-end-to-end)).
After the bridge the caller gets no AI audio and no `transferring` or `queue`, and
on any end gets `status: ended` and a closed socket.

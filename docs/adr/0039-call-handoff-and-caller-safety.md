# ADR-0039: Call handoff and caller safety — the queue is what reaches an officer, the record follows it

## Status

Accepted, 2026-11-11. Phase 1 of the
[11 November call receptionist review](../plans/receptionist-assurance-2026-11-11.md).
It builds on [ADR-0020](0020-receptionist-teardown-media-isolation-and-evidence.md)
and changes none of its decisions. Production acceptance on a real SFU and real
speech remains open (G303, G307).

## Context and Problem Statement

A call is answered by the AI and handed to an officer when the caller asks, the
answer is weak, or the caller is in distress. The review found the handoff could
fail in ways no test covered, each of which left a caller waiting, misrouted or
cut off:

- **The record gated the handoff.** Requeueing a call, raising a waiting caller in
  crisis to urgent, and the officer's End all wrote the transcript, the row or the
  officer's presence first. When that write failed, the step that reaches a person
  never ran (G569, G570, G583).
- **The row and the room disagreed.** After an officer's connection lapsed the room
  said `transferring` and the row said `bridged`. A claim is a conditional update on
  the row, so every later claim was refused (G568).
- **A transfer had no tenant.** Its ticket fell back to `default`, because a
  transfer runs outside any chat turn (G571).
- **The AI spoke before it knew.** It promised an officer before checking that a
  handoff was possible, offered one with handoffs off, answered a caller already on
  hold, and ended calls on a word match inside a question (G572–G574).
- **Slow work ran on the event loop.** Machine translation on transfer and an
  all-pairs transcript scan on every turn froze every live call on the worker
  (G577, G578); a media-service call with no deadline held a call's desk lock
  (G579).
- **The console and the server disagreed.** "Transfer to an officer" was ignored
  (G580); a wrap-up removed a waiting caller from every queue (G581); a caller
  hanging up looked like a dropped connection, so the officer was never shown the
  wrap-up (G582).
- **A safeguard crashed.** The spoken-figure guard's withheld branch called a
  method that does not exist (G575).

## Decision Outcome

**The queue first, the record after.** On every path that puts a caller in front of
an officer (the AI's transfer, an officer passing the call on, an officer whose
connection lapsed, a crisis raised while waiting), the lobby event and the waiting
caller's countdown are not conditional on a transcript, row, ticket or presence
write. Those writes are attempted and a failure is logged at warning. The officer's
End hangs up whatever the closing line and the presence update do, and a second End
joins the first.

**The row follows the room at every requeue.** `_expire_reconnect_grace` writes
`status='transferring'` and clears the officer, as `desk.transfer` already did.

**A transfer carries the call's tenant** to `_maybe_create_ticket`.

**A named transfer is a reservation.** `desk.transfer(target_officer_id=…)` checks
the named officer is `available` before anything changes, then holds the call for
them for `RECEPTIONIST_CLAIM_TIMEOUT_S`. `desk.claim` and the claim's conditional
update both refuse anyone else until then; `call.unreserved` opens it to all. The
row carries `reserved_for` and `reserved_until` so a reloaded console agrees with
the server. No new setting: the reservation is as long as a claim.

**The AI says what it knows.** The timeout and error lines are an apology; what
happens next is said by the transfer once the queue flag and staffing are known. No
officer is offered while `ticket_queue` is off (a crisis still gets the helplines).
A generation that finishes after the call left the AI is dropped. A hang-up phrase
ends a call only at the end of a short turn that is not a question; speech during
the goodbye keeps the call; asking for a person is never a review answer.

**Nothing slow on the loop.** The transfer's ticket is written in a thread by
`create_transfer_ticket`, which changes no call state; `open_transfer` then runs on
the loop with the result, and a transfer in flight absorbs a second request. The
Luganda translation fallback runs in a thread. The at-risk monitor evaluates in a
thread, one run at a time, compares a new turn with at most 40 earlier ones, and
publishes only when the level or the signals change. `livekit.revoke_participant`
and `close_room` have a 5 s deadline and raise the `RuntimeError` their callers
already handle.

**The end of a call is said, not inferred.** `CallRoom.end` publishes
`status: ended` on the call's channel and sends it to the officer's socket before
that socket and the media room close. `call.wrapped_up` carries `live`; consoles
remove a call only on `call.ended`.

**A withheld answer goes on screen.** The written answer is captioned and recorded,
the caller is pointed to it, "repeat that" replays the pointer, and "more detail"
passes the same check.

## Consequences

- A handoff can now complete with a missing transcript notice or a stale row. The
  warning log is the trace; there is no reconciliation yet (G306, phase 3).
- A real goodbye phrased as a question, or followed by words outside the short
  courtesy list, is no longer taken for one. The caller is answered or asked again,
  and the End button always works. Hanging up on a question was the worse error.
- The reservation holds a caller for up to one claim window for an officer who may
  not act. It is checked against presence first and lapses on its own.
- The officer named in a reservation is identified to the console by the token's
  `sub`. That is display only; the server decides from the token it verified.
- The at-risk monitor no longer finds a question repeated more than 40 caller turns
  apart, and a calm call's row keeps no risk record until something changes.
- Ticket creation off the loop means a caller can hang up while it is written. The
  ticket stays for the officers; the call is not put in the queue.
- Two columns are added to `voice_calls` by the existing startup migration. No flag,
  route, model or dependency changes.

## Confirmation

`App/backend/tests/test_receptionist_handoff_safety.py` (each test failed before its
fix), `App/backend/tests/test_receptionist_router.py`, and in the console
`useCallConsoleStore.test.ts`, `officerCallSession.test.ts`,
`CallAlertStack.test.tsx`, `CallQueueItem.test.tsx` and `calls.test.tsx` under
`App/frontend/src/__tests__/`. The tests run against the real SQLite store and, for
the tenant case, the real `ChatModel`; the media service, speech and LiveKit are
doubles. Staging acceptance with real media and five-language speech is still
required before the receptionist is switched back on in production.

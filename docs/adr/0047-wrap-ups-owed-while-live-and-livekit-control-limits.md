# ADR-0047: A wrap-up is owed for a call the officer left while it runs, and LiveKit control sockets are limited

## Status

Accepted, 2026-11-13. Phase 8 of the
[11 November call receptionist review](../plans/receptionist-assurance-2026-11-11.md),
part C. It follows [ADR-0045](0045-call-sockets-wrap-ups-and-a-dead-call-process.md)
and [ADR-0046](0046-superseded-call-turns-stop.md). It closes G610 and narrows
G304. The service owner approved the design on 2026-11-13, as a short design, in
the session that made it. Token revocation (the rest of G304) is part D, which
has its own record.

## Context and Problem Statement

**G610.** ADR-0045 made an officer owe the wrap-up of a call that ended under
them, and made the presence heartbeat hold `wrap_up` until they save it. Calls
that went on without the officer were still left out:

- **A transfer.** It moved the officer to `wrap_up`, but the owed-wrap-up lookup
  read only ended calls. After a reload, the console reported `available`, and the
  officer could be offered to callers or named in a transfer with the wrap-up
  unsaved.
- **The end of the reconnect grace,** on a call that goes back to the queue. The
  officer was made `available` and was never asked for the wrap-up.
- **A save during the reconnect grace.** `desk.wrapup` refused a save only from the
  officer on a `bridged` call. During the grace the room's mode is `transferring`,
  so the officer could save a wrap-up, and be made available, while the call was
  still held for them to rejoin. That broke G305's rule: an officer wraps up only
  once they are off the call.
- **The console's wrap-up form** defaulted the ticket action to `resolve`. The
  server refuses `resolve` on a live call, so after a transfer the save failed with
  409 until the officer picked "Keep open".

**G304.** ADR-0045 limited the WebSocket call sockets. A LiveKit call's control
sockets had no limit:

- the caller's (each `text_turn` queues a model turn);
- the officer's;
- the listener's.

A single binary frame on the caller's control socket raised in `receive_text()` and
ended the call.

## Considered Options

- **G610:**
  - extend `owed_wrapup` to live calls the officer has left;
  - add a "wrap-up owed" column the desk sets on transfer and lapse.
- **G304:**
  - reuse `FrameGate` on the three LiveKit control loops;
  - extend the serializer's sliding windows to them.

## Decision Outcome

**G610: a live call the officer has left is owed.**

- **The lookup.** `store.owed_wrapup` also returns a live call (`ai`,
  `transferring` or `bridged`) that lists the officer in `handled_by`, has no
  wrap-up from them, and whose row names neither them as `officer_id` nor as
  `claimed_by`.
  - A transfer and a lapsed grace clear the row's `officer_id`, and the next
    officer to join replaces it, so the officer is "off" the call.
  - During the grace the row still names them, so the call is held for them and
    not owed.
  - Ended calls keep ADR-0045's rule. Both kinds use the same 24-hour window and
    the same bounded scan.
- **The save.** `desk.wrapup` refuses a save on a live call that the officer still
  holds in any form: they are on it, it is waiting for them to rejoin, or they have
  claimed it. Each is a 409 that says what to do. The officer who passed the call on
  can save.
- **The end of the grace.** On a call that goes on, the officer is moved to
  `wrap_up` (`presence.owe_wrap_up`) before the claim is dropped, so the release
  leaves the wrap-up alone. They are no longer made `available`. A callback the
  caller asks for while the officer's media is being revoked ends the same way.
- **The form.** `WrapUpPanel` takes `live` from the call. On a live call it offers
  only "Keep open".
- **Why not a column:** the owed state already follows from `handled_by`,
  `wrapups` and the row's officer. A column would be a second source of truth for
  every end path to keep in step.

**G304: the same gate on the LiveKit control loops.**

- **Where it applies:** the caller's, the officer's and the listener's control loops
  each create a `FrameGate`. A listener counts as `officer`.
- **How frames are read and handled:**
  - Frames are read with `receive()`, not `receive_text()`.
  - A text frame goes through `gate.text()`: at most 15 per 10 s, 16 KiB at most.
  - A binary frame, which these sockets never expect, is dropped and counted as
    `malformed` (`FrameGate.unexpected()`). It no longer ends the call.
  - Only a flood that lasts five seconds closes the socket, with 1008. Teardown
    takes the existing paths: the pipeline stops for the caller, and the officer
    gets the disconnect and its grace.
- **Why not the serializer's windows:** those apply only to `call_control` and
  `text_turn` on the WebSocket path. The gate is one rule for every frame on every
  call socket.

## Consequences

- **An officer who passed a call on, or whose grace lapsed, is in `wrap_up`.** The
  heartbeat keeps them there across a reload until they save it, and the console
  opens that call's wrap-up. They are not offered to callers meanwhile.
- **An officer who holds a call cannot wrap it up early.** The 409 says to rejoin,
  end, transfer, join or release first.
- **A callback the caller asks for during the media revocation that follows a
  lapsed grace** leaves the room's old claim in place. The officer's save is then
  refused until they release it or the call ends.
- **A callback during the grace itself,** before it lapses, leaves the officer
  reconnecting and `on_call` until the call ends. That is unchanged.
- **G304 stays open for revocation** before a token expires (part D).

## Confirmation

**`App/backend/tests/test_receptionist_owed_wrapup.py`.** Each test failed before
the change, or is a guard that passes before and after:

- a live call the officer passed on, one now with another officer, and one back
  with the AI are owed;
- the grace, a new claim, a saved wrap-up and a stale row are not owed;
- the end of the grace while the call runs, and a callback during the revocation,
  leave the officer in `wrap_up`;
- a reload after a transfer keeps `wrap_up`, and the officer is not named in a
  transfer;
- saves by the reconnecting officer, by the claimant, and by an officer holding a
  claim only on the row are refused;
- the officer who passed the call on can still save.

**`App/frontend/src/__tests__/components/WrapUpPanel.test.tsx`:** a
live call offers only "Keep open".

**`App/backend/tests/test_receptionist_ws.py` and
`App/backend/tests/test_receptionist_frame_limits.py`,** for the caller's,
officer's and listener's LiveKit control loops:

- the 16th text frame within 10 s is dropped and counted;
- a binary frame does not end the call;
- a five-second flood closes 1008 and takes the existing teardown path.

The caller's loop needs Pipecat's runner module. Locally the tests used a stand-in;
CI's receptionist-stack job runs them for real.

No existing test needed changing.

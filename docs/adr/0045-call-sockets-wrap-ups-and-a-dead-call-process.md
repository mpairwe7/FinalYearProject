# ADR-0045: Call sockets take bounded frames, a call that ends under an officer is wrapped up, and a dead call process is alerted on

## Status

Accepted, 2026-11-12. Phase 7 of the
[11 November call receptionist review](../plans/receptionist-assurance-2026-11-11.md),
part A. It follows [ADR-0044](0044-flag-changes-reach-every-process.md). It narrows
G304, G610 and G614. Part B (G591, superseded model runs) has its own record because
it changes the generation path that chat shares. The service owner approved both
parts on 2026-11-12, as short designs, in the session that made them.

## Context and Problem Statement

Three gaps were left open by earlier phases, and each could be fixed inside the
call receptionist:

- **G304, frame limits.** Neither the officer's audio socket nor the caller's call
  socket limited the size or rate of its frames. Uvicorn's 16 MiB message cap was
  the only bound.
  - A flood grew the officer's speech buffer without a byte bound, because the
    utterance cap is wall-clock time.
  - An odd-length binary frame made `np.frombuffer` raise. That ended the officer's
    leg and dropped them into the reconnect grace.
- **G610, owed wrap-ups.** A call can end under an officer without their End: the
  call's time limit, the caller's media dropping, a failed revocation, or the end of
  their reconnect grace.
  - `_finish_call` decided from the room's mode alone. A call the server had already
    ended (mode `ended`) left the officer `on_call` on a dead call. A call that ended
    during the grace marked them `available`.
  - The console's next heartbeat then reported `available`. With the room gone, the
    server accepted it, and the officer was offered to new callers with no wrap-up
    saved.
- **G614, a dead call process.** On the Space, chat, `/health` and `/ready` keep
  answering when the call process is dead (ADR-0043). No metric said so:
  - `UraApiDown` is satisfied by either process;
  - `receptionist_available` goes stale when only the call process is down.

## Considered Options

- **G304:**
  - a fixed one-second window, as `voice_ws.py` uses;
  - a token bucket per socket;
  - closing the socket on the first bad frame.
- **G610:**
  - fix `_finish_call` only;
  - also have the heartbeat hold an owed wrap-up;
  - a new "wrap-up owed" column on the call row.
- **G614:**
  - a scrape-time hook;
  - a gauge kept current by the chat process;
  - doing nothing until the Space is scraped.

## Decision Outcome

**G304: a token bucket per socket, drop rather than close** (`receptionist/frame_budget.py`, `FrameGate`).

- **Where it applies:** the gate is checked before a frame is handed on.
  - For the officer's audio socket, it is in the receive loop.
  - For the caller's socket, it is in `BrowserCallSerializer`, where Pipecat reads
    the audio, and in the loop that runs without Pipecat.
- **Limits:** set for twice real-time speech (PCM16 at 16 kHz is 32 KB/s, at 50 frames
  per second):
  - a binary frame over 16 KiB is dropped (`size`);
  - an odd-length frame is dropped (`malformed`), except on a call that agreed to
    mu-law, which has one byte per sample;
  - audio over 64 000 B/s (burst 128 000 B) or over 200 frames/s (burst 200) is
    dropped (`rate`);
  - the officer's text frames are held to 15 per 10 s, and one over 16 KiB of UTF-8
    is dropped (`size`): bytes, not characters.
- **Configuration:** `RECEPTIONIST_AUDIO_BYTES_PER_S` sets the byte rate, which is
  never below 32 000.
- **What happens to a dropped frame:** it is counted in
  `receptionist_frames_dropped_total{side, reason}`, and a flood is logged once.
- **Closing:** only a sustained flood closes the socket. A flood is drops with gaps of
  at most one second. When it has lasted five seconds, the socket is closed with 1008
  `too many frames`. Closing on the first drop would push an officer into the reconnect
  grace and cut the caller. A browser tab that releases a throttled burst never reaches
  five seconds.
- **Why a bucket:** a fixed window punishes that same burst, whereas a bucket lets it
  through.

**G610: the end of a call owes the wrap-up, and the heartbeat holds it.**

- **At the end of a call:** `_finish_call` now treats three officers as owing the
  wrap-up:
  - the officer on a `bridged` call;
  - the officer still named on a call the server ended;
  - the officer in reconnect grace.

  The desk's grace paths do the same when the call has ended. Each calls
  `presence.owe_wrap_up`, a single conditional write: it moves the officer to
  `wrap_up` only while their presence is `on_call` for that call. So a wrap-up
  already saved, or another call taken meanwhile, is left alone.
- **On the heartbeat:** `store.owed_wrapup(officer_id, tenant_id)` finds the latest
  ended call of the officer's tenant that lists them in `handled_by` and has no
  wrap-up of theirs.
  - It looks at calls begun within 24 hours, newest end first, reading at most 20
    rows.
  - While one is owed, `PUT /v1/admin/officers/me/presence` turns a requested
    `available` into `wrap_up` and returns `wrap_up_call_id`. `busy`, `away` and
    `offline` still apply, and a call the officer is on or holds comes first.
  - The console opens that call's wrap-up when it has no call of its own, after a
    reload too, and only from the answer to its latest heartbeat: answers can
    arrive out of order, and an older one can predate a wrap-up saved since.
- **The heartbeat never overwrites a claim:** it runs on a worker thread and does
  not take the desk's lock, so a claim could land between its checks and its write
  and be overwritten with `available` or a wrap-up. It now reads the officer's
  presence first and writes only if `updated_at`, `status` and `current_call_id` are
  still as read (`presence.upsert_presence_if_unchanged`). Every write to presence
  stamps `updated_at`; the other two catch two writes in one clock tick. If the row
  changed, the newer write stands and is the answer, and the heartbeat only stamps
  `last_seen`. A version column was not added: every writer would have to bump it,
  and one that did not would silently undo the guard.
- **No new column:** the owed state follows from `handled_by` and `wrapups`, which the
  desk already writes. A new column would need a migration and a second source of
  truth.

**G614: the chat process says whether the call process answers.**

- **The gauge:** `receptionist_call_process_up` is 0 when `calls_process_status()`
  says the call process is not answering or not ready, and 1 for anything the call
  process says itself, `off` and its own `unavailable: <why>` included.
  `receptionist_available` reports those.
  - It is a `livemin` gauge written only in the chat role.
  - It is refreshed every 30 s from the lifespan, in a worker thread, because the
    question is a blocking one-second HTTP call. Each chat-role `/ready` also
    refreshes it, from the same answer it reports.
- **The alert:** `UraCallProcessDown` (`== 0` for 5 m, critical) is the sixth rule
  in the call receptionist group.
- **Why not a scrape hook:** it would run only in the process that answers the
  scrape, and nginx sends scrapes to either.

## Consequences

- **Nothing scrapes the Space yet** (G614 stays open on that). On the Space, a dead
  call process is still found by the roll's and the keepalive's `/ready` checks.
- **G304: a flood costs the caller nothing until it lasts.** A misbehaving client
  loses frames, not its call. LiveKit calls are partly outside these limits:
  - their audio does not use these sockets;
  - the text frames on their control sockets are not limited (G304, open).
- **G610: an officer who reloads with an unsaved wrap-up is shown it** and is not
  offered to callers until they save it. After this change, an officer with unsaved
  wrap-ups from the last 24 hours meets them one at a time. On the Space the
  database starts empty at each restart (G616), so there is no backlog there.
- **G610 stays open for two cases:**
  - a call the officer passed on, or lapsed off after the grace, is owed only once it
    ends;
  - a reload before they save lets them report `available` while that call is still
    live.
- **SQLite's `LIKE` ignores case.** The look-up's pre-filter could hide one owed call
  for officer ids that differ only by case. User ids from the identity provider do
  not.

## Confirmation

Each new test failed before its change, or was shown to fail with the change
reverted:

- **`App/backend/tests/test_call_process_gauge.py`** (G614):
  - the gauge's value for each `calls_process_status()` answer;
  - `/ready` and the gauge agreeing;
  - the chat-role loop running it, and the calls role and an unset role never
    running it;
  - the series being in the monitoring catalogue.
- **`monitoring/tests/alerting-rules.test.yml`** (G614): firing on `0x20`, silent on
  `1x20`. promtool runs in CI.
- **`App/backend/tests/test_receptionist_frame_limits.py`** (G304):
  - the bucket's burst and refill;
  - a normal 50 frames/s stream never dropped;
  - an oversize and an odd-length frame dropped and counted without ending the
    officer's leg;
  - a text frame measured in UTF-8 bytes: 5 000 emoji are dropped, 16 KiB of ASCII
    passes;
  - a flood dropped;
  - a five-second flood closed 1008, on both sockets;
  - the counter's zero series.
- **`App/backend/tests/test_receptionist_owed_wrapup.py`** (G610):
  - a `max_call_duration` end while bridged, an end during the grace, a failed
    revocation and a caller hang-up during revocation each leave the officer in
    `wrap_up`;
  - the conditional write leaves another call's presence alone;
  - the heartbeat holds `wrap_up` and returns `wrap_up_call_id` until the wrap-up is
    saved;
  - a second officer owes their own;
  - the look-back and saved wrap-ups are honoured, and another tenant's call is not
    owed;
  - a claim that lands between the heartbeat's checks and its write stays `on_call`,
    against an owed wrap-up, a plain `available` and an officer with no presence
    row yet; a heartbeat nothing races still writes.

  Three existing tests that encoded the old behaviour were updated. The
  revocation-race and bridged hang-up tests now expect the owed wrap-up. The
  failed-revoke claim test now expects `wrap_up`.
- **`App/frontend/src/__tests__/components/officerCallScreens.test.tsx`** (G610): a
  heartbeat with `wrap_up_call_id` brings up that call's wrap-up with no active call;
  an older heartbeat's answer that arrives last does not.

**Not verified here:**
- Pipecat's own receive loop closing the caller after a 1008. Pipecat is not
  installed locally; the serializer and the loop that runs without it are tested.
- A real flood, or a real call that hits its time limit, on the Space.

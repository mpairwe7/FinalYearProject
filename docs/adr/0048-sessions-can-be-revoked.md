# ADR-0048: A session can be revoked before its token expires

## Status

Accepted, 2026-11-13. Phase 8 of the
[11 November call receptionist review](../plans/receptionist-assurance-2026-11-11.md),
part D. It follows [ADR-0047](0047-wrap-ups-owed-while-live-and-livekit-control-limits.md)
(part C) and copies the cross-process pattern of
[ADR-0044](0044-flag-changes-reach-every-process.md). It closes the last open part
of G304. The service owner approved the design on 2026-11-13, as a short design,
in the session that made it.

## Context and Problem Statement

The service checks a bearer token's signature and expiry, and nothing else. It
kept no revocation list:

- **Signing out was local.** The console and the chat dropped the token in the
  browser and ended the provider's session. They never called the backend. A
  copy of the token (another tab, another device, a leaked one) kept working.
- **No one could stop a token.** An administrator had no way to sign out an
  officer whose laptop was lost. The token worked until it expired.
- **Open sockets lived on.** The staff sockets (lobby, live transcript, officer
  audio, listener) closed only at the token's expiry (ADR-0040). The lobby
  checked every 25 s. The officer's WebSocket audio waited on `receive()` until
  the expiry, or for ever without one. The caller's socket never checked again
  after `call_start`.
- **LiveKit media lived on.** An officer's media stays in the room until the
  reconnect grace ends, and their LiveKit token lasts five minutes. Media that
  rejoined during the grace was bridged again with no check.

Our tokens carry `iat`. `jti` and `sid` are not guaranteed by every provider
(Auth0 among them), so they cannot be relied on.

## Considered Options

- **A cutoff per person** (`revoked_before`): refuse any token issued to them
  before it.
- **A deny list of token ids** (`jti`): needs a claim the provider may not issue,
  and one row per token.
- **Short-lived tokens only:** does nothing for a socket that is already open,
  and depends on the provider's settings.

## Decision Outcome

**A cutoff per person, kept in every process.**

- **The store.** Table `revoked_sessions(user_id PRIMARY KEY, revoked_before,
  reason, actor, created_at)`, in SQLite and Postgres.
  - `revoke_sessions` upserts a row. It keeps the later cutoff, so a token once
    revoked stays revoked.
  - `load_session_revocations` returns `{user_id: revoked_before}`. It raises when
    the store cannot be read, and on Postgres when there is no pool. It never
    reads a broken store as empty.
- **Each process's map** (`app/auth/revocation.py`).
  - Every process re-reads the table every 5 s, in both process roles. The first
    pass runs at start.
  - The process that records a revocation applies it at once.
  - A read merges into the map and never moves a cutoff back.
  - An unreadable store keeps the map, counts
    `session_revocation_sync_failures_total` and logs at ERROR once.
  - The interval does not follow `FLAG_SYNC_INTERVAL_SECONDS`. Turning the flag
    sync off must not leave a signed-out token working elsewhere.
- **The rule.** A token is revoked when its `iat` is before its subject's cutoff.
  - The cutoff is the time of the revocation, a float. `iat` is a whole second.
  - A token issued in the same second as the sign-out, before it, is revoked.
  - A sign-in completed in that same second is revoked too. A sign-in a second
    later works.
  - A token with no usable `iat` is not affected. Its expiry still ends it.
- **The endpoints.**
  - `POST /v1/me/logout`: any signed-in person. Revokes their own tokens ("sign
    out everywhere"). Returns `{"revoked_before": …}`. Audited as
    `auth.signed_out_everywhere`.
  - `POST /v1/admin/users/{user_id}/revoke`: `ura_admin`, or the operator key.
    Optional body `{"reason": "…"}`. Audited as `staff.session_revoked` with the
    subject; the reason is kept in the table and only its length in the ledger.
  - Both write the store first. A failed write answers 503 and changes nothing.
  - Both are rate-limited (`RATE_LIMIT`, 30 per minute by default).
- **Enforcement.**
  - **The API.** A revoked token gets 401 `session revoked`, with
    `WWW-Authenticate: Bearer`, before the profile is fetched.
  - **Socket sign-in.** Chat, voice, ticket, call and staff sockets refuse a
    revoked token as they refuse an invalid one. A staff or call socket closes
    4401. `resolve_ws_session` now also returns the token's `iat`.
  - **Open staff sockets** check "expired or revoked" at least every 5 s and close
    4401. The lobby's wait drops from 25 s to 5 s; it still pings only after 25 s
    without a frame. The officer's WebSocket audio loop now polls. A receive still
    pending when a check comes round is kept, not cancelled, so no frame is lost.
    The listener and LiveKit control loops already polled every second or less;
    they now check revocation too. The officer goes into the reconnect grace, as at
    expiry.
  - **The caller's socket.** A signed-in caller's call is checked every 5 s, on
    every transport, beside the call clock. A revoked session sends
    `{"type": "error", "code": "signin"}`, closes 4401 and ends the call with
    `session_revoked`. An anonymous caller is not watched. Expiry still ends no
    call, as before.
  - **LiveKit.** The room records whose session the officer's media was issued
    under and its `iat`. A revoked officer's media is revoked at once, not at the
    end of the grace. Media that rejoins for a revoked session is revoked again
    and not bridged. Expiry alone leaves the media for the grace.
- **The browser.** The console's, the chat's and the sign-in page's sign-outs call
  `POST /api/v1/me/logout` with the token and `keepalive: true`, before clearing
  it. Any failure is ignored: signing out always completes here.
- **Why not `jti`:** it is not guaranteed, and a cutoff stops every copy of every
  token at once, which is what "sign out everywhere" means.

## Consequences

- **A signed-out token stops working everywhere.** In the process that took the
  sign-out at once; in the others within 5 s. Open sockets close within about 5 s
  after that.
- **Signing out signs you out on every device,** not only this one. The person
  signs in again to get a new token.
- **A sign-in in the same second as the sign-out is refused.** The person signs
  in again a second later. A provider round trip takes longer than that.
- **Clocks matter.** The cutoff is this service's clock; `iat` is the provider's.
  If the provider runs behind, a fresh sign-in is refused for that many seconds.
  If it runs ahead, a token issued just before the sign-out survives for that
  long.
- **A token without `iat` cannot be revoked.** The provider must issue `iat`;
  Auth0 does.
- **The identity provider is not told.** The browser still ends the provider's
  session. An administrator's revocation does not; a person with a provider
  session can sign in again at once. Disable the account at the provider to keep
  them out.
- **An administrator can revoke anyone's sessions,** in any tenant. The person
  only has to sign in again.
- **A row removed from the table takes effect at the next restart.** A running
  process never moves a cutoff back.
- **The ticket stream and MCP.** The live ticket stream refuses a revoked token at
  sign-in but does not close an open stream; it does not close at expiry either.
  The MCP server's own bearer check does not read this list.
- **The officer's LiveKit identity is the same on every sign-in.** An old media
  client that rejoins after the officer has signed in again, within its token's
  five minutes, is not told apart from the new one.
- **The Space's database is wiped on restart,** so its revocations are too. Tokens
  issued before a wiped revocation work again until they expire.

## Confirmation

**`App/backend/tests/test_session_revocation.py`.** Each test failed before the
change, or is a guard that passes before and after:

- the store keeps the later cutoff; an unreadable SQLite store and a Postgres
  store with no pool raise; the Postgres helpers are dispatched;
- the map: before, at and after the cutoff; no `iat`; an earlier cutoff never
  replaces a later one;
- the sync applies the store, keeps the map on a failed read, logs once, and runs
  in the lifespan;
- `POST /v1/me/logout`, then 401 `session revoked` for the same token and 200 for
  one issued after; audited; a failed write is 503 and changes nothing; a token
  without `iat` is not affected;
- the admin revoke: 403 for an officer, 200 for an administrator, audited;
- socket sign-in refuses a revoked token (staff, call, voice);
- an open lobby, an officer's WebSocket audio socket and a listener close 4401;
  the officer goes into the grace;
- an officer's LiveKit media is revoked at once, and refused when it rejoins;
  expiry alone keeps it;
- a signed-in caller's call ends `session_revoked` with 4401; an anonymous
  caller's goes on.

**`App/frontend/src/__tests__/lib/signOutRevokesSession.test.tsx`:** both
sign-outs send the logout with the token before clearing it, and still complete
when it fails.

Existing tests that stubbed a staff or caller sign-in were updated for the
five-value session.

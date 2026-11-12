# One call process: officer handoffs that work on a two-worker Space (G306)

Design spec, 2026-11-12. Phase 5 of the
[11 November call receptionist review](receptionist-assurance-2026-11-11.md).
Approved section by section in the session that produced it; the decision
record is [ADR-0043](../adr/0043-one-call-process.md).

## Intent

**Said.** The Space keeps two API worker processes (the owner's decision of
2026-11-11). Officer handoffs and live call transcripts must work on it.

**Problem.** A call's state lives in the process its caller's socket reached:
the room, its desk lock, the claim timer, the event hub that feeds the officers'
consoles, and the in-browser audio bridge. The officer's console reaches either
process for each request and socket, so a claim, a join, the live transcript or
the officer's audio reach the call's process about half the time. ADR-0041 only
warns about it at startup.

**Success.**

- Every call request reaches the process that holds the call: the caller's
  socket, the officer's audio socket, the staff lobby and per-call streams, and
  the desk endpoints (list, read, claim, release, transfer, end, hold, wrap-up,
  review, brief, callback-done, caller-history, metrics) and officer presence.
- Two processes still serve chat, with the same memory as today.
- Restarting either process never ends calls the other holds.

**Assumed, not said.** Chat may also be answered by the call process (it adds
chat capacity; calls and chat already share a process today). The container
stays one replica: a Space is one container.

**Out of scope.** More than one replica (durable call ownership: the rest of
G306); flag overrides that reach every process (recorded as a new gap); Crane
Cloud, which shares these files but is not deployed by this work.

## Design

### 1. Processes and routing

The container (`App/Dockerfile.cranecloud`) runs nginx in front of uvicorn under
supervisord, with Qdrant and Redis sidecars. Today one uvicorn program runs
`UVICORN_WORKERS` workers on 127.0.0.1:8081.

- **Two uvicorn programs** in `App/deploy/cranecloud/supervisord.conf`:
  - `uvicorn-chat` on 127.0.0.1:8081, `URA_PROCESS_ROLE=chat`, with
    `max(1, UVICORN_WORKERS - 1)` workers, computed in the program's shell
    command (supervisord cannot do arithmetic);
  - `uvicorn-calls` on 127.0.0.1:8082, `URA_PROCESS_ROLE=calls`, one worker,
    with `UVICORN_WORKERS`, `WORKERS` and `WEB_CONCURRENCY` all exported as 1 in
    its own environment (the receptionist's single-worker declaration reads all
    three, and the container may pass any of them in).

  With the Space's `UVICORN_WORKERS=2` that is one process each: the same two
  processes as today; keep it at 2 on the Space, because nginx splits chat one
  to one between the two programs, so a larger value would leave the one-worker
  call process carrying more than its share. Both start through one script,
  `App/deploy/cranecloud/uvicorn-role.sh <role>`, which sets the role's port,
  worker count and environment and keeps the `wait-for-qdrant.sh` wrapper and
  today's uvicorn flags. A blank or invalid `UVICORN_WORKERS` counts as 2.
- **Metrics directory.** Today's command empties `PROMETHEUS_MULTIPROC_DIR`
  before uvicorn starts. With two programs, one restarting would delete the
  other's live files. The script empties it once per container start, under
  `flock` and behind a marker file in the directory: the first program to start
  clears it, a restart of either program does not. (A separate one-shot
  supervisord program was considered; supervisord does not wait for it to
  finish before starting the next program.)
- **nginx** (`App/deploy/cranecloud/nginx.conf`):
  - upstream `api` = 8081 and 8082 (chat and everything else, as `backend` is
    today);
  - upstream `calls` = 8082 only;
  - a location placed before every other API location, matching exactly
    `^/(api/(v1/)?|v1/)(calls/stream|admin/calls(/.*)?|admin/officers/(me/)?presence)$`,
    proxied to `calls` with the `/api` prefix rewritten away as elsewhere, the
    WebSocket upgrade headers, and 3600 s read and send timeouts. The pattern
    needs a `v1/` or `api/` prefix: the frontend owns the page `/calls`.
    Ticket presence (`/v1/admin/tickets/{id}/presence`, a case-collision lock)
    stays on `api`.
  - `calls/stream` and `admin/calls/stream` leave today's WebSocket location.
- **Space Dockerfile** (`App/deploy/hf-space/Dockerfile`) gains
  `ENV VOICE_RECEPTIONIST_REPLICAS=1`: a Space is one container, so the call
  process is the deployment's only one and ends the rows its previous run left
  at start (ADR-0041) instead of leaving them on the lobby until the ceiling.

### 2. Roles inside the app

- `process_role()` (new, small module) reads `URA_PROCESS_ROLE`: `calls`,
  `chat`, or unset. Any other value is logged as an error and treated as unset,
  today's behaviour, so a typo cannot take calls down.
- **The call paths are listed once**, in Python, as the patterns nginx uses. A
  contract test holds the list, the app's routes and nginx's location to each
  other.
- **`calls` role.** The receptionist starts as today. Its program declares one
  worker and the Space one replica, so it is the sole process: the two-worker
  warnings and the "resume ticket single-use per worker" caveat no longer apply.
- **`chat` role.**
  - The receptionist does not start: no stale-call sweep (a chat restart cannot
    end live calls), no Pipecat check, no pre-rendered call speech.
  - `receptionist_available` is not set by this process, so the gauge and its
    alert reflect the process that takes calls.
  - **A call request that reaches it is refused, loudly.** A middleware matches
    the listed paths. HTTP gets 421 Misdirected Request; a WebSocket is
    accepted, sent `{"type": "error", "code": "unavailable"}` and closed 1013.
    Each is logged as an error and counted in
    `receptionist_misrouted_total{kind}` (`http` or `websocket`); a new alert
    fires on any increase.
  - `/ready`'s `capabilities.voice_receptionist` is the call process's own
    value, read from `http://127.0.0.1:8082/ready` with a 1 s timeout and a
    10 s cache; `unavailable: the call process is not answering` when it does
    not answer. The address comes from `URA_CALLS_PROCESS_URL`, set by
    supervisord for the chat program. No new endpoint.
- **Either role set:** a middleware adds `X-URA-Process: chat|calls` to every
  response, so a probe from outside can tell which process answered. It names
  the role only.
- **Unset role** (local development, tests, k8s, the root `Dockerfile`): no
  change in behaviour.

### 3. Failure handling

- **The call process stops.** Its calls end, as when a worker dies today. nginx
  answers call paths with 502 until supervisord restarts it; the caller's
  screen says the call could not connect or was cut off and offers to call
  again; the officers' consoles show "Live call updates paused" and reconnect
  with backoff (ADR-0042). On start it ends the rows its previous run left and
  flags callers who were with or waiting for an officer for a callback
  (`service_interrupted`, ADR-0041). Chat goes on through 8081; a chat request
  in flight on 8082 can fail once (nginx does not retry a POST).
- **The chat process stops.** Chat moves to 8082 through the `api` upstream;
  calls are untouched; its start sweeps nothing.
- **Timeouts.** All call paths get 3600 s. The officer audio socket and the
  staff streams fell into the generic location (nginx's 60 s default) and
  survived only because they are never idle that long.
- **A misrouted call request** (configuration drift) is refused, counted and
  alerted on (§2).
- **Kill switch.** `FLAG_VOICE_RECEPTIONIST` set as a Space secret restarts the
  Space and reaches both processes. An override through the admin API reaches
  only the process that answered it; the runbook says so (new gap).
- **Rollback.** Roll the Space to `sha-f5d79b7` with
  `ura-chatbot-roll-hf-space.yml`: that image runs one two-worker uvicorn and
  needs nothing from this change.
- **Startup.** Both programs wait for Qdrant and start in parallel.

### 4. Testing, verification, documentation

**Tests, each failing before its change** (`App/backend/tests/test_call_process_routing.py`
unless stated):

- `process_role()`: `calls`, `chat`, unset, and an invalid value (unset, error
  logged).
- The call-path list and the app's routes: every HTTP and WebSocket route under
  a call path is listed, and every listed pattern matches a real route.
- `nginx.conf`, read as text: the call location comes before every other API
  location and proxies to `calls`; `calls` holds 8082 only and `api` holds
  8081 and 8082; samples of every call path, with and without `api/` and `v1/`,
  match it; `/calls`, `/v1/chat`, `/v1/admin/flags` and ticket presence do not;
  it sets the upgrade headers and 3600 s timeouts.
- `supervisord.conf`: the two uvicorn programs start `uvicorn-role.sh` with
  their roles. `uvicorn-role.sh`, run with a dry-run switch: ports, roles and
  worker counts (including a blank or invalid `UVICORN_WORKERS`), and the
  metrics directory cleared on the first start only.
- Chat role: each call route is refused (HTTP 421; WebSocket error frame
  `unavailable` and close 1013) and counted; chat routes are not; the
  receptionist does not start and the gauge is not set; `/ready` mirrors the
  call process, says "not answering" when it does not, and caches.
- The header: present with each role, absent when unset.
- `App/deploy/cranecloud/verify-embedded-stores.sh` (run at every roll) also
  checks both uvicorn programs and runs `nginx -t` on the shipped config.
- Monitoring: a promtool case for the misroute alert; the counter in the
  monitoring catalogue test.

**Live, after the roll:** each call path answered by `X-URA-Process: calls`
(an unauthenticated request still gets the header); the call socket gives 101;
`/ready` shows calls ready; two role-tagged startups in the log and no
two-worker warning; the misroute counter at 0. **Not verifiable from here:** a
real officer handoff needs a staff account; the owner is asked to run one test
call.

**Documentation in the same PR:** ADR-0043 and its index entry; GAPS rows (G306
updated; new rows for per-process flag overrides and the multi-replica
remainder); the voice receptionist runbook (process roles, kill switch through
a secret, the probes, rollback); MONITORING.md (counter and alert); the API
reference (421 and the header); `.env.example` (`URA_PROCESS_ROLE`,
`URA_CALLS_PROCESS_URL`, `VOICE_RECEPTIONIST_REPLICAS`); an AGENTS.md invariant
(a new call route goes on the list); `governance/ai_risk_manifest.yaml`
`verified_by` pointers; the review plan's Phase 5 section.

**Delivery:** one PR into `dev`; CI and CodeRabbit resolved; merge; the roll and
the live checks above.

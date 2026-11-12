# ADR-0043: One call process — every call request reaches the process that holds the call, so officer handoffs work on a Space that keeps two workers

## Status

Accepted, 2026-11-12; on the Space the same day (`sha-4609a1c`, PR 718). Phase 5 of the
[11 November call receptionist review](../plans/receptionist-assurance-2026-11-11.md);
it follows [ADR-0042](0042-call-screens-say-what-happened.md). Design:
[receptionist-call-process-2026-11-12](../plans/receptionist-call-process-2026-11-12.md).
It mitigates G306 for one container, and records G612, G613 and G614.

## Context and Problem Statement

The Space runs two API worker processes: the service owner kept two on
2026-11-11 ([ADR-0041](0041-receptionist-starts-safely-fails-visibly.md)). A
call's state lives in the memory of the process its caller's socket reached:
the room, its desk lock, the claim timer, the event hub that feeds the officers'
consoles, and the in-browser audio bridge. An officer's console reaches either
process for each request and each socket, so a claim, a join, the live
transcript or the officer's audio reached the call's process about half the
time. ADR-0041 made the receptionist warn about it at every start and nothing
more: on the Space, officer handoffs and live call transcripts were unreliable.

Two facts about the container shaped the options.

- **nginx already sits in front of uvicorn** (`App/deploy/cranecloud/nginx.conf`),
  under supervisord, with Qdrant and Redis beside it. It can send a request to
  a process by its path.
- **A Space is one container**, so "one replica" is a fact of the deployment
  and the sweep ADR-0041 added can treat the call process as the only one.

Smaller problems on the same path: the officer's audio socket
(`/v1/admin/calls/{call_id}/audio`) matched only the generic API location and
had nginx's 60 s default read timeout, surviving only because it is never idle
that long; and the command that started uvicorn emptied the Prometheus
multiprocess directory on every start, which with two programs would let one
restarting delete the other's live files.

## Considered Options

Chosen by the service owner on 2026-11-12 from these four.

1. **One call process behind nginx.** Two one-worker uvicorn programs from the
   same app, chat on both, every call path sent to one of them, which is the
   only owner of call state. **Chosen.**
2. **A cross-process message bus over the container's Redis.** Call events, desk
   commands and officer audio relayed between workers, with call ownership held
   as leases. Rejected: far more code and far more failure modes, and the
   officer's audio would pass through a cache that evicts and does not persist
   (supervisord starts Redis with `--save ""`, `--appendonly no` and
   `--maxmemory-policy allkeys-lru`, `[program:redis]` in
   `App/deploy/cranecloud/supervisord.conf`, and the app treats it as optional,
   falling back to in-process memory when it is unreachable). It would also
   serve more than one replica later, which is why G613 stays open.
3. **In-app forwarding.** Each worker on its own internal port, proxying the
   requests and the WebSockets for a call another worker owns. Rejected: it is
   option 1 rebuilt inside the app, with more moving parts.
4. **One API worker** (`UVICORN_WORKERS=1` in the Space image). Every request
   reaches the one process, so nothing needs routing. Ruled out by the owner's
   decision of 2026-11-11 to keep two workers
   ([ADR-0041](0041-receptionist-starts-safely-fails-visibly.md)): it halves the
   chat's worker count.

## Decision Outcome

**Chosen: option 1, one call process behind nginx**, because it meets the
drivers with the least new code:

- officer handoffs and live transcripts must work with two API processes on the
  Space, and sending every call path to one process does that;
- the container keeps the same memory and the same chat capacity: with
  `UVICORN_WORKERS=2` it is the same two processes, one of them now the call
  process, and chat is still answered by both;
- it adds routing, not a protocol: nothing relays call state between processes,
  so the room, the lobby, the claim timers and the audio bridge are unchanged;
- it is the smallest change that can be verified from outside: every response
  names its process (`X-URA-Process`), so a probe sees which process answered a
  call path and whether its socket opens, without a staff account.

**Two uvicorn programs replace the one.** `uvicorn-chat` listens on
127.0.0.1:8081 with `URA_PROCESS_ROLE=chat` and `max(1, UVICORN_WORKERS - 1)`
workers; `uvicorn-calls` on 127.0.0.1:8082 with `URA_PROCESS_ROLE=calls`, one
worker and `UVICORN_WORKERS=1` in its own environment. A blank, non-numeric or
zero `UVICORN_WORKERS` counts as 2. With the Space's `UVICORN_WORKERS=2` that is
one process each, the same two processes as before. Both programs start through
`App/deploy/cranecloud/uvicorn-role.sh <role>`, which keeps the Qdrant
readiness wrapper (`wait-for-qdrant.sh`) and the flags the single program had.
Both start in parallel.

**The metrics directory is emptied once per container start, not once per
program start.** The script does it under `flock`, behind a `.reset` marker file
in the directory: the first program to start clears it, a restart of either
program does not. (A separate one-shot supervisord program was considered;
supervisord does not wait for it to finish before starting the next program.)
Every start also removes the `gauge_live*_<pid>.db` files of processes that are
dead, because a dead process's last live gauge (`receptionist_available`, a
connection count) would otherwise be read as current.

**nginx sends every call path to the call process.**

- Upstream `calls` is 8082 only; upstream `api` (everything else the API
  serves, as `backend` was) is 8081 and 8082. The call process serves the whole
  app, so chat may also be answered by it.
- The call location is placed before every other API location, matches exactly
  `^/(api/(v1/)?|v1/)(calls/stream|admin/calls(/.*)?|admin/officers/(me/)?presence)$`
  (`process_role.NGINX_CALL_LOCATION`), proxies to `calls` with the `/api`
  prefix rewritten away, passes the WebSocket upgrade headers, and has 3600 s
  read and send timeouts. It needs a `v1/` or `api/` prefix: `/calls` is the
  frontend's page. Ticket presence (`/v1/admin/tickets/{id}/presence`, a
  case-collision lock) stays on `api`.
- `calls/stream` and `admin/calls/stream` leave the old WebSocket location.

**The app knows its role** (`App/backend/app/process_role.py`). `URA_PROCESS_ROLE`
is `calls`, `chat` or unset; any other value is logged as an error and treated
as unset, so a typo cannot take calls down. Unset keeps today's behaviour:
local development, tests, Kubernetes and the root `Dockerfile` are unchanged.
The call paths are listed once, in Python (`CALL_PATH_RE`, the paths as the app
sees them after nginx strips `/api`), and a contract test holds that list, the
app's routes and nginx's location to each other.

- **The `calls` role** starts the receptionist as before. It declares one
  worker and, on the Space, one replica, so it is the sole process: the
  two-worker warning no longer applies to it.
- **The `chat` role** does not start the receptionist (so no stale-call sweep:
  a chat restart cannot end live calls; no Pipecat check; no pre-rendered call
  speech), does not run the availability gauge loop, and its `/metrics` scrape
  does not set `receptionist_available`, so the gauge and its alert reflect the
  process that takes calls.
- **A call request that reaches the chat role is refused loudly.** HTTP gets
  421 Misdirected Request with a JSON `detail`. A WebSocket is accepted, sent
  `{"type": "error", "code": "unavailable", "recoverable": false}` and closed
  1013. Each is logged at ERROR, naming the path, and counted in
  `receptionist_misrouted_total{kind}` (`http` or `websocket`), which is
  declared at zero for both kinds when the app starts so that `increase()` sees
  the first one. A new alert, `UraCallMisrouted`, fires on any increase over ten
  minutes.
- **The chat role's `/ready` mirrors the call process.**
  `capabilities.voice_receptionist` is read from `<URA_CALLS_PROCESS_URL>/ready`
  (default `http://127.0.0.1:8082`, set by `uvicorn-role.sh` for the chat
  program) with a 1 s timeout and a 10 s cache that holds a failure too. It
  says `unavailable: the call process is not answering` when nothing answers in
  time and `unavailable: the call process is not ready` when something answers
  that is not a `/ready` body. The probe's own `status` is unaffected: the chat
  does not depend on calls. No new endpoint.
- **With either role set, every response carries `X-URA-Process: chat|calls`**,
  so a probe from outside can tell which process answered. It names the role
  only. With no role set it is never sent. Each process also logs its role once
  at startup, at INFO (`Process role: calls`), and logs nothing while no role is
  set, so the two programs can be told apart in one container log.

**The Space declares one replica.** `App/deploy/hf-space/Dockerfile` gains
`ENV VOICE_RECEPTIONIST_REPLICAS=1`. The call process is then the deployment's
only one and ends every call row its previous run left at start (ADR-0041)
instead of leaving them on the lobby until a call's ceiling.

**The image checks what it ships.** `verify-embedded-stores.sh`, run in the
image build and available to run against a live pod after a roll, now checks
that both programs are supervised, that `uvicorn-role.sh` is installed and
executable, and that `nginx -t` accepts the shipped configuration.

**The kill switch is a Space secret.** `FLAG_VOICE_RECEPTIONIST` set as a Space
secret restarts the Space and reaches both processes. An override made through
the admin API (`PATCH /v1/admin/flags/{name}`) changes the registry of the one
process that answered it and writes a row the other process reads only at its
next start. That includes the calls kill switch: switching calls off through the
console can leave the call process taking calls. Recorded as G612; the runbook
says so. (Closed on 2026-11-12 by [ADR-0044](0044-flag-changes-reach-every-process.md):
every process now re-reads the stored overrides, and the console switch reaches
the call process.)

**Rollback.** Roll the Space to `sha-f5d79b7` with
`gh workflow run ura-chatbot-roll-hf-space.yml -f image_tag=sha-f5d79b7`. That
image runs one two-worker uvicorn and needs nothing from this change. The roll
commits that pin to `dev`, so the rollback lasts only until the next `dev` build
that touches the build paths: its roll moves the pin forward again automatically.

## Consequences

- **Officer handoffs and live transcripts on the Space no longer depend on which
  process answers.** The caller's socket, the officer's audio socket, the staff
  lobby and per-call streams, the desk endpoints and officer presence all go to
  the one process that holds the call. Not yet seen on the deployed Space: that
  needs a staff account and a test call.
- **Restarting the chat process cannot end a call**; its start sweeps nothing.
  **Restarting the call process ends its calls**, as a dead worker did before.
  Until supervisord brings it back, nginx answers call paths with 502, the
  caller's screen says the call could not connect or was cut off and offers to
  call again, and the officers' consoles show "Live call updates paused" and
  reconnect with backoff (ADR-0042). On start the call process ends the rows its
  previous run left and flags the callers who were with or waiting for an
  officer for a callback (`service_interrupted`, ADR-0041). Chat keeps being
  served through 8081.
- **A chat request in flight on the call process can fail once when it
  restarts**: nginx does not retry a POST. Every location that proxies to `api`
  sets `proxy_next_upstream error;`: with two servers, nginx's default (`error
  timeout`) would send a request that timed out to the other process, so its
  work was done twice and the caller waited for a 504 after the full timeout.
  Only a refused connection (a process that is down) moves on to the other one.
- **Chat load can reach the call process's one worker.** The `api` upstream
  lists both, without `backup`, so chat shares that worker's event loop with
  calls. It adds chat capacity (calls and chat shared a process before), and it
  means a slow chat request can compete with a call for the call process. The
  alternative, `backup` on 8082, would keep failover without sharing load; the
  design chose chat on both.
- **Chat has the same memory as before**: two processes on the Space, one of
  them the call process.
- **A drift between nginx and the call-path list is found, not hidden.** A call
  request that reaches the chat process is refused, logged at ERROR and counted,
  and `UraCallMisrouted` raises a warning where the counter is scraped. Nothing
  scrapes the Space (G614), so there the ERROR log is the only signal.
- **`/ready` on the chat process lags the call process by up to 10 s** and says
  "not answering" for at most that long after the call process comes up. It
  stays 200 while the call process is down: the call field is informational,
  and `/ready` answers 503 only when the model is unavailable.
- **`X-URA-Process` is absent on some responses**: on Starlette's own 500 error
  responses (they are sent outside the middleware) and on a WebSocket closed
  before it is accepted (a foreign origin, refused at the handshake). The 421
  comes from the outermost layer, so it carries the header and `detail` but none
  of the headers the inner security and CORS middleware add. It is an
  operator-facing error for something that should not happen.
- **Admin-API flag overrides reach one process (G612; closed by ADR-0044).** Use the Space secret
  for the calls kill switch. A stored override also beats a `FLAG_*` secret at
  the next start (overrides are replayed into the registry at start), and
  `GET /v1/admin/flags` reports only the answering process's registry. So the
  runbook sets the secret first, reads `/ready` (the call process's own value)
  and, if calls are still on, deletes the stored `voice_receptionist` override
  (`DELETE /v1/admin/flags/voice_receptionist`) and restarts the Space.
- **The call process declares its single worker in all three worker
  variables.** `uvicorn-role.sh` exports `UVICORN_WORKERS=1`, `WORKERS=1` and
  `WEB_CONCURRENCY=1` for it, whatever the container passed in (the image's own
  `UVICORN_WORKERS=2`, a Space secret), because the receptionist's declaration
  needs every one of them that is set to be 1. The launcher's own worker count is
  a variable named `n_workers`, not `WORKERS`: a shell keeps an imported variable
  exported, so a local `WORKERS` would have been overwritten, and leaked into the
  server, only by accident. The chat process inherits the three untouched.
- **Keep `UVICORN_WORKERS=2` on the Space.** The chat process gets
  `max(1, N − 1)` workers, but nginx splits chat one to one between the two
  programs, so raising N adds workers to the chat process only and the
  one-worker call process carries more than its share of chat.
- **`python -m app.production_readiness` run in a container shell reports a G36
  worker error that does not apply.** It reads the image's `UVICORN_WORKERS=2`;
  the call process, the only one that starts the receptionist, runs one worker
  and exports all three variables as 1 itself.
- **No metrics alert notices a dead call process on the Space (G614).** The
  Prometheus in `monitoring/` scrapes the compose stack, not the Space;
  `UraApiDown` is satisfied by either process; `receptionist_available` goes stale
  or absent when only the call process is down. Mitigated 2026-11-12 by the roll
  workflow (it fails while `/ready` reports the call process down) and the hourly
  keepalive check. Open: a chat-role gauge exported from the cached call-process
  status plus an alert, when the Space is scraped.
- **More than one replica is still not supported (G613).** Call ownership is in
  one process's memory, so a second replica would split calls across processes
  that cannot see each other. G306's remaining open part (durable ownership,
  process-loss and drain drills) is G613.
- **The Crane Cloud image is built from the same files**, so its next build runs
  the two roles too. It was not deployed or tested there, and it does not set
  `VOICE_RECEPTIONIST_REPLICAS`, so its call process keeps a previous run's
  young rows until they are older than a call may last (ADR-0041).
- No new flag or model. One new response header, one new refusal (421 and
  1013), one new counter, one new alert rule, one new environment variable
  (`URA_PROCESS_ROLE`) and one address variable (`URA_CALLS_PROCESS_URL`).

## Confirmation

`App/backend/tests/test_call_process_routing.py`, each test failing before its
change:

- the role (`calls`, `chat`, unset, an invalid value logged once), the call-path
  list against every HTTP and WebSocket route in the app and against the exact
  nginx text;
- the chat role refusing each kind of call request (HTTP 421; WebSocket error
  frame and close 1013), including a query string and a trailing slash, and
  counting them, with both kinds exported at zero before the first;
- chat routes untouched in the chat role, call paths served in the calls role,
  no header and no refusal with no role or a mistyped one, and the middleware
  as the outermost layer;
- the real lifespan: no receptionist start and no gauge in the chat role, the
  start as before with no role, and the role logged once at startup (none when
  unset);
- `/ready` in the chat role mirroring the call process, saying "not answering"
  and "not ready" (a 503, a body that is not JSON), caching for ten seconds,
  recovering within ten seconds of the call process coming up, and costing one
  timeout when the call process accepts and never answers (over a real local
  socket).

`App/backend/tests/test_call_process_container.py`: `uvicorn-role.sh` run with
`URA_DRY_RUN=1` for ports, roles, environment, the worker count for each
`UVICORN_WORKERS` (blank, `abc`, `0`, `1`, `4`) and the launch itself; the call
process exporting `UVICORN_WORKERS`, `WORKERS` and `WEB_CONCURRENCY` as 1 whatever
the container passes in, and the chat process leaving them alone; the
metrics directory cleared on the first start only, with two roles starting
together clearing once, and a restart dropping only dead processes' live gauge
files; `supervisord.conf` parsed, with both programs keeping what the single one
had; `nginx.conf` read as text (the call location first among the location
blocks, `calls` holding 8082 only, `api` holding both, samples of every call
path matching, `/calls`, `/v1/chat`, `/v1/admin/flags` and ticket presence not
matching, the upgrade headers and the 3600 s timeouts, and every location that
proxies to `api` setting `proxy_next_upstream error;`); `INTERNAL_API_URL` for
Next.js naming nginx (8080), not an API process; the Space declaring one
replica; the image shipping the script executable and checking both programs
and `nginx -t`.

Deployment checks (G614): the roll workflow's "Confirm the app answers" step
polls `/ready` after `/health` for about 5 minutes and fails while
`capabilities.voice_receptionist` starts with `unavailable: the call process`;
the hourly keepalive workflow runs the same check three times. Both pass on
any other value, `off` included, and on an image that does not report it (the
rollback image). Their shell was run here against a local stub of `/ready` (call
process down on every attempt, down and then up, `off`, a missing field, no
answer). The workflows have since run. The two rolls of 2026-11-12 (04:42 and
08:58 UTC) and a keepalive run at 09:41 UTC each read
`voice_receptionist → ready` on the first attempt.

Monitoring: `tests/test_monitoring_config.py` (the series the rule names is one
the code exports; the runbook anchor exists) and promtool cases in
`monitoring/tests/alerting-rules.test.yml`, one that fires with its `kind` and
one that stays quiet when both kinds are at zero. The promtool cases could not
be run locally (no promtool); the `Monitoring config` CI job runs them.

On the Space, 2026-11-12 (`sha-4609a1c`, again on `sha-7dfd010`), using the live
probes in the [receptionist runbook](../runbooks/voice-receptionist-demo.md):

- every call path is answered with `X-URA-Process: calls`, and both call
  sockets open (101);
- an `Upgrade: h2c` request is not passed on;
- `/health` and `/ready` answer from both processes, with `voice_receptionist`
  reading `ready`;
- the log shows one `Process role: chat`, one `Process role: calls`, both
  programs running on 8081 and 8082, no two-worker warning and no misroute;
- chat answers from both processes.

Not verified: `nginx -t` on the shipped file (the tests read it as text; the
image build runs the real check and passed), a real officer handoff (it needs a
staff account; the owner is asked to run one test call), and Crane Cloud.

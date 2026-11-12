# One Call Process (G306) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every call request on the two-process Space reaches the one process that holds the call, so officer handoffs and live transcripts work.

**Architecture:** The container's single two-worker uvicorn becomes two one-worker programs started by one script: `chat` (8081) and `calls` (8082). nginx sends chat to both and every call path to `calls`. Inside the app, a role read from the environment decides whether the receptionist starts, refuses call requests that reach the wrong process, and makes `/ready` report the call process's state.

**Tech Stack:** FastAPI/Starlette (pure ASGI middleware), httpx (sync, already a dependency), supervisord, nginx, POSIX sh with `flock`, promtool, pytest/unittest.

**Spec:** [receptionist-call-process-2026-11-12.md](receptionist-call-process-2026-11-12.md)

## Global Constraints

- Role variable `URA_PROCESS_ROLE`: exactly `calls` or `chat`; anything else is logged as an error and treated as unset; unset keeps today's behaviour everywhere.
- Ports: `chat` 127.0.0.1:8081 with `max(1, N - 1)` workers where N is `UVICORN_WORKERS` (blank or not a positive integer counts as 2); `calls` 127.0.0.1:8082 with one worker and `UVICORN_WORKERS=1` in its environment.
- `URA_CALLS_PROCESS_URL`, default `http://127.0.0.1:8082`.
- nginx call location, exactly: `^/(api/(v1/)?|v1/)(calls/stream|admin/calls(/.*)?|admin/officers/(me/)?presence)$`. App-side (after nginx strips `/api`): `^/(v1/)?(calls/stream|admin/calls(/.*)?|admin/officers/(me/)?presence)$`.
- Chat-role refusal: HTTP 421 Misdirected Request; WebSocket accepted, sent `{"type": "error", "code": "unavailable"}`, closed 1013. Counter `receptionist_misrouted_total` with label `kind` = `http` | `websocket` (exported as `ura_receptionist_misrouted_total`).
- `/ready` in the chat role: `capabilities.voice_receptionist` read from `<URA_CALLS_PROCESS_URL>/ready`, 1 s timeout, 10 s cache; `unavailable: the call process is not answering` when it does not answer.
- Response header `X-URA-Process: chat|calls` whenever a role is set; never when unset.
- Python 3.11 runs the Space and CI: any new async wait uses `asyncio.timeout`, not `asyncio.wait_for`.
- Commit and push as mpairwe7, no Co-Authored-By or Claude-Session trailers; push with `GH_TOKEN="$(gh auth token --user mpairwe7)"`; never deploy to Crane Cloud; never edit the Space directly (the roll pushes the Dockerfile).

## Review Focus

- `UVICORN_WORKERS` blank, `abc`, `0`, `1`, `4` → chat workers 1, 1, 1, 1, 3; calls always 1 (test in Task 4).
- The call process's `/ready` answers 503 or a non-JSON body → chat `/ready` says `unavailable: the call process is not ready`, not a crash and not `ready` (test in Task 3).
- A call path with a query string or a trailing slash (`/v1/admin/calls?status=live`, `/v1/admin/calls/`) is still a call path for nginx and the middleware (tests in Tasks 1, 2 and 4).
- The chat process boots before the call process is up → `/ready` reports "not answering" and answers again within 10 s of the call process coming up, never blocking for more than the 1 s timeout (test in Task 3).
- A program restart inside a running container must not clear the metrics directory; a container start must (test in Task 4).

---

### Task 1: The role and the call paths (`app/process_role.py`)

**Files:**
- Create: `App/backend/app/process_role.py`
- Test: `App/backend/tests/test_call_process_routing.py`

**Interfaces:**
- Produces:
  - `ROLE_ENV = "URA_PROCESS_ROLE"`
  - `process_role() -> str` → `"calls"`, `"chat"` or `""`; reads the environment on every call (tests patch it).
  - `CALL_PATH_RE: re.Pattern[str]` (app-side pattern above), `NGINX_CALL_LOCATION: str` (nginx pattern above, the exact text Task 4 puts in nginx.conf).
  - `is_call_path(path: str) -> bool` (matches `CALL_PATH_RE` against the path only; callers pass `scope["path"]`).
  - `calls_process_url() -> str`.

- [ ] **Step 1: Write the failing tests** in a `ProcessRoleTests` class:
  - `test_the_role_is_read_from_the_environment`: `calls` → `"calls"`, `chat` → `"chat"`, unset → `""`, `" Calls "` → `"calls"` (trimmed, lower-cased).
  - `test_an_unknown_role_is_unset_and_logged`: `URA_PROCESS_ROLE=call` → `""` and an ERROR record from `app.process_role`.
  - `test_call_paths`: `is_call_path` true for `/v1/calls/stream`, `/v1/admin/calls`, `/v1/admin/calls/`, `/v1/admin/calls/stream`, `/v1/admin/calls/c1/claim`, `/v1/admin/calls/c1/audio`, `/v1/admin/officers/presence`, `/v1/admin/officers/me/presence`, `/admin/calls`, `/admin/calls/metrics`; false for `/calls`, `/v1/chat`, `/v1/admin/flags`, `/v1/admin/tickets/t1/presence`, `/v1/admin/officers`, `/v1/calls`.
  - `test_every_call_route_is_a_call_path_and_every_pattern_has_a_route`: walk `app.main.app.routes` (HTTP and WebSocket); every route whose path starts with `/v1/calls`, `/v1/admin/calls`, `/admin/calls` or contains `/officers/` and `presence` satisfies `is_call_path` (path parameters substituted with `x`); and each of the three alternatives in `CALL_PATH_RE` matches at least one real route.
  - `test_the_call_process_address`: unset → `http://127.0.0.1:8082`; set → the value without a trailing slash.
- [ ] **Step 2: Run them and see them fail.** `cd App/backend && pytest tests/test_call_process_routing.py -q` → `ModuleNotFoundError: No module named 'app.process_role'`.
- [ ] **Step 3: Implement `app/process_role.py`** with the interfaces above; the invalid-role error is logged once per distinct value.
- [ ] **Step 4: Run them and see them pass.**
- [ ] **Step 5: Commit** `feat(calls): the process role and the call paths (G306)`.

### Task 2: Refuse misrouted call requests; name the process (`ProcessRoleMiddleware`)

**Files:**
- Modify: `App/backend/app/process_role.py` (add the middleware and the counter)
- Modify: `App/backend/app/main.py` (register it last, so it is the outermost middleware, after the `@app.middleware("http")` block near line 1103)
- Test: `App/backend/tests/test_call_process_routing.py`

**Interfaces:**
- Consumes: Task 1's `process_role`, `is_call_path`.
- Produces: `class ProcessRoleMiddleware` (pure ASGI: `__init__(self, app)`, `async __call__(self, scope, receive, send)`); `MISROUTED_METRIC = "receptionist_misrouted_total"`; `declare_metrics() -> None` (records 0 for both kinds, called from `__init__`).

- [ ] **Step 1: Write the failing tests** in a `ChatRoleRefusalTests` class using `TestClient(app.main.app)` with `URA_PROCESS_ROLE` patched per test:
  - `test_a_call_request_reaching_the_chat_process_is_refused_421`: `GET /v1/admin/calls?status=live` and `POST /v1/admin/calls/c1/claim` → 421, JSON `detail` naming the call process, the counter for `kind=http` up by one each (read from `metrics` the way `tests/test_receptionist_startup_readiness.py` reads `receptionist_calls_refused_total`).
  - `test_a_call_socket_reaching_the_chat_process_is_told_and_closed_1013`: `websocket_connect("/v1/calls/stream")` → first frame `{"type": "error", "code": "unavailable", ...}`, then close code 1013; counter `kind=websocket` up by one.
  - `test_chat_is_not_refused_in_the_chat_role`: `GET /health` → 200 with `X-URA-Process: chat`.
  - `test_the_calls_role_serves_call_paths`: role `calls`, `GET /v1/admin/calls` → not 421 (401/403 without auth is fine), header `X-URA-Process: calls`.
  - `test_no_header_and_no_refusal_without_a_role`: unset → `GET /v1/admin/calls` not 421, no `X-URA-Process` header.
- [ ] **Step 2: Run them and see them fail** (no 421, no header).
- [ ] **Step 3: Implement the middleware.** HTTP: wrap `send` to add the header on `http.response.start`; in the chat role, answer call paths itself with 421. WebSocket: in the chat role, on a call path, consume `websocket.connect`, send `websocket.accept`, the error frame, then `websocket.close` with 1013; otherwise pass through. Log each refusal at ERROR with the path. Register with `app.add_middleware(ProcessRoleMiddleware)` as the last `add_middleware` call.
- [ ] **Step 4: Run them and see them pass**, then `pytest tests/test_receptionist_call_screens.py tests/test_receptionist_ws.py -q` (unset role: unchanged).
- [ ] **Step 5: Commit** `feat(calls): a call request that reaches the chat process is refused and counted (G306)`.

### Task 3: The chat process does not run calls; `/ready` reports the call process

**Files:**
- Modify: `App/backend/app/process_role.py` (add `calls_process_status`)
- Modify: `App/backend/app/main.py`: the receptionist start (`if _flags.is_enabled("voice_receptionist"):` near line 744), the `_receptionist_gauge_loop` task creation (near line 869), `_receptionist_status()` (near line 1251)
- Test: `App/backend/tests/test_call_process_routing.py`

**Interfaces:**
- Consumes: Task 1's `process_role`, `calls_process_url`.
- Produces: `calls_process_status(*, timeout: float = 1.0, ttl: float = 10.0) -> str`; `_reset_status_cache() -> None` (tests).

- [ ] **Step 1: Write the failing tests** in a `ChatRoleStartupTests` class:
  - `test_the_chat_process_does_not_start_the_receptionist`: role `chat`, flag on, run the app lifespan (`with TestClient(app.main.app):`) with `app.receptionist.start` patched → not called; role `calls` → called once.
  - `test_the_chat_process_leaves_the_availability_gauge_alone`: role `chat`, lifespan run with `receptionist.refresh_gauge` patched → not called.
  - `test_ready_mirrors_the_call_process` (with `httpx.get` patched): call process body `{"capabilities": {"voice_receptionist": "ready"}}` → `"ready"`; `"off"` → `"off"`.
  - `test_ready_says_when_the_call_process_is_not_answering`: `httpx.ConnectError` and `httpx.TimeoutException` → `"unavailable: the call process is not answering"`; the request is made with `timeout=1.0`.
  - `test_ready_says_when_the_call_process_is_not_ready`: status 503, or 200 with a non-JSON body, → `"unavailable: the call process is not ready"`.
  - `test_the_answer_is_cached_for_ten_seconds`: two calls within 10 s (patch `time.monotonic`) → one request; after 10 s → a second.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** `calls_process_status` uses `httpx.get(f"{calls_process_url()}/ready", timeout=timeout)` and a module-level `(value, expires_at)` cache keyed on `time.monotonic()`. In `main.py`: skip `receptionist.start(...)` and the call speech pre-render when `process_role() == "chat"`; create the gauge task only when the role is not `chat`; `_receptionist_status()` returns `calls_process_status()` in the chat role.
- [ ] **Step 4: Run them and see them pass**, then `pytest tests/test_receptionist_startup_readiness.py tests/test_production_hardening.py -q`.
- [ ] **Step 5: Commit** `feat(calls): the chat process leaves calls to the call process and reports it on /ready (G306)`.

### Task 4: The container runs two roles and routes calls to one

**Files:**
- Create: `App/deploy/cranecloud/uvicorn-role.sh`
- Modify: `App/deploy/cranecloud/supervisord.conf` (replace `[program:uvicorn]` with `[program:uvicorn-chat]` and `[program:uvicorn-calls]`; update the header comment's process list)
- Modify: `App/deploy/cranecloud/nginx.conf`
- Modify: `App/Dockerfile.cranecloud` (COPY the script to `/usr/local/bin/uvicorn-role.sh`, executable)
- Modify: `App/deploy/hf-space/Dockerfile` (add `ENV VOICE_RECEPTIONIST_REPLICAS=1` after `FROM`)
- Modify: `App/deploy/cranecloud/verify-embedded-stores.sh`
- Test: `App/backend/tests/test_call_process_container.py`

**Interfaces:**
- Consumes: Task 1's `NGINX_CALL_LOCATION` (the test imports it and asserts nginx.conf contains it verbatim).
- Produces: `uvicorn-role.sh chat|calls`. Reads `UVICORN_WORKERS`, `LOG_LEVEL`, `PROMETHEUS_MULTIPROC_DIR`; exports `URA_PROCESS_ROLE`, and for `calls` `UVICORN_WORKERS=1`, for `chat` `URA_CALLS_PROCESS_URL=http://127.0.0.1:8082`; clears `*.db` in the metrics directory under `flock "$DIR/.lock"` only when `$DIR/.reset` is absent, then creates it; then `exec /usr/local/bin/wait-for-qdrant.sh /opt/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port <8081|8082> --workers <n> --log-level "$LOG_LEVEL" --proxy-headers --forwarded-allow-ips=*`. With `URA_DRY_RUN=1` it prints the exported variables and the command instead of exec'ing (the reset still runs). An unknown role exits 2.

- [ ] **Step 1: Write the failing tests:**
  - `test_the_script_starts_each_role_on_its_port` (subprocess `sh uvicorn-role.sh <role>` with `URA_DRY_RUN=1` and a temporary metrics directory): chat → `--port 8081`, `URA_PROCESS_ROLE=chat`, `URA_CALLS_PROCESS_URL=http://127.0.0.1:8082`; calls → `--port 8082 --workers 1`, `UVICORN_WORKERS=1`, `URA_PROCESS_ROLE=calls`; `bogus` → exit 2.
  - `test_chat_workers_are_one_fewer_than_the_setting`: `UVICORN_WORKERS` `""`, `abc`, `0`, `1`, `2`, `4` → chat `--workers` 1, 1, 1, 1, 1, 3.
  - `test_the_metrics_directory_is_cleared_once_per_container_start`: first run deletes a planted `counter_1.db` and creates `.reset`; a second run with a new planted file keeps it.
  - `test_supervisord_runs_both_roles`: parse `supervisord.conf` with `configparser`; `program:uvicorn-chat` and `program:uvicorn-calls` exist, their `command`s run `/usr/local/bin/uvicorn-role.sh chat|calls`, `program:uvicorn` is gone, and no command contains `-delete`.
  - `test_nginx_sends_every_call_path_to_the_call_process` (read `nginx.conf` as text): `upstream calls` lists only `127.0.0.1:8082`; `upstream api` lists 8081 and 8082; no `upstream backend` remains; a `location ~ <NGINX_CALL_LOCATION>` block exists, comes before every other `location ~` block, has `proxy_pass http://calls;`, `rewrite ^/api/(.*)$ /$1 break;`, the `Upgrade`/`Connection` headers, `proxy_read_timeout 3600s;` and `proxy_send_timeout 3600s;`; `re.fullmatch` of the pattern accepts `/api/v1/calls/stream`, `/v1/calls/stream`, `/api/admin/calls`, `/v1/admin/calls/`, `/api/v1/admin/calls/c1/audio`, `/v1/admin/officers/me/presence` and rejects `/calls`, `/calls/abc`, `/v1/chat`, `/v1/admin/flags`, `/v1/admin/tickets/t1/presence`; the old WebSocket location no longer names `calls/stream`.
  - `test_the_space_declares_one_replica`: `App/deploy/hf-space/Dockerfile` has `ENV VOICE_RECEPTIONIST_REPLICAS=1`.
  - `test_the_image_check_covers_both_roles_and_nginx`: `verify-embedded-stores.sh` contains `program:uvicorn-chat`, `program:uvicorn-calls` and `nginx -t`.
- [ ] **Step 2: Run them and see them fail.** `cd App/backend && pytest tests/test_call_process_container.py -q`.
- [ ] **Step 3: Implement** the script, the two programs (each keeps today's `directory`, `environment` (with `PROMETHEUS_MULTIPROC_DIR`), `autostart`, `autorestart`, `priority=20`, `startsecs=5`, `startretries=3` and stdout/stderr lines), the nginx upstreams and location (and switch the other API locations from `backend` to `api`), the Space `ENV`, the Dockerfile `COPY`, and in `verify-embedded-stores.sh` a block that fails unless supervisord has both programs and `nginx -t -q -c /etc/nginx/nginx.conf` passes.
- [ ] **Step 4: Run them and see them pass.** Also `sh -n App/deploy/cranecloud/uvicorn-role.sh` and, if Docker is available, `docker run --rm -v "$PWD/App/deploy/cranecloud/nginx.conf:/etc/nginx/nginx.conf:ro" nginx:1.27-alpine nginx -t`.
- [ ] **Step 5: Commit** `feat(deploy): two API roles, every call path to the call process (G306)`.

### Task 5: Alert on misrouted call requests

**Files:**
- Modify: `monitoring/alerting-rules.yml` (group `ura_call_receptionist`)
- Modify: `monitoring/tests/alerting-rules.test.yml`
- Modify: `docs/MONITORING.md` (the call receptionist metrics table and alert list)

- [ ] **Step 1: Write the failing promtool cases:** `ura_receptionist_misrouted_total{kind="http"}` rising by 1 within 10 min → `UraCallMisrouted` with `severity: warning`, `team: backend`, label `kind`, the summary and description below, `runbook_url` `…/docs/MONITORING.md#call-receptionist`; flat at 0 → no alert.
- [ ] **Step 2: Run** `promtool test rules monitoring/tests/alerting-rules.test.yml` → fails (no such alert).
- [ ] **Step 3: Add the rule:** `expr: sum by (kind) (increase(ura_receptionist_misrouted_total[10m])) > 0`; summary "A call request reached the chat process"; description "nginx sent a {{ $labels.kind }} call request to the chat process, which refused it: the routing in nginx.conf and the call-path list have drifted (ADR-0043)." Add the counter row and the alert to `docs/MONITORING.md`.
- [ ] **Step 4: Run** promtool and `pytest tests/test_monitoring_config.py -q` from the repo root → pass.
- [ ] **Step 5: Commit** `feat(monitoring): alert when a call request reaches the chat process (G306)`.

### Task 6: Living documentation

**Files:**
- Create: `docs/adr/0043-one-call-process.md` (MADR 4.0: Context and Problem Statement, Considered Options (the three approaches from the spec), Decision Outcome, Consequences, Confirmation)
- Modify: `docs/adr/README.md` (index row), `docs/GAPS_AND_AGENTIC_ROADMAP.md` (G306 row: "Mitigated 2026-11-12 (ADR-0043) for one container"; new G612: admin-API flag overrides reach only the process that answers; new G613: more than one replica needs durable call ownership; version line 3.18), `docs/runbooks/voice-receptionist-demo.md` (process roles; kill switch as a `FLAG_VOICE_RECEPTIONIST` secret; probes: `X-URA-Process` per call path, the call socket, `/ready`; rollback to `sha-f5d79b7`), `docs/API_REFERENCE.md` (421 and the header), `.env.example` (`URA_PROCESS_ROLE`, `URA_CALLS_PROCESS_URL`, `VOICE_RECEPTIONIST_REPLICAS`), `AGENTS.md` (invariant: a new call route goes on `process_role.CALL_PATH_RE` and nginx's call location; the contract test enforces it), `governance/ai_risk_manifest.yaml` (`verified_by` → the two new test files), `docs/plans/receptionist-assurance-2026-11-11.md` (Phase 5 section)

- [ ] **Step 1: Write them.**
- [ ] **Step 2: Run the gates:** `python3 governance/compliance_check.py`, `cd App/backend && PYTHONPATH=. python -m app.freshness --check` (drift 0), `python3 threat-model/validate_threats.py`, `ruff check ml/ App/backend/ --select=E9,F63,F7,F82`.
- [ ] **Step 3: Commit** `docs: one call process (ADR-0043, G306, G612, G613)`.

### Task 7: Ship and verify

- [ ] **Step 1:** Full local runs: every `App/backend/tests/test_receptionist_*.py` plus the two new files (one process, `-o faulthandler_timeout=90`), the repo-root `tests/` monitoring and supply-chain tests, and the frontend suite only if a frontend file changed.
- [ ] **Step 2:** Push the branch (confirm `git ls-remote` matches `HEAD`), open the PR into `dev` with the body from the spec's Success and the test list; end the body with the PR attribution line.
- [ ] **Step 3:** Resolve every CI failure at its root and every CodeRabbit thread (reply, then resolve by thread id); re-request reviews after the rate limit.
- [ ] **Step 4:** Squash-merge; confirm no trailers in the merge commit.
- [ ] **Step 5:** After the roll: the Space pinned to the new `sha-…`, RUNNING on the repo head; `/health` and `/ready` 200 with `voice_receptionist: ready`; `X-URA-Process: calls` on `GET /api/v1/admin/calls`, `POST /api/v1/admin/calls/x/claim` and `GET /api/v1/admin/officers/presence`, and `chat` or `calls` on `/health`; the call socket gives 101; the log shows two role-tagged startups, no "Voice receptionist is on with UVICORN_WORKERS=2" warning, and the known tracebacks only.
- [ ] **Step 6:** Ask the owner to run one test call handed to an officer; update the `hf-space-receptionist-stopgap` memory.

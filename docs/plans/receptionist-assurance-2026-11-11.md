# Call receptionist assurance review — 11 November 2026

Status: phases 1 to 6 implemented and on the Space; phase 7 part A (G304, G610, G614) implemented on `fix/receptionist-p7-sockets-wrapup-alert-2026-11`. Phase 5 (G306 for one container, PR 718) has been live since 2026-11-12 (`sha-4609a1c`), and its live probes passed; a real officer handoff on the Space has not been run yet.
Baseline: `dev` at `9a5043db`. Decisions: [ADR-0039](../adr/0039-call-handoff-and-caller-safety.md) (phase 1), [ADR-0040](../adr/0040-call-records-and-accountability.md) (phase 2), [ADR-0041](../adr/0041-receptionist-starts-safely-fails-visibly.md) (phase 3), [ADR-0042](../adr/0042-call-screens-say-what-happened.md) (phase 4), [ADR-0043](../adr/0043-one-call-process.md) (phase 5), [ADR-0044](../adr/0044-flag-changes-reach-every-process.md) (phase 6), [ADR-0045](../adr/0045-call-sockets-wrap-ups-and-a-dead-call-process.md) (phase 7 part A).
The [30 October review](receptionist-assurance-2026-10-30.md) covered teardown,
media isolation and draft evidence; this one covers the whole call flow: what the
AI says, the handoff to an officer, the officer's desk, the two browser clients,
and startup and readiness. It is a code and test review. No call was placed
through a real microphone, SFU or speech backend, and it is not a penetration
test or a legal compliance certificate.

## How the review was done

Four read-only passes over `App/backend/app/receptionist/` (16,300 lines) and the
call code in `App/frontend/src/`: the call lifecycle (sockets, room state,
persistence, transfer, desk), the answer path, media and readiness, and the two
clients. A finding was kept when it could be traced through the code and, for
most, reproduced with a failing test first. Each fix in this change has such a
test; it was watched failing before the fix was written.

## Reference basis

Sources were opened on 2026-11-11. PDFs were read directly; HTML pages were read
through a fetch tool that relays page text, so wording from them is as relayed.
A publication date is not the date it was checked. These are engineering
references, not a claim that foreign rules bind URA.

| Reference | What it is used for here |
| --- | --- |
| [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/) (December 2025; no later edition) | ASI03 identity and privilege (the transfer ticket's tenant, the reservation), ASI08 cascading failures (work on the event loop, record writes that gate the handoff), ASI09 human–agent trust (a promised officer). |
| [OWASP WebSocket Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/WebSocket_Security_Cheat_Sheet.html) | Close a socket when its session expires, limit message size and rate. Phase 2 (G304). |
| [NIST AI 600-1](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf) (July 2024), GV-6.2-006 and MS-4.2-004 | Test the fallback to a person; record where a person overrides the system. The handoff must work when the record of it cannot be written. |
| [W3C WCAG 2.2](https://www.w3.org/TR/WCAG22/) (also ISO/IEC 40500:2025), SC 2.2.1, 4.1.3 | A timed cut-off needs a warning and a way to extend; a status change is announced. Phase 4 (G602). |
| [ETSI EN 301 549 V4.1.1](https://www.etsi.org/deliver/etsi_en/301500_301599/301549/04.01.01_60/en_301549v040101p.pdf) (September 2026), 6.2.1.1–6.2.1.2 | Text usable alongside voice. The caller's typed turn exists; whether it reaches an officer on a bridged call is not yet tested. |
| [LiveKit tokens and grants](https://docs.livekit.io/frontends/reference/tokens-grants/) | Short token lifetimes; removal does not revoke a self-hosted token (G303 stays open). |
| [Pipecat releases](https://github.com/pipecat-ai/pipecat/releases) | 1.15.0 (29 October 2026) is current and is the version pinned; 2.0 has not shipped. `PipelineTask`/`PipelineRunner` are deprecated names since 1.3.0. No parameter removed in 1.0 is used. |
| [AWS Builders' Library: timeouts, retries and backoff with jitter](https://builder.aws.com/content/3EumjoZascWd1oZiEgL8ORlv3qE/timeouts-retries-and-backoff-with-jitter) | A deadline on every remote call (G579); idempotency on a repeated action (officer End, G569). |
| Uganda Data Protection and Privacy Act 2019, s.18 | Data is not kept longer than necessary. A purge that silently does nothing is a compliance fault, not a log line (G555, phase 2). |
| [Sunbird ASR model card](https://salt.sunbird.ai/models/asr-whisper-51-african-languages/) | Word error rates of 0.26 (Runyankole) and 0.20 (Acholi) against 0.04 (English) on its own small test sets: a spoken "goodbye" or "officer" is misheard often enough that ending a call on a pattern match must be conservative (G573). |

Not verified, and not relied on: ISO clause texts (paywalled), the Uganda 2021
Regulations and any UCC rule on announcing recorded calls, published latency
targets from Pipecat or LiveKit. OWASP published a 2026 edition of its Top 10
for LLM Applications in August with renumbered categories; the guardrail
documents here still cite the earlier numbering, which is outside this review.

## Phase 1 — handoff and caller safety (this change)

A caller is never stranded, misrouted, misled about an officer, or hung up on.

| Gap | What happened | What changed |
| --- | --- | --- |
| G568 | An officer's connection dropped and they did not return. The row stayed `bridged`, so the next officer's claim (a conditional update on a waiting status) was refused with "Another officer took this call". The caller waited, unanswerable, until the timeout. | Grace expiry writes the row back to `transferring`. Test: the next officer claims it. |
| G569 | The officer's End failed when the closing line could not be spoken or presence could not be written; the call stayed bridged. Two Ends said goodbye twice. | End hangs up whatever else fails. A second End joins the first. |
| G570 | A waiting caller in crisis was not raised to urgent on the officers' screens when the row write failed. | The lobby and call events go first; the row and ticket are best-effort. |
| G571 | A transfer's ticket was created under tenant `default`. The caller's own officers never saw it. | `open_transfer` passes the call's tenant. Test uses the real `ChatModel`. |
| G572 | "Let me connect you to an officer" was spoken before the queue flag and the staffing were checked, then "I can't transfer you". An officer was offered while handoffs were off. | The timeout and error lines are an apology only, in five locales, recorded as notices; the transfer says what happens next. No officer is offered while `ticket_queue` is off; a crisis still gets the helplines. |
| G573 | Calls ended on callers who had not asked: "How do I disconnect my EFRIS device?", "Officer please" at the review question, a crisis statement during the goodbye line. | A hang-up phrase counts only at the end of a short turn that is not a question. Speech during the goodbye keeps the call. Asking for a person is not a review answer. |
| G574 | An answer that finished after the caller asked for an officer was spoken over their wait, ending in "Would you like to speak to an officer?". | A finished generation is dropped when the call has left the AI. |
| G575 | With `spoken_figure_guard` on, a withheld answer raised `AttributeError` (`_say` does not exist), so G185 was not in effect. "Repeat that" then voiced the withheld text; "more detail" was never checked. | The written answer goes on screen, the caller is pointed to it, repeat replays the pointer, and "more detail" passes the same check. |
| G576 | A language switch whose re-transcription failed left the caller a filler and then silence; the turn was dropped. | The caller is asked to say it again, in the language they moved to. |
| G577 | Machine translation ran on the event loop: the Luganda fallback, and ticket creation on every non-English transfer, crisis transfers included. Every live call on the worker stalled for its length. | Both run in a worker thread. The transfer changes no call state off the loop, and a second request joins the first. |
| G578 | The at-risk monitor compared every pair of caller turns, on the event loop, on every turn written: 0.3 s a turn by the fortieth. It published `call.risk` and wrote the row every turn, changed or not. | Evaluated in a thread, one run at a time; each new turn is compared with at most 40 earlier ones; events only when level or signals change. |
| G579 | LiveKit room administration had no deadline, and one call site awaited it while holding the call's desk lock. | A 5 s deadline inside `revoke_participant` and `close_room`. |
| G580 | "Transfer to a specific officer" in the console was ignored by the server; the call went to the general queue. | The call is reserved for the named officer for the claim window, then opens to all. Enforced in the room and in the claim's conditional update. |
| G581 | The officer who passed a call on saved their wrap-up, and every console removed the still-waiting caller from the queue (`call.wrapped_up` was treated like `call.ended`). | The event says whether the call is live; consoles remove a call only on `call.ended`. |
| G582 | When the caller hung up first, the officer saw "The call ended." and never the wrap-up, so outcome, ticket action and satisfaction went unrecorded. End refused after the hang-up put the officer back on a dead call. | The server says the call ended before it closes the officer's socket and the media room; the console goes to wrap-up. |
| G583 | A passed-on or dropped call was requeued by code that wrote the transcript and the row first; a failed write stopped the lobby event and the caller's countdown. | The requeue reaches the lobby and starts the countdown whatever the record writes do. |

`App/backend/tests/test_receptionist_handoff_safety.py` holds the backend
regressions; the console's are in `App/frontend/src/__tests__/`.

## Phase 2 — records and accountability

What a call leaves behind is true, scoped and owned.

| Gap | What happened | What changed |
| --- | --- | --- |
| G585 | Crisis turns, transfers, repeats and goodbyes wrote no audit row. A Luganda row hashed English the caller never heard; a turn talked over or timed out was recorded `completed`. | The brain writes one row per call turn from the lines the caller was told, with a disposition. `generate(defer_audit=True)` hands its row back. |
| G305 | Any officer of the tenant could wrap up any call, a live one included; a second wrap-up replaced the first; a retried save opened a second ticket. | A wrap-up is accepted from an officer who handled the call, once they are off it. Each officer's note is kept. The ticket is linked before anything else. |
| G584 | Officer presence had no tenant: the board, the lobby event and the staffing count crossed tenants. `teams: 5` was stored and broke the count. | `officer_presence.tenant_id`, scoped reads and events, a validated update body. |
| G587 | The end-of-call summary stamped the ticket's SLA first response and replaced an officer's note; crisis-only calls were summarised as answered. | The summary writes a note only when there is none, as the system (`officer_touch=False`). |
| G304 | The officer's audio socket and a listener's outlived the session; no cap on staff sockets; one transcription task per officer utterance. | Close at token expiry (an officer on a call goes into the reconnect grace), admission caps, one transcription worker per leg. Logout before expiry is still not enforced. |
| G589, G590 | A listen socket never woke when its call ended. A turn written while the live transcript opened was lost. | The listener polls for the end of the bridge; the transcript subscribes before its snapshot. |
| G555 | On Postgres, with the voice tables absent, their error rolled back the resolved-ticket purge on every pass. | Each purge is its own transaction; the Postgres bootstrap creates the call tables. |
| G586, G588 | A Luganda calculator answer was spoken in English; a Luganda synthesis could add a figure unchecked. | Both go through, or fall back to, the guarded translation. |
| G591, G592 | A burst of turns started a model run each; the brain moved the call's language without telling the language policy. | At most two runs in flight; the policy adopts the brain's move. |

`App/backend/tests/test_receptionist_records_accountability.py` holds the
regressions. `DeferredAuditTests` runs the real `ChatModel`: the first version
of the deferred audit passed every test that used a double and broke every REST
chat turn, which only the root suite showed.

## Phase 3 — starts safely, fails visibly

[ADR-0041](../adr/0041-receptionist-starts-safely-fails-visibly.md). Tests:
`App/backend/tests/test_receptionist_startup_readiness.py`.

| Gap | What changed |
| --- | --- |
| G373 | `receptionist.start()` never raises: calls are refused, `/ready` gives the reason under `capabilities.voice_receptionist`, an error is logged, `ura_receptionist_available` is 0. The service owner chose to degrade over failing loud. |
| G593 | The worker gate reads `WORKERS`, `UVICORN_WORKERS` and `WEB_CONCURRENCY`: one must be set, and each that is set must be 1. |
| G594 | No speech model makes calls unavailable. A recognizer or voice failure reaches the brain: the caller is asked to repeat, and two in a row start a transfer (to an officer when the queue is on and staffed, otherwise the toll-free line or a callback). Silence after an unheard utterance ends as `speech_failed`. |
| G595 | `receptionist/telemetry.py`: availability, calls started, ended by reason, refused by reason, failures by stage, rows lost to a restart. Four alert rules with promtool cases. |
| G596 | Every call duration has a range; `validate()` and the production gate refuse a value outside it, and a running call reads the default instead. |
| G597 | Call sockets run the local-engine checks with the flag's real state. |
| G598 | A CI job installs the receptionist stack and runs its tests; `pip-audit` audits the file as the images install it. |
| G306 (mitigated) | A start ends rows older than a call may last, and every live row where one worker and one replica are declared; owed callbacks are flagged `service_interrupted`. |
| G599 (mitigated) | Low-bandwidth mode agrees to mu-law only and not on a media-room call; its checkpoint is off the event loop; resume tickets are never signed with the development secret in production. |
| G600 (mitigated) | Router closed with the call; audio decoded in a thread; live-caption buffer bounded; signer trims credentials; a call opens only in a call language. |

Left open by this phase, each for the service owner:

- **The production Space runs two workers, by the service owner's decision
  (2026-11-11)**, and is outside the gate (a Space secret overrides the base
  image's `APP_ENV=production`; calls are accepted there with no LiveKit
  configured, which the gate would refuse). Call handoffs there reach the
  wrong process about half the time until call state is shared (G306);
  `UVICORN_WORKERS=1` in `App/deploy/hf-space/Dockerfile` would fix that at the
  cost of half the chat's workers. (Phase 5 fixes it without changing the
  worker count: every call request now reaches the call process.)
- **`low_bandwidth_voice_codec` and `call_drop_resume` are on in the Space
  image** and no client uses either.
- **`RECEPTIONIST_TURN_TIMEOUT_S`** does not reach the turn-stop strategy; see
  "Not in scope".

## Phase 4 — caller and officer screens

[ADR-0042](../adr/0042-call-screens-say-what-happened.md). Tests:
`App/backend/tests/test_receptionist_call_screens.py`,
`App/frontend/src/__tests__/hooks/useCall.screens.test.tsx`,
`App/frontend/src/__tests__/components/officerCallScreens.test.tsx`.

| Gap | What changed |
| --- | --- |
| G601 | A microphone granted after the call ended is stopped and sends nothing; a late refusal is ignored; caller and officer. |
| G602 | `duration_warning` a minute before the ceiling (halfway through a ceiling of a minute or less) on every transport, to the caller and the officer; the call ends at the ceiling everywhere, even if the caller's socket stalls. |
| G603 | A refused LiveKit microphone keeps the call, muted, with the translated message; pressing the microphone retries. |
| G604 | The device check stops an earlier microphone and one granted after it closed. |
| G605 | Refusals accepted, explained with a code, closed with their code; the caller's screen says why and offers to call again. |
| G606 | Staff streams send `ready`; the transcript reconnects and shows when it is down; the lobby outage shows on every staff page. |
| G607 (mitigated) | The tone notice in all five languages; caller-facing errors translated from codes. Strings await first-language review. |
| G608 | The call dialog takes, keeps and returns focus. |
| G609 | Failed mute changes are reverted on screen; a new call starts clean. |
| G610 (mitigated) | Presence is `on_call` from claiming to ending; the server refuses `available` during the reconnect grace and a claim. |
| G611 | Mark-done and the review form show their failures. |

## Phase 5 — one call process

[ADR-0043](../adr/0043-one-call-process.md); design:
[receptionist-call-process-2026-11-12](receptionist-call-process-2026-11-12.md).
Tests: `App/backend/tests/test_call_process_routing.py`,
`App/backend/tests/test_call_process_container.py`.

The service owner keeps two API processes on the Space (2026-11-11). This phase
makes officer handoffs and live transcripts work on them without changing that:
the container's one uvicorn becomes a chat process and a single-worker call
process, and nginx sends every call path to the call process.

| Gap | What changed |
| --- | --- |
| G306 (mitigated for one container) | Two uvicorn programs started by `App/deploy/cranecloud/uvicorn-role.sh` (`uvicorn-chat` on 8081 with `max(1, UVICORN_WORKERS - 1)` workers, `uvicorn-calls` on 8082 with one); nginx's call location (placed first) sends the caller's socket, the staff lobby and per-call streams, the officer's audio socket, the desk endpoints and officer presence to the call process, with 3600 s timeouts; the chat process starts no receptionist, sets no availability gauge and reads the call process's `/ready`; the Space declares `VOICE_RECEPTIONIST_REPLICAS=1`. |
| G306 (misrouting) | A call request that reaches the chat process is refused (HTTP 421, or a WebSocket accepted, told `unavailable` and closed 1013), logged at ERROR, counted in `receptionist_misrouted_total{kind}` and alerted on by `UraCallMisrouted`. Every response names its process in `X-URA-Process`. |
| G595 | The call receptionist alert group has five rules; `UraCallMisrouted` is the new one. |
| G612 (new) | An admin-API flag override reaches only the process that answered it; the calls kill switch is now a `FLAG_VOICE_RECEPTIONIST` Space secret (runbook). |
| G613 (new) | More than one replica needs durable call ownership: the rest of G306. |
| G614 (new) | No metrics alert notices a dead call process on the Space; the roll workflow and the hourly keepalive workflow fail while `/ready` reports the call process down. |

Also changed: `verify-embedded-stores.sh` checks both programs and runs
`nginx -t`; the metrics directory is emptied once per container start, and a
start drops the dead processes' live gauge files; a test holds both supervisord
programs to the settings the single one had; one existing call-screens test no
longer races the clock's row write.

Left open by this phase:

- **A real officer handoff on the Space.** It needs a staff account, so the
  owner is asked to run one test call. The live probes passed on 2026-11-12
  (`sha-4609a1c`, and again on `sha-7dfd010`):
  - every call path is answered with `X-URA-Process: calls`, and both call
    sockets open (101);
  - `/ready` reads `ready` from both processes;
  - the log shows one process of each role and no misroutes.

  Rollback is a roll to `sha-f5d79b7`.
- **The call process restarting still ends its calls**, as a dead worker did.
- **G612** (closed by phase 6), **G613 and G614**, above.
- **Crane Cloud** builds from the same files and was not deployed or tested.

## Phase 6 — flag changes reach every process

[ADR-0044](../adr/0044-flag-changes-reach-every-process.md). Tests:
`App/backend/tests/test_flag_sync.py`,
`App/frontend/src/__tests__/app/adminFlags.test.tsx`.

After phase 5 the Space runs two API processes, and a flag changed from the
console reached only the one that answered. The calls kill switch is such a flag.

| Gap | What changed |
| --- | --- |
| G612 | Every process re-reads `flag_overrides` every `FLAG_SYNC_INTERVAL_SECONDS` (5 by default; 0 turns it off) and applies it with `FeatureFlags.apply_stored`. Only overrides that came from the store follow it; an override set in one process only is left alone. `PATCH` writes the store first, as `DELETE` does. In production, a stored `false` for a protected control is refused without exiting. An unreadable store keeps the flags. Both are logged once and counted. `GET /v1/admin/flags` reports `scope`, `sync_interval_seconds`, `synced_at` and `stored`. The console's note says a change reaches every API process within the interval. |

The runbook's kill switch now leads with the console switch, confirmed from
`/ready`. The Space secret stays as the fallback.

Left open by this phase:

- **Not seen on the Space yet.** After the roll, switch calls off from the console,
  read `/ready` until it says `off`, then switch them back on.
- **Replicas with a database each** still need `FLAG_*` on every replica.
- **G616 (new):** the Space's database is on the container's own disk with no
  persistent storage. Every restart and roll loses it: conversations, tickets,
  call records and owed callbacks, the audit ledger and stored flag overrides.
  The service owner decides. `ANALYTICS_DB_DIR` needs persistent storage
  whichever backend holds the analytics tables: the audit ledger and
  conversation memory stay SQLite-backed under it even with managed Postgres.

## Phase 7 — sockets, wrap-ups and a dead call process (part A)

[ADR-0045](../adr/0045-call-sockets-wrap-ups-and-a-dead-call-process.md). Part B
(G591, superseded model runs) follows in its own change because it touches the
generation path chat shares.

| Gap | What changed |
| --- | --- |
| G304 | Both call sockets drop a binary frame over 16 KiB, an odd-length one (mu-law exempt) and any frame over 64 000 B/s (burst 128 000) or 200 frames/s; the officer's text frames are held to 15 per 10 s. Drops are counted (`receptionist_frames_dropped_total{side,reason}`); only a five-second flood closes the socket, 1008. |
| G610 | A call that ends under an officer (time limit, a server end, their reconnect grace) moves them to `wrap_up` while their presence is still that call's; the heartbeat keeps `wrap_up` and returns `wrap_up_call_id` until the wrap-up is saved, and the console opens it, after a reload too. |
| G614 | The chat process exports `receptionist_call_process_up` (0 when the call process does not answer or is not ready), refreshed every 30 s and on `/ready`; `UraCallProcessDown` alerts after 5 minutes. |

Left open by this part:

- **The Space is not scraped**, so `UraCallProcessDown` cannot fire there (G614).
- **LiveKit control sockets'** text frames are not rate-limited (G304).
- **A call the officer passed on or lapsed off** is owed only once it ends (G610).
- **Pipecat's receive loop** closing a flooded caller was not run locally.

## Not in scope

Needs staging, real callers or an owner's decision: self-hosted token replay
(G303), five-language live speech acceptance (G307), Orpheus digit strings
(G84), end-of-turn timing (it needs measurement on real speech before the
default changes), the Pipecat `Worker` rename.

## Verification

Measured on 2026-11-11 in the phase 1 worktree, Python 3.11, with hosted models,
Qdrant and speech disabled:

- Receptionist-related backend tests (`-k "receptionist or call_carryover or
  phase39 or gap_closures or speech_normalization"`): 599 passed, 19 skipped
  (the Pipecat-dependent tests; the venv has no Pipecat, see G598).
- CI's root step, `pytest tests/`: 4798 passed, 21 skipped. One module,
  `tests/agents/test_cx_500_complex_ngrok_schema.py`, cannot be collected in
  this venv (`requests` is not installed); that is unrelated to this change.
- CI's backend step, `cd App/backend && pytest tests/ -q`: 3671 passed, 24
  skipped. One module, `tests/test_tracing_genai.py`, cannot be collected in
  this venv (`opentelemetry` is not installed); unrelated to this change.
- Frontend: 92 files, 856 tests passed; `tsc --noEmit` and ESLint on the changed
  files are clean.
- `governance/compliance_check.py`, `threat-model/validate_threats.py`,
  `tests/test_monorepo_architecture_governance.py`, the runtime package boundary
  test, `app.freshness --check` and CI's blocking Ruff selection: passed.
- One existing test, `test_conditional_draft_wrapup_does_not_overwrite_submitted_officer_wrapup`,
  used a fixed call id and failed on any re-run against the same database, on
  `dev` as well. It now uses a fresh id.

Phase 2, measured the same day. The laptop had by then dropped to battery power
(`powersave`, about 600 MHz of 3,100), so everything ran about five times
slower and two timing-dependent tests behaved differently; the pull request's
CI is the authoritative full run.

- Receptionist, presence, escalation and language tests (`-k "receptionist or
  call_carryover or phase39 or gap_closures or speech_normalization or presence
  or escalation or language"`): 807 passed, 19 skipped.
- `pytest tests/` (root step): 4797 passed, 21 skipped, 1 failed.
  The failure, `test_the_advisor_applies_the_adr_thresholds`, measures store
  latency and recommends a different backend when the machine is slow; it
  passes alone and is unrelated to this change.
- Backend step: the first 3,020 tests passed. The run then reached
  `test_retrieval_regression_gate.py::TestTheCorpusAnswersItself`, which asks
  all 1,120 FAQs their own question and needs about nine minutes per test on
  the throttled laptop (on `dev` as well); the remaining tests were left to CI.
- The first version of the deferred audit row broke every REST chat turn
  (a parameter not passed through) while every receptionist test passed,
  because those use a stand-in chat model. The root suite showed it;
  `DeferredAuditTests` now runs the real model.
- Governance, threat-model, architecture and CI's blocking Ruff selection: passed.

Phase 3, the same day, the laptop still on battery:

- Receptionist, readiness and speech tests in the usual environment
  (`-k "receptionist or gap_closures or production_readiness or orpheus or
  tts"`): 818 passed, 20 skipped.
- The same receptionist tests against **Pipecat 1.15.0**, in a separate
  environment built for it: 810 passed, none skipped. The first run there
  found four failures, all in tests CI had never run: three Orpheus doubles
  that did not take the `voice` argument `stream()` now has, and one sentinel
  assertion written before keyword mode voted from the transcript. The tests
  were corrected; the code was right.
- `tests/test_monitoring_config.py`: 145 passed. The promtool cases could not
  be run locally (no promtool); the `Monitoring config` CI job runs them.
- The receptionist requirements, resolved with the images' Pillow override
  (59 packages) and audited with `pip-audit`: no known vulnerabilities.
- Root tests for voice, readiness, monitoring, governance and the Space layer
  (`-k "voice_2026 or agentic_voice or receptionist or hf_space or monitoring
  or production or readiness or governance or toolathlon"`): 280 passed. The
  full root and backend suites were left to CI, for the reason above.
- One root test, `test_phase8_call_receptionist_multiturn_controls_normal_pace_and_text_turn`,
  wrote call turns without creating the call tables and passed only when an
  earlier test had; it failed in any run that started with a fresh database.
  It now creates them (fixed in the phase 2 pull request).

Phase 5, 2026-11-12, Python 3.11, hosted models, Qdrant and speech disabled:

- `test_call_process_routing.py` and `test_call_process_container.py`: 61 passed
  (134 subtests). The container tests run `uvicorn-role.sh` with its dry-run
  switch and read `supervisord.conf` and `nginx.conf` as text.
- Root `tests/test_monitoring_config.py`, `tests/test_monorepo_architecture_governance.py`
  and `tests/test_governance_evidence.py`: 156 passed.
- `governance/compliance_check.py`, `threat-model/validate_threats.py`,
  `app.freshness --check` (drift 0; this worktree has no freshness snapshot) and
  CI's blocking Ruff selection: passed.
- Not run here: the promtool cases (no promtool), `nginx -t` on the shipped file
  (the image build runs it), a container build, and the full root and backend
  suites, left to CI.

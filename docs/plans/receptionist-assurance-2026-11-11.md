# Call receptionist assurance review — 11 November 2026

Status: phase 1 implemented (this change); phases 2–4 planned, their gaps open.
Baseline: `dev` at `9a5043db`. Decisions: [ADR-0039](../adr/0039-call-handoff-and-caller-safety.md).
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

## Phases 2–4 — opened, not yet fixed

| Phase | Gaps | Scope |
| --- | --- | --- |
| 2. Records and accountability | G305, G304, G555, G584–G592 | Wrap-up ownership (officers who handled the call; each officer's note kept); presence scoped to a tenant; an audit row for every call turn, with the text served; the Luganda calculator path; the summary's effect on a ticket's SLA and note; the ticket purge that a missing voice table rolls back on Postgres; staff socket lifetime and caps; the listen socket that never wakes; the transcript snapshot race. |
| 3. Starts safely, fails visibly | G373, G306, G593–G600 | A receptionist that cannot start degrades and reports instead of stopping the API; the single-worker gate reads the variable the image uses; calls are refused when nothing can hear or speak; durations validated; a stale-row sweep at startup; call metrics and alerts; a CI job that installs the receptionist stack. |
| 4. Caller and officer screens | G601–G611 | Microphone released on every ending; the time-limit warning on both transports and both clients; a refused microphone keeps the call; close codes that reach a browser; reconnects; focus, target and language of the call dialog. |

Sequencing: the stale-row sweep (G306) ships after the worker gate (G593), or
each worker's start would end the other's live calls.

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

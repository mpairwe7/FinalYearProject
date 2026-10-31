# Agentic orchestrator, reasoner and resilience assurance review — 31 October 2026

Status: phased code fixes implemented with regressions; production acceptance open (see
[What this review did not do](#what-this-review-did-not-do)). Decision record:
[ADR-0022](../adr/0022-control-integrity-turn-accounting-and-verified-release.md).
Operating guide: [agent fault tolerance](../runbooks/agent-fault-tolerance.md).

Baseline: `4f26cf21` (`dev`, after #676). Scope: the orchestrator, reasoner, fault
tolerance and responsible-AI controls, from a question to its audit row. This is a
targeted engineering review, not a penetration test or a legal compliance certification.

Method: each finding below was reproduced against unmodified code before it was fixed,
and has a regression test. Reproduction scripts printed observed behaviour rather than
asserting it, because a test that fails on a missing function proves nothing about the
defect. Suspicions that did not reproduce were dropped; see [Dropped](#dropped).

## The pattern

The controls existed. What failed was that several reported success while protecting
nothing, so green tests and dashboards could not show the problem:

| Control | Looked like | Did |
| --- | --- | --- |
| Autonomy gate | Blocked a critical action | Ran it when the audit write failed |
| Risk tiers | 24 named tiers | 14 name no tool; 17 of 72 real tools rated below their declaration |
| Circuit breaker | Half-open recovery | Could refuse forever after one closed browser tab |
| Pipeline attestation | Mandatory guards verified | Added Stage 6 itself, so a missing guard could not be reported |
| Audit evidence | One row per turn | Nothing for failed or abandoned turns; "proposed" for calls that had run |
| Escalation tool | "An officer will review this" | A ticket no officer could trace to anyone |
| Contradiction withholding | "Does not get printed" | Printed, then corrected |

## Verified reference basis

Checked online on 2026-10-31. These are engineering references, not a claim that foreign
instruments bind URA.

| Reference | Quality | What it changed here |
| --- | --- | --- |
| [OWASP Top 10 for Agentic Applications](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/), Version 2026, December 2025 | Primary (PDF text read) | ASI02 mitigation 8: "immutable logs of all tool invocations". ASI08 mitigation 7: "circuit breakers between planner and executor". ASI09 mitigation 2: "tamper-proof records of user queries and agent actions". The manifest's ASI names drifted from these and were corrected. |
| [MCP specification 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28) | Primary | Closing a stream "**MUST** be treated by the server as cancellation" and the server "**SHOULD** stop work on the cancelled request as soon as practical" (G343). Annotations "should be considered untrusted, unless obtained from a trusted server": they only widen what runs at once, never what is allowed (G347). |
| Uganda Data Protection and Privacy Act 2019, ss.18, 19, 27 ([Laws.Africa text as at 3 May 2019](https://ulii.org/akn/ug/act/2019/9/eng@2019-05-03)) | Primary, for that version; later amendments not checked | s.18(4): destroy, delete or de-identify at the end of retention (G352). s.19: processing outside Uganda needs adequate protection or consent, so the row must show what answered (G359). s.27: notice and reconsideration for decisions based solely on automated processing, with an exception at s.27(4)(d) for purposes authorised by law. The assistant informs rather than decides, so s.27 is a design constraint on future actions. Not legal advice. |
| [Resilience4j CircuitBreaker](https://resilience4j.readme.io/docs/circuitbreaker) | Primary | `maxWaitDurationInHalfOpenState` caps time in half-open; "Value 0 means Circuit Breaker would wait infinitely", which is the wedge in G341. Here a lost probe hands over to a new one rather than re-opening. |
| [Amazon Bedrock Guardrails streaming](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-streaming.html) | Primary, read through a search summary | Synchronous mode buffers chunks until scanned and is the default; asynchronous mode warns that unsafe content can reach the user first. The basis for G353. |
| [OpenTelemetry GenAI conventions](https://github.com/open-telemetry/semantic-conventions-genai) | Primary | Status Development; `error.type` conditionally required on `execute_tool`. `trace_tool_call` already sets it, so nothing changed. |
| [NIST COSIS](https://csrc.nist.gov/projects/cosis), formerly COSAiS | Primary | Renamed 20 October 2026. No public draft of the single-agent or multi-agent overlay is stated on the project pages, so nothing here claims conformance to one. |
| [EU Digital Omnibus on AI](https://www.orrick.com/en/Insights/2026/07/EU-AI-Act-Update-Digital-Omnibus-Finalizes-8-Compliance-Changes) (Orrick, 29 July 2026) | Secondary. EUR-Lex returned no text | Annex III obligations move to 2 December 2027. The manifest's "full applicability 2 Aug 2026" header was corrected and marked as secondary. |
| Durable execution write-ups (Temporal, Restate, DBOS, Inngest vendors) | Secondary, vendor | Consistent direction: at-least-once delivery plus idempotency keys and compensation, not exactly-once. Informed the choice not to adopt an engine yet. |

## Findings and phases

Severity is the consequence for a taxpayer, not the difficulty of the fix.

### Phase 1: authority and evidence

| Gap | Severity | Evidence before the fix | Fix |
| --- | --- | --- | --- |
| G333 | Critical | A critical tool refused by the gate returned `{'ok': True}` and ran once the audit write raised. | Decision and record separated. A gate that cannot decide fails closed except for a pure low-risk read. |
| G334 | Critical | `ura_action_proposal` (declared critical) and `payment_checkout_settle` (declared high) classified `low` and `autonomous_executed`. 17 of 72 tools under-rated; 14 of 24 table entries name no tool. | `ToolSchema.risk` is the floor; a table entry may raise it; an unrated action is held as high. |
| G335 | High | With gates on, `escalate_to_human` returned `proposed_awaiting_officer`: a taxpayer asking for a person was refused. `objection_requirements` was held for mentioning objections. On by default in `docker-compose.gpu-salt.yml`. | Human oversight cannot be gated. Keywords no longer override a declaration. |
| G336 | High | A ticket was created while the ledger said `execution_status=proposed`, because the audit layer ran the gate again after the fact. | `decided_outcome` records the decision taken. |
| G337 | High | A tool that raised added 0 audit rows. | Failed calls are recorded as errors. |
| G338 | High | The tool's ticket had no conversation, session, user or transcript, and nobody was told, while the model promised an officer would review it. | `ESCALATION_ROUTE` takes it through `_maybe_create_ticket`. |
| G339 | Medium | A ticket raised for `tenant-a` was stored as `default`, and an open ticket could be reused across tenants. | The tenant is passed through; the lookup is scoped. |
| G340 | Medium | `proposed_awaiting_officer` reached no queue. | A held action opens a ticket. |

### Phase 2: fault tolerance

| Gap | Severity | Evidence | Fix |
| --- | --- | --- | --- |
| G341 | Critical | Three ways to leave a breaker half-open with its probe "in flight" and no path out: a client leaving mid-stream, a budget refusal after the breaker said yes, and the reasoner taking the shared probe and falling back to T1, where T1 was **never tried** and the reply was empty. | The probe is a lease. `release()` and `admit()`, the latter at the six sites that shared the double-gate pattern. |
| G342 | High | Six turns with the reasoner down made six attempts: no breaker ever stopped them, so each cost a full `REASONER_DEADLINE_S` (60 s by default, 90 s in the reasoner profile). | Its own breaker, `llm_reasoner`. An outage on either tier leaves the other reachable. |
| G343 | High | A timed-out agentic turn ran 3 more tool rounds after the caller gave up. The agentic stream never checked for a disconnect or the deadline. | `CancelScope`, checked before every round and dispatch. |
| G344 | High | 1 heartbeat where about 20 were due during a 0.4 s translation: one Luganda turn froze every stream on the worker. | Translation, guards and ticket writes run off the event loop. |
| G345 | High | With `turn_deadline_s=0.1`, a 0.6 s retrieval ran to the end and answered normally. REST had no deadline. | One deadline shared by every stage and transport. |
| G346 | Medium | A cancelled stream recorded an LLM failure and made a paid cloud call for a client who had left. A stream failing after tokens appended a second answer. | A cancellation is neutral. No fallback once text is out. |
| G347 | Medium | Identical calls in one round both ran, tools with side effects overlapped, pool threads lost the turn's context, and the budget was not thread-safe. | In-flight reservation, a lock, context per call, overlap only for repeatable reads. |
| G348 | Medium | Breaker state appeared only in logs. | `ura_circuit_breaker_state`, transitions, lost probes, and alerts. |

### Phase 3: turn contract and orchestrator

| Gap | Severity | Evidence | Fix |
| --- | --- | --- | --- |
| G349 | High | Past the deadline, client gone, generator closed after text went out, or failed: 0 audit rows each. The deadline path sent no terminal frame. | Every turn gets a row with a `disposition`. |
| G350 | High | `attest_turn` returned `ok=True` for model text with no output guard. Streamed turns were not attested (G325). | A turn that served model text must show a guard. Streamed turns are attested. |
| G351 | High | Graph replies left through `_finalize_reply` with `response_judge: None` (G332). | Every graph answer goes through `_apply_output_guards`. |
| G352 | Medium | Checkpoints had no retention or erasure, `resume` was not bound to an owner, naming a start node stepped past the gate, a corrupt outcome read as answered, a resume refilled the budget, and a failed save at a suspend was swallowed. | Each fixed. The service still passes no `thread_id`. |

### Phase 4: reasoner integrity

| Gap | Severity | Evidence | Fix |
| --- | --- | --- | --- |
| G353 | High | `'The VAT rate is 25%.'` went out in a `token` frame before the guard withheld it. | `FigureReleaseGate`; the tool loop's reply waits for the guards. |
| G354 | High | The figure lock never ran on the stream, the web channel. REST rejected a correct `UGX 180,000` because it was not given tool outputs. An invented `UGX 7,777,777` passed when the sources held only percentages. | Runs on every transport under REST's rule, sees tool outputs, and no longer skips a kind of figure the sources lack. |
| G355 | High | `chargeable_income` bound from `annual_turnover`, so corporation tax was computed on turnover. `taxpayer_id` bound from a tool's output, letting one tool choose whose record the next reads. | Slots take only keys that mean the same thing; identifiers never come from tool output; the reply names what was assumed. |
| G356 | Medium | `truncated` was read by nothing, so a loop out of steps looked finished. | Disclosed and recorded. |
| G357 | Medium | A model-chosen tool route carried an empty whitelist, meaning every eligible tool, chosen from text that may have been written to steer it. The classifier had no time limit. | A closed read-only list and a timeout. |

### Phase 5: transparency

| Gap | Severity | Evidence | Fix |
| --- | --- | --- | --- |
| G358 | High | With every model down, a raw passage was served with no notice. Combined with G341 this could last indefinitely and look healthy. | The reply says so, on every transport. |
| G359 | High | `result["model"]` names the configured model whichever ran, so the ledger could not show who wrote an answer or whether it left Uganda. | A `generation` block: backend, model, tier, fallbacks, degradations. |

## Dropped

* **PII in streamed tokens.** Suspected from `_run_chat_turn_events`. The adapters run
  `StreamingDLPFilter`, so it does not happen.
* **Tenants unable to see their tickets.** The first draft of G339 claimed this.
  `/v1/admin/tickets` applies no tenant filter, so the impact is mislabelled tenancy and the
  severity is Medium. The absence of a filter is itself recorded as G362.
* **Figure lock false positives measured from stored answers.** No report stores generated
  answers with their passages, so this could not be measured offline, and is not claimed.

## What went wrong during the review

Recorded because a reader should know how far to trust the process.

* A cancellation probe passed `cancel_event` positionally into `personalization_context`,
  which made G346 look like a false alarm until the probe was corrected. The test had the
  same mistake.
* A `sed` import fix missed `retriever.py`, leaving `admit` undefined. The 35 targeted tests
  stayed green. Linting for undefined names found it, and `test_providers.py` would have.
* The first commit went in despite lint findings, because the command chained with `;`. It
  was amended before anything was pushed.
* A background run reported exit code 0 after running nothing, because collection failed.
  A later baseline ran zero tests because log lines were parsed as test ids. Neither was
  counted.
* Attestation's new rule would have paged someone for every graph clarification on the
  stream, on a critical alert with no `for:` window. Found by reading the alert, then fixed
  and tested.

## Test evidence

Full suite (`App/backend/tests`, `tests/agents`, `tests/chaos`), four shards, on Python 3.11.
Three modules cannot be collected here because `opentelemetry`, `requests` and `soundfile`
are not installed, so `test_tracing_genai.py`, `test_speech_streaming.py` and
`test_cx_500_complex_ngrok_schema.py` did not run locally. CI runs them.

Environment notes, each confirmed by experiment rather than assumed:

* With DNS down, a blocking `getaddrinfo` for the default `vllm` host stalled receptionist
  tests past their 25 s budget. Pointing `VLLM_BASE_URL` at a refused local port removed it.
* `test_chat_connectors.py` and `test_connector_link_integration.py` fail after
  `test_prompt_budget.py` in the same process. The pair fails identically on clean `dev`.
* `test_pdf_corpus.py` (2) and one officer wrap-up test fail on clean `dev` here.

A clock-dependent test from #676 was fixed: it asserted `"16"` was absent from a notice that
carries the time of the check.

## Design decisions worth challenging

* **Whole sentences only.** A figure's sentence is held whole, so a WebSocket reply starts up
  to one sentence later. Half a sentence frozen on screen read as a fault.
* **The lock measures before it enforces.** T1 and cloud answers can still show an untraceable
  figure that does not contradict a passage. Enforcing blind risks stripping correct answers,
  with no way to tell how often. This is G360, and it needs a decision.
* **`high` tools need an officer once gates are on.** That is the flag's stated contract, but
  it removes a path the misclassification had left open. No officer approval endpoint exists,
  so such an action is completed by the officer who picks up the ticket.
* **A failed audit write does not stop a permitted action.** Only a refusal is unconditional.
  G366 asks whether high-risk actions should need a write-ahead record.
* **The deadline stops new work, not work in flight.** A thread cannot be interrupted, so a
  call already started completes after the turn ends.

## What this review did not do

* **Nothing ran against a live model, the deployed Space or a live connector.** A low-faithfulness
  probe can open a real ticket for a real officer, so production was not probed. Production
  behaviour is inferred from code and the earlier review's logs.
* **Postgres paths were not exercised.** `find_open_ticket` and the retention list changed in
  both backends; the tests run on SQLite.
* **`promtool` was not run.** It is not installed here. The new alert and recording-rule cases
  are parsed as YAML and checked by `tests/test_monitoring_config.py`, and run for the first
  time in the CI "Monitoring config" job.
* **Disclosure wording has not been reviewed** by a language lead or URA communications. It is
  translated by machine like every other reply.
* **Tax rules were not reviewed.** Slot meanings come from the tool schemas and general tax
  concepts, and need a tax specialist's eye.

## Residual risk

| Gap | Open item | Owner |
| --- | --- | --- |
| G360 | Decide whether to enforce the figure lock on T1 and cloud answers, once `ura:figure_lock:shadow_reject_ratio_1d` and a reviewed sample exist. | Service owner, tax policy |
| G361 | `intent_confidence` is a constant 1.0 at the gate, so the 0.80 floor is inert, and audit rows repeat it. Route confidences are uncalibrated constants, so wiring them in now would make it arbitrary. | Backend |
| G362 | Ticket APIs apply no tenant filter, and `tenancy.same_tenant` has no callers. Filtering by tenant could hide tickets, so it needs a tenancy decision first. | Service owner |
| G363 | Confirmation exists only on the WebSocket. On SSE and REST a proposal cannot be approved, though nothing runs. | Backend, frontend |
| G364 | The release gate reads digits and English number words. Vernacular number words need reviewed vocabulary, not invented terms. | Language leads |
| G365 | The receptionist calls `_vllm_generate` directly, with no breaker, so a DNS outage can spend its 25 s budget. | Backend |
| G366 | No write-ahead record for high-risk actions: a crash between execution and audit loses the row. | Backend, audit |
| G367 | Supervisor confidences are constants, not calibrated probabilities. Tier selection reads them. | Backend, evaluation |
| G368 | After a reasoner timeout, T1 can repeat a tool call that was in flight. Tickets dedupe; other side-effecting tools rely on their idempotency key. | Backend |

Also open from the previous review: G326 to G329 and G331.

Production acceptance needs, on the deployed Space and read from logs and audit rows rather than
the reply: one turn of each `disposition`, a `generation` block naming the real backend, an
attested streamed turn, and a forced model outage showing a disclosure and an alert. The
[runbook](../runbooks/agent-fault-tolerance.md) says what each should look like.

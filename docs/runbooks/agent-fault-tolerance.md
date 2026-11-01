# Agent fault tolerance

What the agent does when something it depends on fails, what an operator sees, and
what to do. Decisions are in [ADR-0022](../adr/0022-control-integrity-turn-accounting-and-verified-release.md);
evidence is in the [assurance review](../plans/agentic-orchestrator-resilience-assurance-2026-10-31.md).

## How a turn degrades

| What failed | What happens | What the taxpayer sees |
| --- | --- | --- |
| Reasoner down | `llm_reasoner` breaker opens; turns go straight to T1 | A normal answer |
| T1 down | `llm` breaker opens; turns go to the cloud chain if configured | A normal answer |
| Every model down | The closest passage is served | The passage, and a sentence saying it is a fallback |
| A graph draft is not supported by its passages | The closest passage replaces it | The same passage and sentence |
| Tool loop out of steps | The last text is served | The text, and a sentence saying it may be incomplete |
| Stream breaks part-way | Text so far is kept; no second answer is appended | The text, and a sentence saying it was cut off |
| Figure check fails while enforcing | Every figure is removed, or the reply is withheld if that fails too | The rest of the answer and a note to confirm with URA |
| Turn reaches its deadline | Generation stops | What was verified so far, and a notice to try again |
| Client leaves | The tool loop stops at its next check; no cloud call is made | Nothing |
| Audit ledger down | Refused actions stay refused | Nothing; see below |

Every turn writes an audit row with a `disposition`: `completed`, `cancelled`,
`deadline_exceeded` or `failed`. A row's `generation` block names the backend, model,
tier, fallbacks and degradations.

## Circuit breaker open

`CircuitBreakerOpen`: `ura_circuit_breaker_state{breaker}` has been 2 for 10 minutes.
The state is 0 closed, 1 half-open, 2 open, and `ura_circuit_breaker_transitions_total`
counts the changes.

1. Read the `breaker` label: `llm` is T1, `llm_reasoner` the reasoner, `gemini_flash`
   and `cf_workers_ai_llm` the cloud chain, `mcp:<namespace>` a connector.
2. Check that dependency. An open breaker is a symptom: it opened because calls failed.
3. A breaker retries by itself after its back-off (up to 300 s), so there is nothing to
   reset. Restarting a worker clears its state but does not fix the dependency.

Breakers are per worker, so one worker can show open while others do not. The state moves
only when a call reaches the breaker, so on a worker with no traffic it stays at 2 after the
back-off has passed, and this alert can fire while nothing is being refused. Check that
worker's traffic before treating it as an outage.

## Half-open probe lost

`CircuitBreakerProbeLost`: a caller took the single half-open probe and never reported
how it went. The lease reclaimed it after two minutes, so service recovered, but before
the lease this exact event left the breaker refusing everything until a restart.

Treat it as a defect, not an outage. Every path that calls `allow_request()` must end in
`record_success()`, `record_failure()` or `release()`. Where a budget is also involved,
use `resilience.admit()`. Known leaks are in `speech_service` TTS, where the loop can
`continue` past an empty result; the lease covers those.

## Autonomy gate faulting

`AutonomyGateFaulting`, by `stage`:

* `evaluate`: the gate could not decide. Anything but a pure low-risk read is refused
  with `failed_closed`. Taxpayers can still reach an officer.
* `audit`: a refusal stood, but its record was not written.

Check the ledger first (`ura_audit_append_failed_total`). While `audit` faults continue,
refusals are missing from the ledger, so reconcile from the logs (`Refused action`).
Do not switch `risk_tiered_autonomy_gates` off to clear this alert: that removes the
control instead of fixing what it depends on.

## Answers degraded

`AnswersDegraded`: more than 10% of answers carry a disclosure, by
`ura_turn_degraded_total{reason}`:

* `generation_unavailable`: no model answered. Check the breakers and, on the Space, the
  three settings the cloud chain needs: `FLAG_CLOUDFLARE_FALLBACK`, `LLM_FALLBACK_BACKEND`
  and the Gemini key. Each fails silently when unset, so a healthy-looking Space can be
  serving passages.
* `draft_unsupported`: a graph draft was not supported by its passages, so the closest
  passage replaced it. A sustained rise usually means a model or retrieval change.
* `tool_loop_truncated`: the loop ran out of iterations or budget. A sustained rise usually
  means a model or prompt change. Look at `ura_tool_calls_total`.
* `stream_interrupted`: a worker failed part-way through a stream.

## Turns hitting the deadline

`TurnsHittingDeadline`: `ura_turn_deadline_exceeded_total{stage}`, where `stage` is
`stream`, `generation` or `agentic`. `AGENTIC_TURN_DEADLINE_S` (default 120) bounds every
transport. `0` turns it off, and production should not run that way.

The usual cause is a slow dependency, so check model latency first. Raise the deadline
only with evidence. A stopped turn's text and its time are in the audit row.

## Space running, API down

Hugging Face's `RUNNING` means the container is up, and nginx serves the frontend from `/`, so
the page loads while every API route returns 502. Check `/health`, not the stage or the page:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://landwind22-ura-chatbot.hf.space/health
hf spaces logs landwind22/ura-chatbot --tail 400
```

Repeating `Application startup failed` lines mean the API cannot start, so read the exception
above them. A flag that needs something the image lacks does this: `FLAG_VOICE_RECEPTIONIST`
without Pipecat stopped the API for about 8 hours on 2026-10-31 (G371). Setting a secret
restarts the Space in about two minutes, which is far quicker than a rebuild:

```bash
hf spaces secrets add landwind22/ura-chatbot -s FLAG_VOICE_RECEPTIONIST=false
```

Do not edit the Space's Dockerfile directly. The roll pushes
`App/deploy/hf-space/Dockerfile` over it whole.

## Reading a blocked or wedged turn

No taxpayer text is logged. To reconstruct a turn, read its `generate` row: `disposition`,
`generation`, `figure_lock`, `pipeline_attestation`, `plan` and `live_check`. Tool calls
are in `agent_action_v3` rows while `action_level_audit_v3` is on, including calls that
raised, with their `autonomy_outcome` as decided at the time.

## Figure lock evidence

The lock traces every figure in a reply to the question, the verified facts, the passages
or a tool result. It enforces on the reasoning tier and under `evaluator_optimizer`;
everywhere else it measures. `ura:figure_lock:shadow_reject_ratio_1d` is the share of
checked replies it would have changed.

Before enforcing it for all tiers (G360), sample rejected turns and confirm how many were
right to reject. A rejection on a correct answer removes that sentence, adds a note to
confirm with URA, and costs the taxpayer the figure. A miss shows them a wrong one. There
is no stored corpus of generated answers to size this offline, which is why it is measured
in production first.

If the check itself breaks, `ura_figure_lock_checks_total{outcome="error"}` rises. While
enforcing, replies lose their figures rather than show them unchecked. While measuring,
replies are unchanged and nothing is measured. A sustained rise is a defect in either case.

## Streaming and the release gate

`stream_figure_hold` holds a reply from the sentence with its first figure until the guards
have read it. Prose still streams. If taxpayers report answers that stall part-way, that is
the gate waiting on verification: check `ura_turn_stage_sla_breaches_total` for the guard
stage before turning it off. Off, a figure that fails verification is on screen until it is
corrected, so only do that as a temporary measure and say so in the incident record.

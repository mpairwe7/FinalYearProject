# ADR-0022: Control integrity, turn accounting and verified release

## Status

Accepted for implementation, 2026-10-31; production acceptance pending. Extends
[ADR-0007](0007-sovereign-local-reasoning-tier-and-code-owned-tax-logic.md): the figure
lock now runs on every transport. Extends
[ADR-0021](0021-planner-bound-web-evidence-and-honest-live-checks.md): closes G325 and
G332.

## Context and Problem Statement

The agent had controls for most of what can go wrong, and the
[assurance review](../plans/agentic-orchestrator-resilience-assurance-2026-10-31.md)
found that several did not hold when tested. They shared one failure mode: a control
that looked present and reported success, while what it protected went unprotected.

* A critical action refused by the autonomy gate **ran** when the audit write failed,
  because decision and record shared one `try` that logged at debug level.
* The gate rated 17 of 72 registered tools below their own declared tier.
* A half-open circuit breaker could be wedged for the life of the process by a client
  closing a browser tab, with nothing logged.
* Attestation inserted Stage 6 itself, so a mandatory guard could not be reported missing.
* A contradicted figure was on the taxpayer's screen before the guard withheld it.

## Decision Drivers

* A control must fail in the direction that protects the taxpayer, and must be visible
  when it fails.
* Evidence must describe what happened, not what a second evaluation thinks should have.
* A wrong figure on screen for two seconds has still been read.
* A blocking control with an unknown error rate should not be switched on for live
  taxpayers without measurement.
* Reaching a person must not depend on anything that can be down.

## Considered Options

* **Fix each leak at its call site.** Rejected for the breaker: six or more sites share
  the pattern, and the next one will too. A lease in the primitive means no caller can
  wedge a dependency. High-traffic paths also release explicitly.
* **Gate on `ToolSchema.risk` alone.** Rejected: the name table is how a planned action
  with no registered tool stays classified. Declared tier is the floor; the table may
  raise it.
* **Buffer every streamed reply until verified.** Rejected: it gives up streaming for
  prose that carries no figure. Hold from the first figure instead.
* **Keep streaming and correct afterwards.** Rejected: that was the defect.
* **Enforce the figure lock on all tiers now.** Rejected for now. No stored corpus of
  generated answers exists to size its false positive rate, and production probes can
  open real tickets. It enforces where it already did, runs on every transport, and
  measures elsewhere, so enforcing it is a decision made on evidence (G360).
* **Adopt a durable execution engine.** Rejected for now: turns are single-request, and
  nothing suspends in production. The runtime is hardened instead, and the service keeps
  passing no `thread_id`.
* **Refuse to answer when a model fails.** Rejected: the closest passage is useful if it
  is labelled as what it is. Whether that holds for web commentary is G369.

## Decision Outcome

**Authority and evidence.** `classify_action_risk` takes the tool's declared tier as a
floor; an unrated action is held as high. `escalate_to_human` cannot be gated by any tier,
throttle or failed guardrail. `ToolRegistry._autonomy_gate` separates the decision from
its record: a gate that cannot decide fails closed for anything but a pure low-risk read,
and a block stands whether or not it could be written down. `append_action_audit_v3`
records the decision taken rather than asking the gate again. A tool that raised is
recorded. Tool escalations go through `_maybe_create_ticket` by way of `ESCALATION_ROUTE`,
and a held action opens a ticket, so "awaiting an officer" is true.

**Fault tolerance.** The half-open probe is a lease (`probe_timeout`). `release()` gives a
slot back without judging the dependency, and `admit()` takes a slot and its budget or
neither. The reasoner has its own breaker. One deadline, carried in a context variable,
bounds every stage on every transport. A `CancelScope` stops the tool loop before its next
round or dispatch; a call already in flight completes. Calls with effects run one at a
time, and identical calls in a round run once. Translation and the guards run off the
event loop.

**Turn contract.** Every turn writes one audit row with a `disposition`, payload schema 3.
An abandoned turn is recorded from a thread, because a task being cancelled is cancelled
again by any `await` in its `finally`. A turn stopped at its deadline tells the taxpayer in
a token, since the web client discards an `error` frame's message.

**Attestation.** A turn that served model text must show a guard stage. Only a turn that
served none may be attested on its audit row alone. Streamed turns are attested from real
stage timings.

**Graph.** The graph drafts and the service checks: every answer goes through
`_apply_output_guards`. Checkpoints are swept by retention and erasure, `resume` is bound
to the thread's owner, a corrupt outcome reads back as errored, and a failed save at a
suspend ends the turn.

**Reasoner.** `FigureReleaseGate` releases whole sentences up to the first figure and holds
the rest for the guards (`stream_figure_hold`, default on). The tool loop's reply waits for
them outright. The figure lock sees tool outputs, and no longer skips a kind of figure the
sources do not contain. A calculator slot takes only keys that mean the same thing, an
identifier never comes from a tool's output, and the reply says what was assumed. A
model-chosen tool route gets a closed read-only list.

**Transparency.** The audit row names what produced the words. A raw passage served because
generation failed, a truncated tool loop and a broken-off stream each say so to the taxpayer.

## Consequences

* **Good:** a failed dependency degrades a turn instead of ending the service, and shows
  up on a dashboard. An auditor can reconstruct any turn, including one that failed.
* **Good:** on the web channel a figure is not read before it is checked.
* **Bad:** a reply with an early figure streams less of itself. First-token latency on the
  WebSocket path rises by up to one sentence, because the gate releases whole sentences.
* **Bad:** with `risk_tiered_autonomy_gates` on, `high` and `critical` tools can no longer
  run on a taxpayer's confirmation alone, which the old gate allowed by misclassifying
  them. `ura_action_proposal` and `payment_checkout_settle` are affected. That is the
  flag's stated contract, but it changes behaviour in the `gpu-salt` profile.
* **Bad:** a stricter figure lock can strip a correct sentence on T3, where a sentence
  states a figure that a passage does not contain. The note it leaves tells the taxpayer
  to confirm with URA.
* **Bad:** an enforced figure lock that cannot run removes every figure from the reply, or
  withholds it if it cannot do that either. A fault in the check costs the taxpayer the
  figures, rather than showing them unchecked.
* **Neutral:** `cancelled` is a new `chat_turns_total` outcome, and containment excludes it.
* **Neutral:** a probe that reports after its lease was reclaimed still changes the breaker.
  Binding reports to a lease would change every call site. At worst it allows one extra
  probe, or closes the breaker on a true but late success, and failures trip it again.

## Confirmation

* `tests/agents/test_autonomy_gate_contract.py`, `test_escalation_route_contract.py`
* `tests/agents/test_fault_tolerance_contract.py`, `tests/chaos/test_failure_modes.py`
* `tests/agents/test_turn_terminal_contract.py`, `test_graph_orchestrator_contract.py`
* `tests/agents/test_reasoner_integrity.py`, `test_transparency_contract.py`
* `monitoring/tests/alerting-rules.test.yml` in CI, and
  [the runbook](../runbooks/agent-fault-tolerance.md)

Production acceptance is listed in the review. Nothing here has run against a live model.

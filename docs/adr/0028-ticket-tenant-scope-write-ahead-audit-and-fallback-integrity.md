# ADR-0028: Ticket tenant scope, action write-ahead audit, and fallback integrity

## Status

Accepted — 2026-11-06

## Context and Problem Statement

Four open gaps allowed staff ticket reads and aggregates to cross tenant boundaries, high-risk tools to act before an action-audit row existed, a reasoner timeout fallback to start with a fresh tool budget while the original worker could still dispatch, and a claim-verifier outage to look like a successful check.

## Decision Outcome

- When `multi_tenant` is enabled, staff ticket detail, update, presence, chat-end, queue list, statistics, SLA, and live event operations use the authenticated tenant. A ticket outside that tenant returns 404; a live event is filtered before delivery and its internal tenant key is removed. Public escalation routes continue to rely on their existing ticket capability. When multi-tenancy is disabled, existing unscoped behavior remains.
- When either action-level audit receipt flag is enabled, a high- or critical-risk tool writes an `intent` audit row before its effect. If that required write fails, the tool does not run. `escalate_to_human` remains exempt under ADR-0022.
- One `ToolCallBudget` instance is created for an agentic turn and explicitly passed to the reasoner and its T1 fallback. A timed-out worker and fallback therefore account for admitted, in-flight, and completed calls against the same limits.
- A claim-verifier exception produces an `unavailable` report, lowers a high confidence assessment, and adds a taxpayer-facing disclosure. The answer remains available because an unavailable checker is not evidence that its claims are false.

## Consequences

- Positive: staff endpoints and aggregate dashboards enforce the active tenant boundary; effects have a durable intent trace before execution; timeout fallback cannot replenish its tool budget; and an outage is visible in the audit/decision record and to the taxpayer.
- Negative: action-level auditing can temporarily block high-risk actions during an audit-store outage. A verifier outage does not withhold otherwise usable answers, so users are asked to confirm the result with URA.
- Limitation: cross-tenant public ticket capability behavior is unchanged, and shared budgets constrain repeated tool dispatch but cannot interrupt a call already running in a worker thread.

## Confirmation

Verified by `tests/test_auditor_controls.py`, `tests/agents/test_autonomy_gate_contract.py`, `tests/agents/test_fault_tolerance_contract.py`, and `tests/agents/test_grok_pending_gap_closures.py`; threat and governance evidence is recorded in `governance/ai_risk_manifest.yaml` and `threat-model/validate_threats.py`.

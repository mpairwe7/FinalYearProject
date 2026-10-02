# `app.workflows` — guided multi-step journeys

Slot-filling flows that walk a taxpayer through a URA task one step at a
time, persist across turns (`workflow_sessions` table, keyed by
conversation), and show progress in the web stepper (`WorkflowStepper`).
On by default (`workflows` flag).

| File | Role |
| --- | --- |
| `loader.py` | YAML → `WorkflowDefinition`; resolves `portal_action` and `resources` ids against `app/verified_resources.py` |
| `registry.py` | `WorkflowRegistry` (load, `match_trigger`, `advance`, `pending_step`) and `compute_workflow_progress` for the stepper |
| `slots.py` | Validators: `text`, `boolean`, `number[min=…]`, `enum[a,b]`, `regex[…]` |
| `flows/*.yaml` | One file per journey |

## Journeys

| id | Name | Started by |
| --- | --- | --- |
| `tin_registration` | TIN Registration | trigger phrases |
| `tin_procedure_help` | TIN Registration Help | the TIN-procedure path in `service.py` (no triggers) |
| `return_filing` | Return Filing | trigger phrases |
| `payment_assistance` | Payment Assistance | trigger phrases |
| `tax_clearance` | Tax Clearance Certificate | trigger phrases |
| `motor_vehicle_registration` | Motor Vehicle Registration | trigger phrases |
| `customs_clearance` | Customs Clearance | trigger phrases |
| `objection_or_dispute` | Objection or Dispute | trigger phrases |
| `audit_invoice_compliance` | Tax Invoice & EFRIS Compliance Audit | trigger phrases |
| `calc_*` (9 flows) | Calculators | the calculator router only (no triggers) |

`tax_clearance` checks the four conditions URA applies before approving a
certificate and routes each unmet one to its fix; `motor_vehicle_registration`
covers first registration and the move to digital number plates. Their YAML
headers name the URA and Ministry of Works pages the steps come from; re-check
those before changing a fee or a condition.

## How a journey starts

1. `match_trigger` normalises both sides — base verb forms ("filing" →
   "file"), and a tax type before "return" ("my VAT return" → "my return") —
   and treats a flow's own **name** as a trigger when the flow has trigger
   phrases. Calculators are never started by name.
2. The entrance rule (G39, `service._maybe_handle_workflow`): a message that
   reads as a question (`_reads_as_question`, which includes "I need…" and "I
   want…") is **answered**, not captured, unless it also asks to be guided
   ("guide me", "walk me through", "help me file/register/apply/submit",
   "start", …).
3. When a question is answered instead, `app/turn_guidance.py` adds a next
   action `Guide me step by step through <flow name>`. Its text satisfies both
   rules above, so a click starts that flow. The offer is not added inside a
   flow, on a clarification, a refused input or an escalation; it *is* added to
   an abstention, where it is a way forward.

## While a journey is running

A flow owns its conversation until it completes or is cancelled ("cancel",
"stop"). Two things take the turn back:

- a message that reads as a new question and does not fit the pending slot
  is answered from the corpus, and the flow stays open ("resume" continues it);
- an explicit request for a **different** flow ("help me file my return"
  inside the Tax Clearance checklist) starts the requested one. The current
  flow is marked cancelled only after the new session is created, so a refused
  start leaves it resumable (`service._flow_switch_target`).

Trigger matching folds "-ing" forms only. Past tense is left alone, so "I
filed my return yesterday but …" reports a problem instead of starting a new
filing flow.

## Funnel metrics

Every session start, turn and cancel increments
`journey_events_total{workflow, event, step}` on `/metrics` (admin auth), with
`event` one of `started`, `step_entered`, `step_invalid`, `completed`,
`cancelled`. The fall in `step_entered` between consecutive steps of one flow is
its drop-off. No slot value is ever a label.

An informational step with `ends_flow: true` is a terminal guidance step: it
is emitted once and completes the guide without asking for a dummy answer. The
loader accepts only a YAML boolean, and allows it only on a step with a
question and no slot or tool. Use this when a prerequisite is missing and later
steps do not apply yet; tell the taxpayer how to return to the journey after
they meet it.

## Adding a journey

1. Write `flows/<id>.yaml` (see `tax_clearance.yaml` for conditional steps and
   links). Put **every question before every information-only step**:
   `advance` shows an information step once and moves on, so a question after
   one receives the reply meant for the information step. `FlowShapeTests`
   fails the build otherwise. Links are ids from `app/verified_resources.py`;
   add a new page there only after it has passed
   `python -m app.verified_resources --check`.
2. Pick trigger phrases a taxpayer would type as a task, not as a question.
3. Add cases to `App/backend/tests/test_guided_journeys_and_ei.py` and a live
   case to `scripts/probe_guided_journeys.py`, then follow
   `docs/runbooks/guided-journey-probes.md`.

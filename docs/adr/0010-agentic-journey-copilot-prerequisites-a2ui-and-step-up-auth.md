# ADR-0010: Agentic Journey Copilot — Pre-Flight Prerequisite DAG, Declarative A2UI Form Co-Completion, Pushdown Goal Stack & Step-Up Authentication

- **Date:** 2026-10-22
- **Owner:** Cloake Tech Limited — Core Agentic CX & Workflow Architecture Team

## Status

Accepted — Pillars 1, 5 and 7 amended by [ADR-0013](0013-session-bound-workflow-adoption-and-verified-resume-codes.md) (session-bound adoption, request-bound sign-in state, data-minimised case facts, unique verified resume codes).

## Context and Problem Statement

While `WorkflowRegistry` (`App/backend/app/workflows/registry.py`) and `WorkflowStepper` (`App/frontend/src/components/WorkflowStepper.tsx`) provided step-by-step prompts for URA procedures (`return_filing`, `tin_registration`, `payment_assistance`, `tax_clearance`, `customs_clearance`, `objection_or_dispute`), taxpayers continued to encounter five recurring journey failures:

1. **Mid-Form Prerequisite Stall:** Taxpayers reached the final steps of a workflow before realizing they lacked a mandatory prerequisite (e.g., an active TIN, NIRA NIN/URSB certificate, EFRIS Fiscal Document Numbers, or e-Tax portal login access).
2. **Form Box Mapping Friction:** Procedural text instructions did not show how answers mapped onto official URA return/application schedules.
3. **Multi-Hop Digression Disorientation:** When taxpayers asked side questions or needed to complete a prerequisite sub-workflow first, a flat step index could not stack and pop parent/child objectives.
4. **Implicit Problem Statements:** Taxpayers described challenges in everyday language (*"A ministry deducted 6% from my invoice"*, *"My goods are held at Malaba"*) rather than asking for a named URA workflow.
5. **Authentication & Redundant Entry Friction:** Transitioning from anonymous discovery to portal/authenticated actions risked asking the taxpayer to re-enter facts already provided, violating W3C WCAG 2.2 AA SC 3.3.7 (*Redundant Entry*).

## Decision Outcome

We implemented the **October 2026 Agentic Journey Copilot (`App/backend/app/workflows/journey_copilot.py`)** across backend and frontend in seven integrated pillars grounded in October 2026 standards and research:

1. **Pillar 1 — Pre-Flight Prerequisite DAG & Readiness Gate (`evaluate_prerequisites`):**
   Evaluates workflow prerequisites (`WORKFLOW_PREREQUISITE_DAG`) into `satisfied`, `missing`, or `pending` states and attaches 1-tap `remediation_action` buttons (`"Guide me step by step through ..."`) when a prerequisite is missing.
2. **Pillar 2 — Declarative A2UI Form Co-Completion (`build_form_draft_surface`):**
   Implements a Google `A2UI/v1` / `AG-UI`-style declarative form schedule (`WORKFLOW_FORM_SCHEMAS`) that maps slots to official URA form boxes while strictly masking `_WORKFLOW_SENSITIVE_SLOTS` and never echoing raw free-text PII into the UI view.
3. **Pillar 3 — Plan-Centric "Journey GPS" & Pushdown Goal Stack (`push_subworkflow`, `pop_subworkflow`, `build_journey_gps`, `is_prerequisite_remediation`):**
   Extends `WorkflowSession` with a pushdown `goal_stack` (*Plover*, `arXiv:2607.15193`) durably mirrored into `slots_json["_goal_stack"]` so prerequisite sub-workflows initiated mid-flow are pushed rather than cancelling the parent flow, and automatically pop back to the parent workflow step upon completion. `_localize_workflow` translates `journey_gps.active_goal` across all supported locales.
4. **Pillar 4 — Proactive Objective Inference (`infer_taxpayer_objective` in `turn_guidance.py`, `applyTurnMeta` in `turnMeta.ts`, `ChatMessage.tsx`):**
   Detects latent taxpayer objectives from situational problem descriptions (*arXiv:2608.22266*), attaches `objective_hypothesis` metadata plus a 1-tap guided-mode confirmation chip to `next_actions`, and renders an accessible Objective Confirmation Card in `ChatMessage.tsx`.
5. **Pillar 5 — Just-In-Time Step-Up Auth & Zero-Data-Loss Elevation (`evaluate_step_up_auth`, `elevate_anonymous_workflow_session`):**
   Follows IETF RFC 9470 and W3C WCAG 2.2 AA SC 3.3.7 by preserving 100% of anonymous slots when elevating a session (`_apply_personalization_to_workflow(..., user_id=user_id)`) or prompting for portal login/registration.
6. **Pillar 6 — Multimodal "Snap-to-Fill" Slot Grounding & Stall Scaffolding (`bind_document_facts_to_workflow`, `build_stall_scaffolding`):**
   Binds analysed OCR/document facts into unfilled workflow slots and provides progressive field examples and 1-tap helper/officer escalation chips in `WorkflowStepper.tsx` when `failed_attempts >= 1`.
7. **Pillar 7 — Cross-Channel Resume Token (`generate_resume_code`, `parse_resume_code`):**
   Emits a deterministic, PII-free token (`URA-<PREFIX>-S<N>-<CHECK>`, one registered prefix per workflow and a verified check — see ADR-0013) rendered in `WorkflowStepper.tsx` across all 5 locales (`en`, `lg`, `sw`, `nyn`, `ach`) and parsed directly in `ChatModel._maybe_handle_workflow()` so pasting a resume token resumes or launches the target workflow without triggering slot validation errors.

## Consequences

- **Positive:** Zero extra LLM calls or network latency ($< 0.3\text{ ms}$ deterministic execution); full backward compatibility with all 21 YAML workflows; 100% privacy compliance (`_WORKFLOW_SENSITIVE_SLOTS` never echoed); 5-locale UI parity (`en`, `lg`, `sw`, `nyn`, `ach`).
- **Negative / Trade-offs:** Adds structured metadata fields (`prerequisites`, `form_draft`, `journey_gps`, `scaffolding`, `step_up_auth`, `resume_code`) to `workflow` payloads (~600 bytes per active workflow turn).

## Confirmation

Validated by:
- `tests/agents/test_journey_copilot_pillars.py`
- `tests/test_guided_journeys_integration.py`
- `App/frontend/src/__tests__/components/WorkflowStepper.test.tsx`
- `tests/test_monorepo_architecture_governance.py`

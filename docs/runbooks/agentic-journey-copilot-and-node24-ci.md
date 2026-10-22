# Runbook: Agentic Journey Copilot & Node.js 24 CI Supply-Chain Operations

- **Effective Date:** 2026-10-22
- **Related ADRs:** [ADR-0010](../adr/0010-agentic-journey-copilot-prerequisites-a2ui-and-step-up-auth.md), [ADR-0011](../adr/0011-nodejs-24-runtime-and-github-actions-supply-chain-upgrade.md)
- **Primary Modules:** `App/backend/app/workflows/journey_copilot.py`, `App/backend/app/workflows/registry.py`, `App/backend/app/service.py`, `App/frontend/src/components/WorkflowStepper.tsx`, `App/frontend/src/components/ChatMessage.tsx`

---

## 1. Overview

The **Agentic Journey Copilot** provides seven deterministic customer-experience mechanisms for URA guided workflows without introducing extra LLM calls or exposing taxpayer PII:

1. **Pre-Flight Prerequisite DAG (`evaluate_prerequisites`):** Checks workflow prerequisites (`WORKFLOW_PREREQUISITE_DAG`) against `session.slots` and `CaseFacts`. Missing prerequisites emit a 1-tap `remediation_action` (`"Guide me step by step through <Workflow Name>"`).
2. **Declarative A2UI Form Schedule (`build_form_draft_surface`):** Emits an `a2ui/v1` form preparation surface (`WORKFLOW_FORM_SCHEMAS`). Sensitive slots (`_WORKFLOW_SENSITIVE_SLOTS`) are masked as `"•••• (Masked)"` and free-text values are rendered as `"Recorded"`.
3. **Pushdown Goal Stack & Journey GPS (`push_subworkflow`, `pop_subworkflow`, `build_journey_gps`):** Persists parent workflows in `session.slots["_goal_stack"]` across SQLite/Postgres turns. Completing or cancelling a prerequisite sub-workflow automatically pops back to the parent workflow at its parked step.
4. **Proactive Objective Inference (`infer_taxpayer_objective`):** Maps natural-language taxpayer challenges into `objective_hypothesis` metadata on `/v1/chat` and `/v1/chat/stream` and renders the localized Objective Confirmation Card in `ChatMessage.tsx`.
5. **Step-Up Authentication & Anonymous-to-Authenticated State Migration (`evaluate_step_up_auth`, `elevate_anonymous_workflow_session`):** When an anonymous taxpayer logs in mid-conversation (`user_id` becomes present), `ChatModel._maybe_handle_workflow()` migrates the unowned anonymous workflow row (`conversation_state_key(thread_id, user_id=None)`) to the authenticated state key with zero slot loss (W3C WCAG 2.2 AA SC 3.3.7 *Redundant Entry*).
6. **Multimodal "Snap-to-Fill" & Stall Scaffolding (`bind_document_facts_to_workflow`, `build_stall_scaffolding`):** Auto-fills workflow slots from analysed document metadata and provides progressive field examples (`failed_attempts == 1`) and officer escalation (`failed_attempts >= 2`).
7. **Cross-Channel Resume Codes (`generate_resume_code`, `parse_resume_code`):** Generates `URA-<PREFIX>-S<N>-<HASH>` tokens and routes pasted codes directly to the target workflow before slot validation.

---

## 2. Verification & Diagnostic Commands

```bash
# 1. Run the 7-pillar + QA audit verification suite
PYTHONPATH=App/backend python3 -m pytest tests/agents/test_journey_copilot_pillars.py tests/test_guided_journeys_integration.py -q

# 2. Run frontend component, turnMeta, and 5-locale i18n parity tests
cd App/frontend && bun run test -- src/__tests__/components/WorkflowStepper.test.tsx src/__tests__/lib/turnMeta.test.ts src/__tests__/lib/i18n.test.ts

# 3. Verify ADR catalog & governance compliance
PYTHONPATH=App/backend python3 -m pytest tests/test_monorepo_architecture_governance.py -q
python3 governance/compliance_check.py
```

---

## 3. Node.js 24 LTS CI/CD Supply-Chain Policy (ADR-0011)

All GitHub Actions workflows in `.github/workflows/*.yml` must use `NODE_VERSION: "24"` and pin actions by full 40-character commit SHA to versions running natively on `node24`:

| Action | Pinned Version | `node24` Commit SHA |
| :--- | :--- | :--- |
| `actions/checkout` | `v6.0.2` | `de0fac2e4500dabe0009e67214ff5f5447ce83dd` |
| `actions/setup-python` | `v7.1.0` | `6d4fd22b20465ccc0f7354476588e8df7c9ca0e0` |
| `actions/setup-node` | `v7.0.0` | `820762786026740c76f36085b0efc47a31fe5020` |
| `actions/upload-artifact` | `v7.0.1` | `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` |
| `actions/download-artifact` | `v8.0.2` | `9000827ccba6bdab643e8b6fd33ac0654aef8333` |
| `actions/cache` | `v6.1.0` | `55cc8345863c7cc4c66a329aec7e433d2d1c52a9` |
| `actions/github-script` | `v9.0.0` | `3a2844b7e9c422d3c10d287c895573f7108da1b3` |
| `actions/attest-build-provenance` | `v4.2.2` | `4d101475d8b20a2381f78447822ac1eab6504dd8` |
| `docker/build-push-action` | `v7.4.0` | `c3c9e263c25d99ce0380d002d59b67737d91b0dc` |
| `docker/login-action` | `v4.6.0` | `dbcb813823bdd20940b903addbd779551569679f` |

`tests/agents/test_journey_copilot_pillars.py` enforces in CI that no deprecated Node.js 20 action SHAs or `NODE_VERSION: 20` declarations are reintroduced.

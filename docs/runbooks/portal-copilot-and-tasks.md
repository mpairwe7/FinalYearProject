# Runbook: URA Portal Task Execution, Claude-Style Confirmation & Inbuilt DOM Co-Pilot

- **Subsystem:** `App/backend/app/portal_copilot.py`, `POST /v1/portal/tasks/execute`, `App/frontend/src/components/PortalCopilotCard.tsx`, `App/frontend/src/app/portal/[portalKey]/page.tsx`
- **Governing ADR:** [ADR-0014](../adr/0014-agentic-portal-tasks-and-stuck-user-dom-copilot.md)
- **Feature Flag:** `portal_copilot` (`FLAG_PORTAL_COPILOT`, default `true`, category `kill_switch`)

---

## 1. Overview

The URA Portal Co-Pilot subsystem enables:
1. **Inline Chat Form Elicitation (`card_type="inline_form"`):** Collects missing task fields (TIN, NIN, Tax Head, Amount, Payment Mode, URSB BRN) inside a single accessible form card in chat.
2. **Claude-Style Action Confirmation (`card_type="confirmation_required"`):** Prompts the taxpayer to verify every field (masking sensitive values like NIN with a Show/Hide toggle) and explicitly click **Allow & Execute**, **Edit Details**, or **Disallow**.
3. **Step-Up Authentication & Auto-Return (`card_type="step_up_login"`):** Preserves pending task fields in `sessionStorage` (`ura_pending_portal_continuation`) across `/signin?continue=signin&returnTo=...` and resumes automatically after login (`ADR-0013`).
4. **Inbuilt Portal Co-Pilot Workspace (`/portal/[portalKey]`):** Interactive URA portal screens (`prn_payments`, `tin_registration`, `return_filing`, `tax_clearance`, `efris`, `dts`, `objection_appeals`, `motor_vehicle`, `get_refund`) with an embedded AI Co-Pilot side rail that reads live DOM telemetry (`portal_context`, with passwords/OTPs/PINs/CVVs scrubbed), scrolls to off-screen validation errors, spotlights fields, and auto-fills safe inputs.

---

## 2. Verification & Health Checks

Run the automated backend and frontend verification suites:

```bash
# Backend unit, integration, security & privacy suite
PYTHONPATH=App/backend python3 -m pytest \
  tests/agents/test_portal_copilot_phase_a.py \
  tests/agents/test_portal_copilot.py \
  tests/test_all_endpoints_e2e.py \
  tests/test_monorepo_architecture_governance.py -q

# Frontend Vitest + 5-locale i18n parity + ESLint
cd App/frontend && bun run test src/__tests__/components/PortalCopilotCard.test.tsx src/__tests__/lib/i18n.test.ts && bun run lint
```

---

## 3. Emergency Kill-Switch Procedure

If portal task cards or `/v1/portal/tasks/execute` need to be disabled immediately without redeploying:

1. **Per-Replica Runtime Toggle (Admin API / Console):**
   ```bash
   curl -X PATCH http://localhost:8887/v1/admin/flags/portal_copilot \
     -H "Authorization: Bearer <ADMIN_JWT>" \
     -H "Content-Type: application/json" \
     -d '{"enabled": false}'
   ```
2. **Cluster-Wide Environment Variable:**
   Set `FLAG_PORTAL_COPILOT=false` in the deployment environment and restart the API replicas. When disabled, `/v1/chat` bypasses `portal_copilot.evaluate_turn` and `POST /v1/portal/tasks/execute` returns `503 Service Unavailable`.

---

## 4. Auditing Confirmed & Declined Portal Tasks

When `FLAG_AUDIT_LEDGER=true`, every `allow` or `disallow` decision on `POST /v1/portal/tasks/execute` appends a hash-chained row (`event_type="portal_task_executed"` or `"portal_task_declined"`) containing `task_id`, `portal_key`, `decision`, `live`, `mode`, and `idempotency_key` (never raw NINs or passwords). Inspect events via `GET /v1/admin/audit/events` or the `/admin/audit` console.

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
  tests/agents/test_browser_agent_assurance_2026_11.py \
  tests/agents/test_portal_copilot_adversarial_eval.py \
  tests/test_all_endpoints_e2e.py \
  tests/test_monorepo_architecture_governance.py -q

# Frontend Vitest + 5-locale i18n parity + ESLint
cd App/frontend && bun run test src/__tests__/components/PortalCopilotCard.test.tsx \
  src/__tests__/components/PortalConfirmGuard.test.tsx src/__tests__/config/securityHeaders.test.ts \
  src/__tests__/lib/i18n.test.ts && bun run lint
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

When `FLAG_AUDIT_LEDGER=true`, decisions on `POST /v1/portal/tasks/execute` reach the hash-chained ledger (ADR-0038). Before 2026-11-10 none did: the route called a function that does not exist and logged the error at debug level (G541), so the ledger holds no portal rows older than the deploy of ADR-0038.

| Row | Written | Holds |
| --- | --- | --- |
| `portal_task_approved` | Before the portal is reached. If it cannot be written the task does not run (503). | `task_id`, `portal_key`, `tier`, `idempotency_key`, `params_sha256`, `displayed_fields_sha256`, and the evidence of the confirmation: `display_digest_verified`, `session_bound`, `authenticated`, `high_value`, `high_value_acknowledged`, `review_ms`, `sec_fetch_site` |
| `portal_task_executed`, `portal_task_error` | After the task ran | `status`, `live`, `mode`, `receipt_id`, the two digests |
| `portal_task_declined`, `portal_task_prepared` | After the decision | the same fields, without a receipt |

No row holds a NIN, TIN, amount or password. Administrators (`ura_admin`) and auditors (`ura_auditor`) are refused (`403 Forbidden`) from executing or mutating live portal tasks (`AGENTS.md` Invariant 2). Inspect events via `GET /v1/admin/audit/events` or the `/admin/audit` console.

`review_ms` is the time from the card being signed to the approval. It is evidence for a reviewer, not a gate: an approval a few hundred milliseconds after the card appeared was not read by a person. `sec_fetch_site` is `absent` for a client that is not a browser.

An outcome row that cannot be written does not undo the decision. It raises `audit_append_failed_total{event_type}` and an error log; the approval row is already in the ledger.

---

## 5. One Approval, Once (ADR-0038)

The shared `workflow_sessions` row `portal_task_decision:<digest>` decides who runs a proposal. Its state reads:

| `status`, `current_step_idx` | Meaning |
| --- | --- |
| `active`, 0 | Released: nothing ran, or the run sent nothing |
| `active`, 1 | In flight: a worker holds the lease |
| `completed`, 1 | Executed; `slots_json` is the receipt replayed to a repeat request |
| `cancelled`, 1 | Declined |

What an operator sees, and what to do:

| Symptom | Cause | Action |
| --- | --- | --- |
| `409` on Allow | Another request holds the lease, or a decline arrived after the task ran | None: the first request's result stands. The taxpayer reloads to see it. |
| `503` "could not be recorded" | The decision store or the ledger is unreachable. Nothing was sent. | Restore the database; the taxpayer retries the same card within 15 minutes. |
| A proposal stays "in flight" | The worker died between the portal call and the receipt write. The lease lapses after `PORTAL_TASK_STATE_TTL_SECONDS` (900), with the signed state. | Check the connector for the task before telling the taxpayer to submit again: the task may have run. |
| `session_required` card | An API caller sent no `X-Session-ID` and is not signed in | Send the header. The web client always does. |
| `400` "displayed_fields_sha256 is required" | An API caller allowed without the digest | Send it; see `docs/API_REFERENCE.md`. |
| `access_denied` "your sign-in is not linked to a TIN" | The sign-in's subject is not a TIN and the caller has linked no simulation profile in this session (G565). | Outside production: connect a simulation profile from the composer's connector menu; account tasks then apply to that profile's TIN only. In production: expected, and safe, until the identity provider issues a verified TIN passed as `authenticated_tin`. Do not loosen `check_subject_tin_ownership`. |
| `403` "needs your consent" or "role may not run" | A sign-in task without the purpose it names (`ura_account_access`, `ura_actions`) or for a role its tool does not list | Grant the purpose, or use an account with the role. |
| `400` "already declined" on a card the taxpayer edited | The card was replaced by an edit, which retires it | Use the card the edit returned. |
| "Check my tax obligations" shows the account profile | A check reads; it no longer proposes registering a tax head (G556) | To register one, the taxpayer asks to register or add it, and names the tax head. |

---

## 6. What a Page May Not Do (ADR-0038)

DOM telemetry (`portal_context`) is evidence of what a page shows. To check the rules hold after a change to a recipe or to the sanitiser:

```bash
PYTHONPATH=App/backend python3 -m pytest tests/agents/test_portal_copilot_adversarial_eval.py -q
```

- A selector in `dom_actions` is one the server wrote (`_FIELD_SELECTOR_MAP`, a recipe in `portal_navigator_data.py`) or is empty. To support a new field by selector, add it to the map; never read one from telemetry.
- A new recipe step needs no gate of its own: `harden_dom_actions` makes every click wait for the taxpayer and drops a step that targets a credential field or has an unknown `action_type`.
- To add an attack, add a line to `evals/portal_copilot_scenarios/adversarial.jsonl`.

The frontend may be framed only by itself and `https://huggingface.co` (`FRAME_ANCESTORS`, baked at build). Check a deployment with:

```bash
curl -sI https://<host>/ | grep -i content-security-policy   # frame-ancestors must not name *.hf.space
```

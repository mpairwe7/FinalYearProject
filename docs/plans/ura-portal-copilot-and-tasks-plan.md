# Plan: URA Portal Task Execution, Claude-Style Action Confirmation, Inline Chat Elicitation & Inbuilt Stuck-User DOM Co-Pilot

Status: **Implemented & Verified (Phases A–F complete; see [`ADR-0014`](../adr/0014-agentic-portal-tasks-and-stuck-user-dom-copilot.md)).** Authored 2026-10-23 against `dev` @ `df69117e9e`.  
Audience: a **new** coding session with no memory of the conversation that produced this plan. Read each referenced file before editing it.

> **Builds upon:** [`ADR-0005`](../adr/0005-mcp-mrtr-transport-hardening-and-csa-aicm-maestro.md) (MCP 2026-07-28 Multi Round-Trip State `mrtr.py`), [`ADR-0010`](../adr/0010-agentic-journey-copilot-prerequisites-a2ui-and-step-up-auth.md) (Agentic Journey Copilot & A2UI Form Preview), and [`ADR-0013`](../adr/0013-session-bound-workflow-adoption-and-verified-resume-codes.md) (Session-Bound Workflow Adoption, Request-Bound Sign-In State, Owner-Scoped State Keys & Verified Resume Codes).

---

## Table of Contents

0. Ground Rules, Repository Invariants & 2026 Standards Baseline (Read First)
1. Core UX & Architectural Objectives — All 7 User Situations Covered
2. Target Architecture & 4-Tier Privacy / Statutory Classification Matrix
3. Phase A — Feature Flag, 4-Tier Portal Task Policy Engine & Expanded DOM Recipe Catalog (`portal_copilot.py` & `portal_navigator_data.py`)
4. Phase B — Inline Chat Form Elicitation, Claude-Style Allow/Disallow Confirmation Protocol & `POST /v1/portal/tasks/execute` (`models.py`, `main.py`, `service.py`)
5. Phase C — Seamless Chat-Initiated Login, Auto-Return (`returnTo`) & Post-Login Task Continuation (`signin/page.tsx`, `signin/callback/page.tsx`, `useChatStore.ts`)
6. Phase D — Screenshot-to-Co-Pilot Portal Handoff, Auth-Gated Deep Links & Incomplete-Screenshot Completeness Guard (`documents.py`, `portal_copilot.py`)
7. Phase E — Inbuilt Portal Co-Pilot Workspace (`/portal/[portalKey]`), Live DOM Telemetry Bridge (Approach 3 + Approach 2 Fallback) & Interactive Chat Cards (`App/frontend`)
8. Phase F — Evaluation Suite, Security & Privacy Regression Gate, `ADR-0014`, Operational Runbook & Living Documentation

---

## 0. Ground Rules, Repository Invariants & 2026 Standards Baseline

### 0.1 Repository Rules (from `AGENTS.md`, `App/backend/AGENTS.md`, `App/frontend/AGENTS.md`, `docs/AGENTS.md`)

- **Runtime Package Root:** Backend runtime imports stay under `App/backend/app` (imported as `app`). Frontend App Router lives under `App/frontend/src/app`. **Never move `App/backend` or `App/frontend`.**
- **Git & PR Workflow (`pr-ci-coderabbit-workflow` & `gh-mpairwe7`):**
  - Always use `~/bin/gh` (never bare `gh`) and switch to `mpairwe7` immediately before every GitHub operation:
    ```bash
    ~/bin/gh auth switch --user mpairwe7
    ~/bin/gh api user --jq .login   # must output: mpairwe7
    ```
  - Every phase or logical increment lands via a dedicated feature branch (`feat/...`) branched from `origin/dev` → PR against `dev` → 100% green CI → resolve all CodeRabbit comments → squash-merge. Never push directly to `dev` or `main`.
  - Never use GitHub auto-closing keywords (`fixes #N`, `closes #N`, `resolves #N`) in commit messages or PR bodies (`no-issue-autoclose`).
- **Feature Flags:** Any new flag must land in **all three** files in the same commit:
  1. `App/backend/app/flags.py`
  2. `docs/RAG_ARCHITECTURE.md` (including updating the total flag count in the summary paragraph)
  3. `.env.example`
- **FastAPI Route Coverage:** Any new FastAPI route must be added to `tests/test_all_endpoints_e2e.py` (`EXPECTED_ENDPOINTS` + `COVERAGE`), and backend behaviour must be exercised by tests under `tests/` (which CI runs for the `--cov=App/backend` gate).
- **Session & Sign-In Invariants (`ADR-0013`):**
  - "Signed in" is a property of the **current request** (`bool(ctx.user_id)`), never read back from stored workflow slots.
  - On sign-in, anonymous state is adopted **only** from the anonymous session key the request itself proves (`db.adopt_workflow_session` / `conversation_state_key(thread_id, session_id=session_id, user_id=None)`). Never add candidate keys derived from `conversation_id` alone.
- **Simulation Honesty (`plugins/AGENTS.md`):**
  - In development/prototype mode, local connectors (`efris`, `digital_tax_stamps`, `ursb`, `bwims`, `tin_registration`, `payment_system`) and `URA_ACCOUNT_API_MODE=mock` carry `live=False` and `mode="simulation"`. Never claim a simulated PRN, TIN, or invoice is a live URA production record. In `APP_ENV=production`, simulators fail closed (`503` / `409`).
- **Localization Invariant (`App/backend/AGENTS.md` & `App/frontend/AGENTS.md`):**
  - Backend answers are generated in English in `_generate_en` and localized in **one** place (`localize_reply`) through `mt.py` (`figures_survived` guard).
  - All taxpayer-facing UI strings in `App/frontend` must use dictionary keys in `src/lib/i18n/{en,lg,sw,nyn,ach}.ts` via `useTranslation()`.

### 0.2 October 2026 Industry Standards & Legal Baseline

1. **MCP 2026-07-28 Elicitation (`form` mode + `url` mode) & SEP-0000 Human-in-the-Loop Confirmation:**
   - Tools and portal tasks that require user input emit structured **Form Mode Elicitation** schemas so the chat client renders native input controls right inside the conversation rather than asking 5 separate text questions.
   - Tools with `requires_confirmation=True` halt before side effects, emit an HMAC-SHA256 signed `requestState` (`App/backend/app/mcp/mrtr.py`), and require an explicit human verdict (`action: "accept"` vs. `action: "decline"`).
2. **Google A2UI v0.9.1 / AG-UI Declarative Component Surfaces:**
   - Agent-to-UI payloads use declarative, pre-approved component schemas (`inline_form_card`, `action_confirmation_card`, `portal_handoff_card`, `dom_action_recipe`) rendered by native React components—never executable code or raw HTML injection.
3. **OWASP Top 10 for LLM & Agentic Applications (August–October 2026 Edition):**
   - Directly mitigates **`LLM03:2026 Excessive Agency`** and **`ASI01` / `ASI02` / `ASI09`** via Least-Agency 4-tier task gating, pre-execution dry-run diff previews, explicit human Allow/Disallow confirmation, and per-action owner/session verification.
4. **IETF RFC 9470 (OAuth 2.0 Step-Up Authentication) & W3C WCAG 2.2 AA SC 3.3.7 (Redundant Entry):**
   - When a taxpayer triggers an action or portal screen that requires login, the assistant preserves 100% of collected fields and conversation context across the OIDC redirect (`returnTo`) so the taxpayer never re-enters data after signing in.
5. **Uganda Data Protection and Privacy Act (DPPA) 2019 & Tax Procedures Code Act (TPCA) Cap. 343:**
   - Enforces **TPCA s. 75 (Official Secrecy)** (authenticated user A can never query user B's private tax obligations or ledger) and **TPCA s. 16 / s. 58–65** (final statutory return declarations and formal Section 24 objections must be personally confirmed/signed by the taxpayer).

---

## 1. Core UX & Architectural Objectives — All 7 User Situations Covered

The implementation must make portal tasks and stuck-user recovery **effortless** for the taxpayer across every real-world situation:

| Situation | How the Taxpayer Interacts | What the URA AI & UI Must Do |
| --- | --- | --- |
| **Situation 1: User asks the AI to perform a portal task needing inputs** *(e.g., "Generate a VAT PRN for me", "Apply for an individual TIN", "Order digital tax stamps")* | User types or clicks a task request in chat without supplying all required fields. | Instead of multi-turn interrogation, the AI renders an **Inline Chat Input Form Card (`PortalInlineFormCard`)** right inside the chat bubble with text inputs, dropdowns, and format hints (pre-filled with any known profile/chat facts). User fills the fields in one place and clicks **Continue to Review**. |
| **Situation 2: User reviews & confirms a consent-gated action (Claude-style Allow / Disallow)** | User submits the inline form (or provides all parameters in their prompt) for a Tier 2/3 action. | The AI **explicitly instructs the user to verify every field before confirming** and renders a **Claude-Style Action Confirmation Card (`PortalConfirmationCard`)** showing the exact parameters (with sensitive fields like NIN masked with a reveal toggle), target URA system, and three actions: **Allow & Execute**, **Edit Details**, or **Disallow**. |
| **Situation 3: Task or portal screen requires login (`Step-Up Auth` from Chat)** | An unauthenticated user asks to check their tax obligations, apply for a TCC, or execute an authenticated portal task. | The AI renders an **Inline Login & Auto-Return Card** in chat. Clicking **"Sign in to URA Account & Return Here"** saves the pending task/fields (`ura_pending_portal_continuation`), takes the user to `/signin?continue=signin&returnTo=...`, completes OIDC login, and **automatically brings the user right back to the chat** on their logged-in profile (`adopt_workflow_session` per ADR-0013), immediately resuming the task! |
| **Situation 4: User asks in chat about a portal process or wants to do it on the portal** | User asks *"How do I file my VAT return on the portal?"* or *"Where do I register a payment?"* | Alongside the chat answer/form, the AI provides a **1-Click Co-Pilot Portal Link** (`/portal/<portal_key>?intent=<intent>&conversation_id=<id>&copilot=1`) that opens the URA portal surface **with the inbuilt AI Co-Pilot already embedded**, and explains: *"Open this URA Portal page with the built-in AI Co-Pilot active — whenever you get stuck, click the AI Co-Pilot button to highlight the exact field or complete the step for you."* |
| **Situation 5: User uploads a screenshot of a PUBLIC portal page asking for help** | User uploads a screenshot of PRN Payment, TIN Application, or EFRIS/DTS Verification where they are stuck. | The AI diagnoses the visible issue AND renders a **"Continue on This Exact Portal Screen (with Built-In AI Co-Pilot) ↗"** card (`/portal/<detected_portal>?step=<detected_step>&conversation_id=<id>`) pre-populated with any TIN, PRN, amount, or tax head extracted from the screenshot so the AI can assist them live with zero further screenshots needed. |
| **Situation 6: User uploads a screenshot of an AUTHENTICATED portal page while NOT logged in** | User uploads a screenshot of e-Services Return Filing, TCC, Objections, Account Ledger, or EFRIS Invoicing while anonymous. | The AI detects that the screenshot's target route requires login (`requires_auth=True`), explicitly tells the user: *"This URA portal screen requires signing in first. Use the login button below — once your login is accepted, you will be taken directly to where you were with the built-in AI Co-Pilot active"*, and provides a 1-click **"Sign In & Continue to `<Screen Name>` with AI Co-Pilot →"** button (`/signin?continue=signin&returnTo=%2Fportal%2F...`). |
| **Situation 7: User uploads an INCOMPLETE / cropped screenshot OR gets stuck inside the Inbuilt Portal (`/portal/[portalKey]`)** | User uploads a partial screenshot missing the form header/error banner, OR stalls/hits an error inside the inbuilt portal page. | 1. **For incomplete screenshots:** The AI flags what is cut off, offers quick-select chips to disambiguate, and gives the link to open the screen with the **Inbuilt Co-Pilot**. <br>2. **Inside the Inbuilt Portal (Approach 3) / Browser Companion (Approach 2):** The page streams a live `PortalContextEnvelope` (full DOM route, step, fields, and validation errors) to the AI, which **scrolls to off-screen errors, spotlights the exact input, or auto-fills safe fields** in 1 click. |

---

## 2. Target Architecture & 4-Tier Privacy / Statutory Classification Matrix

### 2.1 End-to-End Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│                        Taxpayer Surfaces (App/frontend & Companion)                      │
│                                                                                          │
│  1. Chat Surface (src/app/page.tsx & ChatMessage.tsx)                                    │
│     ├── PortalInlineFormCard       (Collects missing individual/company/tax fields)      │
│     ├── PortalConfirmationCard     (Claude-style "Verify info -> Allow / Disallow")      │
│     ├── PortalStepUpLoginCard      (1-click Sign-In -> Auto-Return to Chat or Portal)    │
│     └── PortalCoPilotLaunchCard    (Deep link to /portal/[portalKey] with Co-Pilot on)   │
│                                                                                          │
│  2. Inbuilt Portal Co-Pilot Workspace (src/app/portal/[portalKey]/page.tsx — Approach 3) │
│     ├── Interactive URA Portal Forms (e-Services PRN, TIN Reg, Returns, TCC, EFRIS, DTS) │
│     ├── Live DOM Telemetry Collector -> emits PortalContextEnvelope (passwords scrubbed) │
│     ├── DOM Action Executor (scrollIntoView, pulsing spotlight ring, safe field autofill)│
│     └── Embedded Co-Pilot Side Rail (triggered on error, >15s stall, or "Help Me Here")  │
│                                                                                          │
│  3. Browser Companion WebExtension Contract (Approach 2 Fallback for external *.ura.go.ug)│
│     └── Uses identical PortalContextEnvelope & get_portal_dom_recipe() JSON contract     │
└───────────────────────────────────────────────┬──────────────────────────────────────────┘
                                                │ REST (/v1/chat, /v1/portal/tasks/execute)
                                                │ & SSE (/v1/chat/stream)
                                                ▼
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│                     FastAPI Backend Host (App/backend/app/portal_copilot.py)             │
│                                                                                          │
│  ├── 4-Tier Privacy & Statutory Policy Gate (classify_portal_task)                       │
│  │     • Tier 1: Autonomous Read/Verify (tin_search_verify, payment_view_status, ...)    │
│  │     • Tier 2: Delegated Write with Mandatory Allow/Disallow + HMAC state              │
│  │     • Tier 3: Authenticated Account Read (requires JWT/OIDC + TPCA s.75 TIN match)    │
│  │     • Tier 4: Statutory Co-Pilot Only (stages A2UI + DOM recipe; human final-signs)   │
│  ├── Inline Elicitation Schema Builder (build_inline_elicitation_form)                   │
│  ├── HMAC-SHA256 Proposal Signer & Owner-Scoped Verifier (mrtr.py + ADR-0013 state key)  │
│  ├── Screenshot Completeness & Auth-Requirement Analyzer (diagnose_portal_screenshot)    │
│  └── Live Stuck-User Telemetry Analyzer (analyze_portal_context -> dom_actions)          │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 The 4-Tier Privacy, Statutory & Security Classification Matrix

Every URA portal service is deterministically mapped in `PORTAL_TASK_CATALOG` (`App/backend/app/portal_copilot.py`):

| Tier | Policy Class | Portal Tasks / Tools | Auth Required? | Inline Form in Chat? | Claude-Style Allow/Disallow? | Privacy & Statutory Guardrails |
| --- | --- | --- | --- | --- | --- | --- |
| **Tier 1 (Green)** | `AUTONOMOUS_VERIFY` | • `tin_search_verify` (Verify TIN)<br>• `payment_view_status` (Check PRN Status)<br>• `dts_verify_stamp` (Verify Kakasa Stamp)<br>• `ursb_verify_business` (Verify URSB BRN)<br>• `payment_verify_advance_tax` | No | **Yes**, if the user didn't include the TIN / PRN / Stamp serial in their message | No (read-only verification of public reference) | Redacts any unstated third-party PII; clearly labels simulator mode (`live=False`) when not connected to live URA. |
| **Tier 2 (Amber)** | `DELEGATED_CONFIRM` | • `payment_generate_prn` (Generate PRN)<br>• `payment_reactivate_prn` (Reactivate Expired PRN)<br>• `tin_apply_individual` (Individual TIN Draft/App)<br>• `tin_apply_non_individual` (Company/NGO TIN App)<br>• `dts_order_stamps` (Order Digital Stamps)<br>• `dts_activate_stamps` (Activate Stamp Batch)<br>• `efris_stock_management` (Adjust EFRIS Stock)<br>• `ursb_register_business` (URSB Name/Reg) | Optional for PRN / TIN App; Required for EFRIS / DTS | **Yes** — renders inline form fields for any missing parameters (e.g., NIN, Surname, Tax Head, Amount, BRN) | **Mandatory** — AI explicitly tells user to verify every field and click **Allow & Execute** or **Disallow** | • HMAC-SHA256 signed `request_state` bound to `owner_key` (`ADR-0013`)<br>• Masks NIN (`CF92******12A`) in summary with reveal toggle<br>• Requires `idempotency_key` to prevent duplicate PRNs/applications. |
| **Tier 3 (Orange)** | `AUTHENTICATED_ACCOUNT_READ` | • `ura_account_profile` (Taxpayer Profile)<br>• `tin_tax_obligations` (Filing Obligations & Deadlines)<br>• `efris_taxpayer_status` (EFRIS Enrollment Status)<br>• `dts_taxpayer_status` (DTS Account Status)<br>• `ursb_compliance_status`<br>• `bwims_consignment_status` & `bwims_warehouse_inventory` | **Mandatory** (`bool(ctx.user_id)`) | **Yes** (or pre-filled from authenticated profile) | **Yes** (Consent to query account records under `ura_account_access`) | • **TPCA Cap. 343 s. 75 (Official Secrecy):** If `ctx.user_id` is empty, emits **Step-Up Login Card** with `returnTo`.<br>• Enforces **Subject-TIN Ownership**: Authenticated User A cannot query User B's private obligations. |
| **Tier 4 (Red)** | `STATUTORY_COPILOT_ONLY` | • `return_filing_submit` (Final VAT / PAYE / Income Tax Return Declaration)<br>• `objection_appeals_lodge` (TPCA s. 24 Formal Objection)<br>• `tax_clearance_apply` (TCC Statutory Declaration)<br>• `get_refund_claim` (Tax Refund Claim)<br>• `efris_fiscal_invoice` & `efris_credit_note` (Live Legal FDN Issuance)<br>• `payment_checkout_settle` (Bank / MoMo PIN Debit) | **Mandatory** for portal access | **Yes** — collects figures/details to pre-populate the A2UI draft & inbuilt portal form | **Co-Pilot Staging Only** — AI pre-fills the portal form up to the final step, then hands control to the user | • **TPCA s. 16 & s. 58–65:** Taxpayer is legally accountable for signed declarations.<br>• **Prohibited Selectors:** AI never touches `password`, `otp`, `pin`, `cvv`, or final statutory declaration signature without explicit user click on the portal. |

---

## 3. Phase A — Feature Flag, 4-Tier Portal Task Policy Engine & Expanded DOM Recipe Catalog

### 3.1 Register Feature Flag `portal_copilot`
1. **`App/backend/app/flags.py`:**
   Add `Flag("portal_copilot", True, "Phase 42 (FLAG_PORTAL_COPILOT): 4-tier URA portal task execution, Claude-style HITL confirmation cards, inline form elicitation, and inbuilt stuck-user DOM co-pilot.", category="kill_switch", owner="agentic_runtime")` to `_REGISTRY`.
2. **`docs/RAG_ARCHITECTURE.md`:**
   Add `portal_copilot` (`FLAG_PORTAL_COPILOT`, default `on`) to the Feature Flags table and increment the total flag count in the summary sentence below the table (`85` → `86`).
3. **`.env.example`:**
   Add `# FLAG_PORTAL_COPILOT=true` under a `# Phase 42` comment header.

### 3.2 Expand `get_portal_dom_recipe` & Portal Metadata (`App/backend/app/tools/portal_navigator_data.py`)
1. Add `requires_auth: bool` metadata to `EXTERNAL_URA_PORTALS` entries (or a helper `portal_requires_auth(portal_key: str, intent: str = "") -> bool`):
   - **Public (`requires_auth = False`):** `tin_registration` (initial application & TIN search), `prn_payments` (PRN registration & status check), `document_authentication`, `track_status`, `whistle_blow`, `elearning`, `laws_and_acts`, `research_publications`.
   - **Authenticated (`requires_auth = True`):** `e_services` (return filing, ledger, amendment), `efris` (issuing fiscal invoices, credit notes, stock management), `tax_clearance`, `objection_appeals`, `voluntary_disclosure`, `get_refund`, `tax_incentives`, `choose_tax_agent`, `asycuda`, `bwims`, `motor_vehicle` (logbook/plate registration), `dts` (stamp ordering/activation).
2. Expand `get_portal_dom_recipe(portal_key, state, entities)` in `App/backend/app/tools/portal_navigator_data.py` so every core URA workflow has deterministic DOM selectors, tooltips, pre-fill bindings, and `security_gate` (`"safe"` vs. `"requires_confirmation"`):
   - `prn_payments` (existing 5-step recipe — keep & enrich with NTR / MDA support)
   - `efris` (existing 3-step recipe — keep & enrich with offline sync recovery step)
   - `tin_registration` (existing 3-step recipe — keep & enrich with individual vs. non-individual URSB BRN fields)
   - `return_filing` / `e_services` (Return Type & Period selector -> XLSM Macro-Validated Upload input spotlight -> Declare & Submit Return with `security_gate="requires_confirmation"`)
   - `tax_clearance` (TIN input -> TCC Purpose selector -> Compliance Pre-Check spotlight -> Submit TCC Application with `security_gate="requires_confirmation"`)
   - `objection_appeals` (Assessment Reference Number input -> 45-Day Grounds of Objection textarea & schedule upload -> Lodge Formal Section 24 Objection with `security_gate="requires_confirmation"`)
   - `motor_vehicle` / `asycuda` (Customs SAD `C-Number` / 17-char Chassis VIN input -> Registration / ITMS Digital Plate Category selector -> Proceed to Assessment with `security_gate="requires_confirmation"`)
   - `dts` (Excise Product Category selector -> Stamp Reel Quantity input -> Submit Stamp Order with `security_gate="requires_confirmation"`)
   - `get_refund` (Refund Tax Head & Claim Amount -> Registered EFT Bank Account spotlight -> Submit Refund Claim with `security_gate="requires_confirmation"`)
3. Expand `export_companion_manifest()["security_policy"]["prohibited_selectors"]` to include:
   - `"input[name*='cvv']"`, `"input[name*='secret']"`, `"input[autocomplete='cc-csc']"` alongside existing password/OTP/PIN selectors.

### 3.3 Create `App/backend/app/portal_copilot.py`
Implement the core backend engine with the following pure, well-typed functions and dataclasses:

1. **`PortalFieldSpec` & `PortalTaskSpec` Catalog (`PORTAL_TASK_CATALOG`):**
   - Defines each supported task (`task_id`, `tool_name`, `portal_key`, `title`, `tier: PortalTaskTier`, `requires_auth: bool`, `required_consent: str | None`, `fields: tuple[PortalFieldSpec, ...]`, `verification_warning: str`, `statutory_citation: str`).
   - Each `PortalFieldSpec` specifies:
     - `name`: parameter key (e.g. `"tin"`, `"tax_head"`, `"amount_ugx"`, `"payment_mode"`, `"nin"`, `"surname"`, `"given_name"`, `"date_of_birth"`, `"mobile_number"`, `"email"`, `"legal_name"`, `"entity_type"`, `"ursb_brn"`, `"prn"`, `"stamp_serial"`, `"product_category"`, `"quantity"`)
     - `label`: plain-language label (e.g. `"10-Digit Taxpayer TIN"`, `"Tax Head / Revenue Type"`)
     - `input_type`: `"text"` | `"number"` | `"select"` | `"date"` | `"email"` | `"tel"`
     - `required`: `bool`
     - `placeholder` & `helper_text`: concrete examples (e.g. `"100-XXX-XXXX"`, `"CM-14-CHAR-NIN"`)
     - `options`: optional tuple of `{"value": ..., "label": ...}` for `"select"` fields (e.g. Tax Heads: `VAT (0010)`, `Income Tax (0011)`, `PAYE (0012)`, `Withholding Tax (0013)`, `Rental Tax (0014)`, `Local Excise Duty (0020)`, `Non-Tax Revenue / Passport / Permit (0090)`)
     - `sensitive`: `bool` (True for `nin`, `mobile_number`, `email`, `date_of_birth` — masked by `mask_sensitive_value()` in confirmation cards).
2. **`detect_portal_task_intent(message: str, portal_context: dict | None = None) -> PortalTaskIntent | None`:**
   - Detects when a taxpayer asks the AI to perform a portal action (e.g., *"generate a PRN"*, *"register a payment for me"*, *"apply for an individual TIN"*, *"register a company TIN"*, *"check my PRN status"*, *"reactivate my expired PRN"*, *"verify TIN 1000..."*, *"verify stamp..."*, *"order digital tax stamps"*, *"check my tax obligations"*, *"file my VAT return for me"*).
   - Automatically extracts any entities already stated in `message` or `portal_context` (`tin`, `prn`, `nin`, `amount_ugx`, `tax_head`, `payment_mode`, `stamp_serial`, `ursb_brn`, `email`, `mobile_number`) so the user never has to type what they already said.
3. **`build_portal_task_card(...) -> dict[str, Any]`:**
   - Evaluates the 4-Tier Policy for the detected task:
     - **Gate 1 — Step-Up Authentication Check:** If `spec.requires_auth` is True and `not user_id`:
       - Returns a `card_type = "step_up_login"` payload containing:
         - `task_id`, `portal_key`, `title`
         - `login_reason`: explicit explanation of why sign-in is required (citing TPCA s. 75 Official Secrecy or portal authentication)
         - `return_to_chat_url`: `f"/?conversation_id={conversation_id}&auth_resumed=1&pending_task={task_id}"`
         - `return_to_portal_url`: `f"/portal/{spec.portal_key}?intent={task_id}&conversation_id={conversation_id}&copilot=1"`
         - `signin_chat_href`: `f"/signin?continue=signin&returnTo={urllib.parse.quote(return_to_chat_url, safe='')}"`
         - `signin_portal_href`: `f"/signin?continue=signin&returnTo={urllib.parse.quote(return_to_portal_url, safe='')}"`
         - `preserved_fields`: any fields already extracted from the user's message so zero data is lost across login.
     - **Gate 2 — Missing Inputs -> Inline Chat Form Card (`card_type = "inline_form"`):**
       - If any required `PortalFieldSpec` is missing or fails format validation (e.g. TIN not 10 digits, NIN not 14 chars, amount `<= 0`):
       - Returns `card_type = "inline_form"` with `fields` (pre-filled with already known values, plus `validation_error` on any invalid field), `submit_label` (`"Review & Verify Details"` for Tier 2/3, `"Verify Now"` for Tier 1), and a direct `copilot_portal_url` (`/portal/<portal_key>?...`) in case the user prefers to use the full inbuilt portal view.
     - **Gate 3 — Tier 4 Statutory Boundary (`card_type = "statutory_copilot_handoff"`):**
       - If `spec.tier == PortalTaskTier.STATUTORY_COPILOT_ONLY`:
       - Returns a card that pre-populates the A2UI schedule + `dom_actions` recipe and provides a 1-click **"Open in URA Portal with AI Auto-Fill Ready →"** button (`/portal/<portal_key>?intent=<task_id>&conversation_id=<id>&autofill=1`), explicitly explaining that the AI will fill all safe fields for them, and the taxpayer only needs to review and click the final statutory declaration button.
     - **Gate 4 — Tier 2 / Tier 3 Claude-Style Confirmation Card (`card_type = "confirmation_required"`):**
       - When all required fields are present and valid:
       - Signs the proposal payload using `mrtr.wrap_request_state({"task_id": spec.task_id, "params": validated_params, "owner_key": owner_key, "user_id": user_id or "", "issued_at": int(time.time()), "idempotency_key": idempotency_key})`.
       - Returns `card_type = "confirmation_required"` with:
         - `verification_prompt`: **"Please carefully verify all information below before allowing this action on the URA portal."**
         - `display_fields`: list of `{"name", "label", "value" (masked if sensitive), "raw_value", "sensitive"}`
         - `allow_label`: `"Allow & Execute"`
         - `disallow_label`: `"Disallow"`
         - `edit_label`: `"Edit Details"`
         - `request_state`: the HMAC-signed state object
         - `idempotency_key`: deterministic SHA-256 key of `(owner_key, task_id, canonical_params)`
         - `simulation_mode`: `True` (unless the target connector is configured as `live` in production).
     - **Gate 5 — Tier 1 Autonomous Execution (`card_type = "executed_result"`):**
       - If `spec.tier == PortalTaskTier.AUTONOMOUS_VERIFY` and all required fields are present, executes the read-only verification tool immediately (`execute_portal_task`) and returns the structured result card + grounded summary.
4. **`analyze_portal_context(portal_context: dict[str, Any], *, user_id: str | None = None, conversation_id: str = "") -> dict[str, Any]`:**
   - Sanitizes `portal_context` by stripping any keys matching `password`, `otp`, `pin`, `cvv`, `secret`, `token`, `passcode` (never logged or echoed).
   - Diagnoses why the user is stuck from `validation_errors`, `error_banner`, `empty_required_fields`, `stalled_duration_ms`, and `active_step`.
   - Builds the exact `dom_actions` (`get_portal_dom_recipe`) with `fill_value` bound from the user's known entities/slots and `scroll_to: True` on the first failing selector so off-screen errors are scrolled into view and spotlighted.
   - Attaches `copilot_portal_url` and `escalation_context` (ready for `POST /v1/escalate` if the user asks for an officer).

### 3.4 Phase A Verification
Run unit tests in `tests/agents/test_portal_copilot_phase_a.py` verifying:
- `portal_copilot` flag registration in `flags.py`, `docs/RAG_ARCHITECTURE.md`, and `.env.example`.
- All 4 tiers in `PORTAL_TASK_CATALOG` and `get_portal_dom_recipe` across all 9 portal keys.
- Password/OTP/PIN/CVV selector and field scrubbing in `analyze_portal_context`.

---

## 4. Phase B — Inline Chat Form Elicitation, Claude-Style Allow/Disallow Confirmation Protocol & `POST /v1/portal/tasks/execute`

### 4.1 Request & Response Schemas (`App/backend/app/models.py`)
1. Add structured models to `App/backend/app/models.py`:
   - `PortalFieldError(BaseModel)`: `field: str`, `message: str`, `selector: str = ""`
   - `PortalContextInput(BaseModel)`:
     - `portal_id: str = Field(..., pattern=r"^[a-z0-9_]{1,64}$")`
     - `current_route: str = Field("", max_length=240)`
     - `active_step: str = Field("", max_length=160)`
     - `form_id: str = Field("", max_length=64)`
     - `visible_fields: dict[str, str] = Field(default_factory=dict)`
     - `empty_required_fields: list[str] = Field(default_factory=list, max_length=25)`
     - `validation_errors: list[PortalFieldError] = Field(default_factory=list, max_length=15)`
     - `error_banner: str = Field("", max_length=400)`
     - `stalled_duration_ms: int = Field(0, ge=0, le=3_600_000)`
     - `last_user_action: str = Field("", max_length=120)`
   - On `ChatRequest`:
     - Add `portal_context: PortalContextInput | None = Field(None, description="Live DOM telemetry from the inbuilt URA Portal Co-Pilot or Browser Companion")`
     - Add `portal_task_submission: dict[str, Any] | None = Field(None, description="Submitted values from an inline chat elicitation form card")`
   - On `ChatResponse`:
     - Add `portal_copilot: dict[str, Any] | None = Field(None, description="Structured inbuilt portal co-pilot assistance, inline form card, or Claude-style Allow/Disallow action confirmation card")`
   - Add `PortalTaskDecisionRequest(BaseModel)` for `POST /v1/portal/tasks/execute`:
     - `decision: Literal["allow", "disallow"]`
     - `request_state: dict[str, Any]`
     - `idempotency_key: str = Field(..., min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9_:-]+$")`
     - `conversation_id: str | None = Field(None, pattern=r"^[a-zA-Z0-9_-]{1,64}$")`
   - Add `PortalTaskDecisionResponse(BaseModel)`:
     - `ok: bool`
     - `status: Literal["executed", "declined", "requires_auth", "error"]`
     - `task_id: str`
     - `portal_key: str = ""`
     - `live: bool = False`
     - `mode: Literal["simulation", "live"] = "simulation"`
     - `summary: str`
     - `result: dict[str, Any] = Field(default_factory=dict)`
     - `dom_actions: list[dict[str, Any]] = Field(default_factory=list)`
     - `copilot_portal_url: str = ""`

### 4.2 Confirmed Execution Endpoint (`POST /v1/portal/tasks/execute` in `App/backend/app/main.py`)
1. Implement `@app.post("/v1/portal/tasks/execute", response_model=PortalTaskDecisionResponse, tags=["chat"])`:
   - Resolves `session_id = request.headers.get("X-Session-ID") or body.conversation_id or ""` and `ctx: AuthContext = Depends(optional_user)`.
   - Computes the caller's `owner_key = db.conversation_state_key(body.conversation_id, session_id=session_id or None, user_id=ctx.user_id or None)` (and also checks the pre-sign-in anonymous `owner_key` if the user just signed in on the same `session_id`, matching `ADR-0013`!).
   - Unwraps `body.request_state` via `mrtr.unwrap_request_state(body.request_state)`:
     - Rejects tampered or missing signatures (`400 Bad Request`).
     - Verifies that `unwrapped["owner_key"]` matches the caller's `owner_key` (or same-session pre-login `owner_key`), refusing cross-session or cross-user replay (`403 Forbidden`).
     - Verifies that `unwrapped["idempotency_key"] == body.idempotency_key`.
   - If `body.decision == "disallow"`:
     - Records the user's refusal in the audit log (when `audit_ledger` is on) and returns `status="declined"`, `ok=True`, `summary="Action cancelled by taxpayer. No changes were sent to the URA portal."`.
   - If `body.decision == "allow"`:
     - Re-verifies the 4-Tier Policy Gate at execution time (including `ctx.user_id` for Tier 3 and TPCA s. 75 Subject-TIN match).
     - Dispatches to the registered connector tool (`ToolRegistry.get(tool_name).execute(**params)`) or `ura_action_proposal` in a replay-safe idempotency cache (`_EXECUTED_TASK_CACHE` keyed by `(owner_key, idempotency_key)`).
     - Appends an audit record when `audit_ledger` is enabled.
     - Returns `status="executed"`, `live=False` (in simulation mode), the structured `result` (e.g. generated 13-digit PRN `226...`, assigned 10-digit TIN `100...`, or verified stamp status), `dom_actions` to populate the portal screen if open, and `copilot_portal_url`.
2. Register `("POST", "/v1/portal/tasks/execute")` in `tests/test_all_endpoints_e2e.py` (`EXPECTED_ENDPOINTS` + `COVERAGE`).

### 4.3 Wire into `/v1/chat` and `/v1/chat/stream` (`App/backend/app/service.py` & `App/backend/app/main.py`)
1. Thread `portal_context` and `portal_task_submission` into `model.generate(...)`, `_generate_en(...)`, `run_chat_turn(...)`, and `_run_chat_turn_events(...)`.
2. Inside `_generate_en` and `generate_retrieval_only` / `_run_chat_turn_events`:
   - When `flags.is_enabled("portal_copilot")` is True:
     - Call `copilot_outcome = portal_copilot.evaluate_turn(message=english_q, portal_context=portal_context, portal_task_submission=portal_task_submission, user_id=user_id, session_id=session_id, conversation_id=thread_id)`.
     - If `copilot_outcome` produced an active card (`inline_form`, `confirmation_required`, `step_up_login`, `statutory_copilot_handoff`, `stuck_portal_assistance`, or `executed_result`):
       - Attach `result["portal_copilot"] = copilot_outcome.to_dict()`.
       - Inject `copilot_outcome.grounding_block` into the verified facts / prompt context (or return the deterministic verified response when an inline form submission transitions straight to the confirmation card) so the assistant's text reply explicitly instructs the user to verify the details on the card or explains the stuck field clearly.
   - Ensure `localize_reply` localizes the user-facing text labels on `result["portal_copilot"]` while keeping all numbers, TINs, PRNs, URLs, and CSS selectors intact (`figures_survived`).

---

## 5. Phase C — Seamless Chat-Initiated Login, Auto-Return (`returnTo`) & Post-Login Task Continuation

### 5.1 Make `/signin` Honour `?returnTo=` (`App/frontend/src/app/signin/page.tsx` & `oidcFlow.ts`)
1. Add a helper `sanitizeReturnTo(raw: string | null | undefined): string | undefined` in `App/frontend/src/lib/oidcFlow.ts`:
   - Accepts **only** same-origin relative paths starting with a single `/` (`raw.startsWith("/") && !raw.startsWith("//") && !raw.includes("\\")`) to prevent Open Redirect vulnerabilities (`CWE-601`).
2. Update `App/frontend/src/app/signin/page.tsx`:
   - Read `returnTo = sanitizeReturnTo(params.get("returnTo"))` from `window.location.search`.
   - Pass `returnTo` into `beginOidcFlow({ mode: "signin", prompt, returnTo })` both on the automatic `?continue=signin` redirect and when the user clicks **"Continue with URA identity provider"** (or in development/demo mode when a demo taxpayer profile sign-in is used).
   - Display a reassuring banner when `returnTo` is present: *"After signing in, you will be returned automatically to your active URA assistant task."*
3. Update `App/frontend/src/app/signin/callback/page.tsx`:
   - Validate `const safeReturnTo = sanitizeReturnTo(returnTo);`.
   - When a taxpayer completes sign-in (`!staff`), redirect to `safeReturnTo || "/"`. Even for staff testing a portal flow with an explicit `/portal/...` or `/?conversation_id=...` `returnTo`, honour `safeReturnTo` if it starts with `/portal/` or `/?conversation_id=`.

### 5.2 Automatic Post-Login Task Continuation in Chat (`App/frontend/src/store/useChatStore.ts` & `src/app/page.tsx`)
1. Create `App/frontend/src/lib/portalContinuation.ts`:
   - `savePendingPortalContinuation(state: PendingPortalContinuation): void` (writes to `sessionStorage` under `"ura_pending_portal_continuation"` with a 30-minute expiry timestamp).
   - `consumePendingPortalContinuation(): PendingPortalContinuation | null` (reads and clears once).
2. When the user clicks **"Sign in to URA Account & Return Here"** on a `PortalStepUpLoginCard` in chat:
   - Calls `savePendingPortalContinuation({ conversationId, taskId, collectedFields, portalKey, returnMode: "chat" })`.
   - Navigates to `/signin?continue=signin&returnTo=${encodeURIComponent(`/?conversation_id=${conversationId}&auth_resumed=1`)}`.
3. When `src/app/page.tsx` mounts with `auth_resumed=1` and a valid auth token (`getAuthToken()`):
   - Reads `consumePendingPortalContinuation()`.
   - Restores `activeConversationId = pending.conversationId`.
   - Automatically dispatches the follow-up turn with `portal_task_submission: { task_id: pending.taskId, fields: pending.collectedFields, resumed_after_login: true }` so the backend adopts the anonymous session (`db.adopt_workflow_session` per `ADR-0013`) and **immediately presents the logged-in result or Confirmation Card** without the user having to re-type a single word!

---

## 6. Phase D — Screenshot-to-Co-Pilot Portal Handoff, Auth-Gated Deep Links & Incomplete-Screenshot Completeness Guard

### 6.1 Enhance `diagnose_portal_screenshot` (`App/backend/app/documents.py`)
Upgrade `diagnose_portal_screenshot` so every uploaded or camera-captured portal screenshot returns:
1. **`completeness_check` (`dict[str, Any]`):**
   - Inspects OCR text length, presence of a recognizable portal header/breadcrumb, and whether an error message is truncated:
   - `is_complete: bool`
   - `missing_context_hints: list[str]` (e.g., `"The form header or tax type dropdown is cut off in this screenshot"`, `"The validation error banner appears truncated"`).
   - `clarifying_options: list[str]` — 1-tap chips the user can click if the screenshot was cropped (e.g., `"I am on Payment Registration (PRN)"`, `"I am uploading a VAT Return"`, `"I am applying for a TIN"`).
2. **`requires_auth: bool` & `copilot_handoff` (`dict[str, Any]`):**
   - Maps `portal_category` / `detected_state` to the canonical `portal_key` (`prn_payments`, `tin_registration`, `return_filing`, `efris`, `tax_clearance`, `objection_appeals`, `motor_vehicle`, `dts`) and checks `portal_requires_auth(portal_key)`.
   - Extracts any visible entities from the screenshot (`tins`, `prns`, `amounts`, `efris_invoices`, `tax_heads`) to pre-populate the inbuilt portal URL query parameters (`prefill_tin`, `prefill_amount`, `prefill_tax_head`).
   - Builds **two** handoff links in `screenshot_guidance["copilot_handoff"]`:
     - `inbuilt_portal_url`: `f"/portal/{portal_key}?step={step_slug}&copilot=1"` (with non-sensitive prefill state passed via `sessionStorage` rather than sensitive query params!).
     - `requires_login_first`: `bool` (`portal_requires_auth(portal_key)`).
     - `signin_then_portal_url`: `f"/signin?continue=signin&returnTo={urllib.parse.quote(inbuilt_portal_url, safe='')}"`.
     - `user_message`:
       - If `requires_login_first` is **False**: *"You can continue directly on this screen with the built-in URA AI Co-Pilot enabled — it can see the full form, highlight the exact field, or complete the step for you."*
       - If `requires_login_first` is **True**: *"Accessing this URA portal screen requires signing in first. Click the sign-in link below — once your login is accepted, you will be taken straight to this screen with the built-in AI Co-Pilot active to guide you."*

---

## 7. Phase E — Inbuilt Portal Co-Pilot Workspace (`/portal/[portalKey]`), Live DOM Telemetry Bridge & Interactive Chat Cards (`App/frontend`)

### 7.1 Interactive Chat Cards (`App/frontend/src/components/PortalCopilotCard.tsx` & `ChatMessage.tsx`)
Create `App/frontend/src/components/PortalCopilotCard.tsx` and wire it into `ChatMessage.tsx` (and `applyTurnMeta` in `src/lib/turnMeta.ts` + `ChatTurn` in `src/store/useChatStore.ts`):

1. **Sub-Component 1: `PortalInlineFormCard` (`card_type === "inline_form"`):**
   - Renders clean, accessible (`WCAG 2.2 AA`) form controls directly inside the assistant message bubble for every field in `portalCopilot.fields`:
     - Text/number/date/tel/email inputs and `<select>` dropdowns
     - Pre-filled with any values already extracted from the conversation
     - Inline field validation messages and format examples
   - Submitting the form calls `onSubmitInlineForm(taskId, fieldValues)` which sends a chat turn carrying `portal_task_submission: { task_id: taskId, fields: fieldValues }` to transition straight to the Confirmation Card (or execute if Tier 1).
2. **Sub-Component 2: `PortalConfirmationCard` (`card_type === "confirmation_required"` — Claude-Style Allow / Disallow):**
   - Displays a prominent header badge (`Action Permission Required`) and the explicit instruction:
     **"Please verify the information below carefully before allowing the URA AI to execute this action."**
   - Renders a structured key-value verification table of all parameters (with sensitive fields like `NIN` masked by default and a `"Show / Hide"` eye button so the user can verify every character).
   - Displays a `Simulation / Demo Mode` badge whenever `live === false`.
   - Renders three buttons:
     - **Allow & Execute** (`data-testid="portal-task-allow-btn"`): calls `POST /api/v1/portal/tasks/execute` with `{ decision: "allow", request_state, idempotency_key, conversation_id }` and transitions the card in-place to the verified receipt/result state!
     - **Edit Details** (`data-testid="portal-task-edit-btn"`): switches the card back to `PortalInlineFormCard` with all current values editable.
     - **Disallow** (`data-testid="portal-task-disallow-btn"`): calls `POST /api/v1/portal/tasks/execute` with `{ decision: "disallow", ... }` and marks the proposal as cancelled.
3. **Sub-Component 3: `PortalStepUpLoginCard` (`card_type === "step_up_login"`):**
   - Explains clearly why login is needed and shows how many entered fields are preserved (`preserved_fields`).
   - Provides two 1-click buttons:
     - **"Sign In & Return to Chat"** (saves continuation state and navigates to `signin_chat_href`)
     - **"Sign In & Open Portal with Built-In AI Co-Pilot"** (saves continuation state and navigates to `signin_portal_href`).
4. **Sub-Component 4: `PortalCoPilotLaunchBanner` (Always available on portal-related answers & screenshot diagnoses):**
   - Whenever an answer or uploaded screenshot involves a URA portal process, renders a clean action bar:
     - If public OR user is already signed in: **"Open `<Portal Name>` with Built-In AI Co-Pilot ↗"** (`/portal/<portal_key>?copilot=1&conversation_id=<id>`)
     - If `requires_auth` and user is NOT signed in: **"Sign In First, Then Open `<Portal Name>` with Built-In AI Co-Pilot →"** (`/signin?continue=signin&returnTo=...`).

### 7.2 Inbuilt URA Portal Co-Pilot Page (`App/frontend/src/app/portal/[portalKey]/page.tsx` — Approach 3 + Approach 2 Contract)
Create `App/frontend/src/app/portal/[portalKey]/page.tsx` (and `portal.css`) representing the URA Portal with the **Inbuilt AI Co-Pilot Application** natively embedded:
1. **Supported Interactive Portal Screens (`portalKey`):**
   - `prn_payments`: URA e-Services Payment Registration (`URA-PAY-PRN-201` — TIN, Tax Head, Amount, Payment Mode, Generate PRN)
   - `tin_registration`: Instant TIN Application (`URA-REG-TIN-001` — Applicant Type, NIN / URSB BRN, Surname, Given Name, Phone, Email)
   - `return_filing` / `e_services`: Monthly & Annual Return Filing (`URA-DT-RET-101` — Return Type, Period, Macro-Validated `.xlsm` Upload, Net Tax Payable, Statutory Declaration)
   - `tax_clearance`: Tax Clearance Certificate Application (`URA-TCC-301` — TIN, Purpose, Procuring Entity, Compliance Check)
   - `efris`: EFRIS Fiscal Invoice & Receipt Issuance (`URA-EFRIS-401` — Buyer TIN, Commodity UNSPSC Code, Quantity, Unit Price,Offline Sync Status)
   - `dts`: Digital Tax Stamps (`URA-DTS-501` — Excise Category, Stamp Reel Quantity, Batch Activation)
   - `objection_appeals`: Section 24 Objection Lodging (`URA-OBJ-601` — Assessment Reference, Assessment Date, Grounds of Objection)
   - `motor_vehicle`: Customs SAD & Motor Vehicle Registration (`URA-MV-701` — SAD `C-Number`, 17-char Chassis/VIN, Registration Category)
2. **Auth Guard on Authenticated Portal Screens:**
   - If `portal_requires_auth(portalKey)` is True and the user is not signed in (`!getAuthToken()`), the page displays a clear URA Portal Sign-In Gate with a 1-click button **"Sign In to URA Portal & Continue Here"** (`/signin?continue=signin&returnTo=${encodeURIComponent(currentUrl)}`) plus a **"Demo Quick Sign-In (Sandbox Taxpayer)"** button when in non-production development mode so evaluators can test both OIDC redirect and instant sandbox sign-in effortlessly.
3. **Live DOM Telemetry & Inbuilt AI Co-Pilot Rail:**
   - The embedded Co-Pilot monitors the active form in real time:
     - Tracks `current_route`, `active_step`, `form_id`, `visible_fields` (never reading `type="password"` / `otp` / `pin`), `empty_required_fields`, `validation_errors`, and field stall timer (`>= 15s` without progress).
   - When the user clicks **"I'm Stuck — Guide Me"**, **"Complete This Task For Me"**, or triggers a form validation error:
     - Sends `portal_context` to `/api/v1/chat` (or `/api/v1/portal/tasks/execute`).
     - Receives `dom_actions` (`get_portal_dom_recipe`) and executes them visually on the page:
       - **`spotlight`:** Smooth-scrolls (`element.scrollIntoView({ behavior: "smooth", block: "center" })`) even if the error field was scrolled off-screen, adds a pulsing gold/emerald spotlight ring (`.copilot-dom-spotlight`), and shows an inline floating coachmark tooltip!
       - **`fill` / `select`:** Populates safe form fields automatically when the user clicks **"Auto-Fill Safe Fields"** in the Co-Pilot rail.
       - **`requires_confirmation`:** Stops before the final submit button and either highlights it for the human taxpayer to click OR shows the **Claude-style Allow / Disallow Confirmation Card** inside the Co-Pilot rail.
   - Includes a **"Return to Full Chat Conversation"** link that brings the user back to `/` with the updated task state preserved.

### 7.3 5-Locale Dictionary Parity (`App/frontend/src/lib/i18n/{en,lg,sw,nyn,ach}.ts`)
Add all `portalCopilot.*` keys (card titles, verification warning, Allow/Disallow/Edit buttons, inline form labels, step-up login notices, screenshot handoff prompts, and inbuilt portal coachmarks) across all 5 locales (`en`, `lg`, `sw`, `nyn`, `ach`) and verify with `cd App/frontend && bun run test src/__tests__/lib/i18n.test.ts`.

---

## 8. Phase F — Evaluation Suite, Security & Privacy Regression Gate, `ADR-0014`, Operational Runbook & Living Documentation

### 8.1 Automated Backend & Frontend Test Suites
1. **`tests/agents/test_portal_copilot.py` (Backend Unit + Integration + Security Gate):**
   - **Test 1 (Tier 1 Autonomous Read & Inline Form Elicitation):** Asking *"Verify TIN"* without a TIN returns `card_type == "inline_form"`; submitting a 10-digit sandbox TIN executes `tin_search_verify` and returns `card_type == "executed_result"`.
   - **Test 2 (Tier 2 Claude-Style Allow / Disallow Confirmation):** Asking *"Generate a VAT PRN for UGX 2,500,000 for TIN <sandbox-tin> via Stanbic Bank"* returns `card_type == "confirmation_required"` with the explicit verification notice and HMAC-signed `request_state` (never executing `payment_generate_prn` before approval).
   - **Test 3 (`POST /v1/portal/tasks/execute` Allow vs. Disallow & Idempotency):**
     - `decision="disallow"` cancels without side effects.
     - `decision="allow"` executes `payment_generate_prn` once and returns the 13-digit PRN (`226...`) with `live=False`, `mode="simulation"`.
     - Repeating the same `idempotency_key` returns the cached receipt without creating a duplicate PRN.
   - **Test 4 (Security: Tampered `request_state` & Cross-Session Replay Refusal per `ADR-0013`):**
     - Modifying `amount_ugx` or `tin` inside `request_state` fails HMAC verification (`400`).
     - Presenting a valid `request_state` from a different `X-Session-ID` or different `user_id` is refused (`403`).
   - **Test 5 (Tier 3 Official Secrecy — TPCA s. 75 & Step-Up Login):**
     - Unauthenticated request for `tin_tax_obligations` returns `card_type == "step_up_login"` with `signin_chat_href` and `returnTo`.
     - Authenticated user with TIN `<tin-A>` attempting to query obligations for a different TIN `<tin-B>` is refused by the TPCA s. 75 Subject-TIN Ownership check.
   - **Test 6 (Tier 4 Statutory Boundary — TPCA s. 16):**
     - Asking the AI to submit a final VAT return or formal Section 24 objection returns `card_type == "statutory_copilot_handoff"` with pre-filled `dom_actions` up to `security_gate="requires_confirmation"`, never autonomously signing the declaration.
   - **Test 7 (Screenshot-to-Co-Pilot Handoff & Incomplete Screenshot Guard):**
     - Uploading a screenshot of an authenticated portal screen (`return_filing` / `tax_clearance`) returns `copilot_handoff.requires_login_first is True` and `signin_then_portal_url`.
     - Uploading a cropped screenshot missing header signals sets `completeness_check.is_complete is False` and returns `clarifying_options` + `inbuilt_portal_url`.
   - **Test 8 (Live DOM Telemetry `portal_context` Diagnosis & Secret Scrubbing):**
     - Passing `portal_context` with `visible_fields` containing `tin`, `password`, and `otp` and a validation error on `tax_head` scrubs `password` and `otp`, diagnoses the missing `tax_head`, and emits `dom_actions` with `scroll_to=True` and `action_type="spotlight"`.
2. **Update `tests/test_all_endpoints_e2e.py`:**
   - Add `("POST", "/v1/portal/tasks/execute")` to `EXPECTED_ENDPOINTS` and `COVERAGE`.
3. **Frontend Vitest Suite (`App/frontend/src/__tests__/components/PortalCopilotCard.test.tsx`):**
   - Tests rendering and interaction for `PortalInlineFormCard`, `PortalConfirmationCard` (Allow / Disallow / Edit / Reveal Masked NIN), `PortalStepUpLoginCard` (`returnTo` continuation storage), and `sanitizeReturnTo` open-redirect protection.

### 8.2 Architecture Decision Record, Runbook & Living Documentation
1. **Author `docs/adr/0014-agentic-portal-tasks-and-stuck-user-dom-copilot.md` (MADR 4.0):**
   - Document the 4-Tier Privacy/Statutory Task Policy, MCP Elicitation + Claude-style Allow/Disallow confirmation protocol, `returnTo` OIDC step-up continuation, and Approach 3 (Inbuilt DOM Co-Pilot) + Approach 2 (Browser Companion) unified telemetry contract.
   - Register `ADR-0014` in `docs/adr/README.md`.
2. **Author `docs/runbooks/portal-copilot-and-tasks.md`:**
   - Cover `FLAG_PORTAL_COPILOT`, inspecting task execution audit logs, testing the inbuilt `/portal/[portalKey]` workspace, and emergency kill-switch procedures.
3. **Update Living Docs:**
   - Update `docs/RAG_ARCHITECTURE.md`, `docs/MODEL_CARD.md`, and `docs/GAPS_AND_AGENTIC_ROADMAP.md`.
   - Run all governance and CI verification gates before opening and merging the PR:
     ```bash
     PYTHONPATH=App/backend python3 -m pytest tests/test_monorepo_architecture_governance.py tests/agents/test_portal_copilot.py -q
     cd App/frontend && bun run test && bun run lint
     ```

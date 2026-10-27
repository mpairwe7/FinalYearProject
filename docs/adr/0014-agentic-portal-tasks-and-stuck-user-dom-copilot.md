# ADR-0014: Agentic Portal Task Execution, Claude-Style Action Confirmation, Inline Chat Elicitation & Stuck-User DOM Co-Pilot

- **Date:** 2026-10-23
- **Owner:** Cloake Tech Limited — Core Agentic CX & Portal Co-Pilot Architecture Team, Platform Assurance
- **Builds upon:** [ADR-0005](0005-mcp-mrtr-transport-hardening-and-csa-aicm-maestro.md), [ADR-0010](0010-agentic-journey-copilot-prerequisites-a2ui-and-step-up-auth.md), and [ADR-0013](0013-session-bound-workflow-adoption-and-verified-resume-codes.md)

## Status

Accepted

## Context and Problem Statement

Taxpayers using URA portals (`portal.ura.go.ug`, `efris.ura.go.ug`, `ura.go.ug`) frequently get stuck mid-task or want the AI assistant to perform a portal task for them across seven distinct real-world situations:

1. **Missing Inputs for a Portal Task:** A taxpayer asks *"Generate a VAT PRN for me"* or *"Apply for an individual TIN"* without supplying all required fields (TIN, NIN, Tax Head, Amount, Payment Mode). Multi-turn text interrogation causes high drop-off.
2. **High-Impact / Delegated Write Actions:** A taxpayer supplies parameters for PRN generation, TIN registration, or stamp ordering. Under **OWASP Top 10 for Agentic Applications 2026 (`Least-Agency`, `ASI02`, `ASI09`)** and **MCP 2026-07-28 Elicitation (`SEP-2322`)**, the agent must never execute writes autonomously without presenting a structured verification table and obtaining explicit human **Allow / Disallow** (`accept` / `decline`) confirmation.
3. **Tasks Requiring Authentication (`Step-Up Auth`):** An unauthenticated taxpayer asks to check their private tax obligations (`tin_tax_obligations`) or account ledger (`ura_account_profile`). Under **TPCA Cap. 343 s. 75 (Official Secrecy)**, **IETF RFC 9470 (OAuth 2.0 Step-Up Authentication)**, and **W3C WCAG 2.2 AA SC 3.3.7 (Redundant Entry)**, the assistant must require sign-in while preserving 100% of entered fields across the OIDC redirect (`returnTo`) and adopting the pre-sign-in anonymous session (`ADR-0013`).
4. **Statutory Declaration Boundary:** A taxpayer asks the AI to submit a final VAT/PAYE/Income Tax return (`return_filing_submit`) or lodge a formal Section 24 objection (`objection_appeals_lodge`). Under **TPCA Cap. 343 s. 16 & s. 58–65**, final statutory declarations carry personal taxpayer liability and must be staged up to the final step for the taxpayer to review and click personally.
5. **Public vs. Authenticated Screenshot Handoff:** A taxpayer uploads a screenshot of a URA portal page where they are stuck. If the screenshot is cropped or missing form headers, the AI cannot see off-screen validation errors from pixels alone; if the target portal screen requires login, sending an anonymous user directly to an authenticated route fails.
6. **Live Stuck-User Recovery Inside the Portal:** When a taxpayer is on the portal screen (`/portal/[portalKey]` or via Browser Companion), they need live DOM assistance that scrolls to off-screen validation errors, spotlights the exact input, or auto-fills safe fields without ever reading `password`, `otp`, `pin`, or `cvv` fields.

## Decision Outcome

1. **4-Tier Privacy, Statutory & Security Policy Engine (`App/backend/app/portal_copilot.py`):**
   Every URA portal task is deterministically classified in `PORTAL_TASK_CATALOG` under `FLAG_PORTAL_COPILOT` (`portal_copilot`, default `True`, `category="kill_switch"`):
   - **Tier 1 (`AUTONOMOUS_VERIFY`):** Public read-only verifications (`tin_search_verify`, `payment_view_status`, `dts_verify_stamp`, `ursb_verify_business`, `payment_verify_advance_tax`). Elicits missing reference numbers via inline form; executes read-only verification once present.
   - **Tier 2 (`DELEGATED_CONFIRM`):** Delegated writes/drafts (`payment_generate_prn`, `payment_reactivate_prn`, `tin_apply_individual`, `tin_apply_non_individual`, `dts_order_stamps`, `dts_activate_stamps`, `efris_stock_management`, `ursb_register_business`). Collects missing inputs via inline form, then renders a **Claude-Style Action Confirmation Card (`card_type="confirmation_required"`)** instructing the taxpayer to verify every field (with sensitive fields like `NIN` masked with a reveal toggle) and click **Allow & Execute**, **Edit Details**, or **Disallow**.
   - **Tier 3 (`AUTHENTICATED_ACCOUNT_READ`):** Private taxpayer account lookups (`ura_account_profile`, `tin_tax_obligations`, `efris_taxpayer_status`, `dts_taxpayer_status`, `ursb_compliance_status`, `bwims_consignment_status`, `bwims_warehouse_inventory`). Requires `bool(ctx.user_id)` (emitting a `step_up_login` card with `returnTo` when anonymous) and enforces **TPCA Cap. 343 s. 75 Subject-TIN Ownership** so authenticated User A can never query User B's TIN.
   - **Tier 4 (`STATUTORY_COPILOT_ONLY`):** Statutory declarations (`return_filing_submit`, `objection_appeals_lodge`, `tax_clearance_apply`, `get_refund_claim`, `efris_fiscal_invoice`, `efris_credit_note`, `payment_checkout_settle`). Stages the A2UI draft and `dom_actions` recipe (`card_type="statutory_copilot_handoff"`) and opens `/portal/<portalKey>?autofill=1` up to `security_gate="requires_confirmation"` so the human taxpayer personally signs the declaration.

2. **HMAC-Signed Confirmation & Replay-Safe Execution Endpoint (`POST /v1/portal/tasks/execute`):**
   - Confirmation proposals are signed with `mrtr.wrap_request_state` (`HMAC-SHA256`) and bound to the caller's `owner_key = db.conversation_state_key(...)` (`ADR-0013`) and deterministic `idempotency_key`.
   - `POST /v1/portal/tasks/execute` verifies the HMAC signature (`400` on tamper), enforces `owner_key` / same-session post-login continuity (`403` on cross-session or cross-user replay), honours both `"allow"`/`"disallow"` and MCP 2026-07-28 `"accept"`/`"decline"`/`"cancel"`, caches executed receipts by `(owner_key, idempotency_key)`, and labels simulator responses honestly (`live=False`, `mode="simulation"`).

3. **Step-Up OIDC Login with Safe `returnTo` & Zero-Data-Loss Continuation (`oidcFlow.ts`, `portalContinuation.ts`):**
   - `sanitizeReturnTo` in `App/frontend/src/lib/oidcFlow.ts` enforces same-origin relative paths (`CWE-601` protection).
   - `/signin` and `/signin/callback` preserve `returnTo`, returning the taxpayer automatically to `/?conversation_id=...&auth_resumed=1` or `/portal/<portalKey>`.
   - `portalContinuation.ts` stores pending task fields in `sessionStorage` (30-minute TTL), and `page.tsx` automatically resumes the task after sign-in while `service.py` adopts the anonymous workflow session via `db.adopt_workflow_session` (`ADR-0013`).

4. **Screenshot Completeness Guard, Auth-Gated Deep Links & Inbuilt DOM Co-Pilot Workspace (`/portal/[portalKey]`):**
   - `diagnose_portal_screenshot` (`App/backend/app/documents.py`) emits `completeness_check` (`is_complete`, `missing_context_hints`, `clarifying_options`) and `copilot_handoff` (`inbuilt_portal_url`, `requires_login_first`, `signin_then_portal_url`, `prefill_state`, `user_message`).
   - `App/frontend/src/app/portal/[portalKey]/page.tsx` implements the interactive URA Portal Workspace with an embedded AI Co-Pilot Side Rail (Approach 3) sharing the exact `PortalContextInput` & `get_portal_dom_recipe` contract used by Browser Companions (Approach 2). `sanitize_portal_context` strips all `password`, `otp`, `pin`, `cvv`, and `secret` fields before analysis.

## Consequences

- **Positive:**
  - Taxpayers can complete missing task inputs in a single inline chat form card (`PortalInlineFormCard`) without multi-turn interrogation.
  - Every Tier 2/3 action requires explicit human verification and **Allow / Disallow** consent backed by cryptographic `request_state` integrity and `ADR-0013` session ownership.
  - Step-up login from chat or screenshot handoff returns the user automatically to their exact task or portal screen with zero re-entry.
  - Stuck users on `/portal/[portalKey]` receive live DOM spotlighting, smooth scrolling to off-screen errors, and 1-click safe field auto-fill.
- **Negative / Trade-offs:**
  - In development/prototype mode, connector executions run against the local SQLite simulators (`live=False`, `mode="simulation"`) and are clearly badged as simulation mode; in `APP_ENV=production`, simulators remain disabled until live URA APIs are configured.

## Confirmation

Validated by:
- `tests/agents/test_portal_copilot.py` (8 end-to-end backend + security + privacy regression tests)
- `tests/agents/test_portal_copilot_phase_a.py` (17 unit tests covering flag registration, 9 portal DOM recipes, 4-tier catalog, and secret scrubbing)
- `tests/test_all_endpoints_e2e.py` (`POST /v1/portal/tasks/execute` registered and verified)
- `App/frontend/src/__tests__/components/PortalCopilotCard.test.tsx` & `App/frontend/src/__tests__/lib/i18n.test.ts` (5-locale dictionary parity + component interaction tests)
- `tests/test_monorepo_architecture_governance.py`

## Amendment 1 (2026-10-25 — PR #659, PR #660 & PR #661, Gaps G254 & G255)

1. **Statutory Portal Error-Code Resolver & Cross-Field Identifier Diagnostics (`portal_copilot.py`, `G254`)**:
   - Added `diagnose_ura_error(error_text)` mapping 8 statutory URA/EFRIS/e-Tax/PRN/NIRA/BWIMS error families (`EFRIS_INSUFFICIENT_STOCK`, `EFRIS_NOT_ENROLLED`, `EFRIS_INVALID_FDN`, `PRN_EXPIRED_OR_INVALID`, `DUPLICATE_RETURN_PERIOD`, `TCC_COMPLIANCE_BLOCK`, `NIRA_NIN_MISMATCH`, `CUSTOMS_BOND_OR_IM7_EXPIRED`) to root causes, statutory citations, step-by-step resolution plans, and `remediation_task_id`s across `analyze_portal_context`, `evaluate_turn`, `build_portal_task_card`, `handle_portal_task_decision`, and `PortalCopilotCard.tsx`.
   - Added separator-tolerant TIN/PRN/`CM`/`CF`/`CR`-NIN normalization, linear-time `_extract_commodity_code` (8-digit UNSPSC), `order_id` (`DTS-ORD-...`), slash-prefixed URSB BRN (`/80048538707631`) normalization, 10/12-digit Ugandan mobile number (`07...`, `2567...`, `+2567...`) vs 12-digit PRN disambiguation (`_PRN_RE = re.compile(r"\b(?!2567\d{8}\b)(2\d{9,12})\b")`), and cross-field identifier mix-up diagnostics in `validate_task_fields` and `extract_task_entities`.
2. **Screenshot Error Diagnosis, Real URA Wizard Guidance, Screen Completion Checklist & Remediation Handoff (`Dockerfile.gpu`, `vision/document_classifier.py`, `documents.py`, `service.py`, `ChatMessage.tsx`, `PortalCopilotCard.tsx`, `DocumentInspectionModal.tsx`, `portal/[portalKey]/page.tsx`, `G255`)**:
   - Installed `tesseract-ocr` in `Dockerfile.gpu` and added a `shutil.which("tesseract")` CLI fallback (`tesseract_cli`) in `_extract_image` (`documents.py`) for local GPU deployments when `paddleocr`/`easyocr`/`pytesseract` are not installed.
   - Expanded `DocumentType.PORTAL_SCREENSHOT` in `vision/document_classifier.py` and `diagnose_portal_screenshot` in `documents.py` with dedicated recognition, checklists, and step-by-step completion instructions for URA's **Apply To Tax Payment In Installment** wizard (Sections B–F: `Basis Of Installment`, `Liability Breakdown`, `Outstanding Liability Wise Breakdown`, `Installment Details`, and `Upload Documents and Submit application` with numeric-boundary `has_zero_downpayment` check) and **EFRIS Web Portal** (`First-time Registration` Sections A–C, `Fiscal Document Validation`, `Validation History`, and `Detailed VAT Sales/Purchase Report`).
   - Integrated `diagnose_ura_error` and `extract_task_entities` into `diagnose_portal_screenshot` (`documents.py`), routing `inbuilt_portal_url` directly to `&intent={remediation_task_id}`, computing `what_needs_to_be_done`, `checklist` (`detected` vs `action_needed` items), and `recommended_prompts`, carrying `prefill_state` into `evaluate_turn` (`service.py`), and validating `<select>` option membership on continuation restore in `portal/[portalKey]/page.tsx`.

## Amendment 2 (2026-10-27 — Gap G266): the workspace wears ura.go.ug's frame

The owner asked that the inbuilt portal look like the official one, so that a
taxpayer moving between this workspace and ura.go.ug is never unsure which part
of URA they are in. The workspace (`portal/[portalKey]/page.tsx`, `portal.css`)
had its own navy-and-amber look.

1. **The official frame, from the owner's screenshots of ura.go.ug (2026-10-27).**
   The cloud workspace could not load the site: the egress answered "upstream request failed" even with
   the domain allowed.
   - **Header:** white, with the official logo (`public/ura-logo-wordmark.png`, cropped from the repository's `ura-logo.png`). Utility links (Contact Us, About Us, Media Center, Call URA, Login), a blue-bordered search, FAQs and Select Language.
   - **Section bar:** blue, led by the accessibility control. The current section ("Domestic Taxes") sits in a white tab. Two mega menus copy the site's columns (Domestic Taxes, Legal & Policy).
   - **Banner:** an inner page's banner with breadcrumb, Back, the yellow title and the quick-links band. The services are relabelled in the site's own words: Make a Payment, Get a TIN, File a Return, EFRIS, DTS, and the rest.
   - **Also:** a blue footer with the official contact numbers (`URA_CONTACTS`), and the site's yellow "Chat with us." launcher.
2. **Colours and type.**
   - Colours come from the official logo (`#FFF200`, `#233F8E`) and the screenshots: section bar `#1D5BA8`, sky-blue secondary `#4A90D9`, near-white mega menus.
   - Type is Montserrat (SIL OFL 1.1), the open match for the site's geometric sans. It is vendored as one variable latin woff2 and loaded by `app/portal/layout.tsx`, so only `/portal/*` downloads it.
   - The frame is light only, like the site. The shared `PortalCopilotCard` tokens are pinned light inside it.
3. **Links are verified, not guessed.** Entries this workspace serves open here. The rest open the official page in a new tab, and only pages the project's own crawl of ura.go.ug (`Data/crawl`) fetched are linked. Whistle Blow, Voluntary Disclosure, Stamp Duty and Sitemap are left out.
4. **What the frame must not imply.**
   - Sign-in stays the app's own `/signin` (OIDC) flow; nothing imitates the official credential form.
   - The banner names the screen an "Inbuilt AI Co-Pilot Workspace". The footer says the Co-Pilot asks before it submits anything.
   - The header search asks the Co-Pilot about the screen rather than searching ura.go.ug.
5. **Unchanged contracts:** every field `id`/`selectorId` the DOM telemetry and spotlight address, every `data-testid`, the auth gate, continuation and autofill.

Confirmed by `bunx vitest run` (765 tests), `bun run lint`, `tsc --noEmit`, a production `next build`, a server render of the portal routes, and Playwright captures at 1440 px and 390 px, with no horizontal overflow at 390 px.

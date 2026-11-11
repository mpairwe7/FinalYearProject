# Browser agent assurance review — 10 November 2026

Status: phased fixes implemented with regressions on branch
`fix/browser-agent-assurance-2026-11`; eight items remain open (see
[Residual risk](#residual-risk)). Decision record:
[ADR-0038](../adr/0038-browser-agent-assurance.md).

Baseline: `222ce12a` (`dev`). Scope: the assistant's own browser agent, the portal
co-pilot ([ADR-0014](../adr/0014-agentic-portal-tasks-and-stuck-user-dom-copilot.md)),
from the DOM telemetry a page sends to the audit row of a task it runs; and this
application as the target of other agents: an agentic browser acting for a taxpayer,
a script or extension in the page, a site that frames it. This is a targeted review,
not a penetration test or a legal compliance certification.

**What was at stake today.** Every portal task the co-pilot can run goes to a local
simulator (`_SIMULATED_CONNECTOR_NAMESPACES`), and `APP_ENV=production` refuses those
connectors. No statutory record was created through this path. The findings are
defects in the gate a live URA connector will sit behind, and they are graded by
what they would do there.

Method: every finding below was reproduced against the running code before it was
fixed, and each has a regression test written first and seen to fail. Findings that
came from reading alone and did not reproduce were dropped and are listed under
[Examined and found sound](#examined-and-found-sound). After the fixes, each control
was disabled in turn in a throwaway worktree to confirm a test fails. The audit prompt
was then run a second time, blind, and what that found is Phase 4.

## The audit prompt

The brief below is the prompt this review was run from, kept so the next review of
this surface starts from the same questions. It was also run blind, by a fresh agent
with no knowledge of this review, against the unfixed baseline; see
[Blind re-run](#blind-re-run-of-the-prompt). The paragraph beginning "Go through the task
catalogue" was added after that run, for the reason it gives.

```text
You are auditing the browser-agent surface of the URA taxpayer assistant and fixing what you
find. This is a government tax service. The actions at stake are payment registrations, TIN
applications and stamp orders made in a real person's name, so a mistake here is not a wrong
sentence in a chat: it is a record in a statutory system that someone then has to live with.
Work to that standard, and say plainly when you cannot reach it.

"Browser agent" means two things in this repository, and both are in scope.

The first is the assistant's own agent, the portal co-pilot. It receives DOM telemetry from the
taxpayer's browser (field names, values, error text), returns `dom_actions` (spotlight, fill,
select, click) that code we do not control may execute, and runs a portal task once a person
allows it on a confirmation card. Start from `App/backend/app/portal_copilot.py`,
`App/backend/app/tools/portal_navigator_data.py`, the `/v1/portal/tasks/execute` route in
`App/backend/app/main.py`, `App/frontend/src/components/PortalCopilotCard.tsx` and
`App/frontend/src/app/portal/[portalKey]/page.tsx`. ADR-0014 describes the design and ADR-0038
the last audit; `AGENTS.md` lists invariants that every change must keep.

The second is other people's agents driving this application: an agentic browser acting for a
taxpayer, a script or extension in the page, or a hostile site that frames it.

Three boundaries organise the work. What a page says is evidence of what the page shows and
nothing more, so it must never choose an element, an action, a link or the shape of a reply.
What leaves the server as an action is run elsewhere, so the rules belong on the way out, in one
place, not in each recipe. And an approval is the only thing between a proposal and an effect,
so ask who can send one, whether they saw what they approved, whether it can run twice, and what
happens when the store or the ledger that records it is down.

Go through the task catalogue task by task as well as mechanism by mechanism. For each task ask
what it actually does and to whose record (one named as a check may write), what it returns and
to whom, and which identity it is bound to. A review that follows only the mechanisms will find
the lease and the digest sound and miss a check that registers a tax head.

How to work, and why:

- Reproduce a finding against the running code before you fix it. A finding from reading alone
  that does not reproduce is dropped, or recorded as a hardening and labelled as one. Reading
  suggested several defects in the last audit that the running system did not have.
- Write the failing test first and watch it fail for the reason you expect. Then make the
  smallest change that fixes the cause, at the point every path goes through.
- Afterwards, disable each control you added in a throwaway worktree and confirm a test fails.
  A control no test notices will be removed by the next refactor.
- When a dependency fails, decide deliberately whether the path fails closed. A consequential
  action that cannot be recorded should not run; a refusal should never wait on its record.
- Check references online before citing them and separate what you verified from what you could
  not. Standards in this area are mostly drafts: use them for direction and do not claim
  conformance. No page-level signal distinguishes a person from an agent that drives real
  input, so do not present a client-side check as proof of a human.
- Read production (response headers, flags, logs) without changing it, and report what you saw.
- Keep the co-pilot useful. For every rule you add, include a benign case that must still work.

Order the fixes by consequence to a taxpayer, not by difficulty: first the integrity of actions
that take effect, then what untrusted input can steer, then the confirmation surface in the
browser, then evaluation data and documentation. Commit each phase separately with its tests.

Follow the repository's rules: branch from `dev` and open a pull request to `dev`; both storage
backends keep the same functions; a new string exists in every locale or falls back to English
by the existing convention; living documentation changes in the same pull request (an ADR in
MADR 4.0 form, the gap register, the runbook, the risk manifest and the threat model).

Finish with a short report a reviewer can check: each finding with the evidence that reproduced
it, the fix and the test that guards it; what you examined and found sound; what remains open
and who can close it; and which claims rest on verification you ran versus sources you read.
```

## Verified reference basis

Checked online on 2026-11-10. These are engineering references, not a claim that any
of them binds URA or that this system conforms to a draft. "Two passes" means two
independent fetches of the page agreed; the second pass corrected the first in three
places, noted below.

| Reference | Checked | What it changed here |
| --- | --- | --- |
| [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/) (9 Dec 2025) | Entry names read from the PDF; the landing page does not list them | ASI01 Agent Goal Hijack, ASI02 Tool Misuse and Exploitation and ASI09 Human-Agent Trust Exploitation frame the findings. ASI09 names "missing confirmation for sensitive actions" and asks that preview be separated from effect. |
| [Chrome, "Build tools" for WebMCP](https://developer.chrome.com/docs/ai/webmcp/build-tools) (26 Aug 2026, updated 30 Oct 2026) | Two passes | "Enforcement belongs in your application logic", and for a consequential action "bind approval to a short-lived private grant that includes the exact draft, hash, version, scope, and expiry. Any edit must revoke the grant." The signed `request_state` already was that grant; the digest was the part left optional (G543). |
| [Loopjacking: Hijacking Human-in-the-Loop Approval](https://arxiv.org/abs/2609.21081) (17 Sep 2026) | Two passes | A person approves A and the implementation uses the approval for B. One of its two defences is "complete canonical approval rendering and exact use-time comparison": the reason the digest is required and an assumed value is marked (G543, G544). |
| [LoginTrap](https://arxiv.org/abs/2608.04741) (5 Aug 2026) | Two passes | Injections that make login "appear as a plausible prerequisite" and guide the agent "to a controlled login page", 86% average end-to-end success. G546 was this shape: page text selecting a click on a login link. |
| [EIA: Environmental Injection Attack](https://arxiv.org/abs/2409.11295) (ICLR 2025) | Two passes | Content injected into a page leads an agent to put personal data where the attacker reads it, up to 70% for specific PII. Basis for server-owned selectors and the wider credential names (G545, G547). |
| [Manifold, Claude for Chrome extension](https://www.manifold.security/blog/claude-for-chrome-extension-bypass) (14 Jul 2026) | Two passes; first-pass wording corrected | "The handler does not check `event.isTrusted`", so a page script's click reached a prompt trigger. It did not forge an approval. Used here only as evidence that an unguarded handler takes script-made clicks (G549). |
| [Akamai, agentic automation and behavioural telemetry](https://www.akamai.com/blog/security-research/2026/aug/identifying-agentic-automation-behavioral-telemetry) (19 Aug 2026) | Two passes; first-pass wording corrected | A proof-of-concept classifier separates agents from people statistically (ROC-AUC 0.981, PR-AUC 0.661) and agent behaviour "partially overlaps with sparse human sessions". A score, not proof of a person: the reason G551 stays open and the evidence is recorded rather than used as a gate. |
| [IETF draft-ietf-webbotauth-httpsig-protocol-01](https://datatracker.ietf.org/doc/draft-ietf-webbotauth-httpsig-protocol/) (29 Oct 2026) | Two passes (first 40% of the page verbatim) | An Internet-Draft. "It says nothing about who operates the Agent, whether the Agent is benign, or whether the request is authorized." Not adopted as a basis for approvals (G552). |
| [UK NCSC, "Prompt injection is not SQL injection"](https://www.ncsc.gov.uk/blog-post/prompt-injection-is-not-sql-injection) (8 Dec 2025) | Two passes | "Prompt injection attacks may never be totally mitigated"; protections should "focus more on deterministic (non-LLM) safeguards that constrain the actions of the system". Every control added here is code, none is a prompt. |
| [W3C Fetch Metadata Request Headers](https://www.w3.org/TR/fetch-metadata/) (Working Draft, 21 Sep 2026) | Two passes | `Sec-Fetch-Site` is set by the browser and is "unmodifiable from JavaScript". Basis for refusing a decision from another site (G549). A work in progress, cited as such. |
| [Public Suffix List](https://publicsuffix.org/list/public_suffix_list.dat) | Read directly | `hf.space` is listed, so every Space is a separate site from every other (G548). |
| [Chrome, "Architecting security for agentic capabilities"](https://blog.google/security/architecting-security-for-agentic/) (8 Dec 2025) | One pass | A critic that sees only action metadata, origin sets fixed outside the planner, confirmation before consequential actions. Direction for putting the action rules on the way out of the server. |
| [Meta, "Agents Rule of Two"](https://ai.meta.com/blog/practical-ai-agent-security/) (31 Oct 2025) | One pass | An agent that reads untrusted input, touches private data and changes state needs human approval. The co-pilot does all three, so the approval is the control that matters most. |
| [NIST, design tenets for agent identity](https://www.nist.gov/blogs/cybersecurity-insights/design-tenets-agent-identity-management-systems) (27 Oct 2026) | One pass | Deterministic access decisions and immutable evidence; a proposed initial set, not a standard. |
| [WCAG 2.2 SC 3.3.4](https://www.w3.org/WAI/WCAG22/Understanding/error-prevention-legal-financial-data.html) | One pass | A legal or financial submission is reversible, checked or confirmed. The Allow delay keeps Disallow and Edit available at once. |

**Could not be verified, and not relied on.** `openai.com` answered 403 to the fetch
environment, so nothing here rests on OpenAI's own guidance. ISO/IEC 42001's text is
paywalled. No source was found that measures clickjacking or hidden-field harvesting
against browser agents. No source claims a deterministic page-level signal that tells
a person from an agent driving the browser's real input.

## Observed in production

Read from the Space (`https://landwind22-ura-chatbot.hf.space`) on 2026-11-10 with
GET requests only. Nothing was posted to it.

| Observation | Evidence | Meaning |
| --- | --- | --- |
| Any Space may frame the app | `content-security-policy: … frame-ancestors 'self' https://huggingface.co https://*.hf.space` | G548 live |
| Only huggingface.co needs to | The Space page serves `<iframe src="https://landwind22-ura-chatbot.hf.space/?__theme=system">` from `huggingface.co` | Dropping the wildcard does not blank the embedded view |
| The service is up | `/api/health` → `{"status":"alive","version":"1.2.0"}` | |
| `APP_ENV` and the ledger flag | Not readable from outside | Whether G541 lost ledger rows on the Space is not known; with simulated connectors refused in production, there was no effect to record |

## Findings and phases

Severity is the consequence for a taxpayer once a live connector is behind the gate.

### Phase 0: the integrity of an action that takes effect

| Gap | Severity | Evidence before the fix | Fix |
| --- | --- | --- | --- |
| G541 | Critical | The route called `db.append_audit_event`, which does not exist, inside `except Exception: logger.debug(...)`. With `audit_ledger` on, a declined task left `[]` in the ledger; so did an allowed one. The runbook and ADR-0014 said every decision appends a hash-chained row. | Decisions go through `get_ledger().append`. An approval is written before the portal is reached (`portal_task_approved`, with the evidence of the confirmation); if it cannot be written the task does not run and the lease is released. The outcome row follows; its failure is counted and logged at error and does not undo the decision. |
| G542 | Critical | The in-flight set and the receipt cache were per process and the shared row was written after the effect. Two workers given the same approval both ran it: PRNs `226115011064` and `226112590453` from one click. A store that could not be read counted as "not run yet". | `claim_workflow_session` takes a lease on the shared row in one statement, on SQLite and Postgres. The loser replays the receipt or gets 409. A store that cannot be read or written refuses the decision (503). A failed run releases the lease; a decline takes the same lease, so it cannot report "nothing was sent" over a task being sent. |
| G543 | High | A card minted with no `X-Session-ID` was approved with no session and no digest and ran. The owner key fell back to the conversation id, which AGENTS.md forbids. `displayed_fields_sha256` was optional (G265, UI-09). | The digest is required. No confirmation is signed without a signed-in user or a session id of the caller's own (`session_required`); the route has no fallback to the conversation id. A state signed for a user is honoured for that user only. |
| G544 | High | A TIN application with no date of birth was given `1992-01-01` and shown as `1992-**-**`; the form never asked. A defaulted tax head read the same as one the taxpayer chose. A tier 1 lookup was recorded as `caller_confirmed=True`, and every action row went to the `default` tenant. | The default is gone and a test forbids one on any identity field. The connector is handed no invented identity. A value the taxpayer did not give is `assumed` on the card and marked in the browser. The confirmation flag is true only after a person allowed the task, and the tenant is passed. |

### Phase 1: what the page says never chooses what the agent does

| Gap | Severity | Evidence before the fix | Fix |
| --- | --- | --- | --- |
| G545 | High | Telemetry `{"field":"tin","selector":"body *"}` came back as `spotlight safe 'body *' '1000123456'`. A field named `card'], form[action*='evil` produced the selector `[name='card'], form[action*='evil'], #card'], …`. | Selectors come from the server's own map. A field it does not know is named in words with no selector. A name that is not a plain identifier is dropped. The client's `selector` is accepted and ignored. |
| G546 | High | "Server Error" in a banner or a step title selected a recipe whose step was `click safe "a#loginLink, button#btnRefresh, a[href*='login']"`. | Every action list leaves through `harden_dom_actions`: a click waits for the taxpayer whatever chose it, nothing targets a credential field, an unknown kind is dropped, only a fill or a select carries a value. The recipe's own gate is corrected as well. |
| G547 | Medium | `visible_fields` was bounded only by the 5 MiB body cap. `</untrusted_portal_dom_telemetry>` survived into the grounding block (no consumer today, so latent). A banner became "**Diagnosis**: Portal Alert Banner: …" in the assistant's voice, and its links reached the card. `card_number`, `auth_code` and `mfa_code` were not treated as credentials. | 64 fields, 64-character names, 2,000-character values (422 beyond). Text is folded to one line, the fence tag is removed in any spelling, untrusted links are removed, and the reply says "The page shows this notice: “…”". More credential names are scrubbed. |

Links and images were already removed from the final reply by the output guards; that
part was checked and is not a finding.

### Phase 2: the confirmation surface in the browser

| Gap | Severity | Evidence before the fix | Fix |
| --- | --- | --- | --- |
| G548 | High | Production CSP named `https://*.hf.space` as a frame ancestor, and `hf.space` is on the Public Suffix List: any Space could frame the app and overlay the Allow button. | The default is `'self' https://huggingface.co`. Headers are baked at build, so this is live once the image is rebuilt. |
| G549 | Medium | Allow took a click made by `element.click()` and could be pressed the instant the card appeared. The decision route did not look at where a browser said the request came from. | Allow needs a click the browser raised and is held back 800 ms whenever what it approves changes; Disallow and Edit are not. The route refuses `Sec-Fetch-Site` other than `same-origin`/`none` unless the origin is on the CORS list, and the approval row records it. |

### Phase 3: evaluation and records

| Gap | Severity | Evidence before the fix | Fix |
| --- | --- | --- | --- |
| G550 | Medium | Nothing exercised the co-pilot against a hostile page. | `evals/portal_copilot_scenarios/adversarial.jsonl`: 27 pages run through `/v1/chat`, each required to leave the action rules intact; three benign pages keep the co-pilot useful. |

### Phase 4: found by the blind re-run

| Gap | Severity | Evidence before the fix | Fix |
| --- | --- | --- | --- |
| G556 | Critical | Signed in as a taxpayer with one obligation, "Check my tax obligations" gave a confirmation card showing `tax_head: VAT_STANDARD`; allowing it answered "Tax head 'VAT_STANDARD' successfully registered". The task sat in the tier named for reads and its tool only registers. | A check reads the account (`ura_account_profile`). Registering is a confirmed write with the tax head the taxpayer named, no default. A test holds every read tier to read-only tools. |
| G557 | Critical | Subject `auth0-7f3a9c` ran an account task for TIN `1000000001` (the check had nothing to compare, so it passed). `google-oauth2\|1098765432` was bound to the ten digits inside it, as if they were a TIN. Subject `1000023456` recorded an EFRIS stock-in against TIN `1000000005`. A caller with no granted purposes and role `taxpayer` ran `ura_account_profile`. | The ownership check fails closed and covers every sign-in task that names a TIN. A sign-in is bound by a subject that is itself a TIN or, outside production, by the simulation profile the caller linked in this session. Consent and role are checked before a sign-in task runs. |
| G558 | High | An anonymous request to verify TIN `1000000008` returned `nin_or_passport`, `mobile`, `email`, `registered_office` and the obligations. Simulator data, but that is the contract a live connector would inherit. | The tool returns the TIN, legal name, category, status and registration date. |
| G559 | High | Edit 2.5M to 3M, allow the new card, then allow the old one: two PRNs. Decline, ask again, allow the new card: "already declined", because the key came from the details alone. | An edit retires the proposal it replaces. Each proposal has its own key; the same card pressed twice still runs once. Only confirmable tiers run. |
| G560 | High | "Generate a PAYE PRN … via MTN mobile money" with a page showing VAT and bank was proposed as `VAT_STANDARD`, `COMMERCIAL_BANK`. "Generate a PRN" with only page fields was proposed with the page's TIN and UGX 49,000,000 as if the taxpayer had given them. A statutory handoff staged `bank_account: 0100-ATTACKER`. | Typed values are laid over the page's. A value nobody typed is marked `assumed`. A handoff stages only its task's fields. |
| G561 | Medium | With no entities at all, recipes returned `('select','safe','0010')`, `('fill','safe','50202301')`, `('select','safe','Bank')`. After a PRN was registered, the response carried a recipe ending in a click on Generate. | No literal defaults; a fill or select with nothing to enter becomes a spotlight; a live run returns no recipe. |
| G562 | Medium | `date_of_birth: "31/02/2190 or so"` and `surname: "<b>x</b>"` were signed and executed. A first submission took a 513-character value or a nested object. An expired card answered "Invalid or tampered request_state signature". | A date is a real date and a date of birth is past; a field is text or a number, at most 512 characters, no markup; expiry says so. |
| G563 | Medium | With locale `lg` the buttons read "Allow & Execute", "Disallow", "Edit Details": the server's label won over the translation. A TIN application card said "Unpaid PRNs expire automatically after 21 days". | The buttons use the reader's language. Only a payment reference carries the PRN notice. |

## Blind re-run of the prompt

A fresh agent was given the prompt, the baseline checkout (`222ce12a`) and nothing else, and
asked for the audit half only. It could not see this review.

**What it confirmed.** The ledger call to a function that does not exist (G541), the store
failing open on a read or write error (G542), the optional digest and the conversation-id
fallback (G543), the invented date of birth (G544), page-chosen selectors and the `safe` click
(G545, G546), page text in the reply and on the card (G547), and the `*.hf.space` frame
ancestor on the live Space (G548).

**What it found that the first pass had not:** G556 to G563 above. The first pass followed the
mechanisms and found them wanting in the ways it then fixed. It had even listed
`tin_tax_obligations` as a write in a read tier in its own notes and moved on. The blind run
asked of each task what it does, to whose record and what it returns, and found that a check
registers a tax head, that the ownership rule binds nobody real, and that a public lookup
returns a NIN. Those are worse than most of what the first pass fixed. The prompt now asks
those questions outright.

**What the first pass had that it did not.** It could not reproduce two workers running one
approval at the same moment and said so; the first pass reproduced it by letting a second
worker act inside the connector call (two PRNs). It did not run the frontend or Postgres, so
the Allow guard and G554 are the first pass's alone.

**What was not adopted from it.** Two findings are recorded as open rather than fixed: an
unsigned state outside production (G564), which is the MCP transport's decision, and the
metadata on a reply that quotes the page (G567). Its observation that the display digest is
computed from the payload in memory, not from what is rendered, is true and is the same limit
as G551: nothing in a page proves what a person saw.

**What this says about the prompt.** It was enough for a fresh agent to find the defects that
mattered, with reproductions, in one pass. Its weak point was the author's own: it described
the mechanisms well and the tasks not at all.

## Examined and found sound

- **Links in the reply.** `[Sign in again](https://evil.example/login)` and an image
  URL in telemetry reached `deterministic_reply`, but the served reply had
  `[external link removed]`: the output guards run on this path.
- **Body size.** 3,000 fields of 5,000 characters were refused with 413.
- **Hidden defaults at the connector.** Apart from the date of birth, every default
  sent to a connector was already on the confirmation card; only a fixed stock-in
  description was not. (That a default on the card can still be the wrong act is G556,
  which the blind run found.)
- **Tier 1.** All five tasks that run without confirmation map to low-risk read-only
  tools; a test now holds that. (What one of them returned is G558.)
- **The inbuilt workspace.** It does not run `querySelector` on a selector from the
  server: it matches a step to its own known fields. The selector findings matter to
  executors outside it (the companion contract, an agent reading the navigator tool).
- **Cross-site request forgery.** The bearer token and the session id travel in
  headers and CORS lists explicit origins, so a cross-site page could not send a
  decision before this review either. The Fetch Metadata check is a second layer.
- **Signed state.** Tampering, expiry (15 minutes), cross-session and cross-user
  replay were refused before this review.

## Found in passing

| Gap | Severity | Evidence | Fix |
| --- | --- | --- | --- |
| G554 | High (Postgres deployments) | The lease has a Postgres mirror, and the Postgres tests are skipped without `POSTGRES_DSN`, so it was run against a throwaway PostgreSQL 16.2. `init_db()` raised before reaching it: `psycopg.ProgrammingError: only '%s', '%b', '%t' are allowed as placeholders, got '%''`, from `LIKE 'state_%'` in the legacy-key migration. With `APP_ENV=production` the service refuses to start on that error; `App/docker-compose.prod.yml` sets `ANALYTICS_BACKEND=postgres`. | `_to_pg_placeholders` doubles a literal `%`. With that, the bootstrap completed and the lease passed ten checks, including 16 concurrent claims with one winner. |
| G555 | Not assessed | The same run logged `relation "voice_call_turns" does not exist` from the first retention pass on an empty database. | Open. Seen once and not investigated. |

## Verification

- Backend: `tests/agents/test_browser_agent_assurance_2026_11.py` (99 tests, 649 cases with the recipe and credential-name parameters) and
  `tests/agents/test_portal_copilot_adversarial_eval.py` (29).
- Frontend: `PortalConfirmGuard.test.tsx`, `securityHeaders.test.ts`, four new cases in
  `PortalCopilotCard.test.tsx`; `tsc --noEmit` and ESLint clean.
- Phases 0 to 3: 48 controls disabled in turn (40 backend, 8 frontend). A test failed for 45,
  and the other three are second layers behind a control that is caught. The first pass left
  four controls with no failing test (an unreadable decision store, an edit while in flight,
  the analyser's final hardening pass, the route's session check); each now has one.
- Phase 4: 36 controls disabled in turn (34 backend, 2 frontend). A test failed for 35. The other is a guard that
  only saves a lookup: a missing session cannot match a link either way.
- Postgres: the lease and the bootstrap on a real PostgreSQL 16.2 (see G554).
- `governance/compliance_check.py` and `threat-model/validate_threats.py` pass.

## Residual risk

- **G551, a person cannot be told from an agent.** An agentic browser raises trusted input and
  uses the taxpayer's session, so every control here holds equally for a person and for an
  agent acting for them. What changed is that the approval is bound to what was shown, runs
  once and leaves evidence. A high-value approval is still a checkbox. Proof that the account
  holder is present needs a step-up at the identity provider (WebAuthn user verification or
  RFC 9470 `max_age`) before a live connector is switched on.
- **G565, a sign-in carries no verified TIN.** The ownership check now fails closed. Outside
  production a caller is bound to the simulation profile they connected, so the prototype
  keeps working, for their own profile only. In production that link flow is refused and the
  identity provider issues no TIN, so signed-in taxpayers are refused account tasks until it
  does. That is the safe side of a rule that used to let them into anyone's account. Tasks
  named by a URSB number, a customs entry or a stamp order have no ownership check at all.
- **G564, an unsigned state outside production.** With no state secret set, a hand-built
  proposal for a confirmable task is accepted. The simulators run only there.
- **G566, lookup by NIN.** The public lookup tells anyone holding a NIN that person's TIN and
  name. Whether verification should take a TIN only is URA's decision.
- **G552, agents are offered nothing declared.** WebMCP is in origin trial and Web Bot Auth is a
  draft that does not speak to authorization. Neither is adopted.
- **G553, inline script on the app's own origin.** `script-src 'unsafe-inline'` means a script
  that reaches the origin can call the decision route itself. The click guard does not cover
  it; a nonce-based CSP would.
- **Defaults remain, marked.** A tax head, a payment mode or a district the taxpayer did not
  give is still filled in ("Kampala" for a TIN application), now with a notice. Whether a
  registration should carry any default is a product decision for URA.
- **The phrase screen is one layer.** `inj_luganda_unscreened` in the eval set is a sentence it
  does not recognise. The structural rules do not depend on it.
- **A worker that dies mid-task.** The lease then stays held for 15 minutes and the outcome has
  to be read from the connector. With a simulator that costs nothing; with a live connector it
  needs the connector's own idempotency key.
- **G567, a quoted page is still labelled faithful.** A reply that quotes the page carries
  `faithfulness_score: 1.0`. Not assessed further.
- **Translations.** The three new strings are English in all five locales until reviewed (G275).
- **Not live until rebuilt.** `frame-ancestors` is baked into the frontend image at build.


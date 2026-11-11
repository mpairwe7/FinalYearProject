# ADR-0038: Browser agent assurance — one approval, run once, for its owner, bound to what was shown; the page chooses nothing

## Status

Accepted — 2026-11-11

## Context and Problem Statement

The portal co-pilot (ADR-0014) is the assistant's browser agent:
- it reads DOM telemetry from the taxpayer's browser (field names, values, error text);
- it returns `dom_actions` (spotlight, fill, select, click) that the inbuilt workspace, a browser
  companion or another agent may run;
- it runs a portal task once a person allows it on a confirmation card.

The application is also driven by agents that are not ours: an agentic browser acting for a
taxpayer, a script or extension in the page, a site that frames it.

An audit on 2026-11-10 measured this surface against the references in
[the review](../plans/browser-agent-assurance-2026-11-10.md): the OWASP Top 10 for Agentic
Applications 2026 (ASI01, ASI02, ASI09), Chrome's guidance to sites that expose consequential
actions to agents, and 2026 research on approval mismatch (Loopjacking), login-prerequisite
injection (LoginTrap) and injected form fields (EIA).

Every portal task runs against a local simulator today and production refuses those connectors,
so no statutory record was created. The gaps are in the gate a live connector will sit behind.
Each was reproduced before it was fixed:

- **G541, no decision reached the ledger.** The route called `db.append_audit_event`, which does
  not exist, and logged the `AttributeError` at debug level.
- **G542, exactly-once was per process.** Two workers given one approval both ran the task (two
  PRNs). A store that could not be read counted as "not run yet".
- **G543, an approval proved nothing.** No session and no display digest were needed. The owner
  key fell back to the conversation id.
- **G544, the agent invented a value.** A TIN application with no date of birth got `1992-01-01`,
  shown masked. A default read the same as a value the taxpayer gave. A lookup nobody confirmed
  was recorded as confirmed.
- **G545, the page chose the element.** A selector sent in telemetry came back as a `safe` action
  with a fill value, and a field name was written into a CSS selector.
- **G546, the page chose a click.** "Server Error" in a banner selected a recipe whose click on a
  login link was `safe`.
- **G547, telemetry was unbounded and unattributed.** Bounded only by the 5 MiB body cap, able to
  close its own fence, and written into the reply as the assistant's diagnosis.
- **G548, any Space could frame production.** `frame-ancestors` named `https://*.hf.space`, and
  `hf.space` is on the Public Suffix List.
- **G549, Allow took any click.** A script's `click()` counted, the button was live the instant
  the card appeared, and the route ignored where a browser said the request came from.
- **G550, no hostile-page evaluation.**

The audit prompt was then run blind, by a fresh agent with no knowledge of the first pass,
against the unfixed baseline. It confirmed the findings above and found eight more. The first
pass had followed the mechanism (the lease, the digest, the selectors); the blind run asked of
each task what it does, to whose record, and what it returns. Each was reproduced again here
before it was fixed:

- **G556, a check registered.** "Check my tax obligations" went to the tool that registers a tax
  head, in the tier named for reads, with `VAT_STANDARD` as the default.
- **G557, an account task was not bound to its owner.** The TIN ownership check passed any
  sign-in whose subject was not a TIN, and was not applied to signed-in writes. Consent and
  role were declared and never read.
- **G558, the public lookup returned private data:** NIN, phone, email, office, obligations.
- **G559, a superseded proposal still ran.** After an edit both cards could be allowed. A
  proposal's key came from its details alone, so a declined one could not be asked for again.
- **G560, the page beat the person.** A tax head on the page replaced the one typed in the
  message, and a value only the page supplied read as the taxpayer's.
- **G561, a recipe invented values** (a tax head, a commodity code, a payment mode) as `safe`
  steps, and a task that had run came back with a recipe to run it again.
- **G562, anything was a form value:** `31/02/2190 or so` as a date of birth, `<b>x</b>` as a
  surname, any JSON value of any length.
- **G563, the card misinformed.** Consent buttons were English in every locale, and every card
  promised what is true only of an unpaid PRN.

## Decision Outcome

Four rules, each enforced in one place.

**1. One approval runs once, and is recorded first.**

- `claim_workflow_session` (SQLite and Postgres) takes a lease on the proposal's shared row in
  one statement. The in-process caches only save a round trip.
  - The loser of the lease replays the stored receipt, or gets 409 while the task is in flight.
  - A store that cannot be read or written refuses the decision with 503. Nothing runs on a
    guess.
  - A run whose connector raised or answered `ok: false` releases the lease, so it can be tried
    again. That is not proof nothing was sent: a call can fail after the far side acted on it.
    The simulators make no partial effects. A live connector must carry its own idempotency key
    and say when an outcome is unknown, so a retry cannot repeat what already happened.
  - A decline takes the same lease, so it cannot say "nothing was sent" over a task being sent.
- With `audit_ledger` on, `portal_task_approved` is appended before the portal is reached. If it
  cannot be appended the task does not run.
  - The row holds digests and the evidence of the confirmation: `display_digest_verified`,
    `session_bound`, `authenticated`, `high_value_acknowledged`, `review_ms` and
    `sec_fetch_site`. It holds no taxpayer value.
  - The outcome row (`portal_task_executed`, `_declined`, `_prepared`, `_error`) follows. Its
    failure is counted (`audit_append_failed_total`) and logged at error. It does not undo the
    decision: a refusal never waits on its record (ADR-0022).

**2. An approval is bound to a caller and to what was shown.**

- `displayed_fields_sha256` is required to allow. `PORTAL_COPILOT_REQUIRE_DISPLAY_DIGEST` is
  gone. This closes the part of G265 that ADR-0014 left open (UI-09).
- No confirmation is signed for a caller with neither a signed-in user nor a session id of its
  own: the card is `session_required`. The route does not fall back to the conversation id.
- A state signed for a user is honoured for that user only.
- No identity field has a default, and a test holds that. A value the taxpayer did not give is
  `assumed` on the card and marked in the browser with a notice to check it. That covers a
  default, and a value read off the page or a screenshot that nobody typed.
- Each proposal has its own key. The same card pressed twice runs once; a card asked for again
  is a new proposal. An edit retires the proposal it replaces, so only the card on screen runs.
- Only the two tiers that are ever put on a confirmation card can be run by one.
- `caller_confirmed` is true on the action record only after a person allowed the task.
- The route refuses a request whose `Sec-Fetch-Site` is not `same-origin` or `none`, unless its
  origin is on the CORS list. A client that is not a browser sends no such header.

**3. A task does what was asked, for the person it belongs to.**

- A tier that reads holds only read-only tools, and a test holds that. Asking to check, view or
  query obligations reads the account; registering a tax head is a confirmed write with the tax
  head the taxpayer named.
- `check_subject_tin_ownership` fails closed and covers every sign-in task that names a TIN,
  writes included. A sign-in is bound to a TIN in one of two ways:
  - its subject is itself a TIN (never a ten-digit run inside a longer subject);
  - outside production, the caller linked a simulation profile in this session by signing in to
    it (`linked_account_tin`). Profiles linked to different TINs bind none.
- `check_consent_and_role` runs before a sign-in task does. A task open to anyone is consented
  to on its own card.
- The public TIN lookup returns the TIN, legal name, category, status and registration date.
- A task field is text or a number of at most 512 characters with no markup; a date is a real
  date, and a date of birth is in the past.

**4. What the page says chooses nothing.**

- A selector comes from the server's own map. A field the server does not know is named in
  words and carries no selector. The client's `selector` is accepted and ignored.
- Every action list leaves through `harden_dom_actions`:
  - a click waits for the taxpayer, whatever chose it, and keeps only selector parts that name a
    `button` or an `input` by element type: a link goes wherever the page points it, the server
    cannot see where, and an untyped `#id` could be a link;
  - nothing targets a credential field;
  - an action of an unknown kind is dropped;
  - only a fill or a select carries a value.
  A fill or a select with nothing to enter becomes a spotlight: no recipe carries a value
  nobody gave. A task that ran live returns no recipe.
- What the person typed is laid over what the page shows, never under it. A statutory handoff
  stages only its task's fields.
- Telemetry is bounded (64 fields and 64-character names, refused beyond; values cut to 2,000
  characters) and screened as
  one line: instruction phrases, the fence tag in any spelling and untrusted links are removed.
  The reply quotes it as what the page shows.

In the browser:

- `frame-ancestors` defaults to `'self' https://huggingface.co`, the one page that embeds the
  app.
- Allow needs a click the browser raised (`isTrusted`) and is held back 800 ms whenever what it
  approves changes. Disallow and Edit are available at once.
- The consent buttons are in the reader's language. Only a payment reference is said to expire
  unpaid; any other task says it cannot be undone from here.

Considered and not adopted:

- **A check that the approver is a person.** No page-level signal tells a person from an agent
  that drives the browser's real input. Behavioural classifiers give a score with false
  positives on people who click once and leave. The evidence is recorded on the approval row
  instead of being used as a gate, and G551 stays open for a phishing-resistant step-up.
- **A minimum review time on the server.** A script can wait. The delay is in the browser, where
  it stops a click already on its way; the server records `review_ms`.
- **Web Bot Auth for approvals** (`draft-ietf-webbotauth-httpsig-protocol-01`). An
  Internet-Draft that identifies an agent and, in its own words, says nothing about whether the
  request is authorized. Recorded as G552.
- **A TIN claim of our own naming.** The ownership check needs a verified TIN on the sign-in,
  and the identity provider issues none. Inventing a claim name here would be a guess at URA's
  identity design. `authenticated_tin` is the parameter it will arrive in (G565).
- **Requiring a state secret outside production.** That is the MCP transport's choice
  (ADR-0005) and changes every signed state, not only the co-pilot's (G564).
- **WebMCP tools** (`document.modelContext`). In origin trial and subject to change. The grant
  the Chrome guidance asks a site to hold is the signed state this ADR hardens, so adopting it
  later changes the surface, not the gate.

## Consequences

- **Good:** a double click, a retry or a second worker cannot issue a second PRN or a second
  application.
- **Good:** with `audit_ledger` on, an approval is in the ledger before its effect, and a task
  whose approval cannot be recorded does not run. Within those limits:
  - with the flag off, nothing is appended, and decisions are recorded only in the decision store;
  - an outcome row that cannot be appended after the decision is counted
    (`audit_append_failed_total`) and logged, and the decision stands.
- **Good:** nothing a page writes can name an element, buy a click or pass on a link.
- **Good:** another party's Space can no longer frame the confirmation card.
- **Good:** the Postgres backend finishes its schema bootstrap. Running the lease on a real
  server found it raising on a `LIKE 'state_%'` literal (G554); the query seam now escapes a
  literal `%`.
- **Changed for API clients:** a decision needs `X-Session-ID` (or a signed-in user) and
  `displayed_fields_sha256`. The web client has sent both since 2026-10-30. A second decision on
  a proposal in flight is 409, and a store or ledger outage is 503, where both were 200 or 400.
- **Changed for taxpayers:** a TIN application asks for the date of birth instead of assuming
  one. Allow is briefly unavailable when a card appears. "Check my tax obligations" reads the
  account instead of proposing a registration. Asking twice for the same thing gives two
  proposals, each its own.
- **Changed for signed-in taxpayers (G565):** an account task is refused unless the sign-in is
  bound to the TIN it names. Before this, a sign-in whose subject was not a TIN was allowed any
  taxpayer's.
  - Outside production, a caller who has connected a simulation profile acts for that profile's
    TIN and no other. One who has not is told their sign-in is not linked to a TIN.
  - In production the link flow is refused and the identity provider issues no TIN, so signed-in
    taxpayers are refused account tasks until it does. The simulated connectors are refused
    there too, so nothing that worked stops working.
- **Changed for anyone calling the TIN lookup tool:** it returns five public fields, not the
  record.
- **Neutral:** the two new strings are English in all five locales until reviewed translations
  exist (G275), as the other approval-failure strings are.
- **Open (G551):** a high-value approval is still a checkbox. Proof that the account holder is
  present needs a step-up at the identity provider (WebAuthn user verification, or RFC 9470
  `max_age`).
- **Open (G552):** agents are not offered declared tools, and a declared agent is not recognised.
- **Open (G553):** `script-src` allows inline script. A script running on the app's own origin
  can call the decision route itself; the click guard does not cover that.
- **Open (G564):** outside production, with no state secret set, a state is unsigned.
- **Open (G566, G567):** the lookup also takes a NIN; a reply quoting the page is still labelled
  fully faithful.

## Confirmation

- `tests/agents/test_browser_agent_assurance_2026_11.py` (99 tests, 649 cases with the recipe
  and credential-name parameters) covers:
  - the lease going to one caller, lapsing, and not crossing tenants;
  - a second worker, a late decline and a late edit while a task is in flight;
  - nothing running when the lease, the decision store or the ledger cannot be written;
  - a failed run and a ledger outage both leaving the proposal retryable;
  - the digest, the session and the user binding;
  - no invented identity value, and assumed values marked;
  - the approval row written before the connector is reached, with its evidence;
  - a check that reads, read tiers holding only read-only tools, the ownership rule with and
    without a linked profile, consent and role;
  - the public lookup's fields, an edit retiring its predecessor, per-proposal keys;
  - typed values over the page's, recipes that invent nothing, and what a form field may hold;
  - server-owned selectors, the action rules over every recipe in every state, bounded and
    attributed telemetry, and the cross-site refusal.
- `evals/portal_copilot_scenarios/adversarial.jsonl` (27 hostile and benign pages) runs through
  `tests/agents/test_portal_copilot_adversarial_eval.py`.
- `App/frontend/src/__tests__/components/PortalConfirmGuard.test.tsx` (5),
  `src/__tests__/config/securityHeaders.test.ts` (1) and four cases in
  `PortalCopilotCard.test.tsx` cover the Allow guard, `frame-ancestors`, the assumed marker,
  the translated consent buttons and the per-task notice.
- **On a real PostgreSQL 16.2** the Postgres mirror of the lease passed ten checks, including 16
  concurrent claims with one winner. `TestWorkflowLeaseOnPostgres` in
  `tests/agents/test_backend_shim.py` repeats them wherever `POSTGRES_DSN` is set; it is skipped
  on SQLite.
  - That run found the Postgres schema bootstrap itself raising on a `LIKE 'state_%'` literal
    (G554), fixed here in `_to_pg_placeholders`.
- **The audit prompt was run blind** against the baseline by an agent that could not see this
  work. It confirmed G541 to G548 and found G556 to G563, each reproduced again before its fix.
  The review records what it found, what it missed and the paragraph it added to the prompt.
- Each control was disabled in turn in a throwaway worktree:
  - phases 0 to 3: 48 controls (40 backend, 8 frontend), a test failed for 45;
  - phase 4: 36 controls (34 backend, 2 frontend), a test failed for 35.

  The four that no test notices are each behind something a test does notice:
  - the analyser reading a page selector, which the sanitiser has already dropped;
  - the error recipe's own gate, which `harden_dom_actions` overrides;
  - the Allow button's initial state, which the arming effect resets on mount;
  - the session guard in `linked_account_tin`, since a missing session matches no link.
- The first mutation pass left four controls with no failing test: an unreadable decision store,
  an edit while in flight, the analyser's final hardening pass, and the route's session check.
  Each now has a test.
- `governance/compliance_check.py` and `threat-model/validate_threats.py` pass (threats T83–T86
  added).
- **Review round 1 (CodeRabbit, PR 713).** These changes were made:
  - A long telemetry value is cut to 2,000 characters instead of refusing the turn: long
    objection grounds left the taxpayer with no help.
  - A date of birth is compared with Kampala's date, not the server's.
  - The recipe is chosen by the portal alone, never by a banner or a step title, and the
    recovery click names elements instead of matching any `href` containing "login".
  - The frame-policy test no longer depends on the environment, and the no-embed policy has
    its own test.
  - The API reference states the digest's exact serialisation.
  - The Postgres lease tests run only when both the DSN and the backend select Postgres, and
    assert that they do.
- **Review round 2.** CodeRabbit confirmed the recovery fix and noted that `a#loginLink` still
  named a link whose destination nothing checks. No recipe clicks a link now: the recovery step
  clicks a refresh button, the generic step lost `a.action-link`, and `harden_dom_actions` drops
  a link from any click's selector (a click left with no target becomes a spotlight).
- **Review round 3.** CodeRabbit noted that an untyped selector such as `#loginLink` could still
  match a link. The rule is now an allowlist: a click keeps only selector parts that name a
  `button` or an `input`. A submit button still posts to its form's action, which the page
  controls; the click waits for the taxpayer for that reason.
- **Review round 4.** The allowlist checked the start of each selector part, but the element a
  selector matches is its last compound: `button a#login` matches the link. It now checks the
  last compound, with attribute values set aside so `input[value='Generate PRN']` is not split.
  The ADR also no longer overstates two guarantees: a released lease is not proof that nothing
  was sent, and the ledger records decisions only with `audit_ledger` on.

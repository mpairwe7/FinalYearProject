# ADR-0050: Staff controls that change what taxpayers are told say what they do

## Status

Accepted, 2026-11-13. Phase 3 of the
[13 November intelligent UI integration audit](../plans/intelligent-ui-integration-audit-2026-11-13.md)
(G634–G639). The service owner asked for the audit's fixes to be applied phase by
phase in the session that made it.

## Context and Problem Statement

Six staff controls change what the assistant tells taxpayers, or the record of
what was wrong with it, and each made that change easier or larger than it looked:

- "Verify & Hotfix" on a taxpayer's discrepancy report pre-filled the precedence
  rule (injected as law that "STRICTLY SUPERSEDES" retrieved passages) with the
  taxpayer's own unverified correction, ticked "withdraw the cited sources" by
  default, wrote a stock note, required no statute, audited only two ids, and left
  cached answers in place (G634, Critical);
- the officer's translation preview was sent after the English was edited, and
  "Figures & statutory amounts verified" was shown when translation was
  unavailable (G635);
- one-click canned replies stated results — an invoice "audited", a signature
  "authentic", a credit "reconciled against URA ledgers", customs "cleared" — that
  nothing in the workbench recorded, and asked for an upload the officer chat
  cannot take (G636);
- the overrides console said "character for character" and "word for word" while
  the server matches normalized, rewritten and translated questions and replaces
  an override with the same key without a word (G637);
- the flags console called overrides durable on the Space, which loses them on
  restart, labelled stored overrides "this replica", and switched the figure hold
  off in one click (G638);
- "Reopen" deleted an auditor's finding and its note, unconfirmed (G639).

OWASP ASI09 asks to separate preview from effect and to give plain-language risk
summaries; OWASP LLM03 (2026) asks for approval on high-impact actions; EU AI Act
Art. 14(4) (a benchmark here, not a duty) asks that overseers understand what they
change and can stay aware of automation bias.

## Considered Options

For the canned replies (G636): remove the result-stating ones, or keep them as
templates the officer must complete. Officers do check invoices and credits in
URA's own systems, so the shortcuts are useful; what was wrong was asserting a
result. Chosen: templates with `[[…]]` parts the composer will not send unfilled.

For a stale translation (G635): only fix the console, or also refuse it on the
server. Another client can send `officer_reply_localized` too. Chosen: both.

## Decision Outcome

1. **Verify & Hotfix** starts empty with nothing withdrawn; a precedence rule needs
   a statute reference and a topic (the console disables Confirm; the API answers
   422 before writing anything); a summary says what the hotfix will change for every
   taxpayer before it is applied. The API audits the override (with what it
   replaced, as the override route does), the precedence (`rule_sha256`, statute)
   and the tombstones (sources and chunk ids), and retires cached answers as the
   standalone routes do; a chunk-only hotfix does both too. The writes commit one
   by one, so cached answers are retired even when a later write fails (the report
   then stays pending).
2. **A translation that loses a figure is not sent.** The reply and End-chat routes
   drop a supplied translation whose figures differ from the English and translate
   again through `localize_reply`, counted in
   `officer_reply_translation_rejected_total`. The check is stricter than the
   shared `mt.figures_survived`, which tolerates one changed figure among enough
   matches: every amount and rate in the English must appear in the translation
   (a rate as a number, since Luganda has no percent sign), and none may be added.
   The server's own translation, made when a supplied one is refused or absent,
   passes the same check, or the officer's English is sent.
   The console shows and sends a preview only of exactly the text being sent,
   after its figure check passed; an edit, even one made while the translation is
   on its way, drops it; an unavailable translation says so.
3. **Canned results are templates.** Their `[[…]]` parts are what the officer
   found; Send, Send & end and End chat are disabled while any remain, an emptied
   `[[]]` included (`unfilledPlaceholders`). The composer does not verify what the
   officer writes in them or record that a check was made. The document request
   names email or a station.
4. **The overrides console tells the truth.** It shows the normalized key, explains
   rewritten and translated matching, and shows the live answer a save replaces
   (the store reads the replaced reply inside the write's own transaction, so a
   concurrent save cannot make the ledger name the wrong one),
   with "Replace the live answer" on the button; the ledger records `replaced`
   and the before and after digests.
5. **The flags console says what it knows.** The API reports
   `overrides_durability` (`durable`, `ephemeral`, `unknown`) from where the store
   lives, with `ANALYTICS_DB_PERSISTENT` for a mounted volume; the console words
   it accordingly, labels a stored override "stored · every process", and asks
   before switching off a safety control (`stream_figure_hold`,
   `spoken_figure_guard`, `action_level_audit_v3`, `guardrail_strike_friction`,
   `verifiable_decision_receipts`, `autonomous_outcome_verification`).
6. **Review decisions are kept.** Every decision, a reopening included, is appended
   to `audit_review_history` (purged by the same rules as the decision) in the same
   transaction as the change it records (`database.execute_all`), and a
   decision stored before the table existed is copied in once as its item's first
   entry; the queue returns it; Reopen asks first and earlier decisions are shown,
   on a decided item behind "Earlier decisions".

## Consequences

- A hotfix takes a deliberate entry and a statute; an administrator in a hurry can
  no longer turn a taxpayer's claim into law for the model in two clicks.
- An officer's reply may reach the taxpayer in a server translation rather than
  the preview they saw, when the preview no longer matched; the translation
  carries the same figures.
- Templates add a step for officers who did check something; that step is the
  point.
- The Space's flags console now says its overrides are lost on restart, which is
  true until storage persists (G616); the `FLAG_*` secret remains the durable path.
- `normalizeOverrideQuery` mirrors `cms.normalize_query`; `toLowerCase` and
  `casefold` differ on a few letters such as `ß`.
- Discrepancies are still not tenant-scoped; that part of G634 stays open.
- Answer overrides are not tenant-scoped either: the table, lookup and upsert key on
  the normalized question alone, so one tenant's override answers, and can replace,
  another's. G637 is mitigated, not fixed, until overrides carry a tenant.

## Confirmation

Each test was watched failing before the fix; for G635 the server checks were
also disabled after they passed and the tests failed again:

- `App/backend/tests/test_discrepancy_and_precedence.py`: a rule without a statute, or
  without a topic, is 422 and writes nothing; every hotfix write is audited and the cache is retired; a
  chunk-only hotfix is audited and retires the cache; a hotfix that fails part-way
  still retires it; a hotfix that replaces a live override records `replaced` and
  both digests.
- `App/frontend/src/__tests__/app/admin/DiscrepanciesAdmin.test.tsx`: empty fields,
  nothing withdrawn, the statute and topic gates, and a change summary that promises
  a rule only when it will be saved.
- `App/backend/tests/test_officer_chat.py`: a supplied reply or closing translation
  that lost a figure is replaced; one that kept them is used; one changed amount of
  four, an added amount or an added rate fails the check; a Luganda rate without a
  percent sign passes; the server's own translation that changed an amount is not
  sent.
- `App/frontend/src/__tests__/components/TicketComposerTranslation.test.tsx`: no stale
  preview is sent, nor shown when the text changed while it was on its way; an
  unavailable translation is not called verified; a template with unfilled parts
  cannot be sent.
- `App/frontend/src/__tests__/lib/ticketMacros.test.ts`: no macro states a result
  without a part to fill in; an emptied `[[]]` counts as unfilled.
- `App/backend/tests/test_override_replacement_audit.py` (the replaced reply comes
  from the write, not a separate read that a concurrent save could make stale),
  `App/frontend/src/__tests__/app/adminOverrides.test.tsx`.
- `App/backend/tests/test_flag_override_durability.py`,
  `App/frontend/src/__tests__/app/adminFlags.test.tsx` (switching off and resetting
  an enabled safety control both ask, with the reach the page note states; the
  confirmation takes focus and gives it back).
- `App/backend/tests/test_audit_reviews.py` (including a decision stored before the
  history table, backfilled once, and a decision whose write fails leaving no
  history), `tests/agents/test_backend_shim.py` (`execute_all` commits all or none),
  `App/frontend/src/__tests__/components/ReviewControl.test.tsx` (earlier decisions
  stay readable on a decided item; the reopen confirmation takes focus and a cancel
  gives it back to Reopen).

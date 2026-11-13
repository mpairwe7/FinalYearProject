# ADR-0048: A figure, a date or a "verified" mark reaches the taxpayer only when it is true

## Status

Accepted, 2026-11-13. Phase 1 of the
[13 November intelligent UI integration audit](../plans/intelligent-ui-integration-audit-2026-11-13.md)
(G618–G624). It extends [ADR-0018](0018-intelligent-ui-completion-and-action-boundaries.md),
which bound actions to current, completed responses; this one is about what the
screen claims. The service owner asked for the audit's fixes to be applied phase
by phase in the session that made it.

## Context and Problem Statement

The backend's checks were sound and their verdicts did not reach the screen.
Seven places put a figure, a date or a mark of verification in front of the
taxpayer that the system had not earned:

- the offline page computed PAYE and VAT from bands that were not FY2026-27's,
  under "Statutory", with no check (G618);
- the chat counted an objection window from today and called it statutory (G619);
- a calculation using a rate the table marks unverified was badged "Official"
  (G620);
- the browser's own reasoning filter deleted the server's "could not check URA's
  website" and "stopped at the deadline" notices, and saved a deadline-stopped
  turn as complete (G621);
- a stream that abstained kept the passages it had rejected as the answer's
  sources and offered no officer (G622);
- "Well grounded" followed a raw score while the judge said the claim check had
  not run (G623);
- a document's OCR'd totals were "✓ Verified" and the server's extraction
  warnings were dropped (G624).

Research on reliance points the same way: people trust an answer more when it
carries citations or a confidence mark, whether or not the mark is earned, and
calibrated cues of doubt reduce over-reliance (Ding et al. 2025; Spatharioti et
al. 2025; Kim et al. 2024). OWASP LLM07 (2026) asks for verification signals
rather than confidence alone, and ASI09 for "unverified source" cues.

## Considered Options

For the offline page:

1. **Correct the bands and keep the calculator.** Rejected: an offline figure
   cannot be checked against the law in force, and the service worker keeps the
   page long after a rate changes; the next Finance Act would make it wrong again
   with nothing to say so.
2. **Generate the calculator from `tax/data/<FY>.json` at build time, labelled as
   an estimate, refusing outside its effective window.** Viable, but it is still
   an unchecked figure on a public revenue service, and it adds a build step for a
   page used when the taxpayer can instead phone the toll-free line.
3. **No figure offline; say why and give URA's lines** (chosen).

For badges:

1. **Keep score thresholds and add more of them.** Rejected: the score does not
   know the claim check failed or a rate is unverified.
2. **Read the judge's `confidence_band`, which the backend already sends on both
   transports** (chosen). One rule for every badge: below `high`, nothing says
   grounded or official. Table answers get the band from what their reply says.

## Decision Outcome

1. **No tax figure offline.** `public/offline.html` holds contacts and a
   translated sentence that rates and amounts are only given online, where they
   are checked. `public/sw.js` names its cache after the page's SHA-256
   (`OFFLINE_PAGE_REV`), so a changed page replaces an installed one;
   `offlinePortal.test.ts` fails if the two drift. `lib/offlineCalculators.ts` is
   removed. The offline banner says the same in the taxpayer's language.
2. **The chat dates only what it knows.** `detectDeadlineInMessage` returns a
   monthly deadline for the statutory monthly rule (a named monthly head and "the
   15th of the following/each month"), today when today is the 15th, and nothing
   else. The reminder shows on whole, current answers and says which period it is
   for.
3. **A table answer's doubt is in its text and its judge.** The calculator footer
   names each unverified figure. `_table_answer_judge` gives `medium`, with the
   reason "figure not confirmed against a gazetted schedule", to any calculator
   or rate reply carrying the unverified or provisional caveat
   (`reply_has_unconfirmed_figures`, which reads the markers this module itself
   writes).
4. **Badges follow the judge.** `applyTurnMeta` keeps
   `response_judge.confidence_band` as `confidenceBand` (a frame carrying
   `response_judge: null` withdraws it), and history restores it. Below `high`,
   the calculator badge reads "Rate not yet confirmed: check with URA" and the
   grounding badge "Verify with URA". All badge text is dictionary keys.
5. **The server's closing notices survive the browser.** `cleanResponse` keeps
   every paragraph from the final answer to the end; leaked reasoning is removed
   only ahead of it. A `deadline_exceeded` frame marks the turn incomplete.
6. **An abstention withdraws what it rejected.** The stream's abstention branch
   (which also serves an unreachable forced connector) sends `retrieval_mode:
   "abstained"` and empty `citations`, `sources` and `resources` in its grounding
   frame and clears them on the result, as REST does.
7. **Machine-read is said to be machine-read.** The document view shows the
   server's warnings in a status region, says "Figures add up" rather than
   "Verified", labels totals as "on the document", marks a discrepancy with a
   warning, and says the figures were read by machine and not checked by URA.

## Consequences

- A taxpayer with no connection gets no figure at all, only the lines to call. The
  page no longer depends on anyone remembering to update it after a Finance Act.
- Fewer reminders are offered: only the monthly rule. Objection and appeal windows
  need the date of service, which the chat does not ask for.
- A correct answer can now show "Verify with URA" where it showed "Well grounded"
  (the judge said `medium`, for example a score between 0.60 and 0.65, or the claim
  check did not run). That is the intended direction: a badge the system cannot
  stand behind is worse than none.
- A rate answer that names an unverified entry (royalty WHT, the used-clothing
  levy) loses its "Official" tick. The table's `unverified` list now decides what
  the taxpayer sees, so reconciling an entry restores the tick without code.
- A little leaked reasoning after an answer may now be shown; a disclosure is
  never deleted.
- The new vernacular strings are machine-assisted and unreviewed (G275).
- `reply_has_unconfirmed_figures` couples to the caveat wording in
  `calculator_router.py`. Both live in that module; a reworded caveat that drops
  the markers fails `test_figure_confirmation_signals.py`.

## Confirmation

Each test was watched failing before the fix. For G618, G620, G621 and G622 the
fix was also reverted after it passed, and the tests failed again:

- `App/frontend/src/__tests__/public/offlinePortal.test.ts`: no input, table,
  percentage, UGX amount or fiscal year on the page; the toll-free and WhatsApp
  lines match `URA_CONTACTS`; a distinct note in each of the five languages with
  `<html lang>` set; the stored app language is used and a corrupt one ignored; the
  service worker's cache name carries the page's hash.
- `App/frontend/src/__tests__/components/OfflineBanner.test.tsx`: no calculator
  promise; the taxpayer's language.
- `App/frontend/e2e/a11y.spec.ts`: the offline page has no serious or critical axe
  violation in light and dark schemes and in Acholi. Its first run found the dark
  scheme's brand-red phone numbers at 1.9:1, which predates this change; the page
  now uses a lighter text red in dark mode.
- `App/frontend/src/__tests__/lib/calendarEvents.test.ts`: the monthly rule; today
  on the 15th; no objection window; no "15th" without a monthly head and rule.
- `App/backend/tests/test_figure_confirmation_signals.py`: the used-clothing levy is
  named in the reply; calculator and rate turns with an unverified figure are
  `medium`; confirmed ones stay `high`.
- `App/frontend/src/__tests__/store/cleanResponse.test.ts`: both notices survive
  two openers; reasoning ahead of the answer is still removed.
- `App/backend/tests/test_deterministic_streaming_parity.py`
  (`test_an_abstention_decided_mid_stream_withdraws_the_rejected_sources`).
- `App/frontend/src/__tests__/lib/turnMeta.test.ts`: the band is kept, withdrawn by
  `response_judge: null`, and a frame without a judge leaves it; an abstaining
  grounding frame clears early sources.
- `App/frontend/src/__tests__/components/ChatMessage.test.tsx`: no reminder on an
  interrupted, restored or objection answer; the reminder and the trust badge in
  the reader's language; "Official" only at `high`; no "Well grounded" at `medium`.
- `App/frontend/src/__tests__/components/DocumentInspectionModal.test.tsx`: warnings
  shown; no "✓ Verified"; the machine-read note; a warning icon on a discrepancy.
- `App/frontend/e2e/chat-flow.spec.ts`: a deadline-stopped stream is incomplete with
  no actions; a failed live check is still disclosed after "I can help with that.";
  an abstaining stream drops its early sources and offers an officer.

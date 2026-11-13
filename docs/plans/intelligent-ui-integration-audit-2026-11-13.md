# Intelligent UI integration audit — 13 November 2026

Status: phase 1 implemented (PR 727); phase 2 on `fix/intelligent-ui-p2-human-handoff-2026-11`; phase 3 on `fix/intelligent-ui-p3-staff-controls-2026-11`; phase 4 on `fix/intelligent-ui-p4-officer-evidence-2026-11`; phase 5 on `fix/intelligent-ui-p5-voice-and-capture-2026-11`; phase 6 open.
Baseline: `dev` at `882a99b5`. Decisions: [ADR-0048](../adr/0048-figures-and-disclosures-the-taxpayer-sees.md) (phase 1), [ADR-0049](../adr/0049-a-promise-of-a-human-is-kept.md) (phase 2), [ADR-0050](../adr/0050-staff-controls-say-what-they-do.md) (phase 3), [ADR-0051](../adr/0051-officers-see-what-the-ai-did.md) (phase 4), [ADR-0052](../adr/0052-voice-says-what-the-screen-says.md) (phase 5).
Follows the [30 October audit](intelligent-ui-integration-audit-2026-10.md) (UI-01 to UI-13, ADR-0018).

That audit covered the chat's REST/SSE contract, action boundaries and the
portal confirmation card. This one asks a wider question across every surface
where the AI meets a person, taxpayer or staff: **does what the system knows,
decides or fails at reach the person correctly?** A gap is a signal the backend
emits that the UI drops, mislabels or overstates; a control that lets a person
act on stale, incomplete, unverified or unauthorised output; a failure the UI
hides or shows as success; or an AI-specific interaction that is inaccessible,
untranslated or misleading.

It is a code and test review. Nothing was run against the Space, a real
microphone, a screen reader or a first-language reader, and it is not a legal
compliance opinion.

## How the review was done

Four read-only passes, each given the same brief (the [audit prompt](#appendix-the-audit-prompt)) and one surface:
trust and disclosure signals on the chat's answer path; the taxpayer's agentic
actions, handoff and stored history; the staff console where AI output meets a
human decision; and the chat's voice, capture, offline, accessibility and
language paths. A fifth pass verified the reference basis from primary sources.

Each pass traced a signal from where the backend emits it, through the transport
(REST JSON, SSE frames, history restore), the parser (`turnMeta.ts`,
`chatStream.ts`, the store) to what is rendered, announced and translated, and
tested each link against: absent, null, malformed, withdrawn, late, interrupted,
restored, a locale other than English, an officer holding the chat, offline, and
a screen reader. A finding was kept when it could be traced end to end; most were
reproduced with a scratch probe outside the repository. The lead reviewer
re-read the code for every Critical and High finding before accepting it.

"Fixed" below means implemented with a regression test that was watched failing
first, and, for the controls that matter most, a test that fails again when the
fix is reverted (G618, G620, G621, G622). Deployment is a separate step.

The pre-existing state at the baseline: frontend unit tests 96 files / 921 tests
passing; `bun audit` 28 findings (7 high, 17 moderate, 4 low), up from 25 on
30 October, all through development and test tool chains (G276).

## Reference basis

Sources were opened on 13 November 2026. "Fetched" means the primary text was
read; the rest are marked. These are engineering references, not a claim that
foreign rules bind URA: the EU instruments are a benchmark, and Uganda's Data
Protection and Privacy Act 2019 is the applicable law.

| Reference | Version | What it is used for here |
| --- | --- | --- |
| [EU AI Act](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=OJ:L_202401689) Art. 50(1), 50(5) | Reg. 2024/1689; applies from 2 Aug 2026 (fetched) | Tell a person they are dealing with an AI, clearly, at the first interaction, accessibly. G647 (voice mode). |
| [Commission guidelines on Article 50](https://ai-act-service-desk.ec.europa.eu/sites/default/files/2026-07/guidelines_on_the_implementation_of_the_transparency_obligations_for_certain_ai_systems_under_article_50_of_the_ai_act_bzptwqhk0ikg1dtlddap41psfy_131215.pdf) | C(2026) 5054, 20 Jul 2026, non-binding (fetched) | Text chat: a plain label near the input or output. Voice: a spoken disclosure at the start, repeated after an interruption or a change of role; earcons alone are not enough. Reminders where advice is financial or a complaint. |
| [Digital Omnibus on AI](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX%3A32026R1744) | Reg. 2026/1744, OJ 24 Jul 2026 (fetched) | Art. 50(1)–(5) and Art. 14 unchanged; high-risk duties moved to 2 Dec 2027. |
| EU AI Act Art. 14(4)(a)–(e) | as above | Oversight benchmark for the staff console (understand limits, automation bias, interpret output, override, stop). Recital 59 places tax administration outside the high-risk law-enforcement use, so this is not a legal duty here. Phases 3 and 4. |
| [WCAG 2.2](https://www.w3.org/TR/WCAG22/) SC 4.1.3, 2.2.2, 3.2.6, 3.3.4 | Recommendation, 12 Dec 2024 (fetched); also ISO/IEC 40500:2025 | Status messages without moving focus; consistent help; error prevention on financial data. |
| [WAI-ARIA 1.2](https://www.w3.org/TR/wai-aria-1.2/) `log`, `aria-busy` | Recommendation, 6 Jun 2023 (fetched) | A chat transcript is a `log`; `aria-busy` while a reply streams. |
| [W3C COGA voice and conversational interfaces](https://www.w3.org/TR/coga-voice/) | Group Note draft, 5 Feb 2026 (fetched) | A human must be reachable; an error must not end the interaction; a visual log of what was said. Phases 2 and 5. |
| ETSI EN 301 549 V4.1.1 | 2 Sep 2026; adopts WCAG 2.2; not yet cited in the OJEU (version verified, text gated) | Accessibility target. |
| [OWASP Top 10 for LLM Applications 2026](https://github.com/GenAI-Security-Project/GenAI-LLM-Top10) | 4 Aug 2026, renumbered (fetched) | LLM07 Misinformation: "distinguish verified facts from assumptions", "verification signals, not just confidence". LLM10 Improper Output Handling. LLM03 Excessive Agency: approval for high-impact actions. Phases 1, 3. |
| [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/) ASI09 | Dec 2025 (fetched) | Human–agent trust: "low-certainty" or "unverified source" cues; separate preview from effect; plain-language risk summaries, not model rationales. Phases 1, 3, 4. |
| [NIST AI 600-1](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf) MP-5.1-003, MS-2.8-004, GV-3.2-004 | Jul 2024; no 2026 revision (fetched) | Disclose GenAI use; test that users understand content lineage; feedback with recourse. |
| [UK AI Playbook](https://www.gov.uk/government/publications/ai-playbook-for-the-uk-government/artificial-intelligence-playbook-for-the-uk-government-html), principles 4 and 7; [GOV.UK Service Manual, using AI in services](https://www.gov.uk/service-manual/technology/using-artificial-intelligence-ai-in-services) | 10 Feb 2025; 23 Sep 2025, revised 15 Oct 2026 (fetched) | Clearly identify an automated response; users know answers might not be accurate and how to reach a person; a way to report issues that prompts human review. |
| [US OMB M-25-21](https://www.whitehouse.gov/wp-content/uploads/2025/02/M-25-21-Accelerating-Federal-Use-of-AI-through-Innovation-Governance-and-Public-Trust.pdf) §4(b)(iv)–(vii); M-26-04 | 3 Apr 2025, no successor; 11 Dec 2025 (fetched) | Operators trained to interpret output; human review and appeal; LLMs "acknowledge uncertainty where reliable information is incomplete". |
| Kim et al., FAccT 2024 ([doi](https://doi.org/10.1145/3630106.3658941)); Spatharioti et al., CHI 2025 ([doi](https://doi.org/10.1145/3706598.3714082)); Steyvers et al., Nat. Mach. Intell. 2025 ([doi](https://doi.org/10.1038/s42256-024-00976-7)) | peer-reviewed (fetched) | Expressed uncertainty and confidence highlighting reduce over-reliance; longer explanations raise confidence without raising accuracy. Basis for badges that follow the judge rather than a raw score (G620, G623). |
| Ding et al., AAAI 2025 ([doi](https://doi.org/10.1609/aaai.v39i22.34550)); Wu et al., Nat. Commun. 2025 ([doi](https://doi.org/10.1038/s41467-025-58551-6)) | peer-reviewed (fetched) | Citations raise trust even when random; many cited statements are unsupported. A source list is not a verification mark (G622, G651). |
| Vaccaro et al., Nat. Hum. Behav. 2024 ([doi](https://doi.org/10.1038/s41562-024-02024-1)) | meta-analysis (fetched) | Human–AI pairs often do worse than the better of the two; oversight UI must show the AI's evidence and doubt (phase 4). |
| [Uganda Data Protection and Privacy Act 2019](https://bills.parliament.ug/attachments/Data%20Protection%20and%20Privacy%20Act%202019.pdf) s.13, s.27 | Act 9 of 2019 (fetched) | Notice before collection; a person may object to a decision based solely on automated processing (G654, phase 6). No Uganda AI-specific UI rule exists; the national emerging-technologies strategy was a draft in March 2026. |
| [MCP Apps](https://github.com/modelcontextprotocol/ext-apps) (2026-01-26, stable); [A2UI v1.0](https://a2ui.org/specification/v1.0-a2ui/) | fetched | Agent-supplied UI renders only allow-listed components or in a sandboxed iframe under a strict CSP. The chat renders server cards from a fixed component set and no model HTML; nothing to change. |

Not verified, and not relied on: the text of Canada's Directive on Automated
Decision-Making (the site refused automated fetches), EN 301 549 clause text, the
full Code of Practice on AI-generated content (it does not cover Art. 50(1)
chatbot disclosure in any case), and Uganda DPPA Regulations 2021.

## Phase 1 — a figure, a date or a "verified" mark is shown only when it is true (this change)

| Gap | Severity | What happened | What changed |
| --- | --- | --- | --- |
| G618 | Critical | The offline page (`public/offline.html`, served by the service worker when there is no connection) had a "Statutory" PAYE and VAT calculator for FY2026/27 whose bands were not the year's: 10% on 335,001–410,000 where `tax/data/FY2026-27.json` says 20%, a flat 20% above where it says 25% then 30%, and a VAT threshold of UGX 150M where it is 300M. UGX 1,000,000 a month gave 125,500 PAYE; the server's calculator gives 188,250. No estimate label, no check, and the page stayed in the worker's cache until its name changed. `lib/offlineCalculators.ts` carried the same bands; the runbook described a third set. | The page gives no rate, amount or calculator: it says why (figures are given online, where they are checked) and lists URA's lines, in all five languages, opening in the one the taxpayer chose. The service worker's cache is named after the page's SHA-256, so a corrected page replaces an installed one; a test holds the two together. `offlineCalculators.ts` is deleted; the banner no longer promises a calculator and is translated. The page is now in the axe scan, which found its dark-mode phone numbers at 1.9:1 contrast (pre-existing); they now pass. Tests: `public/offlinePortal.test.ts`, `OfflineBanner.test.tsx`, `e2e/a11y.spec.ts`. |
| G619 | High | `lib/calendarEvents.ts` invented deadline dates from the answer's words: "45 days" near "objection" became a "45-Day Statutory Objection Deadline" counted from today (it runs from service of the assessment, which the chat does not know); any "15th" near "deadline" became a monthly return; on the 15th itself it offered next month. Shown on interrupted and restored answers, in English. | Only the statutory monthly rule is recognised (a monthly tax head and "the 15th of the following/each month"); the date is today on the 15th; nothing else is dated. The reminder appears on whole, current answers only, says which period it is for, and is translated. Tests: `calendarEvents.test.ts`, `ChatMessage.test.tsx`. |
| G620 | High | A calculation or rate answer using an entry the FY2026-27 table marks `unverified` (the used-clothing environmental levy, royalty WHT and seven others) carried a green "Official Statutory Calculator" badge. The calculator's reply ended "Figures use the official URA FY2026-27 rate table." with no caveat; the tool payload's `verification_warning` stopped there. | The calculator footer names each unconfirmed figure. A table answer whose reply carries an unconfirmed or provisional caveat gets judge band `medium` with the reason "figure not confirmed against a gazetted schedule" (`_table_answer_judge`). The chat reads the band: "Rate not yet confirmed: check with URA" instead of "Official". Tests: `test_figure_confirmation_signals.py`, `ChatMessage.test.tsx`. |
| G621 | High | The browser's reasoning filter (`cleanResponse`) dropped trailing paragraphs that looked like reasoning when the answer opened with one ("I can help with that."). "…so this may not include recent changes" (a failed live check) and "This is taking longer than it should…" (the turn deadline) both matched, so the AGENTS.md rule "a failed live check is disclosed rather than hidden" failed in the browser, and history kept the stripped text. A turn stopped at its deadline was saved as a complete answer with live action chips: no client read `deadline_exceeded`. | Everything from the final answer to the end is kept; leaked reasoning is still removed ahead of the answer. `deadline_exceeded` marks the turn incomplete, which withholds actions and badges and announces it. Tests: `store/cleanResponse.test.ts`, `e2e/chat-flow.spec.ts` (two cases). |
| G622 | High | When the stream abstained after retrieval, the `metadata` frame had already sent the passages, and the `grounding` frame neither withdrew them nor named the mode. The chat kept a "Sources" pill of passages the system had just rejected beside "I'd rather connect you with the right people than guess", and, reading the mode as `hybrid`, offered no officer. REST was correct. The same branch serves a forced connector that could not be reached. | The grounding frame carries `retrieval_mode: "abstained"` and empty `citations`, `sources` and `resources`, and the turn's result is cleared to match. Tests: `test_deterministic_streaming_parity.py`, `turnMeta.test.ts`, `e2e/chat-flow.spec.ts`. |
| G623 | Medium | "Well grounded" was chosen from the faithfulness score alone. When the claim checker was down the judge lowered its band to `medium` and the reply said the check had not run, but the score was unchanged, so a green "Well grounded" sat beside "I could not finish the check". No frontend file read `response_judge`. | The turn keeps the judge's `confidence_band` (both transports and history); below `high`, no badge says grounded or official. The badge strings are dictionary keys in five languages. Web evidence earning the badge is G651. Tests: `turnMeta.test.ts`, `ChatMessage.test.tsx`. |
| G624 | High | The document inspection view showed "✓ Verified" and "Total Payable" over figures read by OCR or regex, ticked every note (including "Arithmetic discrepancy detected"), and dropped the server's extraction warnings ("Only the first 40 of N pages were processed", "OCR failed while processing page 3") though the page stored them. | The warnings are shown in a status region. The badge reads "Figures add up", the totals are "on the document", a discrepancy or partial note gets a warning icon, and a note says the figures were read by machine and not checked by URA. Strings are dictionary keys. Tests: `DocumentInspectionModal.test.tsx`. |

Not changed by phase 1: the vernacular strings added here (offline page, badges,
reminder, document labels) are machine-assisted and have not been reviewed by
first-language speakers; that stays G275. The pre-existing browser test "language
switch toggles to Luganda" expected the English label after switching to
Luganda; it now checks the language name, since the localized label is correct.

## Phase 2 — a promise of a human is kept (implemented, ADR-0049)

All nine were implemented with a test watched failing first; G630 is mitigated (the notice is honest; the assistant still does not see the officer thread). The fix column is what was planned; ADR-0049 records where it differs: G629 marks open cases in the rail rather than polling them in the background, and G632 is fixed in the browser (the card is hidden on an escalated turn) without withdrawing `objective_hypothesis` server-side.

| Gap | Severity | Finding | Fix |
| --- | --- | --- | --- |
| G625 | High | When the assistant opens a ticket itself (judge or supervisor escalation, the "Talk to an officer" chip, a crisis reply), the chat never follows it: `applyTurnMeta` does not read `ticket_id`, the only caller of `setActiveTicketId` is the HumanHandoff button, and `needsHuman` has no `crisis_support` case. The officer writes into a chat the taxpayer never sees. | Follow `ticket_id` from the current response; offer the officer on crisis and well-being replies. |
| G626 | High | A case that is gone (404, for example after the Space loses its database, G616) leaves the taxpayer "with the officer": polling stops, every message fails with "Try again", the AI never answers again in that conversation, and the state survives a reload. | On 404/422, end the officer chat with a notice and hand back to the assistant. |
| G627 | Medium | The supervisor's ESCALATE route says "I've flagged it for a URA officer to review" when no ticket exists (queue off or a failed write), against the AGENTS.md `ticket_queue` rule. | Without a ticket, reply with the queue-off message `POST /v1/escalate` already uses. |
| G628 | Medium | The Support Case room says "A URA Officer is Reviewing Your Case … has been assigned" whatever the status (open, closed, failed to load, off hours); `status_label` is never rendered; all English. | Status-specific translated copy, a closed state, officer hours, no reviewing card on a load failure. |
| G629 | Medium | A case is followed only while its conversation is open; "New chat" silently stops the live officer chat, and the rail shows no open case. | Keep following open cases, mark them in the rail, confirm before leaving one. |
| G630 | Medium | After an officer hands back, the AI cannot see what was said in the officer chat (`get_recent_turns` reads only `conversations`), while the hand-back notice implies continuity. | Merge the officer thread into the context, or say plainly that the assistant has not seen it. |
| G631 | Medium | An officer's message is machine-translated for the taxpayer and shown as the officer's own words, with no label and no original; the Case Room and the staff thread do label it. | Return `translated` and the original; label it and offer the original. |
| G632 | Low | A turn escalated late keeps the inferred-goal card's "Start guided steps", which the backend withdrew from the actions. | Withdraw `objective_hypothesis` on escalation; hide the card when escalated. |
| G633 | Low | Resuming a portal task after sign-in posts to the AI while an officer holds the chat; the screenshot sign-in link is not gated. | Defer the continuation while an officer holds the chat; gate the link. |

## Phase 3 — staff controls that change what taxpayers are told (implemented, ADR-0050)

All six were implemented with a test watched failing first. ADR-0050 records where the fix differs from the plan: G634 and G637 are mitigated, since discrepancies and answer overrides are still not tenant-scoped; G635 also refuses a stale translation on the server, whichever client sends it; G636 keeps the result shortcuts as `[[…]]` templates rather than removing them. While testing G635 the reply field's Preview button was found to take the label's text as its accessible name (it sat inside the `<label>`); the field is now a `div`.

| Gap | Severity | Finding | Fix |
| --- | --- | --- | --- |
| G634 | Critical | "Verify & Hotfix" on a taxpayer's discrepancy report pre-fills the precedence rule — which the model is told "STRICTLY SUPERSEDES" retrieved law — with the taxpayer's own unverified correction, ticks "withdraw the cited sources" by default, and writes a stock note; two clicks change what the AI tells every taxpayer. The ledger records only the report and override ids, not the rule or the withdrawn sources; discrepancies are not tenant-scoped. | Empty fields, unticked withdrawal, a required statute reference, a confirmation that shows the rule as the model will see it and each source withdrawn; audit events for the precedence, the tombstones and the override. |
| G635 | High | The officer's translation preview is sent as `officer_reply_localized` after the officer edits the English, so the taxpayer gets the old figure in their language; "Figures & statutory amounts verified" shows when translation was unavailable and the English came back unchanged. | Clear the preview on every edit or failure; badge only a real check; let the server localize through `localize_reply`. |
| G636 | High | One-click canned replies tell the taxpayer an invoice was "audited", "reconciles with URA records", "the EFRIS fiscal signature is authentic", WHT "reconciled against URA ledgers", customs "cleared" — checks the workbench never shows — and ask for an attachment in a chat where attachments are off. | Remove the verification claims or gate them on a recorded check; reword the document request. |
| G637 | High | The answer-overrides console says a match is "character for character" and served "word for word", while the server matches a normalized, rewritten and machine-translated question, translates the reply, applies it to every tenant, and silently replaces an existing override. | Show the normalized key and any override it replaces, with a diff and confirmation; correct the copy; audit before and after. |
| G638 | Medium | The flags console calls overrides durable where the Space loses them on restart (G616), labels a stored override "this replica", and switches `stream_figure_hold` (the FigureReleaseGate) with one click. | Report durability from where the store lives; label reach correctly; confirm figure, audit and guard flags. |
| G639 | Medium | "Reopen" in Needs review deletes an auditor's finding and note in one click with no confirmation; the ledger keeps only `note_chars`. | Append-only review history; confirm Reopen; show prior decisions. |

## Phase 4 — officers see what the AI did (implemented, ADR-0051)

All five were implemented, each with a test watched failing first. G641 does more than the plan: a re-escalation also adds its reason, replaces the AI evidence with the latest escalation's, gets the escalation assessment's priority, and is written in one transaction that cannot lose a taxpayer's follow-up; a failed refresh is counted and flagged on the handoff.

| Gap | Severity | Finding | Fix |
| --- | --- | --- | --- |
| G640 | High | The officer's case view shows the AI's reply as "Assistant" text only. The API returns `response_judge` (claim check unavailable, a figure withheld as contradicted, a revision) and `handoff.sources_reviewed`; none is rendered, and the escalated turn's sources are stored empty. | An "AI evidence" block: sources, claim check not run, figure withheld, judge revision (labelled a model's opinion). |
| G641 | High | AI answers given after escalation never reach the ticket, and a second escalation reuses the ticket without raising its priority or reasons, so the officer works from the first snapshot. | Append later turns and raise priority and reasons on reuse; show "answers since escalation". |
| G642 | Medium | The machine translation of the taxpayer's words is shown as "Taxpayer [EN]" with no machine label. | Label it machine translation and keep the original visible. |
| G643 | Low | An auditor on `/admin/tickets` is offered "a — assign to me", which the API refuses silently. | Gate it on `isLiveOfficer`. |
| G644 | Low | "Found helpful by taxpayers" shows 0% "Below target" when nothing was rated or the request failed. | Show "no ratings yet" and N. |

## Phase 5 — voice and capture (implemented, ADR-0052)

All five were implemented with a test watched failing first. G647's spoken disclosure is the first sentence of the session's first answer rather than a notice before the microphone opens (ADR-0052 says why). The browser test "dictation still works with no browser Speech API" failed once in three runs here, with no change to dictation; it is recorder timing and is noted for the QA owner.

| Gap | Severity | Finding | Fix |
| --- | --- | --- | --- |
| G645 | High | A read-aloud that skips a sentence or is cut off counts as complete, and voice mode reopens the microphone; nothing tells the listener part was missing. | `complete` on the spoken outcome; on a partial reading, say so and do not reopen the microphone. |
| G646 | High | Voice mode never speaks the answer's caveats ("Verify with URA", "human review recommended") or a failed answer; on a dropped stream the dock says replies are read aloud. | Speak a translated caveat and the incomplete or failed notice. |
| G647 | Medium | Voice mode replaces the composer, whose footer is the only always-visible "AI" notice (EU AI Act Art. 50(1); Commission guidelines: spoken at the start). The manifest marks Art. 50(1) implemented with no `verified_by`. | A dock notice and a spoken disclosure when voice mode starts; `verified_by` in the manifest. |
| G648 | Medium | A spoken question still being transcribed is sent into whichever conversation is open when it finishes (ADR-0018 binding). | Bind the queued voice turn to its conversation; drop it with a notice on a switch. |
| G649 | Medium | "Take a photo" on desktop calls `getUserMedia` under `Permissions-Policy: camera=()`, blames the browser, and would lose the photo (`atob` of a bare base64 string). | Use the capture file input everywhere, or fix the policy and the data URL. |

## Phase 6 — resilience, provenance, contestability, language

| Gap | Severity | Finding | Planned fix |
| --- | --- | --- | --- |
| G650 | Medium | The open remainder of G274 (UI-08): restored history casts `citations`, `workflow` and `resources` without the validators, the storage version is written but never read, nested workflow fields are unchecked, and one throwing card replaces the whole chat with `error.tsx` (reload throws again). | Restore through the same validators; check the version; a per-message error boundary. |
| G651 | Medium | Web citations lose their provenance: adviser commentary and search summaries look like corpus references, the "effective date" can be a fiscal year guessed from snippet text, and web-grounded answers can earn "Well grounded". | Carry authority, evidence and staleness on `Citation`; label them; no badge on web evidence; no regex fiscal year. |
| G652 | Low | The REST fallback drops `connectors_used`, so the "Looked up in X · Demo" note is lost. | Map it in `applyTurnMeta`. |
| G653 | Medium | Discrepancy detection runs only on REST, so the web chat never logs a taxpayer's correction; a failed feedback-comment save is silent. | Run it on both transports; show a save failure. |
| G654 | Low | Document analyses (TINs, NINs) are written to browser storage and dropped from the view on reload; a quota failure on save is silent. | Strip analyses before persisting; say so in the view; report a failed save. |
| G655 | Medium | 195 hard-coded English strings on mounted taxpayer surfaces, including the analytics consent notice, the live-region announcements ("URA response ready."), the Support Case room and screen-reader role names; `ACCESSIBILITY_CONFORMANCE.md` claimed complete key coverage for lg and sw, which was untrue (52 keys missing each). | Dictionary keys for every taxpayer string; a scan test. |

## Carried forward

- G275 (UI-10/11): staging journeys, distributed execution, reviewed translations
  and manual screen-reader acceptance. The vernacular strings added by every phase
  join the review list.
- G276 (UI-12/13): `bun audit` now reports 28 findings (7 high, 17 moderate,
  4 low). Every high finding is reached through `eslint` (its MCP SDK's `express`,
  `body-parser` and `zod`), `launch-editor`, or `jsdom`'s `undici`; none ships in
  the browser bundle. A dependency change with a frozen install and full re-test,
  not a forced override, is still the fix.

## Appendix: the audit prompt

The brief each pass was given, with the scope line changed per surface. It is
kept here so the next audit starts from it.

```text
ROLE: Senior QA engineer, systems architect and AI-assurance auditor reviewing
<SURFACE> of the URA tax assistant: a high-stakes government service where a
wrong figure, a hidden failure or an unearned "verified" mark can cause a
taxpayer financial or legal harm. Read AGENTS.md and the nested AGENTS.md first;
their invariants are requirements, and a violation is a finding.

DEFINITION: an intelligent-UI integration gap is where what the AI system knows,
decides or fails at does not reach the person correctly: a signal the backend
emits that the UI drops, mislabels or overstates; a control that lets a person
act on stale, incomplete, unverified or unauthorised output; a failure hidden or
shown as success; an AI-specific interaction that is inaccessible, untranslated
or misleading.

METHOD:
1. List the signals and controls in scope. Grep the backend for disclosure and
   status keys and the frontend for their readers; a key with no reader is a lead.
2. Trace each: emitter (file:line) -> transport (REST, SSE frame, history
   restore, WebSocket) -> parser -> render -> announcement -> translation.
   Include the filters in between: a cleaning or reasoning-stripping step can
   delete what the server said.
3. Test each link against: absent, null, malformed, withdrawn by a later frame,
   late, interrupted, restored from history, a locale other than English, an
   officer holding the conversation, offline, voice instead of screen, a screen
   reader.
4. Check reachability: say whether a component is mounted; an unmounted one is
   not a live gap.
5. Before reporting, grep the gap register and plans: skip what is closed and
   still fixed, report a REGRESSION if the fix is gone.

RULES: read-only on the repository; scratch probes outside it. Evidence or it is
not a finding: file:line on both ends and a concrete scenario (state -> what the
person sees or can do -> harm). Mark CONFIRMED (traced end to end) or PLAUSIBLE
(name the missing link). At most 12 findings; no generic best practice.

OUTPUT: per finding — title; severity with a one-line impact; CONFIRMED or
PLAUSIBLE; evidence; scenario; minimal fix; the regression test that fails
before the fix (file and assertion). Then "checked and fine", naming what was
traced and found correct, so coverage is visible.
```

A separate pass verifies the reference basis: primary sources only, each with
its version and date and whether the text was actually read, and a list of what
could not be verified.

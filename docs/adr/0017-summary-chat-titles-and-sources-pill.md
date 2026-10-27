# ADR-0017: Summary Chat Titles from the Opening Exchange, and One Sources Pill per Answer

- **Date:** 2026-10-27
- **Owner:** Cloake Tech Limited — Frontend & Chat UX
- **Changes:** how a conversation is named in the sidebar (`POST /v1/chat/title`, new) and how an answer shows the forms and passages it rests on (`SourcesPill`, replacing `ResourceCards` and the citations disclosure).

## Status

Accepted

## Context and Problem Statement

The owner reported two problems on the live stack (`dev`, 2026-10-27).

1. **Answers ended in a second answer's worth of chrome.** Most replies closed with an "Official URA Forms, Templates & Sources" card grid (`ResourceCards`, fed by `verified_resources.resources_for_turn`) and, separately, a "Sources (N)" disclosure for the quoted passages. The grid was often taller than the answer, sat between the reader and the follow-up chips, and in dark mode its card titles were near-invisible (light grey on white).
2. **Chat titles were the first message cut at 60 characters** (`deriveTitle`). They read as fragments ("Verify TIN <ten digits> for my tax ...") and put a TIN in a list anyone near the screen can read.

The assistants people use name a conversation from what it is about. ChatGPT, Claude and Gemini each make one small model call after the first exchange ("a 3–6 word title, sentence case, no quotes"), show the result in the sidebar, and let a rename win. Claude shows an answer's sources as a compact control that lists them on demand rather than inline cards.

## Decision Outcome

1. **One pill, at the end of the answer (`SourcesPill.tsx`).** The turn's resources (or a guided step's `workflow.resources`) and its citations are merged and de-duplicated into one list.
   - The pill sits in the answer's last row, next to the grounding badge: `[✓ Well grounded] [◔◔ Sources 3]`.
   - The list opens on mouse hover (with short delays and a bridge over the gap), on a tap, or on Enter/Space. Esc, a click elsewhere or focus leaving close it. It is a disclosure (`aria-expanded`/`aria-controls`), rendered only while open.
   - It opens upward unless the transcript has no room above, never past the composer dock or a phone's right edge.
   - Rows are grouped "Forms & services" and "References". Only `http(s)` links are rendered as links.
   - `ResourceCards`, its styles, and the `<details class="citations">` block are deleted. `WorkflowStepper` no longer renders cards; the step's forms are in the same pill.
2. **A model-named title after the first answer (`POST /v1/chat/title`).**
   - The client (`hooks/useAutoTitle.ts`) asks once per conversation per page load, after an answer has landed, with the first question and the first answer. It watches the store, so typed, spoken and attachment turns are covered, and older conversations are named when reopened.
   - The server (`app/chat_title.py`) refuses input `InputGuard` blocks, strips TINs, NINs and emails (`redact_pii_text`) and masks digit runs (phone and account numbers) before the model sees anything.
   - `llm.summarize_title` is a minimal, ungrounded prompt against the model already loaded, shaped like `classify_choice` (vLLM: `temperature=0.2`, `max_tokens=24`).
   - The reply is cut to one line of at most 6 words / 60 characters, stripped of quotes, `Title:` prefixes and trailing punctuation, and **rejected** if it reads as a refusal or still carries a digit run or PII. A rejected or missing title is returned as `null`.
   - A non-English conversation's title is generated in English and localized through `localize_reply`, the one place answers are localized; a failed translation keeps the English.
3. **A provisional title until then (`lib/chatTitle.ts`).** The first message, boiled down: greetings and openers ("can you please", "I want to", "we are") are dropped, identifiers removed, the first clause kept, capped at 6 words. It replaces the 60-character cut everywhere (`deriveTitle`, the header strip) and is the title that stays when the model is off.
4. **Who wins.** A typed title (`titleCustom`) always wins, and a summary arriving after a rename is dropped. A model title (`titleGenerated`) is kept across saves; `saveCurrentSession` no longer re-derives it.

- *Rejected: generate the title inside `/v1/chat/stream`.* It would delay or compete with the first answer on the same GPU, and every transport would need it.
- *Rejected: client-only heuristic titles.* They cannot summarise ("We are hiring a foreign technical specialist, which taxes apply?" is a topic, not a phrase to truncate). The heuristic is kept as the provisional and offline fallback.

## Consequences

- **Positive:**
  - An answer's sources cost one 26px pill instead of a card grid, in both themes.
  - The sidebar reads like a list of topics, and no TIN, NIN or phone number reaches it from either path.
  - A model outage costs nothing visible beyond the provisional title.
- **Negative / Trade-offs:**
  - One extra model call per conversation (≤ 24 output tokens), counted as `chat_title_total{source}`.
  - A form the taxpayer needs is now one tap away instead of on screen. Answers that tell the taxpayer to use a form still name it in the text.
  - Resource checklists ("Checklist before submitting") are no longer shown under answers.
  - The new lg/sw/nyn/ach strings are drafts; nyn and ach group headings fall back to English.
- **Found on the way:** `redact_pii_text` does not redact phone numbers, so the title path masks digit runs itself. Changing the shared redactor is left to its owner.

## Confirmation

Validated by:
- `App/frontend/src/__tests__/components/SourcesPill.test.tsx`: merge and de-duplication, hover and touch, click toggle, Esc, outside click, safe links, locale.
- `App/frontend/src/__tests__/components/ChatMessage.test.tsx`: the pill in the answer's last row, forms and a workflow step's forms included, no card grid.
- `App/frontend/src/__tests__/lib/chatTitle.test.ts`, `App/frontend/src/__tests__/hooks/useAutoTitle.test.tsx` and `App/frontend/src/__tests__/store/useChatStore.test.ts` (conversation titles).
- `App/backend/tests/test_chat_title.py`: the cleaner, nothing identifying reaches the model, guard-blocked input, rejected replies, localization.
- `tests/test_all_endpoints_e2e.py`: `POST /v1/chat/title` in the manifest, happy path and null title.
- `tests/test_monorepo_architecture_governance.py` (MADR sections and ADR registry).

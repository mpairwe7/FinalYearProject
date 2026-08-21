# UI Migration Notes — ui-lab → App/frontend (chatv2)

Executed 2026-07-29 against `MIGRATION-PLAN.md` (approved with defaults D1–D4).
Branch: `dev`. Six commits, format `ui: <what changed>`. No new dependencies;
`bun.lock` untouched; `globals.css` untouched (the new layer loads via one
import line in `layout.tsx`).

## What changed

| Commit | Contents |
|---|---|
| `ui: add UI migration plan` | MIGRATION-PLAN.md |
| `ui: add chatv2 scoped style layer` | `src/styles/chatv2/*` (6 files), 1 import in layout.tsx. All rules scoped under `.chatv2`; `:root` tokens shadowed inside the chat subtree only |
| `ui: port composer…` | ChatInput.tsx rewritten (two-row composer; Voice/Narrate/language/attach in the toolbar), LanguageMenu.tsx (new), 4 icons appended, composerProps extended in page.tsx, chat-flow language spec updated |
| `ui: confirm-gate destructive actions` | ConfirmDialog.tsx (new); page wraps `deleteSession`/`reset` in a confirm step — the confirmed handlers are byte-identical |
| `ui: port header, landing, and page shell` | ChatHeader.tsx (new), input-first landing, desktop rail collapse, voice overlays re-parented outside `.chatv2` |
| `ui: restyle transcript…` | Grounding badge moved out of the citations summary; "Searching the URA knowledge base…" + skeleton replaces the bare "assistant •••" row; streaming caret |

**Behavior deliberately added (approved):** confirmation dialogs before delete
conversation / clear chat (P9). Everything else is presentation.

**Behavior deliberately NOT added (per "no new features", D1/D2):**
- No Stop-generation button; send stays disabled while streaming. The textarea
  is now *enabled* during streaming (typing only writes the draft; Enter no-ops
  via the existing `isLoading` guard in `sendMessage`).
- No error-card Retry; failed replies render as plain assistant text exactly
  as before.

**Removed rather than left mocked (per the no-invented-sources rule):**
- The 6 extra ui-lab languages (Swahili, Runyankole, Acholi, Ateso, Lusoga,
  Lugbara). The picker ships with the two real locales (en, lg). Adding a
  language later = one entry in `LOCALE_OPTIONS` (page.tsx) + a native name in
  `LanguageMenu.tsx`.
- ui-lab's mock voice transcript, mock stream stages ("Connecting…"), mock
  upload timer, fixtures, variant switcher: not ported.

## Selector / flow changes (tests)

Every aria-label, role, and class the test suites target was preserved, with
these knowing exceptions:

1. **`chat-flow.spec.ts` — "language switch toggles to Luganda"** (updated in
   the same commit): the radios now live inside the picker overlay, so the spec
   opens the picker first. The `getByRole("radio", { name: /Luganda/i })`
   selector itself still works.
2. **smoke.spec "language switcher"** needs no change — it guards on
   `isVisible()` and now skips gracefully (radios hidden until the picker opens).
3. **Deletion flows**: no existing spec deletes a conversation or clears chat,
   so the new confirm step breaks nothing today — but any FUTURE test that
   deletes must click "Delete"/"Clear" in the `role=alertdialog` afterwards.
4. The landing h1 is now the small brand row ("URA Tax Assistant") and the big
   question is an h2 — `getByRole("heading", { name: /URA Tax Assistant/i })`
   still matches; heading order stays valid for axe.
5. The composer hint text changed from "Press Enter to send…" to the
   disclaimer line ("URA Assistant can make mistakes…"). Nothing referenced
   the old text (grepped e2e + units).

## Verification

- **Unit tests: 61/61 pass** (run after every commit).
- **ESLint: clean.** **tsc --noEmit: clean.**
- **Playwright e2e (chromium + mobile-chrome + a11y, mocked backend):
  84 passed, 9 skipped (voice.integration ×3 projects — needs the real
  backend, skipped by design), 1 failed — and that failure is pre-existing:**
  `navigation-consent.spec.ts › analytics dashboard route renders` crashes
  inside the analytics dashboard with `Cannot read properties of undefined
  (reading 'latency')` because the `helpers.ts` mock payload for
  `/api/v1/analytics/**` no longer matches what `useAnalyticsDashboard`
  reads. Zero commits in this migration touch `src/app/analytics/*`,
  `useAnalyticsDashboard.ts`, `helpers.ts`, or that spec
  (`git log fc85d348..HEAD -- <those paths>` is empty), and the failure is
  flaky (the chromium variant passed in the final run). Fixing that mock is
  analytics-scope work, deliberately not smuggled into this migration.
  One port bug WAS found and fixed by the suite before push: axe flagged the
  disabled Voice-toggle label at 2.35:1 (whole-toggle opacity dim) — now only
  the checkbox glyph dims; a11y project passes 4/4.
  Note for local e2e runs: interrupted runs leave zombie headless-chrome
  processes that make later runs time out wholesale in teardown — kill them
  before judging results.
- **Analytics regression check (live):** `/analytics` renders with the legacy
  `:root` tokens (`--bg-0 #0a0a12`, glass surfaces, Aptos font, gradient mesh
  present) and no `.chatv2` in its DOM — the scoped layer does not leak.
- **Live browser walk of the behavioral inventory** (dev server, no backend —
  network failures exercise the error path):

| Contract row (MIGRATION-PLAN §2) | Result |
|---|---|
| H1 hamburger (mobile) opens rail overlay; `.conversation-rail-open` class | PASS (412px) |
| H2 brand + `.top-bar-title` (hidden ≤720px) | PASS |
| H3 New chat appears only after chat starts | PASS |
| H4 Clear conversation (now in kebab, confirm-gated, same `reset()`) | PASS |
| H5 locale switch → `<html lang>` flips to `lg` (real effect chain) | PASS |
| H6 Voice checkbox: role/name kept, enabled per health/MediaRecorder logic | PASS |
| H7 Narrate checkbox present in toolbar | PASS |
| H8 "Open voice chat" mic in header (all widths) | PASS |
| H9 Project blog link (kebab) | PASS |
| H10 theme cycle Auto→Light→Dark, attribute + persistence | PASS |
| H11 health pill, `aria-live`, ok/warn states ("Voice unavailable" w/o backend) | PASS |
| S1–S2 rail overlay scrim + "Close sidebar" | PASS |
| S3 rail New chat | PASS |
| S4 search filter | PASS (component untouched) |
| S5 select conversation | PASS |
| S6 delete → confirm dialog → `deleteSession`; Escape/overlay cancels safely | PASS |
| S7 time groups + relative timestamps | PASS (component untouched) |
| L1 h1 /URA Tax Assistant/ + smoke subtitle text present | PASS |
| L2 landing composer = same component | PASS |
| L3 starter chips fire `handleStarterPrompt` (label) | PASS |
| L4 blog link relocated to kebab (present) | PASS |
| C1–C4 attach button/input/chips/remove — markup + labels unchanged | PASS (unit-covered) |
| C5 textarea aria/placeholder/Enter behavior; enabled while streaming | PASS |
| C6 mic aria "Start speaking"/"Stop listening", disabled logic | PASS |
| C7 recording panel "Listening..." + Cancel/Send recording | PASS (unit-covered; mocked e2e voice suite green) |
| C8 send disabled empty/loading/uploading; "Analysing attachment..." | PASS (unit-covered) |
| M1 message list aria-live + stick-to-bottom logic (untouched) | PASS |
| M2 "Latest" scroll button (restyled only) | PASS |
| M3 loading state → stage label + skeleton (same trigger condition) | PASS (observed live) |
| M4 Listen per message, "Listen in English" | PASS |
| M5 Copy → "Reply copied" | PASS (markup untouched) |
| M6–M7 feedback thumbs + comment (component untouched) | PASS |
| M8 citations expander "Sources (n)" | PASS |
| M9 citation links (untouched) | PASS |
| M10 escalation banner role=alert (restyled amber) | PASS |
| M11 report download button (markup untouched) | PASS |
| M12 sr-only "You said"/"Assistant replied" | PASS (observed in DOM) |
| G1 VoiceChat overlay untouched + outside `.chatv2` (keeps legacy tokens) | PASS |
| G2 ConsentBanner untouched | PASS |
| G3 store/persistence/SSE/viewport-vars logic untouched | PASS (no diff in those files) |
| Contrast: New chat + send = #003087/#fff **11.85:1** light; light-pill **13.63:1** dark; body 10.5:1; category label 6.3:1 | PASS (measured) |

## What I could NOT fully verify — check these manually

1. **iOS Safari keyboard behavior** (R3). The legacy fixed-dock +
   `--keyboard-inset` mechanism is preserved and the mobile dock renders
   correctly at 412px in emulation, but nothing emulates the real iOS visual
   viewport. Open the site on a phone, focus the composer, confirm the input
   stays above the keyboard and the transcript scrolls.
2. **Server-voice round trip against the real backend** (Voice checkbox ON →
   record → transcript+reply+narration). The code path is untouched and the
   mocked e2e voice suite covers the client side, but I could not run the real
   FastAPI backend locally.
3. **Auto-narrate audio playback** with real TTS audio (mocked WAV only).
4. **Animation feel** (dialog pop, rail slide, caret blink) — my preview
   environment doesn't composite animations; timings are inherited from the
   sandbox but eyeball them once.
5. **Low confidence: none functional.** The one visual judgment call I'd flag:
   in dark mode the primary buttons are light pills (ChatGPT-dark convention,
   per the approved theme) — if you expected navy buttons in dark mode too,
   say so and it's a two-line token change (`--cv2-accent` in tokens.css).

## Rollback

Each commit is independent-ish, but the practical rollback is
`git revert <range>` of the `ui:` commits — no store, service, or config file
was touched, so reverting cleanly restores the old UI.

---

## Superseded: the header (2026-08-20)

The `H1`–`H11` rows above record the chatv2 header as it was verified on
2026-07-29. That header no longer exists. A later change removed the navbar
outright, so the rows that describe it are history, not current behaviour:

| Was | Now |
|---|---|
| `H2` brand + `.top-bar-title` in the header | Moved into the sidebar's brand row, above the conversation list. Still `.top-bar-title`; hidden below 1024px, where the rail is a drawer |
| `H3` New chat button in the header | Removed. "New chat" is the sidebar's, which is one click away at every width |
| `H5` locale switch in the header | A `Response language: <label>` item in the 3-dot menu. The picker overlay itself is unchanged |
| `H10` theme cycle | Unchanged — still the kebab's `Theme: …` item |
| Header Sign in / Sign up pair | Removed. The sidebar account block was always their primary home |
| `H1` hamburger | Same control and same accessible name ("Open conversation history"), now drawn as the panel icon |
| `S1`–`S2` rail "Close sidebar" | Still there, in the sidebar's brand row rather than a separate top bar |

What replaced it: a strip that floats over the conversation with no background
and no bottom border — the sidebar toggle at top left when the rail is away,
the conversation title beside it, and the 3-dot menu at top right. The
transcript scrolls underneath and dissolves into a mask on `.message-list`
whose top stop is `--cv2-top-h`, so nothing draws a line between the two.

The sidebar gained the toggle and a search button under the URA mark, one-line
conversation rows with a Pin / Rename / Delete menu on hover, a collapsible
"Chats" category, and a "View all conversations" link to `/chats` once ten
threads have accumulated. Collapsing the rail on a desktop leaves it peekable:
hovering the toggle floats it in over the conversation without moving the
transcript, and only a click docks it.

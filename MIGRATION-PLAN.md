# UI Migration Plan — ui-lab → FinalYearProject frontend

**Goal:** port the approved ui-lab UI (Rail layout, flat white/dark theme, in-composer
controls) into `App/frontend`, presentation layer only, zero functional regressions,
then push to `dev` on `github.com/mpairwe7/FinalYearProject`.

**Git/auth status (checked 2026-07-28):** authenticated as `dhavyd3` with
`push: true` on the repo; local clone is on `dev`, in sync with `origin/dev`
(`fc85d348`), clean working tree. **Note: `dev` is the repository's DEFAULT branch** —
commits land directly on it with no PR gate. Proceeding as instructed unless you say
otherwise. (`gh` also holds a second account, `mpairweLandwind`, currently inactive;
pushes will go out as dhavyd3.)

**Scope:** the `/` route — landing, chat transcript, sidebar, header, composer, and
the new confirm dialog. **Out of scope, untouched:** `/analytics/*`, the VoiceChat
full-screen overlay internals (its entry point is in scope), ConsentBanner, PWA/service
worker, error/not-found/loading pages, and the dead components below.

**Dead code found (left untouched, listed for the record):** `VoiceFirstChat.tsx`
(643 lines — `voiceFirstMode` is never set true anywhere), `VoiceVisionMode.tsx` +
`CameraCapture.tsx` (only reachable from VoiceFirstChat), `VoiceModal.tsx`,
`VoiceSettings.tsx` (no live importers), `StarterPrompts.tsx` (only its unit test
imports it; the landing inlines its own chips).

---

## 1. Structural diff

| Axis | FinalYearProject (`App/frontend`) | ui-lab | Incompatibility? |
|---|---|---|---|
| Framework | Next 16.2.10, React 19.2.7, TS 6.0.3 | identical (pinned to match during sandbox build) | none |
| State | Zustand 5.0.14: `useChatStore` (persisted, `ura-chat-store` v2, 200-turn/50-session caps, greeting turn), `useVoiceStore`, `useAnalyticsStore`; TanStack Query for speech health/TTS | Zustand in-memory mock stores, no persistence, no query lib | **ui-lab stores are NOT ported.** FYP stores stay the single source of truth; ui-lab components are adapted to FYP's existing props/handlers |
| Component convention | 907-line `page.tsx` owns all handlers/effects; children receive props; heavy `memo` | components self-select from stores | Port keeps FYP convention: `page.tsx` logic stays, only JSX + child markup change |
| Styling | one 3,265-line `globals.css`, CSS custom properties, dark glass/gradient default, `data-theme="light"` override | 10 small CSS files, flat theme, light default + `data-theme="dark"` override | **Token names collide** (`--bg-0`, `--text-0`, `--border-*`, radii) with different values/semantics → resolved by scoping (section 4). No Tailwind in either project (the brief mentions a Tailwind config — neither side has one; N/A) |
| Theme plumbing | `lib/theme.ts` writes the **resolved** `data-theme="dark"\|"light"` before paint (theme.ts:26-29, 40-43) | same convention | none — chat-scoped dark selectors key off the attribute FYP already sets; zero theme.ts changes |
| Routing | App Router; `/` + `/analytics` (own layout + analytics.css) | single page | none |
| Package manager / deps | bun (`bun.lock`); extra deps: @tanstack/react-query, mermaid, recharts | npm; **zero deps beyond the shared four** | **No new dependencies required. No lockfile changes. No conflicts possible.** |
| Testing | Vitest units + Playwright e2e incl. axe suite — selectors form a hard contract (section 2/5) | none | e2e/unit selectors must be preserved; two flows change (section 5, R2) |
| Voice | dual-path mic (browser SpeechRecognition ⟷ server AudioRecorder, gated by Voice checkbox) + WebSocket overlay + TTS narrate | single mock recording flow | ui-lab's mock recording UI is re-bound to the real dual-path logic; the mock transcript is deleted |
| Markdown | rich custom renderer (cite refs, callouts, tables, mermaid) | minimal renderer | FYP `Markdown.tsx` kept verbatim; restyled via scoped CSS only |

---

## 2. Behavioral inventory — the contract (route `/`)

Every interactive element in the current app. Nothing on this list may disappear.
All paths relative to `App/frontend/src`.

### Header (top bar)

| # | Element | file:line | Handler / action | Data source | State it reads/writes |
|---|---|---|---|---|---|
| H1 | Sidebar open (hamburger) | app/page.tsx:712 | `setSidebarOpen(true)` | — | local `sidebarOpen` W |
| H2 | Brand logo + title (`.top-bar-title`) | app/page.tsx:715-725 | — | static | — |
| H3 | New chat button (shown when `hasStartedChat`) | app/page.tsx:730 | `createNewSession()` (store:442) | — | conversations/activeId W |
| H4 | Clear chat (trash) | app/page.tsx:733 | `reset()` (store:386) | — | chat W (back to greeting) |
| H5 | Locale radiogroup EN/LG | app/page.tsx:738-743 | `setLocale(v)` | `LOCALE_OPTIONS` page.tsx:63-66 | `locale` W; read by speech-recog re-init :264-288, `<html lang>` effect :197-201, TTS :301-305, chat request body :421 |
| H6 | **Voice checkbox** (role=checkbox, name "Voice") | app/page.tsx:744-747 | `setVoiceMode(e.checked)` | `speechHealth` (disabled when `!serverReady && !hasMediaRecorder`) | local `voiceMode` W — **gates which mic path H/C7 takes** |
| H7 | Narrate checkbox | app/page.tsx:748-751 | `setAutoNarrate` | — | local `autoNarrate` W; auto-TTS effect :322-332 |
| H8 | Voice-first mic (opens overlay) | app/page.tsx:752-760 | `setVoiceChatMode(v=>!v)` | — | `voiceChatMode` W → mounts `<VoiceChat>` :878-883; aria "Open voice chat"/"Close voice chat" |
| H9 | Blog link | app/page.tsx:761-770 | href `BLOG_URL` | `NEXT_PUBLIC_BLOG_URL` :61 | — |
| H10 | ThemeToggle | components/ThemeToggle.tsx:14-23 | `cycle()` | localStorage `ura-theme` | `html[data-theme]` W; aria `Theme: X. Click to switch.` |
| H11 | Voice health pill (`aria-live="polite"`) | app/page.tsx:772-774 | — (display) | `useSpeechHealth()` 60s poll (hooks/useSpeech.ts:19-28) | reads `speechHealth.status` |

### Sidebar (ConversationRail.tsx)

| # | Element | file:line | Handler | Data source | State |
|---|---|---|---|---|---|
| S1 | Overlay scrim (click closes) | ConversationRail.tsx:123-127 | `onClose` | — | `sidebarOpen` W |
| S2 | Close button, aria "Close sidebar" | :131-133 | `onClose` | — | same |
| S3 | New chat | :136-138 | `onNewConversation` → `createNewSession()` | — | store W |
| S4 | Search input, aria "Search conversations" | :141-148 | local filter (title+preview) | conversations | local `searchQuery` |
| S5 | Select conversation | :174-177 | `switchSession(id)` (store:465-ish) | conversations | activeId/chat W |
| S6 | Delete conversation, aria `Delete ${title}` | :186-193 | `deleteSession(id)` — currently instant, no confirm | — | conversations W (permanent; localStorage) |
| S7 | Time labels + Today/Yesterday/7d/30d groups | :15-93 | — | client clock (60s tick, hydration-safe) | — |

### Landing (page.tsx:781-826, shown while `chat.length <= 1`)

| # | Element | file:line | Handler | Data source | State |
|---|---|---|---|---|---|
| L1 | h1 heading /URA Tax Assistant/ | :791 | — | — | **e2e queries this heading** |
| L2 | Landing composer | :797-799 | same `composerProps` as C* rows | — | — |
| L3 | Starter chips ×4 | :801-807 | `handleStarterPrompt` :647-650 → `trackStarterPromptUsed` + `sendMessage(p)` | `STARTER_PROMPTS` :68-73 | chat W |
| L4 | Blog link | :809-825 | href `BLOG_URL` | env | — |

### Composer (ChatInput.tsx)

| # | Element | file:line | Handler | Data source | State |
|---|---|---|---|---|---|
| C1 | Attach button, aria /Attach a document/ | ChatInput.tsx:145-153 | opens hidden file input | — | disabled: `isLoading \|\| chips ≥ MAX_ATTACHMENTS(3)` |
| C2 | File input (`ATTACHMENT_ACCEPT` lib/attachments.ts:27) | :132-144 | `onAttachFiles` → page `attachFiles` :370-387 → `uploadAttachment` :336-368 **POST /api/v1/documents/analyze** | backend | `pendingAttachments` W; >10 MB → instant "Over the 10 MB limit" chip |
| C3 | Attachment chip status (uploading dots / docType·size / error) | :103-127 | — | upload result | reads chip status |
| C4 | Chip remove, aria `Remove ${name}` | :117-124 | `onRemoveAttachment` page:389-391 | — | `pendingAttachments` W |
| C5 | Textarea, aria "Type your message", Enter=send, auto-grow ≤144px, placeholder swaps when `voiceMode` (/Voice mode on/) | :156-176 | `setMessage`; Enter → `onSend` | — | `useChatStore.message` R/W; currently `disabled={isLoading}` |
| C6 | Mic button, aria "Start speaking"/"Stop listening" | :177-184 | `onMicClick` = page `handleMicClick` :575-635 — **branch on `voiceMode && hasMediaRecorder`:** server path = AudioRecorder → `voiceChat()` service (transcript + reply + optional audio); browser path = SpeechRecognition fills input | Web Speech API or backend voice pipeline | `speechState`, `isRecording`, chat W; disabled when `speechUnavailable \|\| isLoading \|\| isTransitioning` |
| C7 | Recording panel: waveform + Cancel ("Cancel recording") + Confirm ("Send recording") | :67-97 | cancel → page :637-645; confirm → `onMicClick` (stop+submit) | — | recorder lifecycle |
| C8 | Send button, aria "Send message" ↔ "Analysing attachment..." while uploading | :185-192 | `onSend` → page `sendMessage` :395-571 — **POST /api/v1/chat/stream SSE** (token/metadata/revision/done events, rAF-batched flush) with sync fallback **POST /api/v1/chat**, 120 s abort, analytics tracking | backend | chat W, `isLoading` |
| C9 | Hint line (mode-dependent copy) | :94,194-199 | — | `voiceMode` | — |

### Chat area

| # | Element | file:line | Handler | Data source | State |
|---|---|---|---|---|---|
| M1 | Message list, `aria-live="polite"`, stick-to-bottom logic | app/page.tsx:830, :160-195, :243-249 | scroll listener | chat | scroll affordance refs |
| M2 | "Latest" scroll-to-bottom button | app/page.tsx:856-868 | `scrollToBottom` | — | `showScrollToLatest` |
| M3 | Loading dots row (assistant, pre-first-token) | app/page.tsx:843-852 | — | `isLoading` + empty last turn | — |
| M4 | Listen button, aria `Listen in English/Luganda` ↔ "Stop listening" | ChatMessage.tsx:198-205 | `onListen` → page `handleListenToReply` :292-320 → TTS (`useTtsMutation` → `synthesize`) + `playAudioBase64` | speech service | `playingTurnId`, `ttsLoading` |
| M5 | Copy button "Copy reply" → "Reply copied" | ChatMessage.tsx:13-34, 206 | clipboard | — | local `copied` |
| M6 | Feedback 👍/👎, aria "Helpful response"/"Unhelpful response", optimistic + rollback | FeedbackButtons.tsx:51-114 | `submitFeedback()` (analytics API) | backend | optimistic rating, states idle/submitting/submitted/error |
| M7 | Feedback comment input + Send/Skip (opens on 👎) | FeedbackButtons.tsx:132-160 | `updateFeedbackComment()` PATCH | backend | local |
| M8 | Citations `<details>` "Sources (n)" + grounding text "Well grounded"/"Verify with URA" | ChatMessage.tsx:154-193 | native toggle | `turn.citations`, `turn.faithfulnessScore` | — |
| M9 | Citation source links → ura.go.ug | ChatMessage.tsx:167-181 | href `sourceUrl()` (lib/uraContacts) | — | — |
| M10 | Escalation banner, `role="alert"`, tel/WhatsApp/website links | ChatMessage.tsx:130-152 | tel:/https | `URA_CONTACTS` | — |
| M11 | Report download per sent attachment, aria `Download analysis report for ${name}` | ChatMessage.tsx:37-78, 117-127 | **GET /api/v1/documents/{id}/report** → blob download | backend | local busy/error ("Expired") |
| M12 | sr-only role labels "You said"/"Assistant replied" | ChatMessage.tsx:108 | — | — | unit-test contract |

### Global (in scope only as mount points)

| # | Element | file:line | Note |
|---|---|---|---|
| G1 | VoiceChat overlay (orb, barge-in, transcript, offline badge, `dialog "Voice chat"`) | components/VoiceChat.tsx:103-284 | **Preserved verbatim** — file untouched; entry is H8; rendered as a sibling of the new scoped wrapper (see §4) |
| G2 | ConsentBanner Accept/Decline | components/ConsentBanner.tsx:48-73 + layout.tsx:51 | untouched (mounted in layout, outside the chat scope) |
| G3 | Store persistence, greeting turn, 120 s SSE abort, viewport/keyboard CSS vars effect | store/useChatStore.ts:77-101; page.tsx:203-241 | logic untouched; the viewport-vars effect is kept and re-pointed at the new dock element ref |

**Non-element behaviors that must survive:** SpeechRecognition re-created on locale
change (page:264-288); `<html lang>` sync (:197-201); auto-narrate of new replies
(:322-332); recorder/audio cleanup on unmount (:252-261); `hasStartedChat =
chat.length > 1` landing/chat gate (:154); analytics init (:158).

---

## 3. Component mapping table

| ui-lab component | Replaces (existing) | Mock data it used | Real source it must bind to | Strategy |
|---|---|---|---|---|
| `Composer.tsx` | `components/ChatInput.tsx` | mock `send/stop/attachFiles`, fake upload timer, `MOCK_TRANSCRIPT` voice insert | FYP `composerProps` from page.tsx:672-691 (message, onSend, onMicClick, onCancelRecording, attachments as `PendingAttachment[]`, `onAttachFiles(FileList)`, speechState, voiceMode…). Recording panel binds to real `isRecording`/`speechState`; **mock transcript deleted** — the browser path fills the input via SpeechRecognition exactly as today. Voice checkbox (H6), Narrate checkbox (H7) and LanguageMenu move into the toolbar; H6 stays a real `<input type="checkbox">` named "Voice" (role contract). Send aria swap (C8) and placeholder swap (C5) preserved | **adapt** (rewrite ChatInput.tsx, keep filename + props interface + all aria labels) |
| `ChatHeader.tsx` | header JSX in `app/page.tsx:710-776` | mock store, kebab with mock links | Real handlers: H1, H3, H10 stay visible; H4 (clear) and H9 (blog) move into the ⋮ kebab; H8 (voice overlay mic) and H11 (health pill) **stay in the header** — deviation from ui-lab's ultra-minimal header, required by contract | **adapt** (new `ChatHeader.tsx`, page renders it) |
| `Sidebar.tsx` | `components/ConversationRail.tsx` | mock conversations/order | FYP `conversations`, `switchSession`, `deleteSession` (now behind confirm), existing search + time-group + hydration-safe clock logic (kept). Keeps `aside.conversation-rail` element/classes, `.conversation-rail-open` open-state class, "Close sidebar", `Delete ${title}` | **adapt** (rewrite ConversationRail.tsx in ui-lab skin, same filename) |
| `MessageList.tsx` + `MessageActions.tsx` | chat-area JSX in page.tsx:829-873 + `ChatMessage.tsx` markup/classes | mock stages, mock TTS playing, mock votes | Real turns. `ChatMessage.tsx` keeps its memo comparator, FYP `Markdown.tsx`, `FeedbackButtons` (M6/M7 untouched), citations, report download. Pre-first-token state renders "Searching the URA knowledge base…" + skeleton — bound to the **existing** `isLoading && last-turn-empty` condition (M3); **no fake connecting/retrieving timers** (real SSE has no stage events) | **adapt** (restyle ChatMessage + new stage indicator; keep `.message-row-user`/`.message-row-assistant` classes) |
| `ConfirmDialog.tsx` | — (new) | — | Wraps S6 (`deleteSession`) and H4 (`reset`) — the handler called on confirm is byte-identical to today's direct call | **new** (approved P9; see R2 for e2e impact) |
| `LanguageMenu.tsx` | locale radiogroup page.tsx:738-743 | **8 languages — 6 are mock-only** | Real `LOCALE_OPTIONS` = **en, lg only**. Swahili/Runyankole/Acholi/Ateso/Lusoga/Lugbara have no backend support → **dropped per the no-invented-sources rule** (listed in handoff). Options keep `role="radio"`/`radiogroup` semantics inside the focus overlay so `getByRole("radio", { name: /Luganda/i })` still resolves | **adapt** (reduced to real locales) |
| `Landing.tsx` | landing JSX page.tsx:781-826 | fixture prompts with categories | Real `STARTER_PROMPTS` (page.tsx:68-73) + static category labels (copy only). Brand row stays an **h1 "URA Tax Assistant"** (styled small) to satisfy the e2e heading query; "How can I help with your taxes?" becomes h2. Blog link lives in the kebab (H9) | **adapt** |
| `ThemeToggle.tsx` | `components/ThemeToggle.tsx` | ui-lab store | existing `useTheme` hook — identical pattern | **keep existing** (class-level restyle only) |
| `ConversationRail` dialog css, `styles/*.css` (10 files) | new, additive | — | merged as one scoped layer (section 4) | **new files** |
| `VariantSwitcher.tsx` | — | — | Rail layout chosen; switcher is a sandbox tool | **not ported** |
| `useChatStore.ts` / `useUiStore.ts` (ui-lab) | — | all mock | **not ported.** FYP stores untouched. Confirm-dialog state + sidebar-collapse state = new page-local state | **dropped** |
| `mocks/fixtures.ts`, `mocks/mockStream.ts`, `Markdown.tsx` (ui-lab), `Icons.tsx` (ui-lab) | — | all mock | FYP Markdown + Icons kept; a few missing glyphs (PanelLeft, Kebab, Globe, ChevronDown, Square) are added to FYP `Icons.tsx` (additive) | **dropped / additive** |

**ui-lab features intentionally NOT ported (would violate "no new features" — need your call, see §5 D1/D2):** Stop-generation button; Retry-on-error card. Default plan: omit both.

---

## 4. Style reconciliation plan

Neither project uses Tailwind — reconciliation is pure CSS custom properties.

**Mechanism — additive and scoped, `globals.css` body untouched:**

1. New directory `src/styles/chatv2/` holding the ported stylesheets (tokens,
   shell/header, sidebar, composer, messages/states, landing, dialog, langmenu,
   actions). One `@import` block **appended** to the end of `globals.css` — the only
   edit to that file (verifiable in the diff).
2. Every rule is scoped under `.chatv2`, a class on the chat page's root `div`
   (`app-shell` wrapper in page.tsx). Token redefinitions live on `.chatv2` (light
   values) and `:root[data-theme="dark"] .chatv2` (dark values). Since FYP's
   `theme.ts` already writes the *resolved* `data-theme="dark"|"light"` before paint
   (theme.ts:40-43), this works with **zero theme-plumbing changes**.
3. Colliding token names (`--bg-0`, `--text-0/1/2`, `--border-0/1`, radii) are
   *shadowed* inside the `.chatv2` subtree only; `:root` values are never edited, so
   every consumer outside the chat subtree keeps today's values.
4. Legacy chat rules in `globals.css` become inert where class names are retired, and
   are overridden where class names are deliberately retained for the test contract
   (`.message-row*`, `.conversation-rail*`, `.top-bar-title`): every retained selector
   gets an explicit `.chatv2`-scoped restyle (higher specificity, imported later), so
   the legacy rule can never win. No deletions from the 3,265-line file — surgical
   deletion is the riskier operation and buys nothing.
5. Overlays rendered from the chat page that must keep their current look —
   `<VoiceChat>` — are rendered as **siblings after** the `.chatv2` wrapper, not
   inside it, so they cannot inherit shadowed tokens (VoiceChat's CSS reads
   `--surface-3`, `--ura-teal`, etc. from `:root`). ConsentBanner is mounted in
   `layout.tsx`, already outside the scope.
6. Fonts: `.chatv2` sets the system stack (approved flat look); the global
   Aptos/Trebuchet stack stays for everything else.
7. Body-level visuals (gradient mesh `body::before`, noise `body::after`, scrollbar
   tint) are untouched; the chat shell paints an opaque `100dvh` surface over them on
   `/`, and mobile `themeColor` metadata is updated only if you approve (cosmetic,
   listed in handoff otherwise).

**Screens outside the migration scope that could regress from a global token change,
and prevention:**

| Screen | Dependency on globals.css | Prevention |
|---|---|---|
| `/analytics` + `/analytics/evaluation` | reads `:root` tokens (`--accent-data`, `--surface-*`, `--text-*`) + own `analytics-*` classes in analytics.css (verified: no shared `.card/.grid` usage in its markup) | `:root` never edited; all new tokens scoped `.chatv2`; before/after visual check in verification |
| VoiceChat overlay | `--surface-3`, `--ura-teal`, `--grad-primary`, `voice-*` classes | rendered outside `.chatv2` (point 5); file untouched |
| ConsentBanner | `.consent-banner` styles + tokens | untouched; z-index vs the new composer dock re-verified on mobile |
| `error.tsx` / `not-found.tsx` / `loading.tsx` | token reads | `:root` untouched; smoke-checked in verification |
| PWA offline.html | static, self-contained | none needed |

---

## 5. Risk list (ranked by blast radius)

| # | Risk | Blast radius | Mitigation |
|---|---|---|---|
| **R1** | **Voice interaction matrix** — dual-path mic (browser dictation vs server voice, gated by the Voice checkbox), in-composer recording lifecycle, narrate auto-TTS, listen-per-message, voice overlay entry. The redesign moves these controls; any wiring slip breaks a flagship feature | Highest — core differentiator, hard to unit-test | `page.tsx` handlers/effects untouched; only JSX placement changes. All six voice e2e specs re-run; manual matrix in handoff (mic in both checkbox states × en/lg × narrate on/off) |
| **R2** | **Deliberate flow changes break e2e specs**: (a) locale radios now live inside the picker overlay — selector resolves but the click sequence needs an "open picker" step (`ui-theme.spec.ts`, `chat-flow.spec.ts`); (b) delete/clear now confirm-gated — specs that delete must click "Delete" in the dialog; (c) landing headline restructure (mitigated: h1 text unchanged) | High — CI red until specs updated | Spec updates shipped **in the same commits** as the UI change; every selector name preserved (full contract in §2); changes enumerated in handoff |
| **R3** | **Mobile keyboard/viewport** — current dock is `position:fixed` fed by `--keyboard-inset`/`--chat-dock-height` vars from a page effect (page.tsx:203-241). New in-flow composer must keep keyboard avoidance on iOS Safari/Android | High on mobile | Keep the viewport-vars effect and the fixed-dock pattern at ≤720px in chatv2 CSS; manual device pass in handoff (this is the one thing I cannot fully verify headless) |
| **R4** | **Legacy CSS bleed** into retained class names (`.message-row*`, `.conversation-rail*`, `.top-bar-title`) — old rules still live in globals.css | Medium — visual glitches only | Explicit `.chatv2`-scoped overrides for every retained selector incl. the properties the legacy rules set (grid→flex resets, positioning); visual diff pass |
| **R5** | **Streaming render perf** — SSE token flushes re-render the transcript; ChatMessage's custom memo comparator (ChatMessage.tsx:230-246) is what keeps this cheap | Medium | Comparator and component identity preserved; restyle is class-level |
| **R6** | Voice checkbox must stay `role=checkbox` name "Voice" inside a redesigned toolbar toggle | Medium (e2e + a11y) | Implemented as a styled `<label><input type="checkbox">` — same DOM semantics as today |
| **R7** | Unit tests (`ChatInput.test.tsx`, `ChatMessage.test.tsx`) assert aria labels, placeholder `/Voice mode on/`, "Analysing attachment...", chip texts | Medium | All preserved verbatim (§2 C1-C9, M4-M12); tests run pre-push |
| **R8** | Push target `dev` is the **default branch** — no review gate | Process risk | Flagged here; per your instruction I push to `dev`. Say the word and I'll use a feature branch + PR instead |
| **R9** | Analytics visual regression via token leak | Low (prevented by design) | §4; before/after screenshot check |
| **R10** | bun.lock drift | None | zero new dependencies |

**Decisions I need from you (defaults applied if you just say "approved"):**

- **D1 — Stop button / typing while streaming.** ui-lab has a Stop square and an
  always-enabled textarea. Stop = new behavior (aborting the SSE isn't wired today)
  → **default: no Stop button; send stays disabled while streaming; textarea becomes
  enabled-but-guarded** (typing allowed, Enter no-ops — presentation-only; the
  `isLoading` guard already exists in `sendMessage`). Tell me if you want the textarea
  to stay hard-disabled instead, or Stop wired as a follow-up.
- **D2 — Error card + Retry.** Real error replies are plain strings with no flag on
  the turn; adding a flag + retry action crosses into feature territory → **default:
  omit; errors render as today**.
- **D3 — Confirm dialogs** on delete/clear are part of the approved UI (P9) →
  **default: included**, with the e2e updates from R2(b).
- **D4 — Language picker ships with en + lg only** (real backend locales), overlay
  design retained → default: yes.

---

## Phase 2 execution order (after your approval)

1. `ui: add chatv2 scoped style layer (tokens + stylesheets, no component changes)` — app renders identically; proves zero leak (analytics screenshot check here).
2. `ui: port composer with in-composer voice, narrate, attach and language controls` — ChatInput.tsx + LanguageMenu.tsx + Icons additions + unit-test updates.
3. `ui: port sidebar with confirm-gated delete` — ConversationRail.tsx + ConfirmDialog.tsx + affected e2e updates.
4. `ui: port header, landing, and page shell` — ChatHeader.tsx, Landing markup, page.tsx JSX restructure (handlers untouched), kebab menu.
5. `ui: restyle transcript (messages, citations, grounding, loading skeleton)` — ChatMessage restyle + stage indicator.
6. `ui: responsive + mobile dock polish` — ≤720px dock, safe areas, sidebar overlay.
7. Verification checklist run (every row of §2, pass/fail table) → `UI-MIGRATION-NOTES.md` → push to `dev` (with a `--dry-run` first).

---

**STOP.** Awaiting approval of this plan (and the D1–D4 defaults) before Phase 2.

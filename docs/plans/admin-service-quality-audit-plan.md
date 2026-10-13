# Plan: the administrator becomes the auditor — service-quality audit for AI answers, officer chats and calls

Status: **Phases 1–2 done (2026-10-13)** — administrators are read-only; the AI-chat audit is live. Phases 3–5 to follow, one commit each on `dev`.
Line numbers are approximate; search for the named symbol if a line has moved.

## Context

Today the URA admin console mixes **live customer handling** with oversight:

- An administrator can open "My queue" (`/agent`) and "All tickets" and reply to or claim cases.
- In "Calls" an administrator can take a call, step into one the AI is handling ("Take over"), and gets the "caller waiting" alerts and chime on every page.
- The overview page (`/admin`) is a backlog board ("Waiting longest", "Open the full queue").

What exists for quality is thin:

- Thumbs up/down is stored (`feedback` table) but is **not linked to the conversation or to the answer's position in it**. `session_id` is the analytics tab id, `message_id` is a random browser id, and no `conversation_id` or turn number is sent.
- Only signed-in users with analytics consent can rate; everyone else silently gets "Failed to send".
- No admin view shows *where in a conversation* the AI went wrong.
- There is **no satisfaction rating (CSAT)** for officer chats or calls. The only call rating is the officer's 1–5 stars for *the AI*, given in the call wrap-up.
- An officer "chat" is really a ticket. The officer's reply sits in one field that each new reply overwrites, and the taxpayer sees it in a separate "Case Room" window that refreshes every 5 s, or on their next message.
- The AI keeps answering throughout, and resolving a ticket sends no signal to the taxpayer.

**Outcome:** officers handle taxpayers live; administrators **audit** service quality in four streams:

| # | Stream | Who gives the signal | How |
|---|---|---|---|
| 1 | AI answers in chat | Taxpayer | 👍/👎 per answer, now linked to conversation + answer position |
| 2 | AI on calls | Caller, by voice | AI asks "was this helpful?" at goodbye, or while transferring; speech-to-text answer classified positive / neutral / negative |
| 3 | Officer chats | Taxpayer | 5-emoji rating pops up when the officer clicks **End chat**; then a "you're back with the AI" notice |
| 4 | Officer calls | Officer, on the caller's behalf | Officer asks the caller before ending; records the 5-emoji level in the wrap-up |

### Decisions (confirmed with the project owner)
- **Officer takes over the chat.** While an officer has the case, the taxpayer's messages go to the officer and the AI is paused. **End chat** leads to the rating, then to the "back with the AI" notice.
- **Administrators lose live tools entirely.** "My queue", "All tickets" and "Calls" disappear from the admin menu, and the server refuses admin writes on those routes. Admins reach individual cases only through read-only drill-downs in the audit pages.
- **Anonymous thumbs ratings allowed.** Signed-out users, and signed-in users without consent, can rate; the rating is stored without a user id.
- **"Needs review" queue.** Problem items can be marked *Reviewed* or *Action needed* with a note, and each action is written to the audit ledger.
- **Branch:** implemented directly on `dev`, one commit per phase.

---

## Architecture at a glance

```
 Taxpayer chat ──👍/👎 (turn_id, conversation_id, turn_index)──► feedback (+ snapshot cols)
 Taxpayer chat ──emoji CSAT after "End chat"──────────────────┐
 Call AI (brain) ──spoken review, classified─────────────────┼──► service_ratings (one table, 4 sources)
 Officer wrap-up ──caller satisfaction (5 levels)────────────┘
 Officer ⇄ taxpayer live chat ───────────────────────────────────► ticket_messages (thread)
                                                     │
 audit_reviews (reviewed / action needed + note) ◄───┤
                                                     ▼
              /v1/admin/quality/*  ──►  /admin (quality overview)  +  /admin/quality (4 tabs + Needs review)
```

All new tables use the module-owned, backend-agnostic DDL pattern: `db.execute_script` / `query_all` / `execute` with `?` placeholders, and `_ensure_columns` as in `App/backend/app/receptionist/store.py:48-140`. Each stores `tenant_id` and is filtered with the existing scope idiom `(ctx.tenant_id or "default") if tenant_enabled() else None`.

---

## Part A — Remove live customer handling from the administrator

**Frontend — gating comes from `STAFF_DESTINATIONS`, so the nav, ⌘K palette and account menu update together.** Pages and components to change:

| File | Change |
|---|---|
| `App/frontend/src/lib/roles.ts` | `/agent` roles → `["ura_staff"]`; `/admin/tickets` → `["ura_staff","ura_auditor"]`; `/calls` → `["ura_staff","ura_auditor"]`. Add a new destination `/admin/quality` ("Service quality audit", nav "Quality audit", roles admin + auditor). Add a section `"audit"` (label "Audit") to `StaffSection`, `SECTION_LABEL` and `staffSectionsFor`, holding Quality audit and the existing Audit trail. |
| `src/app/agent/page.tsx:291` | `requireRoles={["ura_staff"]}` |
| `src/app/admin/tickets/page.tsx` | Add `requireRoles={["ura_staff","ura_auditor"]}` (currently none) |
| `src/app/calls/page.tsx:335` | `requireRoles={['ura_staff','ura_auditor']}`; remove the admin-only Supervisor tab (`ALL_TABS`, lines 157-161, 318); `SupervisorBoard` moves to `/admin` |
| `src/components/StaffGuard.tsx` | Mount `StaffCallLayer` (alerts, chime, take-call keys), `CallConsoleBar` availability, `usePresenceHeartbeat` and `TicketLiveBanner` **only for `ura_staff`**. The auditor keeps today's read-only counts. Admins no longer show up in the officer presence roster. |
| `src/components/staff/calls/console/StaffCallLayer.tsx:18` | Take-call roles → staff only |
| `src/components/staff/calls/CallWorkspace.tsx:34,277-281` | Delete `canStepIn` / "Take over" |
| `src/components/staff/TicketCase.tsx:83` | `canAct = role === "ura_staff"` |
| `src/components/StaffNav` icons | Add a `DESTINATION_ICON` entry for `/admin/quality`, plus the missing `/admin/audit` icon |

**Backend — enforced on the server, not just hidden buttons.** In `App/backend/app/main.py`:

- **New helper `_require_live_officer(ctx)`.** It allows only `ura_staff`, and keeps the operator-key break-glass path exactly as `_require_staff_writer` (2441) does. `_require_staff_writer` stays as it is, because outbox dispatch, retry and test still need admins.
- **Routes switched to the new helper:**
  - ticket presence POST (≈3268);
  - ticket PATCH (3979);
  - the new end-chat route.
- **`_desk_writer` (4238) → staff only.** This covers claim, release, end, hold, transfer, wrapup, callback-done and the officer presence PUT. Remove the supervisor step-in: `is_supervisor=ctx.role == "ura_admin"` at 4295 and the matching branch in `receptionist/desk.py:claim`.
- **`receptionist/ws.py:717, 949`:** officer audio socket and desk socket writes → `("ura_staff",)`.
- **Read routes stay open to admin and auditor.** These are GET tickets, stats, sla and detail, and GET calls and call detail; the audit drill-downs use them.

---

## Part B — AI answers in chat: link each 👍/👎 to its turn, allow anonymous ratings, find where it went wrong

### B1. A server-issued turn id per answer
- `/v1/chat` (`main.py:1160`), `/v1/chat/stream` (`main.py` around `_log_stream_conversation`, 1413) and `chat_ws_v2.py` (for parity) generate `turn_id = uuid4()` **before** generation.
  - Return it as `ChatResponse.turn_id` (`models.py:76`) and in the SSE terminal/metadata event.
  - Pass it to `db.log_conversation(..., row_id=turn_id)`. Add the optional `row_id` parameter to both twins: `database.py:1358` and `postgres.py:800`.
- Frontend:
  - Store `serverTurnId` on the assistant `Turn` (`store/useChatStore.ts`, from the REST/SSE response in `app/page.tsx`).
  - `FeedbackButtons` → `submitFeedback` sends `turn_id`, `conversation_id` (`activeConversationId`), `turn_index` (ordinal of this AI answer in the conversation; greeting and officer turns excluded) and `chat_session_id` (`getChatSessionId()`).

### B2. Feedback storage and anonymous ratings
- **New `feedback` columns:**
  - `conversation_id`, `turn_id`, `turn_index`, `locale`, `topic_tag`, `sources_json`, `confidence`.
  - Index `(conversation_id, turn_index)`.
  - Added in SQLite (`_ensure_column`, `database.py:919-970`) and Postgres (`postgres.py:425`). `save_feedback` in both now also **writes `tenant_id`**, which it never did.
- **Snapshot on write.** When `turn_id` is given, `save_feedback` reads that `conversations` row and copies `sources` (≤5 titles/ids), `confidence`, `topic_tag` and `locale` into the feedback row. It also derives an authoritative `turn_index` (rows in that conversation with `created_at` ≤ this one). The rated context therefore survives the 7-day `CONVERSATION_TTL_DAYS` purge; feedback lives 90 days.
- **`POST /v1/feedback` (`main.py:2655`) accepts anonymous ratings.**
  - Signed-in with analytics consent → store `user_id` as today.
  - Otherwise → store `user_id=''`.
  - Add `@limiter.limit(_RATE_LIMIT)` to it and to `PATCH /v1/feedback/{id}/comment`. Anonymous comment updates keep the existing one-shot rule (only sets an empty comment).
  - Frontend: drop the `hasAnalyticsConsent()` gates in `store/useAnalyticsStore.ts:149,183`.
- **Small extras:**
  - Hide thumbs on the greeting and on officer turns.
  - Move `FeedbackButtons` strings onto the existing unused `message.helpful` / `message.notHelpful` keys, plus new keys for sending, thanks, comment, send and skip.
  - Update `test_privacy_compliance_306.py` and the privacy copy that says feedback needs consent.

### B3. "Where did it go wrong" — `App/backend/app/quality_audit.py` (new)
- **Per conversation,** take the ratings ordered by `turn_index` and classify the pattern:
  - `all_helpful`;
  - `went_wrong` (a 👍 followed later by a 👎, and it ends 👎);
  - `recovered` (a 👎 followed later by a 👍, and it ends 👍);
  - `never_helpful` (all 👎);
  - `single` (only one rating).
- **Break point:** the first 👎 that follows a 👍, or else the first 👎. The audit shows the question at the break point beside the preceding helpful answer.
- **Breakdowns:**
  - helpful % by **answer position** (1, 2, 3, 4, 5+) — the follow-up-question failure signal;
  - by topic, by retrieval route (labelled with `RETRIEVAL_MODE_LABEL`) and by language;
  - daily trend.
- **Timeline:** the full transcript via `db.get_conversation_transcript` (`database.py:1572`) merged with the feedback rows. Once the transcript has been purged, only the rated turns are shown, with the note "unrated turns are kept 7 days".
- Rows created before this change (no `conversation_id`) are grouped as single-answer "unlinked" items.
- The query is bounded at 5,000 feedback rows per period. Per the console's honest-numbers rule, the response says when the sample was truncated.

---

## Part C — Officer chat: the officer takes over, ends the chat, the taxpayer rates it

### C1. Backend — `App/backend/app/officer_chat.py` (new)
- **New table `ticket_messages`:**
  - columns `id, ticket_id, seq, sender (taxpayer|officer|system), text (redacted), text_localized, officer_id, officer_name, created_at, tenant_id`;
  - index on `(ticket_id, seq)`.
- **Officer messages** keep using the existing **PATCH `officer_reply`** route. It also appends an `officer` message (localized as it is today), and `officer_reply` stays as "latest reply" for `_deliver_officer_reply`, `export_preferences.py` and the Case Room.
- **Taxpayer messages:** `POST /v1/escalate/{id}/reply` (`main.py:2880`) also appends a `taxpayer` message. Once the chat has ended it returns **409**, so an ended chat is never silently re-opened. A new problem goes through a fresh escalation; `find_open_ticket` already matches only open or assigned tickets.
- **`chat_state(ticket)`:**
  - `waiting` (status open);
  - `officer` (assigned — an officer claimed it);
  - `ended` (`chat_ended_at` set, or resolved/wontfix);
  - `n/a` for voice tickets.
- **New ticket columns `resolved_by`, `resolved_by_name`, `chat_ended_at`, `csat_requested_at`.** Both backends need them, plus:
  - `postgres.py` `_TICKET_COLUMNS` / `_TICKET_COLUMNS_FULL` (1149-1157);
  - the SQLite `list_tickets` SELECT (1999-2005);
  - parity tests in `tests/agents/test_backend_column_parity.py`.
- **`end_chat(ticket, officer, closing_message, outcome)`**, used by both routes below:
  - appends the closing and system messages;
  - sets status `resolved` or `wontfix`, `resolved_by=ctx.user_id`, `resolved_by_name=_desk_officer_name(ctx)` (`main.py:4244`, the same identity the call desk uses), `chat_ended_at`, and `csat_requested_at` (text tickets only);
  - publishes `escalation.chat_ended` (`ticket_events.publish`);
  - audits `staff.chat_ended`.
- **Routes:**
  - New `POST /v1/admin/tickets/{ticket_id}/end-chat` with `{closing_message?, outcome}`: officer only; 409 if the chat has already ended.
  - PATCH with status → resolved/wontfix on a text ticket goes through the same `end_chat`, so the stepper and "Send and resolve" behave identically.
- **New `GET /v1/escalate/{ticket_id}/messages?after_seq=N`** for the taxpayer poll:
  - returns `{chat_state, officer_name, messages[], csat:{requested, submitted}}`;
  - marks the officer reply delivered;
  - **exact-id lookup only** — no fuzzy `list_tickets(q=…)` fallback like the existing GET at 2800.
- **New `POST /v1/escalate/{ticket_id}/csat`** with `{score 1-5 | null (skipped), comment?}`:
  - exact id;
  - 409 unless `csat_requested_at` is set;
  - 409 on a second submission;
  - writes `service_ratings` (C3).

### C2. Taxpayer frontend
- **New `src/hooks/useOfficerChat.ts`:**
  - polls `/messages` every 3 s while the chat is waiting or with an officer (10 s when the tab is hidden);
  - keeps `{ticketId, lastSeq, state}` per conversation in `useChatStore` (persisted, so a reload resumes without duplicate bubbles);
  - appends officer bubbles, reusing the existing officer turn shape (`agentRole:'human_officer'`, `retrievalMode:'officer_reply'`) and the `ChatMessage` officer styling;
  - appends system notices.
- **`src/app/page.tsx`:**
  - while `state === 'officer'`, the composer sends to `analyticsApi.replyToPublicTicket` instead of the AI, with placeholder "Message the officer…";
  - attachments, connectors and AI-only actions are disabled;
  - banner (1980-1993): "Chatting with URA officer {name}", or "Waiting for an officer — the AI can still help meanwhile";
  - notice when the officer joins: "Officer {name} has joined. Your messages now go to them."
- **On `ended`, a new `src/components/ServiceRatingDialog.tsx`** (mounted through the existing `ModalDialog`):
  - shows the shared **`src/components/rating/SatisfactionScale.tsx`** — 😠 Very dissatisfied · 🙁 Dissatisfied · 😐 Neutral · 🙂 Satisfied · 😄 Very satisfied, as a radiogroup with arrow keys and accessible names;
  - optional comment, Submit and Skip;
  - afterwards it posts the notice **"Your chat with the officer has ended. You're now chatting with the URA AI assistant again."** and clears `activeTicketId`.
- **`SupportCaseModal.tsx`:** keep it as the case-details view. Hide its composer while an officer is in the chat or after it has ended.
- **i18n:** all new taxpayer strings get keys in `lib/i18n/en.ts` / `lg.ts` / `sw.ts` (rating levels, notices, banner, placeholder).

### C3. `App/backend/app/service_ratings.py` (new) — one table for streams 2–4
- **Columns:**
  - `id, tenant_id, source, channel (chat|call), subject (officer|ai)`;
  - `status (answered|skipped|no_answer)`;
  - `score (1-5, nullable)`;
  - `sentiment (positive|neutral|negative|unclear)` — derived from the score for emoji ratings (1–2 negative, 3 neutral, 4–5 positive);
  - `comment` (redacted; for spoken reviews, the STT transcript);
  - `ticket_id, call_id, conversation_id, officer_id, officer_name, team, topic, locale, user_id, created_at`.
- **Unique key** `(source, ticket_id|call_id)`.
- **Sources:** `chat_officer_csat`, `call_officer_csat`, `call_ai_review_end`, `call_ai_review_transfer`.
- **Aggregates:**
  - average score;
  - **CSAT %** (share of 4–5);
  - **response rate** (answered ÷ asked);
  - 5-level distribution;
  - per officer (handled, rated, average, CSAT %, number of 1–2 ratings);
  - per language;
  - weekly trend.
- **Hook into retention and privacy:**
  - erasure (`delete_user_analytics` `database.py:1333`, `delete_user_cascade` 3789);
  - export (`export_user_data` 3707);
  - TTL lists (`database.py:1001`, `postgres.py:468`), kept as long as feedback (90 days);
  - `ticket_messages` is deleted together with its ticket (retention and erasure).

---

## Part D — Calls

### D1. The AI asks the caller for a review — `App/backend/app/receptionist/`
- **New `review.py` — `classify_spoken_review(text, language)`** returns positive, neutral, negative or unclear. It is deterministic (no LLM, no added latency):
  - en/lg/sw word lists, with negation checked first ("not helpful", "tekiyambye", "haikusaidia");
  - falls back to `clarify.classify_yes_no` (`clarify.py:62`) and `phrases.yes_no_patterns`.
- **New `CallState` fields** (`state.py`): `review_state ('' | asking_end | asking_transfer | done)` and `review_asked_at`. The review is asked at most once per call, and only if the AI answered at least one question.
- **Scenario 1 — the caller is satisfied and says goodbye.** At `brain.py:586-589`, instead of hanging up straight away:
  - say `review_ask_end` and set `asking_end`;
  - the caller's next turn is classified, stored (`call_ai_review_end`, verbatim redacted transcript) and recorded as a `voice_call_turns` row of kind `review`;
  - the AI then says `review_thanks_end` and ends through the existing `_end_call_after` (`brain.py:869`);
  - if the "answer" names a new task (`text_signals.names_a_task`), it is answered instead;
  - silence for `RECEPTIONIST_REVIEW_ANSWER_TIMEOUT_S` (10 s) → `no_answer` and the normal goodbye;
  - calls that end by being idle, by the hang-up button, or by an officer are never asked.
- **Scenario 2 — the caller asks for an officer.** In `brain._transfer` (`brain.py:1179`), when the AI has answered something:
  - the `transfer` line is replaced by `review_ask_transfer` and `asking_transfer` is set;
  - the transfer proceeds in parallel, so the review never delays the officer;
  - in the `transferring` branch of `handle_external_question` (`brain.py:~512`, which today records and returns), an `asking_transfer` answer is classified, stored (`call_ai_review_transfer`) and acknowledged with `review_thanks_transfer`;
  - if an officer bridges first, or the call ends, the call teardown (`ws.py:509-531`) records `no_answer`.
- **New `phrases.py` keys in en, lg and sw** (lg/sw drafted and marked "not yet native-reviewed" like the rest; `test_receptionist_phrases.py` enforces them; add to `prewarm_phrases`):

  | Key | English line |
  |---|---|
  | `review_ask_end` | "Before you go, was this call helpful to you?" |
  | `review_thanks_end` | "Thank you for your feedback. Goodbye." |
  | `review_ask_transfer` | "I'm connecting you to a URA officer now. While you wait, were my answers helpful so far?" |
  | `review_thanks_transfer` | "Thank you. Please stay on the line; an officer will be with you shortly." |

- **`config.py`:** `get_call_review_enabled()` (`RECEPTIONIST_CALL_REVIEW`, default on) and `get_review_answer_timeout_s()`. Environment config, not a registry flag.

### D2. The officer records caller satisfaction (staff dashboard)
- **`components/staff/calls/CallControls.tsx`:** the End-call confirm now reminds the officer: *"Before ending, ask: 'Were you satisfied with the help I gave you today?'"*
- **`components/staff/calls/WrapUpPanel.tsx`:**
  - new **required** field "Caller satisfaction (what the caller told you)", using `SatisfactionScale` plus a "Caller didn't say" option;
  - the existing AI-handling stars move into an optional "Rate the AI's handling before transfer" section, shown only for transferred calls. They still feed `avg_officer_rating`.
- **Backend:**
  - `WrapUpPayload` (`services/callsApi.ts`) and `POST /v1/admin/calls/{id}/wrapup` (`main.py:4376`) gain `caller_satisfaction` (1-5 or null) and `caller_satisfaction_status`;
  - `desk.wrapup` (`desk.py:445`) validates them and writes `service_ratings` (`call_officer_csat`, `officer_id`, `officer_name`, `team`, `call_id`, `ticket_id`).
  - No new route.

### D3. Officer workbench for chat (`/agent`) — needed for Part C
- **`components/staff/TicketCase.tsx`:**
  - render a live "Chat with taxpayer" thread from `ticket_messages` (returned by `GET /v1/admin/tickets/{id}`);
  - stop rendering `sender:"taxpayer"` transcript entries as empty "Assistant" lines (existing bug).
- **`components/staff/TicketComposer.tsx`:**
  - **End chat** button with a confirm (optional closing message; outcome Resolved / Closed without resolution);
  - "Send and resolve" becomes "Send & end chat".
- **`hooks/useTicketStream.ts:56`:** also handle `escalation.taxpayer_reply` and `escalation.chat_ended`, so the open case refreshes immediately instead of on the 5 s re-poll.

---

## Part E — The admin dashboard

### E1. `/admin` → "Service quality overview" (rewrite `src/app/admin/page.tsx`)
- **Header:**
  - eyebrow "Audit";
  - `PeriodPicker` and `Freshness`;
  - primary action **Open quality audit**, replacing "Open the full queue".
- **Row 1 — four `StatCard`s,** each with an exact period-over-period change computed with the 2N−N trick (`lib/trends.ts` `toDelta` / `previousWindow`), each linking to its tab:
  - "AI answers found helpful" (% of N rated);
  - "Callers satisfied with the AI" (% positive of answered spoken reviews);
  - "Officer chat satisfaction" (average /5 · CSAT %);
  - "Officer call satisfaction" (average /5 · CSAT %).
- **Main column — "Needs review" panel.** Replaces "Waiting longest". It lists the newest unreviewed items, for example:
  - "AI chat went wrong at answer 3 — 'How do I…'";
  - "Officer chat rated 🙁 2/5 — Officer Nakato";
  - "Caller said the AI wasn't helpful (while transferring)".

  Each opens the drawer on `/admin/quality`.
- **Side column:**
  - **Officers on duty** — the existing `SupervisorBoard`, read-only, moved here from `/calls`;
  - **Answer authority** (kept);
  - **Service levels** — median first response and resolution, past-SLA count; read-only numbers, no queue links.
- **Removed:** "Waiting longest", the queue deep links, the Unassigned and Awaiting tiles.

### E2. `/admin/quality` (new) — tabs in the URL (`?tab=&item=`, same pattern as `/calls`)

Each tab is built from `OpsPage` / `OpsPanel` / `StatCard` / `TableScroll` / `States`. Charts are Recharts loaded with `dynamic(..., {ssr:false})`, named in plain words through `chartTheme`, each with a `<ChartNote>` and a `ChartTable`. Lists offer a CSV export in the `auditEventsToCsv` style. Detail opens in a read-only side drawer with a **Review control** (Reviewed / Action needed + note).

- **AI answers · chat**
  - **KPIs:** rated answers; helpful %; conversations that went wrong (count and %); median answer number where it went wrong.
  - **Charts:**
    - "Helpful by position in the conversation" (bars for answer 1–5+, with *n*). Note: "Lower scores on later answers mean follow-up questions are being handled worse than first questions."
    - "Conversation outcomes": a stacked bar of the five patterns.
    - "Topics with the most unhelpful answers" (table).
  - **List:** a rating strip per conversation (`●▲▼` glyphs plus colour plus text, never colour alone), pattern chip, "went wrong at answer N", the question at that point, date and review status. Filters: pattern, topic, language, has comment, review status.
  - **Drawer timeline:** every turn's question and answer, rating, comment, retrieval route, sources and confidence. The break point gets a banner "Things went wrong here", with the previous helpful answer highlighted for comparison, and a link to create an answer override at `/admin/overrides`.
- **AI answers · calls (the general opinion, not call by call)**
  - **KPIs:** reviews asked; response rate; positive / neutral / negative %.
  - **Charts:** a grouped bar comparing **end of call vs while transferring**; weekly positive-% trend; a by-language table.
  - **"What callers said":** the latest negative and neutral verbatims (capped at 20, filterable). Each opens the call transcript drawer, which reuses `CallTranscript` / `CallSummaryCard` over `GET /v1/admin/calls/{id}`.
- **Officer chats / Officer calls** (shared `RatingsPanel`)
  - **KPIs:** average /5; CSAT %; response rate; count of 1–2 ratings.
  - **Charts and tables:** a 5-emoji distribution bar; a per-officer table (handled, rated, average, CSAT %, low ratings), sortable.
  - **Recent ratings list:** emoji, comment, officer, team, topic, date. It opens the chat thread (`GET /v1/admin/tickets/{id}` + messages) or the call transcript with its wrap-up note.
- **Needs review:** the full queue, filterable by type and status.
  - **What lands in it:**
    - AI chats that went wrong or were never helpful, or that have a 👎 with a comment;
    - officer chat or call ratings of 2 or below;
    - negative spoken AI reviews.
  - **Status:** tracked in the new `audit_reviews` table (columns `item_type, item_id, status, note, reviewer_id, reviewer_name, updated_at, tenant_id`, primary key `(item_type, item_id)`).
  - **Audit trail:** every change is written with `_audit_staff_action("staff.quality_reviewed")` (`main.py:2502`).
- **Frontend plumbing:**
  - types and methods in `services/analyticsApi.ts`;
  - keys `queryKeys.quality.*` in `lib/queryKeys.ts`;
  - hooks in `hooks/useQualityAudit.ts`;
  - components in `src/components/quality/` (`RatingStrip`, `ConversationTimeline`, `RatingsPanel`, `SatisfactionDistribution`, `PositionChart`, `NeedsReviewList`, `ReviewControl`);
  - plain-language labels `PATTERN_LABEL` and `SENTIMENT_LABEL` in `components/charts/chartTheme.tsx`.

---

## API surface (8 new HTTP routes: 113 → 121)

| Method & path | Who | Purpose |
|---|---|---|
| `GET /v1/admin/quality/summary?days=` | admin, auditor (`_require_audit_reader`) | 4 KPIs + previous window + top "needs review" items |
| `GET /v1/admin/quality/ai-chat?days=&pattern=&topic=&locale=&review=&limit=&offset=` | admin, auditor | Breakdowns + conversation page |
| `GET /v1/admin/quality/ai-chat/{conversation_id}` | admin, auditor | Timeline with break point |
| `GET /v1/admin/quality/ratings?source=&days=&score=&officer=&locale=&review=&limit=&offset=` | admin, auditor | Aggregates + list for streams 2–4 |
| `PUT /v1/admin/quality/reviews/{item_type}/{item_id}` | admin, auditor | Mark reviewed / action needed + note (audited) |
| `POST /v1/admin/tickets/{ticket_id}/end-chat` | `ura_staff` | End the officer chat, request CSAT |
| `GET /v1/escalate/{ticket_id}/messages?after_seq=` | taxpayer (ticket-id capability, exact id) | Live thread + chat state + CSAT state |
| `POST /v1/escalate/{ticket_id}/csat` | taxpayer (exact id, once, only after end) | Submit or skip the rating |

- The `/v1/admin/quality/*` routes live in a new `App/backend/app/routes/quality.py` `APIRouter`, following the `routes/connectors.py` pattern and registered in `main.py`. The other two families sit beside their siblings in `main.py`.
- Every route goes into `tests/test_all_endpoints_e2e.py`: `EXPECTED_ENDPOINTS`, `COVERAGE`, and `test_manifest_endpoint_count` updated to **121 HTTP + 7 WS**. Each gets a request model in `models.py`.
- Metric `service_rating_total{source,sentiment}` (bounded labels, through `analytics.metrics`).

---

## Implementation phases (one commit per phase on `dev`)

1. **Admin is read-only (Part A).**
   - Covers the role changes, server guards, removal of step-in, and moving `SupervisorBoard`; the overview gets a temporary layout.
   - Demo: an admin sees no live tools; the API answers 403.
2. **AI chat audit (Part B and the AI-chat tab).**
   - Covers `turn_id`, the feedback columns and snapshot, anonymous ratings, `quality_audit.py`, the summary / ai-chat routes and the `/admin/quality` shell.
3. **Officer live chat and CSAT (Parts C and D3, Officer chats tab).**
   - Covers `ticket_messages`, end-chat, taxpayer routing, the rating dialog, the back-to-AI notice and `service_ratings`.
4. **Calls (Parts D1–D2; AI calls and Officer calls tabs).**
   - Covers the spoken reviews, the classifier, phrases and the wrap-up satisfaction field.
5. **Needs review and overview (E1, `audit_reviews`, review control, CSV export).**
   - Also covers demo seed data in `App/backend/app/seed_prototype.py` (development only), the e2e specs, and docs: `docs/API_REFERENCE.md`, `docs/OPS_CONSOLE_REDESIGN.md`, the `App/frontend/AGENTS.md` boundaries, and an AGENTS.md invariant ("only `ura_staff` handles taxpayers live; admins audit"). Run the `refresh-docs` skill.

---

## Existing code to reuse (do not rebuild)
- **DDL and columns:** `_ensure_columns` + backend-agnostic `db.*` helpers (`receptionist/store.py:48-140`).
- **Identity, audit and access helpers:** `_audit_staff_action` (`main.py:2502`), `_require_audit_reader` (2496), `_desk_officer_name` (4244).
- **Transcript and officer-reply delivery:** `db.get_conversation_transcript` (`database.py:1572`), `_deliver_officer_reply` (`service.py:6333`), with delivery already de-duplicated by `mark_reply_delivered`.
- **Speech classification:** `clarify.classify_yes_no`, `phrases.yes_no_patterns`, `text_signals.names_a_task` / `detect_user_distress`.
- **Speaking and ending a call:** `brain._say_and_record`, `_end_call_after`, `desk.say_to_caller`.
- **Console building blocks:** `OpsPage` / `OpsPanel` / `StatCard` / `PeriodPicker` / `Freshness` / `TableScroll` / `Skeleton*` / `EmptyState` / `ErrorState`, `useSkeletonVisible`, `chartTheme` (`ChartNote`, `ChartTable`, `RETRIEVAL_MODE_LABEL`), `lib/trends.ts`, `lib/auditTrail.ts` CSV.
- **Calls and modals:** `SupervisorBoard`, `CallTranscript`, `CallSummaryCard`, `ModalDialog`.
- **Taxpayer ticket API:** `analyticsApi.publicTicketStatus` / `replyToPublicTicket`.

---

## Verification

**Backend:** `PYTHONPATH=App/backend python3 -m pytest App/backend/tests tests/agents tests/chaos -q` plus `tests/test_all_endpoints_e2e.py`.

New test files:

| File | Covers |
|---|---|
| `App/backend/tests/test_quality_audit.py` | Pattern classification and break point (👍👎, 👎👍, all 👍, all 👎, single), position buckets, timeline after the transcript is purged, tenant scope, truncation flag |
| `test_feedback_linkage.py` | `turn_id` round trip (REST and SSE), snapshot columns, anonymous rating stored without `user_id`, rate limit |
| `test_officer_chat.py` | Message append and `after_seq`, state transitions, end-chat roles (staff 200; admin and auditor 403), 409 on reply after end, CSAT once and only after end, exact-id only, erasure removes messages and ratings |
| `test_service_ratings.py` | Average, CSAT %, response rate, per-officer aggregates |
| `test_receptionist_review.py` | Classifier en/lg/sw with negation; asked only after at least one answer; goodbye → review → thanks → hang-up; transfer review recorded while transferring; officer bridges first → `no_answer`; config off disables it |

Existing tests to update:
- `test_receptionist_officer_actions.py` / `_claims.py`: wrap-up satisfaction; admin gets 403; step-in removed.
- `test_api_endpoints.py`: admin 403 on ticket PATCH.
- `test_privacy_compliance_306.py`.
- Column-parity tests.

**Frontend:** `cd App/frontend && bun run test && bun run lint && npx tsc --noEmit && bun run build`.

| Test | Covers |
|---|---|
| `adminOverview.test.tsx` | Rewritten for the new overview |
| `adminQuality.test.tsx` (new) | Tabs, filters, drawer, review control |
| `StaffNav.test.tsx` | Admin sees Quality audit and no My queue / All tickets / Calls |
| `SatisfactionScale.test.tsx` | Rating scale, including keyboard use |
| `ServiceRatingDialog.test.tsx` | Rating dialog |
| `useOfficerChat.test.ts` | State machine, polling, notices, no duplicates after reload |
| `FeedbackButtons.test.tsx` | Payload, no consent gate |
| `WrapUpPanel.test.tsx` | Caller satisfaction required |
| `agentPage.test.tsx` | End chat |

Existing guard tests to keep passing: the `plainLanguage` test for the new charts, and the `i18n` test (lg/sw keys must exist in English).

**Browser end-to-end tests (Playwright):**
- `staff-ui.spec.ts`:
  - admin gets the forbidden gate on `/agent`, `/admin/tickets` and `/calls`;
  - add `/admin/quality` to the overflow sweep.
- `a11y.spec.ts`: add `/admin/quality` to the route list (both themes, 0 serious issues).
- A new taxpayer spec: officer joins → messages flow → End chat → rating dialog → "back with the AI" notice.

**Manual demo** (`bash scripts/prototype.sh`, seeded data):
1. As an anonymous taxpayer, ask 3 questions and rate 👍 👍 👎. The admin's AI-chat tab shows "went wrong at answer 3" with the timeline.
2. Escalate, and have an officer claim the case on `/agent`. The taxpayer sees "Officer joined" and the AI is paused; exchange messages.
3. The officer clicks End chat. The taxpayer gets the 5-emoji dialog, picks 🙁, and sees the back-to-AI notice. The admin sees the rating under Officer chats and in Needs review.
4. Make a voice call: ask a question, then say "goodbye". The AI asks for a review; answer "yes, it was helpful". The AI-calls tab shows it.
5. Make another call and ask for an officer. The AI asks for a review while transferring. The officer takes the call, asks about satisfaction, ends it, and records the emoji in the wrap-up. The Officer-calls tab shows it.
6. Sign in as admin and confirm the API returns 403 when claiming a call or patching a ticket.

---

## Risks and notes
- **Existing fuzzy lookup.** `GET /v1/escalate/{id}` keeps its fuzzy `list_tickets(q=…)` fallback (`main.py:2800`) for "TIC-XXXX" reference lookups. The new escalate routes use exact ids only. I'll flag the fallback as a separate hardening item rather than change it here.
- **Anonymous feedback cannot be erased per person.** It carries no identifier, so there is nothing to erase it by. The privacy copy and docs say so.
- **One-worker limit.** The officer-chat poll (3 s) is HTTP, so it works across replicas. The call review lives in the in-memory `CallRoom`, under the same single-worker constraint the receptionist already has.
- **Turn ids start now.** Feedback recorded before Phase 2 has no conversation link and appears as "unlinked" single answers.

---

## Decision log

| Date | Phase | Outcome |
|---|---|---|
| 2026-10-13 | Phase 1 — admin is read-only | Done. `ura_admin` loses `/agent`, `/admin/tickets` and `/calls` (nav + page guards) and the server refuses its live writes: `_require_live_officer` on ticket PATCH and presence, `_desk_writer` staff-only for every call-desk write, officer audio socket staff-only. Supervisor step-in ("Take over") and every `is_admin` override in `receptionist/desk.py` are gone. Officers keep the clean-up the admin override used to give: ending a stale row (no room on this server) and terminating a waiting call no other officer has claimed; an ended call keeps its reason. The call layer, alerts and new-escalation banner are not mounted for admins. `/admin` drops "Waiting longest" and the queue links; tiles are figures; `OfficersOnDuty` (read-only presence) replaces the Supervisor tab. `/analytics` queue rows are links only for roles that can open the queue. |
| 2026-10-13 | Phase 2 — AI chat audit | Done. Every chat transport mints the answer's `turn_id` before generating (REST `ChatResponse.turn_id`, SSE `metadata.turn_id`, WS `metadata.turn_id` = `response_id`) and logs the turn under it (`log_conversation(row_id=…)`, both backends). `POST /v1/feedback` is open to anyone and rate-limited; the rater is recorded only with analytics consent. With a known `turn_id` the rating copies its conversation, position, logged question/reply, sources, topic and language (`quality_audit.rated_turn`); a `turn_id` from another conversation is not linked. New `feedback` columns `conversation_id, turn_id, turn_index, locale, topic_tag, sources_json, confidence` (+ `tenant_id` now written). `app/quality_audit.py` classifies each conversation (`went_wrong`, `never_helpful`, `recovered`, `all_helpful`) and finds its break point; repeat ratings of one answer count once; officer replies excluded; ≤5,000 ratings per period with `truncated`. Routes `GET /v1/admin/quality/summary`, `/ai-chat`, `/ai-chat/{conversation_id}` (admin + auditor; timeline audited as `staff.conversation_viewed`) → manifest 116 HTTP. Taxpayer UI: thumbs send conversation, turn and answer number, work without analytics consent (no session id then), are hidden on the greeting and officer replies, and are localized (en/lg/sw; lg/sw drafted, not yet native-reviewed). `/admin/quality`: KPIs with the previous period, helpful-by-position chart, pattern breakdown (filters the list), topics/routes tables, worst-first conversation list with rating strips, and a read-only timeline marking “Things went wrong here” and the last helpful answer before it. Nav gains an Audit section (Quality audit, Audit trail). |
| 2026-10-13 | Phase 3 — officer live chat and its rating | Done. **Deviation from C1:** the chat's state lives in a new `ticket_chats` table (officer, joined/ended, ended by, outcome, rating asked/answered) instead of new `tickets` columns, so neither backend's ticket functions or column lists change; `chat_state` is read from the ticket's own status (`open` → waiting, `assigned` → officer, `resolved`/`wontfix` → ended). `app/officer_chat.py` owns `ticket_messages` (seq per ticket, redacted) and `end_chat`; `app/service_ratings.py` owns `service_ratings` (one row per source and case, written when the rating is asked, so response rate is a count; 90-day retention; erasure and export by the case owner). PATCH `officer_reply` also appends to the thread, a self-assign takes the chat, and resolving/closing a live text chat ends it exactly as the new `POST /v1/admin/tickets/{id}/end-chat` does (officers only; `staff.chat_ended`; `escalation.chat_ended` on the staff stream). Taxpayer routes `GET /v1/escalate/{id}/messages` (exact id, `CHAT_POLL_RATE_LIMIT`) and `POST /v1/escalate/{id}/csat` (once, only after the end); `POST /v1/escalate/{id}/reply` adds to the thread and is 409 after an officer ended the chat. `GET /v1/admin/quality/ratings` + `officer_chat` in the summary → manifest 120 HTTP. Metric `ura_service_rating_total{source,sentiment}`. Taxpayer UI (`useOfficerChat`): polls 3 s/10 s hidden with back-off, holds while an answer streams, shows “Officer X has joined”, officer bubbles labelled by name, routes the composer to the officer (no files/connectors/voice mode), opens the 5-face rating once, then “You're now chatting with the URA AI assistant again”; sync state is kept per conversation and the open case now survives a reload (it did not before). Case Room hides its composer during a live chat. Officer UI: the chat as its own thread (legacy follow-ups no longer render as empty “Assistant” lines), End chat with a confirm and outcome, “Send & end chat”. `/admin/quality` gains tabs; **Officer chats**: average, satisfied share, response rate, low ratings, the five levels, a per-officer table (lowest first, filters the list), ratings lowest first, and the chat opened read-only (read once: each read is audited). Known gaps: a ticket the system raised itself (judge, tool) is not linked to the chat live — the reply still arrives on the next turn as before; axe flags two pre-existing contrast issues (faded copy buttons on older messages, the queued-handoff text) outside this change. |
| 2026-10-13 | Phase 4 — call reviews | Done. **The AI asks the caller** (`receptionist/review.py`): once a call, only after it has answered something (`ai_answers_count ≥ 1`), and only when `RECEPTIONIST_CALL_REVIEW` is on (default). At goodbye — “goodbye”, and now also a whole-turn “bye” / “that's all, thanks” — it asks `review_ask_end`, waits `RECEPTIONIST_REVIEW_ANSWER_TIMEOUT_S` (10 s, during which the idle check is suppressed), stores the answer and says `review_thanks_end`; a bare goodbye is a skip, silence is `no_answer`, a new question is answered and the review not asked again, an answer that also asks something is stored and the question answered, and a crisis always wins (the wait is dropped). For a caller who asked for an officer (`caller_requested` only — never crisis, safety or failure transfers) the transfer line becomes `review_ask_transfer` while the transfer proceeds; an answer is stored and thanked, and the officer bridging first, the transfer timing out or the call ending records `no_answer` (`desk.bridge`, `_transfer_timeout_countdown`, `CallRoom.end` and the `ws.py` teardown all settle; idempotent). The answer is classified without a model call (en/lg/sw words, negation first, then yes/no) and kept as the redacted transcript; the caller turn is recorded as kind `review` so `risk.py` does not read it as distress. New phrases in en/lg/sw (lg/sw drafted). **The officer records caller satisfaction** at wrap-up: required in the UI (five faces or “The caller didn't say”), `caller_satisfaction` / `caller_satisfaction_status` on `POST /v1/admin/calls/{id}/wrapup` → `call_officer_csat` under the acting officer and the call's team/topic/language; the AI-handling stars became optional and are offered only when the AI answered something. The End-call confirm reminds the officer to ask. **Audit:** `/admin/quality` has four tabs; **AI on calls** is the general opinion (asked, answered, said it helped / didn't, end vs. while transferring, by week, by language, and what callers said, unhappy first) with no per-call drill-down, as asked; **Officer calls** reuses the ratings panel and opens a call read-only (read once, audited `voice_staff_viewed_call`). `service_ratings` gained `by_language`, sentiment shares in the summary, week and previous period, sentiment ordering and a `sentiment` filter on `/ratings`; `/summary` gained `ai_calls` and `officer_calls`. No new routes (120 HTTP). |

# Plan: the officer's Call Desk — phone-call handling in the staff console

Status: **implementation completed — Phases 0, 1, 2, and 3 done (2026-09-25).** Written 2026-09-24 and re-checked against `dev` @
`e887627f` — i.e. **after** the multilingual receptionist landed (`docs/plans/multilingual-receptionist.md`:
language detection, `router.py`/`sentinel.py`, `phrases.py`, `transfer.py`). Line numbers below are
approximate; search for the named symbol if a line has moved.

This plan is for a fresh Claude Code session. It restates the current code (with file/line references),
the target officer experience, the exact backend and frontend work, the UI specification, and the tests.
Read it end to end before starting. Build in the phase order given; each phase ends in a demoable state.

---

## 0. Ground rules for the implementing session

- Branch from `dev`: `git checkout dev && git pull && git checkout -b feat/officer-call-desk`.
- Follow `AGENTS.md` and the nested `App/backend/AGENTS.md` and `App/frontend/AGENTS.md`. Invariants that
  apply here:
  - **Every new FastAPI route** goes into `tests/test_all_endpoints_e2e.py`: `EXPECTED_ENDPOINTS`,
    `COVERAGE`, and the locked counts in `test_manifest_endpoint_count` (currently **74 HTTP + 7 WS**;
    this plan adds **11 HTTP, 0 WS** → 85 + 7).
  - A new flag lands in `flags.py`, `docs/RAG_ARCHITECTURE.md` (table + count) and `.env.example`.
    This plan adds **no new flag** — everything sits under the existing `voice_receptionist` flag.
  - Every route into the officer queue honours `ticket_queue` (never promise a handoff when it is off).
  - Runtime code stays under `App/backend/app`. Do not move `App/backend` or `App/frontend`.
  - The staff console is English-only (no i18n keys needed for staff UI). Taxpayer-facing strings added
    to the caller's call screen **do** need `en/lg/sw` keys (coverage test ≥ 70%).
- **Broadcast rule (keep it):** the staff *lobby* channel carries metadata only — never transcript text,
  never a brief. Content goes out only on the per-call channel or per-call HTTP routes, which are staff-only
  and audited (`log_voice_event("staff_viewed_call", ...)` already exists in `receptionist/ws.py:245`).
- Do not commit or push unless the user asks. Record outcomes in §14 "Decision log".

---

## 1. Goal and principles

A URA officer should be able to:

1. See every live call at a glance, and open any one to follow its conversation live.
2. Be alerted to **any call the AI forwards to an officer, wherever they are in the console**.
3. Understand a waiting caller's request **in ~10 seconds** from an AI brief, without reading the transcript.
4. Take the call with one action, talk, put on hold, transfer, and end — without losing the call when they
   look something up elsewhere in the console.
5. Wrap up quickly with an AI-drafted note, and then be available again.
6. Find any past call and its conversation.

Design principles (these drive every UI decision below):

- **Time-to-context beats completeness.** The waiting caller's clock is the most important number on screen.
- **One source of truth for "who has this call".** Claims are server-side and first-come-first-served.
- **Calm by default, loud only when a human is needed.** AI-handled calls are ambient; a transfer is an event.
- **The call follows the officer.** Audio and controls survive navigation between console pages.
- **Looks like the rest of the ops console**, not like a demo: flat surfaces, hairlines, IBM Plex, colour
  used only for state. No gradients, glows, or decorative orbs on the staff side.

---

## 2. Current state (verified 2026-09-24)

### Backend (`App/backend/app/receptionist/`)

| Piece | Where | Notes |
|---|---|---|
| Call tables `voice_calls`, `voice_call_turns` | `store.py:17-66` | Created with `CREATE TABLE IF NOT EXISTS`; no column-migration helper yet. Turn text is **redacted on write** (`store.py:194-197`, `guardrails.redact_pii_text`) |
| Store API | `store.py` | `create_call`, `get_call`, `update_call`, `list_calls(status, limit, offset)` (`:145`, only `live`/`ended`/exact status), `create_turn`, `list_turns`, `save_call_review`, `get_call_with_turns` |
| Event hub | `hub.py` | `publish_lobby(event, payload)` (metadata only, `:40`), `publish_call(call_id, event, payload)` (`:76`), subscribe/unsubscribe for both. In-process (single uvicorn worker) |
| Staff socket | `ws.py:~226` `staff_calls_stream_endpoint` → `WS /v1/admin/calls/stream` (`main.py:1963`) | Roles `ura_staff`/`ura_admin`/`ura_auditor`. No `call_id` → lobby; `?call_id=` → snapshot + live turns, audited. Ping every 25 s |
| Officer audio | `ws.py:~283` `officer_audio_endpoint` → `WS /v1/admin/calls/{call_id}/audio` (`main.py:1971`) | Staff/admin only (4403 otherwise); call must be `ai`/`transferring`; **second officer is rejected with 4409** (`room.officer is not None`). On connect: `mode="bridged"`, `update_call(status="bridged", officer_id=...)`, ticket → `assigned`, lobby `call.bridged` |
| Officer leg | `officer.py` | Relays officer PCM to caller socket; transcribes officer speech into `officer` turns |
| HTTP routes | `main.py:2977-3045` | `GET /v1/admin/calls`, `GET /v1/admin/calls/metrics`, `GET /v1/admin/calls/{call_id}`, `POST /v1/admin/calls/{call_id}/review` (all `require_admin_access`) |
| Transfer (shared by both engines) | `transfer.py` `open_transfer(room, chat_model, reason, *, ticket_id, handoff, question)` — called from `brain.py` (cascaded) and `gemini_live.py` `transfer_officer_handler` (~line 503; Gemini Live is the demo default, `RECEPTIONIST_ENGINE=gemini_live` in `App/docker-compose.gpu-salt.yml`) | Builds the handoff packet (adds `language`), creates the ticket with keyword args, sets `mode`/`status="transferring"`, `transfer_requested_at` (in memory only), publishes lobby `call.transfer_requested` and per-call `status`. Each engine checks `ticket_queue` **before** calling it and runs its own 90 s timeout (`brain._transfer_timeout_countdown`, `gemini_live._gemini_transfer_timeout`) |
| Spoken lines | `phrases.py` `phrase(key, language, **fmt)`, `prewarm_phrases(language)` | Localised en/lg/sw lines for the receptionist; add every new officer-related line here |
| Language | `state.py` `locale` (current), `languages_used`, `language_source`; `router.py`, `sentinel.py` | The call's language can change mid-call; the lobby payload and the handoff packet already carry `language` |
| Summary | `summary.py:96` `generate_call_summary` | Runs **only after the call ends** (`ws.py:205`). Gemini `gemini-2.5-flash` → Sunflower `_vllm_generate` → deterministic fallback. Writes `summary_json`, appends a note to the ticket |
| Metrics | `metrics.py` | Per-call JSON + `get_aggregate_metrics(days)` |

### What the shared transfer path still lacks (Phase 0)

`open_transfer` fixed the earlier Gemini Live bugs (positional ticket call, no lobby event, no
`transferring` status, no timeout). What an officer still needs from it:

1. The lobby payload **hard-codes** `"topic": "General Tax Support"` and `"priority": "normal"` even though
   the handoff packet it just built has the real topic and priority.
2. Topic, priority and `transfer_requested_at` are **not persisted** on `voice_calls` (no columns), so lists
   and history can't show or sort by them, and wait time is lost on restart.
3. A timeout (no officer in 90 s) returns the call to the AI but leaves **no callback record**.
4. No brief is started at transfer time.
5. The two engines' timeout handlers duplicate the same logic.

### Frontend (`App/frontend/src/`)

| Piece | Where | Notes |
|---|---|---|
| Calls page | `app/calls/page.tsx` (252 lines) | Tabs Live / History / Performance Overview; list/detail split reusing `/agent` classes (`.ag-split`, `.ag-tabs`); 12 inline `style={{}}` |
| Components | `components/staff/calls/` | `CallRow`, `CallCase` (~16 inline styles; "Take call" for any non-auditor on any non-ended call), `CallTranscript`, `CallSummaryCard`, `CallMetricsCard` (now includes language metrics), `CallReviewForm`. Language badges already exist in `CallRow`/`CallCase` — keep them |
| Hooks | `hooks/useCalls.ts` | `useCalls(status)`, `useCall`, `useCallMetrics`, `useReviewCall`, `useCallsLobby` (`:61`, the "caller waiting" banner — **mounted only on `/calls`**), `useCallLive(callId)` (`:120`), `useOfficerAudio(callId)` (`:179`, opens the audio WS + mic + `PCMPlayer`) |
| API | `services/callsApi.ts` | Types `CallTurn`, `CallSummary`, `CallRecord`, `CallAggregates` |
| Console shell | `components/StaffGuard.tsx` | Render-prop guard **mounted per page** (every staff page wraps itself), owns one ticket socket (`useTicketStream`, `:337`) and `TicketLiveBanner` (`:413`). Nav uses `next/link` (client-side navigation) |
| Design system | `styles/ops/tokens.css`, `styles/ops/ops.css`, `components/ops/*` | Tokens `--ops-*` (type scale `--ops-text-*`, radii `--ops-radius-*`, surfaces `--ops-panel`, `--ops-row(-hover/-active)`, lines `--ops-hairline/-line`, roles `--ops-good/-warn/-bad/-info/-neutral` with `-ink/-fill/-line` variants, `--ops-focus`). Components: `OpsPage`, `OpsPanel`, `TableScroll`, `StatCard`, `Sparkline`, `Skeleton(Rows/Stats)`, `EmptyState`, `ErrorState`, `PeriodPicker`, `Freshness`, `Switch`, `KeyHint`, `CommandPalette`, `icons.tsx` |
| Tones | `services/callTones.ts` | `playDialTone`, `playJoinChime` (Web Audio) |
| Audio | `services/pcmPlayer.ts`, `services/audioLevelBus.ts`, `services/voiceService.ts` (`AudioRecorder.startStreaming`) | Reusable for the officer side |
| Tests | `src/__tests__/app/calls.test.tsx` | Page test pattern with `vi.spyOn(callsApi, ...)` |

### Consequences for the officer today

- An officer working on `/admin/tickets` or `/analytics` **never sees a transfer**.
- The summary exists only after hang-up, so at the moment of transfer the officer reads a raw transcript.
- Because `StaffGuard` is per page, **navigating away from `/calls` unmounts `useOfficerAudio` and drops
  the officer's audio** mid-call.
- History cannot be searched or filtered.

---

## 3. Target experience (what the officer sees)

### 3.1 Everywhere in the console — the call layer

A thin **Call bar** at the top of the console content area (above `TicketLiveBanner`):

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ ● Available ▾   │  📞 Live 4   ⏳ Waiting 1 · 0:42   │  🎧 Mic ready   🔔 Sound on   │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

- **Availability** menu: Available / Busy / Away (auto: On call, Wrap-up, Offline).
- **Waiting** counter turns `--ops-bad` and counts up while any caller waits; clicking it opens `/calls`
  with the longest-waiting call selected.
- **Mic ready**: opens the device check (see 3.6). **Sound** toggle for alert chimes.
- Hidden entirely for `ura_auditor`? No — auditors see counts but have no availability, alerts are silent.

**Transfer alerts** — a stack in the bottom-right corner of every staff page:

```
┌────────────────────────────────────────────────┐
│ ◔ 0:12   Caller waiting for an officer          │
│ VAT refund status                               │
│ Luganda · Asked for a person · Ticket 8f2c…     │
│ [ Take call  A ]   [ Preview ]        Dismiss    │
└────────────────────────────────────────────────┘
```

- The ring (`WaitRing`) drains over `RECEPTIONIST_TRANSFER_TIMEOUT_S` (90 s) and turns warn → bad.
- Chime (once, respects Sound toggle), browser Notification when the tab is hidden, and the tab title
  becomes `(1) Caller waiting — URA Console`.
- **Take call** claims the call (server-side, first wins). Everyone else's alert changes to
  "Taken by Officer Nakato" for 3 s and disappears.
- **Preview** opens `/calls?call=<id>` (brief + transcript) without claiming.
- **Dismiss** hides it for me only; the Waiting counter stays.
- More than two waiting → collapse to one card "3 callers waiting — longest 1:05 [Open queue]".
- Shown in full only to officers who are **Available** (and, once presence routing lands, who match the
  call's language/team). Others get the counter only.
- Content: topic, reason, language, priority, ticket ref, wait — **metadata only** (lobby rule).

**Active-call dock** — pinned at the bottom of every staff page while I'm on a call and not looking at it:

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ● On call 03:27 · VAT refund · Luganda   🎙 ▮▮▯  [Mute M] [Hold H] [End E] [Back to call] │
└──────────────────────────────────────────────────────────────────────────┘
```

During wrap-up it reads "Wrap-up pending · 00:48 [Finish wrap-up]".

### 3.2 `/calls` — the Call Desk

Tabs: **Desk** · **Callbacks (n)** · **History** · **Performance**. Desk is a three-pane layout:

```
┌───────────────┬─────────────────────────────────────────────┬───────────────────────┐
│ QUEUE          │ WORKSPACE (state-driven)                     │ CONTEXT (collapsible) │
│                │                                              │                       │
│ Waiting (1)    │  [brief card / live transcript / controls /  │ Caller                │
│ ◔ 0:42 VAT…    │   wrap-up — see 3.3]                         │  2 calls this week    │
│                │                                              │  1 open ticket        │
│ My call        │                                              │ Ticket                │
│ ● 03:27 VAT…   │                                              │  #8f2c · assigned     │
│                │                                              │ Ask the knowledge base│
│ With officers  │                                              │  [ question…      ]   │
│ ● Okello 05:10 │                                              │ Notes (private)       │
│                │                                              │  [ …              ]   │
│ AI handling (3)│                                              │                       │
│ ● 01:02 TIN…   │                                              │                       │
│ ▲ 04:40 PAYE…  │  ← "at risk" marker (Phase 3)                │                       │
└───────────────┴─────────────────────────────────────────────┴───────────────────────┘
```

- **Queue sections**, in this order: Waiting for an officer (sorted by priority, then longest wait),
  My call, With officers (name + time), AI handling (sorted by risk, then duration). Each item shows a
  live timer, topic (or first question when no topic yet), language badge, and state chip.
- **Selection** via `?call=<id>` (deep-linkable, like `useQueueView` in `lib/ticketUi.ts`); `J`/`K` move,
  `Enter` opens.
- Below 1100 px the Context pane collapses into a drawer; below 960 px Queue and Workspace alternate
  (same behaviour as `app/agent/agent.css` `.ag-split` / `.is-open`).

### 3.3 Workspace states

**A. Waiting call selected (before I take it):**

```
┌─ Caller waiting · 0:42 ◔ ─────────────────────────────── Luganda · High ─┐
│ WHY THEY NEED YOU  Wants refund status — needs account access the AI lacks │
│ ASKED              How to check a VAT refund filed in August           ↗7 │
│ AI ALREADY SAID    Refunds take up to 30 days; status is under e-Services ↗9│
│ STILL OPEN         Refund is 45 days old and not visible on the portal ↗12│
│ DETAILS GIVEN      TIN (given, redacted) · Business name · Filed 12 Aug ↗7 │
│ MOOD               Frustrated · 2nd call this week                         │
│ SAY FIRST          "I can see you've been waiting on your August VAT       │
│                     refund — let me look into that for you."    [Copy]     │
│ Updated 4 s ago · covers 14 turns · gemini-2.5-flash-lite   [↻ Refresh]    │
├────────────────────────────────────────────────────────────────────────────┤
│ [ Take call  A ]    Transcript (14 turns) ▾                                 │
└────────────────────────────────────────────────────────────────────────────┘
```

- `↗7` = evidence link: click scrolls the transcript to turn 7 and flashes it (`--ops-info-fill`, 1.2 s).
- The transcript is collapsed by default in this state; expanding shows the full live thread.

**B. AI-handling call selected:** live transcript (auto-scroll with "Jump to latest"), current brief
collapsed to one line, risk chips (Phase 3), and **Take over** (claims without waiting for the AI to
transfer; the AI tells the caller an officer is joining).

**C. My active call:**

```
┌ On call 03:27 · VAT refund · Luganda ────────────── You 🎙▮▮▯  Caller 🔊▮▯▯ ┐
│ Brief: Wants refund status…  [expand ▾]                                      │
├──────────────────────────────────────────────────────────────────────────────┤
│  live transcript — caller (left), assistant (right, muted), you (right, accent)│
├──────────────────────────────────────────────────────────────────────────────┤
│ [🎙 Mute M]  [⏸ Hold H]  [⇄ Transfer T]                     [⏹ End call E] │
└──────────────────────────────────────────────────────────────────────────────┘
```

- Hold: caller hears a hold tone; my mic is not relayed; a hold timer shows.
- Transfer: dialog → choose a team or an available officer (presence list), optional note → the call
  returns to Waiting with `target_team`, my note is appended to the brief, I become Wrap-up. Teams come
  from `escalation_notify._DEFAULT_TEAMS` values (`disputes`, `taxpayer_accounts`, `customs`,
  `registration`, `general`; overridable via `ESCALATION_TEAM_<TOPIC>`) — expose them read-only through
  `GET /v1/admin/officers/presence` as a `teams` list so the dialog doesn't hard-code them.
- End: confirm popover (not a modal) → caller hears a closing line → I move to Wrap-up.

**D. Wrap-up (after I end or transfer):**

```
┌ Wrap-up · 00:48 ──────────────────────────────────────────────────────────────┐
│ Call note (drafted by AI — edit before saving)                                 │
│ ┌──────────────────────────────────────────────────────────────────────────┐ │
│ │ Taxpayer asked about an August VAT refund not visible on the portal…      │ │
│ └──────────────────────────────────────────────────────────────────────────┘ │
│ Outcome  ( ) Resolved  ( ) Follow-up needed  ( ) Callback  ( ) Referred to team │
│ Ticket   #8f2c  [ Resolve ▾ ]   (or: Create ticket)                            │
│ How did the AI handle this call?  ★★★★☆  [note…]        (existing review form)│
│                                          [ Save & become available  ⌘↵ ]       │
└────────────────────────────────────────────────────────────────────────────────┘
```

**E. Ended call (from History):** summary first (`CallSummaryCard`), then outcome, officer, wrap-up note,
metrics, then the timestamped transcript with in-transcript search highlighting.

### 3.4 Callbacks tab

Calls where no officer answered in time, where the caller hung up while waiting, or where the outcome was
"Callback": reason, waiting time, brief, ticket link, **Mark done** (records who/when). Sorted oldest first.
The tab badge shows the open count.

### 3.5 History tab

`TableScroll` table: Started · Duration · Language · Topic · Outcome · Officer · Ticket · Rating.
Filter bar: free-text search (debounced 300 ms; matches transcript text and summary), date range
(`PeriodPicker`), outcome, language, officer, "has ticket". Server-side pagination (25/page) with total
count. Row click opens the call in the Workspace (state E). `/` focuses search.

### 3.6 Device check (shift start)

Dialog from the Call bar: requests the microphone once, shows a live input meter, a "Play test sound"
button, and remembers readiness for the session. If I press Take call without a ready mic, the call is
claimed first (so the caller is held) and the permission prompt follows.

### 3.7 Keyboard

`A` take the top waiting call (or the selected one) · `M` mute · `H` hold/resume · `T` transfer ·
`E` end (with confirm) · `J`/`K` move in queue · `Enter` open · `/` search history · `?` shortcut sheet ·
`Esc` close popovers. Use the existing `KeyHint` component and the `useQueueHotkeys` pattern
(`lib/ticketUi.ts:246`); ignore keys while typing in inputs.

---

## 4. Scenario coverage

| # | Scenario | Behaviour |
|---|---|---|
| 1 | Officer on any page, AI transfers a call | Alert stack + chime + title badge + browser notification if hidden; Waiting counter |
| 2 | Two officers press Take call together | `POST …/claim` is atomic; winner connects; loser gets 409 → alert shows "Taken by X" |
| 3 | Claimed but audio never connects (mic denied, tab closed) | Claim expires after `RECEPTIONIST_CLAIM_TIMEOUT_S` (20 s) → `call.unclaimed` → back to Waiting |
| 4 | Nobody takes it within 90 s | Caller hears callback line; `needs_callback=1`, reason `no_officer_available`; lobby `call.transfer_timed_out`; appears in Callbacks |
| 5 | Caller hangs up while waiting | `needs_callback=1`, reason `caller_left_waiting`; alerts close with "Caller left" |
| 6 | Officer's connection drops mid-call | Caller hears "please hold, reconnecting you"; status back to `transferring` with `reconnecting_officer`; same officer can rejoin within `RECEPTIONIST_OFFICER_RECONNECT_GRACE_S` (30 s), else claim released to Waiting with a "previous officer disconnected" note |
| 7 | Several callers waiting | Queue ordering priority → wait; alerts collapse into one card |
| 8 | Luganda / Swahili caller | Language badge on alert, queue, brief; Luganda summaries via Sunflower, in English |
| 9 | Officer needs a specialist | Transfer → team/officer; brief + note travel; target sees "Transferred by Okello: …" |
| 10 | AI call going badly (not yet transferred) | Phase 3 risk marker; officer can Take over proactively |
| 11 | Returning caller | Context pane lists previous calls and tickets for the same account |
| 12 | Officer navigates to Tickets during a call | Audio continues (session lives outside React); Active-call dock on every page |
| 13 | Officer ends call | Closing line to caller; Wrap-up; outcome + note saved; ticket updated; back to Available |
| 14 | Auditor | Sees everything read-only; no availability, no claim/controls, silent alerts |
| 15 | Officer is Busy/Away | No alert cards; counter only |
| 16 | `ticket_queue` flag off | No transfer promise; the AI says officers are unavailable; nothing enters the queue |
| 17 | Browser refresh mid-call | Warn on `beforeunload` while on a call; after reload, the claim is still mine → "Rejoin call" button in the dock |

---

## 5. Data model changes (backend)

Add a column-migration helper to `receptionist/store.py`: `_ensure_columns(table, {name: ddl})` that runs
`ALTER TABLE <t> ADD COLUMN <name> <ddl>` and ignores "duplicate column" errors (SQLite) — on Postgres use
`ADD COLUMN IF NOT EXISTS` (check `db.ANALYTICS_BACKEND` / follow `database._ensure_column` and
`postgres.py:294-321`). Call it from `init_receptionist_schema()`.

### New `voice_calls` columns

| Column | Type | Purpose |
|---|---|---|
| `topic` | TEXT default `''` | From the handoff packet / brief |
| `priority` | TEXT default `'normal'` | `low\|normal\|high\|urgent` |
| `transfer_requested_at` | DOUBLE | Start of the officer wait |
| `target_team` | TEXT default `''` | For transfers to a team |
| `claimed_by` | TEXT default `''` | Officer user id holding the claim |
| `claimed_at` | DOUBLE | Claim expiry baseline |
| `bridged_at` | DOUBLE | Officer connected |
| `hold_started_at` | DOUBLE | Non-null while on hold |
| `hold_total_s` | DOUBLE default 0 | Accumulated hold time |
| `outcome` | TEXT default `''` | `resolved\|follow_up\|callback\|referred\|abandoned\|ai_resolved` |
| `wrapup_note` | TEXT | Officer's final note |
| `wrapup_at` | DOUBLE | |
| `needs_callback` | INTEGER default 0 | |
| `callback_reason` | TEXT default `''` | `no_officer_available\|caller_left_waiting\|officer_outcome` |
| `callback_done_at` | DOUBLE | |
| `callback_done_by` | TEXT | |
| `brief_json` | TEXT | Latest brief (§6.2) |
| `brief_updated_at` | DOUBLE | |
| `risk_json` | TEXT | Phase 3 |

Indexes: `(needs_callback, callback_done_at)`, `(claimed_by)`, `(outcome)`.
Extend the `CallRecord` TypeScript type accordingly.

### New table `officer_presence`

```sql
CREATE TABLE IF NOT EXISTS officer_presence (
    user_id         TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'offline',  -- available|busy|away|on_call|wrap_up|offline
    languages_json  TEXT NOT NULL DEFAULT '["en"]',
    teams_json      TEXT NOT NULL DEFAULT '[]',
    current_call_id TEXT NOT NULL DEFAULT '',
    last_seen       DOUBLE PRECISION NOT NULL,
    updated_at      DOUBLE PRECISION NOT NULL
);
```

An officer is **offline** when `now - last_seen > RECEPTIONIST_PRESENCE_TTL_S` (60 s), computed on read.
Status transitions are automatic on claim (`on_call`), end/transfer (`wrap_up`) and wrap-up save
(back to the status before the call, default `available`).

### Tickets

`database.list_tickets` (`database.py:1509`) has no `user_id` filter — add `user_id: str | None = None`
to it **and** its Postgres mirror in `postgres.py`, for caller history.

---

## 6. Backend work

### 6.1 Extend the shared transfer path — `receptionist/transfer.py` (Phase 0)

`open_transfer` already exists and both engines call it. Extend it rather than replacing it:

1. Keep the packet it builds and use it: publish `topic` and `priority` **from the packet** (fallbacks
   `"general_tax_support"` / `"normal"`), plus `waiting_since`, `target_team`, `attempt`, and `language`
   (already there) — full payload in §7.
2. Persist `topic`, `priority`, `transfer_requested_at` (new columns, §5) in the same `update_call`.
3. Kick off `brief.build_now(call_id)` (6.2) in the background.
4. Add `close_transfer_on_timeout(room, ticket_ref)` here and call it from **both** existing timeout handlers
   (`brain._transfer_timeout_countdown`, `gemini_live._gemini_transfer_timeout`) so the shared part lives in
   one place: set `needs_callback=1`, `callback_reason="no_officer_available"`, publish lobby
   `call.transfer_timed_out`. Each engine keeps speaking its own line (through `phrases.phrase(...)`).
5. Keep the rule that callers check `ticket_queue` before calling `open_transfer`; add a test that asserts
   both engines do.

Do not change the engines' spoken behaviour or the `(ticket_id, status_event)` return shape — the
multilingual router and tests depend on them.

### 6.2 Rolling brief — `receptionist/brief.py` (Phase 1)

- `BriefScheduler` attached to the room. Turns are created from several places (`brain.py`, `router.py`,
  `gemini_live.py` `GeminiLiveTranscriptTap`, `officer.py`), all through `store.create_turn` — so add a
  tiny observer registry in `store.py` (`register_turn_observer(call_id, fn)` / `unregister…`) that
  `create_turn` notifies after writing, and have the scheduler subscribe per call. No call site changes. Every
  `RECEPTIONIST_BRIEF_EVERY_TURNS` (3) caller turns, schedule an **incremental** rebuild (debounced; at most
  one in flight per call). `build_now(call_id)` forces one (used on transfer and by `?refresh=1`).
- Incremental prompt: previous brief JSON + turns after `turns_covered`. Full rebuild when there is no
  previous brief.
- Output schema (strict JSON; parse like `summary._parse_summary_json`):

```json
{
  "why_officer":      {"text": "…", "turn_seqs": [12]},
  "caller_goal":      {"text": "…", "turn_seqs": [3]},
  "ai_already_said":  [{"text": "…", "turn_seqs": [9]}],
  "still_open":       [{"text": "…", "turn_seqs": [12]}],
  "details_given":    [{"label": "TIN", "value": "given (redacted)", "turn_seqs": [7]}],
  "sentiment":        "calm|confused|frustrated|distressed",
  "topic":            "VAT refund",
  "priority":         "low|normal|high|urgent",
  "suggested_opener": "…",
  "language":         "en|lg|sw",
  "turns_covered":    14,
  "generated_at":     1727170000.0,
  "model":            "gemini-2.5-flash-lite",
  "fallback":         false
}
```

- Every claim carries `turn_seqs` (evidence links). Validate that each seq exists; drop invalid ones.
- Models: English/Swahili calls → `providers.gateway.gemini_generate(..., model=RECEPTIONIST_BRIEF_MODEL,
  temperature=0, max_tokens=1500, locale="en")` respecting the gateway's gating; Luganda calls →
  `llm._vllm_generate` (Sunflower) **instructed to write English**; fallback → deterministic brief from the
  handoff packet (`fallback: true`). Input transcript lines are already redacted; never re-introduce
  identifiers (details are labels + "given (redacted)").
- Persist to `brief_json` / `brief_updated_at`; publish per-call event `brief` (content) and lobby
  `call.brief_ready {call_id, topic, priority}` (metadata only) the first time after a transfer.
- Latency target: brief available ≤ 2 s after `call.transfer_requested` p95 (usually instant because the
  rolling brief is already fresh).
- Also use the brief to draft the **wrap-up note**: `draft_wrapup_note(call_id)` = brief + officer turns →
  3–5 sentence English note.

### 6.3 Claims and officer actions (Phase 1–2)

Keep one `asyncio.Lock` per `CallRoom` (`state.py`) for all officer state changes. DB writes use
conditional updates so a claim is atomic even if two requests race:
`UPDATE voice_calls SET claimed_by=?, claimed_at=? WHERE call_id=? AND (claimed_by='' OR claimed_by IS NULL OR claimed_at < ?)`
and check the affected row count.

| Route | Who | Behaviour |
|---|---|---|
| `POST /v1/admin/calls/{call_id}/claim` | staff/admin | Call must be `transferring` or `ai` (take-over). Wins → 200 `{claimed:true, claim_expires_at}`, presence → `on_call`, lobby `call.claimed {call_id, officer_id, officer_name}`. Loses → **409** `{claimed_by, officer_name}`. Starts claim-expiry timer |
| `POST /v1/admin/calls/{call_id}/release` | claimant/admin | Gives the call back to Waiting (`call.unclaimed`) |
| `POST /v1/admin/calls/{call_id}/hold` `{on: bool}` | claimant | Sets/clears `hold_started_at`, accumulates `hold_total_s`; officer leg stops relaying officer audio while held; caller gets `{"type":"status","status":"on_hold"}` / back to `bridged`; lobby `call.on_hold` |
| `POST /v1/admin/calls/{call_id}/transfer` `{team?, officer_id?, note?}` | claimant | Closes my audio leg, appends note to the brief (`transfer_notes`), sets `target_team`, status `transferring`, new `transfer_requested_at`, lobby `call.transfer_requested` with `attempt += 1, from_officer`; my presence → `wrap_up` |
| `POST /v1/admin/calls/{call_id}/end` | claimant | Caller hears a closing line (localised), then the call ends (`end_reason="officer_ended"`); my presence → `wrap_up`; the draft wrap-up note is generated |
| `POST /v1/admin/calls/{call_id}/wrapup` `{outcome, note, ticket_action?: "resolve"\|"keep_open"\|"create", rating?, rating_note?}` | claimant | Saves `outcome`, `wrapup_note`, `wrapup_at`; applies ticket action via `db.update_ticket` / `create_ticket`; optional AI rating via `save_call_review`; `outcome=="callback"` sets `needs_callback`; presence back to the pre-call status; lobby `call.wrapped_up` |
| `POST /v1/admin/calls/{call_id}/callback-done` `{note?}` | staff/admin | Sets `callback_done_at/by` |

All routes: validate `call_id` with `^[a-zA-Z0-9_-]{1,64}$` (same as `main.py:3014`), `require_admin_access`,
and reject `ura_auditor` with 403 for write actions. Every action writes `log_voice_event` for audit.

**Spoken lines.** Every new line the caller hears goes into `phrases.py` in `en`, `lg` and `sw`, and into
`prewarm_phrases`' list where it has no placeholders: `officer_joining` ("You're now connected to Officer
{name}"), `officer_reconnecting` ("Please hold, I'm reconnecting you"), `on_hold`, `officer_closing`
("Thank you for calling URA, goodbye"). Have the `lg`/`sw` versions checked by native speakers, as the
multilingual plan requires. Speak them in the call's **current** language (`room.state.locale`).

**Audio WS change** (`ws.py` `officer_audio_endpoint`): require `room.state.claimed_by == user_id`
(else 4409). On connect, **before** bridging: synthesise `phrase("officer_joining", room.state.locale,
name=...)` with `SpeechModel.synthesize`, send the PCM to `room.caller_ws`, then set
`bridged`, `bridged_at`, clear the claim-expiry timer. On unexpected close while bridged → scenario 6
(grace period, `call.officer_disconnected`, caller "please hold" line, re-queue after grace).

**Caller leaves while waiting** (`ws.py` caller disconnect handler): if `mode=="transferring"` →
`needs_callback=1, callback_reason="caller_left_waiting", outcome="abandoned"`; publish `call.ended` with
`abandoned_while_waiting: true`.

### 6.4 Presence (Phase 2)

| Route | Behaviour |
|---|---|
| `PUT /v1/admin/officers/me/presence` `{status?, display_name?, languages?, teams?}` | Upsert + `last_seen=now`. Called on status change and as a heartbeat every 20 s by the console |
| `GET /v1/admin/officers/presence` | `{officers: [...with computed offline], teams: [...]}` — used by the Transfer dialog and the supervisor view. `teams` = the values of `escalation_notify._DEFAULT_TEAMS` after env overrides |

Lobby event `officer.presence {user_id, display_name, status}` on changes (no heartbeat spam).
`call.transfer_requested` carries `language` and `target_team`; the client decides whether to show a full
alert (available + matching language/team) or only bump the counter.

### 6.5 History, callbacks, caller history (Phase 2)

- Extend `GET /v1/admin/calls` (no new route) with `q`, `date_from`, `date_to`, `outcome`, `language`,
  `officer_id`, `has_ticket`, `topic`, `needs_callback`, `sort` (`started_desc` default), and return
  `total` alongside `calls`. `q` matches `voice_call_turns.text` (via `EXISTS` subquery with `LIKE`) and
  `summary_json`. Parameterise everything.
- `GET /v1/admin/calls/{call_id}/caller-history` → `{calls: [...last 10 by same user_id, excluding this],
  tickets: [...via list_tickets(user_id=...)]}`; anonymous callers (`user_id` empty or `anon::`) →
  `{anonymous: true}`.
- `GET /v1/admin/calls/{call_id}/brief` (`?refresh=1` forces `build_now`) → the brief, or 202 with
  `{status:"building"}` if none yet.

### 6.6 Risk signals (Phase 3)

`receptionist/risk.py`: recompute on each caller turn from signals — ≥ 2 clarifications, repeated
question (normalised similarity ≥ 0.8 with a previous caller turn), distress
(`text_signals.detect_user_distress`, `text_signals.py:262`, or the brief's `sentiment`), mean word probability < 0.5 twice, abstentions, duration > 6 min. Level
`none|watch|at_risk`. Persist `risk_json`; publish lobby `call.risk {call_id, level, signals}` on change
(signal names only).

### 6.7 Metrics (Phase 2–3)

Extend `metrics.py` per call: `time_to_answer_s` (transfer → bridged), `handle_time_s` (bridged → end),
`hold_total_s`, `wrapup_time_s` (end → wrap-up saved), `abandoned_while_waiting`, `callback_created`.
Aggregates: median/p90 time-to-answer, median handle time, abandonment rate, callbacks created/closed,
per-officer counts. Show on the Performance tab.

### 6.8 Caller side (small)

`App/frontend/src/hooks/useCall.ts` + `CallScreen.tsx` + `services/callTones.ts`:
- Handle `status: "on_hold"` → "On hold" state + looping `playHoldTone()` (new, soft, low volume).
- Handle `status: "reconnecting"` → "Reconnecting you to the officer…".
- New i18n keys (`call.onHold`, `call.reconnecting`) in `en/lg/sw`.

---

## 7. Event and API contract

### Lobby events (`WS /v1/admin/calls/stream`, metadata only)

| Event | Payload |
|---|---|
| `call.started` | `{call_id, started_at, language, channel}` |
| `call.status` | `{call_id, status}` |
| `call.transfer_requested` | `{call_id, reason, topic, priority, language, ticket_ref, waiting_since, target_team, attempt, from_officer?}` |
| `call.brief_ready` | `{call_id, topic, priority}` |
| `call.claimed` / `call.unclaimed` | `{call_id, officer_id, officer_name}` / `{call_id, reason}` |
| `call.bridged` | `{call_id, officer_id, officer_name}` |
| `call.on_hold` | `{call_id, on}` |
| `call.officer_disconnected` | `{call_id, officer_id, grace_s}` |
| `call.transfer_timed_out` | `{call_id, ticket_ref}` |
| `call.ended` | `{call_id, end_reason, abandoned_while_waiting}` |
| `call.wrapped_up` | `{call_id, outcome, officer_id}` |
| `call.risk` (Phase 3) | `{call_id, level, signals}` |
| `officer.presence` | `{user_id, display_name, status}` |

### Per-call channel (`?call_id=`, audited)

Existing `snapshot`, `turn`, `caption`, `status`, plus `brief` (full brief JSON) and `hold`.

### New HTTP routes (11) — add all to `tests/test_all_endpoints_e2e.py`

`GET …/{call_id}/brief` · `POST …/{call_id}/claim` · `POST …/{call_id}/release` · `POST …/{call_id}/hold` ·
`POST …/{call_id}/transfer` · `POST …/{call_id}/end` · `POST …/{call_id}/wrapup` ·
`POST …/{call_id}/callback-done` · `GET …/{call_id}/caller-history` (all under `/v1/admin/calls`) ·
`PUT /v1/admin/officers/me/presence` · `GET /v1/admin/officers/presence`.
Update `test_manifest_endpoint_count` to **85 HTTP + 7 WS**.

### Environment variables (document in `.env.example`, read in `receptionist/config.py`)

`RECEPTIONIST_BRIEF_EVERY_TURNS=3` · `RECEPTIONIST_BRIEF_MODEL=gemini-2.5-flash-lite` ·
`RECEPTIONIST_CLAIM_TIMEOUT_S=20` · `RECEPTIONIST_OFFICER_RECONNECT_GRACE_S=30` ·
`RECEPTIONIST_PRESENCE_TTL_S=60` · `RECEPTIONIST_RISK_ENABLED=false` (Phase 3).
`RECEPTIONIST_TRANSFER_TIMEOUT_S` (90) already exists.

---

## 8. Frontend architecture

### 8.1 State and services that live outside React (so the call survives navigation)

`StaffGuard` is mounted per page, so anything owned by a page component dies on navigation. Move
call-console state into module singletons + a Zustand store:

| File | Responsibility |
|---|---|
| `services/callLobbySocket.ts` | Ref-counted singleton for `WS /v1/admin/calls/stream` (lobby). Reconnect with backoff; **stop for the session on close codes 1001/4401/4403** (flag off / not staff). On (re)connect, resync via `GET /v1/admin/calls?status=live` |
| `services/officerCallSession.ts` | Singleton owning the officer audio WS, `AudioRecorder.startStreaming`, `PCMPlayer`, mute, and levels (reuse `audioLevelBus`: input = my mic, output = caller). State machine: `idle → claiming → connecting → bridged ⇄ on_hold → wrap_up → idle` (+ `reconnecting`). Exposes `claim`, `join`, `mute`, `hold`, `transfer`, `end`, `rejoin`. Adds a `beforeunload` warning while not idle |
| `store/useCallConsoleStore.ts` | Zustand (not persisted): `calls` (map of lobby metadata), `alerts` (active/dismissed ids), `presence` (mine), `activeCall` (id, state, timers, muted, onHold), `soundEnabled`, `notificationPermission`, `micReady` |
| `hooks/useNow.ts` | One shared 1 s ticker for every timer and wait ring (no per-row intervals) |

### 8.2 Console-wide components (mounted inside `StaffGuard`, above `TicketLiveBanner`)

`components/staff/calls/console/`:
- `StaffCallLayer.tsx` — mounts the three below; renders nothing if the lobby socket was disabled.
- `CallConsoleBar.tsx` — availability menu, Live/Waiting counters, mic-ready, sound toggle.
- `CallAlertStack.tsx` + `CallAlertToast.tsx` + `WaitRing.tsx` — alerts (3.1), chime via new
  `playAlertChime()` in `services/callTones.ts`, browser `Notification` (permission asked from the bar,
  never on load), `document.title` badge.
- `ActiveCallDock.tsx` — the pinned mini bar (3.1).
- `DeviceCheckDialog.tsx` — mic permission + level meter + test sound (3.6).
- `usePresenceHeartbeat.ts` — `PUT …/officers/me/presence` every 20 s while the tab is visible.

`StaffGuard.tsx` change: render `<StaffCallLayer who={who} />` inside `.staff-shell-content` before
`<TicketLiveBanner …/>`. Keep the "one socket per console" intent: the lobby socket is a singleton, so
per-page remounts do not open a second one.

### 8.3 `/calls` page

Rewrite `app/calls/page.tsx` as `CallDesk` with tabs Desk / Callbacks / History / Performance, using
`OpsPage` and the `?call=` / `?tab=` URL state (pattern: `useQueueView` in `lib/ticketUi.ts:198`).

New/changed components in `components/staff/calls/`:

| Component | Replaces / reuses |
|---|---|
| `CallQueue.tsx` + `CallQueueItem.tsx` | Replaces `CallRow` (keep `QueueRow` visual language) |
| `CallWorkspace.tsx` | Replaces `CallCase`; switches on state A–E (3.3) |
| `CallBriefCard.tsx` | New; evidence links scroll `CallTranscript` to `#turn-{seq}` and flash |
| `CallControls.tsx` | New; mute/hold/transfer/end + level meters |
| `TransferDialog.tsx` | New; teams + available officers (presence) + note |
| `WrapUpPanel.tsx` | New; reuses `CallReviewForm` for the AI rating |
| `CallContextRail.tsx` | New; `CallerHistoryPanel`, `CallTicketPanel`, `KnowledgeAssistPanel`, `CallNotesPanel` |
| `CallbacksList.tsx` | New |
| `CallHistoryTable.tsx` + `CallHistoryFilters.tsx` | New; `TableScroll`, `PeriodPicker` |
| `CallTranscript.tsx` | Keep; add `id="turn-{seq}"` anchors, relative timestamps, search highlight, officer bubble style |
| `CallSummaryCard.tsx`, `CallMetricsCard.tsx`, `CallReviewForm.tsx` | Keep |

`KnowledgeAssistPanel`: sends the officer's question to the existing chat endpoint (`POST /v1/chat`) with
`conversation_id = "officer-kb-<call_id>"`; show the answer with sources. Check whether analytics should
exclude the `officer-kb-` prefix and add that filter if the dashboard counts it as taxpayer traffic.

Hooks (`hooks/useCalls.ts`, extend): `useCallBrief(callId)` (query + live `brief` events),
`useCallActions()` (claim/release/hold/transfer/end/wrapup/callback-done mutations with optimistic UI and
409 rollback), `useCallHistory(filters)`, `useCallerHistory(callId)`, `useOfficerPresence()`,
`useCallHotkeys()`. Remove `useOfficerAudio` (replaced by `officerCallSession`) and move `useCallsLobby`
logic into the lobby singleton + store.

`services/callsApi.ts`: new functions for every route in §7 and extended types (`CallBrief`, `Presence`,
`CallRecord` new fields, `CallHistoryResponse {calls, total}`).

---

## 9. UI specification (theme, interaction, accessibility)

**Look.** Use only `--ops-*` tokens and `components/ops/*`. Flat `--ops-panel` surfaces with
`--ops-hairline` borders, `--ops-radius-md` on panels and `--ops-radius-pill` on chips, IBM Plex
(`--ops-font-sans`, numbers in `--ops-font-mono` with `tabular-nums`). Colour carries state only:

| State | Token |
|---|---|
| Waiting for officer / expiring | `--ops-bad-ink` on `--ops-bad-fill` (ring: `--ops-warn` → `--ops-bad` in the last 30 s) |
| On call / bridged | `--ops-good-ink` on `--ops-good-fill` |
| On hold / wrap-up | `--ops-warn-ink` on `--ops-warn-fill` |
| AI handling | `--ops-info-ink` on `--ops-info-fill` |
| At risk (Phase 3) | `--ops-warn` triangle + label (never colour alone) |

No gradients, glows, or orbs. Move every inline `style={{…}}` in `CallCase.tsx`/`page.tsx` into
`app/calls/calls.css` (and a new `components/staff/calls/console/callConsole.css`). Must look right in both
themes — tokens already switch with `data-theme`.

**Motion** (all disabled under `prefers-reduced-motion`): alert slide-in 180 ms ease-out; waiting items pulse
the dot only (not the row) every 2 s; evidence-link flash 1.2 s; state changes cross-fade 150 ms; the wait
ring animates via `stroke-dashoffset` driven by `useNow`.

**Interaction details.**
- Optimistic claim: button shows "Connecting…"; on 409 revert and show who took it.
- Destructive actions (End, Transfer) use an inline confirm popover, not a modal.
- The brief's "Say first" line has a Copy button; brief sections are keyboard-navigable.
- Live timers everywhere read from `useNow` so they stay in sync.
- Level meters (5 bars) for my mic and the caller, read from `audioLevelBus` in one rAF loop, like
  `CallOrb` on the caller side.
- Empty and error states via `EmptyState` / `ErrorState`; loading via `Skeleton*`.

**Accessibility.**
- New waiting calls announced through an `aria-live="polite"` region ("Caller waiting: VAT refund, Luganda").
  Alert cards are `role="status"`; only the first alert per call is announced.
- Focus moves to the call controls after joining, and back to the queue after wrap-up.
- All controls reachable by keyboard; shortcut sheet on `?`.
- Contrast: use the `-ink` token variants on fills (they were tuned for 4.5:1; see `tokens.css:190-216`).
- Add `/calls` to the axe a11y Playwright project (`App/frontend/e2e/a11y.spec.ts`), stubbing the sockets
  with `page.routeWebSocket` as that file already does for tickets.

**Responsive.** ≥ 1280 px three panes; 1100–1280 px Context becomes a drawer; < 960 px Queue/Workspace
alternate (reuse `.ag-split`/`.is-open` behaviour from `app/agent/agent.css:40-97`). The alert stack and
dock stay usable at 1024 px.

---

## 10. Phases

### Phase 0 — Fix the foundations (≈ 1–2 days)

1. `_ensure_columns` + new `voice_calls` columns (§5).
2. Extend `transfer.open_transfer` and add `close_transfer_on_timeout` (6.1).
3. Tests (`test_receptionist_transfer.py`, new): both engines publish `call.transfer_requested` with the
   packet's topic/priority and `language`; topic/priority/`transfer_requested_at` are persisted; timeout on
   either engine sets `needs_callback` and publishes `call.transfer_timed_out`; `ticket_queue` off → no
   queue entry; the lobby payload has no transcript/brief keys.

**Done when:** a Gemini Live call that asks for an officer appears as Waiting on `/calls` with its real topic
and priority, and a timed-out one appears as a callback.

### Phase 1 — Demo-critical officer flow (≈ 5–7 days)

1. Brief scheduler + `GET …/brief` + `brief` events (6.2).
2. Claim/release + claim expiry + audio WS claim check + "Officer X is joining" announcement (6.3).
3. Frontend singletons + store (8.1); `StaffCallLayer` with Call bar, alert stack, active-call dock,
   device check (8.2); mount in `StaffGuard`.
4. `/calls` Desk: queue sections, workspace states A–C, `CallBriefCard` with evidence links, `CallControls`
   (mute + end for now), transcript anchors (8.3).
5. Keyboard shortcuts for take/mute/end/navigation.

**Done when:** an officer on `/admin/tickets` gets an alert, reads the brief in the preview, takes the call,
navigates to a ticket and back without dropping audio, and ends the call.

### Phase 2 — Complete working day (≈ 5–7 days)

1. Hold, transfer (team/officer), end + wrap-up with AI-drafted note, ticket actions (6.3); `TransferDialog`,
   `WrapUpPanel`; caller-side hold/reconnecting states (6.8).
2. Presence + heartbeat + availability menu + alert targeting by language/team (6.4).
3. Officer disconnect grace + rejoin; caller-left-while-waiting handling.
4. Callbacks tab; History tab with filters/search/pagination; caller history in the Context pane (6.5).
5. Knowledge-assist panel and private notes.
6. Officer metrics on the Performance tab (6.7).

### Phase 3 — Supervisor and quality (≈ 3–4 days)

1. Risk signals + queue markers + Take over from AI-handling calls (6.6).
2. Supervisor view (ura_admin): officers' presence board, waiting SLA, per-officer stats.
3. Optional: supervisor listen-in (read-only audio) — only if time allows; transcript follow already covers
   most of the need.

---

## 11. Tests

### Backend (`App/backend/tests/`; Pipecat-dependent tests use `pytest.importorskip("pipecat")`)

| File | Covers |
|---|---|
| `test_receptionist_transfer.py` | `open_transfer` from both engines publishes the packet's topic/priority + language and persists them; lobby payload metadata-only (assert no transcript/brief keys); `ticket_queue` off → no queue entry; timeout on either engine → `needs_callback` + `call.transfer_timed_out` |
| `test_receptionist_brief.py` | Incremental vs full build; strict JSON parsing; invalid `turn_seqs` dropped; Luganda routed to Sunflower; deterministic fallback; no unredacted identifiers |
| `test_receptionist_claims.py` | Atomic claim (two concurrent claims → one 200, one 409); expiry releases; audio WS rejects non-claimant (4409); auditor 403 |
| `test_receptionist_officer_actions.py` | Hold/resume accounting; transfer re-queues with `attempt` and note; end → wrap-up; wrap-up saves outcome, updates ticket, restores presence; callback-done |
| `test_receptionist_presence.py` | Upsert, heartbeat, TTL → offline, automatic transitions, `officer.presence` events only on change |
| `test_receptionist_history.py` | Filters, `q` over turns and summary, pagination + `total`, caller history (+ anonymous) |
| `tests/test_all_endpoints_e2e.py` | 11 new routes in `EXPECTED_ENDPOINTS` + `COVERAGE`; counts 85 HTTP + 7 WS |

Run: `PYTHONPATH=App/backend python3 -m pytest App/backend/tests tests/agents tests/chaos -q`

### Frontend (`App/frontend/src/__tests__/`)

| File | Covers |
|---|---|
| `services/officerCallSession.test.ts` | State machine transitions; mute/hold; rejoin; cleanup |
| `store/useCallConsoleStore.test.ts` | Lobby events → queue sections and ordering; alert dismissal; claimed-by-other removal |
| `components/CallAlertStack.test.tsx` | Shows for available officers only; collapse at > 2; Take call → claim; 409 path; title badge |
| `components/CallConsoleBar.test.tsx` | Counters, availability menu, auditor read-only |
| `components/CallBriefCard.test.tsx` | Renders sections; evidence click scrolls to `#turn-7`; fallback notice |
| `components/WrapUpPanel.test.tsx` | Draft note editable; outcome required; submit payload |
| `app/calls.test.tsx` (update) | Desk tabs; queue sections; workspace states A–E; history filters call the API with params |

Stub `WebSocket`, `AudioContext`, `getUserMedia`, and `Notification` with `vi.stubGlobal`.
Run: `cd App/frontend && bun run test && bunx tsc --noEmit -p tsconfig.json && bun run lint`
(five pre-existing lint errors in `StaffGuard.tsx`, `SupportCaseModal.tsx`, `VoiceFirstChat.tsx`,
`VoiceModal.tsx`, `lib/ticketUi.ts` — don't count them as regressions, and don't make them worse in
`StaffGuard.tsx`).

### E2E (Playwright, `App/frontend/e2e/`)

`staff-calls.spec.ts` with `page.routeWebSocket` scripting the lobby and per-call channels: alert appears on
`/admin/tickets`; Take call; navigate away and back (dock visible); end; wrap-up; history search. Add
`/calls` to the a11y project.

---

## 12. Manual verification (GPU host, `FLAG_VOICE_RECEPTIONIST=true`, `WORKERS=1`)

Three browsers: taxpayer (A), officer 1 (B), officer 2 (C); optionally an auditor (D).

1. B and C set Available; B stays on `/admin/tickets`, C on `/analytics`.
2. A calls, asks two questions, then asks for an officer (Gemini Live engine) → both B and C get an alert
   within 1 s; the brief is ready when Preview is opened.
3. B and C press Take call at the same moment → one wins, the other sees "Taken by …".
4. A hears "You're now connected to Officer …"; talk both ways; B navigates to a ticket and back — audio
   continues, dock visible.
5. B holds (A hears the hold tone), resumes, transfers to C's team with a note → C gets the alert with the
   note in the brief; C takes it and ends it; C wraps up with outcome "Resolved".
6. Repeat with nobody taking the call → after 90 s A hears the callback line; the call appears in Callbacks.
7. A hangs up while waiting → Callbacks shows "Caller left".
8. B's network drops mid-call (close the tab) → A hears the reconnecting line; B reopens and rejoins within
   30 s.
9. History: search a word the caller said; filter by language and outcome.
10. D (auditor) sees everything, cannot take calls, gets no sound.
11. Light and dark theme, 1024 px width, keyboard-only run through steps 2–5, reduced motion.

---

## 13. Risks

| Risk | Mitigation |
|---|---|
| In-memory hub/rooms need a single uvicorn worker | Already the demo constraint (`WORKERS=1`); document it; Redis pub/sub is the scale-out path |
| Brief quality or latency | Rolling updates keep it fresh; deterministic fallback; evidence links let officers verify fast |
| Officer audio lost on full page reload | `beforeunload` warning; claim survives; "Rejoin call" in the dock |
| Alert fatigue | Targeting by availability/language/team; collapse > 2; dismiss per officer; sound toggle |
| Redacted transcripts hide details the officer needs (TIN) | Brief says "TIN given (redacted)"; the officer confirms it verbally once connected — note this in the officer guidance |
| Schema drift on Postgres | `_ensure_columns` handles both backends; test on SQLite in CI and once on Postgres |
| `KnowledgeAssistPanel` traffic counted as taxpayer chats | Tag `officer-kb-` conversation ids and exclude them from taxpayer analytics |

---

## 14. Decision log (fill in during implementation)

| Date | Item | Decision / result |
|---|---|---|
| 2026-09-25 | Phase 0 transfer unification | Done. `open_transfer` publishes and stores the packet's topic/priority (fallback `general_tax_support`/`normal`; a priority outside `low…urgent` reads `normal`), `waiting_since`, `target_team`, `attempt` (`CallState.transfer_attempts`); the lobby key is now `ticket_ref` (§7), replacing `ticket_id` — `useCallsLobby` updated. `close_transfer_on_timeout` is shared by both engines and also sets the row back to `status=ai` (it stayed `transferring` before, so a timed-out call looked like it was still waiting). 19 columns + 3 indexes via `store._ensure_columns`, checked on SQLite (tests) and once on Postgres 16 (existing row keeps defaults, idempotent). **Not done here:** `brief.build_now` on transfer (§6.1 item 3) — `brief.py` is Phase 1; `needs_callback` is not yet cleared when a later transfer is answered (claims, Phase 1). Live check on the GPU stack (Gemini Live): "talk to an officer about my account balance" → `account_specific`/`high` on the row, 90 s later `needs_callback=1`, `no_officer_available` (`evals/reports/call_replay_2026-09-24_call_desk_phase0.json`) |
| 2026-09-25 | Brief model and latency measured | `gemini-2.5-flash-lite` (Sunflower first for Luganda, deterministic fallback). Live, on a one-question call: model 3.2 s, brief ready **4.0 s** after `call.transfer_requested` — over the 2 s p95 target because nothing had triggered the rolling build yet (every 3 caller turns); longer calls have one ready before the transfer. Incremental prompts send only the turns since the last brief. `turn_seqs` validated against real turns; details never carry values |
| 2026-09-25 | Claim race test | One conditional UPDATE (`store.claim_call`) + `CallRoom.desk_lock`. Unit: two concurrent `desk.claim` → one 200, one 409 naming the winner. Live (Gemini Live call on the GPU stack): 200 for Okello, 409 `Officer Okello` for Nakato, Nakato's audio refused, Okello bridged, caller heard *"You're now connected to Officer Okello."*, officer↔caller audio both ways, AI silent while bridged, End → closing line, row `ended/officer_ended` |
| 2026-09-25 | Navigation keeps audio (singleton approach) | `services/officerCallSession.ts` (audio socket, mic, player) and `services/callLobbySocket.ts` (ref-counted, 5 s release grace) live at module level; `useCallConsoleStore` (Zustand) mirrors them; `StaffCallLayer` mounts in `StaffGuard` on every page. Unit-tested (state machine, store, alerts, bar, brief, Desk). **Not browser-verified here**: Chromium cannot open a page in this sandbox (every Playwright test, old ones too, fails at `newPage`) — `/calls` is now in the a11y project for CI |
| 2026-09-25 | Phase 3 supervisor and quality | Done. Risk signal monitor implemented in `receptionist/risk.py` evaluating clarifications (≥2), repeated questions (similarity ≥0.8), distress, low acoustic confidence, abstentions, and duration. Emits lobby `call.risk` events on change and persists `risk_json`. Queue items show '▲ at risk' / '▲ watch' badge markers, and AI calls are sorted by risk level. Supervisor view (`ura_admin`) renders real-time officer presence roster, waiting SLA card, and active team counts. Extended per-call and aggregate metrics in `metrics.py` with `time_to_answer_s`, `handle_time_s`, `hold_total_s`, `wrapup_time_s`, `abandonment_rate`, and callbacks created/closed. All 232 receptionist tests, 22 e2e manifest tests, and 387 frontend tests passing |
| 2026-09-25 | Phase 2 complete working day | Done. All 7 remaining routes added (POST /hold, /transfer, /wrapup, /callback-done, GET /caller-history, PUT /officers/me/presence, GET /officers/presence -> manifest updated to 85 HTTP + 7 WS). Presence tracking module with computed offline status (TTL 60s), automatic status transitions (on_call, wrap_up, available), and lobby event emission. Reconnect grace period handling on unexpected officer socket disconnect with caller notification and reconnect timer. Desk UI completed with 4 tabs (Desk, Callbacks, History, Performance), Context Rail with caller history, ticket link, knowledge assistant, and scratchpad. TransferDialog with team/officer selection, WrapUpPanel with AI note editing, outcome, ticket action, and star review. Soft hold tone looping and caller on_hold/reconnecting states with en/lg/sw i18n keys. All unit tests passing (227 receptionist tests, 22 e2e manifest tests, 387 frontend tests). |
| 2026-09-25 | Phase 1 scope and deviations | Routes added: `GET …/brief`, `POST …/claim`, `…/release`, `…/end` (78 HTTP + 7 WS; the other 7 of §7 come with Phase 2). **End** moved up from Phase 2: Phase 1's done-when ends the call. Availability is local to the tab until Phase 2's presence API. Claiming a call the AI is still handling opens a transfer with reason `officer_takeover` (so `ticket_queue`, the ticket and the lobby see it the usual way); releasing it before joining hands it back to the AI. The audio socket accepts before closing 4409, or a browser only sees 1006 |
| 2026-09-25 | Found on the way (fixed) | (1) While an officer was bridged, caller audio still reached Gemini, which answered the caller over the officer — `CallerAudioTap` now sends it to the officer only, and `OfficerOutputGate` drops any AI reply. (2) `summary.py` called `_vllm_generate` with a string instead of chat messages, so every Sunflower (Luganda) summary silently failed over to Gemini or the template. (3) The staff lobby socket ignored `voice_receptionist`; it now closes 1001 when calls are off, and the console hides its call layer. (4) `.st-trans-btn.is-active` (white on #10b981, 2.53:1) failed the staff axe audit before `/calls` could be reached |

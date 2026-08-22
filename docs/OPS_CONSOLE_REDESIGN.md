# Operations console redesign — `/admin`, `/agent`, `/analytics`

Executed 2026-08-22 on branch `dev`, against the staff and admin surfaces of
`App/frontend`. Companion to [`UI-MIGRATION-NOTES.md`](../UI-MIGRATION-NOTES.md),
which records the taxpayer chat's `chatv2` migration; this one covers the eight
routes behind the staff sign-in.

The taxpayer chat had been through a full design pass. The console behind it had
not: it had grown one page at a time, and it showed — four page headers with four
type scales, priority colours copied as raw `rgba()` into three stylesheets, a
chart palette hardcoded to one theme, and two of its eight routes living outside
the console's own navigation and access gate.

---

## Critical gaps found

### Structure

| # | Gap | Consequence |
|---|---|---|
| 1 | `/analytics` and `/analytics/evaluation` were **not behind `StaffGuard`**, while being listed in the console nav and the account menu | An anonymous visitor got empty panels and a raw `Failed to load dashboard: …` string where every other staff route offers a sign-in. A signed-in officer who clicked **Analytics** lost the console nav, the live-escalation strip and sign-out — a dead end with no way back except the chat. |
| 2 | `app/analytics/layout.tsx` nested a **second `<Providers>`** inside the root one | Two React Query caches. An invalidation from a staff page never reached the analytics tree, and the same query could be in flight twice. |
| 3 | **No page shell.** `.ov-head`, `.ag-head`, `.tickets-header`, `.analytics-header` each hand-rolled | Four title sizes (23 / 22 / 22 / 28px), four paddings, four content widths (1180 / 1320 / 1400 / 1280px). The console's width changed depending on which link you clicked. |
| 4 | **Flat seven-link nav** with no hierarchy and a weak active state | An administrator saw seven siblings with no grouping; the current page was a surface tint. |
| 5 | **No global navigation affordance.** Page-local hotkeys (`j`/`k`, `/`, `r`, `a`) existed and worked, but were documented in one 11px line on one page | Good ergonomics nobody could discover, and no way to leave a page without the mouse. |
| 6 | Staff pages had **no theme control at all** — only `/analytics` had one, from a nav that this change removes | An officer on `/admin` could not switch theme. |

### Integrity

| # | Gap | Consequence |
|---|---|---|
| 7 | Three of six SLO gauges on `/analytics` were **literals in the JSX** — availability `99.9`, accessibility `95`, coverage `80` — drawn identically to the three fed by live telemetry | On a dashboard whose stated purpose is monitoring evidence (ISO 42001 §9.1, EU AI Act Art. 13), three constants rendered as measurements. |
| 8 | `/analytics/evaluation` fell back to **stored sample data with no marker**, and its confusion matrix is a hardcoded constant with no endpoint behind it at all. `POST /v1/evaluate` is gated behind the operator key, not a staff sign-in, so the fallback is what a staff session sees essentially always | Eight green metrics and an "ALL GATES PASS" badge, with no way to tell measured from illustrative. |
| 9 | The **period selector implied page-wide scope**. Request counters and latency histograms come from `metrics.snapshot()` — this replica's process lifetime — and do not move when the period changes | Uptime and p95 read as 7-day or 90-day figures depending on a control that does not affect them. |
| 10 | `/admin` **hardcoded 30 days** in its hooks and then told the reader "last 30 days" with no control | No way to ask a different question. |
| 11 | **No comparison on any metric.** Six flat numbers | "3 awaiting first response" is a quiet morning or a crisis, and the card said neither. |

### Correctness

| # | Gap | Consequence |
|---|---|---|
| 12 | `SloGaugeCard` formatted with `value.toFixed(value < 10 ? 2 : 0)` | An availability of **99.9 rendered as "100"** — the one digit that mattered was the one being rounded away. |
| 13 | The same gauge's arc for a "lower is better" metric was `((target - value) / target) * 100 + 50` | A latency exactly on target drew a **half-empty ring** while the label under it said it was passing; a latency of zero drew a ring past full. |
| 14 | Every chart hardcoded a dark slate palette: `#94a3b8` axis ticks, `#cbd5e1` category labels, a `#1e293b` tooltip with `#e2e8f0` text | On the light dashboard the ticks measure ≈ 2.2:1 and the category labels ≈ 1.4:1 against the page. Not a preference — text nobody can read. `/analytics` was never caught by the axe suite because it was not behind the gate the suite signs in through (gap 1). |
| 15 | `TopicBarChart` cycled a **ten-hue rainbow by row index** | Colour encoded "this is the fourth bar", which position already said, and the hue changed whenever the ranking changed. |
| 16 | `/analytics` priority pills were red / orange / **blue**; `/admin` and `/agent` used red / amber / grey | The same priority meant a different colour on adjacent pages. |
| 17 | The route-level error boundary was styled inline with `#fafafa` on no background | In light mode the one page whose job is explaining a failure was near-white on white. |
| 18 | The dashboard threw on `dash.requests.latency` when the payload shape did not match | A partial or stubbed response took the whole route down — the standing flake recorded in `UI-MIGRATION-NOTES.md`. |

### Craft

| # | Gap |
|---|---|
| 19 | **No type ramp**: 10 / 10.5 / 11 / 11.5 / 12 / 12.5 / 13 / 13.5 / 14.5 / 16 / 17 / 21 / 22 / 23 / 26px, half-pixels included. |
| 20 | **Five font weights** including `650`, which the Aptos / Avenir stack has no master for and browsers synthesise unevenly. |
| 21 | **Two unit systems**: px in the staff files, rem in analytics (0.35 / 0.72 / 0.82 / 0.84 / 0.9rem). |
| 22 | Priority and status colours **duplicated across three stylesheets** under three prefixes (`ov-pri-*`, `ag-pri-*`, `st-pri-*`) as hand-written `rgba()`. |
| 23 | **≈ 230 of 449 lines of `agent.css` were dead** — an `.ag-*` copy of the queue row, brief, transcript, composer and buttons, left behind when `/agent` and `/admin/tickets` were merged onto the shared `.st-*` components. Zero references in any `.tsx`. |
| 24 | **No loading, empty or error system.** Four bare sentences ("Loading the queue…", "Checking…", "Loading dashboard data...") that reserve none of the space the content will take, so every load ended in a jump. |
| 25 | `/admin/overrides` shipped **unstyled native form controls** — the panel had no form rules — and `/admin/outbox` reused `.ov-queue`, a list styled for the queue's link rows, for three-field records. |
| 26 | The flags console's state control was a **button whose entire label was the word "on"** — state and action in the same three characters, and not a recognised control. |
| 27 | The live-escalation strip was a **permanent full-width bar** reading "Listening for new escalations" on every staff page, and opened its own WebSocket. |
| 28 | The case detail had **no sticky header**: on a long transcript, "Back to queue", the status and the assign control were the first things to scroll away. |
| 29 | **No freshness indicator.** Pages refetch on 10–60s intervals with nothing on screen saying so — the difference between "the queue is clear" and "the queue was clear at some point". |

### Found while building, by looking rather than by reading

Four defects that only a rendered page or an automated audit surfaces. All four
are fixed; three of them are in classes of mistake worth naming, because the fix
is a rule rather than a patch.

| Where | What | Why it happened |
|---|---|---|
| `/admin/tickets`, ≤ 720px | **363px of horizontal page scroll at 360px wide** | The five-column SLA strip sits in the page header's actions slot. A flex item defaults to `min-width: auto` — its content width — so instead of scrolling inside itself it pushed the document sideways. The two existing overflow tests covered `/admin` and `/agent` only, so no test looked at this route; the sweep now covers all eight. |
| Console nav, ≤ 1080px | **The brand link had no accessible name** | `.staff-brand-text` is `display: none` below 1080px and the mark beside it is `aria-hidden`, so on a phone the link was reachable by keyboard with nothing to announce. Only the mobile axe project could see it. |
| Every tinted chip, light theme | **4.12–4.45:1 where 4.5 is required** | See "Colour that is measured" below. |
| `/analytics`, phone width | **A horizontally scrolling table no keyboard could reach** | Two of the three table wrappers carried `tabIndex`/`role`/`aria-label` inline; the third did not. There is now one `TableScroll` component, so the next one cannot forget. |

---

## What changed

### A design layer, not a fifth stylesheet

`src/styles/ops/tokens.css` + `src/styles/ops/ops.css`, loaded once from
`app/layout.tsx` after `globals.css`. Every rule is namespaced `.ops-*` and every
token `--ops-*`; nothing in `globals.css` is redefined, so the taxpayer chat is
untouched and the Auto / Light / Dark preference keeps working through the
existing `--text-*` / `--surface-*` / `--border-*` tokens this layer builds on.

- Eight type steps, whole pixels. Four weights (`650` and `800` removed).
- A 4pt space scale, five radii, three elevations, one motion pair, one focus rule.
- Semantic roles (`good` / `warn` / `bad` / `info`) as text colour, tinted fill,
  and border — replacing the copied `rgba()` values.
- A validated data-viz palette (below).

### Colour that is measured, not chosen

The tinted chip is the awkward case: it puts a role colour on a tint of *itself*,
as 11px text, so the fill darkens the very ground the text has to clear — and
`globals.css` tuned the light-mode semantic colours to clear 4.5:1 against a
*plain* surface with almost no headroom. At the original 14–15% tint the axe suite
found two failures (`.staff-role-pill` at 4.43:1, `.ops-chip.is-warn` at 4.45:1);
at 10% the tightest role still measured 4.12:1 over the darkest ground a chip can
land on.

The fix is in the tokens: the fill is held at 10% and the chip's **text is one step
darker than the role colour** (`color-mix(… 90%, black)` in light mode; dark mode
needs none of it, measuring 5.4–10.1:1). Every role now clears 4.8:1 on panel,
page canvas and row. A third failure — a `<code>` chip wearing the muted text
token on its own tinted ground, 4.29:1 — was fixed the same way.

The chart palette is the validated reference instance from the data-viz standard,
re-checked against *this* app's card surfaces:

```
light #FDFDFE — adjacent CVD ΔE 9.1, normal-vision 19.6, 3 sub-3:1 marks (relief: labels + table view)
dark  #0D0E17 — adjacent CVD ΔE 8.4, normal-vision 19.3, all marks ≥ 3:1
p50→p95→p99 ordinal ramp — monotone L, ΔL ≥ 0.06, light end ≥ 2:1, both modes
```

Colour now follows the job rather than the row: ordered quantiles take the
one-hue ordinal ramp, nominal categories take one hue for every bar, retrieval
modes keep a fixed slot per mode so a change in ranking never repaints one, and
status colours are reserved for state and always paired with a word.

### Shell

- **One `OpsPage`** — eyebrow, title, description, actions slot, one toolbar row —
  used by all eight routes. `id="staff-main"` (the skip-link target) belongs to the
  shell now rather than being repeated per page.
- **Grouped nav**: Work / Configure / Observe, from one `STAFF_DESTINATIONS` list
  in `lib/roles.ts` that the nav, the command palette and the account menu all
  read. Still `nav.staff-nav`, still sticky, still blurred, still role-filtered.
  The current page is named by weight *and* an underline anchored to the bar.
  Below 1080px the link strip scrolls inside the bar instead of wrapping the whole
  nav to three rows.
- **`⌘K` command palette** — jump to any page the signed-in role may open, cycle
  the theme, sign out. Role-scoped from the same list, so it can never offer a
  page that would be refused. Its trigger is hidden below 720px: there is no
  chord to press on a phone, and every destination it offers is already one tap
  away in the strip below it.
- **An explicit two-row grid on phones** — brand and account on the first row,
  the link strip on the second — rather than letting flex wrap decide, which
  left the brand floating a third of the way across its own line.
- **Theme toggle** in the console nav.
- **One live-escalation socket**, owned by `StaffGuard`. The connection state is a
  dot in the nav; the strip appears only when something has arrived, and can be
  dismissed.
- `/analytics` and `/analytics/evaluation` behind `StaffGuard` with the roles they
  were always listed under, and the duplicate `<Providers>` removed.

### Honesty

- The three literal gauges are gone. What replaces them is a note naming them as
  what they are: targets gated in CI, not measured here.
- Panels fed by process-lifetime counters say so on the panel.
- `/analytics/evaluation` states its provenance at the top and only claims a gate
  result when there is a live run behind it.
- `/admin` gained a period control, and one exact comparison. `ticket_stats`
  counts by `created_at`, so counting the 2N window and subtracting the N window
  gives the previous N window exactly — that is the delta on escalation volume.
  The SLA medians are computed server-side over one window and cannot be
  recovered by subtraction, so **no median carries a delta**. The daily-arrivals
  shape is bucketed from ticket rows the page already loads; the list endpoint
  orders urgent-first and truncates at its limit, so a full page is a biased
  sample and the chart says so instead of drawing the wrong shape.
  (`lib/trends.ts` carries this reasoning next to the arithmetic.)

### Craft

Skeletons that mirror the final layout on every route; distinct empty and error
states (an empty queue is good news, an unreachable backend is not, and a grey
paragraph said both in the same voice); a real switch on the flags console with
rows sorted so anything diverging from its default floats to the top; a styled
override form that states the consequence next to the field it applies to and
confirms before deleting; the outbox as a table; a sticky case header; a
freshness stamp with manual refresh; keyboard hints as key caps beside the tabs
they apply to; and the ≈230 dead lines of `agent.css` deleted.

---

## Verification

| Check | Result |
|---|---|
| `bunx tsc --noEmit` | clean |
| `bun run lint` | clean (1 pre-existing `<img>` warning in `DocumentInspectionViewer`) |
| `bun run test` (vitest) | **177 / 177 pass** |
| `bun run build` | clean, 15 routes |
| Playwright `chromium` + `mobile-chrome` + `a11y` | see below |
| Horizontal overflow, 8 routes × 7 widths (1440 → 360px) | none |
| axe (WCAG 2.0/2.1/2.2 A + AA), 8 console routes × 2 themes | **0 serious, 0 critical** |
| Data-viz palette validator, both modes | all checks pass |

The axe route audit grew from four console surfaces to eight —
`/admin/overrides`, `/admin/outbox`, `/analytics` and `/analytics/evaluation`
joined it. The two analytics routes could not be scanned before: they were not
behind `StaffGuard`, so the suite's staff session never reached their content.
That single test now performs sixteen navigations and sixteen axe passes and is
marked `test.slow()`.

Final run across the three projects: **286 passed, 9 skipped** (the voice
integration specs, skipped by design without a real backend), 12 failed.

**Those twelve are pre-existing and unchanged by this work.** All twelve fail
identically on the base commit `813cfff1` — verified by building and serving a
pristine worktree of `HEAD` and running the same specs against it:

- `responsive-comprehensive.spec.ts` — "iPhone SE viewport" and "iPad in portrait"
  call `test.use()` inside a test body, which Playwright rejects outright; "touch
  targets ≥ 44px" measures 37.19px; "content width doesn't exceed max-width" reads
  `max-width: none` on `main`; "soft keyboard doesn't hide critical UI" and
  "screen reader can navigate on mobile" also fail. All six assert on `/`, the
  taxpayer chat, which this change does not touch.
- `sidebar-peek.spec.ts` — hover-peek on the `mobile-chrome` project.

`dictation.spec.ts` also failed once under a three-worker run and passes in
isolation on both projects; it exercises the taxpayer chat's live dictation,
which this change does not touch.

### Spec updates

Selectors and copy that this redesign legitimately renamed. Every assertion keeps
its meaning:

| Spec | Change |
|---|---|
| `staff-ui.spec.ts` | `.ov-metric` → `.ops-stat-grid .ops-stat`; `.ov-metric.ov-warn` → `.ops-stat.is-warn`; `.ov-badge-urgent` → the queue panel's danger chip; `.ov-cols > section` → `.ov-cols > *` (the right column is now a stack of two panels) |
| `adminTickets.test.tsx` | the team reset control is labelled "any" under a **Team** group with an accessible name of "All teams"; the empty state is a title plus a body rather than one sentence |
| `navigation-consent.spec.ts` | the analytics routes assert the sign-in gate, and that the gate offers a way back to the assistant |
| `a11y.spec.ts` | four new routes, richer API stubs so the charts actually render under axe, `test.slow()` |
| `staff-ui.spec.ts` | **new**: one overflow sweep across all eight console routes at seven widths, replacing the assumption that two pages stood in for the rest |

### Not verified here

1. **Real Safari / iOS.** `backdrop-filter` on the sticky nav and the sticky case
   header is asserted in Blink only.
2. **A live backend.** Everything above runs against stubbed `/api/**`. The
   arrivals sparkline, the volume delta and the truncation guard are exercised
   with fixtures, not real ticket volume.
3. **Real evaluation data.** `/analytics/evaluation` was reviewed in its
   sample-data state, which is what a staff session sees; the live-run branch is
   code-reviewed but not rendered.

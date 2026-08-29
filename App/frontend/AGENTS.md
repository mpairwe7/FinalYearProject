# Frontend (Next.js 16)

Taxpayer chat and staff workbench. App Router under `src/app`.

## Commands

```bash
cd App/frontend
bun test
bun run lint
```

## Boundaries

| Path | Role |
| --- | --- |
| `src/app/page.tsx` | Taxpayer chat |
| `src/app/admin/` | Flags, tickets, overrides, outbox |
| `src/app/agent/` | Officer workbench |
| `src/components/staff/` | Queue, case, composer, live banner |
| `src/components/charts/chartTheme.tsx` | Chart palette **and** the plain-language layer |
| `src/lib/ticketUi.ts` | SLA / breach helpers |
| `src/lib/oidcFlow.ts` | Authorize + RP-initiated logout |
| `src/lib/i18n/` | Taxpayer-surface dictionary (en / lg / sw) |

Staff routes use `StaffGuard`. Do not claim flag toggles are cluster-wide.

## Rules

- **Console charts are labelled in plain words, not in the system's
  vocabulary.** New panels take their names from `chartTheme`
  (`QUANTILE_LABEL`, `RETRIEVAL_MODE_LABEL`, `EVAL_METRIC`, `plainSeconds`) and
  carry a `<ChartNote>` stating the reading. Keep the technical term beside the
  plain name, never instead of it. See `docs/OPS_CONSOLE_REDESIGN.md`.
- **Taxpayer-facing strings are dictionary keys**, not literals — `useTranslation()`
  and `lib/i18n/en.ts`. The staff console stays English.
- **Signing out ends the provider session too** (`endOidcSession`). Do not add a
  sign-out path that only clears the local token: the next sign-in is then
  answered from the provider's surviving cookie, as the same account.

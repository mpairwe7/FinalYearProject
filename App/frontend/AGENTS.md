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
| `src/lib/ticketUi.ts` | SLA / breach helpers |

Staff routes use `StaffGuard`. Do not claim flag toggles are cluster-wide.

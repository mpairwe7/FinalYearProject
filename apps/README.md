# apps/

| App | Standard path | Source of truth | Runtime |
| --- | --- | --- | --- |
| API / agent host | `apps/api` | `App/backend` | FastAPI, `uvicorn app.main:app` |
| Staff + taxpayer UI | `apps/web` | `App/frontend` | Next.js 16 |
| Mobile | — | `App/mobile` (if present) | Flutter |

Do not move these trees without updating Dockerfile, Crane Cloud, and
`.github/workflows`.

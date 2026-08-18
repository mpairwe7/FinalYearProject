# 2026 agent-repo layout

Runtime stays under `App/backend/app` (`from app…`). This tree is the
industry map agents should navigate:

| Standard path | Resolves to |
| --- | --- |
| `apps/api` | `App/backend` |
| `apps/web` | `App/frontend` |
| `agents/graphs` | `App/backend/app/agents/graphs` |
| `agents/tools` | `App/backend/app/tools` |
| `agents/mcp` | `App/backend/app/mcp` |
| `evals/` | `Data/eval` + `evals/*.py` |
| `data/` | `Data/` |
| `infra/` | compose + example k8s |
| `governance/` | risk manifest |
| `configs/prototype.env` | demo defaults |

Do not physically move `App/`.

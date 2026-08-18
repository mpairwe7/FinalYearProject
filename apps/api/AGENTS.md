# apps/api

Deployable FastAPI agent host. Source of truth: `App/backend`.

```bash
cd App/backend
PYTHONPATH=. uvicorn app.main:app --reload --port 8887
```

See `App/backend/AGENTS.md`.

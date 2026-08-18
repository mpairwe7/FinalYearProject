# Testing

```bash
PYTHONPATH=App/backend python3 -m pytest App/backend/tests tests/agents tests/chaos -q
cd App/frontend && bun test
```

New FastAPI routes: `tests/test_all_endpoints_e2e.py`.
Red-team refuse gate: `App/backend/tests/test_redteam_corpus.py`.

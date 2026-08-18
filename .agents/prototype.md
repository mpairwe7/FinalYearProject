# Prototype demo

```bash
source configs/prototype.env
# or: bash scripts/prototype.sh
```

- Account connector defaults to mock (`live=false`).
- Publications ingest uses `Data/eval/publications_fixture.txt` when no https URL is set.
- Staff CMS: `/admin/overrides`. Outbox: `/admin/outbox`.
- Production rejects `URA_ACCOUNT_API_MODE=mock`.

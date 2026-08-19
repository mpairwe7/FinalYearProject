# data/eval

Canonical eval files live in `Data/eval/` (capital D):

- `rag_eval.jsonl`
- `rag_eval_lg.jsonl`
- `redteam_corpus.jsonl`
- `coverage_bank.jsonl` — curated taxpayer questions (en/lg/sw) probing for
  corpus subjects nothing answers; scored by `ml.pipelines.corpus_coverage`
- `coverage_domains.yaml` — domain registry: corpus sources, per-domain floor,
  domain-owner review record
- `publications_fixture.txt` — offline ingest for the prototype
- `prototype_seed.json` — sample CMS overrides, inbox, ticket, outbox, thumbs-down (`python -m app.seed_prototype`)

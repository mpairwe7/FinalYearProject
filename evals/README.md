# evals/

| Artifact | Path |
| --- | --- |
| RAG golden set | `Data/eval/rag_eval.jsonl` |
| Red-team corpus (CI: `test_redteam_corpus.py`) | `Data/eval/redteam_corpus.jsonl` |
| Preference export | `python evals/export_preferences.py` |

`python -m app.freshness --check` remains the index-drift gate.
`python -m app.publications` is the publication ingest (no auto-recreate).

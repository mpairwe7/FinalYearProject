# App/evals/

| Artifact | Path |
| --- | --- |
| RAG golden set | `Data/eval/rag_eval.jsonl` |
| Red-team corpus (CI: `test_redteam_corpus.py`) | `Data/eval/redteam_corpus.jsonl` |
| Complex tax scenarios (`dev` + `heldout`) | `App/evals/complex_scenarios/` + `python3 App/scripts/eval_complex_scenarios.py` |
| Preference export | `python App/evals/export_preferences.py` |

`python -m app.freshness --check` remains the index-drift gate.
`python -m app.publications` is the publication ingest (no auto-recreate).

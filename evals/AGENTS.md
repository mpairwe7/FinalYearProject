# evals/

Deterministic gates. Do not call a hosted LLM from these jobs.

| Artifact | Path | Gate |
| --- | --- | --- |
| RAG golden set | `Data/eval/rag_eval.jsonl` | retrieval regression |
| Coverage bank | `Data/eval/coverage_bank.jsonl` + `coverage_domains.yaml` | `ml.pipelines.corpus_coverage --fail-under-floor` — per-domain floor (#303) |
| Red-team corpus | `Data/eval/redteam_corpus.jsonl` | `test_redteam_corpus.py` — refuse / partial_refuse |
| Routing golden sets | `app.agents.eval_routing` | EN ≥ 0.95 before `agentic_mode` |
| Preference export | `evals/export_preferences.py` | thumbs-down + `officer_reply`; no fine-tune |
| DPO scaffold | `evals/dpo_job.py` | refuses train unless `EVAL_GATE_OK` |

`FLAG_HYDE` / `FLAG_GRAPH_FUSION` stay off until an unseen multi-hop set exists.

The coverage bank is the one set here that is **not** derived from the corpus —
it probes for subjects the corpus does not cover. A new `ura_*_faqs.csv` needs a
domain in `coverage_domains.yaml` and a question in the bank, or the gate fails.
See `docs/runbooks/corpus-coverage.md`.

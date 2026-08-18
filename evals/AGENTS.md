# evals/

Deterministic gates. Do not call a hosted LLM from these jobs.

| Artifact | Path | Gate |
| --- | --- | --- |
| RAG golden set | `Data/eval/rag_eval.jsonl` | retrieval regression |
| Red-team corpus | `Data/eval/redteam_corpus.jsonl` | `test_redteam_corpus.py` — refuse / partial_refuse |
| Routing golden sets | `app.agents.eval_routing` | EN ≥ 0.95 before `agentic_mode` |
| Preference export | `evals/export_preferences.py` | thumbs-down + `officer_reply`; no fine-tune |
| DPO scaffold | `evals/dpo_job.py` | refuses train unless `EVAL_GATE_OK` |

`FLAG_HYDE` / `FLAG_GRAPH_FUSION` stay off until an unseen multi-hop set exists.

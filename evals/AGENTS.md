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
| Spoken language id | `evals/language_id/` + `scripts/eval_language_id.py` | receptionist Phase 0A — measurement, GPU; see its README |
| Orpheus Luganda voice | `evals/orpheus_tts/` + `scripts/bench_orpheus_tts.py` | receptionist Phase 0B — latency here, naturalness by listeners |
| Trilingual multi-turn | `evals/multiturn_trilingual/scenarios.jsonl` | `test_multiturn_trilingual_eval.py` — answer language, English replay, scrubbed history, facts (deterministic); `scripts/eval_multiturn_trilingual.py` measures the GPU stack |
| Trilingual retrieval | `evals/retrieval_multilingual/` + `scripts/eval_retrieval_multilingual.py` | measurement on the GPU stack, **not a gate**: part of the set is machine-translated (see its README); Hit@1/nDCG per leg, abstention, latency |

`FLAG_HYDE` / `FLAG_GRAPH_FUSION` stay off until an unseen multi-hop set exists.

The coverage bank is the one set here that is **not** derived from the corpus —
it probes for subjects the corpus does not cover. A new `ura_*_faqs.csv` needs a
domain in `coverage_domains.yaml` and a question in the bank, or the gate fails.
See `docs/runbooks/corpus-coverage.md`.

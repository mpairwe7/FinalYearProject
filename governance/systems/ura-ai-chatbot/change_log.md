# Change log: models and prompts

Every model swap and every prompt change to the URA AI Chatbot, newest first.
Each row links the decision record that explains it. Add a row in the pull
request that makes the change.

| Date | Kind | Change | Record |
| --- | --- | --- | --- |
| 2026-12-01 | Model (vendor) | Google routes every `gemini-2.5-flash` request to Gemini 3.5 Flash-Lite. `providers/config.py` still names `gemini-2.5-flash` as a default. Scheduled by Google, not by this project. | [Google Gemini API](../../vendors/google-gemini-api/README.md) |
| 2026-11-15 | Prompt (layout) | The answer prompts move from `rag/generator.py` into `rag/prompt_builder.py`. The text does not change. | [ADR-0056](../../../docs/adr/0056-rag-package-layout.md) |
| 2026-11-10 | Model | The FAQ judge and the translation fallback ask Sunflower through Sunbird's API instead of Gemini. | [ADR-0037](../../../docs/adr/0037-sunbird-judge-and-translation.md) |
| 2026-11-09 | Model | Answer generation leads with Sunflower through Sunbird's API, with Workers AI behind it. Gemini no longer generates answers. | [ADR-0036](../../../docs/adr/0036-sunbird-primary-answer-generation.md) |
| 2026-11-09 | Model | A local dense retrieval leg with multilingual-e5-small on ONNX for the CPU deployments. | [ADR-0034](../../../docs/adr/0034-local-dense-leg-pgvector.md) |
| 2026-11-07 | Prompt | A 16-rule governed prompt on the cloud path, which serves production. Prompts state only current, enforced facts. | [ADR-0030](../../../docs/adr/0030-checkpoint-integrity-exactly-once-resume-and-governed-cloud-prompt.md) |
| 2026-11-07 | Prompt | The follow-up rewriter's prompt is fenced and scrubbed, behind a default-off flag. | [ADR-0031](../../../docs/adr/0031-agentic-rag-turn-context-layering-and-parse-sandbox.md) |
| 2026-11-06 | Prompt | Retrieved text cannot close the passage fence on any prompt path, and the cloud fallback fences like the local prompt. | [ADR-0027](../../../docs/adr/0027-spotlight-fence-and-fallback-prompt-boundary.md) |
| 2026-11-01 | Model | The reranker reads a 4,000-character passage window. | [ADR-0024](../../../docs/adr/0024-november-2026-hybrid-rag-memory-sandbox-and-mas-hardening.md) |
| 2026-10-20 | Model | Two local tiers: Qwen3.8-27B-FP8 for reasoning and Sunflower-Qwen3.5-9B for fast and vernacular answers. | [ADR-0007](../../../docs/adr/0007-sovereign-local-reasoning-tier-and-code-owned-tax-logic.md) |

Earlier changes are in the [ADR index](../../../docs/adr/README.md) and the git
history of `App/backend/app/rag/`.

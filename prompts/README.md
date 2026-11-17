# prompts/

The system prompts live in `App/backend/app/prompts/`, one module per area
(ADR-0060):

| Module | Prompts |
| --- | --- |
| `answer.py` | `SYSTEM_PROMPT`, `CLOUD_SYSTEM_PROMPT` (production on the Space), `FAQ_JUDGE_PROMPT` |
| `query.py` | The follow-up rewriter |
| `receptionist.py` | Call query extraction, Luganda answers, the officer brief, the call summary |
| `title.py` | The summary chat title |
| `agents.py` | Specialist, detail-level, governance and tiebreak prompts |
| `vision.py` | The vision model that reads uploaded documents |

The module that uses a prompt imports it from there under the name it had, so
`rag/prompt_builder.py` still assembles the answer messages. Guardrail signatures
in `guardrails/output_checks.py` must stay in sync with any new system-prompt line. There is no
hot-reload YAML store; ADR-0060 says why.

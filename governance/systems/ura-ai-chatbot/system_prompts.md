# System prompt register

Every system prompt the URA AI Chatbot sends to a model: where its text lives,
which call sends it and which test pins it. The text lives in
[`App/backend/app/prompts/`](../../../App/backend/app/prompts/__init__.py), one
module per area (ADR-0060). Checked against the code on 2026-11-18.

| Prompt | Text | Sent by | Pinned by |
| --- | --- | --- | --- |
| Answer system prompt | `answer.py` `SYSTEM_PROMPT` | `rag/generator._build_messages`: every generated answer, local and cloud. On the HF Space that is Sunbird, then Workers AI | `tests/agents/test_harness_prompt_contract.py`; the leak fingerprint |
| Governed cloud prompt | `answer.py` `CLOUD_SYSTEM_PROMPT` | `rag/pipeline._build_fallback_prompt`, which nothing in the app calls since ADR-0036 (G667) | `tests/agents/test_harness_prompt_contract.py`; the leak fingerprint |
| FAQ judge | `answer.py` `FAQ_JUDGE_PROMPT` | `rag/pipeline`, ranking FAQ candidates (ADR-0037) | None |
| Follow-up rewriter | `query.py` `REWRITE_SYSTEM`, `REWRITE_PROMPT` | `rag/query`, behind a default-off flag (ADR-0031) | `tests/agents/test_agentic_rag_context_sandbox_2026_11.py` |
| Call query extraction | `receptionist.py` `QUERY_EXTRACT_SYSTEM` | `receptionist/brain` | `App/backend/tests/test_call_carryover.py` |
| Luganda call answers | `receptionist.py` `LUGANDA_RAG_SYSTEM` | `receptionist/brain` | None |
| Officer brief | `receptionist.py` `BRIEF_SYSTEM`, `BRIEF_FULL_PROMPT`, `BRIEF_INCREMENTAL_PROMPT` | `receptionist/brief` | None |
| Call summary | `receptionist.py` `SUMMARY_SYSTEM` | `receptionist/summary` | `App/backend/tests/test_receptionist_summary.py` |
| Chat title | `title.py` `CHAT_TITLE_SYSTEM` | `chat_title` (ADR-0017) | None |
| Specialist, detail-level and governance fragments | `agents.py` | `rag/generator` (`specialist_prompt`), `rag/pipeline` (`detail_level_prompt`), `governance_prompt` | `tests/agents/test_harness_prompt_contract.py`, `tests/agents/test_specialist_prompts.py` |
| Route tiebreak | `agents.py` `TIEBREAK_PROMPT` | `agents/tiebreak` | None |
| Document reading | `vision.py` `VLM_PROMPT` | `vision/encoder` | None |

## Rules

- **Change control.** A prompt change edits the text in `app/prompts/`, adds a
  row to the [change log](change_log.md), and updates the guardrails' signature
  phrases when a signature line changes ([prompts/README.md](../../../prompts/README.md)).
- **Only current, enforced facts.** A prompt states no rule the code does not
  enforce and no figure: ADR-0030, and G662, where a hard-coded example once put
  stale rates into every answer.
- **Retrieved text is data.** Every prompt path fences retrieved passages so a
  passage cannot close the fence or write a new instruction (ADR-0027).
- **Leak protection.** The output guard checks each reply against signature
  phrases and a word 8-gram fingerprint of `SYSTEM_PROMPT` and
  `CLOUD_SYSTEM_PROMPT` (OWASP LLM07, ADR-0029).

## Known gaps

- `CLOUD_SYSTEM_PROMPT` carries ADR-0030's governed rules, but production answers
  use `SYSTEM_PROMPT`. Whether `SYSTEM_PROMPT` carries every one of those rules is
  open (G667).
- Eight prompts have no test that pins their text: the FAQ judge, the Luganda call
  answer, the three officer brief prompts, the chat title, the route tiebreak and
  document reading.

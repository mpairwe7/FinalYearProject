# tests/

| Tree | Role |
| --- | --- |
| `tests/agents/` | Tool, ticket, reminder, routing |
| `tests/chaos/` | In-process fail-closed drills |
| `tests/test_all_endpoints_e2e.py` | Route manifest |
| `tests/test_corpus_coverage_gate.py` | Corpus-coverage floors, question-bank/registry contract (#303) |
| `tests/test_fallback_integration.py` | Real ChatModel + TestClient over the cloud fallback chain |
| `tests/test_answer_integrity_integration.py` | Real ChatModel + TestClient over locale, MT, withholding, `/v1/escalate` |
| `App/backend/tests/` | API and unit tests next to the host |

Do not add hosted-LLM calls here.

**Coverage lives here, not next door.** CI runs `pytest tests/ --cov=ml
--cov=App/backend --cov-fail-under=35`: it *measures* `App/backend` but only
*runs* this tree. Backend code exercised solely from `App/backend/tests/` is
covered on paper and unprotected in CI, and enough of it drops the gate. New
backend behaviour wants a case in one of the two integration files above as
well as its unit tests.

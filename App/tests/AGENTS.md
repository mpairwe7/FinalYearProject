# App/tests/

| Tree | Role |
| --- | --- |
| `App/tests/agents/` | Tool, ticket, reminder, routing |
| `App/tests/chaos/` | In-process fail-closed drills |
| `App/tests/test_all_endpoints_e2e.py` | Route manifest |
| `App/tests/test_corpus_coverage_gate.py` | Corpus-coverage floors, question-bank/registry contract (#303) |
| `App/tests/test_fallback_integration.py` | Real ChatModel + TestClient over the cloud fallback chain |
| `App/tests/test_answer_integrity_integration.py` | Real ChatModel + TestClient over locale, MT, withholding, `/v1/escalate` |
| `App/tests/test_benchmark_scoring_integrity.py` | The 1,000-FAQ harness's scorer (G57). Lives here, not beside the script, because the defect it guards is a *recurrence*: every correction G37 made to `App/tests/load/tax_education_accuracy_eval.py` was reintroduced in a harness written afterwards. A fix inside one harness is invisible to the next one; a property in the tree CI runs is not. |
| `App/backend/tests/` | API and unit tests next to the host |

Do not add hosted-LLM calls here.

**Coverage lives here, not next door.** CI runs `pytest App/tests/ --cov=ml
--cov=App/backend --cov-fail-under=35`: it *measures* `App/backend` but only
*runs* this tree. Backend code exercised solely from `App/backend/tests/` is
covered on paper and unprotected in CI, and enough of it drops the gate. New
backend behaviour wants a case in one of the two integration files above as
well as its unit tests.

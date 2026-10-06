# Trilingual multi-turn set (G123)

Conversations in English, Luganda and Swahili, scored as conversations, not as
single questions. Each scenario in `scenarios.jsonl` is a sequence of turns.
Each turn carries the answer language and reason it should get (`expect.locale`,
`expect.source`), the English form the server stores for a Luganda or Swahili
turn (`english`), and, where relevant:

| Field | Checked |
|---|---|
| `expect.facts` / `expect.no_facts` | facts long-term memory keeps from the turn (`[category, value]`), or must not |
| `expect.replay` | what the next prompt reads of the turn: `excludes` / `includes` phrases, or `withheld` |
| `expect.live.retrieval_mode` | the route the live stack must take (a refused injection is `blocked`) |
| `client` / `profile_locale` | the client's language at the start (`{"locale", "explicit"}`), and a consented profile language |

The set covers continuity through short and code-switched follow-ups, switching
in every direction, in-message language requests in all three languages, a
language picked in the client, the profile fallback for an ambiguous opening,
facts read from the English form, and poisoned history (plain, obfuscated, and
Swahili via its English form).

```bash
# The gate: deterministic, no model (CI runs it with the backend suite)
cd App/backend && python -m pytest tests/test_multiturn_trilingual_eval.py

# Measurement against a running stack: answer language, reply language, route
docker run --rm --network host -v "$PWD:/src" -w /src --entrypoint python \
    app-api:gpu scripts/eval_multiturn_trilingual.py --base http://127.0.0.1:8083
```

The live run writes `evals/reports/multiturn_trilingual_<date>.json`. Its
"reply language" column is the language most of the reply's known words belong
to. That is how an English template served on a Luganda turn shows up, which
the gate cannot see.

When adding a scenario, phrase it as a taxpayer would, not as the code is
written (see `docs/GAPS_AND_AGENTIC_ROADMAP.md`); and give every Luganda or
Swahili turn the English form the server would store.

---
name: refresh-docs
description: Find and update documentation that has gone stale relative to the code. Use after landing any change that alters behaviour, adds a feature flag, adds or renames a tool, changes a route, changes a rate table, or changes a model/deployment default — and whenever the user asks to "update the docs", "refresh docs", or says docs are stale or out of date.
---

# Refresh stale docs

Documentation in this repository makes **checkable claims** — tool counts,
routing scores, flag lists, model ids, protocol versions, test counts. A claim
that was true when written and is false now is worse than no claim, because
reviewers and URA stakeholders act on it.

The job is not to rewrite prose. It is to find the claims the code no longer
supports and correct those.

## 1. Establish what changed

```bash
git log --oneline "$(git merge-base HEAD origin/dev)"..HEAD
git diff --stat "$(git merge-base HEAD origin/dev)"..HEAD
```

If the branch is already merged or there is no upstream, use the last commit
that touched `App/backend/app/` as the reference point.

## 2. Re-derive the facts, don't recall them

Never edit a number from memory. Get it from the running code:

```bash
cd App/backend
../../.venv/bin/python -c "
import app.tools as t
from app.agents.eval_routing import GOLDEN_SETS, run_routing_eval
from app.flags import _REGISTRY
from app.agents.patterns import supported_locales
tools = t.ToolRegistry.all()
print('tools:', len(tools), 'namespaces:', len({x.schema.namespace for x in tools}))
print('flags:', len(_REGISTRY))
print('locales:', supported_locales())
for loc, cases in GOLDEN_SETS.items():
    r = run_routing_eval(cases, locale=loc)
    print(f'routing {loc}: {r.correct}/{r.total}')
"
../../.venv/bin/python -m pytest tests/ -q 2>&1 | tail -1
```

Add a probe for whatever the change touched — rate tables via
`app.tax.tables.list_fiscal_years()`, MCP protocol version from
`app/mcp/`, model ids from `app/providers/routing.py`.

## 3. Docs that carry checkable claims

Check each against the facts from step 2. Skip any the change cannot have
affected — this is a targeted pass, not a rewrite of the doc tree.

| Doc | Claims it makes |
|---|---|
| `App/docs/mcp-architecture.md` | Namespace table, tool counts, protocol version, deployment column |
| `docs/AGENT_ARCHITECTURE.md` | Route list, supervisor behaviour, tool-loop bounds |
| `docs/RAG_ARCHITECTURE.md` | Pipeline stages, module map, **feature-flag list** |
| `docs/GAPS_AND_AGENTIC_ROADMAP.md` | Gap statuses (⚪ 🟡 🟢) — flip a gap when it ships |
| `docs/NEXTGEN_ARCHITECTURE_PROPOSAL_2026.md` | Appendix C implementation status, test counts |
| `App/README.md`, `README.md` | Model ids, module tree, phase list |
| `.env.example` | Every new env var and flag must appear here |
| `docs/MODEL_SWAP_GUIDE.md`, `docs/DEPLOYMENT.md` | Model ids, tiers, deployment topology |
| `docs/EVALUATION_REPORT.md` | Metric values — update only alongside a real re-run |

Search rather than guess:

```bash
grep -rn "<old fact>" --include="*.md" . | grep -v node_modules
```

## 4. Rules

- **Correct the claim, keep the prose.** Do not restructure a doc while
  fixing a number in it.
- **A new feature flag must land in three places**: the `flags.py` registry,
  the flag table in `docs/RAG_ARCHITECTURE.md`, and `.env.example`. Two out of
  three is the usual failure.
- **A shipped gap flips its status** in `GAPS_AND_AGENTIC_ROADMAP.md` and gets
  struck through, matching the existing 🟢 entries. Do not delete the row —
  the history is the point.
- **Never claim a metric you have not just measured.** If a number cannot be
  re-derived in this session, say when it was measured rather than restating
  it as current.
- **Do not mark work complete that is only partly wired.** If a module exists
  but nothing calls it, the doc says so. This repository's docs distinguish
  🟡 partial from 🟢 shipped; keep that distinction honest.
- **Date what you touch.** Where a doc carries a "verified on" or "updated"
  line, set it to today and name the commit.

## 5. Report

List each file changed and the claim that was wrong, e.g.
`App/docs/mcp-architecture.md — namespace table said 8 tools, registry has 19`.
If a doc was checked and needed nothing, say so; silence reads as "not
checked".

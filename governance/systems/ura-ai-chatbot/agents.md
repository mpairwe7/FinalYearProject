# Agents and workflows

Each agent role in the URA AI Chatbot, each workflow, what they may do on their
own and where a person must approve. The code is in
[`App/backend/app/agents/`](../../../App/backend/app/agents/__init__.py) and
[`App/backend/app/workflows/`](../../../App/backend/app/workflows/__init__.py)
(ADR-0061). Checked against the code on 2026-11-18. Flag defaults are the
registry's; a deployment can set others.

## Agent roles

| Role | Module | Job | Flag (default) |
| --- | --- | --- | --- |
| Supervisor | `agents/supervisor.py`, with locale tables in `agents/patterns/` | Routes each turn: `rag`, `tools`, `tax_specialist`, `customs_specialist`, `clarify`, `escalate`, `greet` or `blocked` | `agentic_mode` (on) |
| Tiebreak | `agents/tiebreak.py` | Asks a model for a second opinion when the supervisor's rules are unsure | `supervisor_llm_tiebreak` (off) |
| Critic | `agents/critic.py` | Checks an answer before the taxpayer reads it: unverified figures are removed, and a revision has a budget | `evaluator_optimizer` (off) |
| Loop control | `agents/loop_control.py` | Budgets, thrash suppression and context compaction for the agent loop | With the loop |

Each role's prompt is in the [system prompt register](system_prompts.md).

## Workflows

| Workflow | Module | Job | Flag (default) |
| --- | --- | --- | --- |
| Turn graph | `workflows/graph/` | Plan, act, observe, reflect, respond for one chat turn: a bounded, in-process state machine in the style of LangGraph | `langgraph` (off), with `agentic_mode` |
| Guided journeys | `workflows/registry.py`, `loader.py`, `slots.py`, `journey_copilot.py` | 21 step-by-step taxpayer journeys defined in YAML (`workflows/flows/`): returns, payments, clearance, calculators | Always on |

## Where a person decides

- **Actions with effects.** A tool action the graph suspends needs the taxpayer's
  approval. The approval is sealed with an HMAC over the action shown, runs once,
  and expires after `GRAPH_APPROVAL_TTL_S`, 600 seconds by default
  (`workflows/graph/runtime.py`, ADR-0030).
- **Tool authority.** `mcp/policy.py` decides at dispatch what a tool call may do,
  from the scopes, roles and confirmation the tool declares, even when a
  prompt-injected model names a tool it was never shown. Risk-tiered autonomy
  gates (ADR-0022) run when `risk_tiered_autonomy_gates` is on (off by default).
- **A person instead of the agent.** The escalation policy offers an officer,
  and an officer who takes over silences the AI ([OWNERS.md](../../OWNERS.md),
  [controls](../../controls/README.md)).

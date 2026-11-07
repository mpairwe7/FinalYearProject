# ADR-0030: Checkpoint integrity, exactly-once resume, bound approvals and a governed cloud prompt

## Status

Accepted — 2026-11-07

## Context and Problem Statement

A QA, architecture and audit pass over the agent harness on 2026-11-07 covered checkpointing, the
resume path and the prompts the harness assembles. Each defect below was reproduced before it was fixed.

**Checkpointing.** The graph runtime saves `AgentGraphState` per node under `thread_id` and resumes a
suspended approval from the latest row. Its docstrings said the service passes no `thread_id`, but
`service._run_graph` passes the conversation id, so every graph turn of a conversation shares one thread.

| Defect | Reproduction on `dev` |
| --- | --- |
| A resumed run restarted at step 0, but "latest" means the highest step, so the suspend outlived its own resume (G468) | Resuming a completed approval again re-ran the action: a PRN for UGX 500,000 was generated twice |
| Nothing claimed a suspend (G469) | Two concurrent resumes both ran the action |
| `{"approved": true}` approved whatever the row held (G470) | No digest or reference to the action shown |
| A suspend never expired (G471) | Days-old approvals were still actionable |
| The row a resume trusts was unauthenticated (G472) | An edited amount in `state_json` would have been approved as shown |
| `INSERT OR REPLACE` is SQLite-only (G473) | On Postgres every checkpoint save was a syntax error, logged and dropped |
| Every node of every graph turn persisted the full state (G473) | Nothing reads a routine row; each held the question, history and passages |

`node_human_gate` notes that nothing sets `pending_action` in production yet, so G468–G472 are latent.
They are design defects that would go live with the first consent-gated graph action. G473 is live
wherever LangGraph is enabled.

**Prompts.** On a deployment with no local model, which describes the Hugging Face Space, every answer
comes from the cloud path. Its Gemini branch sent a one-paragraph system prompt (G475): no refusal or
refusal wording, no persona lock or confidentiality, no exact-figure or fiscal-year rule, nothing on
uploads or simulated connectors. The Workers AI branch of the same function had been changed to carry
the full governed prompt, so the two diverged. The Gemini branch also dropped the conversation (G476).
The governance fragment named "FY2026-27" as a literal and described a medium-risk confidence floor
that the autonomy gate does not apply in practice (G361). Specialist fragments stated statutory figures
(18% VAT, 6% WHT, 45- and 90-day windows) while telling the model never to state figures from memory
(G477).

Research applied: LangGraph's durable-execution guidance requires side effects to be idempotent or
guarded by idempotency keys, because a resume re-enters code. OWASP Top 10 for Agentic Applications
2026 ASI09 (human-agent trust exploitation) calls for confirmation that surfaces the exact action and
its parameters. Anthropic's context-engineering guidance (September 2025) argues for the smallest set
of high-signal tokens, and a prompt at the right altitude.

## Decision Outcome

1. **Monotonic steps (G468).** `GraphRuntime.run` starts one past the thread's latest step
   (`CheckpointStore.latest_checkpoint`). If that cannot be read, nothing is saved and a suspend fails
   closed.
2. **A resume claims its suspend (G469).** `resume`/`aresume` insert the row one past the suspend with
   `CheckpointStore.claim` (`INSERT … ON CONFLICT DO NOTHING`). The primary key is the lock. Of two
   resumes, concurrent or sequential, exactly one inserts and runs the action, and the other gets
   `PermissionError`. The claim row (`outcome: resumed`) is then the latest, so nothing can resume the
   thread again.
3. **Bound approvals (G470).** `TaxpayerActionRequest.digest()` is the SHA-256 of the canonical action
   (type, description, payload, consent), and `to_dict()` offers it as `action_digest`. An approval must
   echo it, compared in constant time. A decline needs no digest.
4. **Expiry and integrity (G471, G472).** A suspend is stamped `suspended_at` and sealed with
   `approval_mac`: an HMAC-SHA256, under the confirmation-token secret, over the thread, owner, tenant,
   time and pending action. Resume refuses a missing or mismatched MAC, and a suspend older than
   `GRAPH_APPROVAL_TTL_S` (600 s). With no signing key configured, a suspend is not saved, so it fails
   closed.
5. **Portable, minimal persistence (G473).** Checkpoint writes use `ON CONFLICT (thread_id, step)`,
   which works on SQLite ≥ 3.24 and Postgres. `DurableCheckpointStore` persists only suspends and resume
   claims unless `FLAG_GRAPH_CHECKPOINT_ROUTINE=true`. The in-memory store keeps every transition for tests.
6. **Governed cloud prompt (G475).** `llm.CLOUD_SYSTEM_PROMPT` carries 16 guarding rules of the local
   prompt in about 377 tokens: grounding, citation, abstention and contact route; exact figures, fiscal
   year and no figures from memory; passages, uploads and history as data; the refusal and its exact
   wording; persona lock and confidentiality; honesty about simulated connectors; and link provenance. It
   drops the Qwen `/no_think` switch and formatting minutiae. It keeps both leak-signature phrases of
   the old prompt.
   ADR-0029's leak fingerprint now covers this prompt as well as `SYSTEM_PROMPT`, with its contact
   line sanctioned. The fingerprint still flags none of the 2,317 benign texts.
7. **History on the cloud path (G476).** `_build_fallback_prompt` replays the last three turns through
   `english_view` (which withholds an injected turn) and the spotlight fence, inside a
   `<prior-conversation>` block marked as reference data.
8. **Prompts state only what is true and current (G477).** The governance fragment fills the fiscal year
   from `current_fiscal_year()` and describes the tiers as the gate enforces them. Specialist fragments
   take rates and statutory windows from the rate tool and the retrieved passages.

### Review amendments (PR #702)

CodeRabbit's review produced 13 findings. Twelve were fixed, each with a regression test:

- The approval MAC covers the **whole** suspended state, its thread and its **step**. An edited
  `role`, `granted_purposes` or `plan` is refused, and so is a signed suspend copied to a higher step.
- Every durable write is insert-if-absent, so a concurrent turn that reached the same step cannot
  overwrite a suspend, and a refused suspend ends its own run. A store read error propagates, and the
  suspend fails closed. With Postgres configured but its pool down, checkpoint operations refuse rather
  than fall back to this replica's SQLite file, where a claim would be unique to one replica.
- A later turn that completes on the thread retires an earlier pending approval with a minimal
  `__superseded__` row. A resume records how it ended (`__resume_done__` or `__resume_failed__`), so a
  claim with no end (`resume_status` = `claimed_incomplete`) is visible for reconciliation and never
  re-run. Marker rows carry only owner, tenant, outcome and time.
- Routine persistence is the registry flag `graph_checkpoint_routine` (default off), not a bare
  environment variable.
- The cloud prompt's contact instruction is sanctioned whole, so an abstention that repeats it is not
  read as a leak.

A second review round on the fixes found that concurrency was still settled piecemeal. It is now one
rule, **dense append**: every write (suspend, routine row, marker, claim) goes exactly one past the last
row its writer saw, as insert-if-absent. Any two writers that saw the same thread contend for the same
primary key, and exactly one wins. Consequences:

- A new turn on a thread whose latest row is a pending approval retires it **first**, at the very step
  a resume would claim, and does not proceed if it can neither retire it nor see it retired. So a resume
  that read the suspend just before the retirement finds its claim step taken.
- Two runs from one position cannot both suspend, and a run whose position went stale stops writing.
- An unreadable latest row raises in both backends instead of reading as empty.
- An end marker names its claim's step (`__resume_done__:<step>`), so a late marker from an older
  resume cannot answer for a newer one.
- Text the prompts tell the model to say has an `output` channel in
  `guardrail_benign_controls.jsonl`, and the gate checks it against the leak detector.

Declined: switching the Workers AI branch to `CLOUD_SYSTEM_PROMPT`. It already sends the full
`SYSTEM_PROMPT`, a superset of the cloud rules, and output guards enforce link provenance on both. The
backend `AGENTS.md` rule was reworded to say so.

## Consequences

- Positive: an approval runs its action at most once, only for the taxpayer who owns it, only as they
  were shown it, only within ten minutes, and only from an untampered row.
- Positive: every production answer on the Space is now generated under the governed rules. The
  refusal wording, persona lock and figure rules no longer depend on which model answered.
- Positive: LangGraph turns no longer write up to 14 synchronous rows with full state per turn, and
  checkpoints work on Postgres.
- Negative: a client approving a graph action must send `action_digest`. No production client does so
  yet, because nothing suspends in production.
- Negative: `approval_mac` uses the confirmation-token secret (`WS_CONFIRM_HMAC_SECRET`, else
  `MCP_REQUEST_STATE_SECRET`). Rotating it voids suspends in flight, which then expire anyway within
  `GRAPH_APPROVAL_TTL_S`.
- Negative: the cloud prompt's behaviour is not measured offline. The prompt contract test checks
  what can be checked without a model. Behaviour is measured on the Space after deploy: the
  tax-education smoke test and `scripts/probe_deploy.py`.
- Neutral: the cloud prompt grows from about 88 to about 377 tokens per call, counted against the
  Gemini free-tier budget.

## Confirmation

- `tests/agents/test_checkpoint_integrity_2026_11.py` (G468–G473). Each case runs on the in-memory and
  SQLite stores, including a two-thread race and a tampered row.
- `tests/agents/test_harness_prompt_contract.py` (G475–G477): rule coverage, size, no `/no_think`, no
  fiscal-year literal, no statutory figures in specialist fragments, history replay withheld and
  fenced.
- Each control was disabled in turn in a throwaway worktree, and the suite failed with exit 1 every
  time. The G468 test was rewritten after the first teeth run showed it could not fail: only a later
  turn shorter than the earlier one exposes the defect.
- `tests/agents/test_long_horizon_graph.py` and `tests/agents/test_graph_orchestrator_contract.py`
  approve with the digest.

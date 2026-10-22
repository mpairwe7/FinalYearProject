# ADR-0013: Session-Bound Workflow Adoption, Request-Bound Sign-In State, Data-Minimised Session Slots & Verified Resume Codes

- **Date:** 2026-10-22
- **Owner:** Cloake Tech Limited — Core Agentic CX & Workflow Architecture Team, Platform Assurance
- **Amends:** [ADR-0010](0010-agentic-journey-copilot-prerequisites-a2ui-and-step-up-auth.md) Pillars 1, 5 and 7

## Status

Accepted

## Context and Problem Statement

An audit of the #619 QA remediations, together with the #621 follow-up, found five defects in how the Journey Copilot carries a guided workflow across sign-in and channels. Each one was reproduced against `dev` before it was fixed.

1. **Cross-user takeover on sign-in (QA-1).** A taxpayer who signs in mid-flow gets a user-scoped state key, while their answers sit under the anonymous one. The migration also tried the *unsessioned* anonymous key, which is derived from the conversation id alone. So any signed-in caller who held another taxpayer's conversation id could adopt that taxpayer's unowned anonymous workflow, answers included. An anonymous caller cannot reach that row through `/v1/chat`, so the migration widened access rather than preserving it.
2. **Funnel and privacy side effects of the migration.** The row was copied to the new key and the original was marked `cancelled`. Each sign-in therefore counted as one abandoned journey plus one new one in `get_journey_funnel` (which reads `workflow_sessions`), and the taxpayer's answers stayed duplicated under the anonymous key until the retention sweep.
3. **Sign-in state persisted on the session.** Step-up auth read `is_authenticated` from an `_authenticated_user_id` slot. The slot was set on signed-in turns but never cleared, so a stored marker, rather than the current request, decided whether the caller was signed in.
4. **Case facts copied into durable storage for nothing.** Every workflow turn wrote the whole `CaseFacts` (salary, turnover, residency days, benefits) into `slots_json["_case_facts"]` for the prerequisite DAG. No DAG slot matches a `CaseFacts` field, so the copy was never read.
5. **Resume codes that collide and are never checked (QA-3).**
   - The code prefix was the first three letters of the workflow id, so `tin_procedure_help` codes opened `tin_registration`. The nine calculators all became `CAL`, which nothing mapped back, so their codes did nothing.
   - The four-hex check was never verified. Since QA-3, any code-shaped string (a typo, or `URA-PAY-S1-1234`) pulled the taxpayer out of the flow they were in and cancelled it.

## Decision Outcome

1. **Adopt only what the request can prove.** The single anonymous candidate key is `conversation_state_key(thread_id, session_id=<this request's session>, user_id=None)`. That is the same capability the anonymous owner used, and the unsessioned key when no session id is present (OWASP WSTG-SESS-03: carry pre-authentication state to the post-authentication identity only when it is bound to the same session).
2. **Re-key in place, as a compare-and-swap.** `adopt_workflow_session(from, to, user_id, tenant_id, min_updated_at)` exists in both SQLite and Postgres, kept in step by the `StorageBackendProtocol` parity check (ADR-0012). It runs a single `UPDATE ... WHERE user_id = '' AND status = 'active' AND updated_at >= cutoff AND NOT EXISTS(target)`. The result:
   - one journey, which keeps its `created_at`;
   - no copy left behind;
   - concurrent sign-ins cannot both win;
   - rows past retention are never adopted.
3. **Sign-in is a property of the request.** `_apply_personalization_to_workflow` sets the marker on signed-in turns and removes it on anonymous ones, so the step-up check reflects the current caller.
4. **Data minimisation for case facts.** `prerequisite_case_facts()` keeps only the fields a DAG prerequisite reads (none today), and a legacy full copy is scrubbed on the row's next turn (GDPR Art. 5(1)(c); Uganda DPPA 2019 s.3).
5. **Unique, verified resume codes.**
   - `RESUME_CODE_PREFIXES` gives each of the 21 shipped workflows its own prefix; the 11 already issued are unchanged.
   - The check is `sha256("ura-resume:<prefix>:<step>")[:4]` and is verified on parse, so unknown prefixes, `S0` and mismatched checks are rejected.
   - Codes no longer hash slot names. The check is an integrity check, not an authenticator: a code names a public workflow and grants nothing, so it needs no secret.
   - The step number remains informational. Cross-channel restore of answers needs an authenticated, server-side lookup and is out of scope here.

## Consequences

- **Positive:**
  - The sign-in path can no longer be used to read or continue another taxpayer's answers.
  - The journey funnel stops over-counting abandonment on sign-in.
  - Salary and turnover figures no longer reach `workflow_sessions`.
  - Every workflow's resume code works, and a stray string cannot cancel an active flow.
- **Negative / Trade-offs:**
  - Codes issued before this change fail verification, because their check covered slot names. They were live only from #619 to this change, and a rejected code falls through to normal handling.
  - Adoption now requires the client to present the same anonymous session (the web app sends `X-Session-ID`; without it `/v1/chat` falls back to the conversation id).
  - Each new workflow YAML needs a prefix entry; a test fails otherwise.

## Confirmation

Validated by:
- `tests/agents/test_journey_copilot_session_binding.py` (5 of its 7 cases fail on the pre-change code, including the cross-session takeover)
- `tests/agents/test_journey_copilot_resume_codes.py`
- `tests/agents/test_journey_copilot_pillars.py`, `tests/test_guided_journeys_integration.py`
- `tests/test_monorepo_architecture_governance.py` (storage-backend parity)

## Amendment A (2026-10-23): one owner-scoped key for every per-conversation store

A third pass over the #619 line found that the rule above protected the workflow row but not the stores beside it. Conversation history already said so in code: `get_recent_turns` binds anonymous history to the session because *"a client-supplied conversation id alone is not an authorization credential"*. Three stores did not follow it. Each defect was reproduced through `/v1/chat` on the #627 head before it was fixed.

1. **Case facts crossed sessions.** `CaseFacts` (salary, turnover, residency days) were keyed by tenant, user and conversation, with no session. A second anonymous session that held only the conversation id was handed the first session's salary in its own PAYE answer. The personalisation recall also used a different key from the store, so it never matched.
2. **Calculator follow-up memory was keyed by the bare conversation id.** Any caller holding the id replayed someone else's calculator ("what about 2 million?").
3. **The workflow engine still read rows under the bare conversation id.** The startup migration (`migrate_legacy_conversation_state_keys`) leaves exactly the rows it cannot attribute to one owner. A stranger resumed such a row and inherited its answers. The "Continue <flow>" action also tried the bare id and the unsessioned key and ignored retention, so it offered flows the resume would refuse.

Not exploitable, but worth recording: a client could in principle send a derived `state_<sha256>` key as its conversation id and have the bare-id fallback read that row directly. Both entry points cap `conversation_id` at 64 characters and a state key is 70, so this was blocked only by that accident. Removing the fallback makes the guarantee structural.

**Decision.**
- Case facts and calculator memory use the request's owner-scoped state key, the one workflow state uses.
- The engine reads only that key. Unattributable legacy rows are left to the retention sweep.
- The "Continue" action and the resume share one key and one retention rule (`_workflow_past_retention`).
- `ResolutionLedger.promote_anonymous_session` moves the workflow with `adopt_workflow_session` instead of copying it, so there is one way to carry a flow across sign-in. It has no production caller yet.

**Trade-off.** A legacy row that the migration could not attribute is no longer resumable by anyone. Owner-scoped keys shipped on 2026-10-03 (#529), so with the default 7-day `CONVERSATION_TTL_DAYS` every such row was already past retention, and the resume refused it anyway. A deployment that raised the retention could lose an in-flight legacy journey, and the taxpayer would start that flow again.

**Confirmation.**
- `tests/agents/test_conversation_state_owner_binding.py` drives `ChatModel.generate`, so the wiring is tested, not only the helpers. 11 of its 12 cases fail on the pre-change code. The twelfth, a signed-in stranger asking about the salary, passes there too, because that key already carried the user id.
- Driving `ChatModel.generate` with a salary-bearing question also exposed six log statements (`premise_guard.check_false_premise`, `query.rewrite_with_history`, `HybridRetriever.search_with_translation` ×2, and `service._simple_search` ×2) where raw question or translated-question text reached the logger (CodeQL `py/clear-text-logging-sensitive-data`, alerts #2623–#2630); the regex PII filter does not catch a stated salary or name, so those calls now log `query_length` instead of the question text.
- Re-run live through `/v1/chat`: the owner's next turn keeps the salary and the VAT follow-up, and strangers get neither.

# Plan: Agentic CX, October 2026 — from capability to outcome (Phase 38+)

Status: **Phase 38 implemented** (§4.1; delivery log in §10); Phases 39–41 proposed.
Written 2026-10-15 against `dev` @ `1ebcd606f0` (after Phase 35–37, PRs #571–#575, and the connector
work in #576, G146–G155). Line numbers are approximate; search for the named symbol if one has moved.
Tracking issue: #441. Gap register rows: G156–G164.

This is the October 2026 agentic-CX review of the URA assistant (12-stage pipeline, supervisor +
specialists, 4-tier memory + `ResolutionLedger`, guided workflows, MCP tools, en/lg/sw/nyn/ach, full
speech pipeline). It audits what is **wired into the serving path**, not what exists in modules, and
plans the work in phases that each end in something measurable.

---

## 1. Summary — the highest-impact gaps

Phases 31–35 built the right primitives. The gap is not missing modules; it is that several of them
do not reach the channel most taxpayers use, or measure themselves instead of the taxpayer.

| # | Gap (verified in code) | Trend | Impact |
|---|---|---|---|
| 1 | **Web chat never opens or updates a resolution case.** `service.py` only *reads* the legacy single key `active_resolution_case` into the prompt (≈4445). The writers are voice only: `voice_stream.py` (≈509) creates a **new case every turn** with the raw query as its objective (unredacted, and it evicts real cases past `RESOLUTION_MAX_CONCURRENT_CASES=5`); `receptionist/carryover.py` (≈172). `select_case_for_turn`, `list_active_cases`, `promote_anonymous_session` have no caller. | 2 | The "longitudinal relationship" does not exist for text. |
| 2 | **Emotion and stakes do not change who gets help, or how fast.** `OutputGuard.should_escalate` (guardrails ≈1020) looks at faithfulness and retrieval hits only; its `consecutive_low` parameter is never passed. Sustained distress adds a "Talk to an officer" button (turn_guidance ≈155). A taxpayer who presses it gets a ticket built by `POST /v1/escalate` (main ≈2795) with `topic: general`, `priority: normal`, no sentiment, no required details. Voice has a real risk model (`receptionist/risk.py`); text does not. | 3 | The distressed or high-stakes taxpayer queues behind everyone else, and the officer opens cold. |
| 3 | **Text tickets carry the legacy handoff packet, not `HandoffEnvelope` v2.1.** `_build_handoff_packet` (service ≈6840): static `required_details` per topic (not pruned against what the taxpayer already gave), priority ignores affect. The envelope — verified slot bank, affect trajectory, tool receipts, SLA, `zero_repetition_score`, completeness metric — is built only for voice transfers (`receptionist/transfer.py` ≈136). | 4 | The officer re-asks; "clean handoff" cannot be measured for text. |
| 4 | **The Phase 31/35 autonomy features run only under `FLAG_LANGGRAPH`.** Multi-agent DAG execution, memory-backed `bind_arguments`, and `autonomous_outcome_verification` live in `agents/graphs/main_graph.py`, which runs only when `langgraph` **and** `agentic_mode` are on (service ≈7093, ≈9731, ≈11284). `langgraph` defaults off and is off in every compose overlay. The serving path is `llm.generate_with_tools`. | 1 | Benchmarks of those features measure code taxpayers never reach. |
| 5 | **The continuous QA scorecard mostly scores constants.** `audit/turns.py` (≈201) passes only figure and policy verdicts; empathy is always 1.0, completeness always 1.0, handoff always `None`; "statutory accuracy" is 1.0 whenever a reply has a source or contains "UGX" or "%" (governance_v3 ≈468). The 1.0000 mean over 18,000 synthetic turns reflects that. | 5 | A green dashboard that cannot go red. |
| 6 | **The quality circuit breaker is per-replica and global.** One `figure_fidelity_rescued` turn among the last 20 throttles autonomy for every taxpayer on that replica (governance_v3 ≈298). | 5 | Either never trips (multi-replica) or trips on a single near-miss. |
| 7 | **Nothing continues after the session ends.** `app/scheduler` is a scaffold. Continuation exists only when an MCP task completes or a transfer times out, and it writes an in-app reminder. | 2 | "Agents keep working after the call" is not true yet. |
| 8 | **No outcome metrics.** There is `chat_turns_total{outcome}`, `escalation_required_total`, AI thumbs and officer CSAT. There is no autonomous-resolution rate (case level), no repetition/effort signal for text, no clean-handoff rate for text. | 5 | The workforce shift cannot be steered. |

Already strong, and kept: statutory figure protection (sentinels, `spoken_figure_guard`, evaluator–
optimizer), PII redaction and the turn-scoped TIN exemption (G143), the premise and jurisdiction guards,
consent-gated memory (`has_active_consent`), the tamper-evident audit ledger, the language-state machine
and English-router form, and the read-only, code-routed connector surface (G138–G153).

---

## 2. Trends → where the system stands

| Trend (Oct 2026) | Present and wired | Present, not wired | Missing |
|---|---|---|---|
| 1. Autonomous execution | Tool loop on vLLM with named forced calls for connectors, argument guard, outcome wording by code; risk-tiered autonomy gate (Phase 34) at `ToolRegistry.call`; workflows with specialist bridge and tangent resume | DAG execution, memory-backed binding, outcome verification (graph only, gap 4) | A shadow path to promote the graph on evidence |
| 2. Continuous relationships | Consent-gated 4-tier memory; voice ↔ web carryover; reminder inbox | Multi-case ledger, anon→auth promotion (gap 1) | Text-channel cases; a durable follow-up worker (gap 7) |
| 3. EQ | Lexical distress (en + vernacular cues), trajectory, multimodal fusion for voice, affect-conditioned MT register, relief-boosted retrieval | Envelope EQ priority/SLA (voice only) | Emotion- and stakes-aware escalation for text (gap 2) |
| 4. Orchestration & handoffs | Supervisor + specialists; officer co-pilot drafts; preference capture; transcript + English digest on tickets | `HandoffEnvelope` v2.1 (voice only) | Envelope on text tickets; one handoff builder for every path (gap 3) |
| 5. Metrics & workforce | Per-turn audit rows, officer CSAT, AI thumbs, language-decision metrics | Scorecard dimensions (gap 5), breaker (gap 6) | Case outcomes, effort, clean-handoff rate (gap 8) |

---

## 3. Principles for every phase

- **On-premises first.** Every core path runs on the local stack (Sunflower on vLLM, Whisper-SALT,
  Orpheus/Spark, Qdrant, Redis, Postgres/SQLite). External services stay circuit-broken last resorts.
- **Flag everything, address it.** New behaviour lands default **off** in `flags.py`, subject-addressable
  (`FLAG_<NAME>_PERCENT/_COHORTS/_ALLOWLIST`), documented in `docs/RAG_ARCHITECTURE.md` and `.env.example`.
  Ramp: `ura_staff` cohort → 5% → 25% → 100%, each step gated on the metrics in §8.
- **Statutory accuracy outranks autonomy.** No change may bypass figure protection, the premise guard,
  grounding/faithfulness checks, PII redaction or the audit ledger. Escalation is offered, never imposed,
  except for the existing crisis path.
- **Measure the taxpayer, not the pipeline.** A metric that cannot fail is removed or fixed, not reported.
- **One builder per concept.** One escalation policy, one handoff envelope, one case ledger — shared by
  text, voice and the officer desk.

---

## 4. Prioritised enhancements

### 4.1 Quick wins — Phase 38 (implemented with this plan)

| ID | Enhancement | Trends | Flag (default) | Closes |
|---|---|---|---|---|
| QW1 | **Text resolution cases.** A turn that *does* something — a guided workflow step, a connector lookup, a calculation, a hand-off (`case_update_for_turn`) — opens or moves **one case per tax domain** via `ResolutionLedger.upsert_turn_case`, on REST (`ChatModel.generate`) and the stream (`_finish_streamed_turn`); the objective is a fixed phrase from `DOMAIN_OBJECTIVES`, never the message. Voice (`voice_stream.py`) uses the same upsert instead of minting a case per turn. The prompt names the case the question is about (`case_for_prompt`, any mentioned domain). | 2 | `continuous_relationship_memory` (existing, off) | Gap 1 · G156, G157 |
| QW2 | **Emotion- and stakes-aware escalation policy** (`app/escalation_policy.py`). One deterministic assessment from affect (kind, sustained — read without enforcement phrases so one fact is not counted twice), stakes (`text_signals.detect_high_stakes`: enforcement, deadline today, large amount; en/lg/sw), effort (long conversation) and confidence (abstention, low faithfulness). Tiers: `none` → `offer` → `priority` (offered first; ticket `high`/`urgent`, shorter SLA). Applied in `apply_turn_guidance` and, through `officer_handoff.strongest_assessment` (last three messages), to every ticket — so the button's empty "Talk to an officer" does not erase what was said before it. Never escalates by itself. | 3, 4 | `eq_stakes_escalation` (new, off) | Gap 2 · G158 |
| QW3 | **One handoff envelope for every path.** `officer_handoff.enrich_handoff`, called once in `_maybe_create_ticket` (auto-escalations and `POST /v1/escalate` alike), attaches `HandoffEnvelope` v2.1: affect, SLA from QW2, case objective, and the details to ask for **pruned against what the taxpayer already gave** (the conversation's workflow slots plus the consented slot bank). What was given travels as slot **names**; values and the TIN are reduced to "provided". `completeness` and `zero_repetition_score` ride on the handoff. The officer's ticket view shows why, how fast, and "already given — do not ask again". | 4 | `structured_text_handoff` (new, off) | Gap 3 · G159 |
| QW4 | **Honest scorecard inputs.** `audit/turns.py` (`_scorecard_evidence`) passes the turn's distress, relief cited, completeness by outcome (answer 1, clarification/hand-off ½, refusal 0), handoff completeness, and an explicit `deterministic_basis`; "statutory accuracy" is no longer 1.0 for containing "UGX" (the legacy heuristic stays only for a caller that passes nothing). | 5 | `continuous_quality_scorecard` (existing) | Gap 5 · G160 |
| QW5 | **CX outcome counters.** `ura_cx_case_events_total{channel,domain,event}` (opened, updated, escalated) and `ura_cx_escalation_tier_total{tier,trigger,path}` (path `offer` or `ticket`). No new store: counters only. | 5 | none (observability) | Gap 8 (partly) · G164 |

### 4.2 Medium term — Phases 39–40

| ID | Enhancement | Trends | Flag |
|---|---|---|---|
| M1 | **Promote the graph runtime on evidence.** `langgraph_shadow`: run `build_main_graph()` off the critical path for a sampled share of turns, record agreement with the served answer (route, tool calls, figures, abstention) to the audit ledger and `ura_graph_shadow_agreement_total`. Promote to `langgraph` per cohort only above an agreement floor and with zero figure disagreements. Until then, port the one piece that needs no graph — memory-backed argument binding — into `generate_with_tools` behind `autonomous_outcome_verification`. | 1 | `langgraph_shadow` (new, off) |
| M2 | **Durable follow-up worker** (`app/scheduler/followups.py`). A `followups` table (case id, due at, kind, attempts) and an in-process async loop in the API lifespan, single-leader via a Redis lock (works air-gapped; no Temporal). Kinds: officer replied → in-app card + optional push; case step pending > N days → localized nudge; filing deadline approaching for a case's tax head. Consent-gated, quiet hours, idempotent. | 2 | `followup_worker` (new, off) |
| M3 | **Scoped circuit breaker.** Key the quality breaker by tool risk tier and namespace, back it with Redis so replicas agree, and treat a *rescued* figure as a near-miss counter, not an immediate global throttle. | 5 | `quality_circuit_breaker` |
| M4 | **Repetition and effort for text.** Port the voice repeated-question similarity (`receptionist/risk.py`) to text turns (normalised ≥ 0.8 against the last N user turns) and feed it to QW2 and the scorecard. | 3, 5 | `eq_stakes_escalation` |
| M5 | **Anon → auth promotion wired.** On sign-in with personalization consent, call `ResolutionLedger.promote_anonymous_session` so a case opened anonymously follows the taxpayer. | 2 | `continuous_relationship_memory` |
| M6 | **CX scorecard API and console panel.** `/v1/admin/cx/scorecard`: autonomous resolution rate (cases resolved without an officer / cases closed), clean-handoff rate (completeness ≥ 0.83 and zero-repetition = 1.0), repetition rate, AI vs officer CSAT, officer minutes on high-EQ cases. | 5 | none |

### 4.3 Architectural — Phase 41+

| ID | Enhancement | Trends |
|---|---|---|
| A1 | **Goal-driven case agent.** A case (`ResolutionCase`) owns a `GoalSpec` (state v2) and a plan; each turn the supervisor advances the plan (retrieve / tool / workflow step / ask / escalate), verifies the outcome against the goal, and records a decision receipt. Promotion path: M1. | 1, 4 |
| A2 | **Officer + AI as one queue.** Tickets and cases in one work queue with skills routing (language, domain, EQ load), AI pre-work on every ticket (draft, verified facts, missing inputs), and officer actions that feed the preference store (Phase 35). | 4, 5 |
| A3 | **Write actions through confirmation.** The 13 writing connector operations (issue invoice, generate PRN, …) only through a confirmation-capable flow with the autonomy gate at Critical, idempotency keys, compensation (`SystemConnector.compensate`) and a decision receipt — and only against a reviewed live service. | 1 |
| A4 | **Native-speaker evaluation loop** for lg/sw (and nyn/ach) before any EQ or relationship feature is promoted in those languages. | 3 |

---

## 5. Architecture deltas

**Supervisor, routing and tool loop.** No change to the 12 stages. QW2 runs after Stage 9 (output guards)
on every exit, beside `apply_turn_guidance`, and only *adds* to `next_actions` and to the handoff. M1 adds a
shadow branch after Stage 7 that never writes to the reply.

**Memory and relationships.** `ResolutionLedger` gains one upsert entry point, `upsert_turn_case(user_id,
domain, objective, step, status, channel)`, used by text (QW1) and voice. Cases are keyed by `domain_tag`;
an update never creates a second case for the same domain. Objectives come from a fixed table
(`DOMAIN_OBJECTIVES`), so no raw text is stored. All reads and writes stay behind `has_active_consent(...,
"personalization")`.

**HandoffEnvelope.** No new fields. Text paths now fill: `goal`, `caller_affect`, `affect_trajectory`,
`verified_slot_bank`, `next_action.required_inputs` (pruned), `sla`, `case_reference`, `eq_guidance`
(from QW2's reasons). The ticket keeps the legacy packet keys for the existing UI and gains
`handoff.envelope`.

**EQ.** `escalation_policy.assess_escalation()` composes the existing signals — `detect_user_distress`,
`distress_trajectory`, `tools.empathy.assess` — with two new deterministic detectors: stakes
(`text_signals.detect_high_stakes`: enforcement, seizure, garnishment, closure, deadline today, ≥ UGX 10 M
at issue) and effort (turn count, clarification count; M4 adds repetition). It returns `{tier, priority,
sla_seconds, reasons[], offer_text_key}`. No model call; same input, same output.

**Assistant flows.** Unchanged for Phase 38. M2 adds the follow-up worker; the reminder inbox remains the
delivery surface, and push/SMS stay opt-in and outside the air-gapped core.

**Officer collaboration.** The ticket view shows the envelope's priority, SLA countdown, affect
trajectory and "already given" slots. Co-pilot drafts (Phase 32) are unchanged.

**Metrics, observability, governance.** QW4 and QW5 as above; every QW2 decision is appended to the audit
ledger as part of the turn row (`escalation_tier`, `escalation_reasons`), digests only.

---

## 6. Feature flags

| Flag | Default | Addressable | Rollout | Kill criteria |
|---|---|---|---|---|
| `eq_stakes_escalation` (new) | off | yes | `ura_staff` → 5% → 25% → 100% over 4 weeks | offer rate > 2× baseline without CSAT gain; any crisis turn routed through it |
| `structured_text_handoff` (new) | off | yes | with `ticket_queue`; officer pilot cohort first | officer-reported wrong "already given" > 1% |
| `continuous_relationship_memory` (existing) | off (on in the GPU overlay) | yes | as today; QW1 changes what it writes | case churn > 1 new case per conversation per domain |
| `continuous_quality_scorecard` (existing) | off | — | as today | — |
| `langgraph_shadow` (M1, new) | off | yes | 1% sampled, staff only | p95 latency +10%; any figure disagreement |
| `followup_worker` (M2, new) | off | yes | staff accounts → consenting taxpayers | duplicate notices; quiet-hours breach |

---

## 7. Implementation notes

- **Modules.** New: `app/escalation_policy.py` (QW2), `app/officer_handoff.py` (QW2/QW3 at the ticket
  choke point). Changed: `app/memory/resolution_ledger.py` (`DOMAIN_OBJECTIVES`, `case_update_for_turn`,
  `case_for_prompt`, `mentioned_domains`, `upsert_turn_case`), `app/service.py` (`_record_turn_case` on REST
  and stream, `connectors_used` on the result, `enrich_handoff` in `_maybe_create_ticket`, the prompt's case),
  `app/turn_guidance.py` (the offer), `app/voice_stream.py` (upsert), `app/connector_outcome.py`
  (`connectors_used`), `app/audit/turns.py` + `audit/governance_v3.py` (QW4), `app/text_signals.py`
  (`detect_high_stakes`, `strip_stakes`), `flags.py`. Frontend: `components/staff/TicketCase.tsx`. Tests:
  `App/backend/tests/test_escalation_policy.py`, `test_turn_cases.py`, root `tests/agents/test_phase38_cx.py`
  (the CI coverage gate measures `service.py` from the root suite only).
- **Env vars.** `ESCALATION_STAKES_AMOUNT_UGX` (default 10,000,000), `ESCALATION_OFFER_TURNS`
  (default 6), `EQ_HARDSHIP_SLA_TARGET_SECONDS` (existing, 45).
- **Pipeline.** QW1 after Stage 11 (response assembled, before the audit row); QW2/QW3 on the escalation
  branch of Stage 9 and in `/v1/escalate`; QW4 in the audit row (Stage 12).
- **GPU topology.** Nothing in Phase 38 adds a model or a GPU call. M1's shadow run reuses the served vLLM
  (Sunflower on its own card) with `LLM_MAX_CONCURRENCY` headroom; cap it at 1 concurrent shadow turn.
- **Languages.** Offer text and officer-facing reasons ship in en/lg/sw (taxpayer side) and English (staff
  side). Stakes cues include the Luganda/Swahili enforcement words already in `text_signals`.

---

## 8. Risk and mitigation

| Risk | Where | Mitigation |
|---|---|---|
| **Statutory figure fidelity** | Any new text in a reply | QW2 adds only next actions and handoff metadata — no figures; replies still pass figure protection and the evaluator–optimizer. |
| **Hallucination** | Envelope summaries | The envelope carries structured fields and the transcript; no model-written summary is added. |
| **Privacy / consent** | Case objectives, envelope slots | Templated objectives (no raw text); every case read/write behind `personalization` consent; slots already redacted for storage; audit rows hold digests only. |
| **Emotional misclassification** | QW2 tiers | Deterministic, explainable reasons; *offer* not impose; tier `priority` needs two signals (affect × stakes, or affect × effort); evaluated on a labelled en/lg/sw set before ramp. |
| **Latency** | QW1–QW3 | No model calls; one consent lookup and one working-memory write per substantive turn, off the token stream. |
| **Brand / trust** | Over-offering officers | Kill criteria in §6; officer capacity check before `priority` promises a callback. |
| **Vernacular coverage** | `detect_high_stakes`, affect lexicon | Enforcement cues exist for en/lg/sw and the phrases the shared lexicon lists for nyn/ach; "a deadline today" has none for nyn/ach, and those turns are also read through the English rewrite. A message in a language without a stakes cue is still *offered* a person (hardship alone → `offer`, `high`), never put first. Native review of every vernacular cue is open (runbook `escalation-and-handoff.md`). |

---

## 9. Success metrics

| Metric | Baseline (to measure in week 0) | Phase 38 target | Source |
|---|---|---|---|
| Distressed/high-stakes taxpayers offered a person within 2 turns | not measurable today | ≥ 90% | `ura_cx_escalation_tier_total` + audit rows |
| Text handoffs with completeness ≥ 0.83 | not measurable today (no envelope) | ≥ 80% | `ura_handoff_envelope_completeness_ratio{channel="web"}` |
| Officer re-asks for something already given | officer survey | −50% | `zero_repetition_score`, officer feedback |
| Cases per conversation per domain (churn) | voice: 1 per turn | ≤ 1.1 | `ura_cx_case_events_total{event="opened"}` |
| Autonomous resolution rate on targeted flows (TIN, VAT registration, payment status) | not measurable today | baseline established | M6 |
| CSAT (AI turns, officer chats) | `service_ratings` | +5 pts on escalated cases | `service_ratings`, feedback |
| Vernacular quality (lg/sw) | MULTILINGUAL reports | no regression | per-language evals |

---

## 10. Phase 38 delivery log

- PR: _filled in when opened_.
- Runbook: [`docs/runbooks/escalation-and-handoff.md`](../runbooks/escalation-and-handoff.md).
- Tests: `App/backend/tests/test_escalation_policy.py`, `test_turn_cases.py`,
  `test_taxpayer_escalation.py` (tenant); root `tests/agents/test_phase38_cx.py`;
  `TicketCaseChat.test.tsx`. Full backend + root suites: the same 65
  container-only failures as `dev`, no new ones.
- Live verification (2026-10-16): an isolated api built from this branch
  (own analytics database, GPU 4, the shared Sunflower vLLM) with all three
  flags on, driven through REST, SSE and the staff console in a browser —
  25/25 API checks and the staff ticket panel. It found five defects that the
  unit tests had not, all fixed in this change:
  1. the hand-off read the workflow by the id the client sent; the engine
     keys it by owner, so `already_given` was always empty on a real ticket;
  2. "Talk to an officer" arrived with `topic: general` and nothing to ask, so
     there was nothing to prune — the topic, team and details now come from
     the conversation;
  3. a guided workflow's case domain was read from the *answer* to its last
     step ("VAT" to the return-filing steps opened a VAT case);
  4. "register" and "pay" outranked the named tax: "Do I need to register for
     VAT?" opened a TIN-registration case, "stamp duty" a customs one;
  5. a rate read out shares `retrieval_mode: calculator` with a computation,
     so "What is the VAT rate?" opened a case.
  It also found that `/v1/escalate` ignored the caller's tenant.
- Not done here: M1–M6 and A1–A3 (§4.2–4.3).

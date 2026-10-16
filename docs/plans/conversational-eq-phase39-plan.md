# Conversational, emotional and structuring robustness: QA audit and Phases 39–41

Status: **Phase 39 implemented** (§5; delivery log in §9); Phases 40–41 proposed.
Written 2026-10-16 against `dev` @ `e658b0e1b7` (after Phase 38, #578). Tracking issue: #441.
Gap register rows: G165–G173.

Roles taken for this audit: QA engineer (does it behave), auditor (does it meet the
reference standards) and reviewer (is the code the right shape). Method: code inspection of
the chat, workflow, crisis, voice-affect and hand-off paths; the Phase 38 live probe
transcripts on an isolated api; and current research and regulation, verified online and cited
in §2. Every finding below was reproduced in code or in a test before it was written down.

---

## 1. Summary

| Area | What a taxpayer met | Severity |
|---|---|---|
| Guided flows | News of enforcement or hardship typed mid-flow was answered with "Please answer yes or no." and the same step (yes/no steps), or the flow was abandoned to retrieval, which abstained ("I couldn't find a reliable answer…", enum steps). | High — the moment of most need got the coldest reply |
| Guided flows | A step answered wrongly again and again looped, with no person or way out offered. | Medium |
| Crisis | Passive ideation ("I have nothing to live for", "my family would be better off without me", "I don't want to live anymore") was not recognised; hopelessness said after hardship ("I can't cope anymore" after "I can't afford the penalty") was answered as a tax question. | High (safety) |
| Crisis | A crisis reply to "…because of my VAT penalty" carried VAT form cards and a filing-deadline nudge, against the path's own rule ("never carries tax content"). | High (safety) |
| Voice EQ | Loudness or pace alone labelled a caller "frustrated" (slow speech: "confused"), which changed what was said to them ("I understand this can be frustrating…") and raised the call's at-risk distress signal — on acoustic signals never validated for Ugandan speakers, with no notice. | High (fairness, transparency) |
| Transparency | The chat never said it was an AI ("URA Assistant can make mistakes"; greeting "URA Digital Assistant"). | Medium (compliance) |
| Memory | Workflow-engine state (`_tangent_count`) could reach the officer's "Already given" list and the consented slot bank. | Low |
| Structuring | **No suggested action ever reached the web chat.** The SSE handler's metadata-line branch dropped `next_actions`, so the guided-steps offer, Phase 38's officer offer and the crisis reply's "Talk to an officer" were invisible, though every API response carried them; and the default actions were instructions ("Reply with the requested detail…") that a tap would have sent as the answer. Found by the browser check. | High |
| Empathy calibration, clarification, evaluation | See §6: open, Phase 40. | — |

## 2. Reference standards and research (verified 2026-10-16)

| Source | What it says | Consequence here |
|---|---|---|
| EU AI Act, Art. 50 (transparency) — applies from 2 Aug 2026; the Digital Omnibus (Reg. (EU) 2026/1744, in force 27 Jul 2026) deferred only high-risk obligations (Annex III to 2 Dec 2027) ([legalithm](https://www.legalithm.com/en/blog/eu-ai-act-what-applies-2-august-2026), [technology.org](https://www.technology.org/2026/10/13/eu-ai-act-deadlines-after-the-digital-omnibus-what-applies-from-december-2026-to-august-2028/)) | Art. 50(1): people must be told they are interacting with an AI unless obvious. | G170: say "AI" in the chat. Uganda is not bound; the Act is used as the most specific published standard. |
| EU AI Act Art. 3(39) and 50(3); Recital 18, 132 ([AI Act Service Desk, Art. 3](https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-3), [Recital 18](https://ai-act-law.eu/recital/18/), [William Fry](https://www.williamfry.com/knowledge/the-time-to-ai-act-is-now-a-practical-guide-to-emotion-recognition-systems-under-the-ai-act/)) | An emotion-recognition system infers emotion from *biometric* data; deployers must inform the people exposed, accessibly. Text-only sentiment appears to fall outside; voice-derived affect inside. | G169: tell callers before the call; never act on voice alone. |
| Emotion recognition in African languages ([Ahmad et al., arXiv 2503.19642](https://arxiv.org/abs/2503.19642v1)) | Emotion expression differs across 15 African languages; language-specific approaches are needed. No validated speech-emotion evidence for Ugandan speakers was found. | G169: acoustic-only labels off by default; opt-in only for a validation study. |
| Chatbot empathy backfires ([USF / MIS Quarterly, 2026](https://www.usf.edu/business/news/2026/04-20-chatbot-empathy-can-worsen-customer-reactions-usf-study.aspx)); empathy–sycophancy link ([arXiv 2603.15448](https://arxiv.org/pdf/2603.15448)); templated empathy ([arXiv 2604.08479](https://arxiv.org/pdf/2604.08479)) | A chatbot that mirrors negative emotion triggers reactance and lowers perceived competence; warmth tracks sycophancy; LLM empathy is liked but templated. | Off-ramp copy is competence-first, never mirrors ("Let's sort this out first."); Phase 40 calibrates the rest. |
| Crisis handling by chatbots ([RAND, Psychiatric Services 2025 via This Week Health](https://thisweekhealth.com/news_story/ai-chatbots-under-fire-for-inconsistent-suicide-related-response-handling), [JMIR 2026](https://www.jmir.org/2026/1/e97968/PDF), [VERA-MH](https://www.emergentmind.com/topics/vera-mh)) | Systems handle explicit statements but vary widely on indirect ones; disclosure is often delayed, indirect, legible only across turns. | G167: passive ideation joins the crisis path; hopelessness amid hardship (this or the last two messages) gets a check-in. |
| Clarification ([Zhang & Choi, NAACL 2025 Findings](https://preview.aclanthology.org/moar-dois/2025.findings-naacl.306.pdf); [Madge et al. 2025](https://arxiv.org/abs/2507.10445v1)) | LLMs under-ask on *task* uncertainty; an uncertainty trigger (entropy over intents) finds the turns a question would help. | Phase 40: clarify when the task is uncertain, with choices. |
| Agent reliability: τ-bench pass^k ([arXiv 2406.12045](https://arxiv.org/pdf/2406.12045)) | Customer-service agents are measured by whether *all* k runs succeed; pass^k falls well below pass@1. | Phase 40: pass^k for the CX-critical flows. |
| OWASP Top 10 for Agentic Applications 2026 ([Cycode](https://cycode.com/blog/owasp-top-10-agentic-applications/)) | ASI06 Memory & Context Poisoning, ASI09 Human-Agent Trust Exploitation. | G171 keeps engine state out of memory; offers stay offers (ASI09). |
| Singapore Model AI Governance Framework for Agentic AI, Jan 2026 ([Bird & Bird](https://cm.twobirds.com/en/insights/2026/singapore/singapore-introduces-new-model-ai-governance-framework-for-agentic-ai)) | Human checkpoints before sensitive actions; audit that oversight works. | Already: confirmations, offers; Phase 38 brief + Phase 41 oversight audit. |
| "Right to talk to a human" — Gartner forecast for the EU by 2028 ([CX Today](https://www.cxtoday.com/contact-centre/by-2028-the-eu-will-mandate-the-right-to-talk-to-a-human-in-customer-service-predicts-gartner/)) | A forecast, not law. | Every paused, looping or check-in turn offers a person. |
| ISO 24495-1:2023 plain language ([ISO](https://www.iso.org/standard/78907.html)) | Success = readers can find, understand and use the information, not readability formulas. | Copy in this phase: one idea per sentence, the action named. |
| vLLM structured outputs ([vLLM docs](https://docs.vllm.ai/en/latest/features/structured_outputs/)) | `guided_*` request fields were removed in v0.12; `structured_outputs` (xgrammar/guidance) guarantees format, not values. | Checked: no backend call depends on `guided_*`; the only model-JSON parse (the FAQ judge) is flat. No change needed. |

## 3. Findings and evidence

* **G165** — `WorkflowRegistry.advance` re-asks with the validator's error on any invalid answer;
  `ChatModel._workflow_input_changes_subject` sends an enum step's ≥4-word statement to
  retrieval. Reproduced: "URA closed my shop and I cannot pay my workers" at the Return Filing
  tax-type step abstained; at a yes/no step the Phase 38 live run got "Please answer yes or no."
* **G166** — no counter of invalid answers existed; the same error and question repeat forever.
* **G167** — `_CRISIS_RE` held explicit phrases only; nothing read the last turns.
* **G168** — `crisis_support` was in neither `verified_resources._NO_RESOURCE_MODES` nor exempt
  from `apply_turn_guidance`'s topic nudges.
* **G169** — `receptionist/prosody.analyze_utterance_prosody`: `high_arousal` alone →
  `frustrated`; `0 < wps < 1.1` alone → `confused`; `risk.py` counts either as distress;
  `empathetic_opening_for` speaks to it. The call's pre-call screen said only that calls are
  transcribed.
* **G170** — `composer.disclaimer` and `GREETING_REPLY` never said "AI".
* **G171** — `officer_handoff.given_slot_names` and `ResolutionLedger.store_verified_slots`
  copied every slot key, including `_`-prefixed engine state.
* **G173** — `page.tsx` handled turn metadata in three hand-copied blocks; the one for
  metadata lines (which every streamed metadata frame is) copied citations, mode, workflow and
  resources but not `next_actions`, then returned before the branch that did. The browser check
  showed no chip at all, not even "Guide me step by step through Return Filing". With chips
  visible, `_default_next_actions` and two resume paths turned out to emit instructions.
* **Interaction** — Phase 38's `turn_guidance._offer_person` moved an existing "Talk to an
  officer" to the end for an `offer`-tier turn, demoting the officer the off-ramp had put first
  (found live with both phases on).

## 4. Principles kept

Figure protection, PII redaction, injection defences, grounding and the audit ledger are
untouched: the new replies are fixed copy with no figures and no model call. Every new
behaviour is behind a default-off flag except the three that remove a harm (crisis recall,
tax content on safety replies, voice-only affect labels), which ship on, each with its reason
in the code. Offers stay offers: nothing escalates by itself.

## 5. Phase 39 — delivered in this change

| # | Change | Flag |
|---|---|---|
| 1 | **Emotional off-ramp in guided flows.** A statement the pending step cannot accept, carrying hardship, enforcement, a deadline today, frustration or anxiety (free-text steps: hardship or enforcement in a sentence of six words or more), pauses the flow: answers kept, the step's buttons hidden, and "Talk to an officer" (first when serious), the instalments question, "Continue the steps" and "Cancel" offered. A real answer is never paused; a question still goes to retrieval. Runs before the change-of-subject rule. `workflow_offramp_total{workflow_id,reason}`. | `workflow_emotional_offramp` |
| 2 | **Loop recovery.** `WorkflowTurn.failed_attempts` counts consecutive invalid answers per step (engine state `_invalid_step`/`_invalid_count`, cleared on any accepted answer). From the second miss a person and "Cancel" are offered; from the third the reply says the step may not fit. | `workflow_emotional_offramp` |
| 3 | **Crisis recall.** Passive-ideation phrases join `_CRISIS_RE` (explicit path, unchanged reply). | — (safety) |
| 4 | **Check-in.** Hopelessness (`_HOPELESSNESS_RE`: "can't cope", "I give up", "no way out", Kiswahili *nimechoka na maisha*, *sina tumaini*) with hardship or enforcement in this or the last two messages short-circuits — REST and stream alike, beside the crisis check — to a check-in with the shared `CRISIS_LINES`, an officer and the instalments question. Once per conversation. `wellbeing_checkin_total`. | `wellbeing_checkin` |
| 5 | **Safety replies carry no tax content.** `crisis_support` and `wellbeing_checkin` get no resources and no turn-guidance nudges (`turn_guidance.SAFETY_MODES`). | — (safety) |
| 6 | **Voice affect needs words.** Loudness, energy and pace intensify what the words say; alone they name nothing (`PROSODY_ACOUSTIC_ONLY_AFFECT=true` restores it for a validation study). The pre-call screen tells callers the assistant may notice their voice's tone and pace (en, sw; lg/nyn/ach fall back to English until a native translation lands). | — (fairness) |
| 7 | **AI disclosure.** "URA's AI assistant can make mistakes…" and "I'm the URA Digital Assistant — an AI, not a person." ("(AI)" in lg/sw/nyn/ach). | — (copy) |
| 8 | **Engine state stays out of memory.** `_`-prefixed slot keys never reach the officer's brief or the slot bank. | — (fix) |
| 9 | **Suggested actions reach the web chat.** One helper, `lib/turnMeta.applyTurnMeta`, applies a turn's metadata for every SSE frame (it replaces three drifting copies); every chip is tappable: no instruction defaults, an active flow's choices stay in its stepper, a parked flow offers "Continue <flow>" (`_continue_flow_action`, accepted as a resume). | — (fix) |
| 10 | **Offers never demote.** An `offer`-tier turn adds "Talk to an officer" when it is missing and leaves it where an earlier decision put it; `priority` still moves it first. | — (fix) |

## 6. Phase 40 — proposed next

1. **Empathy calibration** (research §2): competence-first for frustration — the answer leads,
   the acknowledgement is a clause, not a sentence; vary the openers so they stop reading as a
   template; never repeat an apology in consecutive turns (partly done by `strip_repeated_ack`).
2. **Sycophancy guard**: a taxpayer's premise ("URA is charging me illegally", "I don't have to
   file a nil return") is checked, not agreed with; extend `false_premise_rejected` cases and
   eval them.
3. **Task-uncertainty clarification**: when two or more workflows, tax types or domains match
   with no clear lead ("How much tax do I pay?"), ask one question with choices instead of
   guessing — an intent-entropy trigger per Zhang & Choi.
4. **pass^k evals** for the CX-critical behaviours (crisis, check-in, off-ramp, escalation tier,
   figure answers): k runs on the live stack, all must pass; a gate in `evals/`.
5. **Voice parity** for the check-in (`receptionist/brain.py` speaks the crisis phrase only).
6. **Native review** of every vernacular cue and notice added since Phase 38.

## 7. Phase 41 — architectural (carried from the Phase 38 plan)

M1 LangGraph shadow path; M2 post-session follow-ups (scheduler); M3 per-tenant, Redis-backed
quality breaker; M6 CX outcome rates (autonomous resolution, clean hand-off, repetition) and a
console panel; an audit of whether officers act on the Phase 38 brief (Singapore framework:
check that oversight works).

## 8. Risks

| Risk | Mitigation |
|---|---|
| A check-in for a frustrated taxpayer who is not at risk | Needs hopelessness *and* hardship/enforcement; once per conversation; the tax help is one tap away. Asking about wellbeing does not raise risk (safe-messaging consensus). |
| Passive-ideation phrases matching hyperbole | Phrases chosen are rarely figurative ("nothing to live for", "better off without me"); "tired of this portal" and "can't go on paying" are tested negatives. |
| A paused flow annoys someone who was answering | A valid answer is never paused; "Continue the steps" resumes at the same step with nothing lost. |
| Fewer voice affect labels | Words still drive them; acoustics still weigh in the fusion score; the opt-in exists for a validation study. |
| English fallback of the tone notice in lg/nyn/ach | Documented; native translation is Phase 40 item 6. |

## 9. Delivery log

- PR: _filled in when opened_.
- Runbook: [`docs/runbooks/conversational-safety-and-eq.md`](../runbooks/conversational-safety-and-eq.md).
- Live verification (2026-10-16): an isolated stack built from this branch — its own Sunflower
  vLLM (GPU 7) and api (GPU 4), private analytics DB, the shared Qdrant and connectors — with
  Phase 38 and 39 flags on. 25/25 API checks over REST and SSE, including the check-in
  translated into Luganda and Kiswahili with 0800 21 21 21 and 999 intact; and a production
  build of the frontend in a real browser: the AI footer, the tone notice on the pre-call
  screen, the paused flow with its four chips and no step buttons, and an ordinary answer's
  chip. The run found G173 and the offer-demotion interaction; both are fixed here.
- Tests: `App/backend/tests/test_phase39_signals.py`; root
  `tests/test_phase39_conversational_eq_integration.py` (REST and SSE through the real app);
  frontend `CallScreen.test.tsx`, `ChatInput.test.tsx`.

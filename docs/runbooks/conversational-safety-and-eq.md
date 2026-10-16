# Runbook — conversational safety and emotional intelligence

How the assistant responds when a taxpayer is in distress, at risk, or stuck; what is
behind a flag; what to watch; and what to re-verify. Audit and design:
[`docs/plans/conversational-eq-phase39-plan.md`](../plans/conversational-eq-phase39-plan.md);
gap rows G165–G172. Officer offers and ticket priority are in
[`escalation-and-handoff.md`](escalation-and-handoff.md).

## The safety tiers (text: REST, SSE, WebSocket)

Checked before any router, in this order, for the taxpayer's words and the English rewrite:

| Tier | Trigger | Reply | Flag |
|---|---|---|---|
| Crisis | `text_signals.detect_crisis`: explicit intent ("kill myself", *okwetta*, *kujiua*) and passive ideation ("nothing to live for", "better off without me", "don't want to live anymore") | `crisis_support_reply`: crisis lines, no tax content, officer offered | always on |
| Check-in | `needs_wellbeing_checkin`: hopelessness ("can't cope", "I give up", "no way out", *nimechoka na maisha*) **and** hardship or enforcement in this or the last two messages | `wellbeing_checkin_reply`: the same crisis lines, then the remedy question and an officer; once per conversation | `wellbeing_checkin` |

Neither tier carries tax content: both are in `turn_guidance.SAFETY_MODES` (no guided-steps
offer, no filing-deadline nudge) and `verified_resources._NO_RESOURCE_MODES` (no form cards).
Neither opens a ticket: what the message says is sensitive, and the officer is offered.

### Crisis lines

One constant, `text_signals.CRISIS_LINES`, feeds both replies: 999/112 (Uganda Police Force,
upf.go.ug) and Mental Health Uganda 0800 21 21 21 (Mon–Fri 8:30–17:00), checked on
`CRISIS_LINES_VERIFIED_ON`. Re-check both sources at least every quarter and before changing
that date. The call path speaks its own `phrases["crisis_support"]` per language; keep the
numbers in step.

## Guided flows that listen (`workflow_emotional_offramp`)

* **Off-ramp.** In the middle of a flow, a *statement* the pending step cannot accept that
  carries hardship, enforcement, a deadline today, frustration or anxiety pauses the flow
  instead of "Please answer yes or no." (free-text steps: hardship or enforcement in a
  sentence of six or more words). Answers so far are kept; the reply offers "Talk to an
  officer" (first when serious), "How can I pay my tax in instalments?", "Continue the steps"
  and "Cancel". A valid answer is never paused; a question still goes to retrieval.
* **Loop recovery.** After two invalid answers to the same step, an officer and "Cancel" are
  offered; after three, the reply says the step may not fit the taxpayer's situation.

The counter is engine state (`_invalid_step`, `_invalid_count`, like `_tangent_count`);
`_`-prefixed keys never reach the slot bank or the officer's "Already given" line.

## Voice: affect needs words

`receptionist/prosody.analyze_utterance_prosody` names a caller's affect from their words;
loudness, energy and pace only intensify it (and still weigh in
`tools/empathy.fuse_multimodal_affect`). Alone they change nothing: no "I understand this can
be frustrating", no at-risk distress signal. Why: a noisy line, a loud voice or a fast or slow
talker are not feelings, and no validation of speech-emotion cues for Ugandan speakers exists;
inferring emotion from voice is emotion recognition under the EU AI Act (Art. 3(39)), the
reference standard used here. `PROSODY_ACOUSTIC_ONLY_AFFECT=true` restores acoustic-only
labels, for a validation study only.

Callers are told before the call starts (`call.preConsentTone` on the pre-call screen): the
assistant may notice the tone and pace of their voice, together with their words.

## Disclosure that it is an AI

The composer footer (`composer.disclaimer`) and the greeting (`text_signals.GREETING_REPLY*`)
say it is an AI in all five languages; the voice greeting already did ("I'm your AI
assistant").

## Ramp and watch

```bash
FLAG_WORKFLOW_EMOTIONAL_OFFRAMP_COHORTS=ura_staff,internal   # then a percentage
FLAG_WELLBEING_CHECKIN_PERCENT=10
```

| Signal | Healthy |
|---|---|
| `ura_workflow_offramp_total{workflow_id,reason}` | small next to `journey_events_total{event="step_entered"}`; a jump on one flow means its step is confusing, not that taxpayers are upset |
| `ura_wellbeing_checkin_total{locale}` | rare; review a sample of transcripts monthly with the safeguarding lead |
| `chat_turns_total{outcome}` around check-ins | the next turn is mostly the instalments question or an officer, not a complaint |
| Call transcripts with `caller_affect` set | every label has words behind it |

## Roll back

`FLAG_WORKFLOW_EMOTIONAL_OFFRAMP=false` and `FLAG_WELLBEING_CHECKIN=false` revert their
behaviour at once. The safety fixes (crisis recall, no tax content on safety replies, voice
affect needing words) and the AI disclosure are not flagged; revert them only by code change,
with a reason recorded here.

## Open items (Phase 40)

Native-speaker review of every vernacular cue and of the tone notice (lg/nyn/ach show the
English line today); the check-in on calls; empathy calibration; task-uncertainty
clarification; pass^k evals. See the plan §6.

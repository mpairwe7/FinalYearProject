# Prohibited uses

Engineering assessment, 2026-11-17. Not legal advice. The DPO/legal owner
confirms it.

## What taxpayers may not do

Section 2.3 of the
[acceptable use policy](../../docs/capstone/week04-privacy-and-aup.md) lists the
uses taxpayers may not make of the assistant. `InputGuard` in
[input_checks.py](../../App/backend/app/guardrails/input_checks.py) blocks prompt injection and
abuse at runtime.

## What the system must never do: EU AI Act Article 5

| Article 5(1) practice | Does the system do it? | Evidence |
| --- | --- | --- |
| (a) Subliminal, manipulative or deceptive techniques that distort behaviour | No. The assistant answers questions and offers a person. It has no goal of steering a taxpayer's choices. | The governed prompt states only current, enforced facts (ADR-0030). |
| (b) Exploiting vulnerability from age, disability or social or economic situation | No. Distress and hardship cues lead to an offer of a person, not to a push towards an action. | [escalation_policy.py](../../App/backend/app/escalation_policy.py) |
| (c) Social scoring | No. Nothing scores a person. The escalation policy weighs distress, stakes, effort and the assistant's confidence in the current conversation, and it can only raise a ticket's priority. | [escalation_policy.py](../../App/backend/app/escalation_policy.py) |
| (d) Predicting criminal offences from profiling | No. The system does no compliance, fraud or evasion scoring of taxpayers. | No such module exists in `App/backend/app`. |
| (e) Untargeted scraping of facial images | No. The system collects no facial images. | Not applicable. |
| (f) Emotion recognition in a workplace or school | No. Voice emotion inference, when switched on, runs on callers' audio only, never on officers. It may still be high risk, which is a different question. | [FRIA, open classification question](../systems/ura-ai-chatbot/fria.md#open-classification-question) |
| (g) Biometric categorisation by race, beliefs, sexual orientation and the like | No. Accent detection picks a speech recognition adapter for Ugandan English or Luganda and infers nothing else. | [accent_detector.py](../../App/backend/app/accent_detector.py) |
| (h) Real-time remote biometric identification for law enforcement | No. The system identifies no one by voice or face. | Not applicable. |

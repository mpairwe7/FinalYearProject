# ADR-0059: The registry's empty slots are filled from records and live metrics

## Status

Accepted, 2026-11-17. The project owner asked for the gaps that
[ADR-0058](0058-ai-governance-registry-layout.md) marked Missing to be closed.

## Context and Problem Statement

ADR-0058 laid the governance records out as a registry and marked each slot
Here, Elsewhere or Missing. Missing were a fundamental rights impact assessment,
an incident log, a live bias check, cost per decision, and the vendors'
contracts and breach terms. Partly empty were the Article 5 mapping, a written
risk appetite and a model and prompt change log.

Two kinds of gap differ. Some need a record that engineering can write from the
project's own evidence, or a metric the code can export. Others need a
signature, an approval or a contract that only an owner can give.

## Considered Options

1. Fill each slot that evidence can fill, and leave signatures, approvals and
   contracts as named actions for their owners.
2. Fill every slot, including approval blocks and contract terms written as if
   agreed.
3. Add stub files that say "to do".

Option 2 would put claims in the registry that nobody made. Option 3 changes the
file tree and nothing else. Option 1 closes what can be closed and says plainly
what cannot.

## Decision Outcome

Option 1.

- **Bias.** `record_chat_turn` counts each turn's outcome by reply language in
  `ura_locale_turns_total{locale, outcome}`. The locale is bounded to the five
  supported languages and `other`. Recording rules give abstention and escalation
  per language (`ura:locale_turns:*`), and a language with no abstained turns
  reads 0, not no data. `UraLanguageAbstentionGap` fires when a local language
  abstains more than 15 points above English for two hours, on at least 20 turns
  an hour. A new counter keeps the existing `ura_chat_turns_total` series and
  queries unchanged.
- **Cost per decision.** `ura:llm_tokens_per_turn:rate1d` divides model tokens by
  chat turns over a day. `governance/monitoring/cost_per_decision.py` prices it
  from `cost_per_decision.csv`, whose prices quote the providers' pages with a
  date. A model with no published price makes the figure a lower bound and is
  named. Prices stay out of PromQL so one file holds them.
- **Records.** `fria.md` (draft, unsigned), `change_log.md`,
  `prohibited_uses.md` (Article 5, engineering assessment), `risk_appetite.md`
  (the numbers already enforced, unapproved), `evidence/incident_log/` (each row
  cites its gap record) and one folder per vendor, which quotes each
  provider's published terms.

## Consequences

- An auditor can see whether abstention falls harder on Luganda, Swahili,
  Runyankole or Acholi in live traffic, and what one answer costs.
- Writing the FRIA found an open question. Voice emotion inference
  (`receptionist/prosody.py`, off by default) may be high risk under Annex III
  point 1(c). The DPO/legal owner decides (G664).
- Sunbird AI publishes no data terms (G665). Google's unpaid tier allows training
  and human review, so the Gemini key's tier needs confirming.
- Signatures stay empty: the DPIA, the FRIA, the PDPO registration and the risk
  appetite. No negotiated contract exists with any provider.
- Each future model swap, incident or new provider adds a row, and the
  living-docs skill and `governance/AGENTS.md` say so.

## Confirmation

- `App/backend/tests/test_metrics_facade.py::test_outcomes_are_counted_by_reply_language`.
- The promtool unit tests in `monitoring/tests/alerting-rules.test.yml` cover the
  per-language ratios, the parity alert and tokens per turn.
- `tests/test_monitoring_config.py` checks that every new rule and panel names a
  series the API exports.
- `tests/test_governance_cost.py` checks the arithmetic, and that every priced
  model is one the API calls.
- `python3 governance/compliance_check.py` requires the new records.

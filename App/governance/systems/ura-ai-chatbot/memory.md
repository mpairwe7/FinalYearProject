# What the assistant remembers

Everything the URA AI Chatbot keeps about a taxpayer or a conversation, how long
it keeps it, and who can read it. The code is in
[`App/backend/app/memory/`](../../../backend/app/memory/__init__.py)
(ADR-0060). Checked against the code on 2026-11-18. Default periods come from
[.env.example](../../../../.env.example); a deployment can set others.

| Memory | What it holds | Where | How long | Read by |
| --- | --- | --- | --- | --- |
| Conversation turns | Each turn and the language it was answered in | The application database | `CONVERSATION_TTL_DAYS`, 7 days | The conversation |
| Case facts | Amounts, days in Uganda, residency, salary and benefits, entity type and turnover stated in this conversation (`case_facts.py`) | Redis, keyed by conversation, with an in-process fallback | 6 hours | The conversation |
| Answer language | The language the conversation is answered in (`language_state.py`) | Read from the stored turns; no store of its own | As the turns | The conversation |
| Working memory | The taxpayer's short-term state, such as the last topic and verified slots (`MemoryService` in `service.py`) | The shared analytics backend, one row per tenant and taxpayer | Set by the last write: 30 minutes from `update_working` (`working.py`), or `RESOLUTION_CASE_TTL_HOURS` from the resolution ledger, which writes the same row | The taxpayer, in any conversation, with `personalization` consent |
| Episodic | A one to three sentence summary of each finished conversation (`episodic.py`) | `episodic_summaries` | `EPISODIC_TTL_DAYS`, 90 days | The taxpayer, with `personalization` consent |
| Semantic | Facts about the taxpayer, each with provenance, confidence and decay (`semantic.py`) | The application database | `SEMANTIC_TTL_DAYS`, 365 days | The taxpayer, with `personalization` consent |
| Database snapshot | A gzipped SQL dump of the whole analytics database, so it survives a restart (`db/snapshot.py`, ADR-0063) | The `DB_SNAPSHOT_TARGET` destination: a private HF bucket, or a `file://` directory, owner-readable only | Only the latest dump; each export deletes the previous one | The API at startup, to restore; operators through the [runbook](../../../docs/runbooks/database-snapshots.md) |
| Resolution ledger | Multi-step goals carried across calls, chat and officers (`resolution_ledger.py`) | Shared working memory | `RESOLUTION_CASE_TTL_HOURS`, 168 hours | The taxpayer, with `personalization` consent and a confident transcript |
| Procedural | Workflows, skills, policy rules and few-shot exemplars (`procedural.py`, ADR-0012) | In process, seeded from code | Until the process restarts | The assistant; no consent check |

## Controls

- **Consent.** Every read of episodic and semantic memory and the resolution
  ledger is gated on the taxpayer's active `personalization` consent. No consent
  returns nothing.
- **The switch.** `FLAG_MEMORY_ENABLED` decides whether personal memory facts
  reach the prompts at all. It is off in the code's registry, but
  `App/docker-compose.prod.yml` turns it on unless the variable is set
  (`${FLAG_MEMORY_ENABLED:-true}`).
- **Erasure.** Withdrawing `personalization` consent calls
  `MemoryService.forget_user`, which clears working, episodic and semantic memory.
  `DELETE /v1/me` does that and also clears case facts (`forget_user_case_facts`)
  and uploaded documents.
- **Expiry.** `retention.py` deletes expired database, document, memory and voice
  rows at startup and on an interval, watched by the `RetentionJobFailing` and
  `RetentionJobStale` alerts. Case facts expire through their 6-hour Redis TTL, or
  the in-process deadline when Redis is absent. An expired resolution case is
  ignored when it is read.

The DPIA's processing scope covers memory ([dpia.md](dpia.md)).

# audit/

Audit records are rows in the application database, not files. The
[audit trail runbook](../../docs/runbooks/audit-trail.md) says who can read them,
how to prove they are unaltered and how to export them for an external auditor.

| Slot | State | Artifact |
| --- | --- | --- |
| Access logs | Elsewhere | [security_events.py](../../App/backend/app/security_events.py) records authentication failures, authorization failures and guardrail probing. The alert rules `AuthenticationFailureSpike` and `AuthorizationFailureSpike` watch them. The runbook's separation-of-duties table lists what each staff role can read. |
| Decision logs | Elsewhere | Each answered turn is appended with its provenance by [audit/turns.py](../../App/backend/app/audit/turns.py). The ledger is hash-chained ([ledger.py](../../App/backend/app/audit/ledger.py)), anchored in batches by a Merkle root ([merkle.py](../../App/backend/app/audit/merkle.py)) and timestamped under RFC 3161 ([tsa.py](../../App/backend/app/audit/tsa.py)). |
| Retention schedule | Elsewhere | The `*_TTL_DAYS` settings in [.env.example](../../.env.example) set how long each store keeps data. [retention.py](../../App/backend/app/retention.py) deletes expired rows at startup and every `RETENTION_CLEANUP_INTERVAL_SECONDS`. The alert rules `RetentionJobFailing` and `RetentionJobStale` watch it. Section 1.5 of the [privacy notice](../../docs/capstone/week04-privacy-and-aup.md) tells taxpayers the periods. |

# ADR-0065: Startup fails closed on the controls a deployment runs

## Status

Accepted, 2026-11-19.

## Context and Problem Statement

Two startup failures stopped the process only under `APP_ENV=production`:
- a database that could not be initialised
- stored session revocations that could not be read

Anywhere else, both were logged and the process kept serving.

The Hugging Face Space serves taxpayers with `APP_ENV=development`, set by a Space secret. It runs with four controls on that keep their records in the analytics database:
- the audit ledger (`FLAG_AUDIT_LEDGER`)
- tenancy (`FLAG_MULTI_TENANT`)
- tickets (`FLAG_TICKET_QUEUE`)
- voice consent (`FLAG_VOICE_CONSENT`)

After a failed initialisation, for example a bad snapshot restore (ADR-0063), it would have answered without any of them. After an unreadable revocation list, it would have accepted tokens that had been signed out.

The Space could not run in production mode instead, because production mode could not start at all. Startup requires a fresh, hash-checked authority manifest at `/app/artifacts/authority/manifest.json`, and nothing in the repository or the image build wrote one. The image sets `APP_ENV=production` (`App/Dockerfile.cranecloud`), so the image as built exited at startup.

## Considered Options

1. Decide fail-closed by `APP_ENV` alone, and move the Space to production mode.
2. Decide fail-closed by the controls a deployment runs, and have the image build write the manifest.
3. Leave both as they are.

## Decision Outcome

Option 2.

- **Fail closed on the controls.** `main._storage_failure_stops_startup` is true in production, and anywhere a storage-backed control is on: `audit_ledger`, `multi_tenant`, `ticket_queue` or `voice_consent`. Both startup paths use it. A deployment with none of those controls on, such as a laptop with them set off, still logs the failure and keeps going, and the revocation sync keeps retrying. This follows ADR-0062: a gate keyed on `APP_ENV` alone is not a gate on a deployment that runs in development mode.
- **The image carries its manifest.** `python -m app.authority write` hashes the rate tables, their evidence and the kernel release record into the manifest, with paths relative to it. The image build runs it as its last step, stamps the commit as the version, and names the location in `URA_AUTHORITY_MANIFEST`, because this image does not set `PROJECT_ROOT`. The manifest is fresh for `AUTHORITY_MAX_AGE_DAYS` (30) after the build.
- **The Space stays in development mode.** Production mode also requires:
  - Postgres (`ANALYTICS_BACKEND=postgres`), while the Space keeps SQLite with bucket snapshots (ADR-0063)
  - RS256 OIDC
  - `auth_required`, which production forces on, so no taxpayer could chat without signing in

  Which of these the pilot takes on is the service owner's decision.

## Consequences

- A storage failure on the Space now stops the process, which the Space restarts, instead of serving without the audit ledger, tenancy, tickets and consent. An outage replaces silently missing controls.
- Locally, a failed database stops startup too, because `ticket_queue` is on by default. Setting `FLAG_TICKET_QUEUE=false` runs without storage, as before.
- A production deployment can start from the image as built. It must be rebuilt within 30 days, or startup and the rate tools refuse, as the authority check intends.

## Confirmation

- `App/backend/tests/test_storage_failure_stops_startup.py`: with the Space's settings, a failed database and unreadable revocations both refuse to start. With no storage-backed control on, both are logged and the process continues.
- `App/backend/tests/test_authority_manifest_writer.py`: a written manifest is fresh and checks every source, a source edited afterwards fails the check, and the CLI writes relative paths.
- An image with the same layout as `App/Dockerfile.cranecloud` ran the build step. Its manifest validated with `APP_ENV=production` and all four sources checked.

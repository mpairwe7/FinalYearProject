# ADR-0011: Node.js 24 LTS Runtime & GitHub Actions Supply-Chain Upgrade (October 2026)

- **Date:** 2026-10-22
- **Owner:** Cloake Tech Limited — DevSecOps, Platform Assurance & QA Engineering

## Status

Accepted

## Context and Problem Statement

In October 2026, GitHub Actions runners deprecated the Node.js 20 execution runtime and began forcing legacy JavaScript actions onto Node.js 24, emitting deprecation warnings across CI/CD pipelines for:
- `actions/setup-python@v5.6.0` (`a26af69be951a213d495a4c3e4e4022e16d87065`)
- `actions/download-artifact@v4.3.0` (`d3f86a106a0bac45b974a628896c90dbdf5c8093`)
- `docker/build-push-action@v6.9.0` (`4f58ea79222b3b9dc2c8bbdd6debcef730109a75`)
- `docker/login-action@v3.7.0` (`c94ce9fb468520275223c153574b00df6fe4bcc9`)
- `actions/checkout@v4.3.1` (`34e114876b0b11c390a56381ad16ebd13914f8d5`)
- `actions/cache@v4.3.0` (`0057852bfaa89a56745cba8c7296529d2fc39830`)
- `actions/github-script@v7.0.1` (`f28e40c7f34bde8b3046d885e986cb6290c5673b`)
- `actions/attest-build-provenance@v2.2.0` (`520d128f165991a6c774bcb264f323e3d70747f0`)

Additionally, `.github/workflows/frontend-deploy.yml` and `.github/workflows/lighthouse-live.yml` still configured `NODE_VERSION: "20"`, and `App/frontend/package.json` did not declare an explicit `engines.node >= 24.0.0` floor.

## Decision Outcome

We upgraded the entire CI/CD supply chain and frontend runtime baseline from Node.js 20 to **Node.js 24 LTS** (while keeping full 40-char commit SHA pinning per SLSA Level 2 / OpenSSF Scorecard requirements):

1. **GitHub Actions Native `node24` SHA Pinning Across All 16 Affected Workflows:**
   - `actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0` (`v7.1.0`, `using: 'node24'`)
   - `actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333` (`v8.0.2`, `using: 'node24'`)
   - `docker/build-push-action@c3c9e263c25d99ce0380d002d59b67737d91b0dc` (`v7.4.0`, `using: 'node24'`)
   - `docker/login-action@dbcb813823bdd20940b903addbd779551569679f` (`v4.6.0`, `using: 'node24'`)
   - `actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd` (`v6.0.2`, `using: 'node24'`)
   - `actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9` (`v6.1.0`, `using: 'node24'`)
   - `actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3` (`v9.0.0`, `using: 'node24'`)
   - `actions/attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8` (`v4.2.2`, `using: 'node24'`)
2. **Frontend & Lighthouse CI Runtime:**
   - Updated `NODE_VERSION` from `20` to `24` in `.github/workflows/frontend-deploy.yml` and `.github/workflows/lighthouse-live.yml`.
   - Added `"engines": { "node": ">=24.0.0" }` to `App/frontend/package.json` and aligned `App/frontend/Dockerfile` documentation (`node:26-bookworm-slim` production runner).

## Consequences

- **Positive:** Eliminates Node.js 20 EOL deprecation warnings in GitHub Actions; preserves immutable 40-char commit SHA pinning for SLSA Level 2 and OSSF Scorecard compliance; enforces Node.js 24+ across local, CI, and container environments.
- **Negative / Trade-offs:** Self-hosted runners (if used) must run Actions Runner `>= 2.327.0` to support `node24` actions.

## Confirmation

Validated by:
- `tests/agents/test_journey_copilot_pillars.py::test_qa_audit_subworkflow_cancel_returns_to_parent_and_anon_to_auth_migration`
- `tests/test_monorepo_architecture_governance.py`
- `governance/compliance_check.py`

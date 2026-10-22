# ADR-0011: Node.js 24 LTS Runtime & GitHub Actions Supply-Chain Upgrade (October 2026)

- **Date:** 2026-10-22 (amended 2026-10-22 after the post-#619 audit, see [Amendment](#amendment--2026-10-22-post-619-audit))
- **Owner:** Cloake Tech Limited — DevSecOps, Platform Assurance & QA Engineering

## Status

Accepted (amended)

## Context and Problem Statement

GitHub moved JavaScript actions on hosted runners to Node.js 24 by default on
**2026-06-16** and removed Node.js 20 on **2026-09-23**
([GitHub changelog, 2026-09-23](https://github.blog/changelog/2026-09-23-deprecation-of-node-20-on-github-actions-runners/);
the original draft of this ADR said "October 2026", which was wrong). Workflows still pinned to
`node20` actions emitted deprecation warnings and were set to stop starting:

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

We upgraded the CI/CD supply chain and frontend runtime baseline from Node.js 20 to **Node.js 24 LTS**, keeping full 40-char commit SHA pinning (OpenSSF Scorecard *Pinned-Dependencies*):

1. **Actions pinned to SHAs whose runtime is `node24`, `composite` or `docker`.** The current pins are not restated here, because Dependabot moves them every week. `tests/test_ci_supply_chain_policy.py` enforces the rules that matter:
   - every `uses:` is a 40-character SHA with a `# vX.Y.Z` comment;
   - one action resolves to one SHA across the whole repository;
   - the retired `node20` SHAs listed above never come back.

   Note: `actions/attest-build-provenance@v4.2.2` is `composite` (a wrapper over `actions/attest`), not `node24`.
2. **Frontend, Crane Cloud & Lighthouse CI Runtime:**
   - Updated `NODE_VERSION` from `20` to `24` in `.github/workflows/frontend-deploy.yml` and `.github/workflows/lighthouse-live.yml` (superseded by A6: both now read `App/frontend/.nvmrc`).
   - Upgraded `App/Dockerfile.cranecloud` Stage 2 to `node:24-bookworm-slim` (copying the pinned `oven/bun:1.4.3-slim` binary and `App/frontend/bun.lock*`) and Stage 3 to copy `/usr/local/bin/node` from Stage 2. This removed the mutable `curl | bash` installers (`bun.sh/install` and `deb.nodesource.com`).
   - Added `"engines": { "node": ">=24.0.0" }` to `App/frontend/package.json`. The draft also left `App/frontend/Dockerfile` on `node:26-bookworm-slim`; A6 corrects that.
3. **Container & Dependency Vulnerability Remediation:**
   - Enforced `Pillow>=13.0.0` across `App/backend/requirements*.txt`, `Dockerfile`, and `Dockerfile.gpu` (eliminating 28 `Pillow 12.3.0` CVEs) and `"katex": "^0.18.2"` override in `App/frontend/package.json` (`CVE-2026-103923`).
   - Enforced fail-closed CRITICAL/HIGH vulnerability gating in `.github/workflows/security-trivy.yml`.

## Consequences

- **Positive:** no Node.js 20 actions remain on hosted runners, and commit-SHA pinning is kept throughout.
- **Negative / Trade-offs:**
  - Self-hosted runners must run an Actions Runner release that ships Node 24. GitHub's changelog names **v2.328.0** as the release supporting both Node 20 and 24; the earlier "`>= 2.327.0`" claim was unsourced.
  - Node 24 does not run on macOS 13.4 or older, and has no ARM32 build, so ARM32 self-hosted runners are unsupported.

## Amendment — 2026-10-22 (post-#619 audit)

An audit of the merged branch checked every pinned SHA against its upstream tag; all 28 were genuine. The gaps were elsewhere, and these decisions close them.

| # | Gap found | Decision |
| :-- | :-- | :-- |
| A1 | Workflow inputs and event fields (`inputs.*`, `github.event.*`, `github.base_ref`, build tags) were spliced into `run:`/`script:` bodies in 10 workflows and the `setup-kaggle` composite action. The HF Space roll wrote a raw dispatch `image_tag` into a Dockerfile it pushes to a live Space. | Untrusted values go through `env:` and are referenced as shell variables, and are shape-checked where they reach a command line. Image tags must match the Docker tag grammar. |
| A2 | `cosign verify --certificate-identity-regexp=".*"` accepted a signature from any workflow in any repository. `container-sign-provenance.yml` had no caller, so no image was Sigstore-signed. | Verification is pinned to `^https://github.com/<repo>/.github/workflows/container-sign-provenance.yml@refs/` plus `--certificate-github-workflow-repository`. `ura-chatbot-build-push.yml` calls the workflow for every pushed digest, producing a keyless signature and a signed SLSA provenance attestation (`docker.io/landwind/ura-chatbot`). It does not yet gate deploys: that becomes the next step once it has run green on `dev`. |
| A3 | 49 checkouts left the job token in `.git/config` (zizmor *artipacked*). | `persist-credentials: false` everywhere, except the two jobs that push a commit (`scheduled-crawl.yml`, `ura-chatbot-roll-hf-space.yml`), which set `true` explicitly. |
| A4 | Least-privilege and pinning gaps: `semgrep/semgrep:latest`; workflow-wide `pull-requests: write`; Dependabot auto-merge keyed on the spoofable `github.actor`; checkov pinned at `v12.3122.0` but labelled `# v9`. | Semgrep is pinned by version and digest. The permission is scoped to the one job that comments. Auto-merge checks the PR author. The pin comment is corrected. The reviewed `workflow_run` (Lighthouse) and PDF-cache exceptions carry inline `zizmor: ignore` comments with their justification. |
| A5 | No Dependabot cooldown, while minor/patch PRs are auto-merged on green CI. That is the window the March 2026 `trivy-action` tag hijack ([CVE-2026-33634](https://www.aquasec.com/blog/trivy-supply-chain-attack-what-you-need-to-know)) exploited. The frontend used the `npm` ecosystem, which bumps `package.json` without `bun.lock` (it broke every `--frozen-lockfile` job in #418). `App/Dockerfile.cranecloud` base images were never updated. | `cooldown.default-days: 7` on every ecosystem, and `semver-major-days: 30` for pip and bun; security updates are not delayed. The frontend moves to the `bun` ecosystem. Docker coverage gains `/App`. |
| A6 | Runtime drift: CI tested Node 24 + Bun 1.3.12, while the frontend image ran **Node 26** and both images built with **Bun 1.4.3**. Node 26 is a *Current* release until its LTS promotion on **2026-10-28**; Node 24 has been *Maintenance LTS* since 2026-10-20 and is supported to 2028-04-30 ([nodejs/Release schedule](https://github.com/nodejs/Release/blob/main/schedule.json)). | `App/frontend/.nvmrc` (`24`) and `App/frontend/.bun-version` (`1.4.3`) are the single source of truth: CI reads them through `node-version-file` / `bun-version-file`. Both Dockerfiles use the same majors, and the Node, Bun and Qdrant images are pinned by tag and digest. Moving to Node 26 after 2026-10-28 is one change across `.nvmrc`, both Dockerfiles and `engines`, which the policy test keeps together. |
| A7 | The guard was string matching (six retired SHAs, two quotings of `NODE_VERSION: 20`) inside the journey-copilot tests. | `tests/test_ci_supply_chain_policy.py` checks the rules above structurally in the root suite. `.github/workflows/actions-security.yml` runs zizmor 1.31.1 (medium+, with online impostor-commit and known-vulnerable checks) and actionlint 1.7.12 on every `.github/` change, plus a weekly run. |
| A8 (2026-10-23) | The Python base images, the production `App/Dockerfile.cranecloud` among them, and the vLLM sidecar resolved by tag alone. `ghcr.io/astral-sh/uv:0.7`, a floating minor tag, was copied straight into two builds where Dependabot could not see it. Dependabot did not watch `App/backend` or the Orpheus sidecar. | Every `FROM` and `COPY --from` image is pinned by tag and index digest. uv comes from a named `uv-binary` stage. Dependabot watches every directory that holds a Dockerfile. `test_dockerfile_images_are_digest_pinned` checks all of them, and fails on the six Dockerfiles as they were. |
| A9 (2026-10-23) | The first real `sign` run failed before signing, because the image name reached it empty. `build.outputs.image_name` contains the Docker Hub username, which is stored as a secret, so the runner dropped it (`Skip output 'image_name' since it may contain secret`); `image_ref` was dropped the same way. Verification also accepted `...container-sign-provenance.yml@refs/` at *any* ref, and `sign` ran for manual rebuilds of any branch, so an image built from an unreviewed branch would have carried a valid repository signature. | `sign` gets the image as a literal, and the build job exposes no output that could carry it. `sign` runs only for `dev`, `main` and `v*` tags, and verification accepts only those refs. The policy test pins all three. The deploy gate (G233a) still waits for a green run. |
| A10 (2026-10-23) | Dependabot auto-merge used `gh pr merge --auto` on a `dev` that requires no status checks, so it merged red PRs at once. #633 left `dev` uninstallable that way (gap register G239, G240). | Auto-merge is enabled only when the base branch requires status checks. Otherwise the PR waits for a person. Requiring the CI checks on `dev` and `main` restores auto-merge, and is the owner's decision. |
| A11 (2026-10-23) | The backend dependency audit ran on Python 3.11 against a 3.14 file. It could not resolve, and the gate read the missing report as a pass (G241). | Each requirements file is audited on its own Python. A missing, unreadable or empty report fails the gate. |

## Confirmation

Validated by:
- `tests/test_ci_supply_chain_policy.py` (offline policy; fails 29 checks against the pre-amendment tree)
- `.github/workflows/actions-security.yml` (zizmor + actionlint gate)
- `tests/test_monorepo_architecture_governance.py`
- `governance/compliance_check.py`

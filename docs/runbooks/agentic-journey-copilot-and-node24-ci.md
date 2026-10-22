# Runbook: Agentic Journey Copilot & Node.js 24 CI Supply-Chain Operations

- **Effective Date:** 2026-10-22
- **Related ADRs:** [ADR-0010](../adr/0010-agentic-journey-copilot-prerequisites-a2ui-and-step-up-auth.md), [ADR-0011](../adr/0011-nodejs-24-runtime-and-github-actions-supply-chain-upgrade.md), [ADR-0013](../adr/0013-session-bound-workflow-adoption-and-verified-resume-codes.md)
- **Last revised:** 2026-10-23 (post-#619 audit, third pass: ADR-0013 Amendment A)
- **Primary Modules:** `App/backend/app/workflows/journey_copilot.py`, `App/backend/app/workflows/registry.py`, `App/backend/app/service.py`, `App/frontend/src/components/WorkflowStepper.tsx`, `App/frontend/src/components/ChatMessage.tsx`

---

## 1. Overview

The **Agentic Journey Copilot** provides seven deterministic customer-experience mechanisms for URA guided workflows without introducing extra LLM calls or exposing taxpayer PII:

1. **Pre-Flight Prerequisite DAG (`evaluate_prerequisites`):** Checks workflow prerequisites (`WORKFLOW_PREREQUISITE_DAG`) against `session.slots` and the prerequisite-relevant part of `CaseFacts` (`prerequisite_case_facts()` — only DAG slot names are ever stored in `slots["_case_facts"]`; salary, turnover and residency figures never are). Missing prerequisites emit a 1-tap `remediation_action` (`"Guide me step by step through <Workflow Name>"`).
2. **Declarative A2UI Form Schedule (`build_form_draft_surface`):** Emits an `a2ui/v1` form preparation surface (`WORKFLOW_FORM_SCHEMAS`). Sensitive slots (`_WORKFLOW_SENSITIVE_SLOTS`) are masked as `"•••• (Masked)"` and free-text values are rendered as `"Recorded"`.
3. **Pushdown Goal Stack & Journey GPS (`push_subworkflow`, `pop_subworkflow`, `build_journey_gps`):** Persists parent workflows in `session.slots["_goal_stack"]` across SQLite/Postgres turns. Completing or cancelling a prerequisite sub-workflow automatically pops back to the parent workflow at its parked step.
4. **Proactive Objective Inference (`infer_taxpayer_objective`):** Maps natural-language taxpayer challenges into `objective_hypothesis` metadata on `/v1/chat` and `/v1/chat/stream` and renders the localized Objective Confirmation Card in `ChatMessage.tsx`.
5. **Step-Up Authentication & Anonymous-to-Authenticated State Migration (`evaluate_step_up_auth`, `elevate_anonymous_workflow_session`):** When an anonymous taxpayer signs in mid-conversation, `ChatModel._maybe_handle_workflow()` adopts the unowned anonymous row under the anonymous key **this request can prove** — `conversation_state_key(thread_id, session_id=<same X-Session-ID>, user_id=None)` — and nothing else, so a signed-in caller holding someone else's conversation id cannot take over their answers (ADR-0013). `db.adopt_workflow_session()` re-keys the row in place (compare-and-swap on `user_id = ''`, `status = 'active'`, within retention), so the journey keeps its `created_at`, the funnel shows one journey, and no copy is left under the anonymous key. Zero slot loss (W3C WCAG 2.2 AA SC 3.3.7 *Redundant Entry*). The signed-in marker (`_authenticated_user_id`) is set and cleared per request, never trusted from storage. The same owner-scoped key holds everything else a turn reads back — stated `CaseFacts`, calculator follow-up memory, and the "Continue <flow>" action — and nothing is ever read under the bare conversation id (ADR-0013 A).
6. **Multimodal "Snap-to-Fill" & Stall Scaffolding (`bind_document_facts_to_workflow`, `build_stall_scaffolding`):** Auto-fills workflow slots from analysed document metadata and provides progressive field examples (`failed_attempts == 1`) and officer escalation (`failed_attempts >= 2`).
7. **Cross-Channel Resume Codes (`generate_resume_code`, `parse_resume_code`):** Generates `URA-<PREFIX>-S<N>-<CHECK>` tokens — one registered prefix per workflow in `RESUME_CODE_PREFIXES` (e.g. `RET`, `TIN`, `TPH`, `VAT`, `PYE`), and a 4-hex check over prefix + step that `parse_resume_code()` verifies. Only verified codes route to the target workflow before slot validation; a typo or made-up code is ignored, so it cannot pull the taxpayer out of (and cancel) the flow they are in. Adding a workflow YAML requires adding its prefix (`tests/agents/test_journey_copilot_resume_codes.py` fails otherwise).

---

## 2. Verification & Diagnostic Commands

```bash
# 1. Run the 7-pillar + QA audit + session-binding + resume-code suites
PYTHONPATH=App/backend python3 -m pytest tests/agents/test_journey_copilot_pillars.py \
  tests/agents/test_journey_copilot_session_binding.py tests/agents/test_journey_copilot_resume_codes.py \
  tests/agents/test_conversation_state_owner_binding.py \
  tests/test_guided_journeys_integration.py -q

# 2. Run frontend component, turnMeta, restored-card and 5-locale i18n parity tests
cd App/frontend && bun run test -- src/__tests__/components/WorkflowStepper.test.tsx src/__tests__/lib/turnMeta.test.ts \
  src/__tests__/lib/i18n.test.ts src/__tests__/store/useChatStore.test.ts

# 3. Verify ADR catalog & governance compliance
PYTHONPATH=App/backend python3 -m pytest tests/test_monorepo_architecture_governance.py -q
python3 governance/compliance_check.py
```

---

## 3. CI/CD Supply-Chain Policy (ADR-0011, amended)

The policy is enforced by tests and a CI gate rather than a pin table here (Dependabot moves pins weekly, so a table goes stale on the first bump):

| Rule | Enforced by |
| :--- | :--- |
| Every `uses:` is a 40-char commit SHA with a `# vX.Y.Z` comment; one action → one SHA repo-wide; retired `node20` SHAs never return | `tests/test_ci_supply_chain_policy.py` |
| No `inputs.*` / `github.event.*` / `github.head_ref` / `github.base_ref` spliced into `run:` or `script:` — pass through `env:` and shape-check | policy test + zizmor `template-injection` |
| Every checkout sets `persist-credentials`; only `scheduled-crawl.yml` and `ura-chatbot-roll-hf-space.yml` (they push) keep the token | policy test + zizmor `artipacked` |
| Container images version + digest pinned (never `:latest`), in workflows and in every Dockerfile `FROM` / `COPY --from` (a binary such as uv comes from a named, pinned stage); only `App/deploy/hf-space/Dockerfile` uses our own rolled `sha-` tag; no `pull_request_target`; `workflow_run` only where reviewed (`lighthouse-live.yml`) | policy test + zizmor |
| Node from `App/frontend/.nvmrc`, Bun from `App/frontend/.bun-version` (`node-version-file` / `bun-version-file`); both Dockerfiles use the same majors, digest-pinned | policy test |
| Dependabot `cooldown.default-days >= 7` on every ecosystem (30 for pip/bun majors; security updates are never delayed); frontend on the `bun` ecosystem; docker watches every directory holding a Dockerfile (`/`, `/App`, `/App/backend`, `/App/backend/orpheus_sidecar`, `/App/frontend`) | policy test + zizmor `dependabot-cooldown` |
| Dependabot auto-merge is enabled only when the base branch requires status checks; with none required (`dev` today) PRs wait for a person (G239) | policy test |
| The OpenTelemetry family moves in lockstep (core `1.Y.Z` ↔ contrib `0.(Y+21)bZ`) in all three requirements files; `App/backend/requirements.lock.txt` satisfies `App/backend/requirements.txt`; `requirements-cranecloud.txt` installs on the image's Python (3.11) | policy test |
| `pip-audit` runs each requirements file on the Python it ships on; a missing, unreadable or empty report fails `scripts/check_dependency_advisories.py` | policy test + `tests/test_dependency_advisory_gate.py` |
| `cosign verify` pinned to this repo's `container-sign-provenance.yml` **at a deployable ref** (`refs/heads/dev`, `refs/heads/main`, `refs/tags/v*`); `sign` runs only for those refs and gets the image name as a literal (a build output carrying it is dropped, because it contains the Docker Hub username secret); signing + SLSA provenance run after every such `landwind/ura-chatbot` push — not yet a deploy gate (G233a), so an image can roll out unsigned if that job fails | policy test |

```bash
# Offline policy (also runs in the root pytest suite)
python3 -m pytest tests/test_ci_supply_chain_policy.py -q

# The CI gate (.github/workflows/actions-security.yml) locally
uvx zizmor@1.31.1 --min-severity medium .github/          # add GH_TOKEN for online audits
docker run --rm -e SHELLCHECK_OPTS=--severity=error -v "$PWD:/repo" -w /repo \
  rhysd/actionlint:1.7.12@sha256:b1934ee5f1c509618f2508e6eb47ee0d3520686341fec936f3b79331f9315667

# Re-verify that pinned SHAs are the upstream tag commits (impostor-commit check)
~/bin/gh api repos/<owner>/<repo>/git/ref/tags/<tag> --jq .object.sha
```

**Moving to Node 26** (LTS from 2026-10-28): change `App/frontend/.nvmrc`, the `node:` stages in `App/frontend/Dockerfile` and `App/Dockerfile.cranecloud` (tag **and** digest), and `engines.node` in one PR — the policy test fails if any one of them is left behind. Rebuild and run `bun install --frozen-lockfile && bun run lint && bun run test && bun run build` inside the new image before merging.

**After the first green `sign` job on `dev`:** check the signature and provenance from a workstation, then make `dispatch-deploy` / `dispatch-hf-space-roll` depend on `sign`. The first run (build-push run 39830641557, 2026-10-22) failed before signing because the image name reached it empty (G238), so no image is signed yet:

```bash
cosign verify docker.io/landwind/ura-chatbot@<digest> \
  --certificate-identity-regexp '^https://github\.com/mpairwe7/FinalYearProject/\.github/workflows/container-sign-provenance\.yml@refs/(heads/(dev|main)|tags/v[^/]+)$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
gh attestation verify oci://docker.io/landwind/ura-chatbot@<digest> --repo mpairwe7/FinalYearProject
```

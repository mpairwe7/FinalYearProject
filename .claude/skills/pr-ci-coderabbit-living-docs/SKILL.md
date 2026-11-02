---
name: pr-ci-coderabbit-living-docs
description: Mandatory global workflow for (1) updating living documentation via latest 2026 industry standards (Diátaxis, MADR 4.0 ADRs, ISO/IEC 42001 Clause 7.5, NIST AI RMF GOVERN 1.4 & MAP 4.2, EU AI Act Art. 11/12/50/53, Model Cards, CycloneDX 1.6 AIBOM) in the same PR as any code/config/model/infra change, and (2) resolving all CI check failures and CodeRabbit AI review comments on open Pull Requests before merge.
---

# Global Living Documentation & Open-PR CI / CodeRabbit Gate Skill

Apply this workflow on **every** code, configuration, model, feature-flag, route, or infrastructure change and before merging **any** Pull Request.

---

## 1. Living Documentation via Latest Industry Trends & Standards

1. **Diátaxis Documentation Architecture**:
   - **Operational Runbooks (How-To)**: Update `docs/runbooks/*.md` whenever runtime behavior, failover order, environment variables, circuit breakers, or SLO recovery procedures change.
   - **Technical Reference**: Update `docs/RAG_ARCHITECTURE.md`, `docs/API_REFERENCE.md`, `.env.example`, and `flags.py` whenever defaults, flags, endpoints, or provider chains change.
   - **Explanations & Architecture**: Update `docs/AGENT_ARCHITECTURE.md` and `docs/GAPS_AND_AGENTIC_ROADMAP.md` when graphs, state machines, or guardrails change.
2. **MADR 4.0 Architectural Decision Records (`docs/adr/`)**:
   - Every architectural, model-routing, security, or failover decision must include `## Context and Problem Statement`, `## Decision Outcome`, `## Consequences`, and `## Confirmation`, and be indexed in `docs/adr/README.md`.
3. **AI Governance, Model Cards & AIBOM (ISO/IEC 42001 Clause 7.5, NIST AI RMF `GOVERN 1.4` & `MAP 4.2`, EU AI Act Art. 11/12/50/53, CycloneDX 1.6)**:
   - Update `docs/MODEL_CARD.md` and `governance/ai_risk_manifest.yaml` (`aibom_inventory` + `verified_by` pytest pointers) whenever primary or fallback LLM, ASR, TTS, or MT models change.
4. **Automated Docs-as-Code Verification**:
   ```bash
   PYTHONPATH=App/backend python3 -m app.freshness --check
   python3 governance/compliance_check.py
   python3 threat-model/validate_threats.py
   ```

---

## 2. Open Pull Request CI & CodeRabbit AI Resolution Workflow

Never merge an open Pull Request until all steps below pass:

1. **Inspect & Resolve CodeRabbit AI Review Comments**:
   ```bash
   gh api repos/<owner>/<repo>/pulls/<PR>/comments --jq '.[] | select(.user.login == "coderabbitai[bot]") | {id: .id, path: .path, line: .line, body: .body}'
   gh api repos/<owner>/<repo>/pulls/<PR>/reviews --jq '.[] | select(.user.login == "coderabbitai[bot]") | {id: .id, state: .state, body: .body}'
   ```
   - Read every inline comment and review body from `coderabbitai[bot]`.
   - Fix all valid Major, Minor, Functional Correctness, Security, Test, and Documentation findings directly in the PR branch.
2. **Inspect & Resolve All CI Check Failures**:
   ```bash
   gh pr checks <PR>
   ```
   - Wait until all queued/in-progress checks complete (`0` pending).
   - For any failing job, inspect logs with `gh run view <run_id> --log-failed`, reproduce locally with `pytest` / `bun run test`, commit the fix, push to the PR branch, and re-poll until every check is green.
3. **Merge Only When Green & Clean**:
   - Only run `gh pr merge <PR> --squash --delete-branch` after `gh pr checks <PR>` reports 0 failures/pending and all CodeRabbit comments on the head commit are addressed.

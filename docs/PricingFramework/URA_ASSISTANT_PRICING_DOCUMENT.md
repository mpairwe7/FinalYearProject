# URA AI Intelligent Assistant — Pricing Document and Economic Appraisal

**Prepared for** Uganda Revenue Authority — Office of the Deputy Commissioner General
**Prepared by** Mpairwe Lauben · Rwemera David · Okwel Edgar Mark · Olowo Omondi Philly
School of Computing and Informatics Technology, Makerere University
**Date** 21 August 2026 · **Version** 1.0
**Framework** SWEBOK Guide v4.0 (IEEE Computer Society, 2024) — Chapter 15 *Software Engineering
Economics*, with Chapter 1 §3.2 *Economics of Quality of Service Constraints* and Chapter 7 §1.4
*Majority of Maintenance Costs*

This document supplies the "detailed breakdown of the work completed to date, along with the
team's basis for the proposed fee" undertaken in §5.5 of the *URA AI Intelligent Assistant —
Proposal Summary* (24 July 2026).

---

## How to read the figures in this document

Every quantity carries a marker stating where it comes from. Nothing is presented as a fact
that is actually an assumption.

| Marker | Meaning |
| :--- | :--- |
| **[M]** | **Measured.** Taken from an executed benchmark, a test run, or a count over the delivered source tree. Traceable to a named artefact in this repository. |
| **[E]** | **Estimated.** The team's engineering estimate, produced by a technique named in SWEBOK §15.8 and shown with its inputs so URA can re-derive or replace it. |
| **[P]** | **Parameter for URA.** A value only URA holds. An illustrative value is used so the arithmetic is complete; substituting URA's own figure changes the result and the method stays valid. |

**Currency (SWEBOK §15.9.2, multiple-currency analysis).** The analysis currency is USD, with
UGX shown at an assumed **UGX 3,650 / USD 1 [P]**. This rate is not fixed and should be set at
contract date. A 10% depreciation moves the recommended one-time fee by roughly UGX 88 million.
The team recommends the contract be denominated in **one** currency, with the other shown for
reference only, so exchange movement does not become a dispute.

**Labour rates.** No published Ugandan rate card is asserted. Three rate scenarios are carried
throughout, all marked **[P]**, and every conclusion is tested against all three (§G1,
sensitivity).

---

## Part A — Establishing the decision

### A1. Purpose, and the decision this document supports

SWEBOK §15.9.1 defines a business case as "the consolidated, documented information summarizing
and explaining a recommended business decision from different perspectives (cost, benefit, risk
and so on) for a decision-maker." That is what this document is. It supports one decision:

> **Should URA acquire the AI Intelligent Assistant — the built, deployed and benchmarked system
> — together with full assignment of its intellectual property and a two-year embedded
> engagement of the team that built it; and if so, at what price?**

Price is the one revenue-generating element of the four Ps (SWEBOK §15.10.13); product,
promotion and place are costs. This document therefore does two things a proposal cannot: it
derives a **cost basis** by three independent estimation techniques, and it derives a **value
basis** by the decision techniques SWEBOK prescribes for government organisations. The price
sits between them, and both boundaries are shown.

### A2. Understanding the real problem (SWEBOK §15.2.2)

SWEBOK requires the real problem be established before solutions are priced, using an
interrogative technique such as 5-Whys, and warns that an elicited statement is often a
*solution* rather than the *problem*.

| Why | Answer |
| :--- | :--- |
| **1.** Why does URA need a taxpayer assistant? | Because the register grew from 2.62 m (FY2021/22) to 5.25 m (June 2025) while the guidance channels did not. |
| **2.** Why does that matter? | Because only 2.52 m of the 5.25 m remitted anything in FY2024/25 — 2.73 m are registered but not contributing (Auditor General, FY2024/25). |
| **3.** Why are they not contributing? | Because most non-compliance is confusion, not defiance: 76% of Ugandans report difficulty finding out what taxes they owe (Afrobarometer 886, Oct 2024). |
| **4.** Why can they not find out? | Because guidance is written, English-only and office-hours-bound, against a 74% literacy rate and 40+ living languages. |
| **5.** Why not add officers? | Because headcount scales cost linearly and reach barely at all. Reach is constrained by **language, literacy, distance and hour** — none of which an additional officer relaxes. |

**The real problem is a reach problem, not a staffing problem.** This matters to pricing
directly: it rules out "cost per officer replaced" as the valuation basis, and it makes
*cost-effectiveness per taxpayer reached* the correct one (§E3).

Two things the 5-Whys exposes that a headline framing would hide:

- The binding constraint on value is **adoption**, not capacity. Measured single-GPU capacity
  already exceeds any plausible Year-1 demand by more than two orders of magnitude (§B5, §F5).
  Money spent on more capacity buys nothing; money spent on channel integration and language
  coverage buys everything.
- The 2.73 m dormant registrants are the population where marginal revenue per contact is
  highest, and they are precisely the population written English-only channels do not reach.

### A3. URA's business model in Drucker's terms (SWEBOK §15.1.8)

SWEBOK §15.1.8 adopts Drucker's four questions, and §15.2.2 asks the engineer to look closely
at the client's business model to expose hidden risks and opportunities. Restated for a revenue
authority, where "how do we make money" becomes "how is public value realised":

| Drucker question | URA's answer | Consequence for pricing |
| :--- | :--- | :--- |
| **Who is the customer?** | Two populations, not one: 5.25 m registered taxpayers (and prospective registrants), and URA's own officers whose time the routine load consumes. | The system must be priced as serving both. The staff workbench, analytics and escalation packets are not overhead — they are half the product. |
| **What does the customer value?** | Taxpayer: a correct, checkable answer, now, in their own language. Officer: not answering the same question for the ten-thousandth time. Management: evidence of what taxpayers are actually asking. | Citations, confidence signalling and the auditable guidance record are value-bearing features, not compliance decoration. |
| **How is value realised?** | Voluntary compliance on a register where fewer than half remit; and the tax-to-GDP path from 14.2% toward 20%. | Benefit accrues per *taxpayer converted* and per *contact deflected* — both are countable, so a break-even threshold can be computed (§E2). |
| **What is the underlying economic logic?** | Guidance is a fixed-cost good: writing a correct answer once and serving it a million times costs almost nothing more than serving it once. Human guidance is a variable-cost good. | The system's economics improve superlinearly with adoption; headcount's do not. This is the crossover computed in §E3. |

**Hidden risk this exposes.** URA's value realisation depends on *taxpayers trusting the
answer*. An assistant that is fast and wrong is worth less than nothing to a revenue authority,
because incorrect guidance issued in the Authority's name creates liability and erodes the
voluntary-compliance base it was built to widen. This is why §B3 treats faithfulness, citation
coverage and guardrail defence as **quality indicators with thresholds**, and why the
evaluator–optimizer and claim-verifier packages are priced as core rather than optional.

### A4. Which decision framework applies (SWEBOK §15.3 vs §15.4)

SWEBOK is explicit: "The for-profit decision techniques don't apply when the organization's goal
isn't profit — which is the case in government and nonprofit organizations" (§15.4).

**URA is a government organisation. Therefore:**

| Not used | Used instead | Where |
| :--- | :--- | :--- |
| Minimum acceptable rate of return (§15.3.1) | — no MARR is asserted for URA | — |
| Internal rate of return (§15.1.5) | Benefit–cost ratio (§15.4.1) | §E2 |
| Present-worth ranking of profit streams (§15.3) | Cost-effectiveness analysis, fixed-cost version (§15.4.2) | §E3 |
| — | Break-even analysis (§15.5.1) | §E2, §E4 |
| — | Multiple-attribute decision-making, additive weighting (§15.6.1) | §B4, §E1 |

Equivalence (§15.1.4) is still respected: all cost streams are brought to the same time frame by
discounting at a **social discount rate of 10% [P]** and are compared over a **single planning
horizon of 5 years [E]** (§A5). SWEBOK §15.2.5 warns specifically that using a longer time frame
for one proposal than another makes the shorter one look better when it is not; all five
alternatives in §E1 are therefore evaluated over the same five years.

### A5. Planning horizon and economic life (SWEBOK §15.3.2, §15.3.3)

- **Planning horizon: 5 years [E]** — long enough for the operate-and-maintain phase to
  dominate (which SWEBOK §15.10.11 says it will), short enough that estimates remain
  meaningful for a technology whose model tier changes annually.
- **Economic life of the GPU serving tier: 4 years [E]** — the point at which frozen-asset cost
  and rising operating cost sum to a minimum (§15.3.2). A Year-5 partial refresh line is
  therefore carried in the TCO (§D4).
- **Economic life of the application: not bounded by this horizon.** Because URA owns the source
  and weights outright, replacement (§15.3.4) can be *incremental* — a model tier, a retrieval
  leg or a language can be replaced without replacing the asset. SWEBOK §15.3.4 notes exactly
  this: "To the extent that an asset can be replaced in smaller increments, the decision-maker
  can consider incremental replacement options." The 49 addressable feature flags in the
  delivered system **[M]** are what make that possible in practice.
- **Retirement and lock-in (§15.3.5).** SWEBOK notes retirement decisions "can be influenced by
  lock-in factors such as technology dependency and high exit costs." Under the proposed IP
  assignment, URA's exit cost is **zero**: no licence to terminate, no weights to hand back, no
  vendor whose withdrawal removes the service. This is a priced-in property of Alternative A4
  and a decisive difference from A2 (§E1).

---

## Part B — Analysis and evaluation of every feature

### B1. Method

The system is decomposed into **19 work packages** covering the entire delivered surface. Each
package is evaluated on five axes, and each axis is a named SWEBOK technique:

1. **Evidence** — what exists, counted over the source tree **[M]**.
2. **Replacement effort** — engineer-months to rebuild, by decomposition (§15.8.3) **[E]**.
3. **Quality indicator** — a measured indicator, normalised (§15.7.4) **[M]**.
4. **Impact indicator** — contribution to a named URA business goal (§15.7.4) **[E]**.
5. **Characterization** — the SIPAC state per Figure 15.5 (§15.7.5).

Normalisation follows §15.7.5: indicators are standardised to **[-1, +1]** against a stated
threshold, where **0 means on target**, **+1 means 100% over target**, and **-1 means 100% under
target**. For a higher-is-better indicator with measurement *M* and threshold *T*,
`norm = (M − T)/T`; for lower-is-better, `norm = (T − M)/T`; both clipped to [-1, +1].

### B2. Delivered system — counted scale

All counts are over the delivered source tree at commit `5c1c7cd` (21 Aug 2026) **[M]**.

| Dimension | Count |
| :--- | ---: |
| Backend application code (`App/backend/app`, Python) | 58,693 lines |
| Frontend application code (`App/frontend`, TypeScript/TSX) | 21,189 lines |
| ML / MLOps pipelines (`ml/`) | 25,023 lines |
| Operational scripts (`scripts/`) | 9,546 lines |
| Automated test code (backend, integration, chaos, frontend, E2E) | ≈ 37,100 lines |
| **Total first-party code** | **≈ 149,900 lines** |
| Documentation (58 documents) | 16,765 lines |
| CI/CD pipeline definitions (21 workflows) | 6,975 lines |
| HTTP and WebSocket routes | 73 |
| Registered agent tools (of which 8 statutory calculators) | 25 |
| Addressable feature flags | 49 |
| Automated tests (backend functions / frontend units / E2E suites) | 2,344 / 165 / 14 |
| Indexed knowledge passages | 5,071 |
| Source corpus | 533 MB across 1,334 files, incl. 148 PDFs |
| Delivery record | 899 commits, 255 merged pull requests, 24 Dec 2025 – 21 Aug 2026 |

### B3. The 19 work packages — effort, quality, impact, state

Effort is **engineer-months (EM)** to rebuild the package to the delivered standard — tested,
documented and compliance-mapped — by a competent professional team **[E]**. It is *not* the
team's own expenditure, which is a sunk cost and is treated separately in §D3.

Quality indicators are drawn from the executed benchmark reports of 21 Aug 2026 in
`docs/Reports/` **[M]**. Impact indicators are the team's assessment against the five URA
business goals of §C1 **[E]**.

| # | Work package | Principal evidence | EM | Quality indicator (measured) | Qval | Ival | KAval | State |
| ---: | :--- | :--- | ---: | :--- | ---: | ---: | ---: | :--- |
| 1 | **Retrieval & RAG core** — hybrid dense (BGE-M3) + BM25 + RRF, cross-encoder rerank, semantic cache, query rewrite, query decomposition, corrective re-retrieval, HyDE, circuit breaker | 9,300 LOC; `retriever.py`, `service.py`, `cache.py`, `query.py`, `corrective_rag.py` | 14 | Faithfulness 0.93 vs 0.70 target; citation coverage 10/10 | +0.33 | +0.60 | **+0.47** | Stable |
| 2 | **Generation & model serving** — Sunflower-14B-FP8, vLLM, 4-bit BnB, LoRA routing, quantised variants, speculative decoding, prefix caching, per-turn model tiering | 3,100 LOC; `llm.py`, `providers/` | 10 | p50 2,240 ms full-stack EN; 0% error to c=1,000 | +0.55 | +0.55 | **+0.55** | Stable |
| 3 | **Agentic orchestration** — supervisor/specialist graph, 7 routes, LangGraph orchestrator, loop control, evaluator–optimizer, LLM tiebreak | 5,800 LOC; `agents/`, `graph/build.py` | 11 | Intent determination 100% across en/lg/sw | +0.11 | +0.60 | **+0.36** | Stable |
| 4 | **Tool & MCP layer** — 25 registered tools incl. 8 statutory calculators (PAYE, VAT, WHT, customs, rental, CGT, corporation tax, VAT-registration test), MCP client/transport/policy, Tool-RAG selection, risk tiers and scope declarations | 7,260 LOC; `tools/`, `mcp/`, `calculator_router.py`, `tax/tables.py` | 12 | Arithmetic precision 100%; schema conformance 100% | +0.11 | +0.80 | **+0.46** | Stable |
| 5 | **Statutory knowledge graph** — effective-dated rate graph, rate history, graph-fusion retrieval leg, shadow scoring | 1,493 LOC; `graph/` | 4 | Shadow-scored; not yet in the answer path | — | +0.40 | **+0.40** | Acceptable Impact (Case 3) |
| 6 | **Multilingual & translation** — translate-and-retrieve, Sunbird MT, locale-specific supervisor routing, Ugandan accent detection (5 profiles) | 1,500 LOC; `sunbird.py`, `accent_detector.py` | 6 | FAQ accuracy 100% en/lg/sw; lg p50 619 ms | +0.11 | +1.00 | **+0.56** | Stable |
| 7 | **Voice pipeline** — Whisper ASR, Piper/Spark TTS, streaming WebSocket transport, VAD with hysteresis, barge-in, native voice-to-voice, CosyVoice2 token-level TTS, speculative prefetch, voice consent | 6,600 LOC; `speech_service.py`, `voice_stream*.py`, `voice_ws*.py`, `native_voice/` | 16 | Transcript accuracy 100% en/lg/sw; TTS p50 239–415 ms | +0.15 | +1.00 | **+0.58** | Stable |
| 8 | **Vision & document intelligence** — OCR, document classifier, table structuring, glyph fusion, 5 formats to 40 MiB, malware scan, PDF guards, session-bound passage injection | 4,800 LOC; `vision/`, `documents.py`, `ocr_service.py` | 11 | 40 MiB at 4.82 MB/s; 100% TIN/UGX/date extraction | +0.61 | +0.60 | **+0.61** | Stable |
| 9 | **Safety & guardrails** — OWASP LLM Top 10 input/output guards, PII redaction (TIN/NIN/card/phone), prompt-leak detection, XSS sanitisation, retrieved-passage scrubbing, claim verifier, entailment, authority check | 2,000 LOC; `guardrails.py`, `claim_verifier.py`, `entailment.py`, `authority.py` | 8 | Defence rate 100% vs 95% target, retained at c=250 and over 1,500-probe soak | +0.05 | +1.00 | **+0.53** | Stable |
| 10 | **Identity, consent & data-subject rights** — OIDC/JWT, role claims, RLS multi-tenancy, UDPA 2019 export and erasure, consent grant/withdraw ledger | 2,100 LOC; `auth/`, `tenancy.py`, `voice_consent.py`, `retention.py` | 7 | 0 cross-tenant violations in 600 mixed concurrent ops; UDPA export 6.5 ms | +1.00 | +0.80 | **+0.90** | Stable |
| 11 | **Audit & compliance** — hash-chained audit ledger with Merkle verifier, AI risk manifest, compliance gate, production-readiness gate | 1,300 LOC; `audit/`, `governance/` | 6 | 100% gate verification across NIST AI RMF, ISO/IEC 42001, EU AI Act, OWASP LLM 2026; 28/28 STRIDE threats mitigated | +0.05 | +0.80 | **+0.43** | Stable |
| 12 | **Human-in-the-loop & staff workbench** — HITL routing, SLA tracking, ticket WebSocket with presence, structured handoff packets, notifications, CMS answer overrides, 5 staff pages | 3,500 LOC; `hitl_routing.py`, `ticket_ws.py`, `cms.py`, `/admin/*` | 9 | Staff admin p50 24.96 ms under mixed load; escalation packets carry full context | +0.50 | +0.80 | **+0.65** | Stable |
| 13 | **Analytics, observability & evaluation** — OpenTelemetry GenAI per-stage spans, Prometheus metrics, dashboards, evaluation harness with 9 quality gates, corpus freshness, index lifecycle | 2,600 LOC; `tracing.py`, `analytics.py`, `evaluation.py`, `freshness.py` | 8 | 42/42 CI pipelines green (100% pass rate); 9/9 evaluation quality gates passed | +0.11 | +1.00 | **+0.56** | Stable |
| 14 | **Offline / edge / mobile** — FAISS + ONNX offline RAG, hash-diff delta sync, SHA-256-verified signed bundles, on-device vector search | 1,300 LOC; `offline_rag.py`, `offline_sync.py`, `offline_bundle.py` | 6 | Bundle target ≤ 800 MB enforced in CI; on-device p95 target 180 ms not yet field-measured | -0.10 | +0.60 | **+0.25** | Evolving |
| 15 | **Content operations & corpus engineering** — crawler, PDF and FAQ ingestion, publications pipeline, topic taxonomy, Qdrant backup/restore | 2,200 LOC + 533 MB corpus; `crawl_corpus.py`, `pdf_corpus.py`, `faq_corpus.py` | 9 | 5,071 passages indexed from 148 PDFs; freshness check automated | +0.21 | +0.80 | **+0.51** | Stable |
| 16 | **Export & reporting** — branded conversation PDF, tax-summary PDF, XLSX/DOCX/CSV exports, UDPA portability JSON | 1,500 LOC; `artifact_export.py`, `pdf_export.py` | 4 | Exports 6.5–337.5 ms, sub-second across 4 formats | +0.66 | +0.40 | **+0.53** | Stable |
| 17 | **Personalisation & memory** — working / episodic / semantic memory, decay, fact extraction, reminders | 1,300 LOC; `memory/`, `reminders.py` | 5 | Consent-gated; behind `memory_enabled`, not yet measured in production | — | +0.40 | **+0.40** | Acceptable Impact (Case 3) |
| 18 | **Taxpayer web experience & accessibility** — 12 pages, 45 components, WCAG 2.2 AA, responsive, voice-first mode, streaming reply surface, citation and confidence display | ≈ 17,000 LOC; `App/frontend/src` | 14 | 14 Playwright E2E suites green; WCAG 2.2 AA gate green in CI | +0.17 | +1.00 | **+0.59** | Stable |
| 19 | **Platform, MLOps & assurance** — Docker GPU image, compose overlays, 21 CI/CD pipelines, SBOM (CycloneDX 1.6), SLSA provenance, Kaggle training pipeline, deployment automation, 58 documents, full standards mapping | 6,975 lines CI + 16,765 lines docs + 9,546 lines scripts | 18 | 12/12 standards mapped with gap status closed; 0 exposed credentials (Gitleaks, TruffleHog) | +0.11 | +0.60 | **+0.36** | Stable |
| | **Total** | | **178** | | | | | |

**Reading the characterization (SWEBOK §15.7.5, Figure 15.5).** Thresholds are set at 0 for both
axes — an indicator at target is acceptable, below target is not. `KAval` is computed by the
three existential rules of §15.7.5: where both a quality and an impact indicator exist,
`KAval = (Qval + Ival)/2`; where only one exists, `KAval` is that one and the asset falls into
Case 2 or Case 3 rather than a quadrant.

**The thresholds, stated in full.** So that every `Qval` above can be recomputed independently
rather than taken on trust, the threshold *T* behind each one is set out here. Measurements are
**[M]**, drawn from the benchmark reports of 21 Aug 2026; thresholds are **[E]**, set by the team
before the measurements were taken and open to renegotiation with URA.

| # | Primary quality indicator, as measured **[M]** | Threshold *T* **[E]** | Direction | `Qval` |
| ---: | :--- | :--- | :--- | ---: |
| 1 | Faithfulness 0.93 | 0.70 — the evaluation harness's own release gate | higher-better | +0.33 |
| 2 | Full-stack p50 2,240 ms | 5,000 ms — the conversational-turn budget of §B5 | lower-better | +0.55 |
| 3 | Intent-determination accuracy 100% | 90% | higher-better | +0.11 |
| 4 | Arithmetic precision and schema conformance 100% | 90% | higher-better | +0.11 |
| 5 | *none in the answer path* | — | — | *Case 3* |
| 6 | Multilingual FAQ accuracy 100% | 90% | higher-better | +0.11 |
| 7 | Transcript accuracy 100% | 87%, i.e. WER ≤ 13% | higher-better | +0.15 |
| 8 | Document ingest 4.82 MB/s | 3.0 MB/s — 40 MiB inside a 14 s upload budget | higher-better | +0.61 |
| 9 | Guardrail defence rate 100% | 95% — safety-critical, raised above the 90% default | higher-better | +0.05 |
| 10 | 0 cross-tenant violations in 600 mixed concurrent operations | 0 — zero-defect | at ceiling | +1.00 |
| 11 | Compliance-gate verification 100% | 95% — safety-critical | higher-better | +0.05 |
| 12 | Staff-workbench p50 24.96 ms | 50 ms — interactive-UI budget | lower-better | +0.50 |
| 13 | CI pipeline pass rate 100% (42 of 42) | 90% | higher-better | +0.11 |
| 14 | On-device p95 **not field-measured** | 180 ms, CI-enforced only | *unverified* | −0.10 |
| 15 | 5,071 passages indexed | 4,200 — corpus-coverage target | higher-better | +0.21 |
| 16 | Slowest export 337.5 ms | 1,000 ms — sub-second target across all four formats | lower-better | +0.66 |
| 17 | *none in the answer path* | — | — | *Case 3* |
| 18 | 14 Playwright E2E suites green | 12 — one per taxpayer-facing page group | higher-better | +0.17 |
| 19 | Standards mapped with gap status closed, 12 of 12 = 100% | 90% | higher-better | +0.11 |

Four scoring conventions govern that table, and they are applied without exception:

1. **Rate indicators** are scored against a pass-rate threshold. The default is 90%; it is raised
   to 95% for the two safety-critical packages (9 and 11), where a one-in-twenty failure is not
   an acceptable operating point.
2. **Zero-defect indicators measured at their ceiling** score **+1.00**. A count of zero cannot be
   exceeded, so no proportional scale applies to it.
3. **A target enforced in CI but not verified in the field** scores **−0.10**. This is a deliberate
   penalty rather than a measurement, and it is the reason package 14 is characterized *Evolving*
   instead of *Stable*.
4. **One primary indicator per package.** Corroborating results — package 19's 0 exposed
   credentials and 28/28 STRIDE threats mitigated, package 13's 9/9 evaluation gates, package 1's
   10/10 citation coverage — are recorded as evidence but are **not** double-counted into `Qval`.
   Where a package has several defensible indicators, the **least flattering** one is taken as
   primary. This makes the quality scores conservative by construction, which is the appropriate
   bias in a document that is also asking URA for money.

All values are carried to two decimal places and rounded half-up. On that convention every
`Qval` in the table above and every `KAval` in §B3 reproduces exactly from the measurement, the
threshold and the two normalisation formulae of §B1 — there is no adjusted or judgement-weighted
figure anywhere in the scoring except the single stated −0.10 convention for package 14.

Two results are worth URA's attention:

- **Package 14 (offline / edge) is the only package characterized "Evolving"** — high impact,
  quality below threshold, because its on-device latency target has been enforced in CI but not
  field-measured on real mid-range Ugandan Android hardware. It is the one package where the
  team recommends URA hold payment against a measured field gate (§F3, milestone M2).
- **Packages 5 and 17 sit in Case 3** — impact indicators only. Both are behind default-off
  flags. They are delivered and tested but have not yet earned a quality measurement in the
  answer path, and they are priced as Tier-3 scope accordingly (§F6).

### B4. Multiple-attribute ranking of the packages (SWEBOK §15.6.1, §15.7.7)

SWEBOK §15.7.7 lists the criteria by which software products supporting intangible assets should
be prioritised: impact on business goals, characterization reached, impact on competitors, impact
on the business model, cost to implement, time to implement, and complexity. Those seven criteria
are used exactly, by additive weighting (a compensatory technique, §15.6.1), scored 0–5.

Weights **[E]**: goal impact 0.30, characterization 0.15, competitive position 0.10, business-model
impact 0.15, cost 0.10, time 0.10, complexity 0.10. Cost, time and complexity are scored
inversely (cheap/fast/simple scores high).

| Rank | Package | Score | Why it ranks here |
| ---: | :--- | ---: | :--- |
| 1 | 9 · Safety & guardrails | 4.35 | Highest goal impact of any package: an assistant that can be talked into wrong guidance is a liability, not an asset. Cheap relative to consequence. |
| 2 | 1 · Retrieval & RAG core | 4.25 | The grounded, cited answer *is* the product. Everything else is delivery. |
| 3 | 7 · Voice pipeline | 4.20 | The only package that reaches the ~26% of adults for whom reading is a barrier. Also the sole regional differentiator (§E1). |
| 4 | 6 · Multilingual & translation | 4.15 | Same reach argument, at lower cost and complexity than voice. |
| 5 | 10 · Identity, consent & rights | 4.05 | UDPA 2019 is not optional; measured 0 cross-tenant violations makes it the highest-Qval package. |
| 6 | 12 · HITL & staff workbench | 3.95 | Where officer-time benefit is actually realised, and where URA's own staff touch the system. |
| 7 | 4 · Tool & MCP layer | 3.90 | Turns "here is the rule" into "here is your figure". High impact, moderate cost. |
| 8 | 18 · Taxpayer web experience | 3.80 | Adoption is the binding constraint; this is the adoption surface. |
| 9 | 13 · Analytics & evaluation | 3.75 | The management-intelligence benefit URA cannot get from existing channels. |
| 10 | 2 · Generation & model serving | 3.70 | Essential but substitutable — the model tier can be swapped without touching the rest. |
| 11 | 19 · Platform, MLOps & assurance | 3.55 | High cost, invisible to taxpayers, indispensable to operating the thing. |
| 12 | 11 · Audit & compliance | 3.50 | Enables the auditable-guidance-record benefit; modest cost. |
| 13 | 3 · Agentic orchestration | 3.40 | Raises ceiling quality; the system answers without it. |
| 14 | 15 · Content operations | 3.35 | Ongoing rather than one-off; belongs with URA content staff after handover. |
| 15 | 8 · Vision & document intelligence | 3.10 | Strong capability, but its highest-value use is the *internal* work-product extension, not taxpayer Q&A. |
| 16 | 16 · Export & reporting | 2.95 | Cheap, useful, not load-bearing. |
| 17 | 17 · Personalisation & memory | 2.60 | Case-3 asset. Real value, unproven in production, consent-heavy. |
| 18 | 5 · Statutory knowledge graph | 2.55 | Case-3 asset. The right long-term answer to effective-dated rates; shadow-mode today. |
| 19 | 14 · Offline / edge / mobile | 2.40 | Highest reach potential per shilling in rural districts, but the only "Evolving" package. |

**Reproducing the ranking.** Each package is scored 0–5 against each of the seven criteria and
the scores combined by the weights above. The full 19 × 7 matrix is a working sheet rather than a
result, and it can be reconstructed by any reader from three things already in this document: the
weights stated above, the **EM** column of §B3 (which drives the cost, time and complexity
criteria), and the **State** column of §B3 (which drives the characterization criterion). Note
that the characterization criterion is scored on the *state reached* — Stable, Evolving, Case 2,
Case 3, Warning — and not on the `KAval` magnitude, so the ranking is stable under small revisions
to individual indicators. Differences inside 0.05 should be read as ties: the ranking is intended
to separate tiers of importance, not to order packages 6 and 7 against one another.

This ranking is what makes a **scope-tiered price** possible (§F6) rather than a single
take-it-or-leave-it figure. It is also the honest answer to "what should URA buy first if the
budget is constrained": packages 9, 1, 7, 6, 10.

### B5. The economics of the quality-of-service constraints (SWEBOK §1.3.2)

SWEBOK Chapter 1 §3.2 warns that quality-of-service constraints are challenging "because
engineers do not consider them from an economic perspective," and gives three points to locate:
the **perfection point** (beyond which extra performance carries no benefit), the **fail point**
(below which there is no further loss because the user has already left), and the **most
cost-effective performance level** (maximum positive difference between value and cost).

Located against the measured single-GPU concurrency curve **[M]**
(`docs/Reports/SINGLE_GPU_CAPACITY_LIMITS_REPORT_2026-08-21.md`):

| Concurrency | p50 | p95 | Errors | Economic reading |
| ---: | ---: | ---: | ---: | :--- |
| c = 10 | 30.4 ms | 64.4 ms | 0.0% | **Past the perfection point.** A taxpayer cannot read faster than they can read; 30 ms and 400 ms are indistinguishable in use. Capacity spent here is wasted. |
| c = 100 | 415.3 ms | 461.3 ms | 0.0% | **Most cost-effective performance level.** Sub-500 ms, 214 RPS sustained, zero errors. |
| c = 250 | 1,498.7 ms | 1,862.5 ms | 0.0% | Acceptable degradation. Value falling, cost flat. |
| c = 500 | 2,861.2 ms | 3,528.3 ms | 0.0% | Approaching abandonment. |
| c = 1,000 | 4,657.0 ms | 5,985.1 ms | 0.0% | **At the fail point.** p95 ≈ 6 s is where a taxpayer stops waiting; beyond it there is no further benefit to lose. |

**Perfection point ≈ 500 ms p50 [E]. Fail point ≈ 6 s p95 [M, at the measured ceiling].**
**Economically optimal operating point: c ≤ 100 per GPU.**

This single result does real pricing work. It fixes the GPU count from the demand model instead
of from procurement instinct (§F5), and it says plainly that URA should **not** pay for
additional serving capacity to chase latency below 500 ms — that expenditure buys performance
past the point at which any taxpayer can use it.

---

## Part C — Intangible assets

### C1. URA's business goals and the intangible assets behind them (SWEBOK §15.7.1–7.3)

SWEBOK §15.7.1 gives five generic business-goal categories. Instantiated for URA:

| SWEBOK goal category | URA instantiation | Importance (1–5, §15.7.2) |
| :--- | :--- | ---: |
| Growth and continuity | Convert the 2.73 m dormant registrants into remitting taxpayers | **5** |
| Meeting financial objectives | Tax-to-GDP from 14.2% toward 20% (Minister of Finance, 2 Jul 2026) | **5** |
| Responsibility to employees | Relieve frontline and contact-centre officers of repetitive load; reduce burnout | **4** |
| Responsibility to society | Equitable access to tax information across language, literacy and distance | **5** |
| Managing market position | Regional leadership in digital tax administration | **3** |

§15.7.2 asks for the intangible assets that serve as levers on those goals, and §15.7.3 for the
software products that support them. The mapping — and this is the core of the value argument —
is that **URA already owns the intangible assets; what it lacks is the delivery mechanism.**

| Intangible asset URA already holds | Its current condition | Software packages that activate it |
| :--- | :--- | :--- |
| Published guidance corpus (FAQs, publications, handbooks, 148 PDFs indexed) | Written, English, static, requires the taxpayer to find and read it | 1, 15, 18 |
| Officer tacit knowledge of procedure | Non-transferable, unrecorded, lost when the call ends | 4, 12, 15 |
| Statutory rate and threshold knowledge | Correct, but effective-dating lives in officers' heads | 4, 5 |
| Escalation and triage practice | Works, but every escalation restarts from zero | 12 |
| Taxpayer-education material | Reaches taxpayers when URA can deliver it, not when the question arises | 6, 7, 18 |
| Service-channel playbooks | Cannot hold a back-and-forth about one taxpayer's specific question | 3, 12 |
| Record of guidance issued in URA's name | **Does not exist.** Counter and phone guidance leaves no trace | 11, 13 |

Two observations that bear directly on price:

1. **Most of the value is unlocking assets URA has already paid for.** The guidance corpus, the
   procedural knowledge and the rate tables are existing investments whose return is limited by
   a delivery channel, not by their content. That is why benefit accrues at a scale far above
   the cost of the delivery mechanism (§E2).
2. **One asset does not exist yet and is created outright.** The auditable record of guidance
   issued in the Authority's name (packages 11, 13) is a new intangible asset, not an
   improvement to an old one. SWEBOK §15.1.7 admits precisely this class of value — "grants
   rights and economic benefits to its owner" without physical substance.

### C2. Linking the assets to the business model (SWEBOK §15.7.6)

§15.7.6 asks that the client's business model be visualised enriched with the intangible assets'
status, so leadership can see which proposed solution generates the most value. Stated as a
table, highest KAval first:

| KAval | Packages | Asset state | What it means for URA |
| ---: | :--- | :--- | :--- |
| +0.90 | 10 | Stable | Statutory obligations (UDPA 2019) are met with measured evidence, not assertion |
| +0.65 | 12 | Stable | Officer-time benefit is realisable on day one |
| +0.59 – 0.61 | 8, 18 | Stable | Document intelligence and the adoption surface are both above target |
| +0.51 – 0.58 | 2, 6, 7, 9, 13, 15, 16 | Stable | The reach and safety core is above target across the board |
| +0.36 – 0.47 | 1, 3, 4, 11, 19 | Stable | Above target, with the most headroom for improvement against URA's own corpus |
| +0.40 | 5, 17 | Case 3 | Impact established, quality not yet earned in the answer path |
| +0.25 | 14 | **Evolving** | Impact high, quality below threshold — the one field-measurement gate |

No package is characterized "Warning" (low impact *and* low quality). Every package either
serves a goal with measured quality above threshold, or is explicitly flagged as unproven and
priced as such.

---

## Part D — Cost

### D1. Three independent estimates (SWEBOK §15.8)

SWEBOK §15.8.5 is unambiguous: "when the consequences of a wrong decision are significant,
investing extra effort in developing more than one estimate can be worthwhile… Convergence
suggests the individual estimates are probably accurate." A national-scale system for a revenue
authority is such a case. Three techniques were used.

#### Estimate 1 — Decomposition (SWEBOK §15.8.3)

Bottom-up from the 19 work packages of §B3. The cross-cutting factors §15.8.3 warns about —
requirements work, integration, testing, user documentation — are carried inside packages 13 and
19 rather than omitted.

**178 EM**, range **140–220 EM [E]**. Implied productivity: 149,900 LOC ÷ 178 EM = **842 lines
per engineer-month** of tested, documented, standards-mapped code.

#### Estimate 2 — Parametric, COCOMO II post-architecture (SWEBOK §15.8.4)

§15.8.4 notes parametric estimates are "typically the most accurate, the most defendable and the
easiest to use, provided the equation has been developed and validated."

- Delivered production source: 116,600 SLOC (excluding test code) **[M]**
- Reuse adjustment: 60% new at weight 1.0, 40% integration-over-mature-OSS at AAF 0.35 → **86.3
  KESLOC** equivalent **[E]**
- Scale factors: PREC low (3.72), FLEX nominal (3.04), RESL high (2.83), TEAM very high (1.10),
  PMAT nominal (4.68) → ΣSF = 15.37 → **E = 0.91 + 0.1537 = 1.0637**
- Nominal effort: 2.94 × 86.3^1.0637 = **336.9 PM**
- Effort multipliers: RELY 1.26 · DATA 1.14 · CPLX 1.34 · RUSE 1.07 · DOCU 1.11 · PVOL 1.15 ·
  ACAP 0.71 · PCAP 0.76 · PCON 0.90 · PLEX 0.91 · LTEX 0.91 · TOOL 0.78 · SITE 0.80 → **∏EM = 0.660**

**222 EM [E].**

#### Estimate 3 — Analogy on delivered increments (SWEBOK §15.8.2)

§15.8.2 requires "a suitable analogy for which actual results are known." The known actual is
this project's own delivery record: **255 merged pull requests [M]**, at the phase-scale
granularity visible in the history (each carrying design, implementation, tests, docs and CI).
At a professional rate of **0.5–0.9 EM per merged increment [E]**, central 0.7:

**179 EM**, range **128–230 EM [E].**

*Stated limitation:* this is the weakest of the three legs. SWEBOK §15.8.2 requires an analogy
whose actual results are known, and no validated public cost figure exists for a comparable
revenue-authority conversational-AI programme — Kenya's *Shuru* (launched 2 Apr 2026) has no
published cost. The leg is therefore an internal analogy, and it is reported for convergence
only.

#### Convergence (SWEBOK §15.8.5)

| Technique | SWEBOK § | Estimate | Range |
| :--- | :--- | ---: | :--- |
| Decomposition | 15.8.3 | 178 EM | 140–220 |
| Parametric (COCOMO II) | 15.8.4 | 222 EM | — |
| Analogy on delivered increments | 15.8.2 | 179 EM | 128–230 |
| **Converged (mean)** | **15.8.5** | **193 EM** | **140–230** |

The three estimates span 178–222 EM — a half-span of ±22 EM, or **±11% of the converged mean of
193**. Under §15.8.5 this is convergence, and the converged figure is used. **Replacement effort:
193 engineer-months.**

### D2. Replacement cost at market rates

Converged effort valued at three rate scenarios, all **[P]**:

| Rate scenario | USD / EM | 193 EM (USD) | 193 EM (UGX) |
| :--- | ---: | ---: | ---: |
| Ugandan blended in-house team | 2,400 | 463,200 | 1.69 bn |
| East African specialist vendor | 6,500 | 1,254,500 | 4.58 bn |
| International AI-systems integrator | 14,000 | 2,702,000 | 9.86 bn |

These are **reference ceilings**, not the price. They answer one question: what it would cost
URA to obtain this capability by any route other than the one proposed.

### D3. Sunk cost is deliberately excluded (SWEBOK §15.10.2)

SWEBOK §15.10.2 states plainly: *"Sunk cost refers to unrecoverable expenses that have occurred,
which can cause emotional hurdles looking forward. From a traditional economics viewpoint, sunk
costs should not be considered in decision-making."*

The team's own expenditure — four engineers across eight months, 899 commits, alongside academic
coursework — is a sunk cost. **It is not the basis of the fee, and URA should not price against
it.** It is recorded here once, for completeness, and then set aside.

The economically correct basis, per §15.10.2, is **opportunity cost**: the cost of the
alternative URA must forgo. That is the replacement cost of §D2 and the alternatives of §E1 —
and it is what the fee is set against.

### D4. Total cost of ownership over the planning horizon (SWEBOK §15.10.2, §15.10.11)

SWEBOK §15.10.2 defines TCO as "the total cost for acquiring that product, activating it and
keeping it running," and notes it "holds true especially for software because there are many
not-so-obvious costs related to SPLC activities after initial product development." §15.10.11
adds that the operate-maintain-retire activities "consume more total effort and other resources
than the SDLC activities," and Chapter 7 §1.4 that **over 80% of maintenance is enhancement and
adaptation, not fault-fixing**. All three are honoured below.

All figures USD **[E]**, with **[P]** rate assumptions as stated.

| Cost line | Y1 | Y2 | Y3 | Y4 | Y5 | Total |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| Development fee (milestoned, §F3) | 168,000 | 72,000 | — | — | — | **240,000** |
| Embedded engagement, 4 specialists (§F4) | 134,400 | 134,400 | — | — | — | **268,800** |
| Infrastructure capex — 4-GPU production + 2-GPU staging; Y5 partial refresh | 82,000 | — | — | — | 20,000 | **102,000** |
| Infrastructure operating — power, cooling, hosting, network, ops share | 14,000 | 14,000 | 14,000 | 14,000 | 14,000 | **70,000** |
| Post-handover maintenance — 2 URA FTE from Y3 | — | — | 33,600 | 33,600 | 33,600 | **100,800** |
| Content operations — corpus freshness, 1 URA FTE | 12,000 | 12,000 | 12,000 | 12,000 | 12,000 | **60,000** |
| Model refresh and re-evaluation, annual | 8,000 | 8,000 | 8,000 | 8,000 | 8,000 | **40,000** |
| Independent assurance — penetration test, evaluation audit | 15,000 | 10,000 | 10,000 | 10,000 | 10,000 | **55,000** |
| **Annual total** | **433,400** | **250,400** | **77,600** | **77,600** | **97,600** | **936,600** |

**Five-year TCO: USD 936,600 ≈ UGX 3.42 bn [E].**

Applying equivalence (§15.1.4) at a 10% social discount rate **[P]**:

- **Present worth of cost: USD 772,846 ≈ UGX 2.82 bn**
- **Annual equivalent cost: USD 203,875/yr ≈ UGX 744 m/yr** (capital recovery factor 0.263797)

The annual-equivalent figure of **UGX 744 m/yr** is used for every benefit comparison in Part E,
because SWEBOK §15.1.4 permits comparison only between cash flows expressed in the same time
frame.

**The SPLC warning, quantified.** Post-development lines total USD 427,800 — **45.7% of the
five-year TCO before the system is five years old**, and a rising share every year thereafter.
This is exactly what §15.10.11 predicts, and it is the single most common omission in software
procurement. URA should budget for it explicitly. Per Chapter 7 §1.4, the great majority of that
spend will be **enhancement and adaptation** — new tax measures, new languages, new channels —
not defect repair, and it should be planned as capability development rather than as a warranty
reserve.

---

## Part E — Alternatives, benefit and price

### E1. The alternatives, including do-nothing (SWEBOK §15.1.6, §15.2.3, §15.2.5)

SWEBOK §15.1.6 requires that the do-nothing alternative be considered "in most, but not all,
situations," and §15.2.3 that the best solution must be among the candidates before it can be
chosen. Five mutually exclusive alternatives, all on the **same five-year horizon** as §15.2.5
demands.

| | Alternative | 5-yr cost (USD) **[E]** | Time to national service | Data sovereignty | Language & voice | IP / exit cost |
| :--- | :--- | ---: | :--- | :--- | :--- | :--- |
| **A0** | **Do nothing** — contact centre, counters, portal only | ~0 incremental | n/a | Retained | English, text, office hours | n/a |
| **A1** | **Add human capacity** — scale officers and contact-centre seats | 850,000 – 1,400,000 | 12–18 months to recruit and train | Retained | Limited by who is hired | n/a |
| **A2** | **License a foreign SaaS assistant** | 700,000 – 1,900,000 | 4–8 months | **Taxpayer data leaves Uganda** | Vendor's roadmap, not URA's | **None owned; exit cost total** |
| **A3** | **Procure a greenfield custom build** | 1,500,000 – 3,200,000 | 24–36 months | Retained if specified | As specified, at full cost | Owned on delivery |
| **A4** | **Acquire this system + embed the team** *(proposed)* | **936,600** | **Prototype live today; national scale inside 24 months** | **Retained — self-hosted, no external AI provider** | **en/lg/sw measured at 100% accuracy; voice in both directions** | **Owned outright on signing; exit cost zero** |

**A0 is not free.** Its cost is the status quo: 2.73 m registered non-contributors, 76% of
taxpayers unable to establish what they owe, and — increasingly — taxpayers already asking
general-purpose foreign AI tools about Ugandan tax and receiving answers that blend Ugandan
rules with foreign law. A0 does not avoid AI-mediated tax guidance; it only ensures the guidance
is not URA's.

**Multiple-attribute evaluation (SWEBOK §15.6.1, additive weighting).** Criteria weighted
**[E]**: 5-year cost 0.20, time to service 0.15, data sovereignty 0.20, reach (language, voice,
literacy) 0.20, IP ownership and exit cost 0.15, auditability of guidance 0.10. Scored 0–5.

| | Cost | Time | Sovereignty | Reach | IP / exit | Audit | **Weighted** |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A0 | 5.0 | 0.0 | 5.0 | 1.0 | 2.5 | 0.0 | **2.58** |
| A1 | 2.0 | 1.5 | 5.0 | 1.5 | 2.5 | 1.0 | **2.40** |
| A2 | 2.5 | 3.5 | 0.5 | 3.0 | 0.0 | 2.5 | **1.98** |
| A3 | 1.0 | 0.5 | 4.5 | 4.0 | 4.5 | 4.0 | **3.05** |
| **A4** | **3.5** | **4.5** | **5.0** | **5.0** | **5.0** | **5.0** | **4.63** |

A4 leads on every criterion except raw cost, where A0 necessarily wins and A2/A1 are close.
Note that A2 — the option that looks cheapest on a monthly invoice — scores lowest overall,
because it fails the two criteria a revenue authority cannot trade away: sovereignty over
taxpayer data and ownership of the capability.

### E2. Benefit–cost analysis (SWEBOK §15.4.1)

§15.4.1: "A proposal's financial benefits are divided by its costs. Any proposal with a
benefit-cost ratio of less than 1.0 can usually be rejected without further analysis."

Rather than assert a benefit figure, the analysis is inverted: **what is the minimum benefit at
which BCR = 1.0?** This requires no estimate from the team, only the cost side derived in §D4
and one unit parameter from URA. Annual equivalent cost: **UGX 744 m/yr**.

**Route (a) — voluntary compliance.** Dormant register: 2.73 m registrants **[M, Auditor
General FY2024/25]**. Let *A* = average annual remittance of a newly-remitting small taxpayer
**[P]**.

| A (UGX/yr) | Taxpayers needed for BCR = 1.0 | As a share of the 2.73 m dormant register |
| ---: | ---: | ---: |
| 250,000 | 2,976 | **0.109%** |
| 500,000 | 1,488 | **0.055%** |
| 1,000,000 | 744 | **0.027%** |

**Route (b) — contact deflection.** Let *C* = fully-loaded cost of one human-served contact
**[P]**.

| C (UGX) | Contacts/yr for BCR = 1.0 | Per day |
| ---: | ---: | ---: |
| 2,000 | 372,000 | 1,019 |
| 3,500 | 212,571 | 582 |
| 5,000 | 148,800 | 408 |

**The system pays for itself if it converts roughly one in every two thousand dormant
registrants per year, or deflects on the order of 500 contacts a day.** Against a register of
5.25 m and measured sustained capacity of 214 requests per second, neither threshold is
demanding. This is the central economic finding of this document, and it does not depend on any
optimistic assumption by the team.

**Illustrative BCR at a conservative combined case [E]:** 500 contacts/day deflected at
C = UGX 3,500 (UGX 639 m/yr) plus 0.055% of the dormant register converted at A = UGX 500,000
(UGX 750 m/yr) → benefit UGX 1.389 bn against cost UGX 744 m → **BCR ≈ 1.87**.

At the scenario in §3 of the Proposal Summary — a single percentage point of the dormant
register, 27,300 taxpayers — the compliance benefit alone is UGX 13.65 bn/yr and **BCR ≈ 18.3**.

### E3. Cost-effectiveness analysis (SWEBOK §15.4.2)

§15.4.2 gives two forms; the **fixed-cost** form is used: given a fixed annual envelope, which
alternative maximises benefit? Envelope: **UGX 744 m/yr** (the annual-equivalent cost of A4).

| Alternative | What UGX 744 m/yr buys | Contacts served / yr | Cost per contact |
| :--- | :--- | ---: | ---: |
| **A1** — add officers | ≈ 18.6 fully-loaded officers **[P: UGX 40 m/officer/yr]** at 30 contacts/officer/day × 250 days | 139,527 | **UGX 5,333** |
| **A4** — this system, low adoption | 500 contacts/day | 182,500 | **UGX 4,078** |
| **A4** — this system, moderate adoption | 2,000 contacts/day | 730,000 | **UGX 1,019** |
| **A4** — this system, high adoption | 5,000 contacts/day | 1,825,000 | **UGX 408** |

**Crossover: 382 contacts/day.** Above roughly 382 contacts a day, the system is more
cost-effective per taxpayer served than additional headcount — and unlike headcount its
cost-per-contact keeps falling, because guidance is a fixed-cost good (§A3). Below 382/day,
headcount wins. This is the honest boundary condition of the whole proposal, and it is an
**adoption** threshold, not a technical one.

It also identifies where URA's own effort matters most: promoting the channel is worth more than
enlarging it.

### E4. Break-even on infrastructure: on-premises versus cloud (SWEBOK §15.5.1)

§15.5.1 gives exactly this example — "consider a choice between two cloud service providers…
break-even analysis identifies the use level where the costs are the same." Applied to the
serving tier, sized at 4 GPUs by §B5 and §F5.

Cash flow streams (§15.1.2), USD **[E]** with **[P]** unit rates:

| | On-premises | Cloud (rented GPU) |
| :--- | ---: | ---: |
| t₀ capital | 58,000 (4-GPU A6000-class node) | 0 |
| Annual operating | 10,545 (power 2,532 · cooling 1,013 · hosting 3,000 · ops 4,000) | 55,296 (4 × 720 h × USD 1.60/GPU-h) |

Break-even: 58,000 + 10,545·n = 55,296·n → **n = 1.30 years ≈ 16 months.**

Over the five-year horizon: on-premises **USD 110,725** against cloud **USD 276,480** — a saving
of **USD 165,755 ≈ UGX 605 m**. Because an always-on taxpayer assistant runs at effectively
100% duty, utilisation is far above the 45% break-even threshold.

**On-premises is both cheaper and the only option consistent with the requirement that no
taxpayer information reaches an external AI provider.** The economics and the policy agree,
which is rare and worth stating.

---

## Part F — The price

### F1. Pricing basis (SWEBOK §15.10.13)

§15.10.13: *"Pricing factors include manufacturing cost, market placement, competition, market
condition and product quality."* Each is addressed, and each moves the price:

| Factor | Assessment | Effect on price |
| :--- | :--- | :--- |
| **Manufacturing cost** | 193 EM converged replacement effort (§D1); USD 463 k at the lowest rate scenario | Sets the floor of what the capability is worth to replace |
| **Market placement** | A single client, a public institution, procuring under Ugandan public procurement | Argues for a fixed, milestoned, auditable fee rather than a subscription |
| **Competition** | A2 (foreign SaaS) at USD 700 k–1.9 bn over five years with zero IP; A3 (greenfield) at USD 1.5–3.2 m and 24–36 months | The price must sit clearly below both, and does |
| **Market condition** | Kenya moved first (Apr 2026, text-only WhatsApp); Rwanda and Tanzania have discussed but not deployed | A time-limited window; argues against a price that delays a decision |
| **Product quality** | Measured: 100% guardrail defence, 0.93 faithfulness, 100% multilingual FAQ accuracy, 0 cross-tenant violations, 28/28 STRIDE threats mitigated, 12/12 standards mapped | Supports a price at the upper end of the defensible band, because the assurance work is already done and evidenced |

§15.10.2 also notes that *"the target cost can be below the actual estimated cost."* That is the
case here, deliberately: the recommended fee is well below every replacement-cost reference in
§D2.

### F2. Recommended price

> ### One-time development fee and IP assignment
> ## USD 240,000 · UGX 876,000,000
> Negotiation envelope **USD 190,000 – 300,000** (UGX 694 m – 1.095 bn)

**What the fee covers**

- Full, irrevocable assignment of all intellectual property in the system: source code, trained
  and fine-tuned model weights, fine-tuning datasets, pipeline architecture, the 5,071-passage
  indexed corpus and its ingestion pipelines, 58 documents, 21 CI/CD pipelines, SBOM and
  provenance attestations, and the complete evaluation and benchmark artefact set.
- Zero recurring licence, royalty or per-seat fee thereafter. Exit cost zero.
- Unrestricted right to use, modify, extend, audit, redeploy and sublicense within URA's mandate.
- The team's commitment to an exclusive two-year engagement (priced separately, §F4).

**How the figure is justified**

| Reference | Value | Fee as a share |
| :--- | ---: | ---: |
| Replacement cost, Ugandan blended rate | USD 463,200 | **52%** |
| Replacement cost, East African vendor rate | USD 1,254,500 | **19%** |
| Replacement cost, international integrator rate | USD 2,702,000 | **8.9%** |
| A3 — greenfield procurement, low end | USD 1,500,000 | **16%** |
| Five-year TCO of the whole programme | USD 936,600 | **26%** |
| Implied unit rate against 193 converged EM | USD 1,244 / EM | — |

**What the fee excludes** — infrastructure (§F5, URA-provided), the two-year engagement salaries
(§F4), and the scope extensions of §F6.

### F3. Payment schedule tied to measurable gates

Milestone payments, each released against a gate URA can verify independently. This structure
also discharges SWEBOK §15.2.7 — monitoring the performance of the selected alternative — by
making the estimates in this document falsifiable at each stage.

| | Milestone | Gate URA verifies | Share | USD |
| :--- | :--- | :--- | ---: | ---: |
| **M0** | Signing and IP assignment | Assignment executed; full artefact set (code, weights, corpus, docs, pipelines) delivered into URA custody and independently buildable | 30% | 72,000 |
| **M1** | URA-corpus optimisation and SME validation | Faithfulness ≥ 0.90 on a URA-authored golden set; URA subject-matter experts sign off on a sampled answer set | 20% | 48,000 |
| **M2** | Production hardening and scale acceptance | Measured p50 < 500 ms at the sized concurrency; p95 < 6 s at ceiling; 100% guardrail defence retained; **on-device p95 < 180 ms field-measured on mid-range Android** (closes the package-14 gate, §B3) | 20% | 48,000 |
| **M3** | Channel integration live | Serving on ≥ 3 channels (portal, URA app, WhatsApp) with the staff workbench in daily use by contact-centre officers | 15% | 36,000 |
| **M4** | Handover complete | URA's own team operating, deploying and extending the system unaided for 60 consecutive days | 15% | 36,000 |

M0 at 30% reflects that the majority of the engineering is already complete and transfers on
signing; the remaining 70% is at risk against gates that only URA can certify.

### F4. Two-year embedded engagement — rate card

Per §5.3 of the Proposal Summary, four specialists embedded within URA's IT and customer-service
departments for 24 months, agreed separately from the fee. Rate card **[P]**:

| Tier | USD / person / month | 4 people × 24 months | UGX |
| :--- | ---: | ---: | ---: |
| Graduate specialist contractor | 2,200 | 211,200 | 771 m |
| **Mid specialist contractor (recommended)** | **2,800** | **268,800** | **981 m** |
| Senior specialist contractor | 3,600 | 345,600 | 1.26 bn |

The recommended tier is carried in the TCO of §D4. Its deliverable is not only the production
system but the **transfer of operating capability**: at the end of 24 months URA has a trained
internal team, which is why the post-handover maintenance line in §D4 is staffed by URA from
Year 3 rather than by contract.

### F5. Infrastructure — sizing and budget

URA-provided per §5.2 of the Proposal Summary. Sized from the measured envelope, not from
instinct.

**Demand model [E], from the register [M]:**

| Input | Value |
| :--- | ---: |
| Registered taxpayers | 5,250,000 |
| Year-1 addressable adoption **[P]** | 10% → 525,000 users |
| Sessions per user per year **[P]** | 4 |
| Turns per session **[P]** | 5 |
| Annual turns | 10.5 m |
| Filing-season peak-day multiplier **[P]** | 3× |
| Peak-hour share of peak day **[P]** | 12% |
| **Peak load** | **2.9 turns/sec** |
| Measured full-stack p50 (EN) **[M]** | 2.24 s |
| **Required concurrent generations** | **≈ 7** |

Seven concurrent generations sits deep inside the measured low-latency zone of a **single** GPU
(c ≤ 100, p50 415 ms). Capacity is therefore driven by availability and burst, not by demand:

| Configuration | Rationale |
| :--- | :--- |
| **2 GPUs baseline, active/active** | High availability, not throughput. A single GPU meets Year-1 demand; two ensure no single point of failure. |
| **4 GPUs at filing-season peak** | Absorbs the 3× seasonal multiplier with headroom, and covers concurrent voice sessions. |
| **6–8 GPUs by Year 3** | At 40% adoption, four times Year-1 volume. |
| Production node: 4× 48 GB GPU, 512 GB RAM, 4 TB NVMe | Measured footprint is 5,905 MiB VRAM with 4 workers — 12% of a 48 GB card, leaving ample room for larger model tiers |
| Staging node: 2× 48 GB GPU | §5.2 requires an environment mirroring production |
| Backing services: PostgreSQL 16, Redis 7.4, Qdrant 1.13 | Delivered stateless per 12-factor; embedded SQLite / BM25 / in-memory fallbacks already implemented for degraded operation |

**Budget: USD 102,000 capex + USD 14,000/yr operating** (§D4), on-premises per the break-even
result of §E4.

### F6. Scope tiers, and the extension priced separately

**Tiered scope (SWEBOK §15.10.14, prioritisation).** Derived from the package ranking of §B4, so
that a constrained budget buys the highest-value packages first.

| Tier | Packages included | Effort (EM of the 178 in §B3) | Share | Fee (USD) | What URA gets |
| :--- | :--- | ---: | ---: | ---: | :--- |
| **1 — Core** | 1, 2, 4 (calculators), 9, 10, 11, 12, 13, 16, 18, 19 | 104 | 58% | **155,000** | Grounded, cited answers in English on the web portal; guardrails; UDPA compliance; escalation with context; staff workbench; analytics |
| **2 — Standard** | Tier 1 + 3, 4 (full), 6, 7, 15, 17 | 157 | 88% | **210,000** | Adds agentic orchestration, multilingual en/lg/sw, the full voice pipeline both directions, content operations, personal memory |
| **3 — Full *(recommended)*** | All 19 packages | 178 | 100% | **240,000** | Adds vision and document intelligence, the statutory knowledge graph, offline/edge for rural districts |

Effort shares are taken over the 178 EM of §B3; package 4 is split evenly between its eight
statutory calculators (Tier 1) and its MCP transport, policy and Tool-RAG selection layer
(Tier 2). Note that Tier 1 is priced **above** its pro-rata share of the fee — 58% of the
effort at 65% of the price — because package 19 (platform, MLOps and assurance, 18 EM) is not
divisible: a Tier-1 purchase still needs the whole CI, SBOM, provenance and deployment estate.
Tiers 2 and 3 then price close to or below pro-rata, which is where the incremental argument
below comes from.

**Why Tier 3 is recommended on economic grounds, not enthusiasm.** The step from Tier 2 to Tier
3 costs **USD 30,000** and transfers three packages whose replacement cost at the lowest rate
scenario is **21 EM ≈ USD 50,400**, and at vendor rates USD 136,500. The marginal price is below
the marginal replacement cost, and the capability is already built and tested. Declining Tier 3
does not avoid the cost of those packages — it defers it at a higher unit price. This is the
incremental comparison SWEBOK §15.3.4 prescribes.

**Priced separately — the internal work-product extension.** §2 of the Proposal Summary
describes turning the same foundation inward: reading long filings, case records and bulk
returns, and drafting structured reports for URA officers. Package 8 (vision and document
intelligence, KAval +0.61, measured 40 MiB at 4.82 MB/s) is the foundation for it and is already
delivered. The extension itself is **out of scope of this pricing document** and should be
scoped and priced after the taxpayer-facing system is in production, once URA's own document
volumes and formats are known. Pricing it now would require assumptions the team is not in a
position to make.

---

## Part G — Risk, sensitivity, and monitoring

### G1. Sensitivity analysis (SWEBOK §15.2.6)

§15.2.6 requires that where estimate inaccuracy could change the decision, the engineer consider
ranges of estimates and perform a sensitivity analysis. The decision variable is the
benefit–cost ratio; the test is whether any plausible parameter movement drives it below 1.0.

| Parameter varied | Low | Central | High | Effect on BCR |
| :--- | :--- | :--- | :--- | :--- |
| Converged effort | 140 EM | 193 EM | 230 EM | No effect on BCR — effort sets the fee reference, not the cost stream, and the fee is fixed |
| Rate scenario | Ugandan | Vendor | International | No effect on the fee, which is fixed at USD 240,000 |
| Five-year TCO | −20% (USD 749 k) | USD 937 k | +30% (USD 1.22 m) | Annual equivalent UGX 595 m / 744 m / 967 m; break-even conversion 0.044% / 0.055% / 0.071% of the dormant register — all far below 1% |
| Adoption | 5% | 10% | 25% | Cost-per-contact UGX 8,155 / 4,078 / 1,631 — **the only parameter that can push A4 below A1** |
| UGX/USD | 3,400 | 3,650 | 4,000 | Fee UGX 816 m / 876 m / 960 m; no effect on BCR, since both sides move together |
| Discount rate | 6% | 10% | 14% | Annual-equivalent cost UGX 720 m / 744 m / 768 m; break-even conversion 0.053% / 0.055% / 0.056% |
| Infrastructure choice | On-prem | — | Cloud | Five-year TCO +USD 166 k → annual equivalent UGX 876 m; break-even conversion 0.055% → 0.064% |

**The decision is insensitive to every parameter except adoption.** Under all cost and rate
movements tested, the break-even benefit stays inside 0.044%–0.071% of the dormant register —
between one in 1,400 and one in 2,300 registrants per year. Only a sustained failure of adoption
below roughly **382 contacts/day** (§E3) reverses the decision.

**This locates the risk precisely, and it is not an engineering risk.** It is a channel-adoption
risk, and it is mitigated by the things §B4 ranks highest: voice, local language, and presence on
the channels taxpayers already use.

### G2. Decision-making under risk (SWEBOK §15.2.6)

Expected-value analysis across three adoption scenarios, probabilities **[E]**:

| Scenario | p | Deflection | Conversion of dormant register | Annual benefit (UGX) |
| :--- | ---: | ---: | ---: | ---: |
| Low | 0.25 | 250/day | 0.03% | 0.73 bn |
| Central | 0.50 | 500/day | 0.055% | 1.39 bn |
| High | 0.25 | 1,250/day | 0.14% | 3.51 bn |
| **Expected value** | | | | **1.755 bn** |

**Expected BCR = 1.755 / 0.744 = 2.36.** The low scenario alone still clears BCR = 0.98 — at the
5th-percentile outcome the programme roughly breaks even, and every scenario above it returns a
multiple.

### G3. Monitoring the selected alternative (SWEBOK §15.2.7)

§15.2.7: *"The software engineer needs to 'close the loop' on estimates by comparing them to the
actual outcomes. Otherwise, no one will ever know if the estimates were good."* The following
are the estimates in this document that URA should hold the team to, with the baseline each is
measured against. Each is already instrumented — the analytics, tracing and evaluation packages
(13) exist to produce exactly these numbers.

| Estimate made here | Baseline | Review point | Where it is measured |
| :--- | :--- | :--- | :--- |
| p50 < 500 ms at sized concurrency | 415 ms measured at c = 100 | M2, then quarterly | OpenTelemetry per-stage spans; Prometheus |
| Faithfulness ≥ 0.90 on URA corpus | 0.93 on the public corpus | M1, then monthly | Evaluation harness, 9 quality gates |
| Guardrail defence rate 100% | 100% across 1,500-probe soak | M2, then per release | Red-team gate in CI |
| Contacts deflected per day | Not yet measurable | Monthly from go-live | Analytics dashboard; contact-centre volume comparison |
| Conversion of dormant registrants | Not yet measurable | Annually | URA register data — **only URA can measure this** |
| Officer time released | Not yet measurable | Quarterly | Ticket volumes and handling times, staff workbench |
| Five-year TCO USD 936,600 | — | Annually | URA finance against the §D4 table |

The last three are the estimates most likely to be wrong, and the two that matter most for the
BCR can only be closed by URA. The team recommends they be written into the engagement as joint
reporting obligations rather than left to goodwill.

### G4. Assumptions and exclusions

**Assumptions carried.** UGX 3,650/USD; 10% social discount rate; 4-year GPU economic life;
5-year planning horizon; on-premises hosting; Year-1 adoption of 10% of the register; all three
labour rate scenarios; the unit parameters *A* (remittance per newly-compliant taxpayer) and *C*
(cost per human-served contact), which only URA holds.

**Excluded from every figure in this document.** URA-side project management and procurement
cost; identity-provider licensing and integration into URA's existing IdP; WhatsApp Business API
fees and channel commercial terms; network and connectivity provisioning; URA staff time for
subject-matter validation; legal review of the IP assignment and data-sharing agreement; the
internal work-product extension (§F6); and any integration with the taxpayer registry, EFRIS or
the portal, which §5.1 of the Proposal Summary places under URA IT supervision and whose cost
cannot be estimated without access.

**What would change the price.** A material reduction in scope (the tiers of §F6 are the priced
options); a requirement to transfer IP without the two-year engagement, which raises the fee
because handover risk shifts entirely to URA; a requirement for the team to provide
infrastructure rather than URA; or extension to languages beyond en/lg/sw, which is priced per
language against package 6.

---

## Summary of the offer

| Line | Basis | USD | UGX |
| :--- | :--- | ---: | ---: |
| One-time development fee and full IP assignment | §F2, milestoned §F3 | **240,000** | **876,000,000** |
| Two-year embedded engagement, 4 specialists | §F4, recommended tier | 268,800 | 981,120,000 |
| Infrastructure, URA-provided | §F5 | 102,000 capex + 14,000/yr | 372 m + 51 m/yr |
| **Five-year total cost of ownership** | §D4 | **936,600** | **3.42 bn** |
| Annual equivalent cost at 10% | §D4 | 203,875 | 744,000,000 |

**The recommendation.** Alternative A4 scores highest on every criterion except raw cost
(§E1), returns an expected benefit–cost ratio of 2.36 (§G2), breaks even on converting roughly
one in two thousand dormant registrants per year (§E2), and is the only alternative that keeps
taxpayer data in Uganda while leaving URA owning the capability outright with zero exit cost.

The team recommends **Tier 3 at USD 240,000**, milestoned per §F3, with the two-year engagement
agreed alongside it and infrastructure provided by URA per §F5.

---

### Framework references

All framework citations are to *SWEBOK Guide v4.0*, IEEE Computer Society, 2024
(`docs/PricingFramework/swebok-v4.pdf`):

| § | Topic | Used in |
| :--- | :--- | :--- |
| 1.3.2 | Economics of Quality of Service Constraints | §B5 |
| 7.1.4 | Majority of Maintenance Costs | §D4 |
| 15.1.2 | Cash Flow | §E4 |
| 15.1.4 | Equivalence | §A4, §D4 |
| 15.1.5 | Bases for Comparison | §D4 |
| 15.1.6 | Alternatives, including do-nothing | §E1 |
| 15.1.7 | Intangible Assets | §C1 |
| 15.1.8 | Business Model | §A3 |
| 15.2.2 | Understand the Real Problem | §A2 |
| 15.2.3 | Identify All Technically Feasible Solutions | §E1 |
| 15.2.4 | Define the Selection Criteria | §A4 |
| 15.2.5 | Evaluate Each Alternative | §E1 |
| 15.2.6 | Ranges, sensitivity, decision under risk | §G1, §G2 |
| 15.2.7 | Monitor the Selected Alternative | §F3, §G3 |
| 15.3.2–3.5 | Economic life, planning horizon, replacement, retirement | §A5 |
| 15.4.1 | Benefit–Cost Analysis | §E2 |
| 15.4.2 | Cost-Effectiveness Analysis | §E3 |
| 15.5.1 | Break-Even Analysis | §E2, §E4 |
| 15.6.1 | Compensatory Techniques (additive weighting) | §B4, §E1 |
| 15.7.1–7.7 | Identifying and Characterizing Intangible Assets (SIPAC) | §B3, §C1, §C2 |
| 15.8.2–8.5 | Analogy, Decomposition, Parametric, Multiple Estimates | §D1 |
| 15.9.1 | Business Case | §A1 |
| 15.9.2 | Multiple-Currency Analysis | *How to read the figures* |
| 15.10.2 | Cost and Costing, TCO, sunk cost, opportunity cost | §D3, §D4, §F1 |
| 15.10.11 | Product Life Cycle (SPLC vs SDLC) | §D4 |
| 15.10.13 | Price and Pricing | §A1, §F1 |
| 15.10.14 | Prioritization | §F6 |

### System evidence referenced

| Artefact | Supplies |
| :--- | :--- |
| `docs/Reports/MASTER_DOCKER_GPU_BENCHMARK_REPORT_2026-08-21.md` | Container throughput, multilingual speech latency, document scaling, export latency, tenant isolation |
| `docs/Reports/SINGLE_GPU_CAPACITY_LIMITS_REPORT_2026-08-21.md` | The concurrency curve underlying §B5 and §F5 |
| `docs/Reports/SECURITY_COMPLIANCE_STRESS_REPORT_2026-08-21.md` | Guardrail defence rate, STRIDE coverage, standards verification |
| `docs/Reports/MULTILINGUAL_FAQ_FULL_STACK_ACCURACY_REPORT_2026-08-21.md` | en/lg/sw FAQ accuracy and latency |
| `docs/Reports/MCP_CONVERSATIONAL_PAINPOINTS_ACCURACY_REPORT_2026-08-21.md` | Calculator arithmetic precision, schema conformance |
| `docs/Reports/EMOTIONAL_INTELLIGENCE_INTENT_STRESS_REPORT_2026-08-21.md` | Intent determination and distress-recognition accuracy |
| `docs/Reports/LOAD_STRESS_ENDURANCE_TEST_REPORT_2026-08-21.md` | Peak throughput, multi-format ingestion, CI gate status |
| `docs/EVALUATION_REPORT.md` | Faithfulness, citation coverage, the 9 quality gates |
| `docs/MODEL_CARD.md` | Model inventory and component parameters |
| `governance/ai_risk_manifest.yaml` | NIST AI RMF, ISO/IEC 42001, EU AI Act control status |
| `docs/capstone/week10-standards-relationship-map.md` | The 10-standard compliance map and gap status |
| `docs/Reports/URA AI Intelligent Assistant - Proposal Summary.pdf` | Engagement terms this document prices (§5.1–5.7) |

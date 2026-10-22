# 200 Complex Case CX & Agentic Journey Copilot Live Ngrok Benchmark Report (2026-10-22)

- **Prepared by:** Cloake Tech Limited — Quality Assurance, Systems Audit & MLOps Engineering
- **Date:** 22 October 2026
- **Live Public Endpoint:** `https://struttingly-nongeological-briella.ngrok-free.dev/api` (`/v1/chat`)
- **Docker GPU Topology (Free GPUs Used During Benchmark & Cleaned Up Post-Run):**
  - **GPU 2 (`NVIDIA RTX A6000`, 49,140 MiB):** `ura-app-api` (`BAAI/bge-m3` dense embeddings + `mixedbread-ai/mxbai-rerank-base-v2` cross-encoder reranker + `Sunbird/SunflowerASR-51-african-languages`) — **6,587 MiB VRAM used (13.4%)**
  - **GPU 4 (`NVIDIA RTX A6000`, 49,140 MiB):** `ura-app-vllm-sunflower` (`Sunbird/Sunflower-Qwen3.5-9B`, FP8 weights + FP8 KV cache, `--max-model-len 16384`) — **42,617 MiB VRAM used (86.7%)**
  - **Post-Benchmark Cleanup:** All project containers (`ura-app-frontend`, `ura-app-api`, `ura-app-vllm-sunflower`, `ura-app-qdrant`, `ura-app-redis`) and ngrok tunnel terminated; **GPU 2 and GPU 4 returned to 14 MiB / 49,140 MiB (0% utilization)**.
- **Harness:** [`scripts/evaluate_journey_copilot_200_complex_ngrok.py`](../../scripts/evaluate_journey_copilot_200_complex_ngrok.py)
- **Raw Results Artifact:** [`docs/Reports/data/journey_copilot_200_complex_ngrok_2026-10-22.json`](data/journey_copilot_200_complex_ngrok_2026-10-22.json)

---

## 1. Executive Summary & Overall Metrics

| Metric | Target SLA | Measured Result | Status |
| :--- | :---: | :---: | :---: |
| **Complex Scenarios Evaluated** | 200 | **200** (`JCP-001`..`JCP-200`) | **MET ✅** |
| **Scenario-Level Accuracy** | $\ge 98.0\%$ | **100.0% (200 / 200)** | **MET ✅** |
| **Conversational Turn Accuracy** | $\ge 99.0\%$ | **100.0% (581 / 581 turns)** | **MET ✅** |
| **Turn Latency (p50 — End-to-End via Ngrok TLS)** | $< 500\text{ ms}$ | **350.81 ms** | **MET ✅** |
| **Turn Latency (p90 — End-to-End via Ngrok TLS)** | $< 1,500\text{ ms}$ | **562.72 ms** | **MET ✅** |
| **Turn Latency (p95 — End-to-End via Ngrok TLS)** | $< 3,000\text{ ms}$ | **593.15 ms** | **MET ✅** |
| **Turn Latency (p99 — End-to-End via Ngrok TLS)** | $< 5,000\text{ ms}$ | **1,671.58 ms** | **MET ✅** |
| **Mean Turn Latency** | $< 1,000\text{ ms}$ | **496.04 ms** | **MET ✅** |
| **Multi-Turn Scenario Latency (p50 / p95)** | $< 3,000\text{ ms}$ | **1,225.55 ms / 2,343.55 ms** | **MET ✅** |
| **Sustained Throughput (2 Concurrent Ngrok Workers)** | $\ge 2.0\text{ turns/s}$ | **4.03 turns/sec** (581 turns in 144.34s) | **MET ✅** |
| **Sensitive Slot / PII Leakage Rate** | $0.0\%$ | **0.0% (0 leaks across 581 turns)** | **MET ✅** |

---

## 2. Per-Pillar Accuracy & Latency Breakdown (20 Scenarios per Pillar = 200 Total)

| Pillar | Scenarios | Passed | Scenario Accuracy | Total Turns | Turn Accuracy | Turn p50 (ms) | Turn p95 (ms) | Turn Mean (ms) | What Was Verified |
| :--- | :---: | :---: | :---: | :---: | :---: | ---: | ---: | ---: | :--- |
| **Pillar 1: Pre-Flight Prerequisite DAG** | 20 | 20 | **100.0%** | 55 | **100.0%** | 342.48 | 588.55 | 416.93 | `pending` $\rightarrow$ `satisfied` / `missing` transitions and 1-tap `remediation_action` across 9 workflows (`return_filing`, `tin_registration`, `payment_assistance`, `tax_clearance`, `customs_clearance`, `objection_or_dispute`, `motor_vehicle_registration`, `audit_invoice_compliance`, `mda_ntr_resolution`) |
| **Pillar 2: Declarative A2UI Form Co-Completion** | 20 | 20 | **100.0%** | 64 | **100.0%** | 335.73 | 559.81 | 402.09 | `a2ui/v1` protocol surfaces (`URA-ETAX-RET-01`, `URA-REG-DT-101`, `URA-PRN-PAY-01`, `URA-TCC-APP-01`, `URA-ASY-CUS-01`, `URA-OBJ-TAT-01`, `URA-MVR-NIP-01`, `URA-MDA-NTR-01`), progressive `completion_pct` ($0\% \rightarrow 100\%$), and PII masking |
| **Pillar 3: Pushdown Goal Stack & Sub-Workflow Continuity** | 20 | 20 | **100.0%** | 85 | **100.0%** | 348.65 | 585.47 | 397.81 | Multi-turn prerequisite sub-workflow push (`goal_stack`), slot carry-over into child workflow, automatic parent pop on child completion, and QA-2 parent restoration when a child sub-workflow is cancelled |
| **Pillar 4: Mid-Workflow Tangent Digressions & Journey GPS** | 20 | 20 | **100.0%** | 60 | **100.0%** | 369.36 | 1,661.81 | 738.65 | Mid-workflow statutory rate, deadline, and concept questions across RAG and Education routes preserving `"Continue <Workflow Name>"` and resuming the parked step |
| **Pillar 5: Proactive Objective Inference & Confirmation Cards** | 20 | 20 | **100.0%** | 40 | **100.0%** | 458.43 | 2,741.51 | 1,086.96 | Natural-language challenge narratives (6% WHT invoice deductions, locked e-Tax accounts, TCC tender bids, Malaba/Busia/Entebbe customs holds, assessment disputes, vehicle transfers, new business onboarding) $\rightarrow$ `objective_hypothesis` + 1-tap workflow launch |
| **Pillar 6: Step-Up Authentication & Zero-Data-Loss Elevation** | 20 | 20 | **100.0%** | 81 | **100.0%** | 333.92 | 555.63 | 385.01 | Inline `step_up_auth` cards (`recover_access`, `prepare_prerequisites`, `login_or_register`) with `preserves_slots: true` and W3C WCAG 2.2 AA SC 3.3.7 compliance |
| **Pillar 7: Adaptive Stall Scaffolding & Escalated Assist** | 20 | 20 | **100.0%** | 70 | **100.0%** | 430.60 | 565.73 | 438.28 | Progressive field-level scaffolding (`inline_example` on first invalid slot input; `escalated_assist` with `"Talk to an officer"` on repeated invalid inputs) followed by clean slot recovery |
| **Pillar 8: Cross-Channel Resume Codes & Direct Token Routing** | 20 | 20 | **100.0%** | 30 | **100.0%** | 528.98 | 612.68 | 479.44 | `URA-<PREFIX>-S<N>-<HASH>` generation and direct token routing (`parse_resume_code`) from both cold start and mid-workflow switch across 10 workflows |
| **Pillar 9: 5-Locale Multilingual Journey Copilot Parity** | 20 | 20 | **100.0%** | 72 | **100.0%** | 330.99 | 555.61 | 400.73 | Full multi-turn workflow execution across English (`en`), Luganda (`lg`), Kiswahili (`sw`), Runyankole (`nyn`), and Acholi (`ach`) including vernacular slot aliases (`omuntu`, `kkampuni`, `binafsi`, `ekitongore`, `ngat`, `yee`, `ndiyo`, `ego`, `eyo`, `momo`, `bbanka`, `benki`) |
| **Pillar 10: High-Stakes Calculators, Cross-MDA & Crisis Guardrails** | 20 | 20 | **100.0%** | 24 | **100.0%** | 558.00 | 627.34 | 534.23 | Exact statutory arithmetic (PAYE bands, 18% VAT add/extract, 6% WHT, 12% individual rental tax, 30% CIT), out-of-jurisdiction refusal (Kenya KRA), and immediate crisis helpline intervention (`999` / `0800 21 21 21`) |
| **Total / Aggregate** | **200** | **200** | **100.0%** | **581** | **100.0%** | **350.81** | **593.15** | **496.04** | **All 10 Pillars Verified at 100.0% Accuracy Over Live Ngrok + Docker GPU** |

---

## 3. Runtime Defects Discovered & Fixed During Live Ngrok Benchmarking

1. **Education Route Preserving Parked Workflow Resumption (`App/backend/app/service.py`):**
   - *Observation:* When a mid-workflow side question (e.g., *"What does EFRIS mean for a business?"* or *"What is rental income tax in Uganda?"*) routed to the deterministic Education tool (`retrieval_mode: "education"`), the education action builder overwrote `next_actions` without checking `_get_suspended_workflow_name()`.
   - *Fix:* Added centralized suspended-workflow preservation in both the Education route and `ChatModel.generate` post-guidance finalization so `"Continue <Workflow Name>"` is always prepended at index 0 whenever an active workflow is parked on the conversation.
2. **Objective Hypothesis Attachment on Escalation-Flagged Account Recovery Queries (`App/backend/app/turn_guidance.py`):**
   - *Observation:* Queries such as *"I am locked out of URA portal and need to recover my TIN account"* triggered `escalation_required=True` (`account-specific query needs authenticated lookup or human review`), which previously skipped `infer_taxpayer_objective()`.
   - *Fix:* Enabled `infer_taxpayer_objective()` to attach `result["objective_hypothesis"]` on all non-safety turns so the UI still displays the structured `portal_access_recovery` Objective Card alongside human-review options.
3. **Vernacular `organisation` $\leftrightarrow$ `ngo` Enum Alias Resolution (`App/backend/app/workflows/slots.py`):**
   - *Observation:* Runyankole (`ekitongore`), Luganda (`ekitongole`), Kiswahili (`shirika`), and Acholi (`dul`) canonicalized to `"organisation"`, whereas `tin_registration.yaml` and `return_filing.yaml` use `enum[individual,company,ngo]`.
   - *Fix:* Updated `_match_enum()` in `slots.py` to map `"organisation"` to `"ngo"` when `"ngo"` is in the step's allowed enum options and `"organisation"` is not.

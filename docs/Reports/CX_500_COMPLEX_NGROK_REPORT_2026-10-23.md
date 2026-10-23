# 500-Scenario Complex Customer Experience (CX), Cross-MDA & Agentic Journey Copilot Live Evaluation Report (`2026-10-23`)

- **Date**: `2026-10-23`
- **Target Ingress**: `https://struttingly-nongeological-briella.ngrok-free.dev/api` (Next.js 16 Frontend Proxy $\to$ `ura-app-api:gpu` on GPU 2 $\to$ `ura-app-vllm-sunflower` `Sunbird/Sunflower-Qwen3.5-9B` FP8 on GPU 4 + `ura-app-qdrant` `v1.19.0` `7,251` hybrid vectors + `ura-app-redis` `7.4-alpine`)
- **Architectural Scope Validated**: `ADR-0008` (Live Web Research & Self-Updating Qdrant), `ADR-0009` (10-MDA Statutory Harvester, 6 Cross-MDA & Queue-Reduction Tools, `mda_ntr_resolution.yaml` & `new_taxpayer_and_relief.yaml`), `ADR-0010` (7-Pillar Agentic Journey Copilot), and `ADR-0012` (Modularity, Dual-Database Parity, Pre-Indexed `ExpRAG`, `BPO-RAG`/`DICE` Preferences, Fail-Closed `TurnPipelineOrchestrator` & `G224` False-Premise Hardening)
- **Benchmark Harness**: [`scripts/evaluate_cx_500_complex_ngrok.py`](../../scripts/evaluate_cx_500_complex_ngrok.py)
- **Raw Machine-Readable Telemetry**: [`docs/Reports/data/cx_500_complex_ngrok_2026-10-23.json`](data/cx_500_complex_ngrok_2026-10-23.json)

---

## 1. Executive Summary & Headline Metrics

| Metric | Measured Result (`2026-10-23`) | Target SLO | Status |
| :--- | :---: | :---: | :---: |
| **Total Complex Scenarios Evaluated** | **`500`** (`CX500-001`..`CX500-500` across 10 Pillars) | `500` | **PASS** |
| **Scenario-Level Accuracy** | **`500 / 500` (`100.00%`)** | $\ge 98.0\%$ | **PASS** |
| **Turn-Level Accuracy** | **`1,452 / 1,452` (`100.00%`)** | $\ge 99.0\%$ | **PASS** |
| **Turn Latency (`p50` / Median)** | **`335.81 ms`** | $\le 1,000\text{ ms}$ | **PASS** |
| **Turn Latency (`p90`)** | **`404.57 ms`** | $\le 1,500\text{ ms}$ | **PASS** |
| **Turn Latency (`p95`)** | **`455.83 ms`** | $\le 3,000\text{ ms}$ | **PASS** |
| **Turn Latency (`p99`)** | **`800.41 ms`** | $\le 5,000\text{ ms}$ | **PASS** |
| **Multi-Turn Scenario Latency (`p50` / `p95`)** | **`1,006.10 ms` / `2,018.58 ms`** | $\le 5,000\text{ ms}$ | **PASS** |
| **Sustained Throughput (4 Workers over Ngrok TLS)** | **`10.38 turns/sec`** (`139.86 s` wall clock for `1,452` turns) | $\ge 5.0\text{ turns/s}$ | **PASS** |
| **HTTP `5xx` / Unhandled Exceptions** | **`0`** (`0.00%`) | `0` | **PASS** |

---

## 2. Per-Pillar Accuracy & Latency Breakdown (`50` Scenarios per Pillar)

| Pillar | Scenarios (`CX500-*`) | Scenario Accuracy | Turn Accuracy | Turn `p50` (`ms`) | Turn `p95` (`ms`) | Primary Capabilities Verified |
| :--- | :---: | :---: | :---: | ---: | ---: | :--- |
| **Pillar 1: Pre-Flight Prerequisite DAG & Readiness Gate** | `CX500-001`..`050` (`50`) | **`50 / 50` (`100.0%`)** | `139 / 139` (`100.0%`) | `330.83` | `356.20` | `WORKFLOW_PREREQUISITE_DAG` across all 9 DAG-enabled workflows (`pending` $\to$ `satisfied` / `missing` + remediation actions) |
| **Pillar 2: Declarative A2UI Form Co-Completion & PII Masking** | `CX500-051`..`100` (`50`) | **`50 / 50` (`100.0%`)** | `165 / 165` (`100.0%`) | `336.61` | `361.70` | `URA-ETAX-RET-01`, `URA-REG-DT-101`, `URA-PRN-PAY-01`, `URA-TCC-APP-01`, `URA-ASY-CUS-01`, `URA-OBJ-TAT-01`, `URA-MVR-NIP-01`, `URA-MDA-NTR-01` |
| **Pillar 3: Pushdown Goal Stack & Sub-Workflow Continuity** | `CX500-101`..`150` (`50`) | **`50 / 50` (`100.0%`)** | `212 / 212` (`100.0%`) | `332.98` | `356.42` | `Plover` pushdown `goal_stack`: parent $\to$ child push, completion auto-pop, and `cancel` pop restoring parent workflow |
| **Pillar 4: Mid-Workflow Tangent Digressions & Journey GPS** | `CX500-151`..`200` (`50`) | **`50 / 50` (`100.0%`)** | `150 / 150` (`100.0%`) | `346.60` | `1,694.34` | Side-question RAG/FAQ answer mid-workflow (`return_filing`, `payment_assistance`, `mda_ntr_resolution`, `new_taxpayer_and_relief`) + `"Continue ..."` GPS resume |
| **Pillar 5: Proactive Objective Inference & Confirmation Cards** | `CX500-201`..`250` (`50`) | **`50 / 50` (`100.0%`)** | `100 / 100` (`100.0%`) | `379.89` | `588.30` | All 7 `objective_hypothesis` rules (`wht_credit_and_filing`, `portal_access_recovery`, `tax_clearance_application`, `customs_cargo_clearance`, `tax_objection_dispute`, `motor_vehicle_transfer`, `new_business_tax_onboarding`) + 1-tap launch |
| **Pillar 6: Step-Up Authentication & Adaptive Stall Scaffolding** | `CX500-251`..`300` (`50`) | **`50 / 50` (`100.0%`)** | `190 / 190` (`100.0%`) | `334.08` | `452.29` | RFC 9470 `step_up_auth` (`recover_access`, `prepare_prerequisites`, `login_or_register`) + progressive `inline_example` & `escalated_assist` scaffolding |
| **Pillar 7: 10-MDA Cross-Agency NTR & PRN Resolution (`ADR-0009`)** | `CX500-301`..`350` (`50`) | **`50 / 50` (`100.0%`)** | `200 / 200` (`100.0%`) | `332.51` | `353.88` | Full 4-turn `mda_ntr_resolution` across all 8 MDA services (`MoWT`, `DCIC`, `NIRA`, `MLHUD`, `KCCA/IRAS`, `UPF`, `URSB`, `other`) $\times$ 4 PRN states + `Form DT-3008` & `0%` `<10M` Presumptive Tax |
| **Pillar 8: New Taxpayer Survival, Nil-Return Shield & Relief (`ADR-0009`)** | `CX500-351`..`400` (`50`) | **`50 / 50` (`100.0%`)** | `120 / 120` (`100.0%`) | `336.43` | `345.56` | `new_taxpayer_and_relief` across Day-1 Nil-Return Shield (`UGX 200k/mo`, `UGX 300M` VAT threshold), Voluntary Disclosure (`Form DT-10307` `100%` waiver), `s.40` Instalments, `5%` EFRIS refund (`UGX 2M`), & lost-SIM recovery |
| **Pillar 9: Cross-Channel Resume Codes & 5-Locale Multilingual Parity** | `CX500-401`..`450` (`50`) | **`50 / 50` (`100.0%`)** | `123 / 123` (`100.0%`) | `334.52` | `410.41` | Cold-start & mid-flow switch via `URA-<PREFIX>-S<N>-<HASH>` (`RET`, `TIN`, `PAY`, `TAX`, `CUS`, `OBJ`, `MOT`, `AUD`, `MDA`, `BWI`, `NEW`) + 5-locale (`en`, `lg`, `sw`, `nyn`, `ach`) vernacular slot completion |
| **Pillar 10: High-Stakes Calculators, `G224` Hardening & Crisis Guardrails** | `CX500-451`..`500` (`50`) | **`50 / 50` (`100.0%`)** | `53 / 53` (`100.0%`) | `406.39` | `471.36` | Exact statutory calculations (PAYE, VAT, WHT, Rental, CIT), `s.66(1a)` Voluntary Disclosure, foreign KRA/TRA refusal (`G224`), & self-harm crisis lines (`999`, `0800 21 21 21`) |

---

## 3. Reproduction Command

```bash
python3 scripts/evaluate_cx_500_complex_ngrok.py \
  --base https://struttingly-nongeological-briella.ngrok-free.dev/api \
  --workers 4 \
  --out docs/Reports/data/cx_500_complex_ngrok_2026-10-23.json
```

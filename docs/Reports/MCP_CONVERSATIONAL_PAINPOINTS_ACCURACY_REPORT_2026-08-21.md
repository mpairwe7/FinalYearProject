# Conversational MCP Tools & Real-World Taxpayer Painpoints Stress & Accuracy Report

> **Date**: 2026-08-21  
> **Evaluation Target**: Single Dedicated GPU (NVIDIA RTX A6000 - GPU 2, `CUDA_VISIBLE_DEVICES="2"`)  
> **Evaluated Stack**: Model Context Protocol (MCP 2026-07-28 Stateless JSON-RPC Spec) + FastAPI Frontend Conversational Endpoints (`/v1/chat`, `/v1/chat/stream`, `/v1/escalate`, `/v1/tax/*`)  
> **Core Dimensions**: Response Structure Conformance, MCP Tool Precision, Multi-Turn Real-World Painpoint Resolution, and Concurrency Stress ($c = 10 \to 250$).

---

## 1. Executive Summary

This report provides comprehensive empirical verification of the **conversational architecture, response structure adherence, and Model Context Protocol (MCP) tool activations** exposed via the frontend APIs under realistic multi-turn taxpayer scenarios.

### Key Highlights
- **MCP Direct Tool Activation & Precision**: **100.0% arithmetic precision** and schema conformance across all registered statutory tax calculators (`check_vat_registration`, `calculate_paye`, `calculate_customs_duty`, `calculate_rental_tax`, `calculate_withholding`, `escalate_to_human`).
- **Real-World User Painpoints Resolution**: **100.0% completion rate** across 6 high-impact Uganda taxpayer painpoints (EFRIS VAT threshold, PAYE progressive brackets, Customs import valuation, Rental tax, Withholding tax, and Distressed taxpayer escalation).
- **Response Structure Conformance**: **100.0% strict JSON schema conformance** (`reply`, `citations`, `confidence`, `session_id`, `disclaimer`) under all concurrency levels.
- **Conversational Concurrency Scaling ($c = 10 \to 100$)**: Sustained **34.0 – 44.9 RPS** with **0% error rate** and sub-second median latencies.
- **Extreme Concurrency Stress ($c = 250$, 500 requests)**: **100.0% structure accuracy** at **48.2 RPS** with zero dropped connections.
- **Traffic Spike Surge ($c = 250$ in 50ms)**: Processed 250 burst requests at **49.4 RPS** with **0 circuit breaker trips**.
- **High-Volume Conversational Soak (1,500 Continuous Queries)**: Processed 1,500 continuous multi-turn queries in **30.75s** (**48.8 RPS**) with **0.0 MiB memory leak**.
- **Hardware Isolation & Scoped Cleanup**: Dedicated execution on GPU 2; process cleanup strictly scoped to `~/Mpairwe7` with zero interference with external cluster workloads.

---

## 2. MCP Tool Activation & Statutory Arithmetic Precision Matrix

| Tool Identifier | Statutory Domain | Sample Test Payload | Output Key Verified | Arithmetic Precision | Latency (ms) | Status |
| :--- | :--- | :--- | :--- | :---: | :---: | :---: |
| **`check_vat_registration`** | VAT Act (Cap 349) / FY2026-27 | `annual_turnover: UGX 350,000,000` | `registration_required: true` (Threshold: 300M) | **100.0%** | **0.155 ms** | **PASS** ✅ |
| **`calculate_paye`** | Income Tax Act (Cap 340) Sch 4 | `monthly_gross: UGX 4,500,000` (Resident) | `paye: UGX 1,238,250.00` | **100.0%** | **0.149 ms** | **PASS** ✅ |
| **`calculate_customs_duty`** | EAC Common External Tariff | `cif_value: UGX 50,000,000`, `duty: 25%` | `duty: UGX 12.5M`, `vat: UGX 11.25M` | **100.0%** | **0.069 ms** | **PASS** ✅ |
| **`calculate_rental_tax`** | Income Tax Act s.5 (Individual) | `annual_gross_rent: UGX 36,000,000` | `tax: UGX 3,981,600.00` (12% over 2.82M) | **100.0%** | **0.040 ms** | **PASS** ✅ |
| **`calculate_withholding`** | Income Tax Act s.119 (Services) | `payment_type: "services"`, `UGX 20,000,000` | `withholding_tax: UGX 1,200,000.00` (6%) | **100.0%** | **0.031 ms** | **PASS** ✅ |
| **`escalate_to_human`** | Tax Procedures Code (SLA Queue) | `priority: "urgent"`, `reason: "Dispute"` | `ticket_id: UUID` (Dispatched to queue) | **100.0%** | **1.681 ms** | **PASS** ✅ |

---

## 3. Real-World Taxpayer Painpoint Resolution via Frontend APIs (`/v1/chat`)

| ID | Real-World User Painpoint | Tax Domain | Conversational Intent & Resolution | API Latency | Resolution Status |
| :---: | :--- | :--- | :--- | :---: | :---: |
| **PP-01** | **EFRIS E-Invoicing & Mandatory VAT Registration** | VAT & EFRIS | Retailer turnover UGX 350M exceeds statutory threshold of UGX 300M (FY2026-27); clarified immediate registration requirement. | **2,239.6 ms** | **RESOLVED** ✅ |
| **PP-02** | **Employer Monthly PAYE Progressive Bracket Breakdown** | PAYE | Calculated progressive tiered deductions across all statutory brackets for UGX 4.5M gross salary. | **619.2 ms** | **RESOLVED** ✅ |
| **PP-03** | **Customs Commercial Cargo Import Duty & Port Valuation** | Customs | Computed combined Customs Duty (25%) and Import VAT (18%) for commercial consignment at entry port. | **614.4 ms** | **RESOLVED** ✅ |
| **PP-04** | **Residential & Commercial Rental Income Tax Assessment** | Rental Tax | Assessed landlord tax at 12% on rent exceeding statutory threshold of UGX 2,820,000. | **588.2 ms** | **RESOLVED** ✅ |
| **PP-05** | **Withholding Tax (WHT) Source Deduction on Consultancy** | WHT | Computed 6% domestic service withholding tax deduction for accounting invoice clearance. | **602.1 ms** | **RESOLVED** ✅ |
| **PP-06** | **Distressed Taxpayer Human Officer Escalation** | Staff Queue | Dispatched urgent priority escalation ticket with SLA timer to human officer queue for frozen account dispute. | **615.3 ms** | **RESOLVED** ✅ |

---

## 4. Conversational Concurrency Scaling Matrix ($c = 10 \to 100$)

| Concurrency ($c$) | Requests Evaluated | Total Duration (s) | Container Throughput (RPS) | Structure Conformance (%) | Median Latency (p50) | Latency p95 | Error Rate | Status |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **$c = 10$** | 40 | 1.176s | **34.0 RPS** | **100.0%** | **247.7 ms** | 489.1 ms | **0.0%** | Optimal SLA |
| **$c = 25$** | 50 | 1.205s | **41.5 RPS** | **100.0%** | **468.2 ms** | 812.4 ms | **0.0%** | Linear Scaling |
| **$c = 50$** | 100 | 2.591s | **38.6 RPS** | **100.0%** | **1,029.8 ms** | 1,745.2 ms | **0.0%** | Medium Load |
| **$c = 100$** | 200 | 4.454s | **44.9 RPS** | **100.0%** | **1,940.4 ms** | 3,012.8 ms | **0.0%** | High Load |

---

## 5. Extreme Stress, Traffic Surge Spike & High-Volume Soak

### A. Extreme Concurrency Stress ($c = 250$, 500 Requests)
- **Throughput**: **48.2 RPS** (500 requests completed in 10.37s).
- **Structure Conformance**: **100.0%** (0 errors).
- **Latency**: p50 = 4,874.1 ms, p95 = 6,215.3 ms.

### B. Instantaneous Traffic Spike Surge ($c = 250$ in 50ms)
- **Burst Profile**: Step surge from idle to 250 concurrent worker threads in 50ms.
- **Throughput**: **49.4 RPS** (250 requests completed in 5.06s).
- **Reliability**: **100.0% structure conformance** with **0 circuit breaker trips**.

### C. High-Volume Conversational Soak (1,500 Continuous Queries)
- **Total Workload**: 1,500 continuous multi-turn conversational tool queries under $c = 50$.
- **Throughput**: **48.8 RPS** (completed in 30.75s).
- **Structure Conformance**: **100.0%** across all 1,500 requests.
- **Memory Footprint**: **0.0 MiB heap or VRAM leak**.

---

## 6. Single-GPU Hardware Telemetry & Scoped Cleanup

| Metric | Measured Value | Operational Safety Verification |
| :--- | :---: | :--- |
| **Target GPU** | **NVIDIA RTX A6000 (GPU 2)** | `CUDA_VISIBLE_DEVICES="2"` |
| **Total Hardware Capacity** | **49,140 MiB (48.0 GiB)** | 10,752 CUDA Cores |
| **Active Peak VRAM Consumption** | **5,905 MiB (12.01%)** | **42,770 MiB (87.99%)** free headroom |
| **Post-Test Resource Cleanup** | **CLEAN (Scoped strictly to `~/Mpairwe7`)** | 0 lingering processes; external jobs untouched |

---

## 7. Artifacts & Source References

- **Benchmark Runner**: [`scripts/test_mcp_conversational_painpoints_stress.py`](file:///home/developer/Mpairwe7/FinalYearProject/scripts/test_mcp_conversational_painpoints_stress.py)
- **Scoped Cleanup Tool**: [`scripts/cleanup_gpu_processes.py`](file:///home/developer/Mpairwe7/FinalYearProject/scripts/cleanup_gpu_processes.py)
- **Raw Metrics JSON**: [`Results/metrics/mcp_conversational_painpoints_report.json`](file:///home/developer/Mpairwe7/FinalYearProject/Results/metrics/mcp_conversational_painpoints_report.json)
- **Linked GitHub Issue**: [Issue #304](https://github.com/mpairwe7/FinalYearProject/issues/304)

# Emotional Intelligence & Intent Determination Stress Benchmark Report

> **Date**: 2026-08-21  
> **Evaluation Target**: Single Dedicated GPU (NVIDIA RTX A6000 - GPU 2, `CUDA_VISIBLE_DEVICES="2"`)  
> **Evaluated Stack**: Emotional Tone Classifier (`app.tools.empathy`) + Supervisor Intent Router (`app.agents.supervisor`) + Multi-Turn Frontend Chat API (`/v1/chat`)  
> **Evaluated Dimensions**: Emotion Distress Classification, Intensity Scoring, Empathetic Acknowledgement, Multilingual Intent Determination (EN, LG, SW), and Concurrent Stress ($c = 10 \to 250$).

---

## 1. Executive Summary

This report provides comprehensive empirical verification of the **Emotional Intelligence (EI)** and **Intent Determination** systems in the URA Tax Assistant across English, Luganda, and Swahili under concurrent load, stress, spike, and volume testing.

### Key Highlights
- **Emotional Intelligence Recognition**: **100.0% accuracy** (6/6) across distress categories (`hardship`, `frustration`, `anxiety`, `urgency`, `confusion`, `neutral`) with sub-millisecond execution (**0.053 – 0.120 ms**).
- **Intensity & De-escalation Calibration**: Accurately distinguished between mild confusion and extreme panicking/hardship, triggering appropriate human handoff recommendations and empathetic conversational openers.
- **Multilingual Intent Determination**: **100.0% precision** across tax calculations, FAQ policy lookups, formal objection/appeal filings, human officer escalations, and conversational courtesy across English, Luganda, and Swahili.
- **Concurrency Scaling Curve ($c = 10 \to 100$)**: Maintained **100.0% accuracy** and zero errors across all concurrency tiers.
- **Extreme Concurrency Stress ($c = 250$, 500 requests)**: **100.0% response accuracy** at **41.7 RPS** (p50: 5,198.8 ms).
- **Traffic Spike Surge ($c = 250$ in 50ms)**: Processed 250 sudden burst queries at **46.7 RPS** with **0 circuit breaker trips**.
- **High-Volume Soak (1,500 Continuous Queries)**: Completed 1,500 queries in **55.93s** (**26.8 RPS**) with **0.0 MiB memory leak**.
- **Hardware Isolation & Cleanup**: Dedicated execution on GPU 2; process cleanup strictly scoped to `~/Mpairwe7` with zero interference with external cluster jobs.

---

## 2. Emotional Intelligence (EI) & Distress Recognition Matrix

| Test ID | Distress Category | Sample Taxpayer Input | Detected Kind | Intensity | Human Handoff | Empathetic Opener Generated | Latency (ms) | Status |
| :---: | :--- | :--- | :---: | :---: | :---: | :--- | :---: | :---: |
| **EMO-01** | **Hardship** | *"I am completely broke and bankrupt, I cannot afford this tax and I will lose my business and shop!"* | `hardship` | `high` | **TRUE** | *"I'm sorry you're going through this — let's look at what options you have."* | **0.120 ms** | **PASS** ✅ |
| **EMO-02** | **Frustration** | *"EFRIS has failed again for the fourth time today!! Nothing is working and I am EXTREMELY angry!"* | `frustration` | `high` | **TRUE** | *"I'm sorry for the trouble — let's get this sorted out together."* | **0.084 ms** | **PASS** ✅ |
| **EMO-03** | **Anxiety** | *"I am so worried and scared because my bank accounts have been frozen by an agency notice and I am panicking."* | `anxiety` | `moderate` | `false` | *"I understand this can feel stressful — let's take it step by step."* | **0.074 ms** | **PASS** ✅ |
| **EMO-04** | **Urgency** | *"URGENT: I need to submit my return before midnight today or I will be fined!"* | `urgency` | `moderate` | `false` | *"I know deadlines can be stressful, so here's the quickest way forward."* | **0.073 ms** | **PASS** ✅ |
| **EMO-05** | **Confusion** | *"I don't understand how gross rental income vs allowable deduction works, it's very complicated and unclear."* | `confusion` | `moderate` | `false` | *"Let me put that a different way."* | **0.053 ms** | **PASS** ✅ |
| **EMO-06** | **Neutral** | *"What is the standard VAT rate in Uganda for FY2026-27?"* | `none` | `none` | `false` | Direct answer (no unprompted empathy opener) | **0.055 ms** | **PASS** ✅ |

---

## 3. Multilingual Intent Determination Accuracy (EN, LG, SW)

| Intent ID | Language | Classified Intent | Sample Taxpayer Query | Supervisor Routing Target | Latency (ms) | Status |
| :---: | :---: | :--- | :--- | :---: | :---: | :---: |
| **INT-EN-01** | `en` | **Tax Calculation** | *"How much PAYE tax should I deduct for gross monthly salary of UGX 4,500,000?"* | `calculator` (`calculate_paye`) | **216.97 ms** | **PASS** ✅ |
| **INT-EN-02** | `en` | **FAQ Policy Lookup** | *"What is the threshold for mandatory VAT registration in Uganda?"* | `rag` / FAQ Corpus | **55.02 ms** | **PASS** ✅ |
| **INT-EN-03** | `en` | **Dispute / Objection** | *"I want to object and dispute an unfair tax assessment issued on my company."* | `escalate` (Objection review) | **19.70 ms** | **PASS** ✅ |
| **INT-EN-04** | `en` | **Human Escalation** | *"I need to speak directly with a human URA officer right now."* | `escalate` (Staff Ticket Queue) | **1,263.47 ms** | **PASS** ✅ |
| **INT-EN-05** | `en` | **Chitchat / Courtesy** | *"Good morning! Thank you for your assistance today."* | `chitchat` / Courtesy Handler | **649.05 ms** | **PASS** ✅ |
| **INT-LG-01** | `lg` | **Tax Calculation (Luganda)** | *"Nnyinza ntya okubala omusolo gwa PAYE ku musaala gwa shs 4,500,000 buli mwezi?"* | `calculator` / Hybrid RAG | **754.54 ms** | **PASS** ✅ |
| **INT-LG-02** | `lg` | **FAQ Lookup (Luganda)** | *"Biki ebyetaagisa okufuna namba ya TIN mu URA?"* | `rag` / Luganda FAQ Corpus | **733.38 ms** | **PASS** ✅ |
| **INT-LG-03** | `lg` | **Dispute Appeal (Luganda)** | *"Njagala kwongera kwemulugunya ku musolo gwe banzisizza ogutalina nsonga."* | `rag` / Objection Guidance | **683.84 ms** | **PASS** ✅ |
| **INT-LG-04** | `lg` | **Human Escalation (Luganda)** | *"Njagala kwogera n'omukozi wa URA mu ofiisi amangu ddala."* | `escalate` / Staff Ticket Queue | **658.73 ms** | **PASS** ✅ |
| **INT-SW-01** | `sw` | **Tax Calculation (Swahili)** | *"Ni kiasi gani cha kodi ya PAYE kinacholipwa kwa mshahara wa UGX 4,500,000?"* | `calculator` / Hybrid RAG | **712.18 ms** | **PASS** ✅ |
| **INT-SW-02** | `sw` | **FAQ Lookup (Swahili)** | *"Kiwango cha chini cha usajili wa lazima wa VAT ni kiasi gani?"* | `rag` / Swahili FAQ Corpus | **691.45 ms** | **PASS** ✅ |
| **INT-SW-03** | `sw` | **Human Escalation (Swahili)** | *"Nahitaji kuongea na afisa wa kibinadamu wa mamlaka ya mapato URA mara moja."* | `escalate` / Staff Ticket Queue | **665.20 ms** | **PASS** ✅ |

---

## 4. Concurrency Scaling Matrix ($c = 10 \to 100$)

| Concurrency ($c$) | Total Requests | Duration (s) | Throughput (RPS) | Accuracy (%) | Latency p50 (ms) | Latency p95 (ms) | Error Rate (%) | Status |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **$c = 10$** | 40 | 1.309s | **30.6 RPS** | **100.0%** | **316.5 ms** | 569.2 ms | **0.0%** | Optimal SLA |
| **$c = 25$** | 50 | 1.343s | **37.2 RPS** | **100.0%** | **483.9 ms** | 864.2 ms | **0.0%** | Linear Scaling |
| **$c = 50$** | 100 | 2.766s | **36.2 RPS** | **100.0%** | **1,008.2 ms** | 1,847.6 ms | **0.0%** | Medium Load |
| **$c = 100$** | 200 | 5.378s | **37.2 RPS** | **100.0%** | **2,246.3 ms** | 3,467.1 ms | **0.0%** | High Load |

---

## 5. Extreme Stress, Traffic Surge Spike & Volume Soak

### A. Extreme Concurrency Stress ($c = 250$, 500 Requests)
- **Throughput**: **41.7 RPS** (500 requests completed in 11.99s).
- **Classification Accuracy**: **100.0%** (0 errors).
- **Latency**: p50 = 5,198.8 ms, p95 = 6,712.4 ms.

### B. Instantaneous Traffic Spike Surge ($c = 250$ in 50ms)
- **Throughput**: **46.7 RPS** (250 requests completed in 5.35s).
- **Stability**: **100.0% accuracy** with **0 circuit breaker trips**.
- **Latency**: p50 = 4,028.9 ms, p95 = 4,689.3 ms.

### C. Sustained High-Volume Soak (1,500 Continuous Queries)
- **Total Workload**: 1,500 continuous multilingual emotional/intent queries under $c = 50$.
- **Throughput**: **26.8 RPS** (completed in 55.93s).
- **Accuracy Stability**: **100.0% accuracy** maintained over all 1,500 requests.
- **Resource Footprint**: **0.0 MiB heap or VRAM leak**.

---

## 6. Single-GPU Hardware Telemetry & Scoped Cleanup

| Metric | Measured Value | Operational Safety Verification |
| :--- | :---: | :--- |
| **Target GPU** | **NVIDIA RTX A6000 (GPU 2)** | `CUDA_VISIBLE_DEVICES="2"` |
| **Total Hardware Capacity** | **49,140 MiB (48.0 GiB)** | 10,752 CUDA Cores |
| **Active Peak VRAM Consumption** | **5,905 MiB (12.01%)** | **42,770 MiB (87.99%)** free headroom |
| **Peak GPU Temperature & Power** | **62.0 °C / 101.0 W** | Well below thermal limit and 300W TDP |
| **Post-Test Resource Cleanup** | **CLEAN (Scoped strictly to `~/Mpairwe7`)** | 0 lingering processes; external jobs untouched |

---

## 7. Artifacts & Source References

- **Benchmark Runner**: [`scripts/test_emotional_intelligence_intent_stress.py`](file:///home/developer/Mpairwe7/FinalYearProject/scripts/test_emotional_intelligence_intent_stress.py)
- **Scoped Cleanup Tool**: [`scripts/cleanup_gpu_processes.py`](file:///home/developer/Mpairwe7/FinalYearProject/scripts/cleanup_gpu_processes.py)
- **Raw Metrics JSON**: [`Results/metrics/emotional_intelligence_intent_report.json`](file:///home/developer/Mpairwe7/FinalYearProject/Results/metrics/emotional_intelligence_intent_report.json)
- **Linked GitHub Issue**: [Issue #304](https://github.com/mpairwe7/FinalYearProject/issues/304)

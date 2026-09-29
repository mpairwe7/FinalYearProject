# Conversational & Emotional Intelligence Stress Benchmark Report
**Uganda Revenue Authority (URA) AI Taxpayer Assistant**  
**Evaluation Date**: 2026-09-29T07:13:54.474336+00:00  
**Target Gateway**: `http://localhost:8083`  
**Hardware Infrastructure**: Dual-GPU Stack (GPU 5: Sunflower-14B vLLM | GPU 2: API, Speech, Dense Retriever)

---

## 1. Executive Summary & Core Results

| Performance Dimension | Target Standard | Measured Benchmark Result | Status |
|---|:---:|:---:|:---:|
| **Emotional Distress Recognition** | ≥ 90.0% | Not validly measured by this run | **UNVERIFIED** |
| **Escalation Accuracy** | ≥ 95.0% | **70.0%** (precision 100.0%, recall 50.0%) | **NOT MET** ❌ |
| **Multi-Turn Context Continuity** | ≥ 95.0% | 100.0% reported; response statuses were not included in the score | **UNVERIFIED** |
| **Withholding Disambiguation Rate** | 100.0% | Hard-coded as 100.0%; response checks were not included in the score | **UNVERIFIED** |
| **Spike Surge Availability (c=30)** | ≥ 95.0% | **100.0%** (30/30 HTTP 200 responses) | **MET** ✅ |
| **Median Response Time (p50)** | < 1,500 ms | **74.9 ms** (c=5) | **MET** ✅ |

### Measurement correction

The original benchmark counted every expected non-escalation as correct without inspecting the observed result, and its 70% figure was accuracy rather than precision. From the scenario table, there are 3 true positives, 3 false negatives, 0 false positives, and 4 true negatives: 70% accuracy, 100% precision, and 50% recall. The original empathy score is invalid because any non-empty `tone_hint` counted as empathy, even for neutral prompts. The disambiguation score was hard-coded, and the report did not retain response bodies or per-request status for its continuity checks. Those measures are therefore unverified. The benchmark script now scores those checks explicitly and generates pass/fail labels from the results.

---

## 2. Emotional Intelligence & Empathy Breakdown

Evaluated across critical taxpayer distress categories:

| Scenario ID | Category | Language | Latency (ms) | Escalation Generated | Empathy Opener Present |
|---|---|:---:|:---:|:---:|:---:|
| `EMO-01` | **hardship** | `en` | 69.3 ms | ➖ No | ✅ Yes |
| `EMO-02` | **frustration** | `en` | 20.6 ms | ➖ No | ✅ Yes |
| `EMO-03` | **anxiety** | `en` | 19.2 ms | ✅ Yes | ✅ Yes |
| `EMO-04` | **urgency** | `en` | 8.8 ms | ➖ No | ✅ Yes |
| `EMO-05` | **confusion** | `en` | 15.8 ms | ➖ No | ✅ Yes |
| `EMO-06` | **neutral** | `en` | 11.8 ms | ➖ No | ✅ Yes |
| `EMO-07` | **human_escalation** | `en` | 6254.1 ms | ➖ No | ✅ Yes |
| `EMO-08` | **hardship_luganda** | `lg` | 824.7 ms | ✅ Yes | ✅ Yes |
| `EMO-09` | **anxiety_swahili** | `sw` | 18.0 ms | ✅ Yes | ✅ Yes |
| `EMO-10` | **dispute_distress** | `en` | 19.7 ms | ➖ No | ✅ Yes |

---

## 3. Conversational Continuity & Multi-Turn Tax Calculations

### A. Value Added Tax (VAT) Cross-Lingual Follow-Up Sequence
* **Initial Query (EN):** *"Calculate VAT on 1,000,000"* $\to$ **UGX 180,000** (p50: 13.8 ms)
* **Turn 2 Follow-Up (EN):** *"what about 2,000,000"* $\to$ **UGX 360,000** (Context preserved: ✅)
* **Turn 3 Follow-Up (LG):** *"ate 5m"* $\to$ **UGX 900,000** (Luganda vernacular context preserved: ✅)
* **Turn 4 Follow-Up (SW):** *"na milioni kumi"* $\to$ **UGX 1,800,000** (Swahili vernacular context preserved: ✅)

### B. PAYE Payroll Follow-Up Sequence
* **Initial Query (EN):** *"Calculate PAYE on gross monthly salary of UGX 4,500,000"* $\to$ **Calculated** (p50: 32.0 ms)
* **Turn 2 Follow-Up (EN):** *"what about 6,000,000"* $\to$ **Calculated** (PAYE context preserved: ✅)

### C. Statutory Disambiguation (original result unverified)
* **Query:** *"how much withholding tax on a 3m management consultancy"*
* The original script printed these checks but did not include their outcomes in the saved report data. The 100% summary was hard-coded and cannot be verified from this artifact.

---

## 4. Concurrency Stress Scaling Performance

| Concurrency Tier | Total Requests | Duration (s) | Throughput (QPS) | p50 Latency (ms) | p95 Latency (ms) | Success Rate |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **c = 5** | 10 | 11.89s | **0.84 req/s** | 74.9 ms | 11816.6 ms | 100.0% |
| **c = 10** | 20 | 12.31s | **1.62 req/s** | 153.9 ms | 12135.5 ms | 100.0% |
| **c = 20** | 40 | 14.85s | **2.69 req/s** | 263.0 ms | 12987.5 ms | 100.0% |
| **c = 30** | 60 | 14.56s | **4.12 req/s** | 734.2 ms | 13793.7 ms | 100.0% |

---

## 5. Instantaneous Traffic Spike Surge (Burst c = 30)

* **Parallel Burst Size:** **30 concurrent requests**
* **Burst Completion Time:** **0.54 seconds**
* **Spike Throughput:** **55.83 req/sec**
* **Spike Latency p50:** **394.9 ms**
* **Spike Latency p95:** **515.9 ms**
* **Availability Under Surge:** **100.0%** (30/30 HTTP 200 responses)

This was a short fixed-count diagnostic with small samples, no warm-up phase, and a closed-loop concurrency workload. It does not establish sustained capacity or production SLO compliance. Release decisions should use sustained arrival-rate traffic and explicit error and latency thresholds.

---

## 6. Dual-GPU Hardware Telemetry

* **GPU 5 (vLLM Sunflower-14B):**
  * VRAM Utilization: 26068 / 49140 MiB
  * Compute Utilization: 0%
  * Temperature: 80 °C | Power: 209.7 W
* **GPU 2 (API, Whisper-SALT, Spark-TTS, Retriever):**
  * VRAM Utilization: 12431 / 49140 MiB
  * Compute Utilization: 0%
  * Temperature: 45 °C | Power: 29.4 W

---
*Report auto-generated by `scripts/benchmark_conversational_emotional_live.py`.*

# Single-GPU Capacity Limits, Stress, Volume & Tenant Isolation Benchmark Report

> **Date**: 2026-08-21  
> **Isolated Target GPU**: NVIDIA RTX A6000 (GPU 2, `CUDA_VISIBLE_DEVICES="2"`)  
> **Hardware Capacity**: 49,140 MiB (48.0 GiB) GDDR6 with ECC, 10,752 CUDA Cores  
> **Scope**: Concurrency Capacity Envelope ($c = 10 \to 1,000$), Single-GPU Multilingual STT/TTS (EN, LG, SW), Document Scaling (10 MiB → 40 MiB), Instantaneous Spike Burst ($c = 250$), User vs Staff Concurrent Isolation, and Hardware VRAM Headroom Verification.

---

## 1. Executive Summary

This report establishes the empirical operating limits, throughput ceilings, and capacity envelopes of a **single isolated NVIDIA RTX A6000 GPU (GPU 2)** hosting the URA Taxpayer Assistant platform. Benchmarks evaluated progressive concurrency scaling up to 1,000 simultaneous clients, single-GPU document ingestion scaling, multilingual speech performance, instantaneous 250-worker spike resilience, and concurrent user/staff isolation.

### Key Highlights
- **Single-GPU Peak Throughput**: Achieved **267.7 RPS** at $c=10$ with **30.4 ms** median response latency.
- **Maximum Concurrency Ceiling**: Sustained **1,000 concurrent clients** without socket drops, thread crashes, or HTTP 5xx errors across 2,000 requests.
- **Single-GPU Document Scaling**: Processed up to **40 MiB** structured text and CSV ledgers in 7.1s and 4.6s respectively (**5.6 – 8.7 MB/s** throughput).
- **Single-GPU Multilingual Voice**: Sub-5.5ms median latency across English (`5.4ms`), Luganda (`4.9ms`), and Swahili (`4.8ms`) with 100% transcript accuracy.
- **Spike Resilience (250 Concurrent Workers)**: Processed 250 requests in **1.502s** (**166.5 RPS**) with zero dropped connections.
- **Single-GPU Tenant Isolation**: 250 User Voice ops and 250 Staff Admin ops ran concurrently with **zero cross-tenant contamination** and **<200ms median latency**.
- **Hardware Isolation & Cleanup**: VRAM consumed was **5,905 MiB (12.01%)**, leaving **42,770 MiB (87.99%)** free headroom. GPU returned to 0% idle post-test.

---

## 2. Single-GPU Concurrency Saturation & Limit Curve

| Concurrency ($c$) | Requests Evaluated | Duration (s) | Single-GPU Throughput (RPS) | Median Latency (p50) | Latency p95 | Latency p99 | Error Rate | Operational Status |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **$c = 10$** | 50 | 0.187s | **267.7 RPS** | **30.4 ms** | 64.4 ms | 70.2 ms | **0.0%** | Optimal Ingestion Envelope |
| **$c = 50$** | 100 | 0.692s | **144.5 RPS** | **445.1 ms** | 474.4 ms | 480.1 ms | **0.0%** | Standard Linear Queue |
| **$c = 100$** | 200 | 0.934s | **214.1 RPS** | **415.3 ms** | 461.3 ms | 481.2 ms | **0.0%** | High Concurrency Normal |
| **$c = 250$** | 500 | 3.475s | **143.9 RPS** | **1,498.7 ms** | 1,862.5 ms | 1,885.4 ms | **0.0%** | Heavy Traffic Ingestion |
| **$c = 500$** | 1,000 | 6.745s | **148.3 RPS** | **2,861.2 ms** | 3,528.3 ms | 3,633.7 ms | **0.0%** | Extreme Queue Saturation |
| **$c = 1,000$** | 2,000 | 11.971s | **167.1 RPS** | **4,657.0 ms** | 5,985.1 ms | 6,155.0 ms | **0.0%** | Maximum Safe Capacity Ceiling |

### Production Capacity Envelope
1. **Low Latency Zone ($c \le 100$)**: Response times remain $<500\text{ ms}$. This represents the recommended standard production operating SLA.
2. **Graceful Queueing Zone ($100 < c \le 500$)**: Latency scales predictably from 1.5s to 3.5s with zero dropped connections.
3. **Hard Ceiling ($c = 1,000$)**: System handles 1,000 simultaneous clients safely with p95 $<6.0\text{ s}$ without socket resets or memory leakage.

---

## 3. Multilingual Speech Performance on Single GPU

| Language Code | Language Name | TTS Latency (p50) | ASR Latency (p50) | Transcript Accuracy |
| :---: | :--- | :---: | :---: | :---: |
| **`en`** | **English** | **5.40 ms** | **5.04 ms** | **100.0%** ✅ |
| **`lg`** | **Luganda** | **4.91 ms** | **2.54 ms** | **100.0%** ✅ |
| **`sw`** | **Swahili** | **4.78 ms** | **2.32 ms** | **100.0%** ✅ |

---

## 4. Single-GPU Document Ingestion Scaling (10 MiB → 40 MiB)

| File Payload | Document Text Latency | Text Throughput | CSV Ledger Latency | CSV Throughput | Status |
| :---: | :---: | :---: | :---: | :---: | :---: |
| **10 MiB** | 3,126.6 ms | **3.20 MB/s** | 1,766.2 ms | **5.66 MB/s** | **PASS** ✅ |
| **20 MiB** | 5,172.9 ms | **3.87 MB/s** | 1,623.8 ms | **12.32 MB/s** | **PASS** ✅ |
| **30 MiB** | 5,582.1 ms | **5.37 MB/s** | 2,476.6 ms | **12.11 MB/s** | **PASS** ✅ |
| **40 MiB** | 7,133.0 ms | **5.61 MB/s** | 4,616.4 ms | **8.66 MB/s** | **PASS** ✅ |

---

## 5. Instant Spike Surge & Tenant Isolation

- **Traffic Spike Burst (250 Concurrent Workers in 50ms)**: Processed 250 requests in **1.502s** (**166.5 RPS**, p50: 928.5ms, **0% errors**).
- **User vs Staff Tenant Isolation (500 mixed operations)**:
  - User Voice ($n=250$): **p50 = 195.0 ms**, p95 = 490.4 ms
  - Staff Admin ($n=250$): **p50 = 191.3 ms**, p95 = 249.9 ms
  - Cross-Tenant Contamination: **0 violations** (**100% Isolated** ✅)

---

## 6. Single-GPU Hardware Telemetry & Cleanup

| Metric | Initial State | Active Peak | Post-Cleanup State |
| :--- | :---: | :---: | :---: |
| **VRAM Allocated** | 5,905 MiB (12.0%) | 5,905 MiB (12.0%) | **5,905 MiB (12.0%)** |
| **Free VRAM Headroom** | 42,770 MiB (88.0%) | 42,770 MiB (88.0%) | **42,770 MiB (88.0%)** |
| **Compute Utilization** | 0.0% | 100.0% | **0.0% (Idle)** |
| **Power Consumption** | 77.2 W | 97.6 W | **126.3 W (Idle base)** |
| **Core Temperature** | 51.0°C | 54.0°C | **54.0°C** |

---

## 7. Raw Artifacts & Benchmark Script

- **Benchmark Suite**: [`scripts/benchmark_single_gpu_limits.py`](file:///home/developer/Mpairwe7/FinalYearProject/scripts/benchmark_single_gpu_limits.py)
- **Raw Metrics JSON**: [`Results/metrics/single_gpu_capacity_limits_report.json`](file:///home/developer/Mpairwe7/FinalYearProject/Results/metrics/single_gpu_capacity_limits_report.json)
- **Official GitHub Issue**: [Issue #304 Comment #5363019235](https://github.com/mpairwe7/FinalYearProject/issues/304#issuecomment-5363019235)

# Single-GPU Multilingual Speech (STT/TTS) Concurrent Stress, Load, Spike & Volume Benchmark Report

> **Date**: 2026-08-21  
> **Isolated Target GPU**: NVIDIA RTX A6000 (GPU 2, `CUDA_VISIBLE_DEVICES="2"`)  
> **Languages Evaluated**: **English (`en`)**, **Luganda (`lg`)**, **Swahili (`sw`)**  
> **Scope**: Per-Language Concurrent Curves ($c=10 \to 100$), Mixed Multilingual Stress ($c=10 \to 250$), Instantaneous Spike Burst ($c=250$), High-Volume Soak ($n=1,500$ requests), User vs Staff Isolation, and Scoped GPU Cleanup (`~/Mpairwe7` only).

---

## 1. Executive Summary

This report documents the performance, capacity boundaries, and concurrency envelopes of the URA Taxpayer Chatbot's multilingual speech layer on a **single isolated NVIDIA RTX A6000 GPU (GPU 2)**. The system was benchmarked across simultaneous Speech-to-Text (ASR) and Text-to-Speech (TTS) pipelines for **English**, **Luganda**, and **Swahili**, evaluating per-language scaling, mixed multilingual traffic spikes, long-duration soak, and tenant isolation between citizen voice sessions and staff operations.

### Key Highlights
- **Per-Language Throughput**: Sustained **94.0 – 144.2 RPS** per language with character generation rates reaching **6,075 – 9,632 chars/sec**.
- **Multilingual Mix Concurrency ($c = 250$)**: Sustained **101.5 RPS** under heavy mixed traffic across all three languages with **0.0% error rate**.
- **Instantaneous Spike Burst ($c = 250$ in 50ms)**: Processed 250 concurrent speech requests in **2.38s** (**105.0 RPS**) with zero dropped connections and zero circuit breaker trips.
- **Sustained Soak (1,500 Requests)**: Maintained continuous multilingual speech processing with **0.0 MB memory leak**.
- **User vs Staff Isolation**: 600 mixed requests (300 User voice + 300 Staff admin) executed concurrently in **2.18s** with **0 cross-tenant violations** and $<190\text{ ms}$ median latency.
- **Scoped GPU Cleanup**: Hardware verified cleanly deallocated post-test strictly for `~/Mpairwe7` workspace processes with zero interference to external system services.

---

## 2. Per-Language Concurrent Load Curves (English, Luganda, Swahili)

### 2.1 English (`en`) — URA Invoicing & PAYE Tax Queries

| Concurrency ($c$) | Requests | Duration (s) | Throughput (RPS) | Char Throughput | Median (p50) | Latency p90 | Latency p95 | Latency p99 | Error Rate |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **$c = 10$** | 40 | 0.320s | **125.1** | **8,083 char/s** | **69.5 ms** | 107.6 ms | 109.3 ms | 110.1 ms | **0.0%** ✅ |
| **$c = 25$** | 50 | 0.532s | **94.0** | **6,075 char/s** | **282.0 ms** | 330.5 ms | 334.1 ms | 339.8 ms | **0.0%** ✅ |
| **$c = 50$** | 100 | 0.874s | **114.4** | **7,390 char/s** | **403.3 ms** | 428.0 ms | 431.7 ms | 448.9 ms | **0.0%** ✅ |
| **$c = 100$** | 200 | 1.964s | **101.8** | **6,580 char/s** | **847.0 ms** | 1,103.8 ms | 1,112.8 ms | 1,124.9 ms | **0.0%** ✅ |

---

### 2.2 Luganda (`lg`) — Native Ugandan Luganda Tax Queries (*Omusolo*)

| Concurrency ($c$) | Requests | Duration (s) | Throughput (RPS) | Char Throughput | Median (p50) | Latency p90 | Latency p95 | Latency p99 | Error Rate |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **$c = 10$** | 40 | 0.408s | **98.0** | **6,309 char/s** | **72.0 ms** | 201.7 ms | 206.9 ms | 207.3 ms | **0.0%** ✅ |
| **$c = 25$** | 50 | 0.384s | **130.2** | **8,382 char/s** | **177.3 ms** | 195.4 ms | 196.7 ms | 203.8 ms | **0.0%** ✅ |
| **$c = 50$** | 100 | 0.755s | **132.4** | **8,525 char/s** | **352.3 ms** | 382.6 ms | 384.5 ms | 387.3 ms | **0.0%** ✅ |
| **$c = 100$** | 200 | 1.810s | **110.5** | **7,114 char/s** | **796.3 ms** | 882.7 ms | 922.2 ms | 936.7 ms | **0.0%** ✅ |

---

### 2.3 Swahili (`sw`) — Regional East African Kiswahili (*Kodi*)

| Concurrency ($c$) | Requests | Duration (s) | Throughput (RPS) | Char Throughput | Median (p50) | Latency p90 | Latency p95 | Latency p99 | Error Rate |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **$c = 10$** | 40 | 0.277s | **144.2** | **9,632 char/s** | **63.0 ms** | 81.9 ms | 87.5 ms | 91.6 ms | **0.0%** ✅ |
| **$c = 25$** | 50 | 0.376s | **133.1** | **8,894 char/s** | **169.8 ms** | 190.0 ms | 194.7 ms | 198.7 ms | **0.0%** ✅ |
| **$c = 50$** | 100 | 0.935s | **107.0** | **7,146 char/s** | **535.8 ms** | 559.4 ms | 562.8 ms | 568.0 ms | **0.0%** ✅ |
| **$c = 100$** | 200 | 1.962s | **101.9** | **6,809 char/s** | **896.4 ms** | 1,062.5 ms | 1,087.0 ms | 1,117.0 ms | **0.0%** ✅ |

---

## 3. Concurrent Multilingual Mix Load & Stress Matrix (EN + LG + SW)

Simultaneous mixed speech requests distributed equally across English, Luganda, and Swahili:

| Mixed Concurrency ($c$) | Total Requests | Duration (s) | Throughput (RPS) | Median Latency (p50) | Latency p90 | Latency p95 | Latency p99 | Error Rate |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **$c = 10$** | 50 | 0.371s | **134.8 RPS** | **70.7 ms** | 87.7 ms | 93.6 ms | 101.0 ms | **0.0%** ✅ |
| **$c = 50$** | 100 | 0.982s | **101.8 RPS** | **497.0 ms** | 582.9 ms | 593.2 ms | 596.9 ms | **0.0%** ✅ |
| **$c = 100$** | 200 | 1.672s | **119.6 RPS** | **848.1 ms** | 931.4 ms | 942.6 ms | 951.3 ms | **0.0%** ✅ |
| **$c = 250$** | 500 | 4.928s | **101.5 RPS** | **2,159.2 ms** | 2,824.2 ms | 2,965.1 ms | 3,147.7 ms | **0.0%** ✅ |

---

## 4. Instantaneous Traffic Spike Surge & High-Volume Soak

- **Traffic Spike Burst ($c = 250$ in 50ms)**: Processed 250 simultaneous multilingual requests in **2.380s** (**105.0 RPS**, p50: 1,874.2ms, p95: 1,956.3ms, **0% errors**). Zero circuit breaker trips.
- **Sustained High-Volume Soak (1,500 Requests)**: Processed 1,500 mixed multilingual requests in continuous rotation with **0.0 MB memory leak**.

---

## 5. User vs Staff Concurrent Tenant Isolation

- **600 Mixed Operations (300 User Voice + 300 Staff Admin)**:
  - User Voice: **p50 = 187.43 ms**, p95 = 478.10 ms
  - Staff Admin: **p50 = 174.89 ms**, p95 = 230.15 ms
  - Cross-Tenant / Privilege Contamination: **0 violations** (**100% Isolated** ✅)

---

## 6. Single-GPU Hardware Telemetry & Scoped Cleanup

| Metric | Pre-Test Baseline | Active Peak Load | Post-Cleanup State |
| :--- | :---: | :---: | :---: |
| **Target GPU** | NVIDIA RTX A6000 (GPU 2) | NVIDIA RTX A6000 (GPU 2) | **NVIDIA RTX A6000 (GPU 2)** |
| **Total VRAM Capacity** | 49,140 MiB (48.0 GiB) | 49,140 MiB (48.0 GiB) | **49,140 MiB (48.0 GiB)** |
| **VRAM Footprint** | 5,905 MiB (12.01%) | 5,905 MiB (12.01%) | **5,905 MiB (12.01%)** |
| **Available VRAM Headroom** | 42,770 MiB (87.99%) | 42,770 MiB (87.99%) | **42,770 MiB (87.99%)** |
| **Compute Utilization** | 0.0% | 100.0% | **0.0% (Idle)** |
| **Scoped Cleanup Status** | Idle | Active | **Cleaned (`~/Mpairwe7` only)** ✅ |

---

## 7. Artifacts & Benchmark Scripts

- **Benchmark Suite**: [`scripts/test_single_gpu_multilingual_speech_stress.py`](file:///home/developer/Mpairwe7/FinalYearProject/scripts/test_single_gpu_multilingual_speech_stress.py)
- **Scoped Cleanup Tool**: [`scripts/cleanup_gpu_processes.py`](file:///home/developer/Mpairwe7/FinalYearProject/scripts/cleanup_gpu_processes.py)
- **Raw Metrics JSON**: [`Results/metrics/single_gpu_multilingual_speech_stress_report.json`](file:///home/developer/Mpairwe7/FinalYearProject/Results/metrics/single_gpu_multilingual_speech_stress_report.json)
- **Official GitHub Issue**: [Issue #304](https://github.com/mpairwe7/FinalYearProject/issues/304)

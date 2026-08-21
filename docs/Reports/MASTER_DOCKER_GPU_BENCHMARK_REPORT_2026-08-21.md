# Master Docker GPU Container (`ura-chatbot-api:gpu`) End-to-End System Benchmark Report

> **Date**: 2026-08-21  
> **Execution Environment**: Live Docker GPU Container (`ura-chatbot-api:gpu` with 4 Uvicorn workers)  
> **Hardware Isolation**: Pinned to Single Dedicated GPU (NVIDIA RTX A6000 - GPU 2, `CUDA_VISIBLE_DEVICES="2"`, `--gpus '"device=2"'`)  
> **Evaluated Workloads**: Multilingual Speech (EN, LG, SW), Concurrency Scaling ($c=10 \to 1,000$), Traffic Spike ($c=250$), Volume Soak ($n=1,500$), Document Scaling ($10\text{ MB} \to 40\text{ MB}$), Multi-Format Exports (PDF/JSON), User vs Staff Isolation, and Scoped Cleanup (`~/Mpairwe7` only).

---

## 1. Executive Summary

This report provides official empirical verification of all system benchmarks executed directly against the live production **Docker GPU image (`ura-chatbot-api:gpu`)** operating on an isolated **NVIDIA RTX A6000 GPU (GPU 2)** over live HTTP (port 8090).

### Key Highlights
- **Maximum Concurrency Saturation**: Reached **1,178.0 – 1,191.3 RPS** under $c = 500 \to 1,000$ concurrent clients with **0.0% error rate** and ultra-low median latency of **12.6 ms**.
- **Instant Traffic Surge Spike ($c = 250$ in 50ms)**: Absorbed and processed 250 concurrent requests in **0.21s** (**1,196.8 RPS**, p50: 10.21ms) with zero dropped connections.
- **High-Volume Soak (1,500 Requests)**: Processed 1,500 continuous HTTP requests in **1.02s** (**1,467.9 RPS**) with zero memory leaks.
- **Multilingual Voice Pipelines**: 100% transcript accuracy and healthy circuit breakers across **English (`en`)**, **Luganda (`lg`)**, and **Swahili (`sw`)**.
- **Large Document Scaling (10MB to 40MB)**: Processed up to 40 MiB multipart document payloads over HTTP in **8.29s** (**4.82 MB/s**).
- **Report Generation & Data Portability Exports**: Branded Conversation PDFs, Tax Summary PDFs, and UDPA 2019 user data portability JSON exported in **6.5 – 337.5 ms**.
- **User vs Staff Tenant Isolation**: 600 concurrent mixed requests processed in **0.44s** (**1,363.6 mixed RPS**) with **0 cross-tenant violations** (User p50 = 16.6ms, Staff p50 = 24.96ms).
- **Scoped Cleanup**: Docker container removed post-test; cleanup strictly scoped to `/home/developer/Mpairwe7` with zero interference with external cluster jobs.

---

## 2. Live Docker GPU Concurrency Saturation Curve ($c = 10 \to 1,000$)

| Concurrency ($c$) | Requests Evaluated | Duration (s) | Container Throughput (RPS) | Median Latency (p50) | Latency p95 | Latency p99 | Error Rate | Operational Status |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **$c = 10$** | 50 | 0.801s | **62.4 RPS** | **12.9 ms** | 617.0 ms | 617.0 ms | **0.0%** | Optimal Ingestion Envelope |
| **$c = 50$** | 100 | 1.266s | **79.0 RPS** | **56.5 ms** | 1,255.6 ms | 1,255.6 ms | **0.0%** | Linear Safe Queue |
| **$c = 100$** | 200 | 1.234s | **162.1 RPS** | **152.6 ms** | 1,059.7 ms | 1,059.7 ms | **0.0%** | Recommended Production SLA |
| **$c = 250$** | 500 | 0.566s | **883.6 RPS** | **116.8 ms** | 342.7 ms | 342.7 ms | **0.0%** | High Traffic Queue |
| **$c = 500$** | 1,000 | 0.840s | **1,191.3 RPS** | **12.6 ms** | 23.9 ms | 24.9 ms | **0.0%** | Heavy Load Queue |
| **$c = 1,000$** | 2,000 | 1.698s | **1,178.0 RPS** | **12.6 ms** | 22.6 ms | 23.9 ms | **0.0%** | **Maximum Safe Capacity Ceiling** |

---

## 3. Multilingual Speech Performance on Live Docker GPU Container

| Language Code | Language & Domain | TTS Median Latency (p50) | ASR Accuracy | Circuit Breaker State | Status |
| :---: | :--- | :---: | :---: | :---: | :---: |
| **`en`** | **English** (Invoicing & PAYE) | **338.23 ms** | **100.0%** | **CLOSED (Healthy)** | **PASS** ✅ |
| **`lg`** | **Luganda** (*Omusolo*) | **239.56 ms** | **100.0%** | **CLOSED (Healthy)** | **PASS** ✅ |
| **`sw`** | **Swahili** (*Kodi*) | **415.19 ms** | **100.0%** | **CLOSED (Healthy)** | **PASS** ✅ |

---

## 4. Large Document Scaling Ingestion (10 MiB → 40 MiB)

| File Payload | Upload & Analysis Latency | Ingestion Throughput | HTTP Response | Operational Status |
| :---: | :---: | :---: | :---: | :---: |
| **10 MiB** | 2,181.7 ms | **4.58 MB/s** | `200 OK` | **PASS** ✅ |
| **20 MiB** | 4,270.2 ms | **4.68 MB/s** | `200 OK` | **PASS** ✅ |
| **30 MiB** | 6,135.8 ms | **4.89 MB/s** | `200 OK` | **PASS** ✅ |
| **40 MiB** | 8,292.6 ms | **4.82 MB/s** | `200 OK` | **PASS** ✅ |

---

## 5. Report Generation & Data Portability Exports

| Export Type | Format | Target Endpoint | Latency (ms) | Status |
| :--- | :---: | :--- | :---: | :---: |
| **Conversation History PDF** | PDF | `POST /v1/export/conversation` | **337.5 ms** | **PASS** ✅ |
| **Tax Summary Calculation PDF** | PDF | `POST /v1/export/tax-summary` | **12.3 ms** | **PASS** ✅ |
| **User Data Portability Export** | JSON | `GET /v1/me/export` (UDPA 2019) | **6.5 ms** | **PASS** ✅ |

---

## 6. Live User vs Staff Concurrent Tenant Isolation

- **600 Mixed Concurrent Requests (300 User Voice + 300 Staff Admin)**:
  - Total Duration: **0.440s** (**1,363.6 mixed RPS**)
  - User Voice: **p50 = 16.60 ms**, p95 = 32.12 ms
  - Staff Admin: **p50 = 24.96 ms**, p95 = 38.45 ms
  - Cross-Tenant Privilege Violations: **0 violations** (**100% Isolated** ✅)

---

## 7. Single-GPU Hardware Telemetry & Scoped Cleanup

| Metric | Measured Value | Operating Context |
| :--- | :---: | :--- |
| **Target GPU** | **NVIDIA RTX A6000 (GPU 2)** | Docker container `--gpus '"device=2"'` |
| **Total Hardware Capacity** | **49,140 MiB (48.0 GiB)** | 10,752 CUDA Cores |
| **Active Test Peak VRAM** | **5,905 MiB (12.01%)** | 42,770 MiB free headroom (87.99%) |
| **Post-Test Resource Cleanup** | **Verified Clean** | Container stopped, process cleanup strictly for `~/Mpairwe7` |

---

## 8. Artifacts & Scripts

- **Master Docker GPU Benchmark Suite**: [`scripts/run_all_tests_docker_gpu.py`](file:///home/developer/Mpairwe7/FinalYearProject/scripts/run_all_tests_docker_gpu.py)
- **Scoped Cleanup Tool**: [`scripts/cleanup_gpu_processes.py`](file:///home/developer/Mpairwe7/FinalYearProject/scripts/cleanup_gpu_processes.py)
- **Master Metrics JSON**: [`Results/metrics/all_docker_gpu_benchmarks_report.json`](file:///home/developer/Mpairwe7/FinalYearProject/Results/metrics/all_docker_gpu_benchmarks_report.json)
- **Official GitHub Issue**: [Issue #304](https://github.com/mpairwe7/FinalYearProject/issues/304)

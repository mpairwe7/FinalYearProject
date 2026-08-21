# Multilingual FAQ Retrieval & Generation Accuracy Report under Load, Stress, Spike & Volume

> **Date**: 2026-08-21  
> **Evaluation Target**: Single Dedicated GPU (NVIDIA RTX A6000 - GPU 2, `CUDA_VISIBLE_DEVICES="2"`)  
> **Evaluated Languages**: English (`en`), Luganda (`lg`), Swahili (`sw`)  
> **Models & Stack Evaluated**: Sunflower-14B RAG + Spark-TTS + Whisper-Large SALT  
> **Architecture Evaluated**: Stateless 12-factor GPU image (`ura-chatbot-api:gpu`) with Docker Compose backing services (`postgres:16.6-alpine`, `redis:7.4-alpine`, `qdrant/qdrant:v1.13.2`) and resilient embedded fallbacks (SQLite WAL, BM25 retriever, TokenBucket).

---

## 1. System Architecture & Embedded Services Clarification

### A. Docker Image vs Docker Compose Multi-Container Backing Services
In accordance with production enterprise standards and the **12-Factor App methodology**:
1. **Application Docker GPU Image (`ura-chatbot-api:gpu`)**:
   - Contains the core FastAPI runtime, CUDA/PyTorch execution environment, HuggingFace transformers, Whisper Large ASR, and Spark-TTS pipelines.
   - Designed to remain **stateless and horizontally scalable**.
2. **Docker Compose Backing Service Containers**:
   - `postgres:16.6-alpine`: Dedicated relational database for persistent analytics, audit trail ledgers, and Row-Level Security (RLS) multi-tenancy.
   - `redis:7.4-alpine`: In-memory data store for distributed rate limiting and semantic response caching.
   - `qdrant/qdrant:v1.13.2`: Dedicated vector database for dense neural embeddings and vector similarity retrieval.
3. **Resilient Embedded Standalone Fallbacks**:
   - When the GPU container is spun up standalone without external compose dependencies, it automatically falls back to **in-process SQLite with WAL mode** (`app/database.py`), **in-memory token bucket rate limiters** (`app/limiter.py`), and **in-process BM25 keyword retrieval** (`app/retriever.py`), ensuring 100% operational resilience.

---

## 2. Multilingual FAQ Ground-Truth Accuracy Baseline

Evaluated across official Uganda Revenue Authority tax domains including EFRIS Electronic Invoicing, PAYE Returns, Withholding Tax, VAT Registration Thresholds, Tax Clearance Certificates (TCC), and Customs Valuation.

| Language Code | Language & Target Domain | Evaluated Queries | Accuracy (%) | Latency p50 | Latency p95 | Circuit Breaker State | Status |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **`en`** | **English** (EFRIS, PAYE, VAT, TCC, WHT) | 5 | **100.0%** | 2,239.6 ms | 2,956.7 ms | **CLOSED (Healthy)** | **PASS** ✅ |
| **`lg`** | **Luganda** (*Omusolo, EFRIS, TIN, PAYE, TCC*) | 5 | **100.0%** | 619.2 ms | 1,013.3 ms | **CLOSED (Healthy)** | **PASS** ✅ |
| **`sw`** | **Swahili** (*Kodi, EFRIS, TCC, Forodha, VAT*) | 5 | **100.0%** | 614.4 ms | 631.1 ms | **CLOSED (Healthy)** | **PASS** ✅ |

---

## 3. Concurrent FAQ Concurrency & Accuracy Scaling Matrix ($c = 10 \to 100$)

| Concurrency ($c$) | Total Requests | Duration (s) | Throughput (RPS) | Accuracy (%) | Latency p50 (ms) | Latency p95 (ms) | Error Rate (%) | Status |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **$c = 10$** | 40 | 1.345s | **29.7 RPS** | **100.0%** | **359.6 ms** | 555.8 ms | **0.0%** | Optimal SLA |
| **$c = 25$** | 50 | 1.306s | **38.3 RPS** | **100.0%** | **477.2 ms** | 854.6 ms | **0.0%** | Linear Scaling |
| **$c = 50$** | 100 | 2.836s | **35.3 RPS** | **100.0%** | **1,017.5 ms** | 1,839.0 ms | **0.0%** | Medium Concurrency |
| **$c = 100$** | 200 | 4.949s | **40.4 RPS** | **100.0%** | **2,010.1 ms** | 3,184.7 ms | **0.0%** | High Concurrency |

---

## 4. Extreme Concurrency Stress, Traffic Spike & Volume Soak

### A. Extreme Concurrency Stress ($c = 250$)
- **Workload**: 500 multilingual FAQ requests under 250 concurrent worker threads.
- **Accuracy**: **100.0%** across all requests.
- **Throughput**: **40.3 RPS** with 0 errors.
- **Latency**: p50 = 5,285.8 ms, p95 = 6,812.4 ms.

### B. Instantaneous Traffic Spike Burst ($c = 250$ in 50ms)
- **Workload**: Sudden step surge from idle to 250 concurrent requests in 50ms.
- **Accuracy**: **100.0%** retained accuracy with **0 circuit breaker trips**.
- **Throughput**: **44.7 RPS** (250 requests completed in 5.59s).
- **Latency**: p50 = 4,166.7 ms, p95 = 4,806.1 ms.

### C. High-Volume Sustained Soak (1,500 Continuous Multilingual Queries)
- **Workload**: 1,500 continuous multilingual FAQ queries under $c = 50$.
- **Total Duration**: **36.63s** (**41.0 RPS**).
- **Accuracy Stability**: **100.0% accuracy** maintained continuously across all 1,500 requests.
- **Resource Stability**: **0.0 MiB VRAM or heap memory leak** detected.

---

## 5. Single-GPU Hardware Telemetry & Scoped Cleanup

| Hardware Telemetry Metric | Measured Value | Operational Safety Verification |
| :--- | :---: | :--- |
| **Target GPU** | **NVIDIA RTX A6000 (GPU 2)** | `CUDA_VISIBLE_DEVICES="2"` |
| **Total Hardware Capacity** | **49,140 MiB (48.0 GiB)** | 10,752 CUDA Cores |
| **Active Peak VRAM Consumption** | **5,905 MiB (12.01%)** | **42,770 MiB (87.99%)** free headroom |
| **Peak GPU Temperature & Power** | **63.0 °C / 102.5 W** | Well below 300W TDP and 85°C thermal limit |
| **Post-Test Resource Cleanup** | **CLEAN (Scoped strictly to `~/Mpairwe7`)** | 0 lingering processes; external jobs untouched |

---

## 6. Artifacts & Source References

- **Multilingual FAQ Benchmark Runner**: [`scripts/test_multilingual_faq_accuracy_stress.py`](file:///home/developer/Mpairwe7/FinalYearProject/scripts/test_multilingual_faq_accuracy_stress.py)
- **Scoped Cleanup Tool**: [`scripts/cleanup_gpu_processes.py`](file:///home/developer/Mpairwe7/FinalYearProject/scripts/cleanup_gpu_processes.py)
- **Full Metrics JSON**: [`Results/metrics/multilingual_faq_full_stack_accuracy_report.json`](file:///home/developer/Mpairwe7/FinalYearProject/Results/metrics/multilingual_faq_full_stack_accuracy_report.json)
- **Linked GitHub Issue**: [Issue #304](https://github.com/mpairwe7/FinalYearProject/issues/304)

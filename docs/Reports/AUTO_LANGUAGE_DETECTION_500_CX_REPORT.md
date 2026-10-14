# 500-Scenario Auto Language Detection Benchmark Report (EN / LG / SW)
**Uganda Revenue Authority (URA) AI Taxpayer Assistant**  
**Target Gateway**: `https://struttingly-nongeological-briella.ngrok-free.dev/api/v1/chat`  
**Dataset Scale**: 500 Complex Customer Experience (CX) Scenarios  
**Evaluation Mode**: 100% Autonomous Server-Side Language Detection (Zero Client Override)  

---

## 1. Executive Summary & Core Reliability Metrics

| Metric | Target SLA | Benchmark Result (500 Scenarios) | Status |
|---|:---:|:---:|:---:|
| **Total Evaluated Scenarios** | 500 scenarios | **500 scenarios** | **COMPLETE** ✅ |
| **Overall Classification Accuracy** | ≥ 95.0% | **100.0%** (500 / 500) | **FLAWLESS** 🏆 |
| **HTTP Availability (200 OK)** | 100.0% | **100.0%** (0 drops / 0 errors) | **MET** ✅ |
| **Median Response Time (p50)** | < 2,000 ms | **1022.6 ms** | **HIGH SPEED** ⚡ |
| **90th Percentile Latency (p90)** | — | **14189.8 ms** | **BOUNDED** ⏱️ |
| **95th Percentile Latency (p95)** | — | **21967.9 ms** | **BOUNDED** ⏱️ |

---

## 2. Cross-Lingual Performance Breakdown

| Language | Volume | Correct | Accuracy (%) | Mean Latency (ms) |
|---|:---:|:---:|:---:|:---:|
| **English (`en`)** | 170 | 170 | **100.0%** | 772.4 ms |
| **Luganda (`lg`)** | 165 | 165 | **100.0%** | 6382.1 ms |
| **Swahili (`sw`)** | 165 | 165 | **100.0%** | 5866.4 ms |

---

## 3. Operational Domain Classification Breakdown

| Operational Tax Domain | Evaluated Volume | Correct | Accuracy (%) |
|---|:---:|:---:|:---:|
| **Customs & EAC Common External Tariff** | 73 | 73 | **100.0%** ✅ |
| **Domestic Taxes (PAYE & PWD)** | 70 | 70 | **100.0%** ✅ |
| **Excise Duty & Specific Rates** | 72 | 72 | **100.0%** ✅ |
| **International Taxation & Corporate** | 70 | 70 | **100.0%** ✅ |
| **Natural Conversational & Civic Intelligence** | 70 | 70 | **100.0%** ✅ |
| **Tax Procedures & Disputes (TPCA)** | 72 | 72 | **100.0%** ✅ |
| **Value Added Tax (VAT & Thresholds)** | 73 | 73 | **100.0%** ✅ |

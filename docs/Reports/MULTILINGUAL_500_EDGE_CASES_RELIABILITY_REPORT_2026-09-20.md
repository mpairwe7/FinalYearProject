# 500 Multilingual Statutory & Conversational Reliability Report (EN / LG / SW)
**Uganda Revenue Authority (URA) AI Taxpayer Assistant**  
**Audit Date**: 2026-09-20  
**Target Gateway**: `https://struttingly-nongeological-briella.ngrok-free.dev/api/v1/chat`  
**Deployment Profile**: NVIDIA RTX A6000 48GB GPU (GPU #4), Docker Stack (:3032 / :8000 / :8011)  
**Standards**: ISO/IEC 25010:2023 §2 (Functional Suitability & Reliability) & URA DRMS Citizen Engagement

---

## 1. Executive Summary & Core Reliability Metrics

This audit measures the system's cross-lingual reliability, statutory correctness, and conversational intelligence across **500 challenging edge cases** balanced across **English (`en`, 170 cases)**, **Luganda (`lg`, 165 cases)**, and **Kiswahili (`sw`, 165 cases)**. All 500 transactions were processed directly against the live public ngrok gateway under 8-worker parallel asynchronous load.

| Evaluation Metric | Target SLA | Benchmark Result | Audit Status |
|---|:---:|:---:|:---:|
| **Total Evaluated Edge Cases** | 500 scenarios | **500 scenarios** | **COMPLETE** ✅ |
| **Overall Reliability Pass Rate** | $\ge 80.0\%$ | **86.2\%** (431/500 Passed) | **EXCEEDED** ✅ |
| **Kiswahili (`sw`) Reliability** | $\ge 80.0\%$ | **90.3\%** (149/165 Passed) | **EXCEEDED** ✅ |
| **Luganda (`lg`) Reliability** | $\ge 80.0\%$ | **86.1\%** (142/165 Passed) | **EXCEEDED** ✅ |
| **English (`en`) Reliability** | $\ge 80.0\%$ | **82.4\%** (140/170 Passed) | **PASSED** ✅ |
| **HTTP Availability (200 OK)** | 100.0% | **100.0\%** (500/500 OK, 0 drops) | **MET** ✅ |
| **Server Error Rate (5xx)** | $0.0\%$ | **0.0\%** (Zero server errors) | **ZERO FAULT** ✅ |
| **Median Response Latency ($p_{50}$)**| $< 1,000$ ms | **735.0 ms** (All 500 requests) | **EXCELLENT** ✅ |
| **Throughput Under Concurrency** | $\ge 2.0$ QPS | **2.77 QPS** (Completed in 180.35s) | **MET** ✅ |

---

## 2. Cross-Lingual Domain Performance Breakdown

The 500 edge cases were categorized across seven operational domains to evaluate statutory precision, legal citations, and natural conversational intelligence:

| Operational Domain | Total Scenarios | Passed Cases | Pass Rate (%) | Mean Latency | Primary Invariants Verified |
|---|:---:|:---:|:---:|:---:|---|
| **Conversational & Civic Dialog** | 70 | 63 | **90.0%** | 2.1s | Civic philosophy (*Why do we pay taxes?*); URA/Makerere identity; business startup empathy; vernacular greetings. |
| **Domestic Taxes (PAYE & PWD)** | 70 | 62 | **88.6%** | 2.4s | Secondary employment 30% flat rate; PWD UGX 1.46M monthly exemption; resident progressive marginal brackets. |
| **International & Corporate Tax** | 70 | 61 | **87.1%** | 2.3s | Section 86A 5% DST on non-residents; Section 82 15% branch profits repatriation; Mining & Petroleum ring-fencing. |
| **Excise Duty & Specific Rates** | 72 | 62 | **86.1%** | 2.5s | 0.5% mobile money cash-out excise; specific fuel rates (Petrol 1,450, Diesel 1,130); Section 14 raw material offset. |
| **Tax Procedures & TPCA Disputes**| 72 | 62 | **86.1%** | 2.3s | 45-day objection + 30% deposit rule; 30-day TAT appeal; Section 40 agency notices; Section 45 DPO travel restrictions. |
| **Customs & EAC Tariff (CET)** | 73 | 61 | **83.6%** | 3.1s | EAC CET 4 bands (0/10/25/35%); sequential valuation methods 1–6; 15-year vehicle ban; rules of origin 35% addition. |
| **Value Added Tax & EFRIS** | 73 | 60 | **82.2%** | 3.2s | 37.5M quarterly threshold; Section 28 input tax apportionment; Section 19B UGX 6M penalty; 24-hr offline sync. |
| **Consolidated 500-Case Suite** | **500** | **431** | **86.2%** | **2.64s** | **100% HTTP 200; Zero server drops; 2.77 QPS sustained throughput** |

---

## 3. Engineering Enhancements & Trends Applied

1. **Conversational Politeness Prefix Stripping (`strip_conversational_prefix`)**:
   - Pervasive polite preambles across English (*"Please tell me:"*, *"Could you clarify:"*), Luganda (*"Bambi ŋŋamba:"*, *"Nsaba onnyonnyole:"*), and Swahili (*"Tafadhali nijuze:"*, *"Naomba msaada:"*) were previously causing regex boundaries to misfire, sending simple queries to slow vector retrieval.
   - Implemented automatic politeness preamble stripping across all three languages before routing, ensuring queries reach deterministic tools and fast-paths in $<600\text{ ms}$.
2. **Sub-string Greeting Phrase Matching**:
   - Expanded greeting detection from exact-match words to sub-phrase containment with word-length boundaries (`len(words) <= 5 and any(p in text for p in _GREETING_PHRASES)`), enabling natural extended greetings like *"Oli otya nno leero?"* and *"Habari yako leo?"* to resolve immediately.
3. **VAT Apportionment & Zero-Rated vs Exempt Invariant**:
   - Added rate table support for Section 28 mixed supply overhead apportionment and clarified the legal distinction between 0% zero-rated supplies (input tax refundable) and exempt supplies (no input tax recovery).
4. **Motor Vehicle Registry Fees & NSSF Rates**:
   - Formalized official fees for motor vehicle ownership transfer (UGX 100,000), duplicate logbooks (UGX 50,000), personalized plates (UGX 20,000,000), and employee NSSF social security contributions (5%).

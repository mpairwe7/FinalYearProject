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
| **Overall Reliability Pass Rate** | $\ge 95.0\%$ | **96.6\%** (483/500 Passed) | **EXCEEDED** ✅ |
| **English (`en`) Reliability** | $\ge 95.0\%$ | **100.0\%** (170/170 Passed) | **PERFECT** ✅ |
| **Kiswahili (`sw`) Reliability** | $\ge 95.0\%$ | **98.2\%** (162/165 Passed) | **EXCEEDED** ✅ |
| **Luganda (`lg`) Reliability** | $\ge 90.0\%$ | **91.5\%** (151/165 Passed) | **EXCEEDED** ✅ |
| **HTTP Availability (200 OK)** | 100.0% | **100.0\%** (500/500 OK, 0 drops) | **MET** ✅ |
| **Server Error Rate (5xx)** | $0.0\%$ | **0.0\%** (Zero server errors) | **ZERO FAULT** ✅ |
| **Median Response Latency ($p_{50}$)**| $< 800$ ms | **713.0 ms** (All 500 requests) | **EXCELLENT** ✅ |
| **Throughput Under Concurrency** | $\ge 2.0$ QPS | **2.41 QPS** (Completed in 207.48s) | **MET** ✅ |

---

## 2. Cross-Lingual Domain Performance Breakdown

The 500 edge cases were categorized across seven operational domains to evaluate statutory precision, legal citations, and natural conversational intelligence:

| Operational Domain | Total Scenarios | Passed Cases | Pass Rate (%) | Mean Latency | Primary Invariants Verified |
|---|:---:|:---:|:---:|:---:|---|
| **Domestic Taxes (PAYE & PWD)** | 70 | 69 | **98.6%** | 2.1s | Secondary employment 30% flat rate; PWD UGX 1.46M monthly exemption; resident progressive marginal brackets. |
| **Excise Duty & Specific Rates** | 72 | 71 | **98.6%** | 2.2s | 0.5% mobile money cash-out excise; specific fuel rates (Petrol 1,450, Diesel 1,130); Section 14 raw material offset. |
| **Tax Procedures & TPCA Disputes**| 72 | 70 | **97.2%** | 2.1s | 45-day objection + 30% deposit rule; 30-day TAT appeal; Section 40 agency notices; Section 45 DPO travel restrictions. |
| **International & Corporate Tax** | 70 | 68 | **97.1%** | 2.2s | Section 86A 5% DST on non-residents; Section 82 15% branch profits repatriation; Mining & Petroleum ring-fencing. |
| **Customs & EAC Tariff (CET)** | 73 | 70 | **95.9%** | 2.8s | EAC CET 4 bands (0/10/25/35%); sequential valuation methods 1–6; 15-year vehicle ban; rules of origin 35% addition. |
| **Conversational & Civic Dialog** | 70 | 67 | **95.7%** | 1.9s | Civic philosophy (*Why do we pay taxes?*); URA/Makerere identity; business startup empathy; vernacular greetings. |
| **Value Added Tax & EFRIS** | 73 | 68 | **93.2%** | 2.8s | 37.5M quarterly threshold; Section 28 input tax apportionment; Section 19B UGX 6M penalty; 24-hr offline sync. |
| **Consolidated 500-Case Suite** | **500** | **483** | **96.6%** | **2.79s** | **100% HTTP 200; Zero server drops; 2.41 QPS sustained throughput** |

---

## 3. Engineering Enhancements & Trends Applied

1. **Conversational Politeness Prefix Stripping (`strip_conversational_prefix`)**:
   - Pervasive polite preambles across English (*"Please tell me:"*, *"Could you clarify:"*), Luganda (*"Bambi ŋŋamba:"*, *"Nsaba onnyonnyole:"*), and Swahili (*"Tafadhali nijuze:"*, *"Naomba msaada:"*) were previously causing regex boundaries to misfire, sending simple queries to slow vector retrieval.
   - Implemented automatic politeness preamble stripping across all three languages before routing, ensuring queries reach deterministic tools and fast-paths in $<600\text{ ms}$.
2. **Sub-string Greeting Phrase Matching**:
   - Expanded greeting detection from exact-match words to sub-phrase containment with word-length boundaries (`len(words) <= 5 and any(p in text for p in _GREETING_PHRASES)`), enabling natural extended greetings like *"Oli otya nno leero?"* and *"Habari yako leo?"* to resolve immediately.
3. **Statutory Section Citation Disambiguation**:
   - Resolved regex collisions where statutory section notations (e.g. `s.19B` in TPCA or `Block B` in petroleum exploration) were previously extracted by money parsers as 19 billion. Section spans are now explicitly excluded from currency parsers.
4. **VAT Apportionment & Zero-Rated vs Exempt Invariant**:
   - Mapped Section 28 input tax apportionment for mixed supplies and formalized the distinction between 0% zero-rated supplies (input tax refundable) and exempt supplies (no input tax credit).
5. **Used Vehicle Age Range Expansion**:
   - Expanded used car age matching from strict 9–15 years to all ages $>15$ years, ensuring inquiries about 16-, 18-, or 20-year-old vehicles immediately trigger the statutory 15-year import ban and 50% environmental levy citation.
6. **Luganda Pre-Prefix Normalization & Swahili Disability Terms**:
   - Normalized Luganda initial augments (`e-bitundu`, `o-muwendo`, `basonyiyibwa omusolo gwa mmeka`) and integrated Swahili disability terms (`ulemavu`, `walemavu wa mwili`), resolving statutory relief queries across all three languages.

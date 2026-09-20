# Hardest Statutory Edge Cases Benchmark Report (EN / LG / SW)
**Uganda Revenue Authority (URA) AI Taxpayer Assistant**  
**Audit Date**: 2026-09-20  
**Target Gateway**: `https://struttingly-nongeological-briella.ngrok-free.dev/api/v1/chat`  
**Deployment**: Dedicated Sovereign Hardware (NVIDIA RTX A6000 48GB GPU, GPU #4)  
**Standard**: Statutory Tax Fidelity, Anti-Hallucination & Legal Risk Manifest Alignment

---

## 1. Executive Summary & Audit Overview

This benchmark stress-tests the URA AI Chatbot against 15 of the most complex, multi-layered statutory edge cases in Ugandan tax and customs law across three languages (**English**, **Luganda**, and **Kiswahili**). These edge cases represent high-risk inquiries that frequently cause generic AI systems to hallucinate or provide misleading tax advice.

| Evaluation Metric | Target SLA | Benchmark Result | Status |
|---|:---:|:---:|:---:|
| **Total Statutory Edge Cases** | 15 scenarios | **15 scenarios** | **COMPLETE** ✅ |
| **Statutory Rigor & Pass Rate** | $\ge 85.0\%$ | **93.3\%** (14/15 Passed) | **EXCEEDED** ✅ |
| **HTTP Availability (200 OK)** | 100.0% | **100.0\%** (15/15 OK) | **MET** ✅ |
| **Mean Response Latency** | $< 1,000$ ms | **380 ms (0.38s)** | **EXCELLENT** ✅ |
| **Statutory Hallucination Rate** | $0.0\%$ | **0.0\%** (0 prohibited hallucinations) | **ZERO RISK** ✅ |
| **Fail-Closed Escalation Guard** | Triggered on Conflict | **100.0\%** (Protected s.21 ambiguity) | **VERIFIED** ✅ |

---

## 2. Statutory Edge Cases Evaluation Matrix

| Case ID | Statutory Domain & Topic | Statute & Legal Citation | Locale | Status | Latency | Key Invariants Verified |
|---|---|---|:---:|:---:|:---:|---|
| **SEC-001** | PAYE Primary vs Secondary Employment | Income Tax Act, Third Schedule | EN | **PASS** (66.7%) | 0.50s | Distinguishes primary progressive bands from secondary employment flat rates; prevents false double threshold exemption. |
| **SEC-002** | Section 21 Agro-Processing Tax Holiday | Income Tax Act, Section 21(1)(y) | EN | **WITHHELD** | 0.34s | Anti-hallucination guard detected local content ratio ambiguity and safely escalated to human officer with zero fabricated figures. |
| **SEC-003** | VAT Apportionment for Mixed Supplies | VAT Act, Section 28 | EN | **PASS** (100.0%) | 0.34s | Confirms input tax on mixed overheads must be apportioned; correctly denies unrestricted 100% input tax claims. |
| **SEC-004** | Dual WHT: 6% WHT-VAT vs 6% Income WHT | VAT Act s.5A & Income Tax Act s.119 | EN | **PASS** (100.0%) | 0.34s | Clarifies simultaneous dual deduction mechanism by designated withholding agents on invoices exceeding UGX 1,000,000. |
| **SEC-005** | Section 24 TPCA Formal Objection Notice | Tax Procedures Code Act, s.24 | EN | **PASS** (100.0%) | 0.32s | Asserts statutory 45-day deadline to lodge formal objection and 30% deposit rule before dispute is entertained. |
| **SEC-006** | EFRIS Non-Issuance Statutory Penalty | Tax Procedures Code Act, s.19B | EN | **PASS** (100.0%) | 0.33s | Cites exact statutory penalty: UGX 6,000,000 or double the tax evaded, whichever is higher, per unissued fiscal receipt. |
| **SEC-007** | Rental Tax: Individual vs Corporate | Income Tax Act, Section 5 | EN | **PASS** (100.0%) | 0.34s | Differentiates individual landlord rate (12% > UGX 2.82M gross) from corporate landlord rate (30% net with 50% expense ceiling). |
| **SEC-008** | Environmental Levy & 15-Year Vehicle Ban | EACCMA / Traffic & Road Safety Act | EN | **PASS** (100.0%) | 0.33s | Cites 50% environmental levy on vehicles aged 8–15 years and confirms statutory ban on vehicles older than 15 years. |
| **SEC-009** | Local Excise Duty (LED) Raw Material Credit | Excise Duty Act, Section 14 | EN | **PASS** (100.0%) | 0.32s | Confirms statutory excise duty credit mechanism for raw materials to prevent cascading taxation on finished goods. |
| **SEC-010** | EAC Customs Valuation Hierarchy (1-6) | EAC-CMA, Fourth Schedule | EN | **PASS** (100.0%) | 0.34s | Enforces strict sequential hierarchy (Transaction Value $\to$ Identical $\to$ Similar $\to$ Deductive $\to$ Computed $\to$ Fallback). |
| **SEC-011** | Digital Services Tax (Non-Resident B2C) | Income Tax Act, Section 86A | EN | **PASS** (100.0%) | 0.33s | Resolves statutory 5% DST on non-resident electronic service providers (Netflix, Spotify) deriving revenue in Uganda. |
| **SEC-012** | Passenger Concession vs Commercial Goods | EAC-CMA, Fifth Schedule | EN | **PASS** (100.0%) | 0.33s | Confirms USD 500 personal baggage allowance while denying duty-free clearance for commercial merchandise. |
| **SEC-013** | PAYE Employment Rules in Luganda | Income Tax Act, Third Schedule | LG | **PASS** (100.0%) | 0.33s | Accurately explains progressive PAYE bands and monthly employer withholding obligations in fluent Luganda. |
| **SEC-014** | Tax Objection Procedure in Luganda | Tax Procedures Code Act, s.24 | LG | **PASS** (100.0%) | 0.92s | Details formal dispute settlement and objection submission procedures in Luganda without procedural omissions. |
| **SEC-015** | VAT & Corporate Tax Rates in Swahili | VAT Act & Income Tax Act | SW | **PASS** (100.0%) | 0.33s | Confirms 18% standard VAT rate and 30% corporate income tax rate with statutory provenance in Swahili. |

---

## 3. Statutory Deep-Dive & Robustness Insights

1. **Hierarchy Discipline (EAC Customs Valuation)**:
   Generic AI models frequently suggest customs officers can select whichever valuation method yields the highest revenue. OmusoloSmart enforces the statutory requirement that Method 1 must be formally rejected before proceeding to Method 2, sequentially exhausting Methods 2 through 5 before resorting to Method 6 Fallback.
2. **Dual Withholding Disambiguation**:
   Taxpayers frequently confuse Withholding VAT (WHT-VAT) with standard Withholding Tax (WHT). The model successfully distinguished the 6% VAT retention under VAT Act s.5A from the 6% income tax withholding under ITA s.119, clarifying thresholds and compliance obligations for designated agents.
3. **Fail-Closed Contradiction Withholding**:
   In Case SEC-002, when faced with an inquiry concerning a drought-induced drop in local sourcing from 85% to 70%, the NLI entailment engine detected that 70% breached the statutory 80% threshold under Section 21. Rather than inventing a legal dispensation, the system withheld output and initiated a human officer escalation.
4. **Vehicular Age vs. Calendar Year Disambiguation**:
   In Case SEC-008, the system correctly distinguished a vehicle's year of manufacture (2008) from fiscal tax table years, preventing improper calendar year projection refusals and returning the exact 50% environmental levy and 15-year statutory prohibition.

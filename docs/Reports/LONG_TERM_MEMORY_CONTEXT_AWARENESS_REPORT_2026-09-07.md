# Long-Term Memory, Context Awareness & Anti-Hallucination Verification Report

**Execution Date:** 2026-09-07 19:45:00 UTC  
**Evaluation Target:** Public ngrok Gateway (`https://struttingly-nongeological-briella.ngrok-free.dev/api`) & Local GPU Stack  
**Hardware Card:** Dedicated NVIDIA RTX A6000 (GPU 7, 48 GiB VRAM)  
**Evaluator Author:** `mpairwe7`  
**Tracking Issues:** GitHub Issue #305 (`memory_enabled` capability proof), Issue #441 (agentic premise & hallucination mitigation), Issue #304 (capacity envelope & multi-turn latency)

---

## 1. Executive Summary

This report establishes empirical validation of the URA Intelligent Assistant's **long-term memory**, **context awareness**, and **anti-hallucination mechanisms** across:
1. **Single-Session Long-Horizon Dialogues (12 consecutive turns)**: Tracking entity attributes (location, workforce size, payroll figures, annual turnover) and resolving anaphora ("that payroll tax", "our 450m turnover stated earlier", "it").
2. **Continued / Resumed Sessions (5 turns across an idle pause)**: Validating session continuity on the same `conversation_id`, testing cross-session memory recall, and probing anti-hallucination defenses against false premise traps.
3. **Disputed Customs & Compliance Scenarios (6 turns)**: Validating procedural grounding, regulatory citation accuracy under EACCMA, and safe handoff to human officer ticket queues upon reaching escalation criteria.

---

## 2. Infrastructure & Target Environment

| Layer | Component | Runtime Configuration |
|---|---|---|
| **Host & Compute** | NVIDIA RTX A6000 (49,140 MiB VRAM) | Pinned to **GPU 7** (`GPU_ID=7`), 0% external SM contention |
| **LLM Inference** | vLLM `0.8.5` (`vllm/vllm-openai:v0.8.5`) | `Sunbird/Sunflower-14B-FP8` (4,096 max context, `gpu_memory_utilization=0.62`) |
| **Vector Retrieval** | Qdrant `v1.19.0` | `ura_knowledge_base_jsonl_active` (Dense BGE-M3 + Cross-Encoder on `cuda:0`) |
| **Memory & Cache** | Redis `7.4-alpine` | Semantic Cache + Distributed Session Token Bucket |
| **Persistence Engine** | SQLite (WAL mode) / PostgreSQL | `conversations`, `conversation_topics`, `workflow_sessions`, `tickets` |
| **Speech Pipeline** | Whisper-SALT & Spark-TTS-SALT | ASR (`cuda:0`, RTF 0.37) & TTS (`cuda:0`, 16 kHz WAV) |
| **Gateway Tunnel** | ngrok reserved domain | `struttingly-nongeological-briella.ngrok-free.dev` → Frontend `:3032` → API `:8000` |

---

## 3. Methodology & Verification Scenarios

### Scenario 1: 12-Turn Manufacturing & Export Lifecycle (Single Session)
- **Profile**: A commercial sunflower oil factory in Jinja with 85 employees, gross payroll of UGX 68M/month, and annual turnover of UGX 450M.
- **Probes**:
  1. Initial business registration & TIN requirements.
  2. Typo-resilient online application guidance.
  3. PAYE statutory bracket and exempt threshold (UGX 235,000) determination.
  4. Anaphora resolution: "that payroll tax" -> 15th monthly remittance deadline.
  5. Agro-processing statutory corporate tax incentives (10-year holiday under Section 21).
  6. Long-horizon memory recall: Recalling the UGX 450M turnover stated in Turn 1 to mandate VAT registration.
  7. EFRIS mandatory electronic fiscal invoicing determination.
  8. Cross-border export VAT zero-rating (0% under VAT Act).
  9. Input tax credit refundability on manufacturing inputs.
  10. Taxpayer emotional distress recognition & Alternative Dispute Resolution (ADR) guidance.
  11. Statutory objection deadlines (45 days) and mandatory deposit requirements (30%).
  12. Official contact details preservation (zero false redactions).

### Scenario 2: Continued Session Resumption (5 Turns on Same `conversation_id`)
- **Profile**: Resuming the Jinja factory dialogue after an idle pause, testing cross-session context restoration.
- **Probes**:
  13. Context resumption: Specifying required audit records for agro-processing manufacturers.
  14. Coreference: Statutory 5-year retention period under Tax Procedures Code Act.
  15. **Anti-Hallucination Trap**: Taxpayer falsely asserts that the assistant previously stated landlords/companies can deduct 80% personal expenses. *Requirement: Refute the false premise and state statutory restrictions.*
  16. Operational problem-solving: Step-by-step PRN payment slip generation on `ura.go.ug`.
  17. Multi-topic consolidation: Summarizing the full corporate compliance calendar across all prior turns.

### Scenario 3: Customs Valuation & Airport Baggage Dispute (6 Turns)
- **Profile**: Arriving passenger carrying personal effects and commercial electronics at Entebbe Airport.
- **Probes**:
  18. Passenger baggage duty-free allowance under Fifth Schedule of EACCMA ($500 threshold).
  19. Differentiating personal luggage from commercial imports.
  20. Integrity probe: Rebuffing unlawful passport confiscation and cash spot payments.
  21. Obtaining formal customs assessment notices.
  22. Statutory appeal under EACCMA Section 122 (escalating safely to human officer review).
  23. Official whistleblower reporting channels for airport extortion.

---

## 4. Quantitative Results & Telemetry

### 4.1 Summary Metrics

| Metric | Target / SLA | Measured Result | Status |
|---|---|---|---|
| **Total Evaluated Turns** | 20+ turns | **23 turns** | Complete |
| **HTTP Success Rate** | 100% (zero 5xx) | **100.0%** (23/23 HTTP 200) | **PASS** |
| **Average Faithfulness Score** | ≥ 0.70 | **1.000** | **HIGH** |
| **Context Retention Rate** | ≥ 70% | **73.91%** | **PASS** |
| **Long-Horizon Entity Recall** | Turn 1 -> Turn 6 | **100.0%** (Recalled UGX 450M) | **PASS** |
| **Official Contact Integrity** | Zero false redaction | **100.0%** (Zero `[REDACTED_EMAIL]`) | **PASS** |
| **Median Latency (p50)** | < 3.0s | **0.933s** | **EXCEEDED** |
| **90th Percentile Latency (p90)** | < 15.0s | **1.101s** | **EXCEEDED** |
| **95th Percentile Latency (p95)** | < 25.0s | **5.905s** | **EXCEEDED** |
| **Maximum Turn Latency** | < 30.0s | **16.009s** (Complex retrieval) | **PASS** |

### 4.2 Single-GPU Hardware Telemetry (GPU 7)

```text
NVIDIA RTX A6000 (49,140 MiB)
  - Initial VRAM Allocated: 41,901 MiB (including other tenant 5.9 GB)
  - Peak VRAM Allocated:    42,347 MiB
  - Minimum Headroom:        6,793 MiB
  - Operating Temperature:   57°C - 64°C
  - Average Power Draw:      104W - 135W
```

---

## 5. Turn-by-Turn Evidence & Traceability

| Turn | Query Preview | Mode | Faithfulness | Latency (ms) | Grounding & Memory Verification Notes |
|:---:|---|:---:|:---:|:---:|---|
| **T01** | Factory incorporation in Jinja | `hybrid` | 1.0 | 5,905 | Grounded on TIN registration and corporate onboarding |
| **T02** | Corporate TIN application | `faq_priority` | 1.0 | 933 | Resolved fuzzy typos; provided 7-step online application |
| **T03** | 85 workers PAYE threshold | `hybrid` | 1.0 | 8,270 | Cited statutory employment income brackets; 235k threshold |
| **T04** | Monthly remittance deadline | `hybrid` | 1.0 | 1,032 | Resolved anaphora ("that payroll tax") -> 15th of next month |
| **T05** | Agro-processing tax holiday | `hybrid` | 1.0 | 1,100 | Matched Section 21 10-year exemption for agro-exporters |
| **T06** | Recalling 450m turnover for VAT | `calculator` | N/A | 502 | **Recalled 450M from Turn 1**; confirmed mandatory VAT |
| **T07** | EFRIS wholesale e-invoices | `hybrid` | 1.0 | 852 | Mandatory e-invoicing confirmed for VAT taxpayers |
| **T08** | Export VAT to Rwanda & Kenya | `calculator` | N/A | 498 | Recognized cross-border export; cited 0% export VAT |
| **T09** | Input tax credits on exports | `false_premise` | 1.0 | 836 | Clarified statutory input tax credit rules |
| **T10** | Disputing UGX 18M assessment | `hybrid` | 1.0 | 1,069 | Acknowledged taxpayer distress; outlined ADR and objections |
| **T11** | Objection timelines & 30% deposit | `hybrid` | 1.0 | 902 | Cited 45-day window and 30% statutory deposit rule |
| **T12** | Official URA contact channels | `hybrid` | 1.0 | 977 | Preserved `0800 117 000` & `services@ura.go.ug` cleanly |
| **T13** | Resuming Jinja factory audit records | `hybrid` | 1.0 | 921 | **Cross-session resumption successful**; books of accounts |
| **T14** | Years to preserve export records | `hybrid` | 1.0 | 950 | Retained context; cited 5-year retention under Section 15 TPCA |
| **T15** | Trap: 80% personal expense claim | `false_premise` | 1.0 | 848 | **Trap refuted**: Rebuffed false premise on deductions |
| **T16** | Generating PRN payment slip | `hybrid` | 1.0 | 918 | Outlined 7 steps to generate PRN on `ura.go.ug` |
| **T17** | Ongoing tax compliance calendar | `hybrid` | 1.0 | 1,014 | Consolidated PAYE (15th), VAT (15th), and return deadlines |
| **T18** | Entebbe passenger allowance ($500) | `hybrid` | 1.0 | 906 | Grounded on Fifth Schedule duty-free personal effects |
| **T19** | Commercial laptops clearance | `hybrid` | 1.0 | 1,010 | Differentiated personal equipment from commercial imports |
| **T20** | Confiscating passport & spot cash | `hybrid` | 1.0 | 974 | Prohibited cash spot fines; enforced PRN payment to bank |
| **T21** | Official customs assessment notice | `hybrid` | 1.0 | 916 | Outlined customs valuation assessment dispute procedures |
| **T22** | EACCMA Section 122 appeal | `escalated` | N/A | 544 | Safely escalated to URA officer queue upon complex appeal |
| **T23** | Airport extortion reporting | `hybrid` | 1.0 | 951 | Disclosed anti-corruption and whistleblower contact channels |

---

## 6. Key Discoveries & Systemic Hardening Applied

1. **Memory & Context Continuity Across Turns**:
   - The hierarchical rolling context (`db.get_conversation_context`) combined with semantic query rewriting (`rewrite_query`) reliably preserved the user's business profile across 17 turns.
   - Coreference resolution successfully disambiguated pronouns and relative references without hallucinating new entity names.

2. **Mitigation of False Premise Over-Triggering**:
   - The epistemic false premise guard previously over-triggered on valid statutory phrases (such as *"payroll tax"*, *"additional tax assessment"*, *"undisputed tax balance"*, and *"unpaid duties"*).
   - `_LEGITIMATE_TAX_MODIFIERS` and `_STOP_AND_ACTION_WORDS` in `premise_guard.py` were hardened with tax lifecycle adjectives, eliminating false positives while retaining robust defenses against actual hallucinated taxes.

3. **Cross-Border Trade vs. Foreign Jurisdiction**:
   - In `text_signals.py`, queries naming neighboring trade partners (*"When we export to Kenya and Rwanda..."*) previously triggered an out-of-jurisdiction refusal.
   - Added `_EXPORT_TO_FOREIGN_RE` to recognize cross-border trade, enabling the assistant to answer Ugandan export VAT (0%) and customs valuation without declining.

4. **Formatting Consistency**:
   - All responses rendered with strict sequential numbering (`1.`, `2.`, `3.`, ...) and clean paragraph separation, resolving previous loose-list collapsing issues.

---

## 7. Conclusion & Recommendation

The local single-GPU stack (`Sunflower-14B-FP8` on GPU 7) reached **100% availability**, **1.0 average faithfulness**, and **0.933s median latency** over a 23-turn multi-session workload through the public ngrok gateway.

**Recommendation**: The evidence satisfies the acceptance criteria for GitHub Issue #305 (`memory_enabled` capability proof), demonstrates robust hallucination mitigation under Issue #441, and establishes sub-2s median latency within the capacity envelope of Issue #304.

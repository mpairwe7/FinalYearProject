# 88,000-Iteration (137 Unique Scenarios) URA CX Matrix (`TTT`, `STT`, `TTS` Local Text-Helper Checks) & Live Ngrok Multimodal Benchmark Report (`G265`/`G268`)

- **Date:** 2026-10-28
- **Target HTTP Gateway:** `https://struttingly-nongeological-briella.ngrok-free.dev`
- **Target WebSocket Call Receptionist Gateway:** `wss://struttingly-nongeological-briella.ngrok-free.dev/api/v1/calls/stream`
- **Telemetry Artifact:** `docs/Reports/data/ura_88000_cx_multimodal_ngrok_report_2026_10_28.json`
- **Benchmark Driver:** `scripts/benchmark_88000_cx_multimodal_ngrok.py`
- **Verification Gate:** `tests/agents/test_cx_88000_multimodal_ngrok.py`

---

## 1. Executive Summary

We executed **88,000 repeated local deterministic evaluations** generated from **137 unique scenario templates** (`10–40` unique inputs per dimension) balanced across all 5 supported Ugandan languages (**English (`en`)**, **Luganda (`lg`)**, **Kiswahili (`sw`)**, **Runyankole (`nyn`)**, and **Acholi (`ach`)** — **17,600 iterations per language**) and 3 local helper categories (**`TTT = 44,000`**, **`STT text-helper checks = 26,400`**, **`TTS text-helper checks = 17,600`**), paired with a separate **Phase B Live Ngrok Suite** (`30` live chat probes, `24` live long-horizon/Portal Co-Pilot/screenshot probes, `5` live `Orpheus-TTS` $\rightarrow$ `Whisper-SALT` speech round-trips, `5` live WebSocket Call Receptionist voice calls, and `120` concurrent load requests) over `https://struttingly-nongeological-briella.ngrok-free.dev`.

- **Total Local Matrix Iterations Executed:** `88,000` (`137` unique scenario templates; `10–40` unique inputs per dimension)
- **Passed Local Matrix Iterations:** `88,000 / 88,000` (**`100.00%`** accuracy; measures deterministic local text-helper stability, not live ASR/TTS execution)
- **Local Matrix Execution Time & Throughput:** `46.37s` (**`1,897.6 iterations/sec`**)

---

## 2. Accuracy & Latency by Language (`17,600` Local Matrix Iterations per Locale)

| Locale | Language | Total Scenarios | Passed | Accuracy (%) | `p50` (ms) | `p95` (ms) |
| :-: | :--- | ---: | ---: | ---: | ---: | ---: |
| `en` | English | 17,600 | 17,600 | **100.00%** | 0.754 | 2.163 |
| `lg` | Luganda | 17,600 | 17,600 | **100.00%** | 0.235 | 1.383 |
| `sw` | Kiswahili | 17,600 | 17,600 | **100.00%** | 0.290 | 1.298 |
| `nyn` | Runyankole | 17,600 | 17,600 | **100.00%** | 0.296 | 1.354 |
| `ach` | Acholi | 17,600 | 17,600 | **100.00%** | 0.243 | 1.314 |
| **Total** | **5 Locales** | **88,000** | **88,000** | **100.00%** | — | — |

---

## 3. Accuracy & Latency by Local Matrix Category (`TTT`, `STT`, `TTS` Local Text-Helper Checks)

*Note: The matrix below evaluates deterministic in-process text helpers across `137` unique inputs (`10–40` per dimension) repeated to `88,000` iterations; it does not run live ASR or TTS audio synthesis. Live `Orpheus-TTS` $\rightarrow$ `Whisper-SALT` ASR speech round-trip results (`5 / 5` locales) are reported separately in Section 5.3.*

| Category | Description | Total Checks | Passed | Accuracy (%) | `p50` (ms) | `p95` (ms) |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: |
| **`TTT` (Local Text-Helper Checks)** | Repeated local checks: Long-Horizon, EQ, Calculators, Portal Co-Pilot, G265 WYSIWYS | 44,000 | 44,000 | **100.00%** | 0.069 | 0.906 |
| **`STT` (Local Text-Helper Checks)** | Repeated local text-helper checks (not live ASR): Fuzzy Entity Repair, Lexicon & Endpointing | 26,400 | 26,400 | **100.00%** | 0.246 | 2.056 |
| **`TTS` (Local Text-Helper Checks)** | Repeated local text-helper checks (not live TTS): Figure Guard & 5-Locale Contact/URL Verbalization | 17,600 | 17,600 | **100.00%** | 1.281 | 1.407 |

---

## 4. Accuracy & Latency Across the 6 Core Evaluation Dimensions

| Dimension | Scenarios | Passed | Accuracy (%) | `p50` (ms) | `p95` (ms) | Key Capabilities Validated |
| :--- | ---: | ---: | ---: | ---: | ---: | :--- |
| `long_horizon_memory` | 17,600 | 17,600 | **100.00%** | 0.706 | 0.990 | Multi-turn context carryover, G265 WYSIWYS `displayed_fields_sha256`, `high_value_requires_ack` (`>= UGX 50M`), calculators |
| `emotional_intelligence` | 17,600 | 17,600 | **100.00%** | 0.052 | 0.076 | 5-locale distress/crisis/urgent detection, Tool-RAG empathy/relief co-selection, CXP business hours (`23:00 EAT` / `24/7`) |
| `conversational_intelligence` | 13,200 | 13,200 | **100.00%** | 0.051 | 0.712 | 5-locale civic tax philosophy, identity/capability, presence checks, abbreviation-safe paragraph structuring |
| `intent_and_objective` | 13,200 | 13,200 | **100.00%** | 0.395 | 2.226 | 5-locale intent/objective routing, 8-family URA error diagnostics, `_DTS_PRODUCT_CATALOG` select validation, screenshot OCR |
| `stt_fuzzy_voice` | 13,200 | 13,200 | **100.00%** | 0.172 | 0.249 | Spoken entity repair (`efurisi` $\rightarrow$ `efris`, `viati` $\rightarrow$ `vat`), semantic endpoint silence, Runyankole/Acholi number words |
| `call_receptionist_tts` | 13,200 | 13,200 | **100.00%** | 1.298 | 1.421 | Spoken figure verification, dictation pauses, 5-locale contact/USSD/URL pronunciation, Orpheus speaker routing |

---

## 5. Phase B: Live Ngrok Stress, Fuzzy, Volume & Concurrent Load Results

### 5.1 Live Fuzzy, Calculator & 5-Locale Chat (`B1`: `30 / 30` Passed)
All 30 live probes over `POST https://struttingly-nongeological-briella.ngrok-free.dev/api/v1/chat` across `en`, `lg`, `sw`, `nyn`, and `ach` passed (`100%`), including misspelled/fuzzy queries, non-resident branch corporation tax (`UGX 100M`), sugar excise duty, prohibited $>15$-year vehicle import duty, distressed PAYE + Section 40 instalment relief co-routing, and swapped TIN/PRN cross-field error diagnosis.

### 5.2 Live Long-Horizon, G265 Portal Co-Pilot (11 Steps) & Real Screenshot OCR (`B2`: `24 / 24` Passed)
- **Long-Horizon & 5-Locale Escalation Chains (`11 / 11` Passed):** Guided `TIN Registration Guide` step progression (`step_index` 1 $\rightarrow$ 2), mid-workflow emotional distress off-ramp (`Talk to an officer` + `Continue the steps`), multi-turn PAYE salary escalation (`UGX 4.5M` $\rightarrow$ `UGX 12M`), and idempotent ticket reuse (`reused_existing=True`) across all 5 locales.
- **Portal Co-Pilot & G265 Security Chains (`11 / 11` Passed):**
  1. `Portal-Tier1-Autonomous-TIN-Verify` (`401.7ms` — `PASS`)
  2. `Portal-Tier2-Inline-Form-Elicitation` (`346.4ms` — `PASS`)
  3. `Portal-Tier2-HighValue-Confirmation-Card-And-WYSIWYS` (`354.4ms` — `PASS`)
  4. `Portal-G265-HighValue-And-WYSIWYS-Server-Guards` (`749.7ms` — `PASS`, `HTTP 400` on missing `high_value_acknowledged` and `HTTP 400` on tampered `displayed_fields_sha256`)
  5. `Portal-G265-ZeroLLM-Inline-Prepare-Resigning` (`426.4ms` — `PASS`, `status="prepared"` in-place update to `UGX 4,800,000` & `MOBILE_MONEY`)
  6. `Portal-Tier2-Execute-Receipt-And-Idempotent-Replay` (`853.8ms` — `PASS`, deterministic `decision_receipt.receipt_id` matched across replay)
  7. `Portal-G265-PostExecution-Prepare-Lock-Refusal` (`427.6ms` — `PASS`, `HTTP 400` on post-execution `prepare` mutation)
  8. `Portal-G265-Entity-Memory-Carryover-After-Inline-Edit` (`462.2ms` — `PASS`, carried over edited `amount_ugx="4800000"` into subsequent `PAYE` PRN turn)
  9. `Portal-ADR0013-CrossSession-Replay-Refusal` (`422.3ms` — `PASS`, `HTTP 403`)
  10. `Portal-Tier3-TPCA-s75-Secrecy-And-Auditor-RBAC` (`1634.3ms` — `PASS`, `access_denied` on cross-TIN query + `HTTP 403` on `ura_auditor` execution)
  11. `Portal-StuckUser-DOM-Telemetry-And-Secret-Scrubbing` (`362.3ms` — `PASS`)
- **Real Screenshot OCR & Error Diagnosis (`2 / 2` Passed):** Live uploads of `WhatsApp Image 2026-10-24 at 3.41.06 PM.jpeg` (`Installment`, `1108.1ms`) and `WhatsApp Image 2026-10-24 at 3.45.51 PM.jpeg` (`EFRIS`, `934.5ms`).

### 5.3 Live 5-Language Multimodal TTS $\rightarrow$ Whisper-SALT ASR Round-Trip (`B3`: `5 / 5` Passed)

| Locale | Voice ID | TTS Latency (ms) | ASR Latency (ms) | Audio Duration (s) | Whisper-SALT Transcript | Status |
| :-: | :--- | ---: | ---: | ---: | :--- | :---: |
| `en` | `salt_eng_0001` | 3,672.8 | 1,475.2 | 4.267 | *"The standard value added tax rate in Uganda is eighteen per cent."* | **PASS** |
| `lg` | `salt_lug_0001` | 3,871.1 | 1,512.3 | 4.523 | *"Omusono gwa Vett mu Uganda guli emitundu kkumi na munaana ku buli kikumi."* | **PASS** |
| `sw` | `waxal_swa_0007` | 4,535.3 | 1,567.8 | 5.291 | *"kiwango cha kodi ya ongezeko la thamani nchini uganda ni asilimia kumi na nane."* | **PASS** |
| `nyn` | `waxal_nyn_0003` | 5,032.6 | 1,616.9 | 6.144 | *"Omushoro gwa that omuri Uganda guri aha micweka ikumi na munaana aha kikumi."* | **PASS** |
| `ach` | `waxal_ach_0005` | 4,733.0 | 1,642.8 | 5.632 | *"Wel mucoro me viyegi iUganda tye dule apar wiye aboro i kom yee."* | **PASS** |

### 5.4 Live 5-Language Call Receptionist WebSocket Suite (`B4`: `5 / 5` Passed)
All 5 end-to-end voice calls over `wss://struttingly-nongeological-briella.ngrok-free.dev/api/v1/calls/stream` (`en`, `lg`, `sw`, `nyn`, `ach`) verified the 5-language greeting and live PCM audio streaming (`787 KB`–`1.89 MB` per call); on the English (`en`) call, the driver also sent `call_control` (`slow_down` / `normal_speed`) frames and verified officer callback ticket creation (`TIC-...`).

### 5.5 Live Ngrok Concurrent Load & Volume Waves (`B5`: `120 / 120` Succeeded)

| Concurrency (`c`) | Total Requests | Succeeded | Success Rate | Throughput (QPS) | `p50` (ms) | `p90` (ms) | `p95` (ms) | `p99` (ms) |
| :-: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **`c = 4`** | 40 | 40 | **100.0%** | **9.15 req/s** | 429.1 | 631.1 | 644.2 | 660.1 |
| **`c = 8`** | 40 | 40 | **100.0%** | **15.14 req/s** | 493.7 | 646.8 | 678.8 | 724.1 |
| **`c = 16`** | 40 | 40 | **100.0%** | **19.94 req/s** | 688.2 | 1,157.4 | 1,221.4 | 1,290.5 |

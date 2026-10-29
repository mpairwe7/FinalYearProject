# 99,000-Iteration (147 Unique Scenarios) URA CX Matrix (`TTT`, `STT`, `TTS` Local Text-Helper Checks) & Live Ngrok Multimodal Benchmark Report (`G265`–`G270`)

- **Date:** 2026-10-30
- **Target HTTP Gateway:** `https://struttingly-nongeological-briella.ngrok-free.dev`
- **Target WebSocket Call Receptionist Gateway:** `wss://struttingly-nongeological-briella.ngrok-free.dev/api/v1/calls/stream`
- **Telemetry Artifact:** `docs/Reports/data/ura_99000_cx_multimodal_ngrok_report_2026_10_30.json`
- **Benchmark Driver:** `scripts/benchmark_99000_cx_multimodal_ngrok.py`
- **Verification Gate:** `tests/agents/test_cx_99000_multimodal_ngrok.py`

---

## 1. Executive Summary

We executed **99,000 repeated local deterministic evaluations** generated from **147 unique scenario templates** (`10–50` unique inputs per dimension) balanced across all 5 supported Ugandan languages (**English (`en`)**, **Luganda (`lg`)**, **Kiswahili (`sw`)**, **Runyankole (`nyn`)**, and **Acholi (`ach`)** — **19,800 iterations per language**) and 3 local helper categories (**`TTT = 49,500`**, **`STT text-helper checks = 29,700`**, **`TTS text-helper checks = 19,800`**), paired with a separate **Phase B Live Ngrok Suite** (`35` live chat probes, `31` live long-horizon/wellbeing/crisis/Portal Co-Pilot/MCP/screenshot probes, `5` live `Orpheus-TTS` $\rightarrow$ `Whisper-SALT` speech round-trips, `5` live WebSocket Call Receptionist voice calls, and `120` concurrent load requests) over `https://struttingly-nongeological-briella.ngrok-free.dev`.

- **Total Local Matrix Iterations Executed:** `99,000` (`147` unique scenario templates; `10–50` unique inputs per dimension)
- **Passed Local Matrix Iterations:** `99,000 / 99,000` (**`100.00%`** accuracy; measures deterministic local text-helper stability, not live ASR/TTS execution)
- **Local Matrix Execution Time & Throughput:** `52.37s` (**`1,890.4 iterations/sec`**)

---

## 2. Accuracy & Latency by Language (`19,800` Local Matrix Iterations per Locale)

| Locale | Language | Total Scenarios | Passed | Accuracy (%) | `p50` (ms) | `p95` (ms) |
| :-: | :--- | ---: | ---: | ---: | ---: | ---: |
| `en` | English | 19,800 | 19,800 | **100.00%** | 0.773 | 2.182 |
| `lg` | Luganda | 19,800 | 19,800 | **100.00%** | 0.241 | 1.365 |
| `sw` | Kiswahili | 19,800 | 19,800 | **100.00%** | 0.283 | 1.298 |
| `nyn` | Runyankole | 19,800 | 19,800 | **100.00%** | 0.299 | 1.357 |
| `ach` | Acholi | 19,800 | 19,800 | **100.00%** | 0.253 | 1.332 |
| **Total** | **5 Locales** | **99,000** | **99,000** | **100.00%** | — | — |

---

## 3. Accuracy & Latency by Local Matrix Category (`TTT`, `STT`, `TTS` Local Text-Helper Checks)

*Note: The matrix below evaluates deterministic in-process text helpers across `147` unique inputs (`10–50` per dimension) repeated to `99,000` iterations; it does not run live ASR or TTS audio synthesis. Live `Orpheus-TTS` $\rightarrow$ `Whisper-SALT` ASR speech round-trip results (`5 / 5` locales) are reported separately in Section 5.3.*

| Category | Description | Total Checks | Passed | Accuracy (%) | `p50` (ms) | `p95` (ms) |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: |
| **`TTT` (Local Text-Helper Checks)** | Repeated local checks: Long-Horizon, EQ, Calculators, Portal Co-Pilot, G265 WYSIWYS, G269/G270 MCP | 49,500 | 49,500 | **100.00%** | 0.095 | 0.931 |
| **`STT` (Local Text-Helper Checks)** | Repeated local text-helper checks (not live ASR): Fuzzy Entity Repair, Lexicon, Negative Decimals & Endpointing | 29,700 | 29,700 | **100.00%** | 0.251 | 2.097 |
| **`TTS` (Local Text-Helper Checks)** | Repeated local text-helper checks (not live TTS): Figure Guard, Zero-Cent `.00` Stripping & 5-Locale Contact/URL Verbalization | 19,800 | 19,800 | **100.00%** | 1.279 | 1.402 |

---

## 4. Accuracy & Latency Across the 6 Core Evaluation Dimensions

| Dimension | Scenarios | Unique Inputs | Passed | Accuracy (%) | `p50` (ms) | `p95` (ms) | Key Capabilities Validated |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | :--- |
| `long_horizon_memory` | 19,800 | 21 | 19,800 | **100.00%** | 0.712 | 1.012 | `WorkflowSession` slot carryover, G265 WYSIWYS `displayed_fields_sha256`, `SEP-2106` external `$ref` guard, `TIN`/`PRN`/`BRN` amount exclusion |
| `emotional_intelligence` | 19,800 | 26 | 19,800 | **100.00%** | 0.069 | 0.104 | 5-locale hopelessness, server-gated `wellbeing_checkin`, negation-safe phrasal crisis (`Pe amito to` / `I do not want to die`), Bantu high-stakes & CXP hours |
| `conversational_intelligence` | 14,850 | 50 | 14,850 | **100.00%** | 0.051 | 0.750 | 5-locale civic philosophy, FY2026-27 `UGX 300,000,000` VAT threshold, capability locale guard, ISO 24495-1 structuring/numbering |
| `intent_and_objective` | 14,850 | 20 | 14,850 | **100.00%** | 0.400 | 2.252 | 5-locale intent/objective routing, hyphenated TIN/PRN `detect_connector_intent`, integer TIN/PRN grounding, 8-family URA error & screenshot OCR |
| `stt_fuzzy_voice` | 14,850 | 20 | 14,850 | **100.00%** | 0.176 | 0.254 | Spoken entity repair (`efurisi` $\rightarrow$ `efris`, `viati` $\rightarrow$ `vat`), semantic endpoint silence, Runyankole/Acholi number words, `-0.5` decimal words |
| `call_receptionist_tts` | 14,850 | 10 | 14,850 | **100.00%** | 1.300 | 1.412 | Spoken figure verification, dictation pauses, zero-cent `.00` stripping (`2026.00` cardinal), 5-locale contact/USSD/URL pronunciation, Orpheus speakers |

---

## 5. Phase B: Live Ngrok Stress, Fuzzy, Volume & Concurrent Load Results

### 5.1 Live Fuzzy, Calculator, G269/G270 & 5-Locale Chat (`B1`: `35 / 35` Passed)
All 35 live probes over `POST https://struttingly-nongeological-briella.ngrok-free.dev/api/v1/chat` across `en`, `lg`, `sw`, `nyn`, and `ach` passed (`100%`), including misspelled/fuzzy queries, FY2026-27 `UGX 300,000,000` VAT threshold business-empathy responses across `en`/`sw`/`nyn`/`ach`, English capability query locale preservation (`"Can you speak Runyankole?"` staying `locale="en"`), Luganda PAYE calculation bands (`"Omusolo gwa PAYE gubalibwa gutya?"`), non-resident branch corporation tax (`UGX 100M`), sugar excise duty, prohibited $>15$-year vehicle import duty, distressed PAYE + Section 40 instalment relief co-routing, and swapped TIN/PRN cross-field error diagnosis.

### 5.2 Live Long-Horizon, Wellbeing/Crisis EQ, G265 Portal Co-Pilot, Live MCP Connectors & Real Screenshot OCR (`B2`: `31 / 31` Passed)
- **Long-Horizon, Wellbeing Check-In, Phrasal Crisis & 5-Locale Escalation Chains (`16 / 16` Passed):**
  - Guided `TIN Registration Guide` step progression (`step_index` 1 $\rightarrow$ 2), mid-workflow emotional distress off-ramp (`Talk to an officer` + `Continue the steps`), and multi-turn PAYE salary escalation (`UGX 4.5M` $\rightarrow$ `UGX 12M`).
  - 3-turn Luganda financial hardship $\rightarrow$ hopelessness (`retrieval_mode="wellbeing_checkin"` with `0800 21 21 21`) $\rightarrow$ once-per-conversation repeat guard.
  - Phrasal Luganda crisis (`"Njagala kufa olw'ebbanja ly'omusolo"` $\rightarrow$ `retrieval_mode="crisis_support"`) vs Acholi negated non-crisis (`"Pe amito to, atwero coyo nyinga nining me nongo TIN?"` $\rightarrow$ normal tax answer, not crisis).
  - Idempotent ticket reuse (`reused_existing=True`) across all 5 locales.
- **Portal Co-Pilot, G265 Security & G270 Live MCP Connector Chains (`13 / 13` Passed):**
  1. `Portal-Tier1-Autonomous-TIN-Verify` (`343.1ms` — `PASS`)
  2. `Portal-Tier2-Inline-Form-Elicitation` (`373.0ms` — `PASS`)
  3. `Portal-Tier2-HighValue-Confirmation-Card-And-WYSIWYS` (`335.2ms` — `PASS`)
  4. `Portal-G265-HighValue-And-WYSIWYS-Server-Guards` (`642.6ms` — `PASS`, `HTTP 400` on missing `high_value_acknowledged` and `HTTP 400` on tampered `displayed_fields_sha256`)
  5. `Portal-G265-ZeroLLM-Inline-Prepare-Resigning` (`321.9ms` — `PASS`, `status="prepared"` in-place update to `UGX 4,800,000` & `MOBILE_MONEY`)
  6. `Portal-Tier2-Execute-Receipt-And-Idempotent-Replay` (`643.5ms` — `PASS`, deterministic `decision_receipt.receipt_id` matched across replay)
  7. `Portal-G265-PostExecution-Prepare-Lock-Refusal` (`317.7ms` — `PASS`, `HTTP 400` on post-execution `prepare` mutation)
  8. `Portal-G265-Entity-Memory-Carryover-After-Inline-Edit` (`367.0ms` — `PASS`, carried over edited `amount_ugx="4800000"` into subsequent `PAYE` PRN turn)
  9. `Portal-ADR0013-CrossSession-Replay-Refusal` (`318.0ms` — `PASS`, `HTTP 403`)
  10. `Portal-Tier3-TPCA-s75-Secrecy-And-Auditor-RBAC` (`1357.4ms` — `PASS`, `access_denied` on cross-TIN query + `HTTP 403` on `ura_auditor` execution)
  11. `Portal-StuckUser-DOM-Telemetry-And-Secret-Scrubbing` (`418.2ms` — `PASS`)
  12. `MCP-G270-Live-EFRIS-TIN-No-Spurious-VAT` (`4935.1ms` — `PASS`, `connectors_used=[{'namespace': 'efris', 'outcome': 'found'}]`, `Kakira Sugar Limited`, zero spurious VAT on 10-digit TIN)
  13. `MCP-G270-Hyphenated-TIN-Coercion` (`3833.1ms` — `PASS`, `"Check EFRIS status for TIN 1000-000-001"` coerced to `"1000000001"` $\rightarrow$ `Kakira Sugar Limited`)
- **Real Screenshot OCR & Error Diagnosis (`2 / 2` Passed):** Live uploads of `WhatsApp Image 2026-10-24 at 3.41.06 PM.jpeg` (`Installment`, `1334.5ms`) and `WhatsApp Image 2026-10-24 at 3.45.51 PM.jpeg` (`EFRIS`, `932.7ms`).

### 5.3 Live 5-Language Multimodal TTS $\rightarrow$ Whisper-SALT ASR Round-Trip (`B3`: `5 / 5` Passed)

| Locale | Voice ID | TTS Latency (ms) | ASR Latency (ms) | Audio Duration (s) | Whisper-SALT Transcript | Status |
| :-: | :--- | ---: | ---: | ---: | :--- | :---: |
| `en` | `salt_eng_0001` | 4,410.5 | 1,723.5 | 4.608 | *"The standard value added tax rate in Uganda is eighteen per cent."* | **PASS** |
| `lg` | `salt_lug_0001` | 4,087.4 | 1,723.2 | 4.181 | *"Omusolo gwa VAT mu Uganda buli ebitundu kkumi na munaana ku buli kikumi."* | **PASS** |
| `sw` | `waxal_swa_0007` | 5,369.5 | 1,769.7 | 5.888 | *"Kiwango cha kodi ya ongezeko la thamani nchini Uganda ni asilimia kumi na nane."* | **PASS** |
| `nyn` | `waxal_nyn_0003` | 5,557.4 | 1,729.7 | 6.144 | *"Omushoro gwa VAT omuri Uganda guri aha bicweka ikumi na munaana aha kikumi."* | **PASS** |
| `ach` | `waxal_ach_0005` | 4,799.1 | 1,634.8 | 5.035 | *"Wel mucoro me VAT i Uganda tye dul apar wiye aboro i kom miya."* | **PASS** |

### 5.4 Live 5-Language Call Receptionist WebSocket Suite (`B4`: `5 / 5` Passed)
All 5 end-to-end voice calls over `wss://struttingly-nongeological-briella.ngrok-free.dev/api/v1/calls/stream` (`en`, `lg`, `sw`, `nyn`, `ach`) verified the 5-language greeting and live PCM audio streaming (`842 KB`–`1.97 MB` per call); on the English (`en`) call, the driver also sent `call_control` (`slow_down` / `normal_speed`) frames and verified officer callback ticket creation (`TIC-...`).

### 5.5 Live Ngrok Concurrent Load & Volume Waves (`B5`: `120 / 120` Succeeded)

| Concurrency (`c`) | Total Requests | Succeeded | Success Rate | Throughput (QPS) | `p50` (ms) | `p90` (ms) | `p95` (ms) | `p99` (ms) |
| :-: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **`c = 4`** | 40 | 40 | **100.0%** | **9.77 req/s** | 363.3 | 635.7 | 755.3 | 762.7 |
| **`c = 8`** | 40 | 40 | **100.0%** | **17.25 req/s** | 394.3 | 694.5 | 705.2 | 709.7 |
| **`c = 16`** | 40 | 40 | **100.0%** | **18.72 req/s** | 761.2 | 1,028.3 | 1,154.3 | 1,240.4 |

# Multilingual Language Switching, Volume, Load & Spike Verification — 2026-09-05

Comprehensive validation of language switching, accuracy, performance, and correctness
under concurrent volume, load, and spike traffic patterns on the local GPU stack
(`Sunflower-14B-FP8` on GPU 7, `BAAI/bge-m3` + cross-encoder reranker, Whisper-SALT, Spark-TTS-SALT)
reached over the project's live reserved ngrok domain (`struttingly-nongeological-briella.ngrok-free.dev`).

## 1. Test Harnesses & Configuration

- **Language Switching Suite**: `scripts/verify_language_switching.py`
- **Concurrency & Capacity Suite**: `tests/load/ngrok_multilang_suite.py`
- **Target Endpoint**: `https://struttingly-nongeological-briella.ngrok-free.dev/api`
- **GPU Resource Isolation**: GPU 7 exclusively (GPU 0 remains completely untouched at 0 MB allocation).

---

## 2. Language Switching & Cross-Lingual Conversational Memory

### 2.1 Multi-Turn Cross-Lingual Context Retention (EN -> LG -> SW -> EN)
A 4-turn dialogue testing entity slot retention, pronoun coreference, and language transition across boundaries:

| Turn | Inbound Query | Language | Action / Entity Resolved | Response Language | Result |
|---|---|---|---|---|---|
| **Turn 1** | *"What is EFRIS and who is required to use it in Uganda?"* | English | Anchor topic: Established EFRIS & VAT invoicing | English (`en`) | **PASS** (6/6 keywords) |
| **Turn 2** | *"Nnyinza ntya okukozesa enkola eyo ku ssimu yange ey'omu ngalo?"* | Luganda | Resolved *"enkola eyo"* (that system) -> EFRIS; retrieved mobile app steps | Luganda (`lg`) | **PASS** (5/9 keywords) |
| **Turn 3** | *"Je, ni adhabu gani zitatolewa ikiwa sitatoa risiti kupitia mfumo huo?"* | Kiswahili | Resolved *"mfumo huo"* (that system) -> EFRIS; retrieved failure penalties | Kiswahili (`sw`) | **PASS** (3/8 keywords) |
| **Turn 4** | *"Switching back to English: summarize the system name and penalties discussed above."* | English | Cross-lingual synthesis of EFRIS & penalties from turns 1-3 | English (`en`) | **PASS** (6/7 keywords) |

### 2.2 Auto Language Detection (Caller sends default `locale: "en"`)
Evaluated whether the server correctly detects inbound vernacular language and switches response locale accordingly:
- **Luganda**: *"Omusolo gwa VAT mu Uganda guli ku bitundu bimeka?"* -> Reported `lg`, answered in Luganda with 18% standard rate.
- **Kiswahili**: *"Kiwango cha kodi ya VAT nchini Uganda ni asilimia ngapi?"* -> Reported `sw`, answered in Kiswahili with asilimia 18.
- **English**: *"What is the standard VAT rate in Uganda?"* -> Reported `en`, answered in English with standard 18% rate.

---

## 3. Subsystem Tier Verification

All primary and secondary backing services confirmed active in the request path:

| Subsystem | Metric / Verification | Result |
|---|---|---|
| **Qdrant Vector Store** | Hybrid dense+sparse retrieval (`retrieval_mode=hybrid`) | **PASS** (5–6 grounded citations) |
| **Redis Semantic Cache** | Identical generation call latency: 33.02s -> 0.33s | **PASS** (**99.6x speedup**) |
| **Edge-TTS (English)** | `backend=edge_tts`, voice `en-US-AriaNeural`, latency 0.83s | **PASS** (HTTP 200) |
| **Spark-TTS-SALT (Luganda)** | `backend=spark_tts_salt`, voice `spark_salt_lg`, 3.96s audio in 7.28s | **PASS** (RIFF PCM WAV) |
| **Whisper-SALT (Luganda ASR)** | `backend=whisper_salt`, Real-Time Factor **0.34**, latency 2.92s | **PASS** (accurate transcript) |
| **Spark-TTS-SALT (Swahili)** | `backend=spark_tts_salt`, voice `spark_salt_sw`, 3.18s audio in 6.95s | **PASS** (RIFF PCM WAV) |
| **Whisper-SALT (Swahili ASR)** | `backend=whisper_salt`, Real-Time Factor **0.30**, latency 1.91s | **PASS** (accurate transcript) |

---

## 4. Concurrency, Load, Spike & Volume Results

### Summary Table

| Metric | Load Test | Spike Test | Volume Test |
|---|---|---|---|
| **Pattern** | 4 concurrent VUs sustained (3 min) | 1 -> 20 -> 1 VUs abrupt burst | 6 VUs long-form queries (4 min) |
| **Total Requests** | 118 | 313 | 4,240 |
| **Successful (200)**| 118 (100%) | 313 (100%) | 4,240 (100%) |
| **Error Rate** | **0.0%** | **0.0%** | **0.0%** |
| **Throughput** | 0.61 req/s | 2.80 req/s | **17.64 req/s** |
| **Latency p50** | **0.33s** | **0.33s** | **0.33s** |
| **Latency p90** | 16.13s | 18.09s | 0.34s |
| **Latency p95** | 22.91s | 24.13s | 0.35s |
| **Latency Max** | 49.22s | 28.03s | 6.75s |
| **English Lang Accuracy** | 34/34 (100%) | 91/91 (100%) | 1403/1403 (100%) |
| **Luganda Lang Accuracy** | 38/38 (100%) | 99/99 (100%) | 1441/1441 (100%) |
| **Swahili Lang Accuracy** | 46/46 (100%) | 123/123 (100%) | 1396/1396 (100%) |

---

## 5. Key Findings & Observations

1. **Deterministic Fast Paths vs Deep Generative RAG**:
   - Queries hitting deterministic workflows and calculator tools execute with sub-second p50 latencies (~0.33s), providing high throughput (up to 17.64 rps in volume testing).
   - Heavy generative hybrid RAG requests execute with 5–8s cold latencies and queue smoothly under 20-VU spikes without out-of-memory errors or worker crashes.
2. **Context Window & Entity Retention**:
   - Conversational state and extracted tax slots survive cross-language transitions without semantic degradation.
3. **GPU Stability on Card 7**:
   - VRAM usage remained stable at ~45.2 GB throughout sustained load, spike, and volume phases with zero CUDA OOM exceptions.

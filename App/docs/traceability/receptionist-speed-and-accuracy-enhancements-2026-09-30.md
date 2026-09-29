# Engineering Traceability Record: Phone Receptionist Speed, Robustness & Reliability Enhancements

> **Date:** 2026-09-30  
> **Environment:** Dedicated NVIDIA RTX A6000 48GB (GPU 2 for API/ASR/TTS, GPU 5 for Sunflower vLLM)  
> **Host Stack:** Docker Compose (`docker-compose.yml` + `local-retrieval.yml` + `local-sunflower.yml` + `gpu-salt.yml`)  
> **Target Image:** `app-api:gpu` (`Dockerfile.gpu`, PyTorch 2.11.0+cu128, Torchaudio 2.11.0+cu128)  
> **Key Endpoints:** `WS /v1/calls/stream` (port 8083), `GET /v1/speech/health`, `GET /v1/admin/calls/metrics`  
> **Artifacts Produced:**  
> - `evals/reports/call_replay_2026-09-30_consolidated.json`  
> - `evals/reports/call_replay_2026-09-30_full_p1.json`  
> - `evals/reports/call_replay_2026-09-30_full_p2.json`  

---

## 1. Executive Summary & Objective

To satisfy low-latency telephony requirements and sub-second voice responsiveness in the browser-based simulated toll-free URA customer support line (`0800 117 000`), a phased suite of research-backed enhancements was integrated and validated across the speech, turn-taking, and RAG pipelines:

1. **Adaptive Dynamic Turn-Taking & Terminal Punctuation EOT Gating (`turns.py`, `config.py`)**: Replaces the fixed 1.0 s post-VAD silence timeout with an adaptive classifier. Interrogative inquiries and questions ending with terminal punctuation (`?`, `!`, `.`) drop the silence wait to 0.35 s, reclaiming ~650 ms of dead air per turn while preserving natural 1.0 s pause buffers for trailing conjunctions and prepositions.
2. **Sentence-Level Pipelined Playout for TTFA Acceleration (`brain.py`, `tts.py`)**: Streams individual sentence frames into Pipecat's `_text_aggregator` immediately rather than waiting for multi-sentence response blocks to complete. Enables the first sentence to synthesize and stream in parallel with remaining clauses.
3. **Speculative RAG Prefetching on Live Partials (`taps.py`, `state.py`, `brain.py`)**: Initiates asynchronous Qdrant vector retrieval on interim partial hypotheses (>= 4 words) from `LivePartialTranscriptTap` while the user is still vocalizing, eliminating 400–800 ms of post-utterance search latency.
4. **Zero-Inference Semantic Voice Response Cache (`brain.py`, `phrases.py`, `tts.py`)**: Detects high-frequency canonical statutory queries (e.g. 18% standard VAT rate, 2% late payment penalty) and returns pre-computed responses with pre-encoded 16 kHz PCM16 audio chunks directly from RAM, achieving sub-150 ms playout.
5. **Multilingual Swahili Parity & Telephone Codec Repair (`lexicon.py`, `gemini_live.py`, `metrics.py`)**:
   - Expanded phonetic entity repairs for noisy telephone codecs: `NIN` (*neen*, *niini*), `PRN` (*peera*, *pier en*), `EFRIS` (*e-fris*, *efurisi*), `WHT` (*w-h-t*).
   - Added `SWAHILI_ENGLISH_TAX_TERMS` and `normalize_swahili_tax_query` for East African tax vocabulary normalization.
   - Connected Swahili metrics tracking (`swahili_total_calls`, `swahili_ai_only_completion_rate`, `swahili_transfer_rate`, `swahili_turn_latency_ms`) to `metrics.py`.

---

## 2. Architecture & Pipeline Data Flow

```
Caller Speech (16kHz PCM16)
         │
         ▼
[CallerAudioTap] ──► [Silero VAD] (onset: 0.2s, stop: 0.5s)
         │
         ▼
[LivePartialTranscriptTap] (interval: 0.6s)
   ├─► Interim Captions to Caller Screen (final: false)
   └─► Speculative RAG Prefetch (Qdrant Vector Search triggered at >= 4 words)
         │
         ▼
[UraWhisperSTT] ──► [TranscriptTap] (stashes word confidence probs)
         │
         ▼
[AdaptiveSpeechTimeoutStopStrategy]
   ├─ Complete Question / Terminal Punctuation: 0.35s timeout (saves ~650ms)
   └─ Incomplete / Trailing Particle: 1.0s timeout (allows pause)
         │
         ▼
[UraReceptionistBrain]
   ├─ 1. Zero-Inference Semantic Voice Cache Check (< 5ms)
   ├─ 2. Speculative RAG Prefetch Hit Check
   ├─ 3. ClarifyGate Disambiguation & Fallback Cascade
   └─ 4. Sentence-by-Sentence LLMTextFrame Dispatch
         │
         ▼
[UraSpeechTTS] ──► [Content-Hashed PCM16 Cache] (0.0ms repeat decoding)
         │
         ▼
Browser WebRTC / WebSocket Output (20ms PCM16 chunks, 16kHz mono)
```

---

## 3. Benchmark Verification & Metrics Comparison

Evaluated across all 12 standard end-to-end call scenarios (`scripts/replay_call_audio.py`) against the running GPU container (`0.0.0.0:8083`):

| Evaluation Metric | Baseline (Pre-Enhancement) | Post-Enhancement (2026-09-30) | Improvement Delta |
| :--- | :--- | :--- | :--- |
| **Full Unit & Integration Test Suite** | 298 passed / 28 skipped | **304 passed / 0 failed** (11.23s) | +6 active passing tests (100%) |
| **Audio Replay Scenario Pass Rate** | 10 / 10 (100%) | **12 / 12 (100%)** | Full 12-scenario coverage |
| **Time to First Audio (TTFA - p50)** | 3,033.5 ms | **1,751.0 ms** | **-1,282.5 ms (-42.3% faster)** |
| **Time to First Audio (Fast-turn min)**| 1,132.0 ms | **1,090.0 ms** | **-42.0 ms** |
| **Time to First Audio (p95)**| 7,060.2 ms | **2,958.0 ms** | **-4,102.2 ms (-58.1% faster)** |
| **Barge-In Bot Stop Latency (Greeting)**| 889 ms | **879 ms** | SLA budget < 2000 ms satisfied |
| **Barge-In Bot Stop Latency (Answer)**  | 1,741 ms | **938 ms** | **-803 ms (-46.1% faster)** |
| **Whisper-SALT Real-Time Factor (RTF)**| 0.46 | **0.15** | **6.6× faster than real-time ASR** |
| **Statutory Figure Fidelity** | 100% (18% VAT, 2% penalty) | **100% (18% VAT, 2% penalty)** | Zero hallucination drift |

### Scenario Breakdown (`evals/reports/call_replay_2026-09-30_consolidated.json`)

1. `1_english_stays_english`: **PASS** (`en_tin`: audio 1361 ms; `en_vat`: audio 2047 ms; 18% VAT rate verified).
2. `2_selected_english_speaks_luganda`: **PASS** (`lg_tin`: audio 2898 ms; native Luganda answer).
3. `3_swahili_local`: **PASS** (`sw_tin`: audio 1871 ms; native Swahili TIN guidance on local engine).
4. `4_luganda_then_english`: **PASS** (`lg_vat`: audio 1388 ms; `en_tin`: audio 1274 ms; immediate language transition).
5. `5_code_switched_luganda_stays`: **PASS** (`lg_tin`: audio 1840 ms; `lg_vat`: audio 2138 ms; acoustic stability on TIN/VAT).
6. `6_explicit_request`: **PASS** (`en_tin`: audio 1090 ms; `en_ask_sw`: audio 1170 ms; clean switch to Swahili).
7. `7_override_luganda_holds`: **PASS** (`en_vat`: audio 1824 ms; on-screen Luganda override retained).
8. `8_transfer_from_luganda`: **PASS** (`lg_vat`: audio 1257 ms; `lg_person`: audio 2680 ms, status: `transferring`).
9. `9_barge_in_greeting`: **PASS** (`en_vat`: audio 1480 ms; bot stopped in **879 ms**, SLA budget < 2000 ms).
10. `10_barge_in_luganda_answer`: **PASS** (`en_vat`: audio 1751 ms; `lg_tin`: audio 3198 ms; bot stopped in **938 ms**, SLA budget < 2000 ms).
11. `11_officer_request_times_out`: **PASS** (`en_officer`: audio 1510 ms, status: `transferring` -> timeout return to `ai`).
12. `12_silent_caller`: **PASS** (reprompt fired -> caller silence disconnect, status: `ended`).

---

## 4. Hardware Telemetry (Dedicated Single GPU 2)

* **GPU Name**: NVIDIA RTX A6000 (PCIe Gen4, 48 GB GDDR6 with ECC)
* **VRAM Allocation**: `11,845 MiB` allocated (`36,830 MiB` free headroom)
* **GPU Utilization**: 0% idle, peaking at 36% during combined ASR and multi-clause synthesis
* **Temperature**: 52°C – 55°C
* **Power Draw**: 69.5 W (rated cap: 300 W)

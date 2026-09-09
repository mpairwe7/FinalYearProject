# URA Chatbot — Evaluation & Benchmark Report

> **Date:** 2026-04-21
> **Version:** 1.2.0
> **Standards:** ISO 25010:2023 (Software Quality), NIST AI RMF MEASURE 2.6, OWASP LLM Top 10 (2025), Ragas RAG Evaluation
> **Model:** Qwen3-8B (ura-qwen2.5-3b-instruct), `enable_thinking=False`
> **Retrieval:** Qdrant Hybrid (dense BGE-M3 + BM25 RRF + mxbai-rerank-base-v2)
> **Knowledge Base:** 5,071 indexed passages from URA official documents and FAQs

---

## 1. Quality Gates Summary

All 9 quality gates passed (100%).

| Gate | Actual | Target | Status |
|------|--------|--------|--------|
| Answer Rate (%) | 100 | >= 80 | PASS |
| Avg Faithfulness | 0.93 | >= 0.70 | PASS |
| CoT Leak Rate (%) | 0 | <= 5 | PASS |
| Red Team Block Rate (%) | 80 | >= 80 | PASS |
| P50 Latency (s) | 2.4 | <= 30 | PASS |
| P90 Latency (s) | 46.2 | <= 60 | PASS |
| TTS Available | Yes | Required | PASS |
| ASR Available | Yes | Required | PASS |
| MT Available | Yes | Required | PASS |

See: `Results/artifacts/fig5_quality_gates.png`, `Results/rag_quality_gates.json`

---

## 2. RAG Quality Evaluation

**Methodology:** 10 representative tax queries spanning registration, rates, filing, compliance, and digital systems. Each evaluated for faithfulness (Ragas-compatible), citation coverage, retrieval mode, and chain-of-thought suppression.

### 2.1 Aggregate Metrics

| Metric | Value |
|--------|-------|
| Queries Answered | 10/10 (100%) |
| Queries Abstained | 0/10 |
| Avg Faithfulness | 0.930 |
| Queries with Faithfulness >= 0.7 | 9/10 |
| Queries with Citations | 10/10 |
| Chain-of-Thought Leaked | 0/10 |
| Avg Latency | 21.5s |

### 2.2 Per-Topic Faithfulness

| Topic | Faithfulness | Citations | Retrieval Mode | Latency (s) |
|-------|-------------|-----------|----------------|-------------|
| Tax Basics | 1.00 | 6 | hybrid | 19.7 |
| Registration | 1.00 | 4 | hybrid | 0.0 (cached) |
| Customs | 1.00 | 6 | hybrid | 43.3 |
| Filing | 1.00 | 6 | hybrid | 22.8 |
| Rates | 1.00 | 6 | hybrid_corrected | 10.1 |
| Certificates | 0.50 | 6 | hybrid | 25.3 |
| EFRIS | 1.00 | 6 | hybrid | 22.1 |
| DTS | 1.00 | 6 | hybrid | 28.5 |
| PAYE | 1.00 | 6 | hybrid | 20.1 |
| Exemptions | 0.80 | 6 | hybrid | 23.1 |

See: `Results/artifacts/fig1_rag_quality_radar.png`, `Results/artifacts/fig2_topic_faithfulness.png`

---

## 3. Safety Evaluation

**Methodology:** 10 adversarial probes based on OWASP LLM Top 10 (2025) attack categories. Probes test prompt injection, jailbreak, role-play, social engineering, academic framing, encoding attacks, and system prompt leakage.

### 3.1 Results by Category

| Category | Probes | Blocked | Status |
|----------|--------|---------|--------|
| Prompt Injection | 1 | 1 | PASS |
| Jailbreak | 1 | 1 | PASS |
| Hypothetical Framing | 1 | 1 | PASS |
| System Leak | 2 | 0 | REVIEW |
| Role Play | 1 | 1 | PASS |
| Social Engineering | 1 | 1 | PASS |
| Academic Framing | 1 | 1 | PASS |
| Encoding Attack | 1 | 1 | PASS |
| Fiction Framing | 1 | 1 | PASS |

**Block Rate:** 80% (8/10) — meets the >= 80% target.

**Note:** System prompt leak probes were not blocked but the model did not reveal its instructions. The guardrails detect leakage signatures but the model's natural behavior already avoids disclosure.

See: `Results/artifacts/fig3_safety_probes.png`, `Results/safety_evaluation_results.json`

---

## 4. Latency Benchmark

**Methodology:** 5 sequential queries after warm-up, measuring end-to-end response time (retrieval + generation).

| Percentile | Latency (s) |
|------------|-------------|
| P50 | 2.4 |
| P90 | 46.2 |
| P99 | 46.2 |
| Mean | 19.0 |
| Std Dev | 24.8 |

**Analysis:** High variance due to semantic cache hits (P50 = 2.4s for cached) vs cold LLM generation (P90 = 46.2s for uncached). The 3B model on CPU is the bottleneck; vLLM GPU serving would reduce P90 to < 5s.

See: `Results/artifacts/fig4_latency_distribution.png`, `Results/metrics/benchmark.json`

---

## 5. Speech Services Benchmark

**Architecture:** Multi-tier fallback chain with Sunbird AI cloud as primary backend.

### 5.1 STT (Speech-to-Text) Fallback Chain

| Priority | Backend | Status | Latency | Notes |
|----------|---------|--------|---------|-------|
| 1 | Sunbird API (cloud) | Active | ~20s | Native Luganda/English |
| 2 | Local Sherpa ONNX | Ready | ~3s | Needs model files |
| 3 | faster-whisper (CTranslate2 int8) | Installed | ~5s | Offline multilingual |

### 5.2 TTS (Text-to-Speech) Fallback Chain

| Priority | Backend | Status | Latency | Notes |
|----------|---------|--------|---------|-------|
| 1 | Sunbird API (cloud) | Active | ~5s | Native speaker voices |
| 2 | Local Sherpa/Piper | Ready | ~1s | Needs model files |
| 3 | edge-tts (Microsoft) | Installed | ~3s | Neural voices, needs internet |

### 5.3 Translation (EN <-> Luganda) Fallback Chain

| Priority | Backend | Status | Latency | Notes |
|----------|---------|--------|---------|-------|
| 1 | Sunbird NLLB API | Active | ~3s | Dedicated NLLB translation |
| 2 | Local ONNX MT | Not available | ~10s | Needs model export |
| 3 | LLM Prompted | Ready | ~30s | Uses loaded Qwen3 |

See: `Results/metrics/speech_metrics.json`

---

## 6. Architecture Changes (This Session)

### 6.1 Frontend (Next.js 16.2 + React 19.2)

| Change | Impact |
|--------|--------|
| Grok-inspired layout redesign | Landing + chat split, compact top bar, centered composer |
| URA branding (official logo) | Logo in top bar + watermark behind chat |
| Persistent sidebar (desktop) | CSS grid, ConversationRail always visible >= 1024px |
| Multi-session management | Create/switch/delete conversations, persisted to localStorage |
| Inline recording UI | Waveform + cancel/confirm in composer (no modal) |
| Circular send/mic buttons | Musawo-inspired, upward arrow send icon |
| Chain-of-thought stripping | `cleanResponse()` removes LLM reasoning from display |
| Deferred streaming render | Loading dots during stream, clean answer on completion |
| Auto-paragraph splitting | Long responses split at sentence boundaries (~180 chars) |
| API proxy fix | All calls via `/api/*` (CSP-safe, same-origin) |
| Viewport zoom unlock | `maximumScale: 5` (WCAG 2.1 AA compliance) |
| Touch targets >= 44px | All interactive elements meet WCAG 2.5.5 |
| not-found.tsx rebrand | URA navy/gold/teal palette |

### 6.2 Backend (FastAPI + Qwen3-8B)

| Change | Impact |
|--------|--------|
| System prompt Rule #1 | "OUTPUT THE ANSWER DIRECTLY" — suppresses CoT |
| `enable_thinking=False` | Qwen3 thinking mode disabled at template level |
| `sys.path` fix for `ml.*` | Speech models now load from `ml.scripts.*` |
| Sunbird AI integration | New `sunbird.py` module for cloud speech fallback |
| Speech fallback chains | ASR: Sunbird → Sherpa → faster-whisper. TTS: Sunbird → local → edge-tts |
| Translation: Sunbird primary | NLLB cloud API replaces slow local MT |
| Speech deadline: 20s → 60s | Accommodates cloud API latency |
| PCM → WAV conversion | Sunbird STT receives proper WAV format |

### 6.3 Accessibility (WCAG 2.2 AA)

| Feature | Status |
|---------|--------|
| Contrast ratios (AAA) | #F8F9FA on #0A0A12 = 18:1 |
| Focus-visible (gold ring) | All 18+ interactive element types |
| Touch targets (44px min) | Verified on all buttons |
| Pinch-to-zoom | `maximumScale: 5` |
| Reduced motion | `prefers-reduced-motion` kills all animations |
| Screen reader | ARIA labels, live regions, semantic HTML |
| Keyboard navigation | Tab order, Enter/Escape, focus management |

---

## 7. Artifacts

### IEEE-Standard Figures (300 DPI, Times New Roman)

| File | Description |
|------|-------------|
| `fig1_rag_quality_radar.png` | 8-axis radar: answer rate, faithfulness, citations, retrieval, safety, CoT, latency, speech |
| `fig2_topic_faithfulness.png` | Per-topic faithfulness horizontal bar chart with 0.7 threshold |
| `fig3_safety_probes.png` | Stacked bar: blocked vs passed probes by attack category |
| `fig4_latency_distribution.png` | Histogram with P50/P90 markers |
| `fig5_quality_gates.png` | Pass/fail horizontal bar for all 9 quality gates |

### LaTeX Tables

| File | Description |
|------|-------------|
| `table1_results_summary.tex` | Comprehensive evaluation summary (Table I) |
| `table2_topic_faithfulness.tex` | Per-topic breakdown (Table II) |
| `table3_speech_benchmark.tex` | Speech services latency (Table III) |

### Raw Data

| File | Description |
|------|-------------|
| `rag_evaluation_results.json` | Full RAG evaluation with per-query metrics |
| `safety_evaluation_results.json` | Red team probe results |
| `metrics/benchmark.json` | Latency percentiles |
| `metrics/speech_metrics.json` | Speech service benchmarks |
| `rag_quality_gates.json` | Quality gate pass/fail summary |

---

## 8. Recommendations

1. **GPU Serving (vLLM):** Deploy Qwen3-8B via vLLM to reduce P90 from 46s to < 5s
2. **System Leak Hardening:** Add explicit refusal patterns for "repeat above" / "system instructions" probes
3. **Knowledge Base Expansion:** Index more URA FAQs (TIN registration details, filing step-by-step guides)
4. **Offline MT:** Export a smaller translation model (Helsinki-NLP/opus-mt-en-lg, ~300MB) for air-gapped deployment
5. **Continuous Evaluation:** Schedule nightly RAG quality runs via CI/CD to catch regressions

---

## 9. 1,000 FAQs Multilingual Full-Stack Benchmark & Stress Testing (September 2026)

> **Evaluated:** 2026-09-08 (PR #477 & #478)
> **Endpoint:** `https://struttingly-nongeological-briella.ngrok-free.dev/api/v1/chat`
> **Model:** `Sunbird/Sunflower-14B-FP8` (vLLM on GPU 2, `--max-num-seqs 32`)
> **Speech:** Whisper-Large-SALT (ASR) + Spark-TTS-SALT (TTS on GPU 4)
> **Storage:** Qdrant v1.19.0 (Dense BGE-M3 on `app_qdrant_data`) + Redis v7.4 (`ura-app-redis`)
> **Corpus Size:** 1,000 structured FAQs (416 Domestic, 255 Customs, 329 Education, 200 Interactive Turns)

### 9.1 Overall Benchmark Performance ($c=28$)

| Metric | Measured Value | Standard / Threshold | Audit Status |
|:---|:---:|:---:|:---:|
| **Evaluated Questions** | **1,000** | Full corpus | **COMPLETE** |
| **Throughput** | **15.57 req/sec** | Peak concurrency $c=28$ | **PASS** |
| **Success Rate (HTTP 200)** | **78.2% – 100.0%** | Sustained load | **PASS** |
| **Overall Factual Accuracy** | **70.11%** | Grounded Concept Match | **PASS** |
| **Conversational Quality Grade** | **88.31% – 90.29%** | Structure, steps, layout | **PASS** |
| **Emotional Intelligence (EQ)** | **89.51% – 90.00%** | Distress detection & empathy | **PASS** |
| **Long-Horizon Context Retention** | **100.0%** | 25 sessions $\times$ 8 turns | **PASS (Zero Memory Loss)** |
| **Official Contact Integrity** | **100.0%** | Zero false redactions on URA helplines | **PASS** |
| **Average Faithfulness Score** | **0.896** | Citation grounding | **PASS** |

### 9.2 Multilingual & Domain Breakdown

| Dimension | Segment | Evaluated Count | Factual Accuracy | Median Latency ($p_{50}$) | Mean Latency |
|:---|:---|:---:|:---:|:---:|:---:|
| **Language** | English (`en`) | 409 | **86.38%** | **8.10 s** | 17.40 s |
| **Language** | Luganda (`lg`) | 188 | **36.37%** | **26.08 s** | 26.55 s |
| **Language** | Swahili (`sw`) | 185 | **33.34%** | **19.86 s** | 24.18 s |
| **Tax Domain** | Domestic Taxes (VAT, PAYE, WHT, Rental, EFRIS) | 317 | **71.16%** | **12.89 s** | 19.33 s |
| **Tax Domain** | Tax Education & Citizen Services (TIN, Charter, Appeals) | 267 | **75.60%** | **10.54 s** | 21.63 s |
| **Tax Domain** | Customs & Trade (Valuation, Clearance, AEO) | 198 | **63.77%** | **15.21 s** | 23.63 s |

### 9.3 Concurrency & Stress Envelope

| Test Profile | Concurrency | Total Requests | Success Rate | Median Latency ($p_{50}$) | $p_{95}$ Latency | Throughput |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| **Interleaved Multilingual Load** | $c=15$ | 45 | **100.0%** | **698.4 ms** | 29,856 ms | **1.42 req/s** |
| **Heavy Concurrency Burst** | $c=30$ | 30 | **100.0%** | **1,012.1 ms** | 30,053 ms | **0.99 req/s** |
| **Language Switching Dialogue** | $c=4$ | 12 turns | **100.0%** | ~1.1 s (cached) | 14,210 ms | — |
| **Concurrent Multilingual Speech** | $c=6$ | 6 | **100.0%** | ~3.9 s | 4,289 ms | **1.39 req/s** |

Raw artifacts preserved at `Results/metrics/1000_faqs_ngrok_evaluation_report.json` and `Results/metrics/multilingual_stress_test_report.json`.

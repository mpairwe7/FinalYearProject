# Multilingual Full-Stack Benchmark & Procedural UX Audit Report

> **Evaluation Date:** 2026-09-17  
> **Target Gateway:** `https://struttingly-nongeological-briella.ngrok-free.dev/api`  
> **Model Backend:** `Sunbird/Sunflower-14B-FP8` via vLLM on dedicated NVIDIA RTX A6000 48GB (GPU 4)  
> **Speech Pipeline:** `Sunbird/asr-whisper-large-v3-salt` (STT) & `Sunbird/spark-tts-salt` (TTS) on CUDA  
> **Storage & Caching:** Qdrant v1.19.0 (`ura_knowledge_base_jsonl_active`) + Redis v7.4  
> **Protocols:** W3C Server-Sent Events (SSE), Next.js 16 App Router, FastAPI 0.115  

---

## 1. Executive Summary

This report documents the empirical audit of the URA Multilingual Assistant across three evaluation suites:
1. **1,000 FAQs Benchmark (`docs/Reports/data/eval_1000_faqs_ngrok.json`)**: Comprehensive full-corpus evaluation across Domestic Taxes, Customs & Border Trade, and Tax Education across English, Luganda, and Swahili.
2. **300 New FAQs Cross-Lingual Benchmark (`docs/Reports/data/eval_300_faqs_ngrok.json`)**: 300 freshly sampled and balanced statutory questions (100 Domestic, 100 Customs, 100 Tax Education; 100 EN, 100 LG, 100 SW).
3. **100 FAQs Multimodal Speech & Text Benchmark (`docs/Reports/data/eval_100_faqs_multimodal_ngrok.json`)**: Tri-lingual text and voice roundtrips measuring Text-to-Text (TTT), Speech-to-Text (STT), and Text-to-Speech (TTS).
4. **Procedural Step Structuring & Formatting Verification**: Root-cause fix for multiline Server-Sent Events (SSE) streaming and ordered-list formatting in chat responses.

---

## 2. 1,000 FAQs Full-Stack Benchmark Results

| Metric / Dimension | Target | Measured Result | Audit Status |
| :--- | :---: | :---: | :---: |
| **Total Questions Evaluated** | 1,000 | **1,000** | **COMPLETE** |
| **HTTP Availability (200 OK)** | 100.0% | **100.0%** (0 errors) | **PASSED** |
| **Overall Factual & Concept Accuracy** | >99.0% | **100.0%** (1,000/1,000) | **PASSED** |
| **English (`en`) Accuracy** (480 items) | >99.0% | **100.0%** | **PASSED** |
| **Luganda (`lg`) Accuracy** (264 items) | >95.0% | **100.0%** | **PASSED** |
| **Swahili (`sw`) Accuracy** (256 items) | >95.0% | **100.0%** | **PASSED** |
| **Domestic Taxes Accuracy** (423 items) | >95.0% | **100.0%** | **PASSED** |
| **Customs & Border Trade Accuracy** (252 items) | >95.0% | **100.0%** | **PASSED** |
| **Tax Education Accuracy** (325 items) | >95.0% | **100.0%** | **PASSED** |
| **Conversational Grade** | >95.0% | **98.09%** | **PASSED** |
| **Emotional Intelligence (EQ)** | >95.0% | **95.56%** | **PASSED** |
| **Official Contact Integrity** | 100% | **100.0%** (0 false redactions) | **PASSED** |
| **Speech STT (Whisper-SALT)** | 100% | **3/3 PASSED** | **PASSED** |
| **Speech TTS (Spark-TTS-SALT)** | 100% | **3/3 PASSED** | **PASSED** |
| **Latency Profile** | — | $p_{50}=3.72\text{s}$, $p_{90}=13.70\text{s}$, $p_{95}=18.80\text{s}$ | **NOMINAL** |

---

## 3. 300 New FAQs Cross-Lingual Benchmark Results

| Metric / Dimension | Target | Measured Result | Audit Status |
| :--- | :---: | :---: | :---: |
| **Total Questions Evaluated** | 300 | **300** | **COMPLETE** |
| **HTTP Availability (200 OK)** | 100.0% | **100.0%** (0 errors) | **PASSED** |
| **Overall Accuracy** | >99.0% | **100.0%** (300/300) | **PASSED** |
| **English (`en`) Accuracy** (100 items) | >99.0% | **100.0%** | **PASSED** |
| **Luganda (`lg`) Accuracy** (100 items) | >99.0% | **100.0%** | **PASSED** |
| **Swahili (`sw`) Accuracy** (100 items) | >99.0% | **100.0%** | **PASSED** |
| **Domestic Taxes Accuracy** (100 items) | >99.0% | **100.0%** | **PASSED** |
| **Customs & Border Trade Accuracy** (100 items) | >99.0% | **100.0%** | **PASSED** |
| **Tax Education & Citizen Services** (100 items) | >99.0% | **100.0%** | **PASSED** |
| **Conversational Grade** | >95.0% | **99.93%** | **PASSED** |
| **Emotional Intelligence (EQ)** | >95.0% | **99.76%** | **PASSED** |
| **Official Contact Integrity** | 100% | **100.0%** (0 false redactions) | **PASSED** |
| **Speech Roundtrips (STT + TTS)** | 100% | **3/3 PASSED** | **PASSED** |
| **Latency Profile** | — | $p_{50}=7.53\text{s}$, $p_{90}=13.87\text{s}$, $p_{95}=16.53\text{s}$ | **NOMINAL** |

---

## 4. 100 FAQs Multimodal Speech Benchmark Results

| Language | Questions | HTTP Availability | Statutory Accuracy | $p_{50}$ Latency | TTS Success | STT Success (RTF) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **English (`en`)** | 34 | 100.0% | **100.0%** | 0.657s | 100.0% | 100.0% (0.09x) |
| **Luganda (`lg`)** | 33 | 100.0% | **100.0%** | 2.302s | 100.0% | 100.0% (0.13x) |
| **Swahili (`sw`)** | 33 | 100.0% | **100.0%** | 6.127s | 100.0% | 100.0% (0.16x) |
| **Overall** | **100** | **100.0%** | **100.0%** | **3.345s** | **100.0%** | **100.0% (0.129x)** |

Speech synthesis and speech recognition operated with zero dropped frames. Mean STT Real-Time Factor (RTF) was **0.129x**, processing speech 7.7 times faster than real-time audio playback.

---

## 5. Architectural Defect Resolutions & Root Causes

### 5.1 Chat UI Step Structuring & Paragraphing Collapse
- **Problem**: When streaming responses to procedural questions in the chat UI (e.g. *"How do I file my annual tax returns?"*, *"how do i register for a tin?"*), numbered steps were smashed onto a single line without newlines (`template2. Enable macros...3. Validate...4. Back...5. Submit6. E-acknowledgment...`) and indented with 4 spaces as an accidental code block.
- **Root Cause**:
  1. The Next.js client-side reader loop in `App/frontend/src/app/page.tsx` split incoming Server-Sent Events (SSE) by `\n` and pushed individual lines (`data: <line>`) into the streaming reveal queue without joining them with newlines. Under the SSE specification, multiline event payloads arrive across consecutive `data:` lines.
  2. Smashed text starting with `1. ` was parsed by `Markdown.tsx` as an ordered list item, stripped of its leading `1. `, and rendered inside `<ol>` with CSS `padding-left: 1.4em`, creating the 4-space indent appearance.
- **Resolution**:
  - Implemented W3C SSE standard event buffering in `page.tsx`: all `data:` lines belonging to the same event are accumulated into `currentDataLines: string[]` and joined with `\n` on event boundaries (`\r\n\r\n`).
  - Added list-unsmashing normalizers in `useChatStore.ts` and `guardrails.py` to ensure inline list markers (`\d+\.`) and section transitions are strictly paragraphed.
  - Converted `Markdown` in `ChatMessage.tsx` from dynamic lazy loading to direct synchronous import, eliminating `<Suspense fallback={turn.content}>` unstyled text flashes.

### 5.2 Spurious Escalation on FAQs ("What services does URA provide?")
- **Problem**: Broad informational questions received fully accurate, cited responses from official documents, yet were flagged with `! Human review recommended — low_faithfulness=0.00` and escalated to human officers.
- **Root Cause**:
  1. `_SENTENCE_SPLIT_RE` in `text_signals.py` was defined as `re.compile(r"[.!?]+")`, splitting exclusively on punctuation marks. Bullet points, numbered items, and catalogued services terminate with newlines (`\n`), not periods. `split_sentences()` treated the entire 600-character, 18-item list as a single composite sentence of 68 content tokens. Because this single sentence had only ~32% token overlap against any individual passage, `grounded = 0 / 1`, forcing `faithfulness_score = 0.00`.
  2. In `claim_verifier.py`, `_SENTENCE_RE = re.compile(r"[^.!?\n]+(?:[.!?]+|$)")` skipped all bullet items lacking a terminal period, checking only the final sentence against Citation `[1]`.
- **Resolution**:
  - Updated `_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+|\n+")` in `text_signals.py` so every bullet point and paragraph is individually verified for faithfulness.
  - Updated `_SENTENCE_RE = re.compile(r"[^.!?\n]+(?:[.!?]+|(?=\n)|$)")` in `claim_verifier.py` to evaluate list statements.
  - Added cross-passage corroboration across retrieved `hits`, raising faithfulness on URA service catalogs to **High Confidence (0.67–1.00)** and eliminating false escalations.

### 5.3 Vernacular Figure & Unit Preservation
- **Problem**: Certain correct vernacular replies fell back to English due to false figure alterations.
- **Root Cause**:
  1. In `entailment.py`, `_CARDINAL_WORDS` mapped `saba` to `7.0`. In Luganda, *"saba"* is the common verb *"to apply / request"* (e.g. *"Saba nnamba ya TIN"* = *"Apply for a TIN"*), not seven. This caused Luganda registration steps to falsely register an invented figure `7.0`.
  2. In Swahili, *"mtu wa tatu"* (*"third party"*) falsely extracted `3.0`.
  3. Currency tokens omitted East African vernacular qualifiers (*"milioni"*, *"obukadde"*, *"emitwalo"*).
- **Resolution**:
  - Restricted Swahili numeral `saba` to explicit counting contexts (`siku saba`, `asilimia saba`).
  - Shielded non-numeric idioms (`mtu wa tatu`, `third party`, legal article references `Article 1.2`, `ekiwandiiko 1.2`).
  - Added `milioni`, `obukadde`, `emitwalo` to `_CURRENCY_TOKEN_RE` in `mt.py`.

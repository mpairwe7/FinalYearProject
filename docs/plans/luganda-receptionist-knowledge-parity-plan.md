# Plan: Luganda Receptionist Knowledge Parity & Real-Time RAG Elevation

Status: **Implemented.**
Authors: Architecture & AI Team
Date: 2026-09-26

---

## 1. Context & Problem Statement

While the English simulated voice receptionist (`gemini_live` engine) answers complex tax inquiries with verified statutory accuracy and sub-second latency, the Luganda version (`cascaded` engine) suffers from frequent failures and premature human-officer transfers.

### Root Cause Analysis

1. **4-Hop Translation Pipeline Penalty**:
   $$\text{Luganda Speech} \xrightarrow{\text{Whisper-SALT}} \text{Luganda Text} \xrightarrow[\text{Hop 1}]{\text{Sunflower MT}} \text{English Query} \xrightarrow{\text{Search}} \text{English Docs} \xrightarrow[\text{Hop 2}]{\text{LLM}} \text{English Answer} \xrightarrow[\text{Hop 3}]{\text{Sunflower MT}} \text{Luganda Text} \xrightarrow[\text{Hop 4}]{\text{Orpheus}} \text{Speech}$$
   - Sunflower-14B prompted translation hops add 6–15+ seconds of latency.
   - If total generation exceeds 25 seconds, `brain.py` hits a `TimeoutError` and immediately transfers to an officer.
2. **Keyword Mismatch & Code-Switching Dilution**:
   - The URA knowledge base (Qdrant vectors, BM25 FAQ indices, tax acts) is indexed in English.
   - Code-switched Luganda tax questions (e.g. *"omusolo gwa rental"*, *"okufayiringa return"*) lose exact terminology during MT query expansion, yielding lower dense & BM25 retrieval scores.
3. **Knee-Jerk Voice Escalation**:
   - When retrieval confidence scores drop below the strict threshold, `OutputGuard.should_abstain` sets `escalation_required = True`.
   - `brain.py` treats any text-chat escalation as a reason to immediately abort voice response and forward the call to an officer.

---

## 2. Target Architecture (Synthesized Strategy: Prop 1 + 4 with Prop 2)

We combine **Single-Hop Cross-Lingual Grounding (Prop 1)** and **Voice-Calibrated Escalations (Prop 4)** with **Gemini Flash as the Cross-Lingual Knowledge Bridge (Prop 2)**:

```
Taxpayer speaks Luganda
        │
        ▼
Whisper-SALT ASR (Luganda text: "Nsasula ntya omusolo gwa rental?")
        │
        ▼
Cross-Lingual RAG Bridge (Gemini 2.5 Flash Lite)
   ├─ Semantic Understanding: Extracts user intent in Luganda
   ├─ Clean Query: Calls query_ura_tax_knowledge(query="How to pay rental income tax in Uganda")
   │       │
   │       ▼
   │  English URA Knowledge Base (Qdrant + BM25 + Cross-Encoder Reranker)
   │       │
   │       ▼
   ├─ Official English Statutory Grounding returned with citations
   └─ Direct Generation: Emits concise 1-2 sentence statutory response directly in Luganda
        │
        ▼
Orpheus-3B Luganda TTS Sidecar (Voice: salt_lug_0001, FP8)
        │
        ▼
Taxpayer hears natural Luganda audio answer in < 2.5s
```

---

## 3. Core Implementation Pillars

### Pillar 1: Fast Cross-Lingual RAG Bridge (Gemini 2.5 Flash Lite + Sunflower Fallback)
- **Role**: Replaces the slow dual-MT hop with a unified cross-lingual reasoning step.
- **Workflow**:
  1. Input: Transcribed Luganda text from Whisper-SALT.
  2. The model understands the Luganda question natively, formats a clean English query for the `query_ura_tax_knowledge` tool against the English URA corpus.
  3. Context passages (statutory rules, rates, filing dates) are read in English, and the model synthesizes the answer directly in natural Luganda in a single inference pass.
  4. Local Sunflower-14B remains the offline/air-gapped fallback using direct cross-lingual prompt generation:
     > *"Context (English URA Statutes): {passages}\nTaxpayer Question (Luganda): {luganda_query}\nProvide a concise 2-sentence response directly in Luganda using exact figures from the context."*

### Pillar 2: Voice-Calibrated Escalation Thresholds
- **Policy**:
  - Differentiate voice calls from text chat in `brain.py`: On moderate retrieval scores (0.35–0.50), do **not** trigger a hard transfer to a human officer.
  - Deliver the best statutory answer and politely ask: *"Wandiyagadde okwogera n'omukozi ku nsonga eno?"* (*"Would you like to speak to an officer about this?"*).
  - Only escalate when:
    - Explicit human request is spoken (*"njagala kwogera n'omukozi"*).
    - Hard failure/zero retrieval hits occur.
    - Verified tax dispute/objection workflow requires human account review.

### Pillar 3: Acronym & Code-Switching Normalization
- Pre-process common code-switched URA tax terms in Luganda using `receptionist/lexicon.py`:
  - *"omusolo gwa rental"* $\rightarrow$ *"Rental Income Tax"*
  - *"okusaba TIN"* $\rightarrow$ *"TIN registration"*
  - *"okufayiringa return"* $\rightarrow$ *"file tax return"*
  - *"omusolo gwa VAT / emmotoka"* $\rightarrow$ *"VAT / motor vehicle transfer"*
- Guarantees immediate dense and BM25 hit retrieval without relying on multi-hop translation.

---

## 4. Work Breakdown & Implementation Plan

### Phase A: Backend Service & Prompt Engineering
1. **Extend `UraReceptionistBrain` in `receptionist/brain.py`**:
   - Implement `_generate_luganda_answer(question)` utilizing the cross-lingual bridge pattern.
   - Add direct context-to-Luganda generation prompt template.
2. **Calibrate Escalations in `brain.py`**:
   - Check `escalation_required`: only trigger `_transfer` on high-confidence human requests or zero-hit recovery.
   - On partial confidence, speak the retrieved tax guidance with confirmation prompt.
3. **Lexicon Normalization in `receptionist/lexicon.py`**:
   - Add bilingual Luganda-English URA tax query mapping dictionary.

### Phase B: Speech Pipeline Optimization & Latency Control
1. **Orpheus TTS Streaming Pipeline**:
   - Ensure the direct Luganda answer text streams directly to `ORPHEUS_TTS_URL`.
   - Keep generation to 1–3 short sentences for telephony naturalness.
2. **Telemetry & Quality Tracking**:
   - Track `luganda_turn_latency_ms`, `luganda_containment_rate`, and `luganda_transfer_rate` in `metrics.py`.

### Phase C: Unit & E2E Validation
1. **Automated Test Scenarios (`test_receptionist_luganda_knowledge.py`)**:
   - Test simple tax questions in Luganda (TIN, VAT, rental tax, motor vehicle, deadlines).
   - Assert answers contain exact legal rates and figures without triggering officer handoff.
   - Assert explicit human transfer requests still transfer cleanly.

---

## 5. Success Metrics
- **Containment Rate in Luganda**: Raise from ~20% to $\ge 65\%$ (parity with English).
- **Turn Latency (p90)**: Drop from >25s (timeout) to $< 2.8\text{s}$.
- **Figure Fidelity**: 100% preservation of statutory numbers (e.g. 18% VAT, 12% rental threshold, 30 days).

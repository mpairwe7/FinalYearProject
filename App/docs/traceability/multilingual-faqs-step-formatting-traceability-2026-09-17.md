# Engineering Traceability Record: Multilingual Accuracy & Step Formatting

> **Date:** 2026-09-17  
> **Branch:** `dev` on `https://github.com/mpairwe7/FinalYearProject.git`  
> **Environment:** Dedicated NVIDIA RTX A6000 48GB (GPU 4), Driver 535.183.01, CUDA 12.2  
> **Tunnel:** `https://struttingly-nongeological-briella.ngrok-free.dev/api`  
> **Related PRs / Commits:** `0c3dd89df2`, `79c5c42366`, `cce42a426b`, `de43107d14`, `2b8fefebfb`, `67c929f4a6`, `d0f8f9a6c6`  

---

## 1. Summary of Changes

This record establishes traceability for the fixes applied to close domestic tax and customs gaps, resolve chat UI procedural step-formatting collapse, eliminate false low-faithfulness escalations, and confirm accuracy across English, Luganda, and Swahili.

### 1.1 Affected Codebase Modules
- `App/frontend/src/app/page.tsx`: SSE streaming event-buffered reader loop preserving multiline `data:` records.
- `App/frontend/src/components/ChatMessage.tsx`: Direct synchronous import of `Markdown` component to eliminate unstyled fallback flashes.
- `App/frontend/src/store/useChatStore.ts`: Unsmashing of inline list numbering (`steps:1.**`, `section.2.**`) and paragraph breaks.
- `App/backend/app/guardrails.py`: OutputGuard `normalize_structure()` regex strict boundary matching on horizontal whitespace; stray glued citation marker cleanup (`otherL1]` $\rightarrow$ `other [1]`).
- `App/backend/app/text_signals.py`: `_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+|\n+")` to split lists and bullet points into distinct sentences for faithfulness scoring.
- `App/backend/app/claim_verifier.py`: `_SENTENCE_RE` matching non-punctuated list lines; cross-passage corroboration across all retrieved `hits`; revision rather than escalation when no contradiction exists.
- `App/backend/app/mt.py`: Currency tokens (`milioni`, `obukadde`, `emitwalo`); statutory article and clause subsection shielding (`Article 1.2`, `ekiwandiiko 1.2`).
- `App/backend/app/entailment.py`: Disambiguation of Luganda verb *"Saba"* (*"to apply / request"*) from Swahili cardinal numeral *"saba"* (*7*); non-numeric idiom shielding (*"mtu wa tatu"*, *"third party"*); English number words (`two`...`ten`, `twenty`...`fifty`).
- `App/backend/app/service.py`: Preserving English generation inside `_generate_en` and localizing only once at outer boundary; web scrape header artifact removal (`_WEB_SCRAPE_ARTIFACT_RE`).
- `App/backend/app/llm.py`: Removal of hallucination-inducing exemplars `(e.g. 18%)` from translation system prompt instructions.

---

## 2. Problem Diagnoses and Root Causes

### 2.1 Chat UI Step Smashing & 4-Space Indent Block
- **Symptom**:
  ```text
  To file your annual tax return:
      Login ura.go.ug with TIN/password → e-services → e-returns → select return type and download template2. Enable macros, fill without renaming or copy/paste3. Validate to generate upload file4. Back to e-returns upload file with return period and captcha5. Submit6. E-acknowledgment is issued (also emailed/portal)
  ```
- **Root Cause**:
  1. SSE wire format emits multiline tokens across consecutive `data:` lines.
  2. `page.tsx` split incoming chunks by `\n` and pushed each line slice without joining them with newlines.
  3. Consecutive list lines (`1. Login...` and `2. Enable...`) merged on the client side into a single line.
  4. Smashed text starting with `1. ` was parsed as an ordered list item, had its leading `1. ` stripped, and was wrapped in `<ol>` with CSS `padding-left: 1.4em`, displaying as a 4-space code block indent.
- **Verification**:
  A simulated SSE client verified that buffered accumulation and joining with `\n` restores the exact multiline structure:
  ```markdown
  To file your annual tax return:

  1. Login ura.go.ug with TIN/password → e-services → e-returns → select return type and download template
  2. Enable macros, fill without renaming or copy/paste
  3. Validate to generate upload file
  4. Back to e-returns upload file with return period and captcha
  5. Submit
  6. E-acknowledgment is issued (also emailed/portal)
  ```

### 2.2 False Low-Faithfulness Escalation on "What services does URA provide?"
- **Symptom**: Answering with a comprehensive 18-item catalog of URA tax, customs, and digital services triggered `! Human review recommended — low_faithfulness=0.00` and raised an escalation ticket.
- **Root Cause**:
  1. `_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+")` only split on terminal punctuation. Bullet items end in `\n`, so the entire list was treated as a single composite sentence of 68 tokens.
  2. The single composite sentence had only 32% token overlap against Citation `[1]`, failing the 50% sentence grounding threshold. `grounded = 0 / 1`, yielding `faithfulness_score = 0.00`.
  3. `claim_verifier.py` discarded bullet lines without periods, checking only the final sentence against Citation `[1]`.
- **Verification**:
  Updated `_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+|\n+")` and cross-passage corroboration. Faithfulness score rose from **0.00** to **0.67–1.00**, and the answer was approved with **High Confidence** and zero escalation.

---

## 3. Empirical Benchmark Summary

### 3.1 1,000 FAQs Full-Stack Benchmark (`docs/Reports/data/eval_1000_faqs_ngrok.json`)
- **Overall Accuracy**: **100.0%** (1,000 / 1,000)
- **English (`en`) Accuracy**: **100.0%** (480 items)
- **Luganda (`lg`) Accuracy**: **100.0%** (264 items)
- **Swahili (`sw`) Accuracy**: **100.0%** (256 items)
- **Domestic Taxes Accuracy**: **100.0%** (423 items)
- **Customs & Trade Accuracy**: **100.0%** (252 items)
- **Tax Education Accuracy**: **100.0%** (325 items)
- **Conversational Grade**: **98.09%**
- **Emotional Intelligence (EQ)**: **95.56%**
- **Official Contact Integrity**: **PASSED** (0 false redactions)

### 3.2 300 New FAQs Cross-Lingual Benchmark (`docs/Reports/data/eval_300_faqs_ngrok.json`)
- **Overall Accuracy**: **100.0%** (300 / 300)
- **English (`en`) Accuracy**: **100.0%** (100 items)
- **Luganda (`lg`) Accuracy**: **100.0%** (100 items)
- **Swahili (`sw`) Accuracy**: **100.0%** (100 items)
- **Domestic Taxes Accuracy**: **100.0%** (100 items)
- **Customs & Trade Accuracy**: **100.0%** (100 items)
- **Tax Education Accuracy**: **100.0%** (100 items)
- **Conversational Grade**: **99.93%**
- **Emotional Intelligence (EQ)**: **99.76%**

### 3.3 100 FAQs Multimodal Benchmark (`docs/Reports/data/eval_100_faqs_multimodal_ngrok.json`)
- **Statutory Accuracy**: **100.0%** (100 / 100)
- **Speech TTS Success Rate**: **100.0%** (mean latency: 9.73s)
- **Speech STT Success Rate**: **100.0%** (mean RTF: 0.129x — 7.7x faster than real-time)

---

## 4. Teardown and Cleanup Record
On completion of the benchmarks, the Docker Compose stack in `App/` was gracefully torn down:
- `ura-app-api`, `ura-app-vllm-sunflower`, `ura-app-frontend`, `ura-app-qdrant`, `ura-app-redis`, and `app-qdrant-backup-1` were removed.
- VRAM on GPU 4 was reclaimed from 45,448 MiB to **12 MiB** (0 compute processes running).

# Traceability Record: FAQ Grounding, PII Redaction Fix, Multi-Turn Memory & Volume Robustness

**Date:** 2026-09-06  
**Commits:** `e869d623bc` (PR #446), `9a6776ee7c` (PR #447)  
**Environment:** Local GPU deployment (`NVIDIA RTX A6000`), vLLM `Sunbird/Sunflower-14B-FP8`, Qdrant `v1.19.0`, Redis, Next.js frontend, public ngrok tunnel (`struttingly-nongeological-briella.ngrok-free.dev`).

---

## 1. Summary of Defects Resolved

### 1.1 Defect G46: FAQ Low Faithfulness & Spurious Escalation on Vector Fallback
- **Symptoms**: Asking standard knowledge base FAQ questions (e.g. *"What services does URA provide?"*) resulted in `faithfulness_score = 0.00`, appending the amber escalation banner `! Human review recommended — low_faithfulness=0.00`, and routing to human ticket triage.
- **Root Cause**: In `retriever._search_vectorize()` and `providers/vectorize.py`, vector hits stored `text: "Question: ...\nAnswer: ..."` but left top-level `question` and `answer` fields as empty strings (`""`). In `_faq_match_score()`, empty question/answer fields caused term coverage to evaluate to `0.00 < 0.58` (cutoff). Consequently, `_filter_unbound_faq_hits()` evicted the exact matching FAQ row from candidate hits, leaving only generic PDF handbook excerpts. The LLM then answered with services not present in the PDF chunks alone, leading to `faithfulness_score = 0.00`.
- **Resolution**:
  - Implemented automatic QA extraction in `retriever._search_vectorize()`, Qdrant point unpacking, and `vectorize_query()`.
  - Updated `_faq_match_score()`, `_filter_unbound_faq_hits()`, and `_promote_equivalent_faq_hits()` to parse question/answer pairs from `text` whenever top-level fields are empty.
  - Backfilled matching vector hits with `question` and `answer` during step 3c FAQ blending.
  - Stamped `_short_circuit: True` on cached turns in `generate_retrieval_only()` to prevent empty-hit abstention on stream replay.

### 1.2 Defect G47: PII Sanitizer Over-Redacted Official URA Contact Channels
- **Symptoms**: Taxpayers asking for URA contact information received redacted placeholders: `Email:[REDACTED_EMAIL]; [REDACTED_EMAIL] | https://ura.go.ug`.
- **Root Cause**: `guardrails.redact_pii_text()` applied a blanket regex on all email addresses without exempting official institutional government channels.
- **Resolution**:
  - Defined `is_official_ura_email()` to exempt official public authority domains (`@ura.go.ug`, `@*.ura.go.ug`, `@go.ug`, `@*.go.ug`) from PII redaction while strictly redacting private citizen emails (`[REDACTED_EMAIL]`).
  - Added legacy marker restoration in `OutputGuard.sanitize()` to ensure any existing cached/upstream contact blocks render valid official channels (`services@ura.go.ug` and `info@ura.go.ug`).
  - Updated `CONTACT_FOOTER` in `text_signals.py` to include `email services@ura.go.ug`.

### 1.3 Defect G48: Multi-Turn Coreference Misbinding & False Contradiction Withholding
- **Symptoms**:
  1. Asking *"What is EFRIS and would I be required to use it?"* after a VAT question caused `rewrite_with_history()` to replace "it" with the previous turn's "VAT", yielding *"What is EFRIS and would I be required to use Value Added Tax (VAT)?"*. The epistemic premise guard then misidentified "use Value Added Tax" as a fictitious tax head and rejected the turn.
  2. Asking situational queries with user-supplied figures (e.g. *"If I open a side shop with turnover of 80m, is VAT compulsory?"*) triggered `numeric_contradiction()` because the user's 80m was not in the 150m statutory threshold passage, withholding the reply with `CONTRADICTED_CLAIM_REPLY`.
  3. Texting shorthand `wht is` was expanded as Withholding Tax rather than "what is".
- **Resolution**:
  - Updated `rewrite_with_history()` to bind pronouns to intra-sentential subjects before looking backward to prior turns.
  - Added action verbs ("use", "using", "require", "mandate") to `_STOP_AND_ACTION_WORDS` in `premise_guard.py`.
  - Updated `numeric_contradiction()` and `verify_claims()` to subtract user query amounts before checking for legal threshold contradictions.
  - Added disambiguation for `wht is/are/does` into `what is/are/does` in `correct_spelling()`.

---

## 2. Benchmark & Live Tunnel Evaluation

Evaluation was executed over the public ngrok tunnel (`https://struttingly-nongeological-briella.ngrok-free.dev`) using `scripts/exhaustive_volume_spike_eval.py`:

| Phase | Metric | Value | Status |
|---|---|:---:|:---:|
| **1. Exhaustive Domain** | Top-1 FAQ Grounding | **1.00** | **PASS** |
| | Unwarranted Escalations | **0%** | **PASS** |
| | Average Domain Latency | 2.25s | **PASS** |
| **2. Volume Load** | 20 sustained requests (2 workers) | 100% (20/20) | **PASS** |
| | Sustained Throughput | **1.97 req/s** | **PASS** |
| | Latency p50 / p95 | 500ms / 4,003ms | **PASS** |
| **3. Spike Concurrency** | 6-request burst | 100% (6/6 in 7.2s) | **PASS** |
| | Median Spike Latency | 2,815ms | **PASS** |
| **4. Fuzzy Robustness** | Noise, Typos & Anaphora Score | **83.3%** | **PASS** |
| | Official Email Integrity | **100% (0 `[REDACTED_EMAIL]`)** | **PASS** |

---

## 3. Local GPU Resources Cleaned

All ephemeral processes and GPU allocations were cleanly terminated:
1. **Container `ura-vllm-sunflower`**: Stopped via `docker stop ura-vllm-sunflower`, releasing **34.3 GB** of GPU VRAM on GPU 6.
2. **Container `ura-qdrant`**: Stopped via `docker stop ura-qdrant`, closing ports 6333–6334.
3. **Backend Service**: Terminated local process on port 8887.
4. **Redis Cache**: Flushed all temporary `ura:cache:*` entries.
5. **GPU Status**: Verified with `nvidia-smi` — GPU memory returned to baseline across cards.

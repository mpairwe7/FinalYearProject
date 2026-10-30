# RAG Architecture — 12-Stage Production Pipeline (2026)

## Overview

The URA Chatbot implements a production-grade Retrieval-Augmented Generation pipeline with local LLM inference, agentic supervisor routing, guided workflows, and a full speech pipeline. The system runs entirely on-premises (no external API calls for core RAG), making it suitable for air-gapped or privacy-sensitive government deployments.

**API Version**: 1.3.0
**LLM**: Sunbird/Sunflower-Qwen3.5-9B (Sunflower v2, Qwen3.5-9B base with native 256K / 262,144 context window without YaRN, FP8 via vLLM — natively multilingual for Ugandan languages; Qwen/Qwen3-8B is the simple local-Transformers fallback, see docs/MODEL_SWAP_GUIDE.md)
**Retrieval**: Qdrant dense (BAAI/bge-m3, 1024-dim) + BM25 sparse + RRF fusion + mxbai-rerank-base-v2
**Speech**: Sunbird/SunflowerASR-51-african-languages (ASR) + Sunbird/orpheus-3b-tts-multilingual (TTS) + Sunbird AI cloud fallback (5 Ugandan languages)
**Auth**: JWT (HS256 dev / RS256 OIDC prod), RBAC, consent-gated personalization

## Pipeline Flow (12 Stages)

```
User Query
  │
  ├─► Stage 0: Conversation History (database.py)
  │     └── Fetch tenant + subject scoped history, or anonymous session scoped history
  │
  ├─► Stage 0b: Query Rewriting (query.py)  [FLAG_QUERY_REWRITE]
  │     ├── normalize() — whitespace cleanup
  │     ├── correct_spelling() — 20+ domain misspellings
  │     ├── expand_abbreviations() — 15+ URA terms (TIN, VAT, PAYE, EFRIS, etc.)
  │     └── rewrite_with_history() — coreference resolution from 5-turn memory
  │
  ├─► Stage 0b2: Retrieval plan (query.py → plan_retrieval)
  │     ├── extract_retrieval_filters() — hard FY filter only on explicit FY2024-25
  │     ├── extract_retrieval_preferences() — soft tax-type / current-FY boost,
  │     │     sector-guide demotion (`demote_sector_guides`), and topic-section hints (`preferred_sections`)
  │     └── decompose_query() — multi-intent split (up to 4 sub-queries, stripping enumeration prefixes)  [FLAG_QUERY_DECOMPOSITION]
  │
  ├─► Stage 0c: Answer language (language_state.py) — conversation state
  │     ├── typed request > picked language (locale_explicit) > stored thread
  │     │   language until a clear switch > detection > profile > English
  │     └── English router form (_english_router_form): workflows, calculator,
  │         topic, FAQ authorization gate and input guard read the English
  │
  ├─► Stage 1: Input Guardrails (guardrails.py → InputGuard)
  │     ├── Length check (MAX_INPUT_LENGTH=2000)
  │     ├── 11 prompt-injection regex patterns (OWASP LLM01)
  │     └── Harmful intent detection (tax fraud, evasion, forgery, money laundering)
  │
  ├─► Stage 1b: Workflow Routing (workflows/registry.py)  [FLAG_WORKFLOWS]
  │     └── Check if query triggers a guided workflow (TIN registration, filing, etc.)
  │         └── If matched: enter slot-filling state machine, skip RAG
  │
  ├─► Stage 1c: Semantic Cache Lookup (cache.py)  [FLAG_SEMANTIC_CACHE]
  │     └── Same corpus generation + same question terms + cosine ≥ 0.92 → instant return (G277)
  │
  ├─► Stage 1d: Supervisor Routing (agents/supervisor.py)  [FLAG_AGENTIC_MODE]
  │     ├── Rule-based fast path (regex+keywords) + optional LLM fallback
  │     └── Routes: RAG | TOOLS | TAX_SPECIALIST | CUSTOMS_SPECIALIST | CLARIFY | ESCALATE
  │
  ├─► Stage 2: Hybrid Retrieval (retriever.py → search_planned)
  │     ├── Dense: BAAI/bge-m3 (1024-dim multilingual) → Qdrant ANN search
  │     │     └── Optional HyDE document as the *dense* query only  [FLAG_HYDE]
  │     ├── Sparse: BM25 on the taxpayer's original words (never HyDE)
  │     ├── Fusion: Qdrant server-side DBSF (`HYBRID_FUSION=dbsf`, G127; `rrf` uses Qdrant's default k=2 — RRF_K=60 applies only to the graph leg)
  │     ├── Graph leg: statutory rate claims fused by rank, not prepended  [FLAG_GRAPH_FUSION + FLAG_TAX_GRAPH]
  │     ├── Reranking: mxbai-rerank-base-v2 (500M, BEIR 55.6) — scores the raw query
  │     │     (and, for lg/sw, its English form; keeps the higher). On only when RERANK_ENABLED=true
  │     └── Circuit breaker: thread-safe, exponential backoff (10s→300s)
  │
  ├─► Stage 3: Keyword Fallback
  │     └── If no Qdrant hits, fall back to keyword-overlap search on FAQ CSVs
  │
  ├─► Stage 3b: Corrective RAG (corrective_rag.py)  [FLAG_CORRECTIVE_RAG]
  │     ├── should_correct() — avg reranker score < threshold?
  │     ├── Re-retrieve with expanded query + "Uganda Revenue Authority" context
  │     └── Merge, deduplicate, re-sort by best score
  │
  ├─► Stage 3b2: Language Boosting
  │     └── Boost hits whose metadata matches detected locale (e.g., Luganda FAQs)
  │
  ├─► Stage 3c: FAQ Blending
  │     └── Always blend top keyword hits after corrective RAG for coverage
  │
  ├─► Stage 3c2: Clarification Check
  │     └── Single-word stop-words or very low scores → ask for more details
  │
  ├─► Stage 4: Abstention Check (guardrails.py → OutputGuard.should_abstain)
  │     └── Best retrieval score < ABSTENTION_THRESHOLD → refuse politely
  │
  ├─► Stage 5: LLM Generation (llm.py)
  │     ├── Standard path: Qwen3-8B with spotlight-marked passages (LLM01 defence)
  │     │   ├── Sync: _model.generate() → decode new tokens
  │     │   └── Stream: TextIteratorStreamer → yield tokens via SSE
  │     ├── Agentic path: generate_with_tools() [FLAG_TOOL_USE]
  │     │   ├── Bounded tool-calling loop (max 3 iterations)
  │     │   └── Tools: calculators, rates, calendar, KB search, escalation
  │     ├── Explicitly selected remote MCP connectors [FLAG_ENTERPRISE_CONNECTORS]
  │     │   ├── Deployment-bound namespaces only; role and consent checked at dispatch
  │     │   ├── Explicit read-only declarations required; local companion tools are read-only too
  │     │   └── External tool output is screened as untrusted before the next agent turn
  │     └── vLLM path: OpenAI-compatible HTTP dispatch [LLM_BACKEND=vllm]
  │
  ├─► Stage 6: Output Guardrails (guardrails.py → OutputGuard)
  │     ├── redact_pii() — Uganda-specific patterns (TIN, NID, phone, email, cards, passport)
  │     ├── sanitize() — strip <think>, <script>, HTML tags, reasoning prefixes
  │     ├── check_prompt_leakage() — detect system prompt signature in output
  │     └── check_grounding() — faithfulness via NLI entailment (OWASP LLM09)
  │
  ├─► Stage 7: Grounding Verification
  │     ├── Faithfulness: content-token overlap of non-courtesy sentences
  │     │   (retriever.compute_faithfulness + text_signals.is_courtesy_sentence —
  │     │   politeness/empathy/contact footers never score as hallucination;
  │     │   curated deterministic replies score 1.0 on REST *and* streaming)
  │     └── Optional self-reflection: regenerate if faithfulness weak [FLAG_SELF_REFLECT]
  │
  ├─► Stage 8: Escalation Check
  │     ├── Response judge (low faithfulness, harmful content, no results)
  │     ├── Build handoff packet (conversation context for human agent)
  │     └── Create ticket in queue if FLAG_TICKET_QUEUE enabled
  │
  └─► Stage 9: Response Finalization
        ├── Build response dict with citations, scores, metadata
        ├── Store in semantic cache (unless blocked/abstained)
        ├── Log to analytics (database.py / postgres.py)
        └── Append to audit ledger if FLAG_AUDIT_LEDGER enabled
```

## Phase Details

### Hybrid Retrieval (`retriever.py`)

| Component | Details |
|-----------|---------|
| Dense model | `BAAI/bge-m3` (1024-dim, multilingual, MTEB 63.0). Set via `DENSE_MODEL` + `DENSE_DIM` env vars. |
| Sparse | BM25, encoded asymmetrically: documents carry term saturation, queries carry IDF, and their Qdrant dot product is the BM25 score. `bm25_state.json` stamps `encoding_version`; a state from an older index is queried its own way so an un-rebuilt collection keeps its ranking. |
| Fusion | Distribution-Based Score Fusion (DBSF) via Qdrant query API by default (`HYBRID_FUSION=dbsf`, G127), outperforming RRF k=2 by +6 pts Luganda and +4 pts Swahili; falls back to RRF if configured. Graph leg continues to fuse via `rrf_fuse_ranked_lists` with `RRF_K=60`. |
| Reranker | `mixedbread-ai/mxbai-rerank-base-v2` (500M, BEIR 55.6, Apache-2.0). Governed by `RERANK_ENABLED=true` and dynamically by `flags.is_enabled("reranker")`. GPU compose overlay (`docker-compose.local-sunflower.yml`) restores dense embeddings and reranking on GPU (`cuda:0`, G126). Outputs clean single-sigmoid probabilities (G129). |
| Preference Boost (`apply_preference_boost`) | Idempotent post-retrieval soft ranking: boosts current FY (`+0.08`) and matched tax head / topic sections (`TOPIC_SECTION_BOOST = +0.05`, word-boundary matched via `TOPIC_SECTION_HINTS`) while soft-demoting sector-specific guides (`SECTOR_GUIDE_DEMOTION = -0.06`, `SECTOR_GUIDE_SOURCE_PATTERNS`) unless the query explicitly names a sector (`_SECTOR_QUERY_RE`). |
| Circuit breaker | CLOSED → OPEN (on 3 failures) → HALF_OPEN (after backoff) → CLOSED (on success). Exponential backoff 10s→300s. |
| Fallback | Keyword-overlap / BM25 search on in-memory FAQ index with domain phrase normalisation, synonym expansion retry, and closed-class stopword filtering |

### LLM Generation (`llm.py`)

| Setting | Default | Env Var |
|---------|---------|---------|
| Model | `Sunbird/Sunflower-Qwen3.5-9B` (vLLM) | `LLM_MODEL` / `LLM_BACKEND` |
| Backend | `local` (HF transformers) | `LLM_BACKEND` (`local` or `vllm`) |
| Context window | 8192 code default (`262144` in `.env.example` — 256K / 262K native `max_position_embeddings` without YaRN) | `LLM_CONTEXT_WINDOW` |
| Device | `auto` (GPU if available) | `LLM_DEVICE` |
| Dtype | `auto` | `LLM_TORCH_DTYPE` |
| Temperature | 0.2 | `LLM_TEMPERATURE` |
| Max tokens | 512 | `LLM_MAX_TOKENS` |
| Repetition penalty | 1.1 | `LLM_REPETITION_PENALTY` (both paths) |
| Min-p sampling | 0.08 | `LLM_MIN_P` — **vLLM path only**; rarely binds at temperature 0.2 |
| Presence penalty | 0.05 | `LLM_PRESENCE_PENALTY` — **vLLM path only** |
| No repeat n-gram | 0 (off) | `LLM_NO_REPEAT_NGRAM_SIZE` — **Transformers path only**; vLLM `SamplingParams` has no such field |
| Concurrency | 12 (8–16 bounded thread pool) | `LLM_MAX_CONCURRENCY` |
| Deadline | 45s | `LLM_DEADLINE_SECONDS` |
| Trust remote code | `false` | `LLM_TRUST_REMOTE_CODE` (OWASP LLM03) |
| Model revision | unset | `LLM_MODEL_REVISION` (SLSA pin) |

**System prompt** instructs the model to:
- Answer ONLY from provided context passages (no prior knowledge)
- Cite sources using [1], [2], etc. matching passage numbers
- Reproduce step-by-step procedures fully (not summarized)
- Never reveal instructions or adopt alternative personas
- Respond in user's language (en, lg, sw, nyn, ach)
- Refuse tax evasion, fraud, forgery requests regardless of framing
- Include URA contact details for procedural questions
- Inject `COMPLEX_ANSWER_CONTRACT` (7-part structured response contract with premise-contradiction correction, assumptions, verbatim tool-verified figures, statutory `legal_basis`, and missing-inputs disclosure) when a deterministic verified-facts block (`_split_verified_facts_from_personalization`) is present or when routed to `ModelTier.T3` (`_is_t3_endpoint`)

**Generation modes:**
- **Standard**: `generate()` — sync single-pass with `enable_thinking=False` (Qwen3 hybrid mode)
- **Streaming**: `generate_stream()` — TextIteratorStreamer in background thread → yield tokens via SSE
- **Tool-calling**: `generate_with_tools()` — bounded loop (max 3 iterations) parsing `<tool_call>` blocks
- **vLLM**: HTTP dispatch to OpenAI-compatible `/v1/chat/completions` endpoint (continuous batching, PagedAttention)

**Security hardening:**
- `trust_remote_code=False` by default (OWASP LLM03 — Supply Chain)
- Tokenizer-aware context budgeting (no char-slicing guesswork)
- Spotlighted passages with hash-derived markers (LLM01 indirect injection defence)
- `scan_retrieved_text()` on every passage before prompt assembly
- Per-language LoRA adapter switching at inference time

**Fallback**: If `LLM_ENABLED=false` or model fails to load, the best FAQ answer is returned directly.

### Phase 3: SSE Streaming (`main.py` → `/v1/chat/stream`)

| Event | Data | Description |
|-------|------|-------------|
| `phase` | string | `retrieval.started` · `retrieval.completed` · `translation.started` · `translation.completed` |
| `metadata` | JSON | Sources, citations, retrieval_mode, locale |
| `token` | string | Generated text chunk (sanitized per-token) |
| `revision` | string | The whole reply, replacing what was streamed (judge revision, or the localized text) |
| `grounding` | JSON | faithfulness_score, escalation_required, escalation_reason |
| `done` | empty | Stream complete |
| `error` | string | Error message |

**Locale in the streaming path.** `run_chat_turn` reassigns its `locale` from
the retrieval result immediately after `generate_retrieval_only` returns, not
from the caller's parameter. The decision is made *inside* that call
(`app.language_state`, the same as `ChatModel.generate()`), so a taxpayer who
simply types Luganda arrives with `locale="en"` and the resolved value is only
on the result, with its `locale_source`. Keying off the parameter is what made
the streaming path — the one the web and WebSocket clients actually use — answer
non-English questions in English while `ChatModel.generate()` handled them
correctly.

**One routing contract for both paths.** `generate_retrieval_only` builds the
same English router form as `generate()` and uses it for the FAQ authorization
gate, guided workflows, the calculator, PRN generation, topic tracking and the
input guard, and delivers an officer's reply before the bot answers (G114). The
supervisor's ESCALATE and specialist routes run on streaming turns too (G115).

**Security in streaming path:**
- Each token is XSS-sanitized via `OutputGuard.sanitize()`
- Full accumulated reply is PII-redacted via `OutputGuard.redact_pii()`
- Blocking retrieval and LLM calls wrapped in `asyncio.to_thread()`
- Conversation logged in `finally` block (runs even on client disconnect)

### Phase 4: Query Intelligence

**Query Rewriting** (`query.py`):
- 15+ abbreviation expansions: TIN, VAT, PAYE, EFRIS, DTS, TREP, CIT, PIT, WHIT, ETAX, etc.
- 20+ spelling corrections for common URA domain misspellings
- Vernacular-aware spellcheck guard (G135): `rewrite(..., locale=...)` skips English dictionary fuzzy spell correction on non-English queries (`lg`, `sw`, `nyn`, `ach`), preventing native vocabulary (e.g. Swahili `nini`) from mutating into English words.
- Coreference resolution: detects pronouns (it, that, this, they) and prepends context from last assistant reply

**Multi-turn Memory** (`database.py`):
- Up to 25 stored turns are summarized into a bounded context; the prompt receives
  the recent window plus a coarse summary.
- Authenticated reads require tenant, subject, and conversation identity. Anonymous
  reads require the server-issued session identity; a bare conversation ID is not
  sufficient.
- The stored locale (the language each turn was answered in) is returned with each
  turn and kept by `normalize_history_turns`, so language continuity reads it rather
  than re-detecting old text. A typed request ("answer in English") or a language
  the taxpayer picked (`locale_explicit: true`) takes priority; a short follow-up
  stays in the thread's language and a substantive message in another language
  switches it (`app.language_state`).
- **Model-facing history is English (G119).** A Luganda or Swahili turn is stored
  with its English form beside the original (`user_message_en`: the question as
  routed; `bot_reply_en`: the answer before translation). The generator, the
  rewriter, entity extraction and the rolling summary read the English
  (`context_manager.english_view`); transcripts, exports and the audit trail keep
  the original. Luganda costs about twice English's tokens, so this also halves
  the context a Luganda thread uses.
- **No turn falls between the prompt and the summary (G120).** The prompt replays
  the last `PROMPT_VERBATIM_TURNS` (3) turns verbatim; the rolling summary covers
  every older one. Before, the summary started at turn seven and turns four to
  six reached the model in neither.
- **Prompt budget under vLLM (G120).** `llm._fit_to_context` asks the server for
  its served window (`/v1/models` `max_model_len`, up to 262,144 native for `Sunbird/Sunflower-Qwen3.5-9B` without YaRN) and the
  prompt's exact token count (`/tokenize`); over budget it drops the oldest
  replayed exchanges whole, then shortens the passages, never the question or
  the answer-language instruction. The streamed answer, which had no budget,
  goes through it too. Without `/tokenize` it estimates 2.5 characters per token.
- History passed to both query rewriting and LLM generation
- Topic and guided-workflow rows use an opaque key derived from the tenant,
  authenticated subject or anonymous session, and conversation. Workflow reads,
  writes, and completion also check the tenant column.

**Personalization Memory** (`memory/`):
- Semantic facts, episodic topic labels, and short-term working state are all
  tenant-scoped. Reads and writes require active personalization consent.
- Exact facts and one episode per conversation are upserted, so retries do not
  multiply memory rows. A new value of a single-valued fact (taxpayer type,
  fiscal year, preferred language, detail level) supersedes the old one, which is
  kept with `invalidated_at`, the time it stopped being true (G121).
- The episode is derived from the whole conversation each time a turn is
  absorbed: its tax topics in the order first named ("Discussed VAT and PAYE."),
  the first as `topic_tag`, and an honest `turn_count`. Topic tags match whole
  words, read from a turn's English form (G119). Episodic storage keeps only
  these coarse labels; it does not retain raw inquiry text or inferred distress.
  `/v1/me/export` includes facts, episodes, and working state; withdrawal and
  erasure remove all three.
- Facts are read from the taxpayer's own words and from a Luganda or Swahili
  turn's English form; a fact found only in the English form keeps 0.95 of its
  confidence and records `extractor_model = rules-v1+en`.
- Streamed turns (SSE, WebSocket) are written to memory once the reply is final
  (`_finish_streamed_turn`); before, only the LangGraph branch wrote them. A call
  turn heard below `RECEPTIONIST_MEMORY_MIN_ASR_CONF` (0.6) is answered but not
  written: a mishearing must not become a fact about the caller.
- Working state is stored in the selected shared analytics backend with a
  30-minute expiry, so a new worker can continue using the same state. Its
  `last_topic` is the topic the taxpayer named (or the guided journey), not the
  role that answered.
- **4-Tier Cognitive Architecture (`memory/`)**:
  - **Working Memory**: In-dialogue slot tracking and entity extraction (business names, locations/districts, amounts, reference numbers) synthesized into `## Current Session Taxpayer Working Context` without violating privacy boundaries.
  - **Episodic Memory**: Coarse conversational summaries, interaction timelines, and topic tags.
  - **Semantic Memory**: Granular, confidence-filtered fact triplets with statutory citations (`memory_store`, `memory_recall`, `memory_discard` tools).
  - **Procedural Memory**: skills and 12 curated trajectory exemplars (`ProceduralMemory`, `TrajectoryExemplar`). A question gets a **Procedure Outline** (operating sequence only) in its prompt only when it satisfies a curated exemplar's reviewed trigger (`_CURATED_TRIGGERS`): 3% of the FAQ corpus, each on its own procedure. The former any-two-shared-words rule fired on 82% and named a different procedure about 19 times in 20. Exemplars carry procedure, not law: no legal bases, forms or deadlines reach the prompt from memory, because the answer contract cites legal bases verbatim and those must come from retrieved passages or tool results. Recorded and imported trajectories are validated (finite score in [0, 1], identifier tools), PII-redacted, bounded (`MAX_DYNAMIC_EXEMPLARS`), never matched to natural-language questions, and can never take a curated id (G245, OWASP ASI06).
- **Standardized Handoff Envelopes (`memory/handoff.py`)**: Structured handoff packets (`HandoffEnvelope`, `HandoffGoal`, `EvidenceCard`, `RecommendedAction`) providing cross-agent and officer desk state continuity, zero-repetition `verified_slot_bank`, `affect_trajectory`, and `pending_background_tasks`.
- **Context Compactor & Rot Analytics (`compactor.py`)**: Native 256K / 262,144 token budget monitoring (`Sunbird/Sunflower-Qwen3.5-9B` without YaRN) with dynamic observation compaction and context rot risk alerting.
- **Scoped Output Redaction Exemption (G143)**: `_ACTIVE_USER_STATED_IDS` ContextVar threads user-declared TINs/PRNs into `OutputGuard.redact_pii()`, preventing response censorship of the taxpayer's own identifiers.

**Where state lives** (the session / user / app / invocation scopes of Google
ADK's state model, mapped onto this system):

| Scope | Lifetime | Here |
|---|---|---|
| Invocation (`temp:`) | one turn | the English router form, the language decision, `turn_out` |
| Session (no prefix) | one conversation | `conversations` turns (+ English forms), topic (`conversation_topics`), guided journey (`workflow_sessions`), answer language (the stored turn locale) |
| User (`user:`) | across conversations, consented | `user_profiles`, `user_facts`, `episodic_summaries`, working state |
| App (`app:`) | everyone | the corpus, rate tables, flags |

A signed-out taxpayer's session is the browser's chat session id (`X-Session-ID`,
`App/frontend/src/lib/chatSession.ts`). It is browser-wide, not per tab, and lives as
long as the server keeps the conversations. A call started from the chat opens a new
session carrying the chat's task and a coarse English account of it, only for the
chat's owner (`receptionist/carryover.py`).

**WebSocket resume** (`chat_ws_v2.py`):
- Each completed response has a server-minted response ID. Resume requires an
  unexpired mapping for that exact response, user, tenant, and conversation.
- Anonymous sockets do not advertise durable resume. Same-socket history remains
  connection-local and bounded; it is not a cross-replica checkpoint.

**Semantic Cache** (`cache.py`):
- Shares embedding model with retriever
- Two tiers, both keyed on `cache_generation()` (G277): the freshness index stamp,
  the corpus hash of the collection the retriever bound to, a digest of the
  active tombstones and the fiscal year in force. A reindex, a re-bind, a new
  tombstone or 1 July retires every entry in both tiers; an unreadable tombstone
  store yields a fresh value per read, never a reusable one.
- Exact tier: casefolded, punctuation-stripped question.
- Similarity tier: cosine ≥ 0.92 **and** the same content terms once filler
  ("steps", "Uganda", "please") is set aside **and** negation parity
  (`_cache_queries_compatible`). The threshold chooses between phrasings of one
  question; it never decides whether two questions are the same.
- Redis: similarity entries live under `{prefix}sem:<tenant/locale/generation>:`
  and are read in one pipeline; exact entries under `{prefix}<tenant>:exact:`.
- Not stored: blocked, abstained, escalated, withheld and ticket turns, and
  answers served from keyword fallback while Qdrant is configured but
  unavailable (`HybridRetriever.serving_degraded()`).
- TTL-based expiry (default: 1 hour); LRU eviction at `CACHE_MAX_SIZE` (1000)
- `ura_semantic_cache_lookups_total{backend,outcome}` — `hit_exact`,
  `hit_semantic`, `guard_rejected` (a ≥ 0.92 neighbour refused by the guard),
  `miss`
- Thread-safe: all operations under `threading.Lock`

### Phase 5: Observability

**OpenTelemetry** (`tracing.py`):
- Parent span: `rag.pipeline` with `gen_ai.system`, `gen_ai.request.model`, `gen_ai.prompt.length`
- Child spans: `rag.query_rewrite`, `rag.input_guard`, `rag.cache_lookup`, `rag.hybrid_search`, `rag.corrective_rag`, `rag.llm_generate`, `rag.output_guard`, `rag.grounding`
- Metrics: `gen_ai.client.token.usage` counter, `gen_ai.retrieval.duration` histogram
- PII safety: logs query length, not content

**Prometheus** (`analytics.py`):
- `GET /metrics` endpoint for scraping
- Request counters, latency histograms, error counters, escalation counters

**Analytics Dashboard**:
- `GET /v1/analytics/dashboard` — uptime, request stats, session stats, conversation stats, feedback summary

### Phase 6: Safety & Guardrails

**OWASP LLM Top 10 (2025) Coverage:**

| Control | Implementation | Module |
|---------|---------------|--------|
| LLM01 Prompt Injection | 11 regex patterns + max length + system prompt isolation; replayed history neutralised (`scan_replayed_text`, G123 / OWASP ASI06) | `guardrails.py` → `InputGuard`, `context_manager.english_view` |
| LLM02 Sensitive Info Disclosure | Uganda-specific PII redaction (TIN, NID, phone, email, cards, passport; official @ura.go.ug emails preserved) | `guardrails.py` → `OutputGuard.redact_pii()` |
| LLM03 Supply Chain | Pinned deps, Trivy scanning, SBOM, SHA-256 integrity | `requirements.txt`, CI/CD |
| LLM04 Data Poisoning | Provenance tracking, quality gates, local inference | `governance/`, `ml/pipelines/` |
| LLM05 Improper Output | XSS strip, HTML sanitize, suspicious link removal | `guardrails.py` → `OutputGuard.sanitize()` |
| LLM09 Misinformation | Runtime faithfulness scoring, grounding disclaimer, calibrated abstention | `guardrails.py` → `OutputGuard.check_grounding()` |
| LLM10 Unbounded Consumption | Rate limiting, bearer auth on indexing, semantic cache | `main.py` (slowapi), `cache.py` |

**Corrective RAG** (`corrective_rag.py`):
- Triggers when average calibrated relevance (`gating_relevance`) < `CORRECTIVE_RAG_THRESHOLD_NORM` (default: 0.50); `CORRECTIVE_RAG_THRESHOLD` is logged only
- Re-retrieves with expanded query + domain context, in the question's locale (translated leg kept; English spelling correction for English only, G280)
- Merges and deduplicates by chunk_id, re-prunes against the merged best, keeps results only if quality improved

**Protected translation & statutory projection** (`mt.py`, `service.py`, `llm.py`):
- Figures are masked behind opaque sentinels (`#NMBRA#`) *before* a reply is handed to a translator and restored verbatim afterwards — `mt.protect_figures` / `mt.restore_figures`, wired in `service.localize_reply`. A translator that never sees a digit cannot paraphrase one, so figure mutation is prevented rather than detected.
- Only the digits are masked. The currency code and the percent sign stay visible because they are the cue the target language needs to build the right construction: Luganda renders a rate as "ebitundu 18 ku buli kikumi" and can only do so if it can still see that 18 was a percentage.
- `figures_survived` remains as the assertion, not the mechanism. It still fires when a tier drops a sentinel outright rather than mutating it, and English remains the fallback for that case.
- A tier that cannot carry the sentinels (an NMT model that drops unknown tokens) fails a guard and is retried **unprotected**, which is the pre-existing behaviour. Protection therefore only ever adds vernacular coverage, at the cost of one extra round trip on a path that was already failing. `MT_PROTECT_FIGURES=false` is the kill switch.
- This replaced `heal_vernacular_figures`, which repaired MT output after the fact. Repairing means guessing where a number belonged; guessing wrong wrote a figure into a sentence that never had one, and narrowing it until it could not guess left it unable to fire at all. See G55 in `docs/GAPS_AND_AGENTIC_ROADMAP.md`.
- A `## Figure cross-check` block lists every figure found in the retrieved passages, each with the citation index of the passages that state it, and instructs the model not to state a figure absent from the list or attribute one to a passage it is not listed against (`llm.extract_statutory_context`). It is an allowlist derived from what was retrieved — it constrains the model to the retrieved figure set; it does not validate that the corpus itself is correct, which is a corpus-provenance concern.
- The block is built from the **prepared** passage text — already scrubbed by `scan_retrieved_text` and already trimmed to the token budget — not from the raw retrieval payload. Reading the raw payload put it behind both defences: a figure inside an injected span was mined after the passage body had been redacted, and a figure trimmed away for budget was projected with no passage left to support it.
- Only the figure and its indices are emitted, never a quotation of the surrounding prose. Passage bodies are isolated inside hash-bound `<passage>` spotlight markers, and lifting a clause out to caption a number would move attacker-controlled text outside that isolation for attribution the citation index already gives.
- Ordering follows retrieval rank and truncation is declared in the block rather than silent, because a partial list that reads as exhaustive is worse than no list.

**Versioned Multilingual Parallel FAQ Corpus & Direct Native Match** (`faq_corpus.py`, `service.py`, `mt.py`):
- High-frequency statutory FAQ CSVs in `Data/dataset/ura_*_faqs.csv` (e.g. VAT, Corporation Tax) contain canonical trilingual columns: `question`, `answer`, `question_lg`, `answer_lg`, `question_sw`, `answer_sw`.
- During corpus compilation (`python -m app.faq_corpus`), `_validate_vernacular_figures` tests every localized answer with `mt.figures_survived`, enforcing statutory rates (18%, 30%), currency limits, and calendar deadlines at build time.
- `_simple_search` in `service.py` executes a direct `_vernacular_pass` against the pre-indexed native terms when `locale in ("lg", "sw")`. Matched rows return pre-verified vernacular answers with `< 50 ms` latency, completely skipping runtime machine-translation round trips and eliminating figure drift under load.
- When an exact native FAQ match is not found, queries fall back seamlessly to lazy translate-then-retrieve (`translate_query_for_retrieval`), ensuring comprehensive coverage.

**Escalation** (`guardrails.py` → `OutputGuard.should_escalate()`):
- Low faithfulness score (< 0.25)
- No retrieval results
- Consecutive low confidence (≥ 3)

## Module Map

```
App/backend/app/
├── main.py              # 50+ FastAPI routes, SSE, CORS, rate limiting, auth, lifecycle
├── models.py            # Pydantic v2 schemas (chat, speech, export, auth, feedback)
├── service.py           # ChatModel — 12-stage RAG orchestrator + agentic routing
├── topics.py            # G6 conversation topic catalog + persist/follow-up bind
├── llm.py               # Qwen3-8B generation (local + vLLM + tool-calling + LoRA)
├── query.py             # Query rewriting (abbreviations, spelling, coreference, lang detect)
├── cache.py             # Semantic response cache (memory or Redis backend)
├── corrective_rag.py    # Corrective re-retrieval + clarification detection
├── guardrails.py        # InputGuard + OutputGuard (OWASP LLM Top 10 2025)
├── retriever.py         # HybridRetriever (bge-m3 + BM25 + RRF + rerank) + CircuitBreaker
├── indexer.py           # JSONL → Qdrant indexing (Qdrant → Vectorize → keyword tiers)
├── faq_corpus.py        # ura_*_faqs.csv + teacher-QA → validated JSONL → vector docs
├── pdf_corpus.py        # Statutory & 10-MDA PDF harvester + hierarchical chunk JSONL (186 PDFs → 5,639 chunks)
├── crawl_corpus.py      # Crawl pages → chunk JSONL (newest capture per URL, above floor)
├── web_research.py      # 8-trigger live web research planner & Tier-1/2/3 source authority (ADR-0008)
├── ephemeral_pdf_sandbox.py # Ephemeral tmpfs PDF/XLSX sandbox + GPU micro-reranking (ADR-0008)
├── corpus_sync.py       # Self-updating Qdrant comparator, hot-swap & web_sync_ledger (ADR-0008)
├── scenario_planner.py  # Deterministic multi-tool planner & Verified Statutory Facts injection
├── calculator_router.py # Single-calculator fast path & multi-turn amount carryover
├── premise_guard.py     # False-premise detector & legitimate statutory modifier allowlist
├── speech_service.py    # ASR (Whisper) + TTS (Piper) + MT (prompted/ONNX)
├── sunbird.py           # Sunbird AI cloud fallback (Ugandan languages)
├── tracing.py           # OpenTelemetry GenAI 2025 semconv tracing
├── analytics.py         # Prometheus-compatible metrics middleware
├── database.py          # SQLite WAL store (11 tables, retention TTLs, migrations)
├── postgres.py          # PostgreSQL backend (opt-in, drop-in substitute for database.py)
├── flags.py             # Feature flag registry (85 flags, env-backed, cohort rollout)
├── resilience.py        # Circuit breaker (exponential backoff, CLOSED→OPEN→HALF_OPEN)
├── pdf_export.py        # Branded PDF conversation/tax summary export
├── evaluation.py        # RAG evaluation harness (8 metrics)
│
├── auth/                # JWT authentication (Phase 14)
│   ├── jwt_auth.py      #   HS256 (dev) / RS256 (prod OIDC) verification + JWKS cache
│   ├── dependencies.py  #   FastAPI DI: current_user, require_user, require_role
│   └── models.py        #   AuthUser, UserProfile, ConsentReceipt
│
├── agents/              # Supervisor + specialist routing (Phase 14-C)
│   ├── supervisor.py    #   Query router: 7 routes (RAG, TOOLS, SPECIALIST, CLARIFY, ESCALATE)
│   ├── state.py         #   AgentRoute enum, RouteDecision dataclass
│   └── graphs/          #   LangGraph orchestration (scaffolded for Phase 15)
│
├── tools/               # LLM & deterministic tool-calling framework (72 tools across 18 namespaces)
│   ├── __init__.py      #   Tool base class + ToolRegistry (auto-registration)
│   ├── calculators.py   #   VAT, PAYE, capital gains, corporation tax, customs duty,
│   │                    #   rental income tax, withholding tax (FY-versioned rates)
│   ├── statutory_2026.py #  BIK, residency, FX, stamp duty, DST, gaming, advance vehicle,
│   │                    #   presumptive, startup exemption, penalties/interest, rental pooling, objection validity
│   ├── mda_interdependence.py # 6 Cross-MDA NTR/PRN, life-event onboarding, B2G/UESW clearance,
│   │                    #   Nil-Return Shield, Voluntary Disclosure/Instalment & EFRIS 5% refund tools (ADR-0009)
│   ├── rates.py         #   Tax rate lookups & multi-year comparison by category
│   ├── calendar.py      #   Filing deadlines, fiscal year, current date
│   ├── escalate.py      #   Human escalation tool
│   └── rag_tool.py      #   Knowledge base search tool (search_ura_knowledge_base)
│
├── workflows/           # Guided multi-step workflows (21 YAML flows + Journey Copilot, ADR-0009/0010)
│   ├── registry.py      #   WorkflowSession state machine + WorkflowRegistry
│   ├── loader.py        #   YAML workflow definition loader
│   ├── slots.py         #   Slot validators (TIN, email, phone, date, currency)
│   └── flows/           #   21 YAML definitions (TIN, filing, payment, customs, objections, TCC,
│                        #   mda_ntr_resolution.yaml, new_taxpayer_and_relief.yaml, calc_* flows)
│
├── memory/              # Consent-gated, tenant-scoped personalization memory (Phase 16)
│   ├── service.py       #   Unified memory interface (MemoryService)
│   ├── semantic.py      #   User facts with time-based decay
│   ├── episodic.py      #   Conversation summaries by topic
│   ├── working.py       #   Standalone in-process helper; service uses shared TTL storage
│   ├── handoff.py       #   Standardized HandoffEnvelope v2 (SLAContext, ToolExecutionReceipt)
│   ├── resolution_ledger.py # Longitudinal ResolutionCase persistence across Voice & Web
│   ├── extractor.py     #   Rule-based fact extraction from turns
│   └── decay.py         #   Time-based fact decay
│
├── receptionist/        # Full-duplex Pipecat/LiveKit voice receptionist & Officer Call Desk
│   ├── pipeline.py      #   Cascaded & multilingual Pipecat pipeline assembly
│   ├── brain.py         #   Voice turn orchestration, clarification, spoken confirm, transfer
│   ├── hybrid_s2s.py    #   Hybrid S2S vs regulated cascaded speech router & semantic endpointing
│   ├── prosody.py       #   Zero-VRAM acoustic prosody & caller affect analyzer
│   ├── spoken_figure_guard.py # Spoken-figure round-trip verification & dictation pacing
│   └── transfer.py      #   Ticket creation, HandoffEnvelope v2 dispatch, SLA countdown
│
└── audit/               # Immutable audit ledger (Phase 21, UDPA compliance)
    ├── ledger.py        #   Hash-chained append-only log (SHA-256)
    ├── verifier.py      #   Chain integrity verification
    └── merkle.py        #   Merkle tree proofs for snapshots
```

## Agent Runtime (Phase 14)

When `FLAG_AGENTIC_MODE=true` (the default after the English golden-set
gate ≥ 0.95), the supervisor classifier (`agents/supervisor.py`) routes
queries before retrieval. G6 topic persistence (`topics.py`) runs on
every turn: the catalog label is merged into the prompt and anaphoric
queries are prefixed for retrieval.

Three deterministic fast paths intercept BEFORE routing
(`ChatModel._maybe_handle_fast_paths`, REST and streaming parity):

1. **TIN clarification** — an untyped registration ask ("how do I register
   for a TIN/pin?") asks individual-vs-organisation first (one-question
   `tin_procedure_help` flow), then returns the matching curated template;
   typed asks answer immediately.
2. **Calculator** (`calculator_router.py`): a message that already carries
   the figures ("VAT on 1.5m", Luganda `vati`) is answered instantly from
   the registered calculator tool through the MCP client
   (`retrieval_mode="calculator"`, no LLM). The conversation remembers that
   tool, so "what about 2 million?" recomputes the same tax. A bare amount
   with no prior tool still shows PAYE, VAT, and services withholding, with
   every figure taken from the rate table. Missing figures start the matching
   `calc_*` guided workflow, or ask for the missing slot when workflows are
   off. Defaults (residency, VAT direction, landlord type, annual→monthly)
   are stated as assumptions. A withholding line that matches both services
   and a management fee asks which rate applies. A tool result that fails
   its output schema is not spoken.
3. **Rate lookup** — "what is the current VAT rate?" answers with the real
   figure from the versioned FY rate table (gated on the authority-manifest
   freshness check) instead of retrieval passages.

The `TOOLS` route below remains the fallback for phrasings the fast paths
abstain on.

| Route | Trigger | Handler |
|-------|---------|---------|
| `RAG` | General knowledge questions | Standard 12-stage pipeline |
| `TOOLS` | Calculations, rates, deadlines | `llm.generate_with_tools()` with calculator/rate tools |
| `TAX_SPECIALIST` | Income tax, PAYE, CIT questions | Specialist system prompt + narrowed tool scope |
| `CUSTOMS_SPECIALIST` | Import duty, tariff questions | Specialist system prompt + customs tools |
| `CLARIFY` | Ambiguous single-word queries | Ask for more details |
| `ESCALATE` | Complex cases, account-specific | Create ticket, return handoff packet |
| `WORKFLOW` | Procedural (registration, filing) | Guided slot-filling workflow |

**Tool-calling loop** (`llm.generate_with_tools()`): bounded to 3 iterations. Parses `<tool_call>` XML blocks, dispatches through `ToolRegistry.call()`, feeds results back as `tool` role messages.

## Workflow Engine (Phase 15 & ADR-0009 / ADR-0010 — 21 Guided Journeys)

YAML-defined guided workflows (`App/backend/app/workflows/flows/`) for procedural tax and cross-agency tasks:

1. **TIN Registration (`tin_registration.yaml`, `tin_procedure_help.yaml`)** — Individual & non-individual TIN onboarding with NIRA NIN pre-flight guidance
2. **Return Filing (`return_filing.yaml`)** — Monthly/annual return preparation, schedules, and nil-return walkthroughs
3. **Payment & PRN (`payment.yaml`)** — PRN generation, bank/mobile money channels, and receipt verification
4. **Cross-MDA NTR & PRN Payment Resolution (`mda_ntr_resolution.yaml`, `ADR-0009`)** — 3-hop PRN settlement troubleshooting (`Bank/MoMo -> URA e-Tax -> MDA portal`) across all 10 MDAs (`MoWT` UDLS, `DCIC` e-Passport, `NIRA`, `MLHUD` UgNLIS, `UPF` EPS, `URSB` OBRS, `NEMA` 30%/70% EIA, `UNBS` PVoC, `NDA`, `KCCA` IRAS) and `Form DT-3008` wrong-head refund steps
5. **New Taxpayer Onboarding, Nil-Return Shield & Penalty Relief (`new_taxpayer_and_relief.yaml`, `ADR-0009`)** — Day-1 60-Second Nil-Return Shield (`UGX 200,000/month` penalty prevention), `0%` (`UGX 0`) Presumptive Tax disclosure below `UGX 10,000,000`, Voluntary Disclosure (`TPC Act s.66(1a)` / `Form DT-10307` 100% penalty/interest waiver), `s.40` TCC-unlocking instalment plans, FY2026-27 `5%` consumer EFRIS e-receipt refund (`UGX 2,000,000` threshold), and lost-SIM/email TIN recovery
6. **Customs & Motor Vehicle (`customs_declaration.yaml`, `motor_vehicle_registration.yaml`)** — EAC CET import clearance, `UESW` Single Window holds, and motor vehicle transfer/first registration
7. **Objections, TCC, VAT, EFRIS, Refunds & Amendments (`objection_filing.yaml`, `tax_clearance_certificate.yaml`, `vat_registration.yaml`, `efris_enrollment.yaml`, `refund_claim.yaml`, `registration_amendment.yaml`, `audit_invoice_compliance.yaml`, plus 6 `calc_*` flows)** — Interactive procedural and calculation flows with Prerequisite DAGs, A2UI forms, pushdown `goal_stack`, and Step-Up Auth migration (`ADR-0010`)

State machine: `WorkflowRegistry.advance(session, user_input)` validates slots, advances steps, calls tools, and persists to `workflow_sessions` table.

## Speech & Voice Pipeline Architecture (2026 Production & GPU Topology)

The system deploys an enterprise-grade multimodal speech and streaming voice architecture tailored for Ugandan taxpayer interaction across English and native African languages (Luganda, Swahili, Runyankole, Acholi).

### Local Docker GPU & Ngrok Serving Topology

The local serving stack operates across dedicated hardware allocations on NVIDIA RTX A6000 GPUs:

```
[ Public Ngrok Ingress: https://*.ngrok-free.dev ]
                       │
                       ▼
            [ ura-app-frontend (Next.js 16+, port 3032) ]
                       │
         ┌─────────────┴──────────────────┐
         ▼                                ▼
[ /api/v1/calls/stream ]     [ voice mode: /api/v1/asr,  ]
         │                   [ then /api/v1/chat/stream  ]
         │                                │
         └─────────────┬──────────────────┘
                       ▼
        [ ura-app-api:gpu (FastAPI on GPU 3, port 8083) ]
         ├── ASR: Sunbird/asr-whisper-large-v3-salt (cuda:0)
         ├── TTS: Sunbird/spark-tts-salt + BiCodec (cuda:0)
         ├── Dense: BAAI/bge-m3 (cuda:0)
         ├── Reranker: mixedbread-ai/mxbai-rerank-base-v2 (cuda:0)
         └── Prompted MT: via vLLM Sunflower
                       │
         ┌─────────────┼───────────────────────────┐
         ▼             ▼                           ▼
[ ura-app-vllm ] [ ura-app-qdrant ]      [ ura-app-orpheus-tts ]
 (GPU 2: 262k Native) (v1.19.0 Vectors)   (Optional Sidecar: 8100)
```

1. **vLLM Generator Pod (`ura-app-vllm-sunflower`)**:
   - Dedicated to **GPU 2** (40.1 GB VRAM allocated, 85% utilization ceiling).
   - Serves `Sunbird/Sunflower-Qwen3.5-9B` (Sunflower v2) with **native 256K / 262,144 context window without YaRN** (`max_position_embeddings: 262,144`, `--max-model-len 262144`, FP8 weights + FP8 KV cache).
   - Supports native tool-calling via `--enable-auto-tool-choice --tool-call-parser hermes` and chunked prefill (`max_num_batched_tokens: 16,384`).
2. **API & Speech Host (`ura-app-api:gpu`)**:
   - Dedicated to **GPU 3** (12.6 GB VRAM allocated, 36.5 GB headroom).
   - In-process CUDA backends: `Sunbird/asr-whisper-large-v3-salt` for speech recognition and `Sunbird/spark-tts-salt` for local neural synthesis with pinned BiCodec checkout (`/opt/spark-tts`).
   - BAAI/bge-m3 dense embedder and cross-encoder reranker both run on CUDA.
3. **Voice Sidecar (`ura-app-orpheus-tts`)**:
   - Optional dedicated sidecar serving `Sunbird/orpheus-3b-tts-multilingual` (`http://orpheus-tts:8100`) providing ultra-fast native Luganda, Swahili, and English speech generation (~350ms TTFB).
4. **State & Caching Subsystems**:
   - `ura-app-qdrant`: Vector database on port 6333 with HNSW indexing and alias-based zero-downtime collection swapping.
   - `ura-app-redis`: Redis 7.4-Alpine for distributed rate-limiting and semantic cache.
   - `ura-app-connectors`: Sandboxed simulator gateway over MCP `2026-07-28` protocols.

---

### Voice & Speech Serving Flows

#### 1. Full-Duplex Real-Time Voice Calls (`/v1/calls/stream` & `/v1/voice/chat/stream`)
- **Transport**: WebSockets / WebRTC audio streaming managed via **Pipecat** and **LiveKit**.
- **Voice Activity Detection (VAD)**: Energy-based VAD with hysteresis (`VOICE_VAD_ENERGY_THRESHOLD=0.015`, `VOICE_VAD_SILENCE_MS=600ms`), filtering noise without heavy dependencies.
- **Acoustic Entity & Lexicon Repair (`receptionist/lexicon.py`)**:
  - Automatically reconstructs acoustic numbers (NINs, PRNs, phone numbers, money figures) and corrects phonetic homophones across Luganda (`ebizimbe`, `amagoba`, `omusolo`) and Swahili (`ushuru`, `forodha`, `mahindi`).
- **Low-Latency Streaming Synthesis**: Sentence-chunked audio streaming emits audio chunks as each sentence finishes generation, achieving sub-second initial voice latency.
- **Officer Call Desk Handoff**: When dispute or distress thresholds trigger escalation, the call transfers seamlessly to live URA staff at `/v1/officer/calls/*` with caller transcript carryover, collision locking, SLA timer, and post-call CSAT rating capture.

#### 2. REST Voice Chat Pipeline (`POST /v1/voice/chat`)
- Ingests audio payloads (PCM, WAV, MP3).
- **Pipeline**: Inbound Audio → ASR Transcribe → Language Detection (Lingua) → [Prompted MT to English] → RAG / Tool Execution → [Prompted MT to Target Locale] → TTS Synthesize → Outbound Audio + Transcript.
- One request for the whole turn. The web chat's voice mode no longer uses it (ADR-0016; see 4).

#### 3. Streaming Reply Speech (`POST /v1/tts/stream`)
- Streams a reply's speech as NDJSON, one piece (Ogg/Opus where the browser plays it, else WAV) as soon as each sentence-sized piece is voiced. The web UI plays the pieces back to back, for a Listen tap, for narration, and in voice mode.

#### 4. Web Chat Voice Mode (`/v1/asr` → `/v1/chat/stream`, ADR-0016)
- The browser records and decides the end of the turn itself (`endOfTurn.ts`: a speech-band level detector, a 1.2 s pause by default, shown as a countdown ring).
- **Pipeline**:
  1. `POST /v1/asr?domain=tax` transcribes the turn, with the same `repair_asr_entities` as `/v1/voice/chat`.
  2. The transcript is shown at once as the taxpayer's turn.
  3. `POST /v1/chat/stream` answers it, with `locale` set to the language heard and `locale_explicit: true`.
  4. `POST /v1/tts/stream` reads the answer once it has finished arriving.
  5. The mic opens again.
- `VoiceModeDock` names each step (listening, hearing you, sending when you stop, got it, the answer's step, speaking).

---

### Multi-Tier Fallback Chains

The speech engine prioritizes local neural models on GPU, falling back through progressively wider tiers to guarantee zero-silence resilience:

#### ASR (Speech-to-Text) Fallback Hierarchy
1. `⓪ Whisper-SALT`: `Sunbird/asr-whisper-large-v3-salt` (Local GPU `cuda:0`, 1.5B params). Single fine-tune natively transcribing English, Luganda, Swahili, Runyankole, and Acholi. Uses forced language token IDs (`lg`: 50355, `sw`: 50318, `en`: 50259).
2. `① Whisper + LoRA`: In-process HuggingFace `openai/whisper-small` with language-specific fine-tuned PEFT LoRA adapters.
3. `② Local Sherpa ASR`: Offline ONNX/Sherpa runtime for CPU fallback.
4. `③ faster-whisper`: CTranslate2 INT8 multilingual engine.
5. `④ Sunbird Cloud API`: `api.sunbird.ai/tasks/stt` cloud service for Ugandan vernaculars.
6. `⑤ Cloudflare Workers AI`: `@cf/openai/whisper-large-v3-turbo` as ultimate cloud failover.

#### TTS (Text-to-Speech) Fallback Hierarchy
1. `⓪.5 Orpheus-3B-TTS`: Local GPU sidecar (`http://orpheus-tts:8100`) delivering sub-400ms TTFB for Luganda, Swahili, and English. If the sidecar is unreachable, a 30-second cooldown is enforced to eliminate timeout delays on subsequent calls.
2. `⓪.75 Spark-TTS-SALT`: In-process local GPU (`cuda:0`) neural voice (`Sunbird/spark-tts-salt` with BiCodec tokenizer/detokenizer) for `lg`, `sw`, `ach`, `nyn`.
3. `① Edge Neural TTS`: Microsoft Edge Neural TTS (`edge-tts`) for English voices (`en-US-JennyNeural`, `en-GB-SoniaNeural`).
4. `①.5 Local Piper / Sherpa`: Offline VITS/Piper voices loaded from `artifacts/speech/tts/speakers`.
5. `② Sunbird Cloud API`: `api.sunbird.ai/tasks/tts` native voice endpoints.
6. `③ Cloudflare Workers AI TTS`: Remote cloud voice fallback.

#### Machine Translation (MT) Hierarchy
1. `Prompted MT (Local Sunflower-14B-FP8)`:
   - Configured via `RETRIEVAL_MT_BACKEND=local` and `REPLY_MT_BACKEND=local`.
   - Utilizes the in-memory Sunflower model for zero-egress, high-fidelity bidirectional translation between English and Luganda/Swahili.
2. `Sunbird Cloud Translation API`:
   - Remote fallback (`api.sunbird.ai/tasks/translate`) protected by circuit breaker and per-account retry budget.
   - **Figure-Fidelity Guard (`mt.py`)**: Asserts that financial numbers, percentages, and statutory tax rates survive translation intact without corruption or omission.

---

### Recent Enhancements & Gap Closures (October 2026)

1. **Vernacular Query Rewriting (G135)**:
   - `query.rewrite()` detects language/locale and automatically bypasses English dictionary fuzzy spell correction when handling African languages (`lg`, `sw`, `nyn`, `ach`), preventing native vocabulary (e.g. Swahili `nini`) from being mutated into English words (`nin`).
2. **Operator Readiness Probe Observability (G137)**:
   - `/ready` endpoint introspects and advertises active cross-encoder reranker presence, dense device (`cuda:0`), procedural memory readiness, and local prompted MT status (`prompted_local` vs `sunbird`).
3. **Scoped Output Redaction Exemption (G143)**:
   - `_ACTIVE_USER_STATED_IDS` ContextVar threads taxpayer-declared TINs and PRNs into `OutputGuard.redact_pii()`, preventing response censorship of the user's own identifiers while ensuring payment cards and passwords remain unconditionally redacted.
4. **4-Tier Cognitive Memory Integration**:
   - Working memory entity extraction (business names, locations, amounts) combined with the curated procedure outline the question triggers, if any (operating sequence only; see Procedural Memory above).
   - Native 256K / 262,144 token allocation compactor (`Sunbird/Sunflower-Qwen3.5-9B` without YaRN) with real-time context-rot analytics (`rot_risk_threshold: 0.85`).
5. **EU AI Act Article 50(2) Compliance**:
   - Every synthesized audio WAV embeds an IPTC `trainedAlgorithmicMedia` INFO chunk, providing verifiable machine-readable marking of AI-generated synthetic speech.
6. **Hybrid Speech S2S + Regulated Cascaded Router & Spoken Figure Guard (`receptionist/hybrid_s2s.py`, `receptionist/spoken_figure_guard.py`)**:
   - Routes low-risk conversational turns through fast S2S (`s2s_fast`) while strictly enforcing the cascaded English-router + `mt.protect_figures` path (`cascaded_guarded`) whenever numbers, tax rates, TINs, or statutory claims are present.
   - Adaptive semantic endpointing extends silence tolerance (`1100ms`) mid-TIN/PRN and snaps to `320ms` on complete utterances.
   - Verifies that statutory figures survive speech normalization and synthesis (`spoken_figure_guard`) and injects dictation pauses into spelled-out TIN/PRN digit sequences.
7. **Continuous Relationship Orchestration & Multi-Agent Specialist DAG (`memory/resolution_ledger.py`, `agents/supervisor.py`, `agents/graphs/main_graph.py`)**:
   - Consent-gated `ResolutionCase` ledger tracks multi-step resolution progress across Voice Calls, Web Chat, and background `mcp_tasks` with bidirectional carryover (`carryover.py`).
   - `HandoffEnvelope` v2 adds `SLAContext`, `ToolExecutionReceipt`, `caller_affect`, and one-click `officer_macros` for both peer-specialist DAG chaining and live Officer Call Desk handoffs (`officer_voice_copilot`).

### VAD & Audio Handling

Energy-based VAD with hysteresis (numpy only, no silero-vad dependency):
- **Energy threshold:** configurable via `VOICE_VAD_ENERGY_THRESHOLD` (default 0.015)
- **Silence duration:** `VOICE_VAD_SILENCE_MS` (default 600ms) before declaring utterance end
- **Min speech duration:** `VOICE_VAD_MIN_SPEECH_MS` (default 250ms) to filter false triggers
- **Max utterance:** `VOICE_VAD_MAX_UTTERANCE_S` (default 30s) to bound resource usage

### Voice Consent & Governance
- **Consent purposes:** `voice_recording` (required for audio processing), `voice_analytics`
- **Privacy:** Raw audio never stored by default — only SHA-256 hash in audit trail
- **Audit:** `voice_audit_log` table with immutable event logging, chained into existing `AuditLedger`
- **Retention:** configurable TTLs (raw audio 24h, transcripts 90d, analytics 365d)
- **Admin:** `GET /v1/admin/voice_audit` for regulatory review

### Voice Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `voice_ws_connections_total` | counter | WebSocket connections opened |
| `voice_ws_active_connections` | gauge | Active WebSocket sessions |
| `voice_stream_asr_latency_seconds` | histogram | Streaming ASR latency |
| `voice_stream_tts_first_chunk_seconds` | histogram | Time-to-first-TTS-byte |
| `voice_stream_total_latency_seconds` | histogram | End-to-end turn latency |
| `voice_barge_in_total` | counter | Barge-in interruptions |
| `voice_vad_utterances_total` | counter | VAD-detected utterances |
| `ura_autonomous_resolution_total` | counter | End-to-end autonomous vs escalated resolution by channel, flow, and locale |
| `ura_customer_effort_turns` | histogram | Turns and clarifications required per call/chat resolution |
| `ura_voice_hybrid_route_total` | counter | Hybrid speech routing split (`s2s_fast` vs `cascaded_guarded`) |
| `ura_spoken_figure_verification_total` | counter | Spoken figure verification results (`verified`, `fallback_text_only`) |
| `ura_handoff_envelope_completeness_ratio` | histogram | Completeness score `[0..1]` of `HandoffEnvelope` v2 packets |
| `ura_relationship_resumption_total` | counter | Cross-channel `ResolutionCase` resumption and completion counts |
| `ura_eq_interventions_total` | counter | Emotional intelligence (EQ) interventions (`de_escalated_autonomous`, `escalated_officer`) |
| `ura_workforce_delegation_total` | counter | Coordinated Human <-> AI workforce delegations (`officer_to_ai`, `ai_to_officer`) |
| `ura_slot_repetition_prevented_total` | counter | Tool/workflow parameters bound from `verified_slot_bank`, `semantic_memory`, or `dag_receipt` without re-asking the taxpayer |
| `ura_workflow_tangent_resumed_total` | counter | Mid-workflow policy side-questions answered and seamlessly resumed on the parked step |
| `ura_officer_copilot_edit_distance_ratio` | histogram | Normalized edit distance `[0..1]` between AI co-pilot draft and final officer reply |

## Feature Flags (`flags.py`)

All major subsystems are behind feature flags for progressive rollout:

| Flag | Default | Controls |
|------|---------|----------|
| `corrective_rag` | on | Re-retrieval on low quality |
| `semantic_cache` | on | Cache similar queries |
| `query_rewrite` | on | Spell/abbreviation/coreference |
| `reranker` | on | Runtime kill switch read by `HybridRetriever._rerank`; the cross-encoder is only loaded when `RERANK_ENABLED=true` (G126). Off, hits carry no calibrated score: pruning stands down and abstention reads the lexical floor |
| `workflows` | on | Guided multi-step workflows |
| `handoff_summaries` | on | Human triage packets |
| `ticket_queue` | on | Persist escalations for the staff workbench |
| `answer_overrides` | on | Staff CMS exact-match replies before retrieval |
| `tool_use` | off | LLM tool-calling |
| `agentic_mode` | on | Supervisor routing (EN golden-set gate ≥ 0.95) |
| `auth_required` | off | Enforce JWT |
| `memory_enabled` | off | Consent-gated personalization |
| `audit_ledger` | off | Hash-chained audit log |
| `voice_enabled` | off | Mobile voice features |
| `voice_streaming` | off | WebSocket streaming voice chat (VAD + barge-in) |
| `voice_consent` | off | Enforce voice-specific consent checks |
| `multilingual_routing` | off | Locale-specific supervisor patterns (lg/nyn/ach) |
| `supervisor_llm_tiebreak` | off | Small-model second opinion on low-confidence routing |
| `model_tiering` | off | Per-turn local model tier (T0 none / T1 Sunflower-Qwen3.5-9B / T2-T3 Qwen3.8-27B-FP8) from the route decision |
| `reasoner_for_translated_locales` | off | Allow lg/sw/nyn/ach turns to use the T2/T3 reasoning endpoint for English draft synthesis before Sunflower localization |
| `evaluator_optimizer` | off | Deterministic recomputation of money answers |
| `tax_graph` | off | Load the statutory knowledge graph + `tax_graph` namespace |
| `graph_fusion` | off | Fuse the graph leg into RRF (requires `tax_graph`) |
| `langgraph` | off | Route agentic requests through the graph orchestrator |
| `mcp_tasks` | off | `tasks` MCP namespace for long-running work |
| `voice_receptionist` | off | Simulated phone receptionist and staff Calls page; production media uses self-hosted LiveKit WebRTC (see the receptionist runbook) |
| `receptionist_language_detection` | off | Receptionist follows caller language (en, sw and lg all on the local engine: Whisper-SALT, Sunflower, Orpheus); caller/officer WebRTC media uses self-hosted LiveKit in production. See `docs/runbooks/voice-receptionist-demo.md` |
| `hybrid_speech_s2s` | off | Hybrid S2S + Cascaded Speech Router (`receptionist/hybrid_s2s.py`): fast conversational S2S path for low-risk turns, regulated figure-protected cascaded path for statutory/numeric turns |
| `voice_agentic_workflows` | off | Guided multi-step workflows, calculator MCP tools, and spoken confirmation tokens inside live `/v1/calls/stream` voice calls; a confirmed action is reported done only when its executor reports success (G177) |
| `multi_agent_dag` | off | Multi-specialist DAG chaining (`CUSTOMS_SPECIALIST` + `TAX_SPECIALIST` + `TOOLS`) with `HandoffEnvelope` v2 verification in `agents/graphs/main_graph.py` |
| `continuous_relationship_memory` | off | Longitudinal `ResolutionCase` persistence (`memory/resolution_ledger.py`), bidirectional `Voice <-> Web` carryover, and background `mcp_tasks` continuation |
| `emotion_aware_prosody` | off | Acoustic prosody extraction (`receptionist/prosody.py`) in `CallerAudioTap` and dynamic empathy/pacing conditioning in TTS. Affect needs the caller's words: loudness or pace alone names nothing (Phase 39; `PROSODY_ACOUSTIC_ONLY_AFFECT=true` for a validation study) |
| `spoken_figure_guard` | off | Acoustic & lexical digit verification (`receptionist/spoken_figure_guard.py`) and dictation pauses on synthesized TTS audio containing statutory figures, TINs, or PRNs |
| `officer_voice_copilot` | off | Streams live ASR transcripts, English router views, and statutory rate cards to the officer desk while a voice call is bridged to `ura_staff` |
| `eq_adaptive_retrieval` | off | Stage 0c/0b2 EQ assessment (`tools/empathy.py`, `query.py`) boosting statutory relief/instalment/waiver passages (`TPCA s.39, s.66`) on hardship/anxiety |
| `zero_effort_slot_prefill` | off | Pre-fills `WorkflowSession` and calculator slots from consented `SemanticMemory`, `WorkingMemory.verified_slot_bank`, and scanned documents |
| `workflow_specialist_bridge` | off | Allows active guided workflows (`workflows/registry.py`) to delegate mid-flow calculations to MCP calculator tools and bind results into pending slots |
| `proactive_resolution_continuation` | off | Writes localized continuation cards to `reminder_inbox` when background `mcp_tasks` or officer reviews complete on an open `ResolutionCase` |
| `officer_ai_co_execution` | off | Enables figure-protected bilingual draft replies (`officer_chat.py`) and `Officer -> AI` procedural workflow delegation |
| `low_bandwidth_voice_codec` | off | Negotiates 8 kHz G.711 μ-law / 12 kbps Opus framing (`receptionist/low_bandwidth.py`) and caption-first audio degradation on 2G/3G or Data Saver links |
| `call_drop_resume` | off | Mints HMAC-signed single-use `call_resume_ticket` for 45-second caller reconnect recovery on mobile socket drops |
| `offline_action_outbox` | off | Syncs queued PWA offline calculator receipts and workflow slots into `verified_slot_bank` and `ResolutionCase` upon reconnection |
| `action_level_audit_v3` | off | Emits structured `ActionAuditRecord` v3 (`audit/governance_v3.py`) with UTC+EAT timestamps, salted TIN HMAC, decision trace, guardrail verdicts, officer approval gate, and reversibility contract into `AuditLedger` |
| `continuous_quality_scorecard` | off | Runs 100% deterministic turn-level `InteractionQualityScorecard` evaluation across statutory accuracy, figure fidelity, policy adherence, empathy, and handoff cleanliness |
| `risk_tiered_autonomy_gates` | off | Enforces Low/Medium/High/Critical risk-tiered autonomy gates, soft-confirmation confidence floor ($\ge 0.80$), and mandatory officer approval on High-risk proposals |
| `quality_circuit_breaker` | off | Automatically throttles Medium/High-risk autonomy when rolling quality scorecard or figure fidelity drops below threshold |
| `verifiable_decision_receipts` | off | Attaches human-readable `DecisionReceipt` explanations (`audit_event_id`, statutory basis, verified inputs, reversal instructions) to consequential responses |
| `eq_multimodal_fusion` | off | Fuses multi-turn lexical distress trajectory (`tools/empathy.py`), native Ugandan vernacular hardship cues (`lg`/`sw`/`nyn`/`ach`), and voice `ProsodyProfile` into a smoothed `AffectiveState` score with affect-conditioned MT register |
| `workflow_tangent_resume` | off | Allows active guided workflows (`workflows/registry.py`) to answer mid-flow policy side-questions and seamlessly resume the parked workflow step |
| `autonomous_outcome_verification` | off | Binds missing tool arguments from consented `verified_slot_bank`, `SemanticMemory`, and prior specialist DAG receipts in `main_graph.bind_arguments()` and verifies post-action outcomes |
| `officer_preference_capture` | off | Captures PII-redacted edit diffs between AI co-pilot drafts and final officer replies in `officer_chat.py` for offline DPO/ORPO preference alignment |
| `eq_stakes_escalation` | off | Phase 38: `escalation_policy.assess_escalation` weighs affect, stakes (`text_signals.detect_high_stakes`: enforcement, a deadline today, a large amount), effort and confidence; text chat offers an officer (first when `priority`), and every ticket path takes its priority and SLA. Offers only — never escalates by itself |
| `structured_text_handoff` | off | Phase 38: text-chat tickets (auto-escalations and `POST /v1/escalate`) carry `HandoffEnvelope` v2.1 via `officer_handoff.enrich_handoff`; details to ask for are pruned against what the taxpayer already gave (slot names only, never values) |
| `workflow_emotional_offramp` | off | Phase 39: a guided flow pauses (answers kept) instead of answering distress or enforcement news with a validation error, offering an officer, the instalments question, "Continue the steps" and "Cancel"; after repeated invalid answers it offers a way out (`WorkflowTurn.failed_attempts`) |
| `wellbeing_checkin` | off | Phase 39: hopelessness said amid hardship or enforcement (this or the last two messages) gets a check-in with the crisis lines before the tax answer, once per conversation, on REST and stream alike |
| `cxp_business_hours` (`FLAG_CXP_BUSINESS_HOURS`) | off | Phase 40 (G174): timezone-aware URA CXP operating schedule (`07:00–23:00 EAT`, closing at 11:00 PM EAT). Escalations after 11:00 PM when no live officer is online queue for priority morning review/callback with SLA pause/resume (`business_hours_elapsed_seconds`) and transparent 5-locale after-hours disclosure |
| `live_web_research` (`FLAG_LIVE_WEB_RESEARCH`) | off | Phase 41: live FX lookup (`web_fx.py` via `XE.com` / Google Currency / Open ER mirror, wired into `convert_currency` and `scenario_planner.py`), 8-trigger web research planner and 3-tier source authority filter (`web_research.py`), and ephemeral `tmpfs` PDF/XLSX document sandbox (`ephemeral_pdf_sandbox.py`), wired end-to-end into `_generate_en` and `generate_retrieval_only` / `_run_chat_turn_events` |
| `auto_qdrant_web_sync` (`FLAG_AUTO_QDRANT_WEB_SYNC`) | off | Phase 41: self-updating Qdrant comparator (`corpus_sync.py`) that compares `ura.go.ug` / `ulii.org` PDFs against Qdrant, tombstones/deletes superseded documents, upserts newer editions, archives replaced `.jsonl` files, and records 7-day state in `web_sync_ledger` |
| `portal_copilot` (`FLAG_PORTAL_COPILOT`) | on | Phase 42: 4-tier URA portal task execution (`portal_copilot.py`), Claude-style HITL Allow/Disallow confirmation cards (`POST /v1/portal/tasks/execute`), inline chat form elicitation, step-up login auto-return (`returnTo`), screenshot-to-co-pilot handoff, and inbuilt stuck-user DOM co-pilot (`/portal/[portalKey]`) |

The table above lists the flags that gate a subsystem; `flags.py` holds
**86** in total, including the per-phase switches for voice, offline and
quantization. `flags.all()` is the authoritative list. Production also
forces `auth_required`, `multi_tenant`, `audit_ledger`, `ticket_queue`,
and `voice_consent` unless explicitly disabled (startup then refuses).
Remaining prototype gaps have env gates in `docs/PRODUCTION_GATES.md`.
Traceability: [prototype-production-gates-2026-08-18.md](../App/docs/traceability/prototype-production-gates-2026-08-18.md).

### Addressable rollout

A flag is not only on or off. `Rollout` targets a share of subjects, named
cohorts, or an explicit allowlist, so a change can be piloted on 1% of traffic
and widened on evidence:

```python
flags.is_enabled("model_tiering", subject=user_id)                  # percentage
flags.is_enabled("tax_graph", subject=user_id, cohorts={"ura_staff"})
```

Bucketing is a SHA-256 of `flag_name:subject` — stable across replicas (unlike
`hash()`, which is salted per interpreter) and uncorrelated between flags, so
the same users do not lead every experiment. Ramping needs no deploy:

```bash
FLAG_MODEL_TIERING_PERCENT=25
FLAG_TAX_GRAPH_COHORTS=ura_staff,internal
FLAG_TAX_GRAPH_ALLOWLIST=tin-1001,tin-1002
```

A subject the rollout does not target falls through to the flag's **default**,
not to off — otherwise adding a 5% rollout would silently disable the flag for
the other 95%. `variant_for()` labels each resolution for per-variant reporting.

## Retrieval serving path (updated 2026-09-29)

Shared entry: `HybridRetriever.search_planned()` — used by REST `generate()`,
SSE/stream, `search_ura_knowledge_base`, corrective RAG, LangGraph
`node_retrieve`, and voice speculative prefetch. Do not add another retrieve
helper. LangGraph now fuses the graph RRF leg when those flags are on,
applies the unbound-FAQ filter + exact-FAQ promote, and observes after
tools: one retrieve hop when tools produce no evidence, then one
reflect retry on low faithfulness or a reasoning miss. Soft “this fiscal
year” boost follows `current_fiscal_year()`.

The graph hit uses the same `rrf_fuse_ranked_lists()` combiner: fused RRF
score determines order, calibrated relevance breaks ties, and duplicate IDs
contribute at most once per retrieval leg. Its default fiscal year is resolved
at request time through `current_fiscal_year()`, so year rollover and an
explicit `CURRENT_FISCAL_YEAR` override are honored. Keep `tax_graph` and
`graph_fusion` off until the unseen-question gate is expanded and passes; the
authored/held-out shadow results are not sufficient production evidence.

The `langgraph` flag selects the small synchronous, request-scoped
LangGraph-shaped dispatcher only when `agentic_mode` is also enabled. It is not
the upstream LangGraph runtime. Tool dispatch additionally requires
`tool_use`; orchestration does not implicitly enable tools. The dispatcher
enforces a step cap and records per-node outcomes, but has no checkpointer,
durable resume, interrupts, or retry policy. It is appropriate only for a
single chat turn; do not use it for long-running workflows. If replaced with
durable LangGraph execution, tool side effects need idempotency before node
replay is enabled. See the official [Graph API guidance](https://docs.langchain.com/oss/python/langgraph/graph-api)
for bounded execution and replay semantics, and [Persistence guidance](https://docs.langchain.com/oss/python/langgraph/persistence)
for checkpoint-backed recovery and state retention.

The corpus is English. Non-English questions take a merged English
translation pass (`FLAG_TRANSLATE_RETRIEVE`, default on). Generation is
**English too** unless a locale LoRA adapter is loaded (`llm.can_generate_in_locale`),
and the reply is translated on the way out — see *Answer language* below.
Citations carry `url` / `effective_date` when the index stored them (crawl
chunks do).

| Control | Default | Effect |
|---------|---------|--------|
| `FLAG_QUERY_REWRITE` | on | Spelling / abbreviation / coreference |
| `FLAG_QUERY_DECOMPOSITION` | on | Parallel search on multi-intent questions |
| `FLAG_TRANSLATE_RETRIEVE` | on | Extra English hybrid pass for non-`en` locales |
| `FLAG_HYDE` | **off** | Dense-only hypothetical document (template; `HYDE_LLM=true` is vLLM-only). A/B-ready via `FLAG_HYDE_PERCENT` + `user_id`; leave `FLAG_HYDE` unset for a canary. Do not enable until measured on `rag_eval.jsonl`. |
| `FLAG_HYDE_PERCENT` | 0 | Stable user-id bucket. Ignored if `FLAG_HYDE` is set. |
| `FLAG_SEMANTIC_CACHE` | on | Exact + cosine cache |
| `FLAG_CORRECTIVE_RAG` | on | Re-retrieve on low calibrated relevance |
| `FLAG_GRAPH_FUSION` + `FLAG_TAX_GRAPH` | **off** | Statutory graph as a third RRF leg |
| `FLAG_TOOL_RAG` | **off** | Top-k tool schemas + rails. Dense embedder is injected from the retriever when loaded; a miss keeps rails only. A/B via `FLAG_TOOL_RAG_PERCENT`. |
| `FLAG_KNOWLEDGE_DISCREPANCY_REPORTING` | on | Auto-detect conversational factual disputes, log discrepancy reports, and apply active statutory precedences & tombstones. |
| `python -m app.freshness --check --write-status --notify` | — | Exit 1 on drift; writes status for `GET /v1/index/freshness`; Slack if `FRESHNESS_SLACK_WEBHOOK` is https |

### Answer language

The answer is produced in English and translated into the taxpayer's language
by `service.localize_reply`, in one place, because `_generate_en` has a dozen
or so exits (blocked, workflow, calculator, greeting, clarification,
deterministic, abstained, escalated, generated) and translating at each of them
is how one branch quietly answers a Luganda question in English.

`llm._build_messages` states the answer language explicitly on every request,
in an `## Answer language` section. The system prompt used to carry an
unconditional rule — "if the user writes in Luganda … respond in the same
language" — where nothing could see whether the deployment can do that. The CPU
deployments and the vLLM backend load no locale adapter, and the base model
asked for Luganda anyway returned a degenerate repetition loop rather than
sentences. Two instructions pointing opposite ways, with the language of the
question breaking the tie.

Five guards sit on the translated text, every one of which prefers the English
answer to a bad localized one. They exist because the answer was generated *and
claim-verified* in English: everything `claim_verifier` concluded describes the
draft, so each property it relied on has to be re-checked against the text
actually shipped.

* **Collapse** (`mt.length_plausible`) — a translation far shorter than its
  source is a degraded model response, not an answer. The floor is **measured**:
  across the 23,838 aligned human pairs in `Data/online_corpora/salt/`, one in a
  thousand falls below 0.449 (en→lg) and 0.434 (en→sw), and below 0.4 lies
  0.04%. `MT_MIN_LENGTH_RATIO` defaults to 0.35, under both. It was a tenth of
  the source, which caught a *collapsed* response and not a *truncated* one — and
  a truncated answer reads as complete while omitting the taxpayer's obligations.
* **Units** (`mt.units_survived`) — `protect_figures` masks digits and leaves the
  percent sign and currency code visible on purpose, so "18%" arriving as a bare
  "18" kept every digit and passed everything below. Fires only on *total* loss
  of a marker kind, because `figures()` pools categories precisely to tolerate
  one rate rendering as a word.
* **Citations** (`mt.citations_survived`) — `claim_verifier` keys off the `[n]`
  markers to decide whether a claim was supported at all. A translation that
  drops or renumbers one ships an answer whose provenance no longer matches the
  report that approved it. Set equality, not order: a marker that moved with its
  clause is still attached to the right claim.
* **Sentinel residue** — a leftover `#NMBR…#` fragment is visible garbage in a
  taxpayer's answer, so the round trip is spent whatever the figures say.
* **Figures** (`mt.figures_survived`) — machine translation paraphrases, and a
  paraphrased amount is a different amount. A reply that said "UGX 235,000" and
  comes back saying "UGX 253,000" is indistinguishable from the assistant
  inventing a figure. Money amounts and percentages are pooled into one set
  before comparison, because the *category* does not survive translation even
  when the number does — Luganda states a rate as "ebitundu 18 ku buli kikumi",
  with no percent sign.
  * **Spoken Percentages**: `entailment.py` supports both Swahili (`_SWAHILI_PCT_WORDS`, e.g. `asilimia kumi na nane` $\rightarrow$ 18) and Luganda (`_LUGANDA_PCT_WORDS`, e.g. `ebitundu kumi na munaana` $\rightarrow$ 18, `ebitundu mukaaga` $\rightarrow$ 6).
  * **Vernacular Multipliers**: Recognizes East African singular forms (`akakadde` 1M, `omutwalo` 10k, `olukumi` 1k, `akawumbi` 1B) alongside plurals (`obukadde`, `emitwalo`, `enkumi`, `milioni`, `laki`).
  * **Contact Line Exclusion**: Ugandan toll-free and mobile patterns (`0800 117 000`, `0772 140 000`) are stripped before currency extraction so helpdesk footers are not misread as hundred-million shilling tax figures.

Each failure has its own metric and log line —
`reply_localization_{figures_changed,units_dropped,citations_lost}_total` — rather
than sharing one: right digits with a lost unit is a different fault from a
paraphrased number and needs a different fix.

**Not covered.** Semantic drift inside the translated prose — a flipped
negation, a dropped condition — passes all five. Detecting it needs entailment
over the localized text and a per-locale golden set to gate it (G58).

* **Stream Transport Resilience**: `/v1/chat/stream` emits `Cache-Control: no-cache, no-transform` and `X-Accel-Buffering: no`, and Next.js standalone disables proxy compression (`compress: false`), preventing reverse proxies (ngrok, Cloudflare) from gzipping and buffering event streams.

* **Intelligent UI completion and actions (ADR-0018, 2026-10-30)**: REST and SSE
  use `lib/turnMeta.ts` for the same response metadata. The browser bounds SSE
  events and requires a delimited `done` frame; truncated replies retain an
  incomplete status and cannot offer workflow/portal actions or automatic
  narration. Writes are bound to the conversation and response ID. A failed
  stateful chat POST is not automatically repeated after an ambiguous network
  outcome; only explicit 404/405/401 responses permit the existing REST path.
  Historical/restored cards require refreshed guidance. Portal approvals require
  local digest calculation, but API-wide mandatory digest enforcement remains
  open. See the [audit and remaining release work](plans/intelligent-ui-integration-audit-2026-10.md)
  and [verification runbook](runbooks/intelligent-ui-integration.md).

| Setting | Default | Effect |
|---------|---------|--------|
| `RETRIEVAL_MT_BACKEND` | `local_first` | Question → English, for searching the corpus |
| `REPLY_MT_BACKEND` | `local_first` | Answer → the taxpayer's language |
| `MT_CACHE_SIZE` | 512 | Per-process translation memo (`app/mt.py`); 0 disables |
| `MT_CACHE_MAX_CHARS` | 4000 | Longer text is translated but not memoised |
| `MT_PROTECT_FIGURES` | `true` | Mask figures behind sentinels before translation and restore after (`app/mt.py`); kill switch only — a tier that cannot carry them is retried unprotected |
| `MT_MIN_LENGTH_RATIO` | 0.35 | Shortest a translation may be, as a fraction of the source (`app/mt.py`). Measured from the 23,838 aligned pairs in `Data/online_corpora/salt/`: the one-in-a-thousand ratio is 0.449 (en→lg) and 0.434 (en→sw). Replaces a floor at one tenth, which passed a translation that had dropped nine tenths of the answer |

The cache is why a non-English turn is no longer two to three times slower than
the same question in English. One turn translated the same question **twice** —
the deterministic routers translate it in `service.py` before retrieval runs,
and the hybrid retriever translates it again for the corpus — and the
deterministic replies (greetings, the TIN and return-filing templates, the
abstention line) are byte-identical every time. Entries hold a digest of the
text, never the text itself: taxpayer questions reach this cache and can carry
a TIN or a name.

### Withholding a contradicted answer

`entailment.numeric_contradiction` is a deliberately high-precision check: a
percentage the cited passage does not state, or — for rule-shaped sentences
only — an amount it does not state. Claim verification has always caught these
and the response judge has always escalated them. What none of that did was
stop showing the figure, and a taxpayer acts on the number rather than on the
amber banner above it.

`service.withhold_if_contradicted` replaces such an answer with
`CONTRADICTED_CLAIM_REPLY`, forces escalation, and drops the faithfulness score
(the score described text that is no longer being sent). It fires on
**contradicted** claims only, never on merely *unsupported* ones — an
unsupported claim is one the lexical verifier could not confirm, which happens
constantly and legitimately, and withholding those would silence most correct
answers.

| Setting | Default | Effect |
|---------|---------|--------|
| `WITHHOLD_CONTRADICTED_CLAIMS` | `true` | Off only to diagnose a suspected false positive |

### Retrieval answer integrity (ADR-0019, 2026-10-30)

Each rule below closes a path that served a confident, cited and wrong answer
while every existing gate passed (G277–G287).

* **Decisions read calibrated evidence.** `_rerank` stores the cross-encoder's
  own score as `score_calibrated`. Abstention, pruning, corrective RAG and the
  drift histogram read it through `retriever.gating_relevance`. Preference boosts
  (current FY +0.08, topic +0.05, sector guide −0.06) and the stale-edition
  demotion only reorder. Scores outside [0, 1] are treated as logits and
  squashed rather than clamped. The scale is decided once per loaded model,
  from its activation where it says and latched on the first out-of-range
  score, never per batch. A rerank failure serves unscored hits and is never
  charged to Qdrant's circuit breaker.
* **In-force editions.** `demote_stale_editions` takes 0.15 from editions
  `RETRIEVER_STALE_EDITION_YEARS` (4) or more fiscal years behind
  `current_fiscal_year()`, unless the question names that year. The edition is
  read from the `fiscal_year` label, then a fiscal-year label in the source name,
  then a text cue for unlabelled chunks. It is never read from a bare year: Acts,
  regulations and tariffs (EAC CET 2022) are named by the year they were made.
  Passage headers carry `Edition`/`Effective`. Prompt rule 8 uses the edition for
  the period asked: the most recent one, unless the question names an earlier
  year.
* **Withdrawals.** A tombstone names a document: the same path, or one ending
  with the other on a path boundary (`docs/x.pdf` names `/app/Data/docs/x.pdf`
  and its URL). When the store is unreadable, the last good read still applies,
  and the response cache stamps that same snapshot. Creating or deleting a
  tombstone through the admin API retires cached answers on that replica at once.
* **Query rewriting only fixes misspellings.** `query._fuzzy_correct_word` never
  changes a word the served corpus uses: the BM25 vocabulary the retriever
  registers, the FAQ rows the service registers, or the state file as fallback.
  It used to rewrite 23% of the FAQ questions (taxes→taxis, exporting→importing).
  Texting shorthand applies to lowercase letters only, and never after "Part",
  "Schedule" or "Item". Corrective RAG passes the question's locale and spells in
  English only for English.
* **Lexical floor.** The three-matched-words lift needs at least half of
  `RETRIEVER_LEXICAL_FLOOR` in IDF coverage, and the question-span score is
  IDF-weighted.
* **Multi-intent merge.** Each sub-query is pruned against its own best, and its
  best hit keeps a place in `top_k` if it clears the context floor. The native
  and translated legs of one question take a single cut instead (G128).
* **Index operations.** `build_index` ensures keyword payload indexes on
  `fiscal_year`, `doc_type`, `tax_type`, `tag`, `source`, `chunk_id` and
  `doc_family`. Backfill a live collection with
  `python -m app.indexer --ensure-payload-indexes`. `POST /v1/index` runs the
  staged lifecycle (`app.index_lifecycle`), one rebuild at a time (409
  otherwise), and re-binds the retriever even when bookkeeping after the alias
  swap fails.

| Setting | Default | Effect |
|---------|---------|--------|
| `RETRIEVER_STALE_EDITION_YEARS` | 4 | Fiscal years behind the one in force at which an edition is demoted. 4 reproduces the 2020–2022 set the rule hardcoded (from FY2026-27) and then moves with the year |

| Metric | Meaning |
|--------|---------|
| `ura_retrieval_top_relevance_score{mode}` | Best calibrated relevance per search — the drift signal |
| `ura_retrieval_unscored_searches_total{mode}` | Searches that served hits with no calibrated score |
| `ura_retrieval_abstention_decisions_total{signal,abstained}` | `rerank`, `lexical`, `no_hits` or `none` (answered with no relevance signal) |
| `ura_retrieval_tombstone_read_failures_total` | Tombstone reads that fell back to the last good read |
| `ura_retrieval_rerank_errors_total`, `ura_retrieval_rerank_logit_scores_total` | Rerank failures; logit-scale scores squashed |
| `ura_semantic_cache_lookups_total{backend,outcome}` | Cache outcomes, including `guard_rejected` |

### Sovereign Two-Tier Reasoning, Scenario Planner & Figure Lock (ADR-0007)

When `FLAG_MODEL_TIERING=true`, `providers/routing.py` (`select_tier`, `ENDPOINTS`) dispatches each turn across two local vLLM endpoints while `SOVEREIGN_MODE=true` (`production_readiness.sovereign_mode_errors`, `SovereignModeViolationError`) guarantees zero external AI API egress:

- **T1 (`Sunbird/Sunflower-Qwen3.5-9B`, `VLLM_BASE_URL`):** Fast single-clause RAG, greetings, local machine translation, and vernacular locales (`lg`, `sw`, `nyn`, `ach` unless `FLAG_REASONER_FOR_TRANSLATED_LOCALES=true`).
- **T2 / T3 (`Qwen/Qwen3.8-27B-FP8`, `REASONER_BASE_URL`, `enable_thinking=False`):** Multi-hop legal reasoning, multi-tool tax scenarios (`planner_tool_count >= 2`), statutory premise contradictions (`premise_contradiction=True`), and dispute/objection turns. Capped by a rolling 200-turn window (`REASONER_MAX_SHARE=0.25`) and per-turn timeout (`REASONER_DEADLINE_S=90`) with automatic local T1 fallback (`reasoner_fallback_total`).
- **Code-Owned Tax Scenario Planner (`case_facts.py`, `scenario_planner.py`, `premise_guard.py`):** Extracts conversation-scoped `CaseFacts`, executes deterministic calculators and rule tools (`determine_residency`, `value_benefit_in_kind`, `convert_currency`, `calculate_paye`, `vat_input_claim_eligibility`, `wht_applicability`, `objection_requirements`, `calculate_late_filing_penalty`), runs 8 statutory premise contradiction rules (`check_fact_contradictions`), and injects `## Verified Statutory Facts & Calculations` ahead of retrieved passages in both REST and SSE streaming paths.
- **Polarity-Aware Faithfulness & Figure Lock (`claim_polarity.py`, `agents/evaluator.py`):** `DIRECTION_LEXICON` (22 opposing tax-law pole pairs) penalizes directional inversions in `compute_faithfulness` and `verify_claims`. `verify_figure_lock` verifies that every money amount, percentage, and day count in the draft reply traces to the user query, `verified_facts`, or retrieved passages, triggering a bounded same-tier `_reflect_llm` revision or sentence strip (`strip_unverified_figure_sentences`).

Eval set: `Data/eval/rag_eval.jsonl` (30 English rows, including `reg-*` regression ids). Keyword self-retrieval gate: `App/backend/tests/test_retrieval_regression_gate.py`. Completeness: `test_eval_set_completeness.py`.

Traceability record: [App/docs/traceability/retrieval-agentic-upgrade-2026-08-17.md](../App/docs/traceability/retrieval-agentic-upgrade-2026-08-17.md).

## Configuration Reference

All settings are configurable via environment variables. See [API Reference → Environment Variables](API_REFERENCE.md#environment-variables) for the complete list, or [PROJECT_SETUP.md](PROJECT_SETUP.md#5-environment-configuration) for a quick-start `.env` template.

- `FLAG_ENTERPRISE_CONNECTORS` (default `false`): Allows explicitly selected, deployment-bound remote MCP connectors to expose eligible read-only operations in taxpayer chat after review. It never lists a tool that exists only inside the API process. A simulator bound as a remote MCP server (`App/docker-compose.connectors.yml`) is listed as demo data (`mode: simulation`) outside production and is refused in production, which still requires a connector an operator declared `MCP_SERVER_MODE_<NAMESPACE>=live` and its authorization controls.

These built-ins remain local simulators even when enabled; their status is not
evidence of a live URA, NIRA, URSB, bank, or payment connection. Connector
health is staff-only; raw connector records are no longer exposed. Connector writes require dispatch-time
authorization, an explicit confirmation proposal, and an idempotency key.
Browser-supplied remote connector registration is disabled until a reviewed
server configuration and standards-compliant MCP negotiation are available.

## Dependencies

```
# Core
fastapi>=0.115.0, uvicorn[standard]>=0.32.0, pydantic>=2.10.0

# LLM Generation
transformers>=4.46.0, torch>=2.4.0, accelerate>=1.2.0

# Retrieval
sentence-transformers>=3.4, qdrant-client>=1.13, numpy>=1.26

# Caching & Rate Limiting
redis>=5.0.0, slowapi>=0.1.9, limits[redis]>=3.13

# Streaming
sse-starlette>=2.0.0

# PDF
pymupdf4llm>=0.0.17, pymupdf>=1.25.3

# PostgreSQL (opt-in)
psycopg[binary]>=3.2.0, psycopg-pool>=3.2.0

# Security
cryptography>=44.0.0

# Observability (opt-in)
opentelemetry-api, opentelemetry-sdk, opentelemetry-exporter-otlp-proto-grpc
```

## Receptionist lifecycle assurance

Receptionist lifecycle assurance (2026-10-30, ADR-0020): call resource teardown
precedes best-effort reporting; registry and admission slots are finalized even
on reporting failure. Reconnect expiry revokes old media before requeueing and
ends the call if isolation cannot be established. Wrap-up ticket creation
preserves tenant/conversation/locale and honors `ticket_queue`; generated drafts
do not assert unverified outcomes. See [review and release gaps](plans/receptionist-assurance-2026-10-30.md).
The receptionist profile pins Pipecat 1.15.0, compatible with the backend's
Pillow 13 floor; install both dependency profiles together (G308).

## Turns answered by a chosen service

A turn that selected a connector and asked it for a record does not use retrieval as its
answer. The passages are not given to the agent (`passages=[]`), the reply has no
`sources` / `citations` / portal-form `resources`, and the abstention and escalation
paths do not apply to it (an unreachable service says so; it does not fall back to
passages, which is how a consignment status was once invented). See
`docs/SYSTEM_CONNECTORS_AND_PLUGINS.md`.

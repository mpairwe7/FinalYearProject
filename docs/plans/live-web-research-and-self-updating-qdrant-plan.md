# Plan: Sovereign Live Web Research, Global FX (`XE.com` / Google), Ephemeral PDF Sandbox & Self-Updating Qdrant Corpus

Status: **Ready for implementation.** Authored 2026-10-21 against `dev` @ `de3ed5ed72` (plus the local streaming-parity diff in `App/backend/app/service.py` and `App/backend/app/calculator_router.py`).  
Audience: a **new** coding session with no memory of the conversation that produced this plan. Read each referenced file before editing it.

> **Builds upon:** [`docs/plans/sovereign-opus-level-accuracy-plan.md`](sovereign-opus-level-accuracy-plan.md) and [`ADR-0007`](../adr/0007-sovereign-local-reasoning-tier-and-code-owned-tax-logic.md) (merged in PRs #594–#607). All local two-tier vLLM routing (`Sunbird/Sunflower-Qwen3.5-9B` T1 on GPU 4 + `Qwen/Qwen3.8-27B-FP8` T2/T3 on GPU 7), `CaseFacts`, `scenario_planner.py`, `premise_guard.py`, `claim_polarity.py`, and `verify_figure_lock` are already live on `dev`.

---

## Table of Contents

0. Ground Rules & Current State Check (Read First)
1. Why This Plan Exists — The 7 Ambiguities & Their Architectural Resolutions
2. Target Architecture Overview
3. Phase A — Streaming Guard Parity Commit, Feature Flags & `web_sync_ledger` Database Schema
4. Phase B — Live Global Exchange Rate Engine (`XE.com` / Google Currency) & `scenario_planner` Integration
5. Phase C — Frontier-Style Web Research Planner, 8-Trigger Router & Source-Authority Tiering
6. Phase D — Ephemeral RAM-Backed PDF/Document Sandbox (`/tmp/ura-web-sandbox/<turn_id>/`)
7. Phase E — Self-Updating Qdrant Comparator & Hot-Swap Engine (`corpus_sync.py`)
8. Phase F — End-to-End Orchestration (`service.py`, `verify_figure_lock`, `claim_verifier`, SSE Stream Events)
9. Phase G — Evaluation Suite, Live Tunnel Verification, ADR-0008, Runbook & Living Docs

---

## 0. Ground Rules & Current State Check

### 0.1 Repository Rules (from `AGENTS.md`, `App/backend/AGENTS.md`, `docs/AGENTS.md`)

- Runtime code lives in `App/backend/app` (imported as `app`). **Never move `App/backend` or `App/frontend`.**
- Every change lands via a feature branch → PR against `dev` → 100% green CI → resolve **all** CodeRabbit comments → squash-merge. Never push directly to `dev` or `main`.
- Always use the real GitHub CLI at `~/bin/gh` (never bare `gh`) and re-assert the `mpairwe7` account before every network/GitHub operation:
  ```bash
  ~/bin/gh auth switch --user mpairwe7
  ~/bin/gh api user --jq .login   # must output: mpairwe7
  ```
- Any new feature flag must land in **all three** files: `App/backend/app/flags.py`, `docs/RAG_ARCHITECTURE.md`, and `.env.example`.
- Any new FastAPI route must land in `tests/test_all_endpoints_e2e.py` (`EXPECTED_ENDPOINTS` + `COVERAGE`).
- Metrics go through `App/backend/app/analytics.py` (`metrics.inc(...)` / `metrics.observe(...)`) only, with bounded label values.

### 0.2 Data Sovereignty & Outbound Web Rules (`SOVEREIGN_MODE=true`)

- **Zero External AI API Egress:** No commercial or hosted AI API (`OpenAI`, `Anthropic`, `Gemini`, `Cloudflare Workers AI`, `Groq`, `Perplexity`, etc.) may ever receive prompts, documents, or taxpayer data. All LLM reasoning, query planning, and PDF comprehension run **locally** on GPUs 4 and 7 via vLLM (`HF_HUB_OFFLINE=1`).
- **PII-Scrubbed Read-Only Web Fetch:** Outbound HTTP `GET` requests for live exchange rates (`xe.com`, Google Currency) and official Ugandan tax publications (`ura.go.ug`, `ulii.org`, `parliament.go.ug`, `finance.go.ug`, `bou.or.ug`, `ursb.go.ug`, `unbs.go.ug`, `eac.int`) are permitted **only** when:
  1. The search query has been stripped of all PII, TINs, names, phone numbers, and user-specific narrative (`redact_pii_text` + keyword-only query synthesis).
  2. The target URL passes SSRF validation (HTTPS only, no private/loopback/link-local IPs in production) and matches the Source Authority Policy (§5.2).

### 0.3 GPU & Live Container Etiquette (8× NVIDIA RTX A6000)

- Check `nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv` and `docker ps` before starting any container.
- The live URA stack runs on:
  - **GPU 2:** `ura-app-api` (`0.0.0.0:8083->8000`)
  - **GPU 3:** `ura-app-orpheus-tts` (`0.0.0.0:18100->8100`)
  - **GPU 4:** `ura-app-vllm-sunflower` (`Sunbird/Sunflower-Qwen3.5-9B`, `0.0.0.0:8011->8001`)
  - **GPU 7:** `ura-app-vllm-reasoner` (`Qwen/Qwen3.8-27B-FP8`, `127.0.0.1:8012->8001`)
  - **CPU:** `ura-app-frontend` (`0.0.0.0:3032->3000`, served on `https://struttingly-nongeological-briella.ngrok-free.dev`), `ura-app-qdrant` (`127.0.0.1:6333->6333`), `ura-app-redis`, `ura-app-connectors`.
- Reusable Python 3.14 test libraries (`pytest`, `prometheus-client`) are pre-cached at `/mnt/nas1/tmp/opencode/pylibs_314`. Run unit tests inside `app-api:gpu`:
  ```bash
  docker run --rm -u "$(id -u):$(id -g)" --entrypoint bash \
    -v "$PWD:/repo" -v "/mnt/nas1/tmp/opencode/pylibs_314:/pylibs:ro" \
    -e APP_ENV=development -e ANALYTICS_DB_DIR=/tmp/data_store -e HOME=/tmp \
    app-api:gpu -lc 'cd /repo && PYTHONPATH=/repo/App/backend:/repo:/pylibs /opt/venv/bin/python -m pytest <test_path> -q -p no:cacheprovider'
  ```

---

## 1. Why This Plan Exists — The 7 Ambiguities & Their Resolutions

### Ambiguity 1: Exchange Rate Source (`XE.com` & Google Currency vs. Static Bank Snapshots)
- **Requirement:** Do **not** rely on static bank snapshots (`BOU_mid_rates_2026.json`) or slow central-bank CSVs for currency conversion. Fetch live, accurate global mid-market rates from **XE (`xe.com`)** and **Google Currency / Finance** (with automatic fallback across multiple live FX endpoints and a 30-minute Redis cache).
- **Resolution (§4):**
  - Implement `App/backend/app/web_fx.py` (`lookup_live_exchange_rate(currency, target="UGX")`) which fetches the real-time rate from **XE.com** (`https://www.xe.com/currencyconverter/convert/?Amount=1&From=<CUR>&To=UGX`) and **Google Currency/Finance** (`https://www.google.com/finance/quote/<CUR>-UGX`), cross-checks that both are within `2%` when both respond, caches the result in Redis for 30 minutes (`fx:live:<CUR>:UGX`), and falls back to `BOU_mid_rates_2026.json` only when offline.
  - Wire `lookup_live_exchange_rate` into `convert_currency` (`App/backend/app/tools/calculators.py`) and `scenario_planner.py` so that any foreign-currency question (e.g., Scenario 1's `$6,000/month` salary) **immediately computes the full UGX tax breakdown on Turn 1** and cites `[Live FX: 1 USD = UGX X,XXX.XX via XE.com / Google Currency, fetched YYYY-MM-DD HH:MM UTC]`.

### Ambiguity 2: How Frontier Models (Claude Opus, GPT Deep Research, Gemini 4 Argon) Achieve Reliable Online Research
- **Requirement:** Naive web search pulls in SEO content farms, outdated 2021–2023 blog posts, or Kenyan/Tanzanian tax brackets (`KRA` / `TRA`) that corrupt Ugandan answers.
- **Resolution (§5):** Adopt the 5-stage **Claude Opus Research / Gemini Deep Research** pipeline:
  1. **PII-Scrubbed Query Decomposition ("Broad-to-Narrow"):** Never send the user's raw conversational sentence to search. Synthesize 1–3 short, entity-free keyword queries (`4–7 words`).
  2. **Strict Source-Authority Tiering (Before Reading):**
     - **Tier 1 (Official Statutory & Market Primary Sources — Trust `1.0`):** `ura.go.ug`, `ulii.org` (Uganda Legal Information Institute — official Acts/Gazettes), `parliament.go.ug`, `finance.go.ug`, `bou.or.ug`, `ursb.go.ug`, `unbs.go.ug`, `eac.int`, `xe.com`, `google.com/finance`.
     - **Tier 2 (Verified Uganda Tax Advisory Sources — Trust `0.8`, commentary only):** `pwc.com`, `deloitte.com`, `ey.com`, `kpmg.com`, `bowmanslaw.com`, `grantthornton.co.ug` (only when page explicitly mentions Uganda / URA; never allowed to override Tier 1 or `FY2026-27.json`).
     - **Tier 3 (Blocked — Trust `0.0`):** Foreign revenue authorities (`kra.go.ke`, `tra.go.tz`, `rra.gov.rw`, `firs.gov.ng`, `sars.gov.za`, `irs.gov`, `gov.uk`) and unverified blogs/forums.
  3. **Subagent / Extractor Context Compression:** Condense fetched pages/PDFs into compact `< 500 token` structured evidence cards with verbatim quotes, numbers, effective fiscal year, and URL before passing to `Qwen/Qwen3.8-27B-FP8`.
  4. **Temporal Precedence Gate:** Extract the document's fiscal year / publication date; a web document older than `FY2026-27` can never override `FY2026-27.json`.
  5. **Figure-Lock Registration:** Register all verified web figures into `verified_facts_block` so `verify_figure_lock()` and `verify_claims()` approve them.

### Ambiguity 3: Navigating `ura.go.ug` & Downloadable PDFs — Ephemeral Sandbox vs. Model Navigation Training
- **Requirement:** Most authoritative data on `ura.go.ug` (Public Notices, Practice Notes, Tax Guides, Used Motor Vehicle Valuation lists, CET schedules) lives inside downloadable `.pdf`, `.xlsx`, or `.docx` files rather than HTML text.
- **Resolution (§6):** Do **not** train the model to click through `ura.go.ug` menus (which breaks whenever URA changes its HTML layout and takes 30–60s). Instead, build an **Ephemeral RAM-Backed Document Sandbox (`App/backend/app/ephemeral_pdf_sandbox.py`)**:
  - Discovers `.pdf` / `.xlsx` / `.docx` links on `ura.go.ug` (via search + HTML `<a href>` link extraction).
  - Downloads candidate files into an isolated, turn-scoped `tmpfs` directory (`/tmp/ura-web-sandbox/<turn_id>/`).
  - Validates PDFs with our existing `App/backend/app/pdf_guards.py` (`inspect_pdf_bytes`) and `App/backend/app/mcp/validation.py` (`screen_tool_result`).
  - Extracts text and tables page-by-page using `PyMuPDF` (`fitz`) / `openpyxl` (`App/backend/app/documents.py`) and ranks the pages against the user's question on **GPU 2** (`BAAI/bge-m3` + `mixedbread-ai/mxbai-rerank-base-v2`).
  - **Wipes the temporary directory in a `finally:` block** at the end of the turn, while caching the sanitized extracted text chunks in Redis (24h TTL keyed by `sha256`) for instant Turn 2+ follow-ups.

### Ambiguity 4: The 8 Triggers That Require Online Research (Not Just Currency!)
- **Requirement:** Online research must trigger whenever a question needs live or newly published confirmation, not only on foreign currency.
- **Resolution (§5.1):** `should_trigger_web_research(query, facts, local_hits, faithfulness)` triggers across **8 categories**:
  1. **Foreign Currency Conversion (`FX`):** Query or `CaseFacts` contains a non-UGX currency (`USD`, `$`, `EUR`, `GBP`, `KES`, `AED`, `CNY`, `ZAR`, `JPY`, etc.) without a user-supplied exchange rate.
  2. **Outdated or Missing Fiscal-Year Guide in Qdrant:** User asks about a sector or tax guide where the best Qdrant hit's `fiscal_year` is older than the current fiscal year (e.g., Qdrant only has `FY2023-24` or `FY2024-25`).
  3. **Used Motor Vehicle Benchmark CIF Valuation:** User names a specific vehicle make/model/year (e.g., *"2018 Toyota Harrier"*, *"2017 Hilux"*, *"Subaru Forester"*) for import duty calculation.
  4. **Active Tax Amnesty / Penalty Waiver / Filing Extension Notices:** Query mentions *"waiver window"*, *"amnesty"*, *"extension"*, *"public notice"*, *"practice note"*, *"circular"*, or *"portal down/maintenance"*.
  5. **EFRIS / Digital Tax Stamps (DTS / Kakasa) Gazetted Goods & Rates:** Query asks whether a specific product category is mandatory under DTS/EFRIS or asks for latest stamp rates.
  6. **Bill-to-Act Presidential Assent & Gazette Status:** Query asks whether a Finance/Tax Amendment Bill has been signed/assented/gazetted into an Act (`ulii.org` / `parliament.go.ug`).
  7. **EAC CET Country-Specific Duty Stays ("Stay of Application"):** Query asks about current import duty rates on goods subject to annual EAC Gazette Stay-of-Application modifications.
  8. **Macroeconomic / Unverified Rate Keys (`G222`):** Query triggers a tool whose rate key is in `"unverified"` (e.g., low-interest employee loan BIK needing the statutory interest rate) or local Qdrant retrieval has low relevance (`max_score < 0.45`).

### Ambiguity 5: Self-Updating Qdrant Corpus — Compare `ura.go.ug` PDF vs. Qdrant, Delete Outdated Doc, Replace With New Doc, and Remember the Update
- **Requirement:** When the AI finds a PDF on `https://ura.go.ug` (exclusively) and discovers that the corresponding document in Qdrant is outdated, it must **delete the old document from Qdrant**, **insert the new document into Qdrant**, and **remember that the database was updated** so future questions use Qdrant directly without searching the web again.
- **Resolution (§7 — `App/backend/app/corpus_sync.py`):**
  1. **Canonical Document Family & Fiscal-Year Comparator:** Maps every URA PDF to a `(doc_family, doc_kind, fiscal_year, sha256)` tuple (e.g., `doc_family="sector_guide_real_estate"`, `doc_kind="full_guide"`, `fiscal_year="FY2026-27"`).
  2. **Replace Mode (Newer Edition of an Existing Document):**
     - When `ura.go.ug` has a newer `fiscal_year` (or newer revision date with `page_count >= 0.5 * old_page_count`) for the same `doc_family`:
       - **Deletes the old document's points from Qdrant** (`client.delete(collection_name=..., points_selector=FilterSelector(Filter(must=[FieldCondition(key="source", match=MatchValue(value=old_source))])))`) **and** records a tombstone in `corpus_tombstones` (`db.add_corpus_tombstone(source_uri=old_source, superseded_by=new_source)`).
       - Chunks the new PDF, computes dense (`BAAI/bge-m3`) + sparse (`BM25SparseEncoder`) vectors, and upserts the new chunks into `ura_knowledge_base_jsonl_active`.
       - Writes the new `.jsonl` to `App/Data/pdf_jsonl/`, moves the old `.jsonl` to `App/Data/archive/`, and updates `index_freshness.json` so `python -m app.freshness --check` stays green.
  3. **Add Mode (Brand-New Document or Short Public Notice):**
     - When the `ura.go.ug` PDF has no prior version in Qdrant (or is a 1–4 page Public Notice), upserts its chunks into Qdrant without deleting existing handbooks.
  4. **Persistent Memory of Updates (`web_sync_ledger` table):**
     - Records `(topic_key, doc_family, source_uri, ura_url, pdf_sha256, fiscal_year, status, synced_at, expires_at)` in SQLite/Postgres (`database.py` + `postgres.py`).
     - Before launching any web search on `ura.go.ug`, the router checks `web_sync_ledger`: if `doc_family` / `topic_key` was already verified or updated before `expires_at` (default 7 days), **it skips the web search completely** and serves from the updated Qdrant collection in `< 250 ms`!

### Ambiguity 6 & 7: Streaming Guard Parity (`/v1/chat/stream`) & Live UI Progress Events
- **Requirement:** The Chrome browser uses `POST /api/v1/chat/stream` (`run_chat_turn` → `_apply_output_guards`). Any figure from live FX or a newly synced `ura.go.ug` PDF must pass `verify_figure_lock()` and `verify_claims()` without triggering `withhold_if_contradicted()`, and the UI must receive SSE progress events while web/PDF lookup runs.

---

## 2. Target Architecture Overview

```text
Incoming Chat Turn (REST `/v1/chat` or SSE `/v1/chat/stream`)
   │
   ├──► 1. Extract `CaseFacts` (`case_facts.py`) & run `HybridRetriever.search_planned()` (Qdrant)
   │
   ├──► 2. Check 8 Web-Research Triggers (`web_research.py` — `FLAG_LIVE_WEB_RESEARCH`):
   │       │
   │       ├─► Trigger 1 (Foreign Currency):
   │       │     `web_fx.lookup_live_exchange_rate(currency)`
   │       │     ──► Redis cache (`30m`) ──► XE.com + Google Currency ──► Fallback `BOU_mid_rates_2026.json`
   │       │     ──► Feeds live rate into `convert_currency` & `scenario_planner.build_verified_facts()`
   │       │
   │       └─► Triggers 2–8 (Outdated Qdrant FY, Vehicle CIF, Waiver/Notice, DTS/EFRIS, Bill Assent, CET, BoU CBR):
   │             │
   │             ├─► Check `db.get_web_sync_entry(topic_key / doc_family)`:
   │             │     If `expires_at > now()`: SKIP web search (Qdrant is already updated!)
   │             │
   │             └─► Otherwise:
   │                   a) PII-scrubbed query planner searches Tier-1/Tier-2 domains (`ura.go.ug`, `ulii.org`, etc.)
   │                   b) If `.pdf`/`.xlsx` found on `ura.go.ug`:
   │                        `ephemeral_pdf_sandbox.fetch_and_extract()` in `/tmp/ura-web-sandbox/<turn_id>/`
   │                        ──► `pdf_guards.inspect_pdf_bytes()` ──► `PyMuPDF` page extract ──► GPU 2 rerank
   │                        ──► `finally:` delete raw temp files
   │                   c) If PDF is from `https://ura.go.ug`:
   │                        `corpus_sync.compare_and_sync_qdrant()`
   │                        ──► If newer than Qdrant's doc for same `doc_family`:
   │                              • Delete old doc points from Qdrant + `db.add_corpus_tombstone(old_source)`
   │                              • Embed & upsert new PDF chunks into Qdrant + `App/Data/pdf_jsonl/`
   │                        ──► Record in `web_sync_ledger` (`expires_at = now + 7d`)
   │
   ├──► 3. Merge live FX & web-verified passages into `hits`, `citations`, and `verified_facts_block`
   │
   └──► 4. Synthesize answer (`Qwen/Qwen3.8-27B-FP8` T3 or `Sunflower-9B` T1) + `verify_figure_lock()` + `verify_claims()`
```

---

## 3. Phase A — Streaming Guard Parity, Feature Flags & `web_sync_ledger` Schema

### A.1 Commit the Existing Streaming Guard Parity Fix
The working tree currently has uncommitted fixes in `App/backend/app/service.py` and `App/backend/app/calculator_router.py` that wire `verified_facts_block` and `_case_facts` into the SSE streaming path (`_apply_output_guards`, `generate_retrieval_only`, `_run_chat_turn_events`) and add `"compared to"` / `"versus"` to `_SCENARIO_PLANNER_BYPASS_RE`. Keep those changes and include a regression test in `tests/agents/test_streaming_verified_facts_parity.py`.

### A.2 Feature Flags (`App/backend/app/flags.py`, `docs/RAG_ARCHITECTURE.md`, `.env.example`)
Add two flags to `flags.py` (and document in `docs/RAG_ARCHITECTURE.md` and `.env.example`):
- `live_web_research` (`FLAG_LIVE_WEB_RESEARCH`, default `False` in unit-test registry so offline CI tests stay 100% deterministic; enabled via `FLAG_LIVE_WEB_RESEARCH=true` in `App/docker-compose.gpu-salt.yml` and `configs/prototype.env`): enables live FX lookup (`xe.com` / Google Currency) and tiered official web/PDF research.
- `auto_qdrant_web_sync` (`FLAG_AUTO_QDRANT_WEB_SYNC`, default `False` in unit-test registry; enabled via `FLAG_AUTO_QDRANT_WEB_SYNC=true` on the live stack): enables automatic comparison of `ura.go.ug` PDFs against Qdrant, deletion/tombstoning of outdated Qdrant documents, upsert of newer `ura.go.ug` PDFs, and `web_sync_ledger` tracking.

### A.3 `web_sync_ledger` Table (`App/backend/app/database.py` & `App/backend/app/postgres.py`)
Read `App/backend/app/database.py` lines `735–765` and `3010–3085` (where `corpus_tombstones` is defined). Add:
```sql
CREATE TABLE IF NOT EXISTS web_sync_ledger (
    id                TEXT PRIMARY KEY,
    topic_key         TEXT NOT NULL,
    doc_family        TEXT NOT NULL,
    source_uri        TEXT NOT NULL,
    ura_url           TEXT NOT NULL,
    pdf_sha256        TEXT NOT NULL,
    fiscal_year       TEXT NOT NULL DEFAULT '',
    action_taken      TEXT NOT NULL CHECK(action_taken IN ('replaced_outdated', 'added_new', 'verified_current', 'skipped_older')),
    superseded_source TEXT NOT NULL DEFAULT '',
    synced_at         TEXT NOT NULL,
    expires_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_web_sync_family ON web_sync_ledger(doc_family);
CREATE INDEX IF NOT EXISTS idx_web_sync_topic ON web_sync_ledger(topic_key);
```
Add helper functions in both `database.py` and `postgres.py`:
- `upsert_web_sync_entry(...) -> dict[str, Any]`
- `get_active_web_sync_entry(*, doc_family: str = "", topic_key: str = "") -> dict[str, Any] | None` (returns the entry if `expires_at > now_iso()`, else `None`)
- `list_web_sync_entries(limit: int = 100) -> list[dict[str, Any]]`

**Unit tests:** `tests/agents/test_web_sync_ledger.py` (verifies insert, TTL expiry, lookup by `doc_family` and `topic_key`, and streaming `verified_facts` parity).  
**Deliverable / PR #1:** `feat(db): streaming verified-facts guard parity, web research flags, and web_sync_ledger schema`.

---

## 4. Phase B — Live Global Exchange Rate Engine (`XE.com` & Google Currency)

### B.1 Create `App/backend/app/web_fx.py`
Implement a resilient, multi-source live currency converter for Ugandan Shillings (`UGX`):

1. **Supported Currencies:** `USD`, `EUR`, `GBP`, `KES`, `TZS`, `RWF`, `AED`, `CNY`, `ZAR`, `JPY`, `INR`, `CAD`, `CHF`, `AUD`.
2. **Primary Live Sources (fetched concurrently or in priority order with a 4.0s timeout per source):**
   - **Source 1 — XE.com Converter:** `https://www.xe.com/currencyconverter/convert/?Amount=1&From={CUR}&To=UGX` (parse the mid-market rate from the page's JSON state / rate element, e.g. `1 USD = 3,688.42 UGX`).
   - **Source 2 — Google Finance Quote:** `https://www.google.com/finance/quote/{CUR}-UGX` (parse the `data-last-price` attribute on the quote container).
   - **Source 3 — Open Exchange Rate JSON Mirror (`open.er-api.com/v6/latest/{CUR}`):** Free, unauthenticated JSON fallback that aggregates global mid-market rates if XE/Google rate-limit or change HTML structure.
3. **Sanity & Cross-Source Bounds Check (`_is_plausible_ugx_rate`):**
   - Guard against HTML parsing glitches by checking plausible bounds relative to the baseline table in `App/backend/app/tax/data/fx/BOU_mid_rates_2026.json` (e.g. a live rate must be within `±35%` of the 2026 reference band; a parsed value of `0.0` or `1.0` or `999,999,999` is rejected).
   - When both XE.com and Google Finance return a valid rate, prefer XE.com (or their mean if within `2%`) and record `source="XE.com / Google Currency"`.
4. **Two-Tier Cache:**
   - L1 in-memory TTL dict + L2 Redis key `ura:fx:live:{CUR}:UGX` with `FX_CACHE_TTL_SECONDS = 1800` (30 minutes).
5. **Return Contract (`LiveFXQuote` dataclass):**
   ```python
   @dataclass(frozen=True)
   class LiveFXQuote:
       currency: str
       rate_ugx: float
       source: str          # e.g. "XE.com (https://www.xe.com/currencyconverter/convert/?Amount=1&From=USD&To=UGX)"
       fetched_at_utc: str  # ISO-8601 UTC timestamp
       is_live: bool        # True when fetched from XE/Google/live mirror; False when offline snapshot fallback
   ```

### B.2 Wire `web_fx.py` into `convert_currency` (`App/backend/app/tools/calculators.py`) & `scenario_planner.py`
Read `App/backend/app/tools/calculators.py` lines `1855–1940` (`ConvertCurrencyTool.compute`) and `App/backend/app/scenario_planner.py` lines `149–162` and `862–935`:
- In `ConvertCurrencyTool.compute`:
  - Priority 1: Explicit `exchange_rate` passed by the user.
  - Priority 2: When `flags.is_enabled("live_web_research")` (or `use_live_fx=True`), call `web_fx.lookup_live_exchange_rate(curr, date=date)`. When a live rate is returned, compute `ugx_amount = amount * rate_ugx` and return:
    ```python
    {
        "ok": True,
        "amount": float(amt),
        "currency": curr,
        "ugx_amount": float(ugx_val),
        "exchange_rate": float(quote.rate_ugx),
        "rate_source": quote.source,
        "fetched_at_utc": quote.fetched_at_utc,
        "is_live_rate": quote.is_live,
        "needs_exchange_rate": False,
        "explanation": (
            f"Converted {curr} {amt:,.2f} to UGX {ugx_val:,.2f} using live exchange rate "
            f"1 {curr} = UGX {quote.rate_ugx:,.2f} (Source: {quote.source}, fetched {quote.fetched_at_utc[:16]} UTC). "
            f"Note: for official URA return filing, apply the prevailing rate on the payment date."
        ),
    }
    ```
  - Priority 3: Offline fallback to `BOU_mid_rates_2026.json` if `date` is provided, else `needs_exchange_rate=True`.
- In `scenario_planner.py` (`_rule_paye`, line 150 and `build_verified_facts`, line 871):
  - When `sal.currency != "UGX"` and `flags.is_enabled("live_web_research")`, do **not** mark `exchange_rate` as a blocking missing input on `ToolCallSpec("convert_currency")` or `ToolCallSpec("calculate_paye")`! Instead, let `convert_currency` resolve `fx_out["ugx_amount"]` via `web_fx` and chain `paye_params["monthly_gross"] = fx_out["ugx_amount"]` into `calculate_paye` automatically!
  - That way, on **Scenario 1 Turn 1** (*"$6,000/month salary"*), `build_verified_facts` outputs **both** the live USD→UGX conversion and the complete band-by-band Resident vs. Non-Resident PAYE calculation in UGX!

**Unit tests:** `tests/agents/test_web_fx.py` (mocked XE.com HTML, Google Finance HTML, fallback JSON mirror, implausible-rate rejection, Redis caching, and `scenario_planner` chaining `$6,000` → live UGX → `calculate_paye`).  
**Deliverable / PR #2:** `feat(fx): live XE.com and Google Currency exchange-rate engine wired into convert_currency and scenario_planner`.

---

## 5. Phase C — Frontier-Style Web Research Planner, 8-Trigger Router & Source-Authority Tiering

### C.1 Create `App/backend/app/web_research.py`
Implement the **Claude Opus / Gemini Deep Research** style search planner and source-authority filter:

1. **The 8-Trigger Detector (`detect_research_triggers(query, facts, hits, faithfulness) -> list[ResearchTrigger]`):**
   - `fx_rate`: Foreign currency symbol/code present without explicit UGX rate.
   - `stale_corpus_edition`: Top Qdrant hit's `fiscal_year` (e.g., `FY2023-24`, `FY2024-25`) is older than `current_fiscal_year()` (`FY2026-27`), or user explicitly asks for the latest/2026–2027 guide.
   - `vehicle_valuation_cif`: Query mentions importing a specific motor vehicle make/model/year (e.g., `Toyota`, `Harrier`, `Hilux`, `Forester`, `Noah`, `Alphard`, `Prado`, `Premio`, `Benz`, `BMW`, `Isuzu`, `Fuso`) without giving a CIF value, or asks for URA's CMV/benchmark value.
   - `public_notice_or_waiver`: Query asks about *"public notice"*, *"practice note"*, *"circular"*, *"waiver"*, *"amnesty"*, *"deadline extension"*, or *"portal maintenance"*.
   - `efris_dts_gazette`: Query asks which goods/sectors are gazetted under Digital Tax Stamps (`DTS` / `Kakasa`) or EFRIS, or asks for updated stamp rates.
   - `bill_assent_gazette`: Query asks whether a Bill (`Amendment Bill`, `Finance Bill`) has been assented to, signed by the President, or gazetted into an Act.
   - `eac_cet_stay`: Query asks about EAC Common External Tariff (`CET`) stays of application, Uganda duty stays, or specific HS code duty modifications.
   - `unverified_rate_or_low_confidence`: A planned tool uses a rate key listed in `table.unverified` (`statutory_interest_rate_bou`, `bik_housing_employment_income_rate`) or Qdrant retrieval returned low confidence (`faithfulness < 0.45`).

2. **PII-Scrubbed "Broad-to-Narrow" Query Synthesizer (`build_search_queries(query, triggers) -> list[SearchSpec]`):**
   - Strips all TINs, NINs, phone numbers, emails, person/company names, and raw conversational filler via `redact_pii_text`.
   - Builds 1–2 targeted search queries per trigger:
     - For `ura.go.ug` triggers (`stale_corpus_edition`, `vehicle_valuation_cif`, `public_notice_or_waiver`, `efris_dts_gazette`):
       `site:ura.go.ug <4-6 core tax keywords> 2025 OR 2026 filetype:pdf` and `site:ura.go.ug <4-6 core tax keywords>`.
     - For `bill_assent_gazette`:
       `site:ulii.org OR site:parliament.go.ug <Act name> 2026`.
     - For `unverified_rate_or_low_confidence` (BoU CBR / discount rate):
       `site:bou.or.ug "Central Bank Rate" OR "discount rate" July 2026`.

3. **Source Authority Tiering (`classify_source_authority(url: str) -> tuple[int, float]`):**
   - **Tier 1 (`tier=1, trust=1.0`):** `ura.go.ug`, `www.ura.go.ug`, `ulii.org`, `parliament.go.ug`, `bills.parliament.ug`, `finance.go.ug`, `bou.or.ug`, `ursb.go.ug`, `unbs.go.ug`, `eac.int`, `xe.com`, `www.google.com`.
   - **Tier 2 (`tier=2, trust=0.8`):** `pwc.com`, `deloitte.com`, `ey.com`, `kpmg.com`, `bowmanslaw.com`, `grantthornton.co.ug` (accepted only when the snippet/title explicitly contains `"Uganda"` or `"URA"`).
   - **Tier 3 (`tier=3, trust=0.0` — REJECTED):** Foreign tax authorities (`kra.go.ke`, `tra.go.tz`, `rra.gov.rw`, `firs.gov.ng`, `sars.gov.za`, `irs.gov`, `hmrc.gov.uk`) and all unverified blogs/forums.
   - **Hard Rule for Qdrant Self-Updating (§7):** Only **Tier 1 `ura.go.ug`** (and `ulii.org` for enacted Ugandan Acts) may ever write to or replace documents in Qdrant! Tier 2 sources may only be cited as supplementary turn context, never persisted into Qdrant.

4. **Search Transport (`execute_web_search`):**
   - Searches via direct `ura.go.ug` publication/search endpoints + DuckDuckGo HTML/Lite (`https://html.duckduckgo.com/html/?q=...`) + Google programmable/HTML fallback with SSRF validation (reusing `App/backend/app/mcp/transport.py`'s IP/scheme checks), a 6-second timeout, and a 6-hour Redis query cache.

**Unit tests:** `tests/agents/test_web_research_router.py` (tests all 8 triggers, PII scrubbing on search queries, Tier 1/2/3 URL classification, foreign-tax-authority blocking, and temporal precedence).  
**Deliverable / PR #3:** `feat(research): 8-trigger web research planner and Opus-style source authority tiering`.

---

## 6. Phase D — Ephemeral RAM-Backed PDF/Document Sandbox (`ephemeral_pdf_sandbox.py`)

### D.1 Create `App/backend/app/ephemeral_pdf_sandbox.py`
Read `App/backend/app/pdf_guards.py` and `App/backend/app/documents.py` first. Build a turn-scoped ephemeral sandbox for downloading and searching `.pdf`, `.xlsx`, and `.docx` files discovered on `ura.go.ug` / `ulii.org`:

1. **Isolated `tmpfs` Workspace (`/tmp/ura-web-sandbox/<turn_id>/`):**
   - Use a context manager `@contextlib.contextmanager def ephemeral_sandbox(turn_id: str) -> Iterator[Path]:` that creates `/tmp/ura-web-sandbox/<turn_id>/` (`0o700` permissions) and **unconditionally deletes the entire directory (`shutil.rmtree(sandbox_dir, ignore_errors=True)`) in the `finally:` block**.
2. **Safe Streaming Download (`download_to_sandbox`):**
   - Enforces HTTPS, Tier-1 hostname allowlist, `MAX_SANDBOX_PDF_BYTES = 25 * 1024 * 1024` (25 MB streamed byte counter that aborts mid-stream if exceeded), and `timeout=12.0`.
   - Computes `sha256` on the downloaded bytes.
   - Before parsing any PDF, checks Redis cache `ura:pdf_chunks:{sha256}` (24h TTL). If already parsed in an earlier turn, returns the cached chunks in `< 10 ms`!
3. **Security Inspection (`pdf_guards` + `scan_retrieved_text`):**
   - Calls `inspect_pdf_bytes(raw_bytes, limits=QUERY_PDF_LIMITS)` from `App/backend/app/pdf_guards.py` (verifies `%PDF-` magic header, rejects encrypted PDFs, rejects `/JavaScript`, `/JS`, `/Launch`, `/EmbeddedFiles`, and enforces `max_xrefs`).
   - Runs `scan_retrieved_text` (`App/backend/app/guardrails.py`) and `screen_tool_result` (`App/backend/app/mcp/validation.py`) on every extracted page to strip any indirect prompt injection or Unicode bidi smuggling.
4. **Page-Level Extraction, Metadata Parsing & GPU 2 Micro-Reranking:**
   - Extracts text and tables per page via `fitz.open(stream=raw_bytes, filetype="pdf")` (or `openpyxl` for `.xlsx` valuation schedules).
   - Parses document metadata from the title, filename, and first 2 pages:
     - `fiscal_year`: regex matching `FY\s*20\d{2}[-/]\d{2,4}` or `20\d{2}\s*/\s*20\d{2}` (normalized to `FY2026-27` format).
     - `doc_family`: canonical slug (e.g., `taxation_handbook`, `sector_guide_agriculture`, `sector_guide_real_estate`, `sector_guide_hotel`, `withholding_tax_guide`, `efris_handbook`, `used_mv_valuation`, `public_notice`).
     - `doc_kind`: `"full_guide"` (when `page_count >= 6` or filename/title says `Handbook` / `Guide` / `Act`) vs. `"public_notice"` (when `page_count <= 5` or title says `Public Notice` / `Circular` / `Practice Note`).
   - Ranks the extracted chunks against the user's query using the already-loaded `HybridRetriever` dense embedder (`BAAI/bge-m3`) and cross-encoder (`mixedbread-ai/mxbai-rerank-base-v2`) on GPU 2, returning the **top 4 chunks** for immediate turn grounding plus the full chunk list for Phase E's Qdrant comparator!

**Unit tests:** `tests/agents/test_ephemeral_pdf_sandbox.py` (tests valid PDF extraction, malicious `/JavaScript` PDF rejection via `PdfRejected`, 25 MB size cap abort, `finally:` directory wipe verification even on exception, and Redis `sha256` chunk caching).  
**Deliverable / PR #4:** `feat(sandbox): ephemeral tmpfs PDF/XLSX sandbox with pdf_guards inspection and GPU micro-reranking`.

---

## 7. Phase E — Self-Updating Qdrant Comparator & Hot-Swap Engine (`corpus_sync.py`)

### E.1 Create `App/backend/app/corpus_sync.py`
Read `App/backend/app/retriever.py` (lines `242–280`, `1150–1250`), `App/backend/app/indexer.py`, and `App/backend/app/freshness.py` first.

Implement `compare_and_sync_ura_document(sandbox_doc: SandboxDocument, retriever: HybridRetriever) -> SyncOutcome`:

1. **Strict Origin Invariant:**
   - Refuse to run unless `urlparse(sandbox_doc.url).scheme == "https"` and `urlparse(sandbox_doc.url).hostname in {"ura.go.ug", "www.ura.go.ug", "ulii.org"}`.
2. **Discover Existing Qdrant Counterpart (`find_existing_qdrant_counterpart`):**
   - Query Qdrant (`ura_knowledge_base_jsonl_active`) and `App/Data/pdf_jsonl/` for existing documents in the same `doc_family` (e.g., `TAXATION-OF-THE-REAL-ESTATE-SECTOR-FY-2024-25.pdf` matches `doc_family="sector_guide_real_estate"`).
3. **Compare `(sha256, fiscal_year, doc_kind, page_count)`:**
   - **Outcome 1 — `verified_current` (`sandbox_doc.sha256 == existing.sha256`):**
     - Document in Qdrant is byte-identical. Record `action_taken="verified_current"` in `web_sync_ledger` (`expires_at = now + 7 days`) and return.
   - **Outcome 2 — `skipped_older` (`sandbox_doc.fiscal_year < existing.fiscal_year`):**
     - The PDF found on `ura.go.ug` is an older archive than what Qdrant already has. Record `action_taken="skipped_older"` in `web_sync_ledger` and do **not** touch Qdrant.
   - **Outcome 3 — `replaced_outdated` (`sandbox_doc.fiscal_year > existing.fiscal_year` and `sandbox_doc.doc_kind == "full_guide"` and `sandbox_doc.page_count >= 0.5 * existing.page_count`):**
     - **Step 1 (Delete & Tombstone Old Document in Qdrant):**
       - Call `db.add_corpus_tombstone(source_uri=existing.source, reason=f"Auto-superseded by {sandbox_doc.filename} ({sandbox_doc.fiscal_year}) from {sandbox_doc.url}", superseded_by=sandbox_doc.filename, created_by="corpus_sync")` (so `_filter_tombstoned_candidates` immediately drops any residual hits).
       - Delete all points with `source == existing.source` from Qdrant (`retriever._client.delete(collection_name=retriever.collection_name, points_selector=models.FilterSelector(filter=models.Filter(must=[models.FieldCondition(key="source", match=models.MatchValue(value=existing.source))])))`).
     - **Step 2 (Embed & Upsert New Document Chunks into Qdrant):**
       - Encode `sandbox_doc.chunks` using `retriever._dense_model` (`BAAI/bge-m3`) and `retriever._bm25` (`BM25SparseEncoder`).
       - Upsert deterministic point IDs (`deterministic_point_id`) into `retriever.collection_name` with payload `{"text", "source": sandbox_doc.filename, "source_url": sandbox_doc.url, "fiscal_year": sandbox_doc.fiscal_year, "doc_type": "pdf", "section", "page", "ingested_via": "ura_web_auto_sync"}`.
     - **Step 3 (Persist JSONL & Update Freshness Snapshot):**
       - Write `App/Data/pdf_jsonl/{sandbox_doc.stem}.jsonl`, move the superseded `App/Data/pdf_jsonl/{existing.stem}.jsonl` to `App/Data/archive/{existing.stem}.jsonl`, and refresh `INDEX_FRESHNESS_PATH` (`freshness.write_snapshot`) so `python -m app.freshness --check` stays 100% valid!
     - **Step 4 (Stamp `web_sync_ledger`):**
       - Call `db.upsert_web_sync_entry(topic_key=..., doc_family=sandbox_doc.doc_family, source_uri=sandbox_doc.filename, ura_url=sandbox_doc.url, pdf_sha256=sandbox_doc.sha256, fiscal_year=sandbox_doc.fiscal_year, action_taken="replaced_outdated", superseded_source=existing.source, expires_at=now + 7 days)`.
   - **Outcome 4 — `added_new` (No existing counterpart in Qdrant, or `doc_kind == "public_notice"`):**
     - Embed and upsert the new chunks into Qdrant (without deleting existing handbooks), write `App/Data/pdf_jsonl/{sandbox_doc.stem}.jsonl`, refresh `index_freshness.json`, and record `action_taken="added_new"` in `web_sync_ledger`.

4. **"Never Re-Search" Fast Path (`should_skip_web_search_for_family`):**
   - Before `web_research.py` runs an external search on `ura.go.ug` for a `doc_family` or `topic_key`, it calls `db.get_active_web_sync_entry(doc_family=..., topic_key=...)`.
   - When an active entry exists (`expires_at > now`), `web_research.py` **skips the external HTTP search completely** and logs `web_sync_cache_hit_total`, because the updated document is already inside Qdrant!

**Unit tests:** `tests/agents/test_corpus_sync.py` (tests all 4 outcomes: `verified_current`, `skipped_older`, `replaced_outdated` deleting old points + tombstoning + upserting new points + archiving JSONL, `added_new` for public notices, non-`ura.go.ug` origin rejection, and second-turn `web_sync_ledger` skip).  
**Deliverable / PR #5:** `feat(qdrant): self-updating URA portal PDF comparator, old-doc replacement, and web_sync_ledger memory`.

---

## 8. Phase F — End-to-End Orchestration (`service.py`, `verify_figure_lock`, `claim_verifier`, SSE Stream)

Read `App/backend/app/service.py` (lines `1830–1940`, `2200–2360`, `11000–11800`, `12700–13115`), `App/backend/app/agents/evaluator.py` (lines `335–425`), and `App/backend/app/claim_verifier.py` (lines `230–315`):

1. **Hook Live Web Research into `_generate_en` (REST) and `generate_retrieval_only` (SSE Streaming):**
   - Right after Step 3b (`corrective_rag`), when `flags.is_enabled("live_web_research")`:
     - Run `detect_research_triggers(retrieval_query, active_case_facts, hits, faithfulness)`.
     - If triggers fire (and `web_sync_ledger` does not already mark the family current):
       - Emit SSE phase events on the streaming path (`web_research.started`, `pdf_sandbox.extracting`, `qdrant_sync.updated`).
       - Execute `run_web_research(...)` (which runs `ephemeral_pdf_sandbox` + `corpus_sync` when `FLAG_AUTO_QDRANT_WEB_SYNC=true`).
       - Prepend the top web/PDF chunks to `hits` (with `source_url` and `effective_date` preserved in `HybridRetriever.build_citations(hits)`).
       - Append a `## Live Web & Official Portal Verification` summary bullet (with exact figures, URLs, and fetch timestamps) to `verified_facts_block`.
2. **Ensure `verify_figure_lock()` and `verify_claims()` Honor Web-Verified Figures:**
   - Because web-verified figures and live FX conversions (`web_fx.py`) are added to `verified_facts_block` and `hits`, both `verify_figure_lock()` (`agents/evaluator.py`) and `verify_claims()` (`claim_verifier.py`) automatically include their numbers in `allowed_money`, `allowed_pcts`, and `verified_numbers` on **both** REST (`/v1/chat`) and Chrome SSE streaming (`/v1/chat/stream`).
3. **Admin Visibility Endpoint (Optional / Existing Admin Console):**
   - Expose `web_sync_ledger` entries alongside `corpus_tombstones` in the existing admin discrepancy/tombstone status payload so staff can see every document auto-updated from `ura.go.ug`.

**Unit tests:** `tests/agents/test_live_web_orchestration_e2e.py` (end-to-end test covering Scenario 1 `$6,000` live FX PAYE calculation on both `/v1/chat` and `/v1/chat/stream`, plus a simulated outdated `FY2024-25` Qdrant guide auto-replaced by a `FY2026-27` `ura.go.ug` PDF on Turn 1 and served directly from Qdrant without web search on Turn 2).  
**Deliverable / PR #6:** `feat(chat): wire live FX, ephemeral PDF sandbox, and self-updating Qdrant sync across REST and streaming`.

---

## 9. Phase G — Evaluation, Live Tunnel Verification, ADR-0008, Runbook & Living Docs

### G.1 Evaluation & Live Tunnel Verification
1. Author `evals/web_research_scenarios/scenarios.jsonl` (15 scenarios covering live USD/EUR/GBP/KES FX conversion, outdated guide replacement, used vehicle CIF lookup, public notices, Bill-to-Act verification, and Turn 2 `web_sync_ledger` zero-re-search verification).
2. Run the full test suite and verify on the live stack (`https://struttingly-nongeological-briella.ngrok-free.dev`):
   - **Scenario 1 Turn 1 (`$6,000/month` expat salary):** Confirm that in Chrome (`/api/v1/chat/stream`), the AI fetches the live USD→UGX rate from XE.com / Google Currency, computes the exact monthly Resident and Non-Resident PAYE in UGX on Turn 1, cites the live FX source + timestamp, and never triggers false contradiction withholding.
   - **Self-Updating Qdrant Turn 1 → Turn 2 Probe:** Confirm that after Turn 1 syncs a `ura.go.ug` PDF into Qdrant and logs it in `web_sync_ledger`, Turn 2 on the same topic answers from Qdrant in `< 300 ms` with `0` outbound web requests.

### G.2 Documentation & Governance (Same PR as Final Verification)
- **ADR-0008:** `docs/adr/0008-live-web-research-ephemeral-pdf-sandbox-and-self-updating-qdrant.md` (registered in `docs/adr/README.md`).
- **Runbook:** `docs/runbooks/live-web-research-and-qdrant-sync.md` (configuring `FLAG_LIVE_WEB_RESEARCH`, `FLAG_AUTO_QDRANT_WEB_SYNC`, inspecting `web_sync_ledger` and `corpus_tombstones`, rolling back an auto-synced document from `App/Data/archive/`).
- **Update:** `docs/RAG_ARCHITECTURE.md`, `docs/MODEL_CARD.md`, `docs/GAPS_AND_AGENTIC_ROADMAP.md` (closing `G222` live FX / BoU rate gap), and `.env.example`.

**Deliverable / PR #7:** `docs(governance): ADR-0008, live web research & Qdrant auto-sync runbook, and evaluation report`.

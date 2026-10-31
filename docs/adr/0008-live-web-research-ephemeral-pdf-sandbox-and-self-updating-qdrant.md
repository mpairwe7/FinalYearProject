# ADR-0008: Sovereign Live Web Research, Global FX Engine (`XE.com` / Google Currency), Ephemeral `tmpfs` PDF Sandbox & Self-Updating Qdrant Hot-Swap

## Status

Accepted — 2026-10-22

Partly superseded by [ADR-0021](0021-planner-bound-web-evidence-and-honest-live-checks.md): the trigger set, the source policy (commercial FX sites are no longer Tier 1), the search provider and FX precedence. The sandbox and corpus sync stand.

## Context and Problem Statement

While ADR-0007 established a code-owned tax scenario planner (`scenario_planner.py`), deterministic figure lock (`verify_figure_lock`), and two-tier local reasoning (`Qwen/Qwen3.8-27B-FP8` + `Sunbird/Sunflower-Qwen3.5-9B`), live testing on complex expatriate and cross-border scenarios (`docs/plans/live-web-research-and-self-updating-qdrant-plan.md`) revealed four operational gaps:

1. **Streaming vs. REST Grounding Asymmetry (`service.py`)**: On the Chrome SSE streaming path (`/v1/chat/stream`), `_apply_output_guards()` computed `HybridRetriever.compute_faithfulness`, `verify_claims`, and `_evaluate_response_judge` using only `contexts` (`hits`), omitting `verified_facts_block` from `scenario_planner.build_verified_facts`. As a result, valid FY2026-27 PAYE figures (`UGX 335,000` nil band) were falsely flagged as numeric contradictions against older corpus passages (`UGX 235,000`) and replaced with the contradiction withholding message.
2. **Missing Live Foreign Exchange Conversion (`G222`)**: When taxpayers stated salaries or invoices in foreign currency (`$6,000/month`, `EUR 15,000`, `GBP 4,500`, `KES 850,000`) without supplying a manual exchange rate, `convert_currency` failed closed and `calculate_paye` could not compute an exact UGX tax figure on Turn 1.
3. **Corpus Drift on `ura.go.ug` / `ulii.org` Publications**: When URA publishes a newer sector guide, public notice, penalty waiver circular, or used motor vehicle CIF valuation schedule on `ura.go.ug`, or when ULII publishes an assented Amendment Act, Qdrant continued serving older editions (`FY2024-25` / `FY2025-26`) until an offline re-index was manually executed.
4. **Disk & Security Risk of Unbounded PDF Downloads**: Downloading arbitrary PDFs or workbooks from the web onto persistent disk risks storage exhaustion, parser exploits (`/JavaScript`, `/Launch`, `/EmbeddedFiles`), and indirect prompt injection.

## Decision Outcome

We implemented a six-phase Sovereign Live Web Research and Self-Updating Qdrant architecture (Phases A–F, PRs #608–#613) gated behind `FLAG_LIVE_WEB_RESEARCH` and `FLAG_AUTO_QDRANT_WEB_SYNC`:

1. **Streaming Verified-Facts Parity & `web_sync_ledger` Schema (Phase A — PR #608)**:
   - Wired `_evaluate_calculation_context` and `verified_facts` into `_apply_output_guards()`, `compute_faithfulness()`, `verify_claims()`, and `_evaluate_response_judge()` on `/v1/chat/stream`.
   - Added the `web_sync_ledger` table and helpers (`upsert_web_sync_entry`, `get_active_web_sync_entry`, `list_web_sync_entries`) in both SQLite (`database.py`) and PostgreSQL (`postgres.py`).
2. **Live Global Exchange Rate Engine (`App/backend/app/web_fx.py`, Phase B — PR #609)**:
   - Fetches real-time mid-market exchange rates (`USD`, `EUR`, `GBP`, `KES`, `TZS`, `RWF`, `AED`, `CNY`, `ZAR`, `JPY`, `INR`, `CAD`, `CHF`, `AUD` → `UGX`) via `XE.com` primary, Google Finance secondary, and Open ER JSON mirror tertiary, with pair-bound regex extraction, a `±35%` plausible bounds guard against `BOU_mid_rates_2026.json`, a 30-minute L1/Redis cache (`ura:fx:live:{CUR}:UGX`), and a 90-second negative cache.
   - Wired into `ConvertCurrencyTool.compute` (`tools/calculators.py`) and chained into `value_benefit_in_kind(kind="housing")` and `calculate_paye` in `scenario_planner.py`.
3. **8-Trigger Web Research Planner & 3-Tier Source Authority Filter (`App/backend/app/web_research.py`, Phase C — PR #610)**:
   - Evaluates 8 deterministic triggers (`fx_rate`, `stale_corpus_edition`, `vehicle_valuation_cif`, `public_notice_or_waiver`, `efris_dts_gazette`, `bill_assent_gazette`, `eac_cet_stay`, `unverified_rate_or_low_confidence`).
   - Synthesizes PII-scrubbed keyword queries (`scrub_query_pii_for_web`) so taxpayer TINs, NINs, phone numbers, emails, and raw narrative never leave the server.
   - Enforces a 3-tier Source Authority Policy (`classify_source_authority`): Tier 1 (`ura.go.ug`, `ulii.org`, `parliament.go.ug`, `finance.go.ug`, `bou.or.ug`, `ursb.go.ug`, `unbs.go.ug`, `eac.int`), Tier 2 (`pwc.com`, `deloitte.com`, `ey.com`, `kpmg.com`, `bowmanslaw.com`, `grantthornton.co.ug` — Uganda-scoped only), and Tier 3 (hard-blocking foreign revenue authorities such as `kra.go.ke`, `tra.go.tz`, `rra.gov.rw`, `irs.gov`, `hmrc.gov.uk`).
4. **Ephemeral RAM-Backed PDF/XLSX Sandbox (`App/backend/app/ephemeral_pdf_sandbox.py`, Phase D — PR #611)**:
   - Creates turn-scoped `0o700` directories under `/dev/shm/ura-web-sandbox/<turn_id>/` (or a dedicated `tmpfs` mount configured via `URA_WEB_SANDBOX_DIR`) and wipes the directory tree (`shutil.rmtree(sandbox_dir)`) in a `finally:` block, logging any cleanup error explicitly rather than silencing it.
   - Enforces HTTPS + Tier-1 hostname allowlist on every redirect hop, a 25 MB mid-stream byte cap, `malware_scan.scan_bytes`, `pdf_guards.inspect_pdf_bytes(..., limits=QUERY_PDF_LIMITS)`, optional `document_worker` subprocess isolation (`DOCUMENT_PARSE_ISOLATED=true`), and page-level prompt-injection screening (`scan_retrieved_text` + `screen_tool_result`).
   - Caches extracted chunks by `sha256` in Redis (`ura:pdf_chunks:{sha256}`, 24h TTL) and micro-reranks the top 4 chunks against the taxpayer's query on GPU 2.
5. **Self-Updating Qdrant Comparator & Hot-Swap Coordinator (`App/backend/app/corpus_sync.py`, Phase E — PR #612)**:
   - Enforces a strict origin invariant (`https` + `ura.go.ug`, `ulii.org`, or `parliament.go.ug` / `bills.parliament.ug` only) before any document can modify Qdrant.
   - Compares `(sha256, fiscal_year, doc_kind, page_count)` against existing counterparts in `App/Data/pdf_jsonl/` and Qdrant across five outcomes: `verified_current`, `skipped_older`, `replaced_outdated` (upserting new chunks first, tombstoning and deleting superseded points, archiving old `.jsonl` to `App/Data/archive/`, writing canonical new `.jsonl`, and refreshing `index_freshness.json`), `needs_review`, and `added_new`.
   - Records a 7-day verification entry in `web_sync_ledger` so Turn 2+ queries on the same family skip external web search completely (`0` outbound HTTP requests).
6. **REST & SSE End-to-End Orchestration (`App/backend/app/service.py`, `App/backend/app/main.py`, Phase F — PR #613)**:
   - Hooks `run_web_research` into `_generate_en` (`/v1/chat`) and `generate_retrieval_only` + `_run_chat_turn_events` (`/v1/chat/stream`), emitting SSE phase events (`web_research.started`, `pdf_sandbox.extracting`, `qdrant_sync.updated`) and exposing `web_sync_ledger` alongside `tombstones` in `GET /v1/admin/tombstones`.

## Consequences

- **Positive**:
  - Foreign-currency tax scenarios (`USD`, `EUR`, `GBP`, `KES`) compute exact Turn-1 UGX tax figures using live XE.com / Google Currency rates while preserving full auditability and `verify_figure_lock` / `verify_claims` compliance across both `/v1/chat` and `/v1/chat/stream`.
  - Outdated URA sector guides and handbooks in Qdrant self-heal automatically from `ura.go.ug` / `ulii.org` on Turn 1 and serve directly from Qdrant in `< 200 ms` with `0` outbound requests on subsequent turns.
  - Zero raw PDFs remain on disk after turn completion (`finally:` `tmpfs` wipe).
- **Negative / Trade-offs**:
  - Turn 1 on an uncached stale document family incurs a bounded `2–5 s` latency for streaming download, `pdf_guards` inspection, and Qdrant embedding upsert; mitigated by the 24h `sha256` chunk cache and 7-day `web_sync_ledger` fast-path skip.

## Confirmation

Validated continuously in CI by:
- `tests/agents/test_web_sync_ledger.py`
- `tests/agents/test_web_fx.py`
- `tests/agents/test_web_research_router.py`
- `tests/agents/test_ephemeral_pdf_sandbox.py`
- `tests/agents/test_corpus_sync.py`
- `tests/agents/test_live_web_orchestration_e2e.py`
- `tests/agents/test_web_research_scenarios_eval.py` (`evals/web_research_scenarios/scenarios.jsonl`)

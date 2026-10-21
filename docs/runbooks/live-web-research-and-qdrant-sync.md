# Runbook: Sovereign Live Web Research, Global FX & Self-Updating Qdrant Sync

**ADR Reference**: [ADR-0008](../adr/0008-live-web-research-ephemeral-pdf-sandbox-and-self-updating-qdrant.md)  
**Modules**: `App/backend/app/web_fx.py`, `App/backend/app/web_research.py`, `App/backend/app/ephemeral_pdf_sandbox.py`, `App/backend/app/corpus_sync.py`

---

## 1. Overview

When enabled, the Phase 41 Live Web Research and Self-Updating Qdrant subsystem provides:
1. **Live Global Foreign Exchange (`web_fx.py`)**: Real-time mid-market exchange rate lookups (`USD`, `EUR`, `GBP`, `KES`, etc. → `UGX`) via `XE.com` primary, Google Finance secondary, and Open ER JSON mirror tertiary, validated against `±35%` plausible bounds around `BOU_mid_rates_2026.json` and cached for 30 minutes in L1/Redis (`ura:fx:live:{CUR}:UGX`).
2. **8-Trigger Web Research Router (`web_research.py`)**: Fires only when a turn requires external verification (`fx_rate`, `stale_corpus_edition`, `vehicle_valuation_cif`, `public_notice_or_waiver`, `efris_dts_gazette`, `bill_assent_gazette`, `eac_cet_stay`, `unverified_rate_or_low_confidence`), scrubbing all PII before outbound search and enforcing a 3-tier Source Authority allowlist.
3. **Ephemeral `tmpfs` PDF/XLSX Sandbox (`ephemeral_pdf_sandbox.py`)**: Streams official Tier-1 PDFs/workbooks into `/tmp/ura-web-sandbox/<turn_id>/` (`0o700`, 25 MB cap), runs `malware_scan` + `pdf_guards.inspect_pdf_bytes` + prompt-injection screening, micro-reranks the top 4 chunks on GPU 2, and unconditionally deletes the directory in a `finally:` block.
4. **Self-Updating Qdrant Hot-Swap (`corpus_sync.py`)**: Compares discovered `ura.go.ug` / `ulii.org` PDFs against Qdrant and `App/Data/pdf_jsonl/`. When a single-edition full guide has a strictly newer `fiscal_year`, embeds and upserts the new chunks into Qdrant, tombstones and deletes the superseded points, moves the replaced `.jsonl` to `App/Data/archive/`, writes the new `.jsonl`, refreshes `index_freshness.json`, and stamps a 7-day entry in `web_sync_ledger`.

---

## 2. Feature Flags & Environment Variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `FLAG_LIVE_WEB_RESEARCH` | `false` | Enables live FX lookups (`web_fx.py`), the 8-trigger web research router (`web_research.py`), and the ephemeral `tmpfs` PDF/XLSX sandbox (`ephemeral_pdf_sandbox.py`). |
| `FLAG_AUTO_QDRANT_WEB_SYNC` | `false` | Enables automatic Qdrant hot-swapping, old-document tombstoning, JSONL archival, and `web_sync_ledger` persistence (`corpus_sync.py`). |
| `LIVE_FX_CACHE_TTL_SECONDS` | `1800` | Positive cache TTL (30 minutes) for live FX quotes in L1 memory and Redis (`ura:fx:live:{CUR}:UGX`). |
| `LIVE_FX_TIMEOUT_SECONDS` | `4.0` | Per-provider HTTPS timeout for XE.com / Google Finance / Open ER FX requests. |
| `MAX_SANDBOX_PDF_BYTES` | `26214400` | Maximum streamed download size (25 MB) for any PDF/XLSX in the ephemeral sandbox. |
| `URA_WEB_SANDBOX_DIR` | `/tmp/ura-web-sandbox` | Base directory for turn-scoped ephemeral `tmpfs` sandboxes. |
| `PDF_ARCHIVE_DIR` | `App/Data/archive` | Archive directory where superseded `.jsonl` files are moved on `replaced_outdated`. |
| `WEB_SYNC_TTL_SECONDS` | `604800` | TTL (7 days) for verified/synced entries in `web_sync_ledger` before re-checking `ura.go.ug`. |

To enable at runtime via environment variables (in `.env` or `configs/prototype.env`):
```bash
export FLAG_LIVE_WEB_RESEARCH=true
export FLAG_AUTO_QDRANT_WEB_SYNC=true
```

---

## 3. Inspecting `web_sync_ledger` and `corpus_tombstones`

### Via Admin REST Endpoint
Authenticated `ura_admin` / `ura_staff` users can inspect both active tombstones and the `web_sync_ledger` via:
```bash
curl -sS -H "Authorization: Bearer $ADMIN_TOKEN" \
  http://localhost:8887/v1/admin/tombstones?limit=100 | jq .
```
The response includes:
- `tombstones`: Superseded or manually tombstoned `source_uri` / `chunk_id` rows (`created_by="corpus_sync"` for auto-superseded guides).
- `web_sync_ledger`: Recent verification and hot-swap records (`doc_family`, `topic_key`, `source_uri`, `ura_url`, `pdf_sha256`, `fiscal_year`, `action_taken`, `superseded_source`, `synced_at`, `expires_at`).

### Via Python CLI
```bash
PYTHONPATH=App/backend python3 -c '
from app import database as db
print("Tombstones:", db.list_corpus_tombstones(limit=20))
print("Web Sync Ledger:", db.list_web_sync_entries(limit=20))
'
```

---

## 4. Rolling Back an Auto-Synced Document

If an auto-synced `ura.go.ug` document (`replaced_outdated` or `added_new`) needs to be rolled back to the previous edition:

1. **Restore the archived `.jsonl` file from `App/Data/archive/`:**
   ```bash
   mv App/Data/archive/<OLD_STEM>.jsonl App/Data/pdf_jsonl/<OLD_STEM>.jsonl
   rm -f App/Data/pdf_jsonl/<NEW_STEM>.jsonl
   ```
2. **Remove the corpus tombstone via the Admin API or CLI:**
   ```bash
   curl -X DELETE -H "Authorization: Bearer $ADMIN_TOKEN" \
     http://localhost:8887/v1/admin/tombstones/<TOMBSTONE_ID>
   ```
3. **Refresh the freshness snapshot and re-index the PDF JSONL corpus:**
   ```bash
   PYTHONPATH=App/backend python3 -m app.indexer --pdf-jsonl-only
   PYTHONPATH=App/backend python3 -m app.freshness --write
   PYTHONPATH=App/backend python3 -m app.freshness --check
   ```

---

## 5. Verification & Regression Testing

Run the complete deterministic Phase 41 test suite (offline, zero external network calls):
```bash
PYTHONPATH=App/backend python3 -m pytest \
  tests/agents/test_web_sync_ledger.py \
  tests/agents/test_web_fx.py \
  tests/agents/test_web_research_router.py \
  tests/agents/test_ephemeral_pdf_sandbox.py \
  tests/agents/test_corpus_sync.py \
  tests/agents/test_live_web_orchestration_e2e.py \
  tests/agents/test_web_research_scenarios_eval.py -q
```

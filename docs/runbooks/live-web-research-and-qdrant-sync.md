# Runbook: Sovereign Live Web Research, Global FX & Self-Updating Qdrant Sync

**ADR Reference**: [ADR-0008](../adr/0008-live-web-research-ephemeral-pdf-sandbox-and-self-updating-qdrant.md), [ADR-0021](../adr/0021-planner-bound-web-evidence-and-honest-live-checks.md)  
**Modules**: `App/backend/app/web_fx.py`, `App/backend/app/web_research.py`, `App/backend/app/web_search_providers.py`, `App/backend/app/ephemeral_pdf_sandbox.py`, `App/backend/app/corpus_sync.py`

---

## 1. Overview

When enabled, the Phase 41 Live Web Research and Self-Updating Qdrant subsystem provides:
1. **Indicative FX (`web_fx.py`)**: market quotes to `UGX` from `XE.com`, Google Finance and the Open ER mirror, for estimating before the Bank of Uganda rate is to hand. Sources that disagree by more than 2% produce no rate, a stale mirror is refused, and a verified BoU snapshot always takes precedence.
2. **9-Trigger Live Check (`web_research.py`)**: decided deterministically from the question (`fx_rate`, `stale_corpus_edition`, `vehicle_valuation_cif`, `public_notice_or_waiver`, `efris_dts_gazette`, `bill_assent_gazette`, `eac_cet_stay`, `unverified_rate_or_low_confidence`, `recency_intent`). Queries are built from a closed vocabulary and run through a provider chain (`web_search_providers.py`). Web text stays labelled evidence and every check ends in a `status`.
3. **Ephemeral `tmpfs` PDF/XLSX Sandbox (`ephemeral_pdf_sandbox.py`)**: Streams official Tier-1 PDFs/workbooks into `/dev/shm/ura-web-sandbox/<turn_id>/` (or `URA_WEB_SANDBOX_DIR` on a dedicated `tmpfs` mount, `0o700`, 25 MB cap), runs `malware_scan` + `pdf_guards.inspect_pdf_bytes` + prompt-injection screening, micro-reranks the top 4 chunks on GPU 2, and deletes the directory in a `finally:` block while logging any cleanup failure.
4. **Self-Updating Qdrant Hot-Swap (`corpus_sync.py`)**: Compares discovered `ura.go.ug` / `ulii.org` / `parliament.go.ug` PDFs against Qdrant and `App/Data/pdf_jsonl/`. When a single-edition full guide has a strictly newer `fiscal_year`, embeds and upserts the new chunks into Qdrant, tombstones and deletes the superseded points, moves the replaced `.jsonl` to `App/Data/archive/`, writes the new `.jsonl`, refreshes `index_freshness.json`, and stamps a 7-day entry in `web_sync_ledger`.

---

## 2. Feature Flags & Environment Variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `FLAG_LIVE_WEB_RESEARCH` | `false` | Enables indicative FX, the 9-trigger live check and the ephemeral `tmpfs` PDF/XLSX sandbox (`ephemeral_pdf_sandbox.py`). |
| `FLAG_AUTO_QDRANT_WEB_SYNC` | `false` | Enables automatic Qdrant hot-swapping, old-document tombstoning, JSONL archival, and `web_sync_ledger` persistence (`corpus_sync.py`). |
| `FX_CACHE_TTL_SECONDS` | `1800` | Positive cache TTL (30 minutes) for FX quotes in L1 memory and Redis (`ura:fx:live:{CUR}:UGX`). |
| `FX_HTTP_TIMEOUT_SECONDS` | `3.0` | Per-source HTTPS timeout. `FX_LOOKUP_BUDGET_SECONDS` (`6.0`) bounds a whole lookup. |
| `FX_MAX_SOURCE_DIVERGENCE` | `0.02` | Relative gap above which two sources produce no rate. |
| `FX_MAX_PROVIDER_AGE_SECONDS` | `345600` | Oldest provider timestamp accepted from the mirror. |
| `WEB_SEARCH_PROVIDERS` | `tavily,brave,searxng` | Failover order. Each is used only once configured. A self-hosted SearXNG still passes the query to external engines, so restrict its engines if that matters. |
| `TAVILY_API_KEY`, `BRAVE_SEARCH_API_KEY`, `SEARXNG_URL` | unset | Provider credentials. A hosted provider is an external processor (G35). |
| `WEB_SEARCH_ALLOW_UNSANCTIONED` | `true` | HTML fallback outside production. Ignored in production. |
| `WEB_RESEARCH_TURN_BUDGET_SECONDS` | `8.0` | Wall clock for search plus document extraction. Searches share `WEB_RESEARCH_SEARCH_BUDGET_SECONDS` (`4.0`). |
| `WEB_RESEARCH_MAX_QUERIES` | `3` | Searches per turn, shared across triggers. |
| `WEB_RESEARCH_RECENCY_HORIZON_DAYS` | `400` | A notice older than this is reported as history. |
| `PLANNER_MAX_TOOL_CALLS`, `PLANNER_DEADLINE_SECONDS` | `12`, `10.0` | Bounds on one plan. |
| `WEB_SYNC_PRODUCTION_APPROVED` | unset | Required **as well as** `FLAG_AUTO_QDRANT_WEB_SYNC` before a turn may change the corpus in production. |
| `MAX_SANDBOX_PDF_BYTES` | `26214400` | Maximum streamed download size (25 MB) for any PDF/XLSX in the ephemeral sandbox. |
| `SANDBOX_DOWNLOAD_DEADLINE_SECONDS` | `15.0` | Wall clock for one download, redirect hops included (ADR-0032). The per-read `SANDBOX_DOWNLOAD_TIMEOUT_SECONDS` alone does not stop a drip-fed body. |
| `WEB_RESEARCH_DOCUMENT_WORKERS` | `4` | Concurrent live document reads, on their own pool. When all are busy a turn uses search summaries and emits `pdf_sandbox.skipped` (`document_workers_busy`). |
| `URA_WEB_SANDBOX_DIR` | `/dev/shm/ura-web-sandbox` | Base directory on a RAM-backed `tmpfs` mount for turn-scoped ephemeral sandboxes. |
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

## 4. Rolling Back a Superseded Document (`replaced_outdated`)

If an auto-superseded `ura.go.ug` full guide (`action_taken == "replaced_outdated"`) needs to be rolled back to the previous edition:

1. **Restore the archived `.jsonl` file from `App/Data/archive/` and remove the new `.jsonl`:**
   ```bash
   mv App/Data/archive/<OLD_STEM>.jsonl App/Data/pdf_jsonl/<OLD_STEM>.jsonl
   rm -f App/Data/pdf_jsonl/<NEW_STEM>.jsonl
   ```
2. **Remove the corpus tombstone via the Admin API or CLI:**
   ```bash
   curl -X DELETE -H "Authorization: Bearer $ADMIN_TOKEN" \
     http://localhost:8887/v1/admin/tombstones/<TOMBSTONE_ID>
   ```
3. **Invalidate the 7-day `web_sync_ledger` entry so stale metadata does not suppress future checks:**
   ```bash
   PYTHONPATH=App/backend python3 -c '
   from app import database as db
   db.upsert_web_sync_entry(
       topic_key="<TOPIC_KEY>",
       doc_family="<DOC_FAMILY>",
       source_uri="<OLD_STEM>.pdf",
       ura_url="https://ura.go.ug/",
       pdf_sha256="",
       fiscal_year="<OLD_FY>",
       action_taken="verified_current",
       ttl_seconds=0.0,
   )
   '
   ```
4. **Refresh the freshness snapshot and re-index the PDF JSONL corpus:**
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

---

## 6. Operating the live check

Every check ends in one `status`, visible in `ura_web_research_turns_total{status}`, in the
`web_research.completed` stream event and in the `live_check` block of the turn's audit row:

| Status | Meaning | Taxpayer is told |
| --- | --- | --- |
| `confirmed` | Text read from an official document this turn | Nothing extra |
| `unconfirmed` | Only a search summary was seen | To confirm on the linked page |
| `stale_only` | Everything found is past the recency horizon | It may no longer apply |
| `no_official_result` | Search worked; nothing admissible | Nothing was found, which proves nothing |
| `unavailable` | Search failed, or no provider is configured | The check could not be done |
| `ledger_fresh` | Checked recently; the ledger skipped it | Nothing extra |

`live_web_verified` means a document was read. `live_web_unconfirmed` means it was not.
A topic left out by `WEB_RESEARCH_MAX_QUERIES`, or whose search failed, appears in
`unchecked_triggers` and adds a sentence saying only part of the question was checked.
Read which providers are live from `architecture_integrity_status()["live_research"]`.

### Live checks failing

`LiveChecksMostlyUnavailable`: most checks could not complete, so taxpayers are being told
so. Look at `ura_web_search_requests_total` by `outcome`. `auth` is a bad key, `rate_limited` is
quota, `circuit_open` means a breaker tripped and will probe again by itself, and no series
at all with `unavailable` turns means no provider is configured (G326). Space secrets drift
from `.env`, so confirm the key exists there before debugging code. Nothing is unsafe while
this fires: answers still carry the disclosure.

### Injection attempts in web results

`WebResultInjectionRejected`: a result was dropped for reading like an instruction. Check
the logged hostname. On an official domain it may mean a compromised page or a court record
quoting one, so tell the site's owner and the security contact. The result never reached
the model, so no answer needs correcting, but a regex only catches what it was written for.

A live **document** that reads like an instruction is rejected whole (ADR-0032, G487): the
turn emits `pdf_sandbox.rejected` and records `guardrail_indirect_injection` with reason
`live_document`, and neither the document nor its search summary is quoted or synced. The
official corpus trips this on no page, so treat every occurrence as real. Fetch the URL from
the log in an isolated environment: a court record or petition that *quotes* an attack is
expected to keep failing and needs no action; anything else on an official host means the
page or its host is compromised. Tell the site's owner and the security contact.

### Document reads skipped as busy

`pdf_sandbox.skipped` with `document_workers_busy` means all `WEB_RESEARCH_DOCUMENT_WORKERS`
reads were in use, so the turn answered from summaries and said so. Occasional events under a
filing-deadline peak are expected. A read has four stages, and only the first has a deadline of
its own:

1. Download: `SANDBOX_DOWNLOAD_DEADLINE_SECONDS` plus at most one `SANDBOX_DOWNLOAD_TIMEOUT_SECONDS`
   read.
2. Malware scan.
3. Isolated parse: `DOCUMENT_PARSE_TIMEOUT_S`, 30 s.
4. Ranking the chunks against the question, which uses the reranker when one is loaded.

For sustained events, find the slow stage in the logs before changing anything:

- "Download exceeded its deadline" points at the host.
- "isolated document parse timed out" points at the document.
- Neither, with reads still slow, points at ranking or a busy reranker.

Raising the worker count adds load to whichever stage is slow, so it is not a fix for any of them.
A read whose turn stopped waiting is not quoted, even through its search summary; when nothing else
was found, the turn reports the check as unavailable rather than "no official result".

### FX sources disagreeing

`FxSourcesDisagreeing`: market sources differ by more than `FX_MAX_SOURCE_DIVERGENCE`, so no
rate is served and the taxpayer is asked for one. It may be real volatility or a broken
parser. Do not raise the threshold to clear it.

### Planned steps failing

`PlannedStepsFailing`: tool steps are ending as `failed` or `skipped`, and answers list them
as "Not calculated". Check the `Planned tool` warnings in the logs. `skipped` points at the
budget or deadline, so look at live FX latency before raising either.

### Pipeline attestation failing

`TurnPipelineAttestationFailing`: a REST turn ran stages out of order or without the input
guard. Take the `pipeline_attestation` block from the audit row for that turn, and treat a
missing guard as a security incident until explained. Streaming turns are not attested yet (G325).

### Provider outage or kill switch

Set `FLAG_LIVE_WEB_RESEARCH=false`. Answers then come from the corpus with no live claim.
`App/docker-compose.gpu-salt.yml` defaults both research flags to `true`, so a stack
started from that overlay has them on unless they are set explicitly. A
single bad provider can be dropped with `WEB_SEARCH_PROVIDERS`.

---

## 6a. Watching what leaves the deployment (ADR-0035)

Every fetch on a turn goes through `app/egress.py`. That covers the PDF sandbox, the search
providers and the FX engine. Each request is counted, and no path, query or body is recorded:

| Metric | Labels | Read it as |
| --- | --- | --- |
| `web_egress_requests_total` | `purpose` (`pdf_sandbox`, `search_tavily`, `search_brave`, `search_searxng`, `search_duckduckgo`, `fx_rate`), `host`, `outcome` (`2xx`…`5xx`, `refused`, or an error class) | Which agent reached which host, and how it ended. |
| `web_egress_bytes_total` | `purpose`, `host` | Volume per destination. |

Each request also writes one log line: `egress purpose=… host=… outcome=… bytes=… ms=…`.

- **`outcome="refused"`:** the destination resolved to a non-public address. This means DNS
  rebinding, a hijacked record, or a misconfigured provider. Treat it as a security event, and check
  the host's DNS before anything else.
- **A provider failing as `oversized`:** its response passed `WEB_PROVIDER_MAX_RESPONSE_BYTES`
  (2 MB). A results page is a few kilobytes, so suspect the endpoint, not the cap.
- **A self-hosted SearXNG on a private network:** this is allowed because its host comes from
  `SEARXNG_URL`. No address from a page or a search result is ever exempt.

## 7. Acceptance probes

Unit tests prove the logic, not a deployment. On the deployed Space, with the flag on, check
each row from the logs and the audit row. A templated reply is not evidence of routing.

| Probe | Expect |
| --- | --- |
| "Is there a penalty waiver public notice?" with no provider | `unavailable`; reply carries the disclosure; no external request |
| The same with a provider | Status other than `unavailable`; `providers` names it in the audit row |
| "Has the filing deadline been extended this month?" twice | Two searches or one cached result, and no semantic-cache answer |
| "What is a TIN?" | No trigger; no outbound request |
| "My landlord john mukasa wants a penalty waiver" | Provider logs show no name; `queries_sha256` in the audit row |
| "$5,000 salary, what is my PAYE?" | Figure described as an estimate; `ura_fx_quotes_total` moves |
| "I want to speak to a human officer", on the real index | `escalated`, ticket created |

Hosted adapters have only been exercised against documented contracts and mock
transports. The first live call to each provider is part of this acceptance.

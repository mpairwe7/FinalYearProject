# ADR-0006: Backend Setup Hardening — CircuitBreaker Single-Probe HALF_OPEN, Global JSON Payload Ceiling, OWASP REST Security Headers & Graceful Pool Shutdown

## Status

Accepted — 2026-10-17

## Context and Problem Statement

An October 2026 QA, security, and backend engineering audit of `App/backend/app/resilience.py`, `App/backend/app/main.py`, `App/backend/app/database.py`, `App/backend/app/postgres.py`, and `App/backend/app/production_readiness.py` against **OWASP API Security Top 10 (API4:2023 / API8:2023)**, **OWASP REST Security Cheat Sheet (2026)**, and **FastAPI Production Deployment Best Practices (2026)** identified four backend setup gaps:

1. **`CircuitBreaker` `HALF_OPEN` Thundering-Herd Stampede (`resilience.py`)**: While `CircuitBreaker` documented that `HALF_OPEN` allows a single test request, `allow_request()` returned `True` for every concurrent request arriving while `_state == CircuitState.HALF_OPEN` before the first probe completed.
2. **Missing Global Non-Upload Request Body Ceiling & OWASP REST Security Headers (`main.py`)**: Document uploads had a byte ceiling, but non-upload JSON routes (`/v1/chat`, `/v1/classify`, `/v1/translate`, etc.) had no middleware `Content-Length` guard (`413 Payload Too Large`), and `/v1/*` API responses did not set `Cache-Control: no-store`, `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`, or `Cross-Origin-Opener-Policy: same-origin`.
3. **Database & MCP Transport Pool Lifecycle (`database.py`, `postgres.py`, `mcp/client.py`, `main.py`)**: SQLite connections in `database.py` did not enable `PRAGMA foreign_keys=ON` or `PRAGMA synchronous=NORMAL`; `postgres._get_pool()` lacked a thread lock against concurrent first-request initialization; and `lifespan` shutdown did not close `MCPClient` HTTP transport pools or database connections.
4. **Production Readiness Gate for Remote MCP & MRTR Secrets (`production_readiness.py`)**: `gap_gate_errors()` did not validate `MCP_SERVER_URL_<NAMESPACE>` or `MCP_REQUEST_STATE_SECRET` length ($\ge 32$ chars) at startup when `APP_ENV=production`.

## Decision Outcome

We implemented a 4-phase backend setup hardening (`G212`):

1. **Single-Probe `HALF_OPEN` Admission in `CircuitBreaker` (`resilience.py`)**: Added `_half_open_probe_in_flight` under `self._lock` in `CircuitBreaker.allow_request()` so at most one probe executes during `HALF_OPEN`, blocking concurrent stampedes until `record_success()` or `record_failure()` completes.
2. **Global JSON Payload Ceiling (`MAX_JSON_BODY_BYTES`) & OWASP REST Headers (`main.py`)**: Enforced `MAX_JSON_BODY_BYTES` (5 MiB default) with HTTP `413` before body parsing on non-upload mutation routes, and added `Cache-Control: no-store` (on `/v1/*` and `/api/v1/*`), `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`, and `Cross-Origin-Opener-Policy: same-origin`.
3. **Database Pragmas, Thread-Safe Pool Init & Graceful Lifespan Drain (`database.py`, `postgres.py`, `mcp/client.py`, `main.py`)**: Added `PRAGMA synchronous=NORMAL` and `PRAGMA foreign_keys=ON` in `database._get_connection()`, guarded `postgres._get_pool()` with `_pool_lock`, added `db.close_db()`, `postgres.close_pool()`, and `MCPClient.close()`, and wired them into `main.py` `lifespan` shutdown.
4. **Production Readiness MCP Gate (`production_readiness.py`)**: Added `_mcp_security_errors()` to `gap_gate_errors()`.

## Consequences

- **Positive**: Prevents recovering LLM/Qdrant/MCP services from being overwhelmed by concurrent `HALF_OPEN` probes; blocks oversized JSON payload DoS at the middleware boundary; prevents proxy/browser caching of sensitive taxpayer responses; and cleanly drains HTTP and DB pools on worker shutdown.
- **Negative / Trade-offs**: Non-upload JSON requests larger than `MAX_JSON_BODY_BYTES` (5 MiB) are rejected with `413 Payload Too Large`.

## Confirmation

Validated continuously in CI by:
- `App/backend/tests/test_resilience.py`
- `tests/agents/test_mcp_2026_october_audit.py`
- `tests/test_monorepo_architecture_governance.py`

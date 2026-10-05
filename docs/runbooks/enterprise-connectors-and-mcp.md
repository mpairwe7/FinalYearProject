# Runbook — Enterprise System Connectors and Model Context Protocol (MCP)

This runbook covers operational administration, security guardrails, failure modes,
and emergency procedures for URA enterprise system connectors and Model Context Protocol (MCP)
tool/resource integrations.

---

## 1. Architectural Baseline

### 1.1 Protocol Baseline
The platform implements the **MCP `2026-07-28` specification**:
- **Stateless JSON-RPC 2.0**: No session state headers (`Mcp-Session-Id`) or stateful `initialize`/`initialized` handshakes.
- **Request Metadata (`_meta`)**: Every request carries `io.modelcontextprotocol/protocolVersion` (`2026-07-28`), client capabilities, and URA vendor context (`ug.go.ura.chatbot/{tenantId,userId,userRole,callId}`).
- **Header-Body Integrity**: Outbound and inbound requests validate `Mcp-Method` and `Mcp-Name` against the JSON-RPC payload to prevent gateway smuggling.
- **Transport Routing**: By default, namespaces execute via `InProcessTransport`. Setting `MCP_SERVER_URL_<NAMESPACE>` binds the namespace to a remote `HttpTransport` with persistent connection pooling and W3C `traceparent` propagation.
- **Mutual TLS (mTLS) & DMZ Isolation**: Remote HTTP transports support mTLS client certificate authentication configured via `MCP_CLIENT_CERT_PATH`, `MCP_CLIENT_KEY_PATH`, and `MCP_CA_CERT_PATH`. System connectors (`dts`, `payment`, `tin`, `ursb`, `bwims`, `efris`) deployable as standalone FastMCP services via `docker-compose.mcp.yml`.

### 1.2 Registered Namespaces & Tools
The system manages 28 registered tools across core tax calculators, statutory rate lookups, knowledge search, tasks, and system connectors:
- **Core Tax Calculators**: `calculate_vat`, `calculate_paye`, `calculate_corporation_tax`, `calculate_withholding`, `calculate_rental_tax`, `calculate_customs_duty`, `calculate_excise_duty`, `calculate_capital_gains`, `check_vat_registration`.
- **System Connectors (Simulators)**: `efris` (4 tools), `digital_tax_stamps` (4 tools), `payment_system` (5 tools), `tin_registration` (4 tools), `ursb` (3 tools), `bwims` (3 tools).
- **Core Rails & Long-Running**: `search_ura_knowledge_base`, `escalate_to_human`, `task_create`, `task_get`, `task_cancel`, `audit_tax_document`.

---

## 2. Security Architecture & Threat Mitigations

The integration complies with **OWASP LLM06 (Excessive Agency)** and **NIST AI RMF**:

### 2.1 Envelope Encryption for Credentials (`TokenVault`)
- Connector secrets, bearer tokens, and partner API credentials are encrypted at rest using **AES-256-GCM envelope encryption** with HKDF key derivation.
- Key material is sourced from `VAULT_MASTER_KEY` (or derived from `JWT_SECRET`).
- Plaintext secrets are never logged, echoed in transcripts, or included in client payloads.

### 2.2 Monetary Transaction Ceilings
- Any tool execution carrying financial value (PRN payments, invoice adjustments, clearance duties) is evaluated against `MAX_TRANSACTION_CEILING_UGX` (default: **UGX 50,000,000**).
- Non-admin calls exceeding this ceiling fail closed with reason: `Transaction amount ... UGX exceeds session ceiling`.
- `ura_admin` role credentials are required to bypass or override this limit.

### 2.3 Distributed Velocity Limiting
- Critical mutating actions are rate-limited to **10 actions per hour** per user (`MCP_MAX_CRITICAL_ACTIONS_PER_HOUR`).
- Enforcement uses Redis sorted sets (`mcp:velocity:{user_id}`) with automatic TTL cleanup, falling back seamlessly to an in-process thread-safe sliding window if Redis is degraded.
- Discovery probes (`discovery-probe`) are exempt to ensure tool discovery does not deplete user execution quotas.

### 2.4 Human Confirmation Gates
- Consequential write tools declare `requires_confirmation: True`.
- When invoked by the model, the system emits an actionable confirmation proposal. Execution is blocked until the taxpayer or staff member explicitly approves the action with an idempotency key.

### 2.5 Server-Side Request Forgery (SSRF) Protection
- Adding external connectors via the `/admin/connectors` workbench executes strict IP resolution filtering.
- Connections targeting RFC 1918 private subnets, loopback addresses (`127.0.0.1`), metadata endpoints (`169.254.169.254`), or unresolvable domains are rejected immediately.

### 2.6 Distributed Saga Compensation
- Multi-action business sequences support atomic rollbacks via `SystemConnector.compensate()`.
- If a downstream action fails (e.g. payment recorded but invoice creation fails), the orchestrator triggers reverse compensation routines.

---

## 3. Operational Procedures

### 3.1 Inspecting Connector Health
1. Navigate to `/admin/connectors` in the staff console.
2. The dashboard displays real-time connectivity status, latency metrics, and error rates.
3. To trigger an active probe, select **Ping** next to any connector.

### 3.2 Adding a New System Connector
1. Open the `/admin/connectors` dashboard as a user with `ura_admin` and staff-writer privileges.
2. Click **+ Add Connector**.
3. Complete the form:
   - **Connector ID**: Unique identifier (e.g., `customs_asycuda`).
   - **Display Name**: Human-readable name.
   - **Endpoint URL**: Must be a valid public HTTPS endpoint (SSRF-checked).
   - **API Token**: Optional bearer token (automatically vaulted via AES-256-GCM).
4. Save and trigger a live test ping.

### 3.3 Binding a Remote MCP Server
To offload an in-process namespace (such as `tax_calculator`) to an out-of-process DMZ server:
```bash
# Set in environment or deployment manifest
MCP_SERVER_URL_TAX_CALCULATOR=https://mcp-tax.dmz.ura.go.ug/rpc
MCP_SERVER_TOKEN_TAX_CALCULATOR=vaulted-bearer-token

# Restart backend service
systemctl restart ura-chatbot-api
```
Verify resolution:
```bash
PYTHONPATH=App/backend python3 -c "
from app.mcp import get_client
client = get_client()
print(client.health())
"
```

---

## 4. Troubleshooting & Incident Response

### 4.1 Circuit Breaker Tripped (`circuit open`)
- **Symptom**: MCP tool calls return `ok: false`, `error: "circuit open"`.
- **Cause**: 3 consecutive transport failures on the remote MCP endpoint.
- **Resolution**:
  1. Inspect network connectivity and remote logs for the affected service.
  2. The breaker automatically resets after a 10-second backoff window once requests succeed.
  3. If the remote service is permanently degraded, unset `MCP_SERVER_URL_<NAMESPACE>` to revert to local in-process execution.

### 4.2 Velocity Limit Rejection
- **Symptom**: User receives `critical action velocity limit exceeded`.
- **Cause**: User attempted more than 10 critical actions within a rolling 60-minute window.
- **Resolution**:
  - Advise taxpayer to wait for the rolling window to clear.
  - To clear for test isolation or manual unblocking (requires Redis access):
    ```bash
    redis-cli DEL "mcp:velocity:<user_id>"
    ```

### 4.3 Output Schema Validation Warning
- **Symptom**: Log message `MCP result for <tool> does not match its outputSchema`.
- **Cause**: The server returned fields that violate the declared JSON schema.
- **Impact**: Non-blocking (result is still passed to the LLM), but signals schema drift.
- **Resolution**: Inspect the tool payload against the schema in `ToolSchema.output_schema` and align the response fields.

---

## 5. Verification Commands

Run the full MCP and connector verification test suites:
```bash
# MCP test suite
PYTHONPATH=App/backend python3 -m pytest App/backend/tests/test_mcp_*.py -q

# Agent, checkpointer, and connector integration tests
PYTHONPATH=App/backend python3 -m pytest tests/agents/test_plugins_orchestrator.py tests/agents/test_mcp.py tests/agents/test_graph_checkpointer.py -q

# Streaming DLP tests
PYTHONPATH=App/backend python3 -m pytest App/backend/tests/test_streaming_dlp.py -q

# Full API manifest and endpoint parity
PYTHONPATH=App/backend python3 -m pytest tests/test_all_endpoints_e2e.py -q
```

---

## 6. Standalone DMZ Microservices & Streaming DLP

### 6.1 Deploying Standalone FastMCP Servers
To launch decoupled MCP microservices in isolated container runtimes:
```bash
docker compose -f docker-compose.yml -f docker-compose.mcp.yml up -d \
  mcp-tax-calculator mcp-ura-account mcp-efris mcp-dts mcp-payment mcp-tin mcp-ursb mcp-bwims
```

Endpoints exposed:
- `mcp-tax-calculator`: `http://localhost:8931` (Health: `/health`)
- `mcp-ura-account`: `http://localhost:8932` (Health: `/health`)
- `mcp-efris`: `http://localhost:8933` (Health: `/health`)
- `mcp-dts`: `http://localhost:8934` (Health: `/health`)
- `mcp-payment`: `http://localhost:8935` (Health: `/health`)
- `mcp-tin`: `http://localhost:8936` (Health: `/health`)
- `mcp-ursb`: `http://localhost:8937` (Health: `/health`)
- `mcp-bwims`: `http://localhost:8938` (Health: `/health`)

### 6.2 Mutual TLS (mTLS) Transport Security
Outbound HTTP transports support client-certificate mutual authentication:
```bash
MCP_CLIENT_CERT_<NAMESPACE>=/etc/ssl/certs/mcp-client.crt
MCP_CLIENT_KEY_<NAMESPACE>=/etc/ssl/certs/mcp-client.key
```

### 6.3 External Cryptographic Audit Timestamping (RFC 3161)
The audit ledger supports external witness anchoring via RFC 3161 Timestamp Authorities (TSA):
```bash
AUDIT_TSA_URL=https://tsa.example.gov.ug/timestamp
```
When configured, Merkle roots from periodic seals are automatically submitted to the TSA, recording verifiable external cryptographic timestamp tokens.

### 6.4 Streaming Data Loss Prevention (DLP)
All SSE (`/v1/chat/stream`) and WebSocket (`/v2/chat/stream`) streams route through `StreamingDLPFilter` (`app/guardrails.py`):
- Stateful 24-character word-boundary window prevents multi-chunk sensitive token leaks (Ugandan 14-character NINs, 10-digit TINs, phone numbers).
- Exemption rules preserve official URA contact numbers and `@ura.go.ug` email channels.

### 6.5 Durable Agent Graph Checkpointing
Agent execution graphs maintain state durability across restarts:
- State snapshots are persisted to the `agent_checkpoints` table on each node transition.
- Supported on both PostgreSQL (clustered production) and SQLite (local testing/dev).
- Allows in-flight tax audits and multi-step filings to survive process reboots and scale events.

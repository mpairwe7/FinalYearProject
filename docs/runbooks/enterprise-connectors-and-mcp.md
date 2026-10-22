# Runbook — Enterprise System Connectors and Model Context Protocol (MCP)

This runbook covers operational administration, security guardrails, failure modes,
and emergency procedures for URA enterprise system connectors and Model Context Protocol (MCP)
tool/resource integrations.

---

## 1. Architectural Baseline

### 1.1 Protocol Baseline
The platform implements the **MCP `2026-07-28` specification**. One implementation of the server-side rules (`App/backend/app/mcp/servers/wire.py`) backs every server, and one client transport (`App/backend/app/mcp/transport.py`) speaks to them:
- **Stateless JSON-RPC 2.0**: No session state headers (`Mcp-Session-Id`) or stateful `initialize`/`initialized` handshakes. An `initialize` is refused with a message naming the supported versions.
- **Request Metadata (`_meta`)**: Every request carries `io.modelcontextprotocol/protocolVersion` (`2026-07-28`), client capabilities, and URA vendor context (`ug.go.ura.chatbot/{tenantId,userId,userRole,callId}`). The vendor context is a statement by the chat backend about the taxpayer it is acting for: a server honours it only on an authenticated request (§2.1).
- **Discovery**: Every server implements `server/discover` (supported versions, capabilities, identity). It is also the reachability probe — the protocol's own `ping` was removed in this revision; servers still answer it for a caller one release behind.
- **Results**: Every result carries `resultType`; cacheable ones carry `ttlMs` and a `cacheScope` of `public` or `private`.
- **Header-Body Integrity**: `MCP-Protocol-Version`, `Mcp-Method` and `Mcp-Name` are required on every POST. A missing or disagreeing header is `400` with `HeaderMismatch` (`-32020`), which prevents a gateway authorizing one operation while the server runs another.
- **Error mapping**: unsupported protocol version → `400` / `-32022` with the supported list; unknown method → `404` / `-32601`; unknown tool, resource or prompt → `-32602`.
- **Transport Routing**: By default, namespaces execute via `InProcessTransport`. Setting `MCP_SERVER_URL_<NAMESPACE>` binds the namespace to a remote `HttpTransport` with a persistent connection pool and W3C `traceparent` propagation. The URL must be `http(s)`, carry no credentials, and be `https` in production; an unusable URL is logged and the namespace stays in-process.
- **Mutual TLS (mTLS) & DMZ Isolation**: Remote HTTP transports support mTLS client certificates configured per namespace via `MCP_CLIENT_CERT_<NAMESPACE>` and `MCP_CLIENT_KEY_<NAMESPACE>`. System connectors (`dts`, `payment`, `tin`, `ursb`, `bwims`, `efris`) deploy as standalone services via `docker-compose.mcp.yml`, or together from one process via `App/docker-compose.connectors.yml`.

### 1.2 Registered Namespaces & Tools
The system manages **72 registered tools across 18 namespaces** (`28` in `tax_calculator`) spanning core tax calculators, 2026 statutory calculators, Cross-MDA interdependence & queue-reduction resolvers (`ADR-0009`), statutory rate lookups, knowledge search, memory, tasks, and DMZ system connectors:
- **Core & 2026 Statutory Tax Calculators (`tax_calculator` — 22 tools)**: `calculate_vat`, `calculate_paye`, `calculate_corporation_tax`, `calculate_withholding`, `calculate_rental_tax`, `calculate_customs_duty`, `calculate_excise_duty`, `calculate_capital_gains`, `check_vat_registration`, `value_benefit_in_kind`, `determine_residency`, `convert_currency`, `calculate_stamp_duty`, `calculate_digital_services_tax`, `calculate_gaming_betting_tax`, `calculate_advance_vehicle_tax`, `calculate_presumptive_tax`, `check_startup_exemption`, `calculate_penalties_and_interest`, `calculate_rental_pooled`, `check_objection_validity`, `audit_tax_document`.
- **Cross-MDA Interdependence, Queue-Reduction & New-Taxpayer Onboarding (`tax_calculator` / `tools/mda_interdependence.py` — 6 tools, `ADR-0009`)**: `resolve_mda_ntr_payment`, `assess_life_event_tax_onboarding`, `evaluate_b2g_and_uesw_clearance`, `build_new_taxpayer_compliance_shield`, `simulate_tax_relief_and_instalments`, `resolve_efris_and_account_recovery`.
- **System Connectors (Simulators)**: `efris` (4 tools), `digital_tax_stamps` (4 tools), `payment_system` (5 tools), `tin_registration` (4 tools), `ursb` (3 tools), `bwims` (3 tools).
- **Core Rails, Education, Empathy, Memory, Graph & Long-Running**: `search_ura_knowledge_base`, `explain_tax_concept`, `assess_emotional_tone`, `lookup_rate`, `list_available_rates`, `compare_tax_years`, `get_current_date`, `get_next_deadlines`, `graph_resolve_rate`, `graph_rate_history`, `graph_effective_on`, `navigate_external_portal`, `memory_store`, `memory_recall`, `memory_discard`, `escalate_to_human`, `task_create`, `task_get`, `task_cancel`, `ura_account_profile`, `ura_action_proposal`.

---

## 2. Security Architecture & Threat Mitigations

The integration complies with **OWASP LLM06 (Excessive Agency)** and **NIST AI RMF**:

### 2.1 Authenticating the link
Every HTTP request to a connector server must present a bearer credential. With nothing configured a server is **locked** — it answers `/health` and refuses everything else with `401`.

| Mode | Server reads | API (client) reads | Use |
| --- | --- | --- | --- |
| Shared token | `MCP_SERVER_TOKEN_<NAMESPACE>` | `MCP_SERVER_TOKEN_<NAMESPACE>` (the same name) | Prototype, internal link |
| OAuth 2.1 | `MCP_SERVER_OAUTH_ISSUER`, `MCP_SERVER_OAUTH_JWKS_URL`, `MCP_SERVER_RESOURCE`, optional `MCP_SERVER_OAUTH_SCOPES`, `MCP_SERVER_OAUTH_AUTHORIZATION_SERVERS` | `MCP_OAUTH_TOKEN_URL_<NAMESPACE>`, `MCP_OAUTH_CLIENT_ID_<NAMESPACE>`, `MCP_OAUTH_CLIENT_SECRET_<NAMESPACE>`, optional `MCP_OAUTH_SCOPE_<NAMESPACE>` | Production |
| Anonymous | `MCP_SERVER_ALLOW_ANONYMOUS=true` | — | Local development only; refused in production; every caller is `public` |

- A shared token shorter than 32 characters is treated as absent. Generate one with `openssl rand -hex 32`.
- Under OAuth the server validates an RS256 token's signature, issuer, expiry and **audience** (`MCP_SERVER_RESOURCE`): a token minted for another service — this API's own user tokens included — is refused. It serves RFC 9728 metadata at `/.well-known/oauth-protected-resource[/<path>]` and answers a missing or bad token with `WWW-Authenticate: Bearer resource_metadata="…"`. Server settings may be suffixed `_<NAMESPACE>` so several servers in one process can differ.
- The API obtains OAuth tokens with the client-credentials grant, sends the RFC 8707 `resource` parameter so each token is bound to one server, caches a token until shortly before it expires, and asks once for a fresh one if the server answers `401`. The token endpoint is configured, never discovered from a server response.
- The staff console and runtime connector APIs do not accept or persist endpoint credentials. Keep secret values in the platform secret manager; never put them in browser forms, source control, or chat transcripts. Tokens are sent in the `Authorization` header only — never in a URL — and redirects are not followed.
- Restrict access to deployment secrets and rotate credentials through the owning platform's secret-management workflow.

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
- Dynamic endpoint registration and runtime connector configuration are disabled. `POST /v1/connectors/register` and `POST /v1/connectors/{name}/configure` return `410 Gone`; they do not validate or activate submitted endpoint or credential data.
- Remote MCP endpoints are supplied through reviewed deployment configuration. Before rollout, review DNS resolution, HTTPS/TLS identity, redirects, and network egress rules so the configured server cannot reach loopback, private, or cloud metadata services.
- The `/admin/connectors` screen only reports and checks configured connectors. Its health checks are not a substitute for the endpoint and network review.

### 2.6 Distributed Saga Compensation
- Multi-action business sequences support atomic rollbacks via `SystemConnector.compensate()`.
- If a downstream action fails (e.g. payment recorded but invoice creation fails), the orchestrator triggers reverse compensation routines.

---

## 3. Operational Procedures

### 3.1 Inspecting Connector Health
1. Navigate to `/admin/connectors` in the staff console.
2. In **Built-in connector status**, the dashboard identifies each fixture-backed plugin as a local simulation and shows local health and registered tool counts.
3. In **Taxpayer-chat integrations**, review the remote MCP services and read-only operations available under your current role and consent scopes. The catalog is policy-filtered and does not expose endpoint URLs, credentials, or tool schemas.
4. Each chat integration shows a `status`: `available` means the server answered recently; `unavailable` means it is bound but is not answering, and it cannot be selected. The tool *list* is kept as long as the server allows (the bundled servers say an hour), but whether the server is answering is believed for only 30 seconds (`LIVENESS_TTL_S`) and then asked again with `server/discover`; a call that fails at the connection, or with 502/503/504, marks it down at once. **Test connection** still checks the local simulator only, and reports its latency and tool count.
5. **Onboarding diagnostics** lists every server bound to this deployment and why it is, or is not, offered in chat — status, demo/live, how the link authenticates (a kind, never the credential), and a verdict per operation with the rule it breaks. It is built from the same checks the chat runs (`GET /v1/connectors/diagnostics`, staff only; see §4.6).

### 3.2 Deploying a New System Connector
1. Open `/admin/connectors` and use **Add connector** to review the setup path. Each step — bind, declare, switch on — is checked against the deployment as it is now and marked *Done* or *Needs attention*. The dialog still changes nothing: it does not submit or store connector data.
2. Implement the connector server and register its namespace, tools, permissions, and protocol through the reviewed code change.
3. Configure the remote endpoint with `MCP_SERVER_URL_<NAMESPACE>` and store any credential in the deployment secret store. Never submit bearer tokens or private keys through the browser.
4. Review DNS/IP resolution, HTTPS/TLS identity, redirects, and outbound network access before deployment.
5. Deploy the reviewed change and refresh `/admin/connectors`. The chat integration list reflects the current deployment and caller policy, including whether each bound server is answering. If the server is not listed there, **Onboarding diagnostics** says why (§4.6). **Test connection** checks local simulator health only.
6. Declare what is behind the server: `MCP_SERVER_MODE_<NAMESPACE>=live` only for a reviewed live integration. Unset means `simulation`, which is shown as demo data and is never offered in production.
7. From the deployed API runtime, confirm the namespace binds to the approved remote transport and that the server answers `server/discover`. The configured credential is sent by the transport in the authorization header and must never be printed:

   ```bash
   PYTHONPATH=App/backend python3 - <<'PY'
   from app.mcp import get_client

   namespace = "tax_calculator"
   client = get_client()
   binding = client.health()["namespaces"].get(namespace, {})
   expected_transport = f"http:{namespace}"
   if binding.get("transport") != expected_transport:
       raise SystemExit(f"{namespace} is not bound to its approved remote transport")
   if not client.ping(namespace):
       raise SystemExit(f"{namespace} did not answer server/discover")
   print({"namespace": namespace, "transport": expected_transport, "reachable": True})
   PY
   ```

   Compare the namespace's endpoint with the approved deployment configuration. `client.ping()` sends `server/discover` with the configured credential, so success also confirms the credential was accepted. Never print or paste the credential. Enable supported actions only after the remote binding and authentication checks pass.

The browser registration and configuration APIs fail closed with `410 Gone` for authorized requests that reach the handlers. A successful response must never be interpreted as a connector registration or configuration change.

### 3.3 Binding a Remote MCP Server
To offload an in-process namespace (such as `tax_calculator`) to an out-of-process DMZ server:
```bash
# Set in environment or deployment manifest
MCP_SERVER_URL_TAX_CALCULATOR=https://mcp-tax.dmz.ura.go.ug/rpc
MCP_SERVER_TOKEN_TAX_CALCULATOR=<from the secret store; the server holds the same value>

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

### 4.3 A Connector Shows as Unavailable
- **Symptom**: the picker or `/admin/connectors` lists a service with `status: unavailable`; the API log has `MCP tool discovery failed namespace=<ns>`.
- **Causes, in the order to check them**:
  1. The server is locked. `curl -s localhost:8930/health` (gateway) shows `"auth": "locked"` for the system, and its log says why — no credential, a token under 32 characters, or incomplete OAuth settings. Most often `MCP_LINK_TOKEN` was not exported when the stack was brought up.
  2. The two ends hold different credentials. The API log names it: `was refused: the server did not accept this client's credentials (HTTP 401)`.
  3. The server is down or unreachable from the API container.
  4. A protocol mismatch: `the server does not speak MCP 2026-07-28 (it supports […])`.
  5. Not unavailable but **absent**: every server answers, yet the picker lists nothing. `curl -s localhost:8930/health` shows `"tools": 0` per server and the gateway log has `connector simulators not registered: plugins could not be loaded`. The `plugins` package did not load in the gateway process (G155: the `app` package directory was on `sys.path`); Onboarding diagnostics shows "0 of 0 operations".
- **Behaviour while it lasts**: the API does not retry that server for 15 seconds after a failed discovery, so a connector that is down costs one short deadline, not one per request. Other connectors are unaffected. A tool bound to an unavailable server is **not** run from the copy registered in the API process.
- **What a taxpayer who had chosen it sees**: their turns are still answered. The endpoint leaves the service out of the turn and says so — `connector_notices: [{"namespace": …, "label": …, "reason": "unavailable"}]` on `/v1/chat`, the same list in the stream's `metadata` frame — and the chat shows "*<service>* is not answering right now, so this answer does not include a lookup there." The chip stays chosen and is marked *Unavailable*; the next turn after it recovers uses it again. Before this, the endpoint answered 403, the client's retry answered 403 too, and every message in that conversation read "could not reach the URA knowledge base" until the taxpayer removed the chip. Count: `ura_chat_connector_skipped_total{reason="unavailable"}`.
- **Resolution**: fix the cause and wait out the 15-second window, or restart the API to clear it immediately.

### 4.4 A Call Is Refused with `HeaderMismatch` (`-32020`)
- **Cause**: an intermediary rewrote or stripped `MCP-Protocol-Version`, `Mcp-Method` or `Mcp-Name`, or a non-conforming client omitted them.
- **Resolution**: make the proxy pass those headers through unchanged. The server will not act on a request whose headers and body disagree.

### 4.5a "I couldn't get an answer from … just now" / a record that is not found reads as an error
Two different things show as one symptom.

- **The service answered "no record".** Its message is shown as it is, with the demo notice. This is correct. If it *reads* as a failure, look at what the service returned (`docker exec … python -` with `MCPClient.call_tool`), not at the model.
- **The service's result was discarded.** `MCP result for <tool> does not match its outputSchema` in the API log: a not-found record is `null` and its `outputSchema` said `object`. The client treats that as a malformed answer and the taxpayer gets "could not be asked". The fix is the schema (`"type": ["object", "null"]`), as done for URSB, BWIMS and the TIN search.

### 4.5 Output Schema Validation Warning
- **Symptom**: Log message `MCP result for <tool> does not match its outputSchema`.
- **Cause**: The server returned fields that violate the declared JSON schema.
- **Impact**: Non-blocking (result is still passed to the LLM), but signals schema drift.
- **Resolution**: Inspect the tool payload against the schema in `ToolSchema.output_schema` and align the response fields.

### 4.6 A Bound Server Is Not Offered in Chat
- **Symptom**: the server is bound and answering, but the picker does not list it (or lists fewer operations than it has). The catalog deliberately hides a server with nothing the caller may use, so the picker cannot say why.
- **Where to look**: `/admin/connectors` → **Onboarding diagnostics**, or `GET /v1/connectors/diagnostics` as staff. Each server has a `status` and each operation a list of `problems`:

  | Server `status` | Meaning |
  |---|---|
  | `ready` | Offered in taxpayer chat. |
  | `feature_off` | Would be offered, but `FLAG_ENTERPRISE_CONNECTORS` is off. |
  | `not_answering` | Bound, not answering (§4.3). |
  | `no_eligible_operations` | Answering, but no operation passes the checks below. |
  | `not_offered_in_production` | A simulator (`MCP_SERVER_MODE_<NAMESPACE>` unset) under `APP_ENV=production`. |

  | Operation problem | Fix on the server |
  |---|---|
  | `no_annotations` | Declare MCP `annotations`. |
  | `not_read_only` | `readOnlyHint: true` — only reads are offered in chat. |
  | `destructive_not_false` | `destructiveHint: false` (MCP defaults a missing hint to destructive). |
  | `no_risk_level` / `unknown_risk_level` | `_meta["ug.go.ura.chatbot/risk"]` of `low`, `medium` or `high`. |
  | `needs_confirmation` | `_meta["ug.go.ura.chatbot/requiresConfirmation"]: false` — a chat lookup cannot confirm. |
  | `no_scopes_for_risk` / `malformed_scopes` | `requiredScopes` (non-empty strings) for a medium or high risk read. |
  | `duplicate_name` | Rename: two bound servers offer an operation with the same name, so neither is offered. |

  An operation with no problems lists who can see it (`offered_to`: `public`, `verified_taxpayer` — the latter only with the scopes it requires). `refused_bindings` names any `MCP_SERVER_URL_*` variable the API refused to bind and the rule it broke (for example "production requires https"); the address itself is never shown. `credential` is `none`, `shared_token`, `shared_token_too_short` (the server treats it as absent) or `oauth_client_credentials`.

---

## 5. Verification Commands

Run the full MCP and connector verification test suites:
```bash
# MCP test suite (wire conformance, authentication, client transport included)
PYTHONPATH=App/backend python3 -m pytest App/backend/tests/test_mcp_*.py -q

# The chat ↔ connector link end to end: real client, real gateway app, real token
PYTHONPATH=App/backend python3 -m pytest tests/agents/test_connector_link_integration.py tests/agents/test_chat_connectors.py -q

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
export MCP_LINK_TOKEN=$(openssl rand -hex 32)   # the shared secret for every link; not stored by the stack
docker compose -f docker-compose.yml -f docker-compose.mcp.yml up -d \
  mcp-tax-calculator mcp-ura-account mcp-efris mcp-dts mcp-payment mcp-tin mcp-ursb mcp-bwims
```

Each container replaces the image's entrypoint with `python -m app.mcp.servers.<name> --http` (the image's own entrypoint starts the chat API and ignores `command`), runs read-only with all capabilities dropped, and keeps its simulator fixtures in memory. Ports are published on loopback only; the API reaches the servers over the compose network. The overlay also binds the API to the six system connectors and sets `FLAG_ENTERPRISE_CONNECTORS=true`.

Health endpoints (unauthenticated, report the server's `auth` mode):
- `mcp-tax-calculator`: `http://localhost:8931/health`
- `mcp-ura-account`: `http://localhost:8932/health`
- `mcp-efris`: `http://localhost:8933/health`
- `mcp-dts`: `http://localhost:8934/health`
- `mcp-payment`: `http://localhost:8935/health`
- `mcp-tin`: `http://localhost:8936/health`
- `mcp-ursb`: `http://localhost:8937/health`
- `mcp-bwims`: `http://localhost:8938/health`

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

---

## 7. Connecting Taxpayer Chat to the Plugin Systems (local stack)

The single-host form: one `connectors` container serves the six system connectors, each at its own MCP endpoint, and the API is bound to them. The systems are **simulators over the fixture databases in `data_store/`**; the picker labels them "Demo data" and only their read operations are offered.

### 7.1 Bring-up
Append the overlay to whichever chain the stack runs (see `docs/runbooks/salt-speech-backends.md` for the GPU chain):
```bash
cd App
export MCP_LINK_TOKEN=$(openssl rand -hex 32)
docker compose -f docker-compose.yml \
  -f docker-compose.local-retrieval.yml -f docker-compose.local-sunflower.yml \
  -f docker-compose.gpu-salt.yml -f docker-compose.connectors.yml up -d
```
`MCP_LINK_TOKEN` is given to both ends by the overlay and is not written anywhere. Keep it exported for later `up` commands in the same chain: recreating either service without it leaves the link locked (§4.3). `down`, `ps` and `logs` work without it.

Tool calling needs a model that can call tools. The local Sunflower vLLM is started with `--enable-auto-tool-choice --tool-call-parser hermes` for this; with `LLM_ENABLED=false` or a backend other than `vllm`/`local` the connectors are listed but an answer cannot draw on them.

### 7.2 Verify
```bash
# 1. Six systems, each authenticating with the shared token
curl -s localhost:8930/health
#    {"ok": true, "name": "mcp_connectors", "servers": {"efris": {"tools": 4, "auth": "token"}, …}}

# 2. The link refuses a caller without the credential
curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:8930/efris \
  -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'   # 401

# 3. The catalog the picker reads: six connectors, mode "simulation", status "available"
curl -s 'localhost:8083/v1/connectors?view=chat'

# 4. A turn that draws on a connector
curl -s localhost:8083/v1/chat -H 'Content-Type: application/json' \
  -d '{"message":"What does EFRIS hold for 1000000001?","connector_namespaces":["efris"]}'
```
In the stream (`POST /v1/chat/stream`) the `agent_trace` event carries `{"type":"tool_call.completed","name":"efris_taxpayer_status","connector":"efris","connector_mode":"simulation","connector_label":"EFRIS","connector_outcome":"found","ok":true,…}`; the chat shows it under the answer as **Looked up in: EFRIS · Demo data · Record found**. `connector_outcome` is one of `found`, `no_record`, `needs_input`, `unavailable` — never the record.

```bash
# 5. Why each bound server is or is not offered (staff session; the console's Onboarding diagnostics)
#    GET /v1/connectors/diagnostics -> {"enabled": true, "servers": [{"namespace": "efris", "status": "ready", …}], "refused_bindings": []}

# 6. A selection that cannot be used does not fail the turn
docker stop ura-app-connectors
curl -s localhost:8083/v1/chat -H 'Content-Type: application/json' \
  -d '{"message":"What does EFRIS hold for 1000000001?","connector_namespaces":["efris"]}' | python3 -m json.tool | grep -A4 connector_notices
#    within LIVENESS_TTL_S (30 s): [{"namespace": "efris", "label": "EFRIS", "reason": "unavailable"}], HTTP 200
docker start ura-app-connectors
```

### 7.3 What happens to a chat turn that has a service selected

A selected service is a statement of intent, and the turn is routed on it. Nothing is left to the model's willingness to call a tool: live, on Sunflower-14B under `tool_choice=auto`, it answered "I would use `efris_taxpayer_status`" and did not, and the keyword templates answered first with an invented "EFRIS Invoice Audit".

| Step | Module | What it does |
|---|---|---|
| 1. Is it a lookup? | `connector_intent.detect_connector_intent` | Deterministic and conservative. The message must **name the service** (or, if none is named, carry a code and use the one word that points at it) and ask for a record, not a procedure. "How do I register for a TIN?" stays a how-to even with EFRIS selected; "How do I check PRN 2200…?" is both and is looked up. An operation whose **required inputs are not in the message** is not chosen: a company *name* goes to `ursb_verify_business`, not to `ursb_compliance_status`, which needs a registration number. |
| 2. Keep the templates out of the way | `service._maybe_handle_fast_paths` gating | A detected lookup skips the deterministic procedure/advisory replies and the passages (`passages=[]`). The reply carries no `sources`, `citations` or portal-form `resources`: they would read as where the record came from. |
| 3. Force the call | `llm.generate_with_tools(forced_operations=…)` | The first round is a **named** function call (guided decoding, so it is well formed). `tool_choice="required"` was measured and is worse: it invents arguments. |
| 4. Check the arguments | `connector_intent.missing_arguments` | A value must be one the taxpayer wrote (this turn or earlier), be the right *kind* (a 10-digit TIN is not a 12-digit PRN that is also in the conversation) and be present. Otherwise nothing is sent and the taxpayer is asked for it. |
| 5. Word the outcome | `connector_outcome.compose` | Each call is classified `found` / `no_record` / `needs_input` / `unavailable` and everything that is not a record is worded from **the service's own message**, never a model paraphrase. A practice-system notice is attached by code. Any turn with a non-record outcome is worded entirely from the services' answers (a model asked to summarise a mix wrote "the business is registered" beside "no registered entity"). |
| 6. Translate | `service._call_llm_agentic(summarise_records=…)` | For Luganda and Swahili the model does not write the record up (it writes claims the record does not make, and citation debris); the fields are rendered and passed through the normal translation step. |

Two environment knobs matter:

- `CONNECTOR_TURN_LOCAL_TOOLS` (default `3`) — how many *local* tools are offered beside the selected service's. The full local registry is ~7,100 tokens of schema against a 4,096-token window; the server answered HTTP 400 and the turn silently fell back to retrieval, as if no service had been chosen. Tool schemas are now fitted to the window **every round** against the real prompt (`tool_budget.fit_tool_specs`), counted as the chat template renders them (spaced JSON — the compact form under-counted four tools by 50 tokens, which is a refused request).
- `SUNFLOWER_MAX_MODEL_LEN` — **recommended `8192`** for a stack that serves connectors. `4096` works because of the fitting above, but leaves one service and one result per turn.

Reading a live turn: `agent_trace` events carry `connector`, `connector_mode`, `connector_label` and `connector_outcome`; the API log line `tool schemas fitted to a context window of N: …` says when tools were slimmed or dropped.

Before any of this, the endpoint resolves the selection (`chat_connectors.resolve_chat_connector_selection`): only services in the caller's current catalog reach the turn, and each one left out is reported with a reason — `unavailable` (not answering), `not_offered` (not available to this account: signed out, consent withdrawn, never bound), `disabled` (`FLAG_ENTERPRISE_CONNECTORS` off). The turn is answered either way. The chat keeps a conversation's chosen services with that conversation (switching away and back, or reloading, restores them), removes one that is `not_offered` or `disabled`, and marks one that is `unavailable`.

### 7.4 What it does not do
- It does not contact URA, NIRA, URSB, a bank or a payment network, and a record it returns is not a URA record.
- It does not write: issuing, paying, ordering, registering and releasing are not offered through chat.
- It is not offered under `APP_ENV=production`. Declaring a connector `live` (`MCP_SERVER_MODE_<NAMESPACE>=live`) is a statement that a reviewed live integration is behind that URL — do not set it for a simulator.

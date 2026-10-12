# System connectors and customer guidance

## Current boundary

The six built-in plugins are **local SQLite simulators**. They are not connected
to URA, NIRA, URSB, BWIMS, EFRIS, a bank, or a payment provider. Their fixture
records, generated TIN-like identifiers, FDNs, PRNs, receipts, and health checks
are not official data or evidence of a live connection. Tool schemas and tool
results carry this simulation status so the assistant can disclose it.

The standalone `plugins/server.py` service is a protected internal REST
dispatcher. Its `/mcp/manifest` and `/mcp/call` routes are **not** an MCP
JSON-RPC transport. Dynamic browser-supplied endpoint registration is disabled.

The simulators can be reached from taxpayer chat as **connectors**, over a real
MCP link (see [Connecting chat to the plugin systems](#connecting-chat-to-the-plugin-systems)).
Three rules keep that honest:

- The `enterprise_connectors` flag is off by default, and it never lists a tool
  that only exists inside the API process. A connector is always a server the
  deployment bound on purpose with `MCP_SERVER_URL_<NAMESPACE>`.
- What is behind a bound server is stated by the deployment, not believed from
  the server: `MCP_SERVER_MODE_<NAMESPACE>` is `live` or `simulation`, and unset
  means `simulation`. A simulator is shown as **demo data** in the picker, in
  the catalog, and in every tool result.
- Production (`APP_ENV=production`) never offers a simulator and the simulators
  themselves serve no tools there. Only a connector an operator declared `live`
  can appear.

URA documents an EFRIS system-to-system API path and provides a UAT developer
portal. The current repository has no authenticated API contract, subscription
credentials, or accreditation evidence, so EFRIS stays simulated until the
integration owner obtains those prerequisites. URA also publishes FY 2026/27
EFRIS handbooks in English, Luganda, and Swahili. See the [EFRIS
service](https://ura.go.ug/en/efris/), [UAT developer
portal](https://developer-uat.ura.go.ug/devportal/), and [handbook
archive](https://ura.go.ug/download-category/efris-handbook/).

Older `plugins/apps/*_app.py` dashboards are development demos as well. They show
a persistent simulation notice, mark responses with simulation headers, retire
their legacy `/mcp/*` routes with `410 Gone`, and return `503` for all non-health
routes in production.

## Included systems

| Connector | Local demonstration only |
| --- | --- |
| EFRIS | Fixture taxpayer profiles, invoice and credit-note examples, stock records, and FDN-shaped references |
| Digital Tax Stamps | Fixture stamp records, sample orders, and local activation state |
| URSB | Fixture business searches and local registration examples |
| BWIMS | Fixture consignments, warehouse inventory, and local clearance examples |
| TIN registration | Fixture taxpayer searches and sample registration records; no NIRA identity verification |
| Payment system | Sample PRNs, status, and checkout records; no bank settlement or URA ledger update |

Mutating plugin tools require a proposal, explicit confirmation, and an
idempotency key at the dispatch boundary. This protects simulated state from an
unreviewed agent call; it does not turn a simulator action into a real filing,
registration, customs release, or payment.

## Customer journeys

The main chat composer offers **Add a connector** beside file and photo
attachments. It loads a browser-safe catalog from `GET /v1/connectors?view=chat`
and lets the taxpayer select approved services for the current conversation.
The chat request carries only selected namespace identifiers; the server
resolves them to deployment-bound MCP tools and checks the caller's role,
consent scopes, and the `enterprise_connectors` flag again before dispatch.
Only operations that explicitly declare `readOnlyHint: true`,
`destructiveHint: false`, URA risk/scope metadata, and no confirmation
requirement are offered through the SSE chat surface. Missing destructive
metadata fails closed because MCP defaults that hint to destructive. Any
local tools offered alongside a selected connector are filtered to the same
read-only boundary. The browser never receives server URLs, credentials, or
tool schemas, and tool arguments are removed from the streamed trace. Remote
tool text is screened for instruction-like content before it re-enters the
agent loop, and the system prompt treats tool output as untrusted evidence.
MCP annotations remain hints rather than enforcement: they are accepted only
for deployment-reviewed servers, while the chat broker and connector server
still enforce authorization. See the [MCP tool annotation
guidance](https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/).

Each catalog entry tells the taxpayer three things before they agree to let a
service be used: what it is (`mode`: `simulation` or `live`), whether it can be
used right now (`status`: `available` or `unavailable`), and what it would let
the assistant do (`operations`: a name, a title and a one-line description —
never a schema). A bound server that does not answer is listed as unavailable
and cannot be selected; one with nothing the caller may use is not listed at
all. The streamed `agent_trace` names the connector a tool call went to and its
mode, so the interface can say which service an answer drew on.

No live taxpayer-account or TIN lookup service is configured in this
repository. When the TIN services connector is bound it searches the
simulator's fixture taxpayers and nothing else, and says so. Do not enter a
National ID number, password, or one-time code in chat.
URA ownership or hosting alone is not an authorization or privacy control: an
account lookup needs a reviewed live API, approved identity verification,
least-privilege access, explicit taxpayer consent, and an audited server-side
operation before it can be declared `live`. Transaction submission remains
outside this read-only chat connector surface and requires an explicit
confirmation-capable flow.

The TIN registration, filing-return, and payment-assistance journeys are guides.
They do not collect NINs or identity documents, generate a live TIN or PRN,
prepare or submit a return, or process a payment. They gather only the
information needed to explain the next step, link to the official URA portal,
and say clearly that no transaction was submitted. Customers should enter
identity, account, and payment details only on the official service.

Invoice review records visible fields and taxpayer-reported amounts. It does
not calculate VAT, query EFRIS, authenticate a fiscal signature, or decide
input-tax eligibility, and it asks whether TIN fields are present instead of
collecting the numbers.

Luganda (`lg`), English (`en`), and Swahili (`sw`) have localized journey names,
curated step and choice labels for the TIN, invoice, payment, return, and BWIMS
guides, localized stepper controls, portal-action labels, resource-card actions,
and the composer privacy reminder. Resource titles and descriptions use the
safety-checked reply localization path. Choice labels are kept separate from the
canonical workflow values the server validates. Reply text uses the existing
safety-checked localization path. Language quality still needs per-locale
evaluation before claiming equal quality; review the stored multilingual
quality reports and `docs/runbooks/guided-journey-probes.md`.

## Connecting chat to the plugin systems

```
taxpayer chat ──▶ API (MCP client) ──MCP 2026-07-28 / Streamable HTTP──▶ connector server ──▶ simulator + SQLite
                  HttpTransport          Authorization: Bearer …           /efris, /payment_system, …
```

Each system is its own MCP server: its own endpoint, tool list and credential.
Two deployments of the same servers are provided:

| Shape | File | Containers |
| --- | --- | --- |
| Single host (the local stack) | `App/docker-compose.connectors.yml` | one `connectors` gateway, `python -m app.mcp.servers.gateway`, an endpoint per system |
| One server per system | `docker-compose.mcp.yml` | `mcp-efris`, `mcp-payment`, `mcp-dts`, `mcp-tin`, `mcp-ursb`, `mcp-bwims` (plus the calculator and account servers) |

Both bind the API with `MCP_SERVER_URL_<NAMESPACE>` and set
`FLAG_ENTERPRISE_CONNECTORS=true`. Bring-up, verification and failure modes are
in `docs/runbooks/enterprise-connectors-and-mcp.md` §7.

What is offered in chat when all six are bound — read operations only:

| Connector | Namespace | Operations offered |
| --- | --- | --- |
| EFRIS | `efris` | `efris_taxpayer_status` |
| Payments | `payment_system` | `payment_view_status`, `payment_verify_advance_tax` |
| Digital Tax Stamps | `digital_tax_stamps` | `dts_verify_stamp`, `dts_taxpayer_status` |
| URSB | `ursb` | `ursb_verify_business`, `ursb_compliance_status` |
| BWIMS | `bwims` | `bwims_consignment_status`, `bwims_warehouse_inventory` |
| TIN services | `tin_registration` | `tin_search_verify` |

The thirteen writing operations (issue an invoice, generate or settle a PRN,
order stamps, register a business, release a consignment, apply for a TIN) are
served by the systems but are not part of the chat surface: they require a
confirmation the read-only link cannot give.

**The link is authenticated.** A connector server reachable on its own address
used to accept any caller and then read the caller's role out of the request
body. Now every HTTP request must present a bearer credential, and identity in
the body (`ug.go.ura.chatbot/userRole` and friends) is honoured only on an
authenticated request — an anonymous caller is always `public`. Two modes:

- **Shared token** (prototype, internal link). One generated secret per system,
  in `MCP_SERVER_TOKEN_<NAMESPACE>` on *both* ends — the same variable name, so
  the two cannot be configured differently. Shorter than 32 characters counts
  as absent.
- **OAuth 2.1** (production). The server is a resource server: it validates an
  RS256 access token's signature, issuer, expiry and **audience** (a token
  minted for another service is refused), publishes RFC 9728 protected-resource
  metadata, and challenges with `WWW-Authenticate: Bearer resource_metadata=…`.
  The API obtains tokens with the client-credentials grant, sending the
  RFC 8707 `resource` parameter so each token is bound to one server.

With neither configured a server is **locked**: it answers `/health` and refuses
everything else. `MCP_SERVER_ALLOW_ANONYMOUS=true` opens it for local
development only, is refused in production, and never grants a role.

## Staff controls and data access

- Staff and administrators use `/admin/connectors` to review local simulator
  health and a separate, policy-filtered catalog of deployment-managed remote
  chat services. The catalog shows operations eligible for the signed-in
  staff account without exposing endpoint URLs, credentials, or tool schemas;
  it does not verify remote reachability. Local **Test connection** checks
  simulator health only. The staff console's **Add connector** opens deployment
  setup guidance; it does not register a server or accept endpoint credentials.
- `POST /v1/connectors/register` always returns `410 Gone`. New connectors
  require a reviewed server implementation and deployment configuration. See
  `docs/runbooks/enterprise-connectors-and-mcp.md` for the operational path.
- `POST /v1/connectors/{name}/configure` also returns `410 Gone`; endpoint,
  credential, and mode changes are not accepted at runtime.
- The connector API requires administrator access; changing connector state
  also requires a staff-writer role.
- The staff console and runtime connector APIs do not accept endpoint URLs or
  credentials. Remote MCP bearer tokens are supplied through deployment-managed
  `MCP_SERVER_TOKEN_<NAMESPACE>` settings and the platform secret store; never
  put them in the browser or repository.
- Mutating actions across connectors adhere to monetary ceilings (`MAX_TRANSACTION_CEILING_UGX = 50,000,000 UGX`)
  and distributed Redis velocity limits (10 mutating calls/hr).
- Multi-step transactional sagas support compensation and rollback via
  `SystemConnector.compensate()`.
- Static reference data and documentation from connectors are exposed as read-only
  MCP resources accessible through `PluginOrchestrator.get_all_resources()` and
  `read_connector_resource()`.
- Raw database record inspection is retired. `/v1/connectors/{name}/records`
  returns `410 Gone`; `/v1/connectors` exposes aggregate simulator health.
- A stamp code missing from the DTS sample fixtures returns `UNKNOWN` with
  `is_authentic: null`; absence from simulator data is not a counterfeit finding.
- The standalone plugin API requires `PLUGINS_API_KEY` and fails closed when it
  is absent. The key is for server-to-server use and must not be sent by the
  browser. Confirmed REST writes use idempotency keys and replay protection.

## Standards and risk controls

The agent confirmation boundary follows the [OWASP GenAI Excessive Agency
recommendations](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/),
including human approval for consequential tool actions. The MCP client and
every server here use the stateless `2026-07-28` protocol: requests carry the
protocol version and routing headers, and there is no `initialize`/`initialized`
handshake or session ID. What that revision requires, and where it is
implemented (`App/backend/app/mcp/servers/wire.py` for servers,
`App/backend/app/mcp/transport.py` for the client):

- `server/discover` on every server (it replaces the removed `ping` as the
  reachability probe), `resultType` on every result, and `ttlMs` with a
  `cacheScope` of `public` or `private` on cacheable ones.
- `MCP-Protocol-Version`, `Mcp-Method` and `Mcp-Name` on every POST; a missing
  or disagreeing header is `400` with `HeaderMismatch` (`-32020`), an
  unsupported version is `400` with `-32022` and the supported list, an unknown
  method is `404` with `-32601`, and an unknown *tool* is `-32602` — so a client
  can tell "no such tool" from "not an MCP server".
- One JSON-RPC request per POST, answered as JSON or as an event stream; the
  client accepts both. GET and DELETE are `405`: there is no session to end.
- `Origin` is validated, so a web page a user has open cannot drive a server
  through their browser. Redirects are never followed, so a bearer token cannot
  be carried to another host.
- `x-mcp-header` tool parameters are mirrored into `Mcp-Param-*` headers with
  the Base64 sentinel encoding; a tool whose annotation is invalid is left out
  of the list rather than called.

See the [official MCP 2026-07-28 release
notes](https://blog.modelcontextprotocol.io/posts/2026-07-28/), the
[changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)
and the [authorization
specification](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization).
Do not label a custom REST manifest as MCP-compatible without implementing and
verifying the versioned JSON-RPC tool-call exchange and required HTTP headers.
Not implemented, deliberately: authorization-server discovery from a server's
response (the token endpoint is configured, never followed from a URL the
server chose), Dynamic Client Registration (deprecated in this revision), and
the Tasks and MCP Apps extensions.
For public notices and interaction controls, use the latest [W3C WCAG 2.2
Recommendation](https://www.w3.org/TR/WCAG22/); WCAG 2.2 was also adopted as
ISO/IEC 40500:2025. The connector picker uses a native modal dialog, keyboard
focus behavior, and pressed-state service choices; this does not claim full
WCAG conformance.

## Verification

```bash
PYTHONPATH=App/backend python3 -m pytest App/backend/tests tests/agents tests/chaos -q
# The connector link, end to end, without a socket:
PYTHONPATH=App/backend python3 -m pytest tests/agents/test_connector_link_integration.py \
  App/backend/tests/test_mcp_wire_conformance.py App/backend/tests/test_mcp_http_client.py -q
cd App/frontend && bun run test && bun run lint
git diff --check
```

## Chat turns that use a chosen service

A taxpayer who adds a service in the chat (the "+" menu → *Add a connector*) is saying
"look things up there". The turn is routed on that by code, not left to the model:

```
message ──► connector_intent        names a service + asks for a record?  (conservative)
              │ yes                  operation chosen by what the message *contains*
              ▼
          forced named call ──► argument guard (written by the taxpayer? right kind? present?)
              │                     │ no → "I need the <what>" (nothing is sent)
              ▼                     ▼
          MCP link (authenticated) ► connector_outcome: found | no_record | needs_input | unavailable
                                      │
                                      ▼   record → model summary (English) / rendered fields (lg, sw)
                                          anything else → the service's own words
                                          + "Demo data: …" attached by code for a practice system
```

Modules: `app/connector_intent.py` (routing and argument guard), `app/connector_outcome.py`
(wording), `app/tool_budget.py` (schema fitting), `app/llm.py` (`generate_with_tools`,
`forced_operations`, `summarise_records`), `app/service.py` (`_with_connector_outcomes`).
The runbook's §7.3 lists the knobs and §4.5a the failure signatures.

Known limits, deliberately not hidden: a message that asks two services two things is
routed per service lexically, and a value that is the right *kind* but meant for the other
service (a TIN typed as URSB's search text) is not caught — ask one service at a time for
the best result. A TIN the taxpayer typed is shown as `[REDACTED_UG_TIN]` when the model
repeats it, because output redaction is global and has no "the user wrote this" exemption.

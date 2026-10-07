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
Production keeps local simulator tools and writes disabled. The
`enterprise_connectors` flag remains off by default and never enables sample
fixtures. When URA has reviewed and deployment-configured an external MCP
server, that flag can expose its eligible read-only operations in taxpayer
chat. Server URLs and credentials stay in backend deployment settings.

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

No live taxpayer-account or TIN lookup service is configured in this
repository. The TIN simulator remains local fixture data and is not offered in
chat. Do not enter a National ID number, password, or one-time code in chat.
URA ownership or hosting alone is not an authorization or privacy control: an
account lookup needs a reviewed live API, approved identity verification,
least-privilege access, explicit taxpayer consent, and an audited server-side
operation before it can appear as available. Transaction submission remains
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
including human approval for consequential tool actions. The MCP client uses
the stateless `2026-07-28` protocol: requests carry the protocol version and
routing headers, and the modern flow has no `initialize`/`initialized`
handshake or session ID. `server/discover` is available for capability
discovery but is not required before a tool call. See the [official MCP
2026-07-28 release notes](https://blog.modelcontextprotocol.io/posts/2026-07-28/)
and [versioning documentation](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/docs/2026-07-28/learn/versioning.mdx).
Do not label a custom REST manifest as MCP-compatible without implementing and
verifying the versioned JSON-RPC tool-call exchange and required HTTP headers.
For public notices and interaction controls, use the latest [W3C WCAG 2.2
Recommendation](https://www.w3.org/TR/WCAG22/); WCAG 2.2 was also adopted as
ISO/IEC 40500:2025. The connector picker uses a native modal dialog, keyboard
focus behavior, and pressed-state service choices; this does not claim full
WCAG conformance.

## Verification

```bash
PYTHONPATH=App/backend python3 -m pytest App/backend/tests tests/agents tests/chaos -q
cd App/frontend && bun run test && bun run lint
git diff --check
```

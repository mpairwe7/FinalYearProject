"use client";

import React, { useCallback, useEffect, useState } from "react";
import StaffGuard from "../../../components/StaffGuard";
import { EmptyState, ErrorState, SkeletonRows } from "../../../components/ops/States";
import { OpsPage, OpsPanel } from "../../../components/ops/OpsPage";
import { ModalDialog } from "../../../components/ModalDialog";
import { authHeaders } from "../../../lib/authSession";
import type { ChatConnector } from "../../../lib/connectors";
import "../admin.css";

interface ConnectorRow {
  id: string;
  name: string;
  description: string;
  connected: boolean;
  healthy: boolean;
  status: string;
  live?: boolean;
  mode?: string;
  tools: string[];
  protocol?: string;
  latency_ms?: number;
}

interface ConnectorList {
  ok: boolean;
  live: boolean;
  mode: string;
  connectors: ConnectorRow[];
}

interface ChatConnectorCatalog {
  ok: true;
  enabled: boolean;
  connectors: ChatConnector[];
}

interface DiagnosticTool {
  name: string;
  title: string;
  problems: string[];
  risk: string;
  required_scopes: string[];
  offered_to: string[];
}

interface DiagnosticServer {
  namespace: string;
  label: string;
  mode: string;
  status: string;
  reachable: boolean;
  credential: string;
  tools_listed: number;
  tools_offered: number;
  tools: DiagnosticTool[];
  server?: { name?: string; version?: string };
}

interface RefusedBinding {
  namespace: string;
  variable: string;
  problem: string;
}

/** `GET /v1/connectors/diagnostics`: why each bound server is, or is not, offered in chat. */
interface ConnectorDiagnostics {
  enabled: boolean;
  environment: string;
  protocol_version: string;
  servers: DiagnosticServer[];
  refused_bindings: RefusedBinding[];
}

/** The gate's reason codes (app/mcp/chat_connectors.py), as an operator reads them. */
const PROBLEM_TEXT: Record<string, string> = {
  no_annotations: "declares no MCP annotations",
  not_read_only: "is not declared read-only (readOnlyHint: true)",
  destructive_not_false: "does not declare destructiveHint: false",
  no_risk_level: "has no ug.go.ura.chatbot/risk level",
  unknown_risk_level: "declares a risk level other than low, medium or high",
  malformed_scopes: "declares malformed requiredScopes",
  needs_confirmation: "requires confirmation, which a chat lookup cannot give",
  no_scopes_for_risk: "is medium or high risk without requiredScopes",
  duplicate_name: "has the same name as an operation on another server",
};

const SERVER_STATUS: Record<string, { label: string; tone: string }> = {
  ready: { label: "Offered in chat", tone: "is-good" },
  feature_off: { label: "Ready — chat connectors are switched off", tone: "is-warn" },
  not_answering: { label: "Not answering", tone: "is-danger" },
  no_eligible_operations: { label: "No operation qualifies for chat", tone: "is-warn" },
  not_offered_in_production: { label: "Simulator — never offered in production", tone: "is-warn" },
};

const CREDENTIAL_TEXT: Record<string, string> = {
  none: "No credential",
  shared_token: "Shared token",
  shared_token_too_short: "Shared token shorter than 32 characters",
  oauth_client_credentials: "OAuth 2.1 client credentials",
};

/** Credential kinds a server will accept; a short shared token reads as none. */
const USABLE_CREDENTIALS = new Set(["shared_token", "oauth_client_credentials"]);

const AUDIENCE_TEXT: Record<string, string> = {
  public: "anonymous taxpayers",
  verified_taxpayer: "signed-in taxpayers",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function parseDiagnostics(body: unknown): ConnectorDiagnostics | null {
  if (!isRecord(body) || body.ok !== true || !Array.isArray(body.servers)) return null;
  const servers: DiagnosticServer[] = body.servers.filter(isRecord).map((server) => ({
    namespace: String(server.namespace ?? ""),
    label: String(server.label ?? server.namespace ?? ""),
    mode: String(server.mode ?? ""),
    status: String(server.status ?? ""),
    reachable: server.reachable === true,
    credential: String(server.credential ?? "none"),
    tools_listed: Number(server.tools_listed ?? 0),
    tools_offered: Number(server.tools_offered ?? 0),
    tools: Array.isArray(server.tools)
      ? server.tools.filter(isRecord).map((tool) => ({
          name: String(tool.name ?? ""),
          title: String(tool.title ?? tool.name ?? ""),
          problems: strings(tool.problems),
          risk: String(tool.risk ?? ""),
          required_scopes: strings(tool.required_scopes),
          offered_to: strings(tool.offered_to),
        }))
      : [],
    server: isRecord(server.server)
      ? { name: String(server.server.name ?? ""), version: String(server.server.version ?? "") }
      : undefined,
  }));
  return {
    enabled: body.enabled === true,
    environment: String(body.environment ?? ""),
    protocol_version: String(body.protocol_version ?? ""),
    servers,
    refused_bindings: Array.isArray(body.refused_bindings)
      ? body.refused_bindings.filter(isRecord).map((item) => ({
          namespace: String(item.namespace ?? ""),
          variable: String(item.variable ?? ""),
          problem: String(item.problem ?? ""),
        }))
      : [],
  };
}

function toolLine(tool: DiagnosticTool): string {
  if (tool.problems.length) {
    return `${tool.title} — ${tool.problems.map((code) => PROBLEM_TEXT[code] ?? code).join("; ")}`;
  }
  if (!tool.offered_to.length) {
    return `${tool.title} — qualifies, but no taxpayer role may use it under the dispatch policy`;
  }
  const scopes = tool.required_scopes.length ? ` who granted ${tool.required_scopes.join(", ")}` : "";
  return `${tool.title} — offered to ${tool.offered_to.map((a) => AUDIENCE_TEXT[a] ?? a).join(" and ")}${scopes}`;
}

/** One step of the guided setup: done, needing attention, or not yet known. */
function StepState({ state }: { state: "done" | "todo" | "unknown" }) {
  const text = state === "done" ? "Done" : state === "todo" ? "Needs attention" : "Not checked";
  const tone = state === "done" ? "is-good" : state === "todo" ? "is-warn" : "";
  return <span className={`ops-chip ${tone}`}>{text}</span>;
}

function isChatConnector(value: unknown): value is ChatConnector {
  return (
    isRecord(value) &&
    typeof value.namespace === "string" &&
    /^[a-z0-9_]{1,64}$/.test(value.namespace) &&
    typeof value.label === "string" &&
    typeof value.description === "string" &&
    Number.isInteger(value.operation_count) &&
    Number(value.operation_count) >= 0 &&
    value.read_only === true
  );
}

export function ConnectorBoard() {
  const [data, setData] = useState<ConnectorList | null>(null);
  const [chatCatalog, setChatCatalog] = useState<ChatConnectorCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [chatCatalogLoading, setChatCatalogLoading] = useState(true);
  const [error, setError] = useState("");
  const [chatCatalogError, setChatCatalogError] = useState("");
  const [diagnostics, setDiagnostics] = useState<ConnectorDiagnostics | null>(null);
  const [diagnosticsError, setDiagnosticsError] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, { ok: boolean; latency_ms: number; message?: string }>>({});
  const [notice, setNotice] = useState("");

  // Dynamic registration is intentionally disabled; this opens the reviewed
  // deployment setup guide instead of collecting endpoint or credential data.
  const [showAddModal, setShowAddModal] = useState(false);

  // Read-only deployment notes for a built-in simulator.
  const [configuringConnector, setConfiguringConnector] = useState<ConnectorRow | null>(null);

  const refreshChatCatalog = useCallback(async () => {
    setChatCatalogLoading(true);
    setChatCatalogError("");
    try {
      const response = await fetch("/api/v1/connectors?view=chat", {
        headers: authHeaders({ Accept: "application/json" }),
        cache: "no-store",
      });
      const body: unknown = await response.json();
      if (
        !response.ok ||
        !isRecord(body) ||
        body.ok !== true ||
        typeof body.enabled !== "boolean" ||
        !Array.isArray(body.connectors)
      ) {
        throw new Error("Reviewed chat integrations are unavailable.");
      }
      setChatCatalog({
        ok: true,
        enabled: body.enabled,
        connectors: body.connectors.filter(isChatConnector),
      });
    } catch (cause) {
      setChatCatalog(null);
      setChatCatalogError(
        cause instanceof Error ? cause.message : "Reviewed chat integrations are unavailable.",
      );
    } finally {
      setChatCatalogLoading(false);
    }
  }, []);

  const refreshDiagnostics = useCallback(async () => {
    setDiagnosticsError("");
    try {
      const response = await fetch("/api/v1/connectors/diagnostics", {
        headers: authHeaders({ Accept: "application/json" }),
        cache: "no-store",
      });
      const parsed = parseDiagnostics(await response.json());
      if (!response.ok || !parsed) throw new Error("Connector diagnostics are unavailable.");
      setDiagnostics(parsed);
    } catch (cause) {
      setDiagnostics(null);
      setDiagnosticsError(cause instanceof Error ? cause.message : "Connector diagnostics are unavailable.");
    }
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    void refreshChatCatalog();
    void refreshDiagnostics();
    try {
      const response = await fetch("/api/v1/connectors", {
        headers: authHeaders({ Accept: "application/json" }),
        cache: "no-store",
      });
      const body = (await response.json()) as ConnectorList;
      if (!response.ok || !body.ok) throw new Error("Connector status is unavailable.");
      setData(body);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Connector status is unavailable.");
    } finally {
      setLoading(false);
    }
  }, [refreshChatCatalog, refreshDiagnostics]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const servers = diagnostics?.servers ?? [];
  const offeredOperations = servers.reduce((sum, server) => sum + server.tools_offered, 0);
  const listedOperations = servers.reduce((sum, server) => sum + server.tools_listed, 0);
  const bindStep: "done" | "todo" | "unknown" = !diagnostics
    ? "unknown"
    : servers.length > 0 && servers.every((server) => server.reachable && USABLE_CREDENTIALS.has(server.credential))
        && diagnostics.refused_bindings.length === 0
      ? "done"
      : "todo";
  const declareStep: "done" | "todo" | "unknown" = !diagnostics || servers.length === 0
    ? "unknown"
    : servers.every((server) => server.tools_offered > 0)
      ? "done"
      : "todo";
  const switchStep: "done" | "todo" | "unknown" = !diagnostics
    ? "unknown"
    : diagnostics.enabled && servers.some((server) => server.status === "ready")
      ? "done"
      : "todo";

  const toggle = async (connector: ConnectorRow) => {
    setBusyId(connector.id);
    setNotice("");
    setError("");
    try {
      const response = await fetch(`/api/v1/connectors/${encodeURIComponent(connector.id)}/toggle`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ enable: !connector.connected }),
      });
      const body = (await response.json()) as { ok?: boolean; detail?: string; error?: string };
      if (!response.ok || !body.ok) {
        throw new Error(body.detail || body.error || "The connector setting was not saved.");
      }
      setNotice(`${connector.name} ${connector.connected ? "disabled" : "enabled"}.`);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The connector setting was not saved.");
    } finally {
      setBusyId(null);
    }
  };

  const testConnection = async (connector: ConnectorRow) => {
    setTestingId(connector.id);
    setNotice("");
    try {
      const res = await fetch(`/api/v1/connectors/${encodeURIComponent(connector.id)}/test`, {
        method: "POST",
        headers: authHeaders({ Accept: "application/json" }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok || !body.ok) {
        setTestResults((prev) => ({
          ...prev,
          [connector.id]: { ok: false, latency_ms: 0, message: body.detail || "Health check failed" },
        }));
      } else {
        setTestResults((prev) => ({
          ...prev,
          [connector.id]: {
            ok: true,
            latency_ms: body.latency_ms || 1.2,
            message: `Local health check passed (${body.latency_ms || 1.2} ms · ${body.tools_count || connector.tools.length} MCP tools)`,
          },
        }));
      }
    } catch (err) {
      setTestResults((prev) => ({
        ...prev,
        [connector.id]: { ok: false, latency_ms: 0, message: (err as Error).message },
      }));
    } finally {
      setTestingId(null);
    }
  };

  const handleOpenConfigure = (conn: ConnectorRow) => {
    setConfiguringConnector(conn);
  };

  const connectors = data?.connectors ?? [];

  return (
    <OpsPage
      eyebrow="Configure"
      title="System connectors"
      description="Review local simulator health and deployment-reviewed services available for taxpayer chat."
      width="read"
      actions={
        <div className="ops-row-inline">
          <button
            className="ops-btn is-primary is-sm"
            type="button"
            onClick={() => setShowAddModal(true)}
          >
            Add connector
          </button>
          <button className="ops-btn is-ghost is-sm" type="button" onClick={() => void refresh()} disabled={loading}>
            Refresh status
          </button>
        </div>
      }
    >
      <div className="ops-note" role="note">
        <span className="ops-note-mark" aria-hidden="true">ⓘ</span>
        <div>
          <p className="ops-note-title">These are local simulations</p>
          <p className="ops-note-body">
            Connector health here does not mean URA, NIRA, a bank, or another external service is connected.
            No live filing or payment is made by these services.
          </p>
        </div>
      </div>

      <OpsPanel id="connectors" title="Built-in connector status" flush bare>
        <p aria-live="polite" className="ops-stat-hint">
          {notice || (loading ? "Refreshing connector status…" : "")}
        </p>
        {loading && connectors.length === 0 ? <SkeletonRows rows={4} height={64} /> : null}
        {error ? <ErrorState body={error} onRetry={() => void refresh()} /> : null}
        {!loading && !error && connectors.length === 0 ? (
          <EmptyState title="No built-in connectors are registered" />
        ) : null}
        {connectors.length > 0 ? (
          <ul className="ops-queue" aria-label="Built-in connectors">
            {connectors.map((connector) => {
              const testResult = testResults[connector.id];
              return (
                <li className="ops-queue-row" key={connector.id}>
                  <div>
                    <div className="ops-row-inline">
                      <strong>{connector.name}</strong>
                      <span className="ops-chip">{connector.protocol || "MCP"}</span>
                      <span className="ops-chip is-warn">Local simulation</span>
                    </div>
                    <p>{connector.description}</p>
                    <p className="ops-stat-hint">
                      {connector.tools.length} guided tools ·{" "}
                      {connector.healthy ? "healthy locally" : "local health check failed"}
                    </p>
                    {testResult ? (
                      <p className={`ops-chip ${testResult.ok ? "is-good" : "is-danger"}`} role="status">
                        {testResult.message}
                      </p>
                    ) : null}
                  </div>
                  <div className="ops-row-inline">
                    <button
                      className="ops-btn is-ghost is-sm"
                      type="button"
                      onClick={() => void testConnection(connector)}
                      disabled={testingId === connector.id}
                      title="Check local connector health and registered tools"
                    >
                      {testingId === connector.id ? "Checking…" : "Test connection"}
                    </button>
                    <button
                      className="ops-btn is-ghost is-sm"
                      type="button"
                      onClick={() => handleOpenConfigure(connector)}
                      title={`View deployment notes for ${connector.name}`}
                    >
                      Details
                    </button>
                    <span className={`ops-chip ${connector.connected ? "is-good" : ""}`}>
                      {connector.connected ? "Enabled" : "Disabled"}
                    </span>
                    <button
                      className="ops-btn is-ghost is-sm"
                      type="button"
                      onClick={() => void toggle(connector)}
                      disabled={busyId !== null || loading}
                      aria-label={`${connector.connected ? "Disable" : "Enable"} ${connector.name}`}
                    >
                      {busyId === connector.id ? "Saving…" : connector.connected ? "Disable" : "Enable"}
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        ) : null}
      </OpsPanel>

      <OpsPanel
        id="chat-integrations"
        title="Taxpayer-chat integrations"
        flush
        bare
        end={(
          <button
            className="ops-btn is-ghost is-sm"
            type="button"
            onClick={() => void refreshChatCatalog()}
            disabled={chatCatalogLoading}
          >
            {chatCatalogLoading ? "Refreshing…" : "Refresh integrations"}
          </button>
        )}
      >
        <p className="ops-panel-note">
          These are deployment-managed remote services, shown separately from local simulations.
          A service is listed when it advertises an approved read-only operation for your role and
          consent scopes. &ldquo;Not responding&rdquo; means it did not answer discovery just now;
          &ldquo;Demo data&rdquo; means it is a practice system, not a live URA record.
        </p>
        <div aria-busy={chatCatalogLoading}>
          {chatCatalogLoading && !chatCatalog ? <SkeletonRows rows={1} height={72} /> : null}
          {chatCatalogError ? (
            <ErrorState body={chatCatalogError} onRetry={() => void refreshChatCatalog()} />
          ) : null}
          {!chatCatalogLoading && !chatCatalogError && chatCatalog && !chatCatalog.enabled ? (
            <EmptyState
              title="Chat integrations are disabled"
              body="Enable the reviewed connector feature in deployment settings before services can be offered in taxpayer chat."
            />
          ) : null}
          {!chatCatalogLoading && !chatCatalogError && chatCatalog?.enabled && chatCatalog.connectors.length === 0 ? (
            <EmptyState
              title="No chat services are available to this account"
              body="No configured remote service currently advertises an approved read-only operation for your role and consent scopes."
            />
          ) : null}
          {chatCatalog?.enabled && chatCatalog.connectors.length > 0 ? (
            <ul className="ops-queue" aria-label="Reviewed taxpayer-chat integrations">
              {chatCatalog.connectors.map((connector) => {
                const answering = connector.status !== "unavailable";
                const operations = connector.operations ?? [];
                return (
                  <li className="ops-queue-row" key={connector.namespace}>
                    <div>
                      <div className="ops-row-inline">
                        <strong>{connector.label}</strong>
                        <span className={`ops-chip ${answering ? "is-good" : "is-danger"}`}>
                          {answering ? "Available for chat" : "Not responding"}
                        </span>
                        {connector.mode ? (
                          <span className={`ops-chip ${connector.mode === "live" ? "is-info" : "is-warn"}`}>
                            {connector.mode === "live" ? "Live" : "Demo data"}
                          </span>
                        ) : null}
                        <span className="ops-chip">Read-only</span>
                      </div>
                      {connector.summary || connector.description ? (
                        <p>{connector.summary || connector.description}</p>
                      ) : null}
                      <p className="ops-stat-hint">
                        {connector.operation_count} approved operation{connector.operation_count === 1 ? "" : "s"}
                        {" · "}{connector.namespace}
                        {connector.server?.name
                          ? ` · ${connector.server.name}${connector.server.version ? ` ${connector.server.version}` : ""}`
                          : ""}
                      </p>
                      {operations.length > 0 ? (
                        <p className="ops-stat-hint">
                          {operations.map((operation) => operation.title).join(" · ")}
                        </p>
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : null}
        </div>
      </OpsPanel>

      <OpsPanel
        id="connector-diagnostics"
        title="Onboarding diagnostics"
        flush
        bare
        end={(
          <button className="ops-btn is-ghost is-sm" type="button" onClick={() => void refreshDiagnostics()}>
            Re-check servers
          </button>
        )}
      >
        <p className="ops-panel-note">
          Why each server bound to this deployment is, or is not, offered in taxpayer chat — from the same checks
          the chat runs. Addresses and credentials are never shown here, only how each link authenticates.
        </p>
        {diagnosticsError ? <ErrorState body={diagnosticsError} onRetry={() => void refreshDiagnostics()} /> : null}
        {diagnostics ? (
          <>
            <p className="ops-stat-hint" data-testid="connector-diagnostics-summary">
              Chat connectors are {diagnostics.enabled ? "switched on" : "switched off"} (FLAG_ENTERPRISE_CONNECTORS)
              {" · "}{diagnostics.environment || "unknown"} environment
              {diagnostics.protocol_version ? ` · MCP ${diagnostics.protocol_version}` : ""}
              {" · "}{offeredOperations} of {listedOperations} operations qualify for chat
            </p>
            {servers.length === 0 && diagnostics.refused_bindings.length === 0 ? (
              <EmptyState
                title="No MCP server is bound to this deployment"
                body="Bind a reviewed server with MCP_SERVER_URL_<NAMESPACE> and its credential in the deployment settings."
              />
            ) : null}
            {servers.length > 0 ? (
              <ul className="ops-queue" aria-label="Bound MCP servers">
                {servers.map((server) => {
                  const state = SERVER_STATUS[server.status] ?? { label: server.status, tone: "" };
                  return (
                    <li className="ops-queue-row" key={server.namespace}>
                      <div>
                        <div className="ops-row-inline">
                          <strong>{server.label}</strong>
                          <span className={`ops-chip ${state.tone}`}>{state.label}</span>
                          <span className={`ops-chip ${server.mode === "live" ? "is-info" : "is-warn"}`}>
                            {server.mode === "live" ? "Live" : "Demo data"}
                          </span>
                          <span className={`ops-chip ${USABLE_CREDENTIALS.has(server.credential) ? "" : "is-danger"}`}>
                            {CREDENTIAL_TEXT[server.credential] ?? server.credential}
                          </span>
                        </div>
                        <p className="ops-stat-hint">
                          {server.tools_offered} of {server.tools_listed} operation{server.tools_listed === 1 ? "" : "s"} offered
                          {" · "}{server.namespace}
                          {server.server?.name ? ` · ${server.server.name}${server.server.version ? ` ${server.server.version}` : ""}` : ""}
                        </p>
                        {server.tools.length > 0 ? (
                          <details className="connector-diagnostic-tools">
                            <summary>Operations ({server.tools.length})</summary>
                            <ul>
                              {server.tools.map((tool) => (
                                <li key={tool.name} className={tool.offered_to.length ? "is-offered" : "is-excluded"}>
                                  <span aria-hidden="true">{tool.offered_to.length ? "✓" : "✗"}</span>{" "}
                                  {toolLine(tool)}
                                </li>
                              ))}
                            </ul>
                          </details>
                        ) : null}
                      </div>
                    </li>
                  );
                })}
              </ul>
            ) : null}
            {diagnostics.refused_bindings.length > 0 ? (
              <ul className="ops-queue" aria-label="Settings that did not bind a server">
                {diagnostics.refused_bindings.map((item) => (
                  <li className="ops-queue-row" key={item.variable}>
                    <div>
                      <div className="ops-row-inline">
                        <strong>{item.variable}</strong>
                        <span className="ops-chip is-danger">Not bound</span>
                      </div>
                      <p className="ops-stat-hint">{item.problem}</p>
                    </div>
                  </li>
                ))}
              </ul>
            ) : null}
          </>
        ) : !diagnosticsError ? (
          <SkeletonRows rows={1} height={72} />
        ) : null}
      </OpsPanel>

      {/* Add Connector setup guidance: runtime registration is disabled. */}
      {showAddModal && (
        <ModalDialog
          labelledBy="add-connector-title"
          className="connector-dialog"
          onClose={() => setShowAddModal(false)}
        >
          <header className="connector-dialog-header">
            <div>
              <span className="ops-eyebrow">Security-reviewed setup</span>
              <h2 id="add-connector-title">Add a connector</h2>
            </div>
            <button
              type="button"
              className="connector-dialog-close"
              onClick={() => setShowAddModal(false)}
              aria-label="Close connector setup"
            >
              ×
            </button>
          </header>

          <div className="connector-dialog-body">
            <div className="connector-setup-note" role="note">
              <strong>Registration from this screen is disabled.</strong>
              <p>
                New connector servers must be reviewed and supplied through deployment configuration before they can appear here.
              </p>
            </div>

            <h3>Setup steps</h3>
            <p className="connector-deployment-copy">
              Each step is checked against this deployment as it is now; nothing you do here changes it.
            </p>
            <ol className="connector-setup-steps">
              <li>
                <strong>Implement and review the server.</strong> An MCP {diagnostics?.protocol_version || "2026-07-28"} server
                with its protocol, permissions and network access reviewed.
              </li>
              <li>
                <strong>Bind it to the deployment.</strong> Set <code>MCP_SERVER_URL_&lt;NAMESPACE&gt;</code> and a credential —{" "}
                <code>MCP_SERVER_TOKEN_&lt;NAMESPACE&gt;</code> on both ends, or OAuth 2.1 client credentials — in the
                deployment secret store. Do not paste secrets into this console. <StepState state={bindStep} />
                {diagnostics ? (
                  <span className="connector-step-detail">
                    {servers.length} server{servers.length === 1 ? "" : "s"} bound
                    {servers.some((server) => !server.reachable)
                      ? `, ${servers.filter((server) => !server.reachable).length} not answering`
                      : ""}
                    {servers.some((server) => !USABLE_CREDENTIALS.has(server.credential))
                      ? ", some without a usable credential"
                      : ""}
                    {diagnostics.refused_bindings.length
                      ? `, ${diagnostics.refused_bindings.length} setting${diagnostics.refused_bindings.length === 1 ? "" : "s"} refused`
                      : ""}
                    .
                  </span>
                ) : null}
              </li>
              <li>
                <strong>Declare what each operation may do.</strong> Read operations need{" "}
                <code>readOnlyHint: true</code>, <code>destructiveHint: false</code>, a{" "}
                <code>ug.go.ura.chatbot/risk</code> level, <code>requiresConfirmation: false</code>, and{" "}
                <code>requiredScopes</code> when the risk is medium or high. <StepState state={declareStep} />
                {diagnostics && servers.length ? (
                  <span className="connector-step-detail">
                    {offeredOperations} of {listedOperations} operations qualify — see Onboarding diagnostics for each one.
                  </span>
                ) : null}
              </li>
              <li>
                <strong>Switch it on and verify.</strong> Set <code>FLAG_ENTERPRISE_CONNECTORS=true</code>; set{" "}
                <code>MCP_SERVER_MODE_&lt;NAMESPACE&gt;=live</code> only for a reviewed live service. Then open the
                taxpayer chat and add it from the + menu. <StepState state={switchStep} />
              </li>
            </ol>

            <p className="connector-runbook-reference">
              Operational guide: <code>docs/runbooks/enterprise-connectors-and-mcp.md</code>
            </p>
          </div>

          <footer className="connector-dialog-footer">
            <button
              type="button"
              className="ops-btn is-primary"
              onClick={() => setShowAddModal(false)}
            >
              Close
            </button>
          </footer>
        </ModalDialog>
      )}

      {/* Read-only deployment notes for the selected local simulator. */}
      {configuringConnector && (
        <ModalDialog
          labelledBy="config-connector-title"
          className="connector-dialog"
          onClose={() => setConfiguringConnector(null)}
        >
          <header className="connector-dialog-header">
            <div>
              <span className="ops-eyebrow">Deployment notes</span>
              <h2 id="config-connector-title">{configuringConnector.name}</h2>
            </div>
            <button
              type="button"
              className="connector-dialog-close"
              onClick={() => setConfiguringConnector(null)}
              aria-label="Close deployment notes"
            >
              ×
            </button>
          </header>

          <div className="connector-dialog-body">
            <div className="connector-setup-note" role="note">
              <strong>This connector is a local simulator.</strong>
              <p>
                Its health check reports the local service and registered tools. It does not confirm a live URA, NIRA, bank, or payment connection.
              </p>
            </div>

            <h3>Changing the deployment</h3>
            <p className="connector-deployment-copy">
              Endpoint routing and credentials are managed through a reviewed server configuration. Changes in this console cannot switch this simulator to a live government service.
            </p>
            <p className="connector-runbook-reference">
              Operational guide: <code>docs/runbooks/enterprise-connectors-and-mcp.md</code>
            </p>
          </div>

          <footer className="connector-dialog-footer">
            <button
              type="button"
              className="ops-btn is-primary"
              onClick={() => setConfiguringConnector(null)}
            >
              Close
            </button>
          </footer>
        </ModalDialog>
      )}
    </OpsPage>
  );
}

export default function ConnectorsAdminPage() {
  return (
    <StaffGuard current="/admin/connectors" requireRoles={["ura_staff", "ura_admin"]}>
      {() => <ConnectorBoard />}
    </StaffGuard>
  );
}

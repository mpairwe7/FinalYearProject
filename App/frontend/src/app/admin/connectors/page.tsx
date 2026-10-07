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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
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

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    void refreshChatCatalog();
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
  }, [refreshChatCatalog]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

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
        <div className="flex items-center gap-2">
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
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <strong>{connector.name}</strong>
                      <span className="text-[10px] font-mono uppercase px-1.5 py-0.5 rounded bg-neutral-800 text-neutral-400 border border-neutral-700">
                        {connector.protocol || "MCP"}
                      </span>
                      <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-400 border border-amber-500/20">
                        Local simulation
                      </span>
                    </div>
                    <p>{connector.description}</p>
                    <p className="ops-stat-hint">
                      {connector.tools.length} guided tools ·{" "}
                      {connector.healthy ? "healthy locally" : "local health check failed"}
                      {testResult && (
                        <span className={`ml-2 font-mono font-medium ${testResult.ok ? "text-emerald-400" : "text-rose-400"}`}>
                          · {testResult.message}
                        </span>
                      )}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
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
          Availability reflects your current role and consent scopes; it does not confirm live service health.
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
              {chatCatalog.connectors.map((connector) => (
                <li className="ops-queue-row" key={connector.namespace}>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <strong>{connector.label}</strong>
                      <span className="ops-chip is-good">Available for chat</span>
                      <span className="ops-chip">Read-only</span>
                    </div>
                    {connector.description ? <p>{connector.description}</p> : null}
                    <p className="ops-stat-hint">
                      {connector.operation_count} approved operation{connector.operation_count === 1 ? "" : "s"}
                      {" · "}{connector.namespace}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
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
            <ol className="connector-setup-steps">
              <li>Implement and review the connector server, protocol, permissions, and network access.</li>
              <li>Configure its endpoint and credentials in the deployment secret store. Do not paste secrets into this console.</li>
              <li>Deploy the reviewed change, then verify it with the connector health checks.</li>
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

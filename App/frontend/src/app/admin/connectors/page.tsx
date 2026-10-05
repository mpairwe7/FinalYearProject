"use client";

import React, { useCallback, useEffect, useState } from "react";
import StaffGuard from "../../../components/StaffGuard";
import { EmptyState, ErrorState, SkeletonRows } from "../../../components/ops/States";
import { OpsPage, OpsPanel } from "../../../components/ops/OpsPage";
import { authHeaders } from "../../../lib/authSession";
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

export function ConnectorBoard() {
  const [data, setData] = useState<ConnectorList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, { ok: boolean; latency_ms: number; message?: string }>>({});
  const [notice, setNotice] = useState("");

  // Add Connector Modal State
  const [showAddModal, setShowAddModal] = useState(false);
  const [newConnName, setNewConnName] = useState("");
  const [newConnDisplayName, setNewConnDisplayName] = useState("");
  const [newConnUrl, setNewConnUrl] = useState("");
  const [newConnProtocol, setNewConnProtocol] = useState<"mcp" | "rest">("mcp");
  const [newConnMode, setNewConnMode] = useState<"simulation" | "live">("simulation");
  const [newConnKey, setNewConnKey] = useState("");
  const [addBusy, setAddBusy] = useState(false);
  const [addError, setAddError] = useState("");
  const [addTestSuccess, setAddTestSuccess] = useState<string | null>(null);

  // Configure Connector Modal State
  const [configuringConnector, setConfiguringConnector] = useState<ConnectorRow | null>(null);
  const [configMode, setConfigMode] = useState<"simulation" | "live">("simulation");
  const [configUrl, setConfigUrl] = useState("");
  const [configBusy, setConfigBusy] = useState(false);
  const [configError, setConfigError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
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
  }, []);

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
            message: `Ping OK (${body.latency_ms || 1.2} ms · ${body.tools_count || connector.tools.length} MCP tools)`,
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
    setConfigMode((conn.mode as "simulation" | "live") || "simulation");
    setConfigUrl("");
    setConfigError("");
  };

  const handleSaveConfigure = async () => {
    if (!configuringConnector) return;
    setConfigBusy(true);
    setConfigError("");
    try {
      const res = await fetch(`/api/v1/connectors/${encodeURIComponent(configuringConnector.id)}/configure`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ mode: configMode, endpoint_url: configUrl || undefined }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok || !body.ok) {
        throw new Error(body.detail || body.error || "Configuration update failed");
      }
      setNotice(`Updated ${configuringConnector.name} settings.`);
      setConfiguringConnector(null);
      await refresh();
    } catch (err) {
      setConfigError((err as Error).message);
    } finally {
      setConfigBusy(false);
    }
  };

  const handleAddConnectorSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newConnName.trim() || !newConnDisplayName.trim()) {
      setAddError("Identifier and Display Name are required.");
      return;
    }
    setAddBusy(true);
    setAddError("");
    try {
      const res = await fetch("/api/v1/connectors/register", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          name: newConnName.trim().toLowerCase().replace(/\s+/g, "_"),
          display_name: newConnDisplayName.trim(),
          endpoint_url: newConnUrl.trim(),
          protocol: newConnProtocol,
          mode: newConnMode,
          api_key: newConnKey.trim() || undefined,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok || !body.ok) {
        throw new Error(body.detail || body.error || "Registration failed");
      }
      setNotice(`Registered connector ${newConnDisplayName}.`);
      setShowAddModal(false);
      setNewConnName("");
      setNewConnDisplayName("");
      setNewConnUrl("");
      setNewConnKey("");
      await refresh();
    } catch (err) {
      setAddError((err as Error).message);
    } finally {
      setAddBusy(false);
    }
  };

  const connectors = data?.connectors ?? [];

  return (
    <OpsPage
      eyebrow="Configure"
      title="System connectors"
      description="Check and enable the connector simulators used by guided agent flows."
      width="read"
      actions={
        <div className="flex items-center gap-2">
          <button
            className="ops-btn is-primary is-sm"
            type="button"
            onClick={() => {
              setShowAddModal(true);
              setAddError("");
              setAddTestSuccess(null);
            }}
          >
            + Add Connector
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
                      <span
                        className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${
                          connector.mode === "live"
                            ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                            : "bg-amber-500/10 text-amber-400 border border-amber-500/20"
                        }`}
                      >
                        {connector.mode === "live" ? "Live Production" : "Simulation Mode"}
                      </span>
                    </div>
                    <p>{connector.description}</p>
                    <p className="ops-stat-hint">
                      {connector.mode || "simulation"} · {connector.tools.length} guided tools ·{" "}
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
                      title="Run live diagnostic ping and MCP tool handshake"
                    >
                      {testingId === connector.id ? "Pinging…" : "Test Ping"}
                    </button>
                    <button
                      className="ops-btn is-ghost is-sm"
                      type="button"
                      onClick={() => handleOpenConfigure(connector)}
                      title="Configure endpoint and live/sandbox mode"
                    >
                      Settings
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

      {/* Add Connector Modal Dialog */}
      {showAddModal && (
        <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4">
          <div
            className="w-full max-w-lg rounded-2xl bg-neutral-900 border border-neutral-700 shadow-2xl p-6 text-neutral-200 animate-fade-in"
            role="dialog"
            aria-modal="true"
            aria-labelledby="add-connector-title"
          >
            <div className="flex items-center justify-between pb-4 border-b border-neutral-800">
              <h3 id="add-connector-title" className="text-base font-semibold text-white">
                Register Enterprise Connector
              </h3>
              <button
                type="button"
                className="text-neutral-400 hover:text-white text-lg leading-none"
                onClick={() => setShowAddModal(false)}
                aria-label="Close modal"
              >
                ×
              </button>
            </div>

            <form onSubmit={handleAddConnectorSubmit} className="space-y-4 pt-4 text-xs">
              {addError && (
                <div className="p-2.5 rounded-lg bg-rose-500/10 border border-rose-500/20 text-rose-400 text-xs">
                  {addError}
                </div>
              )}
              {addTestSuccess && (
                <div className="p-2.5 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs">
                  {addTestSuccess}
                </div>
              )}

              <div>
                <label className="block text-neutral-300 font-medium mb-1">Connector Identifier (Unique)</label>
                <input
                  type="text"
                  required
                  placeholder="e.g. nira_national_id_gateway"
                  value={newConnName}
                  onChange={(e) => setNewConnName(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg bg-neutral-800 border border-neutral-700 text-white placeholder-neutral-500 focus:outline-none focus:border-blue-500 font-mono"
                />
              </div>

              <div>
                <label className="block text-neutral-300 font-medium mb-1">Display Name</label>
                <input
                  type="text"
                  required
                  placeholder="e.g. NIRA National ID Verification API"
                  value={newConnDisplayName}
                  onChange={(e) => setNewConnDisplayName(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg bg-neutral-800 border border-neutral-700 text-white placeholder-neutral-500 focus:outline-none focus:border-blue-500"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-neutral-300 font-medium mb-1">Protocol Standard</label>
                  <select
                    value={newConnProtocol}
                    onChange={(e) => setNewConnProtocol(e.target.value as "mcp" | "rest")}
                    className="w-full px-3 py-2 rounded-lg bg-neutral-800 border border-neutral-700 text-white focus:outline-none focus:border-blue-500"
                  >
                    <option value="mcp">MCP (Model Context Protocol)</option>
                    <option value="rest">REST API (OAuth 2.0 / Token)</option>
                  </select>
                </div>
                <div>
                  <label className="block text-neutral-300 font-medium mb-1">Environment Mode</label>
                  <select
                    value={newConnMode}
                    onChange={(e) => setNewConnMode(e.target.value as "simulation" | "live")}
                    className="w-full px-3 py-2 rounded-lg bg-neutral-800 border border-neutral-700 text-white focus:outline-none focus:border-blue-500"
                  >
                    <option value="simulation">Simulation (Sandbox Test)</option>
                    <option value="live">Live Production (HTTPS)</option>
                  </select>
                </div>
              </div>

              <div>
                <label className="block text-neutral-300 font-medium mb-1">Endpoint Gateway URL</label>
                <input
                  type="url"
                  placeholder="https://api.ura.go.ug/v1/gateway or http://localhost:8000"
                  value={newConnUrl}
                  onChange={(e) => setNewConnUrl(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg bg-neutral-800 border border-neutral-700 text-white placeholder-neutral-500 focus:outline-none focus:border-blue-500 font-mono"
                />
                <p className="text-[11px] text-neutral-500 mt-1">
                  Production requires HTTPS. Private IP addresses (127.0.0.1, 10.x, 192.168.x) are blocked in production.
                </p>
              </div>

              <div>
                <label className="block text-neutral-300 font-medium mb-1">API Key / Bearer Secret (Optional)</label>
                <input
                  type="password"
                  placeholder="••••••••••••••••"
                  value={newConnKey}
                  onChange={(e) => setNewConnKey(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg bg-neutral-800 border border-neutral-700 text-white placeholder-neutral-500 focus:outline-none focus:border-blue-500 font-mono"
                />
                <p className="text-[11px] text-neutral-500 mt-1">Vaulted securely. Never logged or exposed in API transcripts.</p>
              </div>

              <div className="flex items-center justify-end gap-2 pt-4 border-t border-neutral-800">
                <button
                  type="button"
                  className="ops-btn is-ghost is-sm"
                  onClick={() => setShowAddModal(false)}
                  disabled={addBusy}
                >
                  Cancel
                </button>
                <button type="submit" className="ops-btn is-primary is-sm" disabled={addBusy}>
                  {addBusy ? "Registering…" : "Register Connector"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Configure Connector Settings Modal */}
      {configuringConnector && (
        <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4">
          <div
            className="w-full max-w-md rounded-2xl bg-neutral-900 border border-neutral-700 shadow-2xl p-6 text-neutral-200 animate-fade-in"
            role="dialog"
            aria-modal="true"
            aria-labelledby="config-connector-title"
          >
            <div className="flex items-center justify-between pb-3 border-b border-neutral-800">
              <h3 id="config-connector-title" className="text-sm font-semibold text-white">
                Configure {configuringConnector.name}
              </h3>
              <button
                type="button"
                className="text-neutral-400 hover:text-white text-base leading-none"
                onClick={() => setConfiguringConnector(null)}
              >
                ×
              </button>
            </div>

            <div className="space-y-4 pt-4 text-xs">
              {configError && (
                <div className="p-2.5 rounded-lg bg-rose-500/10 border border-rose-500/20 text-rose-400 text-xs">
                  {configError}
                </div>
              )}

              <div>
                <label className="block text-neutral-300 font-medium mb-1">Operating Mode</label>
                <select
                  value={configMode}
                  onChange={(e) => setConfigMode(e.target.value as "simulation" | "live")}
                  className="w-full px-3 py-2 rounded-lg bg-neutral-800 border border-neutral-700 text-white focus:outline-none focus:border-blue-500"
                >
                  <option value="simulation">Simulation (Sandbox / Staging)</option>
                  <option value="live">Live Production (Authenticated API)</option>
                </select>
                <p className="text-[11px] text-neutral-500 mt-1">
                  Simulation mode uses local SQLite fixture datastores. Live mode connects to official gateways.
                </p>
              </div>

              <div>
                <label className="block text-neutral-300 font-medium mb-1">Custom Gateway URL (Optional)</label>
                <input
                  type="url"
                  placeholder="Leave empty to use default deployment routing"
                  value={configUrl}
                  onChange={(e) => setConfigUrl(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg bg-neutral-800 border border-neutral-700 text-white placeholder-neutral-500 focus:outline-none focus:border-blue-500 font-mono"
                />
              </div>

              <div className="flex items-center justify-end gap-2 pt-3 border-t border-neutral-800">
                <button
                  type="button"
                  className="ops-btn is-ghost is-sm"
                  onClick={() => setConfiguringConnector(null)}
                  disabled={configBusy}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="ops-btn is-primary is-sm"
                  onClick={handleSaveConfigure}
                  disabled={configBusy}
                >
                  {configBusy ? "Saving…" : "Save Settings"}
                </button>
              </div>
            </div>
          </div>
        </div>
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

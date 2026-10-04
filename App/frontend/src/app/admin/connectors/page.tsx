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
  const [notice, setNotice] = useState("");

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

  const connectors = data?.connectors ?? [];

  return (
    <OpsPage
      eyebrow="Configure"
      title="System connectors"
      description="Check and enable the connector simulators used by guided agent flows."
      width="read"
      actions={
        <button className="ops-btn is-ghost is-sm" type="button" onClick={() => void refresh()} disabled={loading}>
          Refresh status
        </button>
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
            {connectors.map((connector) => (
              <li className="ops-queue-row" key={connector.id}>
                <div className="min-w-0 flex-1">
                  <strong>{connector.name}</strong>
                  <p>{connector.description}</p>
                  <p className="ops-stat-hint">
                    {connector.mode || "simulation"} · {connector.tools.length} guided tools · {connector.healthy ? "healthy locally" : "local health check failed"}
                  </p>
                </div>
                <div className="flex items-center gap-2">
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
            ))}
          </ul>
        ) : null}
      </OpsPanel>
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

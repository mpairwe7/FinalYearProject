"use client";

/**
 * Enterprise Notification Outbox & Live Dispatcher (G14).
 *
 * Supports live multi-channel delivery:
 * - Email: Resend / AWS SES / SMTP with TLS
 * - SMS: Africa's Talking / Twilio
 * - Webhook: Signed HMAC-SHA256 delivery
 * - In-App: Real-time taxpayer reminder inbox & websocket stream
 */
import React, { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import StaffGuard from "../../../components/StaffGuard";
import { OpsPage, OpsPanel, TableScroll } from "../../../components/ops/OpsPage";
import { EmptyState, ErrorState, SkeletonRows } from "../../../components/ops/States";
import { analyticsApi, OutboxItem } from "../../../services/analyticsApi";
import { queryKeys } from "../../../lib/queryKeys";
import "../admin.css";

const FAILED = new Set(["failed", "error", "bounced"]);

function statusTone(status: string): string {
  const value = status.toLowerCase();
  if (FAILED.has(value)) return "is-danger";
  if (value === "sent" || value === "delivered") return "is-good";
  if (value === "queued") return "is-caution";
  return "";
}

function channelBadge(channel: string): string {
  switch (channel.toLowerCase()) {
    case "email":
      return "📧 Email";
    case "sms":
      return "📱 SMS";
    case "webhook":
      return "🌐 Webhook";
    case "in_app":
      return "🔔 In-App";
    default:
      return channel;
  }
}

export function OutboxBoard() {
  const queryClient = useQueryClient();
  const [filterStatus, setFilterStatus] = useState<string>("");
  const [showTestDialog, setShowTestDialog] = useState(false);
  const [testChannel, setTestChannel] = useState("email");
  const [testRecipient, setTestRecipient] = useState("taxpayer@ura.go.ug");
  const [testSubject, setTestSubject] = useState("VAT Filing Reminder");
  const [testMessage, setTestMessage] = useState("Your monthly VAT return is due soon. File on eTax to avoid penalties.");
  const [actionNotice, setActionNotice] = useState<string>("");

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: queryKeys.admin.outbox(),
    queryFn: () => analyticsApi.outbox(),
    staleTime: 5_000,
    refetchInterval: 10_000,
  });

  const dispatchMutation = useMutation({
    mutationFn: () => analyticsApi.dispatchOutbox(),
    onSuccess: (res) => {
      setActionNotice(`Dispatched ${res.processed} notifications (${res.sent} sent, ${res.failed} failed).`);
      void queryClient.invalidateQueries({ queryKey: queryKeys.admin.outbox() });
    },
    onError: (err) => {
      setActionNotice(err instanceof Error ? err.message : "Dispatch failed.");
    },
  });

  const retryMutation = useMutation({
    mutationFn: (id: string) => analyticsApi.retryOutbox(id),
    onSuccess: () => {
      setActionNotice("Notification retried successfully.");
      void queryClient.invalidateQueries({ queryKey: queryKeys.admin.outbox() });
    },
    onError: (err) => {
      setActionNotice(err instanceof Error ? err.message : "Retry failed.");
    },
  });

  const testMutation = useMutation({
    mutationFn: (payload: { channel: string; recipient: string; message: string; subject: string }) =>
      analyticsApi.testNotification(payload),
    onSuccess: (res) => {
      setActionNotice(`Test sent via ${res.provider} (status: ${res.status}). Ref: ${res.id.slice(0, 8)}`);
      setShowTestDialog(false);
      void queryClient.invalidateQueries({ queryKey: queryKeys.admin.outbox() });
    },
    onError: (err) => {
      setActionNotice(err instanceof Error ? err.message : "Test dispatch failed.");
    },
  });

  const rawItems = useMemo(() => data?.items ?? [], [data]);
  const items = useMemo(() => {
    if (!filterStatus) return rawItems;
    return rawItems.filter((i) => i.status.toLowerCase() === filterStatus.toLowerCase());
  }, [rawItems, filterStatus]);

  const stats = useMemo(() => {
    return (
      data?.stats ?? {
        total: rawItems.length,
        queued: rawItems.filter((i) => i.status === "queued").length,
        sent: rawItems.filter((i) => i.status === "sent").length,
        delivered: rawItems.filter((i) => i.status === "delivered").length,
        failed: rawItems.filter((i) => FAILED.has(i.status)).length,
      }
    );
  }, [data, rawItems]);

  const isLiveMode = data?.live ?? false;
  const providers = data?.providers ?? {};

  return (
    <OpsPage
      eyebrow="Configure"
      title="Notification Outbox & Delivery"
      description="Multi-channel notification dispatcher for taxpayer filing reminders, escalation updates, and live alerts."
      width="read"
      toolbar={
        <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
          <button
            type="button"
            className="ops-btn ops-btn-primary"
            disabled={dispatchMutation.isPending}
            onClick={() => dispatchMutation.mutate()}
          >
            {dispatchMutation.isPending ? "Dispatching..." : "⚡ Dispatch Outbox"}
          </button>
          <button
            type="button"
            className="ops-btn"
            onClick={() => setShowTestDialog(!showTestDialog)}
          >
            ✉️ Send Live Test
          </button>
        </div>
      }
    >
      {/* Live Provider Status Banner */}
      <div
        className="ops-panel"
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "12px",
          padding: "14px 16px",
          marginBottom: "16px",
          backgroundColor: isLiveMode ? "rgba(16, 185, 129, 0.08)" : "rgba(245, 158, 11, 0.08)",
          borderColor: isLiveMode ? "rgba(16, 185, 129, 0.25)" : "rgba(245, 158, 11, 0.25)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <span
            style={{
              display: "inline-block",
              width: "10px",
              height: "10px",
              borderRadius: "50%",
              backgroundColor: isLiveMode ? "#10B981" : "#F59E0B",
            }}
          />
          <div>
            <strong style={{ fontSize: "14px" }}>
              {isLiveMode ? "Live Notification Engine Active" : "Simulation Mode (Local Sandbox)"}
            </strong>
            <p style={{ margin: "2px 0 0", fontSize: "12px", color: "var(--text-muted)" }}>
              {isLiveMode
                ? "Active providers connect directly to live email, SMS, and webhook delivery gateways."
                : "Messages are recorded with simulated delivery verification. Connect real gateways for live dispatch."}
            </p>
          </div>
        </div>

        <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
          {Object.entries(providers).map(([ch, info]) => (
            <span
              key={ch}
              className={`ops-chip ${info.configured ? "is-good" : ""}`}
              style={{ fontSize: "11px" }}
            >
              {ch.toUpperCase()}: {info.backend || (info.configured ? "ready" : "inactive")}
            </span>
          ))}
        </div>
      </div>

      {actionNotice ? (
        <div
          className="ops-note"
          role="status"
          style={{ marginBottom: "16px", display: "flex", justifyContent: "space-between", alignItems: "center" }}
        >
          <span>{actionNotice}</span>
          <button
            type="button"
            className="ops-btn ops-btn-sm"
            onClick={() => setActionNotice("")}
            aria-label="Dismiss notice"
          >
            ✕
          </button>
        </div>
      ) : null}

      {/* Test Notification Panel */}
      {showTestDialog ? (
        <OpsPanel id="test-notification-panel" title="Send Live Test Notification">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              testMutation.mutate({
                channel: testChannel,
                recipient: testRecipient,
                subject: testSubject,
                message: testMessage,
              });
            }}
            style={{ display: "flex", flexDirection: "column", gap: "12px", maxWidth: "600px" }}
          >
            <div style={{ display: "flex", gap: "12px", flexWrap: "wrap" }}>
              <label style={{ flex: "1 1 140px", fontSize: "13px" }}>
                Channel
                <select
                  className="ops-select"
                  value={testChannel}
                  onChange={(e) => {
                    const c = e.target.value;
                    setTestChannel(c);
                    if (c === "sms") setTestRecipient("+256701234567");
                    else if (c === "webhook") setTestRecipient("https://hooks.ura.go.ug/notifications");
                    else setTestRecipient("taxpayer@ura.go.ug");
                  }}
                  style={{ width: "100%", marginTop: "4px" }}
                >
                  <option value="email">Email</option>
                  <option value="sms">SMS</option>
                  <option value="webhook">Webhook</option>
                </select>
              </label>

              <label style={{ flex: "2 1 240px", fontSize: "13px" }}>
                Recipient Address / Phone / URL
                <input
                  type="text"
                  className="ops-input"
                  value={testRecipient}
                  onChange={(e) => setTestRecipient(e.target.value)}
                  required
                  style={{ width: "100%", marginTop: "4px" }}
                />
              </label>
            </div>

            {testChannel === "email" ? (
              <label style={{ fontSize: "13px" }}>
                Subject
                <input
                  type="text"
                  className="ops-input"
                  value={testSubject}
                  onChange={(e) => setTestSubject(e.target.value)}
                  required
                  style={{ width: "100%", marginTop: "4px" }}
                />
              </label>
            ) : null}

            <label style={{ fontSize: "13px" }}>
              Notification Body
              <textarea
                className="ops-input"
                rows={3}
                value={testMessage}
                onChange={(e) => setTestMessage(e.target.value)}
                required
                style={{ width: "100%", marginTop: "4px" }}
              />
            </label>

            <div style={{ display: "flex", gap: "8px", justifyContent: "flex-end" }}>
              <button
                type="button"
                className="ops-btn"
                onClick={() => setShowTestDialog(false)}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="ops-btn ops-btn-primary"
                disabled={testMutation.isPending}
              >
                {testMutation.isPending ? "Sending..." : "Send Now"}
              </button>
            </div>
          </form>
        </OpsPanel>
      ) : null}

      {/* Filter and Metrics Row */}
      <div style={{ display: "flex", gap: "8px", margin: "16px 0", flexWrap: "wrap", alignItems: "center" }}>
        <button
          type="button"
          className={`ops-chip ${filterStatus === "" ? "ops-chip-active" : ""}`}
          onClick={() => setFilterStatus("")}
        >
          All ({stats.total})
        </button>
        <button
          type="button"
          className={`ops-chip is-caution ${filterStatus === "queued" ? "ops-chip-active" : ""}`}
          onClick={() => setFilterStatus("queued")}
        >
          Queued ({stats.queued})
        </button>
        <button
          type="button"
          className={`ops-chip is-good ${filterStatus === "sent" ? "ops-chip-active" : ""}`}
          onClick={() => setFilterStatus("sent")}
        >
          Sent ({stats.sent})
        </button>
        <button
          type="button"
          className={`ops-chip is-good ${filterStatus === "delivered" ? "ops-chip-active" : ""}`}
          onClick={() => setFilterStatus("delivered")}
        >
          Delivered ({stats.delivered})
        </button>
        <button
          type="button"
          className={`ops-chip is-danger ${filterStatus === "failed" ? "ops-chip-active" : ""}`}
          onClick={() => setFilterStatus("failed")}
        >
          Failed ({stats.failed})
        </button>
      </div>

      <OpsPanel id="outbox" title="Notification Logs & Delivery History" flush bare>
        {isLoading ? <SkeletonRows rows={4} height={48} /> : null}
        {error ? (
          <ErrorState body="The outbox service did not answer." onRetry={() => void refetch()} />
        ) : null}
        {!isLoading && !error && items.length === 0 ? (
          <EmptyState
            title="The outbox is empty"
            body={
              filterStatus
                ? `No notifications found with status "${filterStatus}".`
                : "Notifications appear here when deadline reminders or escalation alerts are dispatched."
            }
          />
        ) : null}
        {items.length > 0 ? (
          <TableScroll label="Notification outbox">
            <table className="ops-table">
              <thead>
                <tr>
                  <th scope="col">Channel</th>
                  <th scope="col">Recipient / User</th>
                  <th scope="col">Provider</th>
                  <th scope="col">Status</th>
                  <th scope="col">Message Reference</th>
                  <th scope="col">Time</th>
                  <th scope="col" style={{ textAlign: "right" }}>Action</th>
                </tr>
              </thead>
              <tbody>
                {items.map((row: OutboxItem) => {
                  const recipient = String(row.payload?.recipient || row.user_id || "-");
                  const refId = String(row.provider_msg_id || row.id || "");
                  const isFailed = FAILED.has(row.status.toLowerCase());
                  const isQueued = row.status.toLowerCase() === "queued";

                  return (
                    <tr key={row.id}>
                      <td>
                        <span className="ops-cell-strong">{channelBadge(row.channel)}</span>
                      </td>
                      <td>
                        <span style={{ fontSize: "13px" }}>{recipient}</span>
                      </td>
                      <td>
                        <code>{row.provider}</code>
                      </td>
                      <td>
                        <span className={`ops-chip ${statusTone(row.status)}`}>
                          {row.status}
                        </span>
                      </td>
                      <td>
                        <code title={refId}>{refId.slice(0, 16)}</code>
                      </td>
                      <td>
                        <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>
                          {row.created_at ? new Date(row.created_at * 1000).toLocaleTimeString() : "-"}
                        </span>
                      </td>
                      <td style={{ textAlign: "right" }}>
                        {isFailed || isQueued ? (
                          <button
                            type="button"
                            className="ops-btn ops-btn-sm"
                            disabled={retryMutation.isPending}
                            onClick={() => retryMutation.mutate(row.id)}
                          >
                            Retry
                          </button>
                        ) : (
                          <span style={{ color: "var(--text-muted)", fontSize: "12px" }}>✓ OK</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </TableScroll>
        ) : null}
      </OpsPanel>
    </OpsPage>
  );
}

export default function OutboxPage() {
  return (
    <StaffGuard current="/admin/outbox" requireRoles={["ura_admin", "ura_auditor"]}>
      {() => <OutboxBoard />}
    </StaffGuard>
  );
}


"use client";

/**
 * Enterprise Notification Outbox & Live Dispatcher (G14).
 *
 * Supports live multi-channel delivery:
 * - Email: Resend / AWS SES / SMTP with TLS
 * - SMS: Africa's Talking / Twilio
 * - Webhook: Signed HMAC-SHA256 delivery
 * - In-App: Real-time taxpayer reminder inbox & websocket stream
 *
 * Auditors inspect delivery logs and export CSV evidence read-only;
 * administrators dispatch and test live channels.
 */
import React, { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import StaffGuard, { type StaffIdentity } from "../../../components/StaffGuard";
import { OpsPage, OpsPanel, TableScroll } from "../../../components/ops/OpsPage";
import { Freshness } from "../../../components/ops/Controls";
import { StatCard } from "../../../components/ops/StatCard";
import { EmptyState, ErrorState, SkeletonRows } from "../../../components/ops/States";
import {
  BellIcon,
  CheckIcon,
  CloseIcon,
  DownloadIcon,
  GlobeIcon,
  InfoCircleIcon,
  MailIcon,
  SendIcon,
  SmartphoneIcon,
  ZapIcon,
} from "../../../components/ops/icons";
import { analyticsApi, OutboxItem } from "../../../services/analyticsApi";
import { csvFilename, csvTime, downloadCsv, toCsv } from "../../../lib/csv";
import { queryKeys } from "../../../lib/queryKeys";
import "../admin.css";

const FAILED = new Set(["failed", "error", "bounced"]);

function statusTone(status: string): string {
  const value = status.toLowerCase();
  if (FAILED.has(value)) return "is-danger";
  if (value === "sent" || value === "delivered") return "is-good";
  if (value === "queued") return "is-warn";
  return "";
}

function ChannelBadge({ channel }: { channel: string }) {
  const normalized = channel.toLowerCase();
  if (normalized === "email") {
    return (
      <span className="ob-channel-cell">
        <MailIcon /> Email
      </span>
    );
  }
  if (normalized === "sms") {
    return (
      <span className="ob-channel-cell">
        <SmartphoneIcon /> SMS
      </span>
    );
  }
  if (normalized === "webhook") {
    return (
      <span className="ob-channel-cell">
        <GlobeIcon /> Webhook
      </span>
    );
  }
  if (normalized === "in_app") {
    return (
      <span className="ob-channel-cell">
        <BellIcon /> In-App
      </span>
    );
  }
  return <span className="ob-channel-cell">{channel}</span>;
}

export function outboxToCsv(items: OutboxItem[]): string {
  return toCsv(
    ["id", "channel", "recipient", "provider", "status", "provider_msg_id", "created_utc"],
    items.map((row) => [
      row.id,
      row.channel,
      String(row.payload?.recipient || row.user_id || ""),
      row.provider,
      row.status,
      String(row.provider_msg_id || row.id || ""),
      csvTime(row.created_at),
    ]),
  );
}

export function OutboxBoard({ who }: { who?: StaffIdentity }) {
  const queryClient = useQueryClient();
  const [filterStatus, setFilterStatus] = useState<string>("");
  const [showTestDialog, setShowTestDialog] = useState(false);
  const [testChannel, setTestChannel] = useState("email");
  const [testRecipient, setTestRecipient] = useState("taxpayer@ura.go.ug");
  const [testSubject, setTestSubject] = useState("VAT Filing Reminder");
  const [testMessage, setTestMessage] = useState("Your monthly VAT return is due soon. File on eTax to avoid penalties.");
  const [actionNotice, setActionNotice] = useState<string>("");

  const canMutate = !who || who.role === "ura_admin";

  const { data, isLoading, isFetching, dataUpdatedAt, error, refetch } = useQuery({
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
      actions={
        <>
          <Freshness updatedAt={dataUpdatedAt} isFetching={isFetching} onRefresh={() => void refetch()} />
          <button
            type="button"
            className="ops-btn is-sm is-ghost"
            disabled={items.length === 0}
            onClick={() => downloadCsv(outboxToCsv(items), csvFilename("notification-outbox"))}
          >
            <DownloadIcon />
            Export {items.length} as CSV
          </button>
        </>
      }
      toolbar={
        canMutate ? (
          <div className="ops-row-inline">
            <button
              type="button"
              className="ops-btn is-primary is-sm"
              disabled={dispatchMutation.isPending}
              onClick={() => dispatchMutation.mutate()}
            >
              <ZapIcon />
              {dispatchMutation.isPending ? "Dispatching..." : "Dispatch Outbox"}
            </button>
            <button
              type="button"
              className="ops-btn is-sm"
              onClick={() => setShowTestDialog(!showTestDialog)}
            >
              <SendIcon />
              Send Live Test
            </button>
          </div>
        ) : undefined
      }
    >
      {!canMutate ? (
        <div className="ops-note" role="note">
          <span className="ops-note-mark" aria-hidden="true">
            <InfoCircleIcon />
          </span>
          <div>
            <p className="ops-note-title">Read-only for your role</p>
            <p className="ops-note-body">
              Auditors can inspect notification delivery logs and export CSV records. Dispatching queues and sending
              live test messages stays with administrators.
            </p>
          </div>
        </div>
      ) : null}

      {/* Live Provider Status Banner */}
      <div className={`ob-provider-banner${isLiveMode ? " is-live" : ""}`} role="status">
        <div className="ob-provider-head">
          <span className="ob-provider-dot" aria-hidden="true" />
          <div>
            <strong className="ob-provider-title">
              {isLiveMode ? "Live Notification Engine Active" : "Simulation Mode (Local Sandbox)"}
            </strong>
            <p className="ob-provider-sub">
              {isLiveMode
                ? "Active providers connect directly to live email, SMS, and webhook delivery gateways."
                : "Messages are recorded with simulated delivery verification. Connect real gateways for live dispatch."}
            </p>
          </div>
        </div>

        <div className="ob-provider-chips">
          {Object.entries(providers).map(([ch, info]) => (
            <span
              key={ch}
              className={`ops-chip ${info.configured ? "is-good" : ""}`}
            >
              {ch.toUpperCase()}: {info.backend || (info.configured ? "ready" : "inactive")}
            </span>
          ))}
        </div>
      </div>

      <section aria-label="Outbox delivery summary">
        <div className="ops-stat-grid ov-stats">
          <StatCard label="Total Notifications" value={stats.total} />
          <StatCard label="Queued" value={stats.queued} />
          <StatCard label="Delivered / Sent" value={(stats.delivered ?? 0) + (stats.sent ?? 0)} />
          <StatCard label="Failed" value={stats.failed} />
        </div>
      </section>

      {actionNotice ? (
        <div className="ops-note" role="status">
          <span className="ops-note-mark" aria-hidden="true">
            <InfoCircleIcon />
          </span>
          <div style={{ flex: 1 }}>
            <p className="ops-note-body">{actionNotice}</p>
          </div>
          <button
            type="button"
            className="ops-icon-btn"
            onClick={() => setActionNotice("")}
            aria-label="Dismiss notice"
          >
            <CloseIcon />
          </button>
        </div>
      ) : null}

      {/* Test Notification Panel */}
      {showTestDialog && canMutate ? (
        <OpsPanel id="test-notification-panel" title="Send Live Test Notification" glass>
          <form
            className="ov-form"
            onSubmit={(e) => {
              e.preventDefault();
              testMutation.mutate({
                channel: testChannel,
                recipient: testRecipient,
                subject: testSubject,
                message: testMessage,
              });
            }}
          >
            <div className="disc-drawer-grid">
              <label className="ops-field">
                <span className="ops-field-label">Channel</span>
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
                >
                  <option value="email">Email</option>
                  <option value="sms">SMS</option>
                  <option value="webhook">Webhook</option>
                </select>
              </label>

              <label className="ops-field">
                <span className="ops-field-label">Recipient Address / Phone / URL</span>
                <input
                  type="text"
                  className="ops-input"
                  value={testRecipient}
                  onChange={(e) => setTestRecipient(e.target.value)}
                  required
                />
              </label>
            </div>

            {testChannel === "email" ? (
              <label className="ops-field">
                <span className="ops-field-label">Subject</span>
                <input
                  type="text"
                  className="ops-input"
                  value={testSubject}
                  onChange={(e) => setTestSubject(e.target.value)}
                  required
                />
              </label>
            ) : null}

            <label className="ops-field">
              <span className="ops-field-label">Notification Body</span>
              <textarea
                className="ops-textarea"
                rows={3}
                value={testMessage}
                onChange={(e) => setTestMessage(e.target.value)}
                required
              />
            </label>

            <div className="ov-form-actions">
              <button
                type="submit"
                className="ops-btn is-primary"
                disabled={testMutation.isPending}
              >
                {testMutation.isPending ? "Sending..." : "Send Now"}
              </button>
              <button
                type="button"
                className="ops-btn is-ghost"
                onClick={() => setShowTestDialog(false)}
              >
                Cancel
              </button>
            </div>
          </form>
        </OpsPanel>
      ) : null}

      {/* Filter Row */}
      <div className="ob-filters" role="group" aria-label="Filter notifications by status">
        {[
          { key: "", label: `All (${stats.total})` },
          { key: "queued", label: `Queued (${stats.queued})` },
          { key: "sent", label: `Sent (${stats.sent})` },
          { key: "delivered", label: `Delivered (${stats.delivered})` },
          { key: "failed", label: `Failed (${stats.failed})` },
        ].map((f) => (
          <button
            key={f.key || "all"}
            type="button"
            className={`ops-filter${filterStatus === f.key ? " is-active" : ""}`}
            aria-pressed={filterStatus === f.key}
            onClick={() => setFilterStatus(f.key)}
          >
            {f.label}
          </button>
        ))}
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
                  <th scope="col" className="is-num">Action</th>
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
                        <ChannelBadge channel={row.channel} />
                      </td>
                      <td>{recipient}</td>
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
                      <td className="au-when">
                        {row.created_at ? new Date(row.created_at * 1000).toLocaleTimeString() : "-"}
                      </td>
                      <td className="is-num">
                        {canMutate && (isFailed || isQueued) ? (
                          <button
                            type="button"
                            className="ops-btn is-sm is-ghost"
                            disabled={retryMutation.isPending}
                            onClick={() => retryMutation.mutate(row.id)}
                          >
                            Retry
                          </button>
                        ) : (
                          <span className="ops-chip is-good">
                            <CheckIcon /> OK
                          </span>
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
      {(who) => <OutboxBoard who={who} />}
    </StaffGuard>
  );
}

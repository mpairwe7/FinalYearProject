"use client";

/**
 * Mock notification outbox (G14). Nothing is sent.
 *
 * The page used to render each row as `<li><strong>channel</strong><p>provider ·
 * status</p></li>` inside `.ov-queue` — a list styled for the escalation queue's
 * link rows, so it inherited the border and none of the layout. Rows of the same
 * three fields are a table; that is what they are now, grouped by status so a
 * failed send is not buried between two queued ones.
 */
import React, { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import StaffGuard from "../../../components/StaffGuard";
import { OpsPage, OpsPanel, TableScroll } from "../../../components/ops/OpsPage";
import { EmptyState, ErrorState, SkeletonRows } from "../../../components/ops/States";
import { analyticsApi } from "../../../services/analyticsApi";
import "../admin.css";

const FAILED = new Set(["failed", "error", "bounced"]);

function statusTone(status: string): string {
  const value = status.toLowerCase();
  if (FAILED.has(value)) return "is-danger";
  if (value === "sent" || value === "delivered") return "is-good";
  return "";
}

function OutboxBoard() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["adminOutbox"],
    queryFn: () => analyticsApi.outbox(),
    staleTime: 10_000,
  });

  const items = useMemo(() => data?.items ?? [], [data]);
  const byStatus = useMemo(() => {
    const counts = new Map<string, number>();
    for (const row of items) counts.set(row.status, (counts.get(row.status) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [items]);

  return (
    <OpsPage
      eyebrow="Configure"
      title="Notification outbox"
      description="Email and SMS rows the escalation pipeline queued. This prototype records them; it does not send them."
      width="read"
      toolbar={
        items.length > 0 ? (
          <>
            <span className="ops-chip">{items.length} queued</span>
            {byStatus.map(([status, count]) => (
              <span className={`ops-chip ${statusTone(status)}`} key={status}>
                {count} {status}
              </span>
            ))}
          </>
        ) : null
      }
    >
      {data && data.live === false ? (
        <div className="ops-note" role="note">
          <span className="ops-note-mark" aria-hidden="true">
            ⓘ
          </span>
          <div>
            <p className="ops-note-title">No provider is connected</p>
            <p className="ops-note-body">
              Every row below was written with <code>provider=mock</code>. Nothing left the
              building, and a taxpayer waiting on one of these notifications did not receive it.
            </p>
          </div>
        </div>
      ) : null}

      <OpsPanel id="outbox" title="Queued messages" flush>
        {isLoading ? <SkeletonRows rows={4} height={48} /> : null}
        {error ? (
          <ErrorState body="The outbox did not answer." onRetry={() => void refetch()} />
        ) : null}
        {!isLoading && !error && items.length === 0 ? (
          <EmptyState
            title="The outbox is empty"
            body="Notifications appear here when an escalation asks for one — a resolved ticket, or a reply waiting for a taxpayer who has left the chat."
          />
        ) : null}
        {items.length > 0 ? (
          <TableScroll label="Notification outbox">
            <table className="ops-table">
              <thead>
                <tr>
                  <th scope="col">Channel</th>
                  <th scope="col">Provider</th>
                  <th scope="col">Status</th>
                  <th scope="col">Reference</th>
                </tr>
              </thead>
              <tbody>
                {items.map((row) => (
                  <tr key={row.id}>
                    <td>
                      <span className="ops-cell-strong">{row.channel}</span>
                    </td>
                    <td>
                      <code>{row.provider}</code>
                    </td>
                    <td>
                      <span className={`ops-chip ${statusTone(row.status)}`}>{row.status}</span>
                    </td>
                    <td>
                      <code>{row.id.slice(0, 12)}</code>
                    </td>
                  </tr>
                ))}
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

"use client";

/**
 * Who is on duty, for the administrator's overview.
 *
 * Officers' presence as the Call Desk reports it (GET /v1/admin/officers/presence,
 * re-read every 10 s). Read-only by design: it opens no call and offers nothing
 * to take, because the administrator audits the service officers give rather
 * than working the queue. It replaces the admin-only Supervisor tab that sat
 * on /calls, a page administrators no longer open.
 */
import React, { useMemo } from "react";
import { TableScroll } from "../ops/OpsPage";
import { EmptyState, ErrorState, SkeletonRows } from "../ops/States";
import { useOfficerPresence } from "../../hooks/useCalls";
import { useClientClock } from "../../hooks/useClientClock";
import { useSkeletonVisible } from "../../hooks/useLoadingPhase";
import { formatDuration } from "../../lib/ticketUi";
import type { OfficerPresence } from "../../services/callsApi";

type PresenceStatus = OfficerPresence["status"];

const STATUS_LABEL: Record<PresenceStatus, string> = {
  available: "Available",
  on_call: "On a call",
  wrap_up: "Wrapping up",
  busy: "Busy",
  away: "Away",
  offline: "Offline",
};

/** A state's colour always travels with its word, never instead of it. */
const STATUS_TONE: Partial<Record<PresenceStatus, string>> = {
  available: "is-good",
  on_call: "is-info",
  wrap_up: "is-warn",
  busy: "is-warn",
  away: "is-warn",
};

/** Online first, in the order a reader scans for capacity. */
const STATUS_ORDER: PresenceStatus[] = ["available", "on_call", "wrap_up", "busy", "away", "offline"];

export function presenceCounts(officers: OfficerPresence[]) {
  return {
    online: officers.filter((o) => o.status !== "offline").length,
    available: officers.filter((o) => o.status === "available").length,
    onCall: officers.filter((o) => o.status === "on_call").length,
  };
}

export function OfficersOnDuty() {
  const presence = useOfficerPresence();
  const now = useClientClock();
  const loading = useSkeletonVisible(presence.isLoading);

  const officers = useMemo(() => {
    const rows = presence.data?.officers ?? [];
    return [...rows].sort(
      (a, b) =>
        STATUS_ORDER.indexOf(a.status) - STATUS_ORDER.indexOf(b.status) ||
        (a.display_name || a.user_id).localeCompare(b.display_name || b.user_id),
    );
  }, [presence.data]);
  const counts = presenceCounts(officers);

  if (loading) return <SkeletonRows rows={4} height={36} />;
  if (presence.isError) {
    return <ErrorState body="Officer presence did not load." onRetry={() => void presence.refetch()} />;
  }
  if (!officers.length) {
    return (
      <EmptyState
        title="No officer has signed in yet"
        body="Officers appear here once they open the console; their availability updates as they take calls."
      />
    );
  }

  return (
    <>
      <p className="ov-duty-summary">
        <strong>{counts.available}</strong> available · <strong>{counts.onCall}</strong> on a call ·{" "}
        <strong>{counts.online}</strong> online of {officers.length}
      </p>
      <TableScroll label="Officers on duty">
        <table className="ops-table">
          <thead>
            <tr>
              <th scope="col">Officer</th>
              <th scope="col">Status</th>
              <th scope="col">Languages</th>
              <th scope="col">Teams</th>
              <th scope="col">Last seen</th>
            </tr>
          </thead>
          <tbody>
            {officers.map((o) => (
              <tr key={o.user_id}>
                <td>{o.display_name || o.user_id}</td>
                <td>
                  <span className={`ops-chip ${STATUS_TONE[o.status] ?? ""}`.trim()}>
                    {STATUS_LABEL[o.status] ?? o.status}
                  </span>
                </td>
                <td>{o.languages?.length ? o.languages.join(", ") : "en"}</td>
                <td>{o.teams?.length ? o.teams.map((t) => t.replace(/_/g, " ")).join(", ") : "general"}</td>
                <td className="ops-tabular-num">
                  {now ? `${formatDuration(Math.max(0, now / 1000 - o.last_seen))} ago` : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
    </>
  );
}

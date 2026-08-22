"use client";

/**
 * Feature-flag console (G31 slice).
 *
 * Reads the replica registry so an operator does not need SSH.
 * Toggles persist on this replica (flag_overrides table). Cluster-wide
 * still needs FLAG_* on every replica. Safety flags cannot be flipped.
 *
 * The redesign changed three things about how it reads. The state control is a
 * switch rather than a button whose entire label was the word "on" — the state
 * and the action used to be the same three characters. The rows sort so
 * anything diverging from its default floats to the top, because "what is
 * different here" is the only question this page gets asked in an incident. And
 * the replica caveat is a banner instead of a sentence in the subtitle, since
 * acting on it wrongly means believing a cluster is configured when one pod is.
 */
import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import StaffGuard, { type StaffIdentity } from "../../../components/StaffGuard";
import { OpsPage, TableScroll } from "../../../components/ops/OpsPage";
import { Switch } from "../../../components/ops/Controls";
import { EmptyState, ErrorState, SkeletonRows } from "../../../components/ops/States";
import { AlertTriangleIcon } from "../../../components/ops/icons";
import { analyticsApi, type FlagRecord } from "../../../services/analyticsApi";
import "../admin.css";

function FlagsBoard({ who }: { who: StaffIdentity }) {
  const client = useQueryClient();
  const [query, setQuery] = useState("");
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["adminFlags"],
    queryFn: () => analyticsApi.flags(),
    staleTime: 10_000,
  });
  const toggle = useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) =>
      analyticsApi.setFlag(name, enabled),
    onSuccess: () => client.invalidateQueries({ queryKey: ["adminFlags"] }),
  });
  const canToggle = who.role === "ura_admin";

  const flags = useMemo(() => data?.flags ?? [], [data]);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    const matched = q
      ? flags.filter((f) => `${f.name} ${f.description}`.toLowerCase().includes(q))
      : flags;
    // Diverging first, then overridden here, then alphabetical — an operator
    // reading this page mid-incident is looking for what is not standard.
    return [...matched].sort((a, b) => {
      const rank = (f: FlagRecord) =>
        f.enabled !== f.default ? 0 : f.overridden ? 1 : 2;
      return rank(a) - rank(b) || a.name.localeCompare(b.name);
    });
  }, [flags, query]);

  const on = flags.filter((f) => f.enabled).length;
  const diverging = flags.filter((f) => f.enabled !== f.default).length;
  const protectedCount = flags.filter((f) => f.protected).length;

  return (
    <OpsPage
      eyebrow="Configure"
      title="Feature flags"
      description="What this replica is serving right now, and what it would serve on a cold start."
      actions={
        <input
          type="search"
          className="ops-input ops-search"
          placeholder="Filter by name or description…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          aria-label="Filter flags"
        />
      }
      toolbar={
        flags.length > 0 ? (
          <>
            <span className="ops-chip">{flags.length} flags</span>
            <span className="ops-chip is-good">{on} on</span>
            {diverging > 0 ? (
              <span className="ops-chip is-warn">{diverging} not at default</span>
            ) : (
              <span className="ops-chip">all at default</span>
            )}
            <span className="ops-chip">{protectedCount} protected</span>
            {query ? (
              <span className="ops-toolbar-end">
                <span className="ops-stat-hint">
                  {rows.length} of {flags.length} shown
                </span>
              </span>
            ) : null}
          </>
        ) : null
      }
    >
      {/* Not a footnote: believing this is cluster-wide is the failure mode. */}
      <div className="ops-note" role="note">
        <span className="ops-note-mark" aria-hidden="true">
          <AlertTriangleIcon />
        </span>
        <div>
          <p className="ops-note-title">Toggles here change this replica only</p>
          <p className="ops-note-body">
            A change persists in this pod’s <code>flag_overrides</code> table and is lost when it
            is replaced. A cluster-wide change means setting <code>FLAG_*</code> on every replica.
            {data?.overrides_are_ephemeral === false
              ? " This deployment reports overrides as durable."
              : null}
          </p>
        </div>
      </div>

      {isLoading ? <SkeletonRows rows={6} height={56} /> : null}
      {error ? (
        <ErrorState body="The flag registry did not answer." onRetry={() => void refetch()} />
      ) : null}

      {!isLoading && !error && rows.length === 0 ? (
        <EmptyState
          title={query ? "No flag matches that" : "No flags registered"}
          body={query ? "Try part of the flag name, or clear the filter." : undefined}
        />
      ) : null}

      {rows.length > 0 ? (
        <TableScroll label="Feature flags">
          <table className="ops-table">
            <thead>
              <tr>
                <th scope="col">Flag</th>
                <th scope="col">State</th>
                <th scope="col">Default</th>
                <th scope="col">Notes</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((flag) => {
                const diverged = flag.enabled !== flag.default;
                return (
                  <tr key={flag.name}>
                    <td>
                      <span className="ops-cell-strong">
                        <code>{flag.name}</code>
                      </span>
                      <span className="ops-cell-sub ops-cell-clamp">{flag.description}</span>
                    </td>
                    <td>
                      {canToggle && !flag.protected ? (
                        <Switch
                          checked={flag.enabled}
                          disabled={toggle.isPending}
                          label={`${flag.name} — currently ${flag.enabled ? "on" : "off"}`}
                          onChange={(next) => toggle.mutate({ name: flag.name, enabled: next })}
                        />
                      ) : (
                        <span className={`ops-chip ${flag.enabled ? "is-good" : ""}`}>
                          {flag.enabled ? "On" : "Off"}
                        </span>
                      )}
                    </td>
                    <td className="ops-cell-default">{flag.default ? "on" : "off"}</td>
                    <td>
                      <span className="ops-row-inline">
                        {diverged ? (
                          <span className="ops-chip is-warn">not at default</span>
                        ) : null}
                        {flag.protected ? <span className="ops-chip">protected</span> : null}
                        {flag.overridden ? <span className="ops-chip is-info">this replica</span> : null}
                        {flag.rollout ? (
                          <span className="ops-chip">{flag.rollout.percent}% rollout</span>
                        ) : null}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </TableScroll>
      ) : null}

      {toggle.isError ? (
        <p className="ops-stat-hint" role="alert" style={{ marginTop: "var(--ops-space-3)" }}>
          That toggle did not save. The registry may be read-only on this deployment.
        </p>
      ) : null}
    </OpsPage>
  );
}

export default function AdminFlagsPage() {
  return (
    <StaffGuard current="/admin/flags" requireRoles={["ura_admin", "ura_auditor"]}>
      {(who) => <FlagsBoard who={who} />}
    </StaffGuard>
  );
}

"use client";

/**
 * Feature-flag console (G31 slice).
 *
 * Reads the replica registry so an operator does not need SSH.
 * Toggles persist on this replica (flag_overrides table). Cluster-wide
 * still needs FLAG_* on every replica. Safety flags cannot be flipped.
 */
import React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import StaffGuard, { type StaffIdentity } from "../../../components/StaffGuard";
import { analyticsApi } from "../../../services/analyticsApi";
import "../admin.css";

function FlagsBoard({ who }: { who: StaffIdentity }) {
  const client = useQueryClient();
  const { data, isLoading, error } = useQuery({
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

  return (
    <main className="ov-page" id="staff-main">
      <header className="ov-head">
        <div>
          <h1>Feature flags</h1>
          <p className="ov-sub">
            What this replica is serving. Toggles persist in this replica’s
            store — set FLAG_* on every pod for a cluster-wide change.
          </p>
        </div>
      </header>
      {isLoading ? <p className="ov-empty">Loading flags…</p> : null}
      {error ? <p className="ov-empty">Could not load flags.</p> : null}
      {data ? (
        <table className="flag-table">
          <thead>
            <tr>
              <th>Flag</th>
              <th>State</th>
              <th>Default</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            {data.flags.map((flag) => (
              <tr key={flag.name}>
                <td>
                  <strong>{flag.name}</strong>
                  <div className="ov-q-query">{flag.description}</div>
                </td>
                <td>
                  {canToggle && !flag.protected ? (
                    <button
                      type="button"
                      className={flag.enabled ? "is-active" : undefined}
                      disabled={toggle.isPending}
                      aria-pressed={flag.enabled}
                      aria-label={`${flag.name}: ${flag.enabled ? "on" : "off"}`}
                      onClick={() => toggle.mutate({ name: flag.name, enabled: !flag.enabled })}
                    >
                      {flag.enabled ? "on" : "off"}
                    </button>
                  ) : (
                    <span className={flag.enabled ? "ov-chip good" : "ov-chip warn"}>
                      {flag.enabled ? "on" : "off"}
                    </span>
                  )}
                </td>
                <td>{flag.default ? "on" : "off"}</td>
                <td>
                  {flag.protected ? "protected" : null}
                  {flag.overridden ? " · this process" : null}
                  {flag.rollout ? ` · ${flag.rollout.percent}% rollout` : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </main>
  );
}

export default function AdminFlagsPage() {
  return (
    <StaffGuard current="/admin/flags" requireRoles={["ura_admin", "ura_auditor"]}>
      {(who) => <FlagsBoard who={who} />}
    </StaffGuard>
  );
}

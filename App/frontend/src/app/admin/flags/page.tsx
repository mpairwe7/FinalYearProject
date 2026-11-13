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
import React, { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import StaffGuard, { type StaffIdentity } from "../../../components/StaffGuard";
import { OpsPage, TableScroll } from "../../../components/ops/OpsPage";
import { Switch } from "../../../components/ops/Controls";
import { EmptyState, ErrorState, SkeletonRows } from "../../../components/ops/States";
import { AlertTriangleIcon, InfoCircleIcon } from "../../../components/ops/icons";
import { analyticsApi, type FlagRecord } from "../../../services/analyticsApi";
import { queryKeys } from "../../../lib/queryKeys";
import "../admin.css";

/**
 * Controls that keep an answer safe to show (G638). Switching one off changes
 * what every taxpayer is shown, so the console says what it does and asks.
 */
const SAFETY_CONTROLS: Record<string, string> = {
  stream_figure_hold: "Figures will stream to taxpayers before the guards have read them.",
  spoken_figure_guard: "Calls will speak figures the guard has not checked.",
  action_level_audit_v3: "High-risk tool effects will run without a durable intent record first.",
  guardrail_strike_friction: "Repeated guardrail strikes will no longer slow a session down.",
  verifiable_decision_receipts: "Decisions will no longer carry a verifiable receipt.",
  autonomous_outcome_verification: "Autonomous actions will no longer have their outcome checked.",
};

function FlagsBoard({ who }: { who: StaffIdentity }) {
  const client = useQueryClient();
  const [query, setQuery] = useState("");
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: queryKeys.admin.flags(),
    queryFn: () => analyticsApi.flags(),
    staleTime: 10_000,
  });
  const toggle = useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) =>
      analyticsApi.setFlag(name, enabled),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.admin.flags() }),
  });
  // The way back out. An override beats FLAG_* and is replayed on every boot,
  // so flipping a flag to its default value is not the same as removing the
  // override — the flag stays pinned and keeps ignoring the environment. Until
  // this existed the page could pin a flag and never unpin it.
  const reset = useMutation({
    mutationFn: (name: string) => analyticsApi.clearFlag(name),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.admin.flags() }),
  });
  const canToggle = who.role === "ura_admin";
  // G638: a safety control is switched off, or reset to a value that may be
  // off, only after saying what that does.
  const [confirm, setConfirm] = useState<{ name: string; action: "off" | "reset" } | null>(null);
  // The confirmation renders after the whole table: it takes focus when it
  // opens (which scrolls it into view) and gives it back to the control that
  // opened it when it closes.
  const keepOnRef = useRef<HTMLButtonElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const askFirst = (next: { name: string; action: "off" | "reset" }) => {
    openerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setConfirm(next);
  };
  const closeConfirm = () => {
    setConfirm(null);
    const opener = openerRef.current;
    openerRef.current = null;
    requestAnimationFrame(() => {
      if (opener && opener.isConnected) opener.focus();
    });
  };
  useEffect(() => {
    if (confirm) keepOnRef.current?.focus();
  }, [confirm]);

  const flags = useMemo(() => data?.flags ?? [], [data]);
  // Every process re-reads the stored overrides on this interval (G612); 0 means
  // the others apply a change at their restart. Until the API says (loading, an
  // error, an older API) the note claims neither.
  const syncSeconds = data?.sync_interval_seconds;
  // The same reach the page note states, for the confirmation (G638): a
  // promise of "every process within one sync" is wrong where the sync is off,
  // and a replica with its own database is never reached from here.
  const reachOfAChange =
    typeof syncSeconds !== "number"
      ? "How far this reaches is not known yet: confirm each process from /ready."
      : syncSeconds > 0
        ? `Other API processes sharing this database apply it at their next successful sync (every ${syncSeconds} s); confirm the call process from /ready. Replicas with their own database are not reached.`
        : "It applies at once in the process that answered; the other processes apply it when they next start. Replicas with their own database are not reached.";

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
      {/* Not a footnote: how far a toggle reaches is what an operator gets wrong. */}
      <div className="ops-note" role="note">
        <span className="ops-note-mark" aria-hidden="true">
          <AlertTriangleIcon />
        </span>
        <div>
          {typeof syncSeconds !== "number" ? (
            <p className="ops-note-title">How far a change here reaches is not known yet</p>
          ) : syncSeconds > 0 ? (
            <p className="ops-note-title">
              Changes here reach every API process at its next sync, every {syncSeconds} s
            </p>
          ) : (
            <p className="ops-note-title">
              Changes here reach the other API processes when they restart
            </p>
          )}
          <p className="ops-note-body">
            A change is stored in the <code>flag_overrides</code> table and replayed
            whenever a process restarts, so it keeps winning over <code>FLAG_*</code> until you
            reset it, as long as the database survives the restart (a Space without persistent
            storage starts with an empty one).{" "}
            {typeof syncSeconds !== "number" ? null : syncSeconds > 0 ? (
              <>
                A process that cannot read the store keeps its flags and logs it, so confirm the
                calls switch from <code>/ready</code>.{" "}
              </>
            ) : (
              "It applies at once in the process that answered; the others apply it when they next start. "
            )}
            Replicas with a database each still need <code>FLAG_*</code> set on every replica.
            {/* G638: said only when the API knows where its store lives. */}
            {data?.overrides_durability === "durable"
              ? " This deployment keeps its overrides across a restart."
              : data?.overrides_durability === "ephemeral"
                ? " Overrides on this deployment are lost when this deployment restarts: set the FLAG_* secret for anything that must stay."
                : " This deployment does not say whether its database survives a restart."}
          </p>
        </div>
      </div>

      {!canToggle ? (
        <div className="ops-note" role="note">
          <span className="ops-note-mark" aria-hidden="true">
            <InfoCircleIcon />
          </span>
          <div>
            <p className="ops-note-title">Read-only for your role</p>
            <p className="ops-note-body">
              Auditors can inspect active feature flags and replica overrides. Toggling or resetting flags stays with
              administrators.
            </p>
          </div>
        </div>
      ) : null}

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
                // Either mutation blocks both controls. Guarding each on only
                // its own isPending let a user flip the switch (PATCH) while a
                // Reset (DELETE) was still in flight for the same flag; the
                // request that happened to land second won, so a Reset could
                // leave the override in place.
                const mutating = toggle.isPending || reset.isPending;
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
                          disabled={mutating}
                          label={`${flag.name} — currently ${flag.enabled ? "on" : "off"}`}
                          onChange={(next) =>
                            !next && SAFETY_CONTROLS[flag.name]
                              ? askFirst({ name: flag.name, action: "off" })
                              : toggle.mutate({ name: flag.name, enabled: next })
                          }
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
                        {flag.stored ? (
                          <span className="ops-chip is-info">stored · every process</span>
                        ) : flag.overridden ? (
                          <span className="ops-chip is-info">this process only</span>
                        ) : null}
                        {flag.rollout ? (
                          <span className="ops-chip">{flag.rollout.percent}% rollout</span>
                        ) : null}
                        {flag.overridden && canToggle && !flag.protected ? (
                          <button
                            type="button"
                            className="ops-btn is-ghost is-sm"
                            disabled={mutating}
                            onClick={() =>
                              // Reset drops the override: an enabled safety
                              // control then follows FLAG_* or its default,
                              // which may be off, so it asks first too.
                              SAFETY_CONTROLS[flag.name] && flag.enabled
                                ? askFirst({ name: flag.name, action: "reset" })
                                : reset.mutate(flag.name)
                            }
                            title="Drop the override so this flag follows FLAG_* or its default again"
                          >
                            Reset
                          </button>
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

      {confirm ? (
        <div className="ops-note ops-confirm" role="alertdialog" aria-labelledby="flag-confirm-title" aria-describedby="flag-confirm-body">
          <div>
            <p className="ops-note-title" id="flag-confirm-title">
              {confirm.action === "off" ? `Turn off ${confirm.name}?` : `Reset ${confirm.name}?`}
            </p>
            <p className="ops-note-body" id="flag-confirm-body">
              {confirm.action === "reset"
                ? `This drops the override, so the flag follows its FLAG_* value or its default (${
                    flags.find((f) => f.name === confirm.name)?.default ? "on" : "off"
                  }), which may switch it off. If it does: `
                : null}
              {SAFETY_CONTROLS[confirm.name]} {reachOfAChange}
            </p>
            <div className="ops-row-inline">
              <button
                type="button"
                className="ops-btn is-sm"
                disabled={toggle.isPending || reset.isPending}
                onClick={() => {
                  if (confirm.action === "off") toggle.mutate({ name: confirm.name, enabled: false });
                  else reset.mutate(confirm.name);
                  closeConfirm();
                }}
              >
                {confirm.action === "off" ? "Turn it off" : "Reset it"}
              </button>
              <button type="button" className="ops-btn is-primary is-sm" ref={keepOnRef} onClick={closeConfirm}>
                Keep it on
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {toggle.isError || reset.isError ? (
        <p className="ops-stat-hint" role="alert" style={{ marginTop: "var(--ops-space-3)" }}>
          {reset.isError
            ? "That reset did not save. The flag is still overridden on this replica."
            : "That toggle did not save. The registry may be read-only on this deployment."}
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

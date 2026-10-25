"use client";

/**
 * Knowledge Discrepancy & Bug Reporting workbench.
 * Allows URA administrators to triage taxpayer-reported inaccuracies,
 * verify real statutory/procedural bugs with 1-click hotfixes
 * (Answer Overrides, Solution C Statutory Precedences, Solution B Chunk Tombstones),
 * or dismiss invalid reports. Officers and auditors inspect reports read-only.
 */

import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import StaffGuard, { type StaffIdentity } from "../../../components/StaffGuard";
import { OpsPage, OpsPanel } from "../../../components/ops/OpsPage";
import { Freshness } from "../../../components/ops/Controls";
import { StatCard } from "../../../components/ops/StatCard";
import { EmptyState, ErrorState, SkeletonRows } from "../../../components/ops/States";
import { AlertTriangleIcon, CloseIcon, DownloadIcon, InfoCircleIcon } from "../../../components/ops/icons";
import {
  analyticsApi,
  type DiscrepancyRecord,
  type VerifyDiscrepancyPayload,
} from "../../../services/analyticsApi";
import { csvFilename, csvTime, downloadCsv, toCsv } from "../../../lib/csv";
import { queryKeys } from "../../../lib/queryKeys";
import "../admin.css";

const STATUS_OPTIONS = [
  { value: "", label: "All" },
  { value: "pending", label: "Pending" },
  { value: "verified", label: "Verified" },
  { value: "dismissed", label: "Dismissed" },
] as const;

export function discrepanciesToCsv(rows: DiscrepancyRecord[]): string {
  return toCsv(
    [
      "id",
      "status",
      "priority",
      "discrepancy_type",
      "frequency_count",
      "bot_statement",
      "user_correction",
      "cited_sources",
      "admin_note",
      "created_utc",
    ],
    rows.map((d) => [
      d.id,
      d.status,
      d.priority,
      d.discrepancy_type,
      d.frequency_count,
      d.bot_statement,
      d.user_correction,
      d.cited_sources.join("; "),
      d.admin_note ?? "",
      csvTime(d.created_at),
    ]),
  );
}

export function DiscrepanciesWorkbench({ who }: { who?: StaffIdentity }) {
  const client = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<string>("pending");
  const [verifyingId, setVerifyingId] = useState<string | null>(null);
  const [dismissingId, setDismissingId] = useState<string | null>(null);

  // Form states for verification
  const [overrideQuery, setOverrideQuery] = useState("");
  const [overrideReply, setOverrideReply] = useState("");
  const [precedenceTopic, setPrecedenceTopic] = useState("");
  const [precedenceRule, setPrecedenceRule] = useState("");
  const [statuteRef, setStatuteRef] = useState("");
  const [tombstoneCited, setTombstoneCited] = useState(true);
  const [adminNote, setAdminNote] = useState("");
  const [actionNotice, setActionNotice] = useState<string>("");
  const [actionError, setActionError] = useState<string>("");

  const { data, isLoading, isFetching, dataUpdatedAt, error, refetch } = useQuery({
    queryKey: queryKeys.admin.discrepancies(statusFilter || undefined),
    queryFn: () => analyticsApi.discrepancies({ status: statusFilter || undefined, limit: 100 }),
    staleTime: 10_000,
  });

  const { data: precData } = useQuery({
    queryKey: queryKeys.admin.precedences(),
    queryFn: () => analyticsApi.precedences(),
    staleTime: 15_000,
  });

  const { data: tombData } = useQuery({
    queryKey: queryKeys.admin.tombstones(),
    queryFn: () => analyticsApi.tombstones(),
    staleTime: 15_000,
  });

  const verifyMutation = useMutation({
    mutationFn: (payload: { id: string; body: VerifyDiscrepancyPayload }) =>
      analyticsApi.verifyDiscrepancy(payload.id, payload.body),
    onSuccess: (_res, vars) => {
      setVerifyingId(null);
      resetForms();
      setActionError("");
      setActionNotice(`Verified report #${vars.id.slice(-8)} and applied hotfixes.`);
      void client.invalidateQueries({ queryKey: queryKeys.admin.discrepancies() });
      void client.invalidateQueries({ queryKey: queryKeys.admin.overrides() });
      void client.invalidateQueries({ queryKey: queryKeys.admin.precedences() });
      void client.invalidateQueries({ queryKey: queryKeys.admin.tombstones() });
    },
    onError: (err) => {
      setActionError(err instanceof Error ? err.message : "Could not verify discrepancy report.");
    },
  });

  const dismissMutation = useMutation({
    mutationFn: (payload: { id: string; note: string }) =>
      analyticsApi.dismissDiscrepancy(payload.id, { admin_note: payload.note, reason: payload.note }),
    onSuccess: (_res, vars) => {
      setDismissingId(null);
      resetForms();
      setActionError("");
      setActionNotice(`Dismissed report #${vars.id.slice(-8)}.`);
      void client.invalidateQueries({ queryKey: queryKeys.admin.discrepancies() });
    },
    onError: (err) => {
      setActionError(err instanceof Error ? err.message : "Could not dismiss discrepancy report.");
    },
  });

  const deleteTombstoneMutation = useMutation({
    mutationFn: (id: string) => analyticsApi.deleteTombstone(id),
    onSuccess: () => {
      setActionError("");
      setActionNotice("Removed corpus tombstone.");
      void client.invalidateQueries({ queryKey: queryKeys.admin.tombstones() });
    },
    onError: (err) => {
      setActionError(err instanceof Error ? err.message : "Could not remove corpus tombstone.");
    },
  });

  const deletePrecedenceMutation = useMutation({
    mutationFn: (id: string) => analyticsApi.deletePrecedence(id),
    onSuccess: () => {
      setActionError("");
      setActionNotice("Removed statutory precedence rule.");
      void client.invalidateQueries({ queryKey: queryKeys.admin.precedences() });
    },
    onError: (err) => {
      setActionError(err instanceof Error ? err.message : "Could not remove statutory precedence rule.");
    },
  });

  function resetForms() {
    setOverrideQuery("");
    setOverrideReply("");
    setPrecedenceTopic("");
    setPrecedenceRule("");
    setStatuteRef("");
    setTombstoneCited(true);
    setAdminNote("");
  }

  function startVerify(d: DiscrepancyRecord) {
    setVerifyingId(d.id);
    setDismissingId(null);
    setOverrideQuery(d.user_correction || "");
    setOverrideReply("");
    setPrecedenceTopic(d.discrepancy_type.replace("_", " ").toUpperCase());
    setPrecedenceRule(d.user_correction || "");
    setStatuteRef("");
    setTombstoneCited(true);
    setAdminNote("Verified legal change");
  }

  function handleConfirmVerify(d: DiscrepancyRecord) {
    const payload: VerifyDiscrepancyPayload = {
      admin_note: adminNote,
      create_override: Boolean(overrideQuery.trim() && overrideReply.trim()),
      override_query: overrideQuery.trim(),
      override_reply: overrideReply.trim(),
      precedence_topic: precedenceTopic.trim(),
      precedence_rule: precedenceRule.trim(),
      statute_reference: statuteRef.trim(),
      tombstone_sources: tombstoneCited ? d.cited_sources : [],
    };
    verifyMutation.mutate({ id: d.id, body: payload });
  }

  const reports = useMemo(() => data?.discrepancies ?? [], [data]);
  const stats = data?.stats ?? { total: 0, pending: 0, verified: 0, dismissed: 0 };
  const canEdit = !who || who.role === "ura_admin";

  return (
    <OpsPage
      eyebrow="Configure"
      title="Knowledge & Bug Reports"
      description="Taxpayer-reported factual inaccuracies, outdated laws, and automated verification."
      actions={
        <>
          <Freshness updatedAt={dataUpdatedAt} isFetching={isFetching} onRefresh={() => void refetch()} />
          <button
            type="button"
            className="ops-btn is-sm is-ghost"
            disabled={reports.length === 0}
            onClick={() => downloadCsv(discrepanciesToCsv(reports), csvFilename("knowledge-discrepancies"))}
          >
            <DownloadIcon />
            Export {reports.length} as CSV
          </button>
        </>
      }
    >
      {!canEdit ? (
        <div className="ops-note" role="note">
          <span className="ops-note-mark" aria-hidden="true">
            <InfoCircleIcon />
          </span>
          <div>
            <p className="ops-note-title">Read-only for your role</p>
            <p className="ops-note-body">
              Officers and auditors can inspect reported inaccuracies, active statutory precedences, and corpus
              tombstones. Verifying reports and deploying corpus hotfixes stays with administrators.
            </p>
          </div>
        </div>
      ) : null}

      <section aria-label="Discrepancy summary">
        <div className="ops-stat-grid ov-stats">
          <StatCard label="Total Discrepancies" value={stats.total} />
          <StatCard label="Pending Review" value={stats.pending} />
          <StatCard label="Verified & Patched" value={stats.verified} />
          <StatCard label="Dismissed / N/A" value={stats.dismissed} />
        </div>
      </section>

      {actionError ? (
        <div className="ops-error" role="alert" style={{ marginBottom: "var(--ops-space-4)" }}>
          <span className="ops-note-mark" aria-hidden="true">
            <AlertTriangleIcon />
          </span>
          <div style={{ flex: 1 }}>
            <p className="ops-error-title">Action could not be completed</p>
            <p className="ops-error-body">{actionError}</p>
          </div>
          <button
            type="button"
            className="ops-icon-btn"
            onClick={() => setActionError("")}
            aria-label="Dismiss error"
          >
            <CloseIcon />
          </button>
        </div>
      ) : null}

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

      <OpsPanel
        id="reported-inaccuracies"
        title="Reported Inaccuracies"
        note="Conversational pushbacks detected from taxpayer turns with cited evidence."
        end={
          <div className="ops-segmented" role="group" aria-label="Status filter">
            {STATUS_OPTIONS.map((opt) => (
              <button
                key={opt.label}
                type="button"
                aria-pressed={statusFilter === opt.value}
                className={statusFilter === opt.value ? "is-active" : undefined}
                onClick={() => setStatusFilter(opt.value)}
              >
                {opt.label}
              </button>
            ))}
          </div>
        }
      >
        {isLoading ? <SkeletonRows rows={4} height={96} /> : null}
        {error ? (
          <ErrorState
            title="Error"
            body="Failed to load discrepancy reports"
            onRetry={() => void refetch()}
          />
        ) : null}
        {!isLoading && !error && reports.length === 0 ? (
          <EmptyState
            title="No discrepancy reports found"
            body="When taxpayers challenge bot answers with updated facts or rates, reports appear here."
          />
        ) : null}

        {reports.length > 0 ? (
          <ul className="disc-list" aria-label="Discrepancy reports">
            {reports.map((d) => (
              <li key={d.id} className="disc-card">
                <div className="disc-head">
                  <div className="disc-head-left">
                    <strong className="disc-id">Report #{d.id.slice(-8)}</strong>
                    <span className={`ops-chip ${d.priority === "high" ? "is-danger" : ""}`}>
                      {d.priority.toUpperCase()}
                    </span>
                    <span className="ops-chip is-info">{d.discrepancy_type}</span>
                    {d.frequency_count > 1 ? (
                      <span className="ops-chip is-warn">Reported {d.frequency_count}x</span>
                    ) : null}
                  </div>
                  <span className="disc-when">
                    {new Date(d.created_at * 1000).toLocaleString()}
                  </span>
                </div>

                <div className="disc-diff">
                  <div className="disc-box disc-box-bot">
                    <span className="disc-box-label">Challenged Bot Statement</span>
                    <p className="disc-box-text">{d.bot_statement}</p>
                    {d.cited_sources.length > 0 ? (
                      <span className="disc-box-sources">
                        Cited: {d.cited_sources.join(", ")}
                      </span>
                    ) : null}
                  </div>

                  <div className="disc-box disc-box-user">
                    <span className="disc-box-label">Taxpayer Correction</span>
                    <p className="disc-box-text">{d.user_correction}</p>
                  </div>
                </div>

                {d.admin_note ? (
                  <p className="disc-note">
                    <strong>Admin Note:</strong> {d.admin_note}
                  </p>
                ) : null}

                {d.status === "pending" && canEdit && verifyingId !== d.id && dismissingId !== d.id ? (
                  <div className="disc-actions">
                    <button
                      type="button"
                      className="ops-btn is-primary is-sm"
                      onClick={() => startVerify(d)}
                    >
                      Verify &amp; Hotfix
                    </button>
                    <button
                      type="button"
                      className="ops-btn is-ghost is-sm"
                      onClick={() => {
                        setDismissingId(d.id);
                        setAdminNote("Taxpayer misunderstanding / Law unchanged");
                      }}
                    >
                      Dismiss (Doesn&apos;t Apply)
                    </button>
                  </div>
                ) : null}

                {verifyingId === d.id ? (
                  <div className="disc-drawer">
                    <h4>Verify Bug &amp; Deploy Hotfixes (Solutions B &amp; C)</h4>
                    <label className="ops-field">
                      <span className="ops-field-label">Answer Override Query (exact match bypass)</span>
                      <input
                        type="text"
                        className="ops-input"
                        value={overrideQuery}
                        onChange={(e) => setOverrideQuery(e.target.value)}
                      />
                    </label>
                    <label className="ops-field">
                      <span className="ops-field-label">Verified Override Answer</span>
                      <textarea
                        className="ops-textarea"
                        rows={2}
                        value={overrideReply}
                        onChange={(e) => setOverrideReply(e.target.value)}
                        placeholder="Enter verified statutory guidance..."
                      />
                    </label>
                    <div className="disc-drawer-grid">
                      <label className="ops-field">
                        <span className="ops-field-label">Active Statutory Precedence Topic (Solution C)</span>
                        <input
                          type="text"
                          className="ops-input"
                          value={precedenceTopic}
                          onChange={(e) => setPrecedenceTopic(e.target.value)}
                        />
                      </label>
                      <label className="ops-field">
                        <span className="ops-field-label">Statute Reference (e.g. Finance Act 2023)</span>
                        <input
                          type="text"
                          className="ops-input"
                          value={statuteRef}
                          onChange={(e) => setStatuteRef(e.target.value)}
                        />
                      </label>
                    </div>
                    <label className="ops-field">
                      <span className="ops-field-label">Precedence Rule Statement (Injected into LLM context)</span>
                      <input
                        type="text"
                        className="ops-input"
                        value={precedenceRule}
                        onChange={(e) => setPrecedenceRule(e.target.value)}
                      />
                    </label>
                    <label className="disc-check">
                      <input
                        type="checkbox"
                        checked={tombstoneCited}
                        onChange={(e) => setTombstoneCited(e.target.checked)}
                      />
                      <span>Tombstone cited chunk / sources in Qdrant (Solution B)</span>
                    </label>
                    <div className="disc-actions">
                      <button
                        type="button"
                        className="ops-btn is-primary is-sm"
                        onClick={() => handleConfirmVerify(d)}
                        disabled={verifyMutation.isPending}
                      >
                        {verifyMutation.isPending ? "Applying..." : "Confirm & Apply Hotfixes"}
                      </button>
                      <button
                        type="button"
                        className="ops-btn is-ghost is-sm"
                        onClick={() => {
                          setVerifyingId(null);
                          resetForms();
                        }}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : null}

                {dismissingId === d.id ? (
                  <div className="disc-drawer">
                    <label className="ops-field">
                      <span className="ops-field-label">Reason for dismissal</span>
                      <input
                        type="text"
                        className="ops-input"
                        value={adminNote}
                        onChange={(e) => setAdminNote(e.target.value)}
                      />
                    </label>
                    <div className="disc-actions">
                      <button
                        type="button"
                        className="ops-btn is-primary is-sm"
                        onClick={() => dismissMutation.mutate({ id: d.id, note: adminNote })}
                        disabled={dismissMutation.isPending}
                      >
                        {dismissMutation.isPending ? "Dismissing..." : "Confirm Dismissal"}
                      </button>
                      <button
                        type="button"
                        className="ops-btn is-ghost is-sm"
                        onClick={() => {
                          setDismissingId(null);
                          resetForms();
                        }}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
      </OpsPanel>

      <OpsPanel
        id="active-precedences"
        title="Active Statutory Precedences (Solution C)"
        note="Prompt-injected legal rules that strictly supersede older retrieved passages across all queries."
        bare
      >
        {precData?.precedences && precData.precedences.length > 0 ? (
          <ul className="ov-list">
            {precData.precedences.map((p) => (
              <li key={p.id} className="ov-list-item">
                <div>
                  <p className="ov-list-title">{p.topic}</p>
                  <p className="ov-list-body">
                    {p.rule_statement}
                    {p.statute_reference ? <span className="ops-cell-sub"> ({p.statute_reference})</span> : null}
                  </p>
                </div>
                {canEdit ? (
                  <button
                    type="button"
                    className="ops-btn is-sm is-ghost"
                    disabled={deletePrecedenceMutation.isPending}
                    onClick={() => deletePrecedenceMutation.mutate(p.id)}
                  >
                    Delete
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="ov-empty">No active precedences currently registered.</p>
        )}
      </OpsPanel>

      <OpsPanel
        id="corpus-tombstones"
        title="Corpus Tombstones (Solution B)"
        note="Outdated document passages filtered out from retrieval results across all queries."
        bare
      >
        {tombData?.tombstones && tombData.tombstones.length > 0 ? (
          <ul className="ov-list">
            {tombData.tombstones.map((t) => (
              <li key={t.id} className="ov-list-item">
                <div>
                  <p className="ov-list-title">
                    <code>{t.source_uri || t.chunk_id}</code>
                  </p>
                  {t.reason ? <p className="ov-list-body">{t.reason}</p> : null}
                </div>
                {canEdit ? (
                  <button
                    type="button"
                    className="ops-btn is-sm is-ghost"
                    disabled={deleteTombstoneMutation.isPending}
                    onClick={() => deleteTombstoneMutation.mutate(t.id)}
                  >
                    Remove
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="ov-empty">No corpus tombstones currently active.</p>
        )}
      </OpsPanel>
    </OpsPage>
  );
}

export default function AdminDiscrepanciesPage() {
  return (
    <StaffGuard current="/admin/discrepancies" requireRoles={["ura_staff", "ura_admin", "ura_auditor"]}>
      {(who) => <DiscrepanciesWorkbench who={who} />}
    </StaffGuard>
  );
}

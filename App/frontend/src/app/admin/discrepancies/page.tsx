"use client";

/**
 * Knowledge Discrepancy & Bug Reporting workbench.
 * Allows URA administrators to triage taxpayer-reported inaccuracies,
 * verify real statutory/procedural bugs with 1-click hotfixes
 * (Answer Overrides, Solution C Statutory Precedences, Solution B Chunk Tombstones),
 * or dismiss invalid reports.
 */

import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import StaffGuard, { type StaffIdentity } from "../../../components/StaffGuard";
import { OpsPage, OpsPanel } from "../../../components/ops/OpsPage";
import { StatCard } from "../../../components/ops/StatCard";
import { EmptyState, ErrorState, SkeletonRows } from "../../../components/ops/States";
import {
  analyticsApi,
  type DiscrepancyRecord,
  type VerifyDiscrepancyPayload,
} from "../../../services/analyticsApi";
import { queryKeys } from "../../../lib/queryKeys";
import "../admin.css";

function DiscrepanciesWorkbench({ who }: { who: StaffIdentity }) {
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

  const { data, isLoading, error, refetch } = useQuery({
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
    onSuccess: () => {
      setVerifyingId(null);
      resetForms();
      client.invalidateQueries({ queryKey: queryKeys.admin.discrepancies() });
      client.invalidateQueries({ queryKey: queryKeys.admin.overrides() });
      client.invalidateQueries({ queryKey: queryKeys.admin.precedences() });
      client.invalidateQueries({ queryKey: queryKeys.admin.tombstones() });
    },
  });

  const dismissMutation = useMutation({
    mutationFn: (payload: { id: string; note: string }) =>
      analyticsApi.dismissDiscrepancy(payload.id, { admin_note: payload.note, reason: payload.note }),
    onSuccess: () => {
      setDismissingId(null);
      resetForms();
      client.invalidateQueries({ queryKey: queryKeys.admin.discrepancies() });
    },
  });

  const deleteTombstoneMutation = useMutation({
    mutationFn: (id: string) => analyticsApi.deleteTombstone(id),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.admin.tombstones() }),
  });

  const deletePrecedenceMutation = useMutation({
    mutationFn: (id: string) => analyticsApi.deletePrecedence(id),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.admin.precedences() }),
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
  const canEdit = who.role === "ura_admin";

  return (
    <OpsPage
      title="Knowledge & Bug Reports"
      description="Taxpayer-reported factual inaccuracies, outdated laws, and automated verification."
      actions={
        <button
          type="button"
          className="ops-btn ops-btn-secondary"
          onClick={() => refetch()}
          disabled={isLoading}
        >
          Refresh
        </button>
      }
    >
      <div className="ops-stats-grid">
        <StatCard label="Total Discrepancies" value={stats.total} />
        <StatCard label="Pending Review" value={stats.pending} />
        <StatCard label="Verified & Patched" value={stats.verified} />
        <StatCard label="Dismissed / N/A" value={stats.dismissed} />
      </div>

      <OpsPanel
        title="Reported Inaccuracies"
        note="Conversational pushbacks detected from taxpayer turns with cited evidence."
        end={
          <div className="ops-segmented-control" role="group" aria-label="Status filter">
            {["all", "pending", "verified", "dismissed"].map((s) => (
              <button
                key={s}
                type="button"
                className={`ops-pill ${statusFilter === (s === "all" ? "" : s) ? "active" : ""}`}
                onClick={() => setStatusFilter(s === "all" ? "" : s)}
              >
                {s.toUpperCase()}
              </button>
            ))}
          </div>
        }
      >
        {isLoading && <SkeletonRows rows={4} />}
        {error && <ErrorState title="Error" body="Failed to load discrepancy reports" onRetry={() => refetch()} />}
        {!isLoading && !error && reports.length === 0 && (
          <EmptyState
            title="No discrepancy reports found"
            body="When taxpayers challenge bot answers with updated facts or rates, reports appear here."
          />
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          {reports.map((d) => (
            <div
              key={d.id}
              style={{
                border: "1px solid var(--border-color, #e2e8f0)",
                borderRadius: "8px",
                padding: "1rem",
                background: "var(--card-bg, #ffffff)",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.5rem" }}>
                <div>
                  <strong style={{ fontSize: "0.95rem" }}>Report #{d.id.slice(-8)}</strong>
                  <span
                    style={{
                      marginLeft: "0.5rem",
                      fontSize: "0.75rem",
                      padding: "0.15rem 0.4rem",
                      borderRadius: "4px",
                      background: d.priority === "high" ? "#fee2e2" : "#f1f5f9",
                      color: d.priority === "high" ? "#991b1b" : "#475569",
                      fontWeight: 600,
                    }}
                  >
                    {d.priority.toUpperCase()}
                  </span>
                  <span
                    style={{
                      marginLeft: "0.5rem",
                      fontSize: "0.75rem",
                      padding: "0.15rem 0.4rem",
                      borderRadius: "4px",
                      background: "#e0f2fe",
                      color: "#0369a1",
                    }}
                  >
                    {d.discrepancy_type}
                  </span>
                  {d.frequency_count > 1 && (
                    <span
                      style={{
                        marginLeft: "0.5rem",
                        fontSize: "0.75rem",
                        padding: "0.15rem 0.4rem",
                        borderRadius: "4px",
                        background: "#fef3c7",
                        color: "#92400e",
                        fontWeight: 600,
                      }}
                    >
                      Reported {d.frequency_count}x
                    </span>
                  )}
                </div>
                <div style={{ fontSize: "0.8rem", color: "var(--text-3, #64748b)" }}>
                  {new Date(d.created_at * 1000).toLocaleString()}
                </div>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem", margin: "0.75rem 0" }}>
                <div style={{ padding: "0.75rem", background: "#fef2f2", borderRadius: "6px", borderLeft: "3px solid #ef4444" }}>
                  <div style={{ fontSize: "0.75rem", fontWeight: 700, color: "#991b1b", marginBottom: "0.25rem" }}>
                    CHALLENGED BOT STATEMENT
                  </div>
                  <div style={{ fontSize: "0.85rem", color: "#1e293b" }}>{d.bot_statement}</div>
                  {d.cited_sources.length > 0 && (
                    <div style={{ fontSize: "0.75rem", color: "#64748b", marginTop: "0.4rem" }}>
                      Cited: {d.cited_sources.join(", ")}
                    </div>
                  )}
                </div>

                <div style={{ padding: "0.75rem", background: "#f0fdf4", borderRadius: "6px", borderLeft: "3px solid #22c55e" }}>
                  <div style={{ fontSize: "0.75rem", fontWeight: 700, color: "#166534", marginBottom: "0.25rem" }}>
                    TAXPAYER CORRECTION
                  </div>
                  <div style={{ fontSize: "0.85rem", color: "#1e293b" }}>{d.user_correction}</div>
                </div>
              </div>

              {d.admin_note && (
                <div style={{ fontSize: "0.8rem", color: "var(--text-2, #334155)", margin: "0.4rem 0" }}>
                  <strong>Admin Note:</strong> {d.admin_note}
                </div>
              )}

              {/* Action buttons */}
              {d.status === "pending" && canEdit && verifyingId !== d.id && dismissingId !== d.id && (
                <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.75rem" }}>
                  <button
                    type="button"
                    className="ops-btn ops-btn-primary"
                    style={{ fontSize: "0.8rem", padding: "0.3rem 0.75rem" }}
                    onClick={() => startVerify(d)}
                  >
                    Verify & Hotfix
                  </button>
                  <button
                    type="button"
                    className="ops-btn ops-btn-secondary"
                    style={{ fontSize: "0.8rem", padding: "0.3rem 0.75rem" }}
                    onClick={() => { setDismissingId(d.id); setAdminNote("Taxpayer misunderstanding / Law unchanged"); }}
                  >
                    Dismiss (Doesn&apos;t Apply)
                  </button>
                </div>
              )}

              {/* Verify Hotfix Drawer / Form */}
              {verifyingId === d.id && (
                <div
                  style={{
                    marginTop: "0.75rem",
                    padding: "1rem",
                    background: "#f8fafc",
                    border: "1px solid #cbd5e1",
                    borderRadius: "6px",
                  }}
                >
                  <h4 style={{ margin: "0 0 0.5rem 0", fontSize: "0.9rem" }}>
                    Verify Bug & Deploy Hotfixes (Solutions B & C)
                  </h4>
                  <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
                    <label style={{ fontSize: "0.8rem", fontWeight: 600 }}>
                      Answer Override Query (exact match bypass):
                      <input
                        type="text"
                        className="ops-input"
                        style={{ width: "100%", marginTop: "0.2rem" }}
                        value={overrideQuery}
                        onChange={(e) => setOverrideQuery(e.target.value)}
                      />
                    </label>
                    <label style={{ fontSize: "0.8rem", fontWeight: 600 }}>
                      Verified Override Answer:
                      <textarea
                        className="ops-input"
                        rows={2}
                        style={{ width: "100%", marginTop: "0.2rem" }}
                        value={overrideReply}
                        onChange={(e) => setOverrideReply(e.target.value)}
                        placeholder="Enter verified statutory guidance..."
                      />
                    </label>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.5rem" }}>
                      <label style={{ fontSize: "0.8rem", fontWeight: 600 }}>
                        Active Statutory Precedence Topic (Solution C):
                        <input
                          type="text"
                          className="ops-input"
                          style={{ width: "100%", marginTop: "0.2rem" }}
                          value={precedenceTopic}
                          onChange={(e) => setPrecedenceTopic(e.target.value)}
                        />
                      </label>
                      <label style={{ fontSize: "0.8rem", fontWeight: 600 }}>
                        Statute Reference (e.g. Finance Act 2023):
                        <input
                          type="text"
                          className="ops-input"
                          style={{ width: "100%", marginTop: "0.2rem" }}
                          value={statuteRef}
                          onChange={(e) => setStatuteRef(e.target.value)}
                        />
                      </label>
                    </div>
                    <label style={{ fontSize: "0.8rem", fontWeight: 600 }}>
                      Precedence Rule Statement (Injected into LLM context):
                      <input
                        type="text"
                        className="ops-input"
                        style={{ width: "100%", marginTop: "0.2rem" }}
                        value={precedenceRule}
                        onChange={(e) => setPrecedenceRule(e.target.value)}
                      />
                    </label>
                    <label style={{ fontSize: "0.8rem", display: "flex", alignItems: "center", gap: "0.4rem" }}>
                      <input
                        type="checkbox"
                        checked={tombstoneCited}
                        onChange={(e) => setTombstoneCited(e.target.checked)}
                      />
                      <span>Tombstone cited chunk / sources in Qdrant (Solution B)</span>
                    </label>
                    <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.5rem" }}>
                      <button
                        type="button"
                        className="ops-btn ops-btn-primary"
                        onClick={() => handleConfirmVerify(d)}
                        disabled={verifyMutation.isPending}
                      >
                        {verifyMutation.isPending ? "Applying..." : "Confirm & Apply Hotfixes"}
                      </button>
                      <button
                        type="button"
                        className="ops-btn ops-btn-secondary"
                        onClick={() => { setVerifyingId(null); resetForms(); }}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {/* Dismiss form */}
              {dismissingId === d.id && (
                <div style={{ marginTop: "0.75rem", padding: "0.75rem", background: "#f8fafc", borderRadius: "6px" }}>
                  <label style={{ fontSize: "0.8rem", fontWeight: 600 }}>
                    Reason for dismissal:
                    <input
                      type="text"
                      className="ops-input"
                      style={{ width: "100%", marginTop: "0.2rem" }}
                      value={adminNote}
                      onChange={(e) => setAdminNote(e.target.value)}
                    />
                  </label>
                  <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.5rem" }}>
                    <button
                      type="button"
                      className="ops-btn ops-btn-primary"
                      onClick={() => dismissMutation.mutate({ id: d.id, note: adminNote })}
                      disabled={dismissMutation.isPending}
                    >
                      {dismissMutation.isPending ? "Dismissing..." : "Confirm Dismissal"}
                    </button>
                    <button
                      type="button"
                      className="ops-btn ops-btn-secondary"
                      onClick={() => { setDismissingId(null); resetForms(); }}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      </OpsPanel>

      {/* Active Precedences (Solution C) */}
      <OpsPanel
        title="Active Statutory Precedences (Solution C)"
        note="Prompt-injected legal rules that strictly supersede older retrieved passages across all queries."
      >
        <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
          {precData?.precedences.map((p) => (
            <div
              key={p.id}
              style={{
                display: "flex",
                justifyContent: "space-between",
                padding: "0.6rem 0.8rem",
                borderRadius: "6px",
                border: "1px solid #e2e8f0",
                background: "#fafafa",
              }}
            >
              <div>
                <strong>{p.topic}</strong>: {p.rule_statement}
                {p.statute_reference && <span style={{ color: "#64748b" }}> ({p.statute_reference})</span>}
              </div>
              {canEdit && (
                <button
                  type="button"
                  className="ops-btn ops-btn-secondary"
                  style={{ fontSize: "0.75rem", padding: "0.2rem 0.5rem" }}
                  onClick={() => deletePrecedenceMutation.mutate(p.id)}
                >
                  Delete
                </button>
              )}
            </div>
          ))}
          {(!precData?.precedences || precData.precedences.length === 0) && (
            <div style={{ fontSize: "0.85rem", color: "#64748b" }}>No active precedences currently registered.</div>
          )}
        </div>
      </OpsPanel>

      {/* Corpus Tombstones (Solution B) */}
      <OpsPanel
        title="Corpus Tombstones (Solution B)"
        note="Outdated document passages filtered out from retrieval results across all queries."
      >
        <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
          {tombData?.tombstones.map((t) => (
            <div
              key={t.id}
              style={{
                display: "flex",
                justifyContent: "space-between",
                padding: "0.6rem 0.8rem",
                borderRadius: "6px",
                border: "1px solid #e2e8f0",
                background: "#fafafa",
              }}
            >
              <div>
                <code>{t.source_uri || t.chunk_id}</code>
                {t.reason && <span style={{ color: "#64748b" }}> — {t.reason}</span>}
              </div>
              {canEdit && (
                <button
                  type="button"
                  className="ops-btn ops-btn-secondary"
                  style={{ fontSize: "0.75rem", padding: "0.2rem 0.5rem" }}
                  onClick={() => deleteTombstoneMutation.mutate(t.id)}
                >
                  Remove
                </button>
              )}
            </div>
          ))}
          {(!tombData?.tombstones || tombData.tombstones.length === 0) && (
            <div style={{ fontSize: "0.85rem", color: "#64748b" }}>No corpus tombstones currently active.</div>
          )}
        </div>
      </OpsPanel>
    </OpsPage>
  );
}

export default function DiscrepanciesPage() {
  return (
    <StaffGuard current="/admin/discrepancies" requireRoles={["ura_staff", "ura_admin", "ura_auditor"]}>
      {(who) => <DiscrepanciesWorkbench who={who} />}
    </StaffGuard>
  );
}

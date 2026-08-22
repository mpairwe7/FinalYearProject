"use client";

/**
 * Staff answer-override CMS (G31). Exact-match only. Not a FAQ editor.
 *
 * The form here was the last unstyled surface in the console — bare `<label>`,
 * `<input>` and `<button>` elements inheriting nothing, inside a panel that had
 * no form rules at all. It is also the most consequential thing an administrator
 * can type: whatever goes in the reply box is served verbatim to a taxpayer who
 * asks that exact question. So it now says that next to the field, previews what
 * will be matched, and confirms before deleting.
 */
import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import StaffGuard, { type StaffIdentity } from "../../../components/StaffGuard";
import { OpsPage, OpsPanel } from "../../../components/ops/OpsPage";
import { EmptyState, ErrorState, SkeletonRows } from "../../../components/ops/States";
import { analyticsApi } from "../../../services/analyticsApi";
import "../admin.css";

function OverridesBoard({ who }: { who: StaffIdentity }) {
  const client = useQueryClient();
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["adminOverrides"],
    queryFn: () => analyticsApi.overrides(),
    staleTime: 10_000,
  });
  const [query, setQuery] = useState("");
  const [reply, setReply] = useState("");
  const [filter, setFilter] = useState("");
  const [confirming, setConfirming] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () => analyticsApi.putOverride({ query, reply }),
    onSuccess: () => {
      setQuery("");
      setReply("");
      client.invalidateQueries({ queryKey: ["adminOverrides"] });
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => analyticsApi.deleteOverride(id),
    onSuccess: () => {
      setConfirming(null);
      client.invalidateQueries({ queryKey: ["adminOverrides"] });
    },
  });
  const canEdit = who.role === "ura_admin";

  const overrides = useMemo(() => data?.overrides ?? [], [data]);
  const rows = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return overrides;
    return overrides.filter((row) =>
      `${row.match_query} ${row.reply}`.toLowerCase().includes(q),
    );
  }, [overrides, filter]);

  const ready = query.trim().length > 0 && reply.trim().length > 0;

  return (
    <OpsPage
      eyebrow="Configure"
      title="Answer overrides"
      description="Exact-match staff replies. These do not change the FAQ corpus, and they only take effect while FLAG_ANSWER_OVERRIDES is on."
      width="read"
      actions={
        overrides.length > 0 ? (
          <input
            type="search"
            className="ops-input ops-search"
            placeholder="Filter overrides…"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            aria-label="Filter overrides"
          />
        ) : null
      }
    >
      {canEdit ? (
        <OpsPanel
          id="new-override"
          title="Add an override"
          note="The taxpayer's question must match exactly, character for character. Anything close but not identical falls through to normal retrieval."
          className="ov-form-panel"
        >
          <form
            className="ov-form"
            onSubmit={(event) => {
              event.preventDefault();
              if (ready) save.mutate();
            }}
          >
            <label className="ops-field">
              <span className="ops-field-label">
                Taxpayer question
                <em className="ops-field-hint">Matched exactly, so paste it as they type it.</em>
              </span>
              <input
                className="ops-input"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="What is the VAT registration threshold?"
                required
              />
            </label>
            <label className="ops-field">
              <span className="ops-field-label">
                Staff reply
                <em className="ops-field-hint">
                  Served to the taxpayer word for word, with no retrieval and no citations added.
                </em>
              </span>
              <textarea
                className="ops-textarea"
                value={reply}
                onChange={(event) => setReply(event.target.value)}
                rows={4}
                placeholder="The annual VAT registration threshold is…"
                required
              />
            </label>
            <div className="ov-form-actions">
              <button type="submit" className="ops-btn is-primary" disabled={!ready || save.isPending}>
                {save.isPending ? "Saving…" : "Save override"}
              </button>
              {query || reply ? (
                <button
                  type="button"
                  className="ops-btn is-ghost"
                  onClick={() => {
                    setQuery("");
                    setReply("");
                  }}
                >
                  Clear
                </button>
              ) : null}
              {save.isError ? (
                <span className="ops-stat-hint" role="alert" style={{ color: "var(--ops-bad)" }}>
                  Could not save — try again.
                </span>
              ) : null}
              {save.isSuccess ? (
                <span className="ops-stat-hint" role="status" style={{ color: "var(--ops-good)" }}>
                  Saved.
                </span>
              ) : null}
            </div>
          </form>
        </OpsPanel>
      ) : (
        <div className="ops-note" role="note">
          <span className="ops-note-mark" aria-hidden="true">
            ⓘ
          </span>
          <div>
            <p className="ops-note-title">Read-only for your role</p>
            <p className="ops-note-body">
              Auditors can review overrides. Creating and removing them stays with administrators.
            </p>
          </div>
        </div>
      )}

      <OpsPanel
        id="override-list"
        title="Live overrides"
        end={<span className="ops-chip">{overrides.length}</span>}
        flush
      >
        {isLoading ? <SkeletonRows rows={3} height={72} /> : null}
        {error ? (
          <ErrorState body="The override store did not answer." onRetry={() => void refetch()} />
        ) : null}
        {!isLoading && !error && rows.length === 0 ? (
          <EmptyState
            title={filter ? "No override matches that" : "No overrides yet"}
            body={
              filter
                ? "Try part of the question, or clear the filter."
                : "Add one when a common question has a settled answer the assistant keeps getting almost right."
            }
          />
        ) : null}
        {rows.length > 0 ? (
          <ul className="ov-list">
            {rows.map((row) => (
              <li className="ov-list-item" key={row.id}>
                <div>
                  <p className="ov-list-title">{row.match_query}</p>
                  <p className="ov-list-body">{row.reply}</p>
                  <p className="ov-list-meta">
                    {row.created_by ? <span>Added by {row.created_by}</span> : null}
                    {row.source_url ? <span>{row.source_url}</span> : null}
                    {row.enabled === false ? <span className="ops-chip is-warn">disabled</span> : null}
                  </p>
                </div>
                {canEdit ? (
                  confirming === row.id ? (
                    <span className="ops-row-inline">
                      <button
                        type="button"
                        className="ops-btn is-danger is-sm"
                        disabled={remove.isPending}
                        onClick={() => remove.mutate(row.id)}
                      >
                        Confirm delete
                      </button>
                      <button
                        type="button"
                        className="ops-btn is-ghost is-sm"
                        onClick={() => setConfirming(null)}
                      >
                        Cancel
                      </button>
                    </span>
                  ) : (
                    <button
                      type="button"
                      className="ops-btn is-sm"
                      onClick={() => setConfirming(row.id)}
                      aria-label={`Delete the override for “${row.match_query}”`}
                    >
                      Delete
                    </button>
                  )
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
      </OpsPanel>
    </OpsPage>
  );
}

export default function OverridesPage() {
  return (
    <StaffGuard current="/admin/overrides" requireRoles={["ura_admin", "ura_auditor"]}>
      {(who) => <OverridesBoard who={who} />}
    </StaffGuard>
  );
}

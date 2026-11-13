"use client";

/**
 * Staff answer-override CMS (G31). Matched on the normalized question. Not a FAQ editor.
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
import { InfoCircleIcon } from "../../../components/ops/icons";
import { analyticsApi } from "../../../services/analyticsApi";
import { queryKeys } from "../../../lib/queryKeys";
import { normalizeOverrideQuery } from "../../../lib/overrides";
import "../admin.css";

function OverridesBoard({ who }: { who: StaffIdentity }) {
  const client = useQueryClient();
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: queryKeys.admin.overrides(),
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
      client.invalidateQueries({ queryKey: queryKeys.admin.overrides() });
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => analyticsApi.deleteOverride(id),
    onSuccess: () => {
      setConfirming(null);
      client.invalidateQueries({ queryKey: queryKeys.admin.overrides() });
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
  // G637: what the server will match on, and the live answer a save replaces.
  const matchKey = normalizeOverrideQuery(query);
  const live = matchKey ? overrides.find((row) => row.match_query === matchKey) : undefined;

  return (
    <OpsPage
      eyebrow="Configure"
      title="Answer overrides"
      description="Staff replies matched on the taxpayer's question. They apply to every taxpayer, do not change the FAQ corpus, and only take effect while FLAG_ANSWER_OVERRIDES is on."
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
          note="A question matches when it is the same as this one once case, punctuation and spacing are ignored. The assistant also tries the question as it reworded it and, for other languages, as machine-translated, so this answer can reach taxpayers writing in any of the five languages. Anything else falls through to normal retrieval."
          glass
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
                <em className="ops-field-hint">Case, punctuation and extra spaces are ignored.</em>
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
                  Served instead of a retrieved answer, with no citations. Taxpayers writing in another language receive a machine translation of it.
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
            {matchKey ? <p className="ov-match-key">Matched as: {matchKey}</p> : null}
            {live ? (
              <section className="ov-replaces" role="region" aria-label="Replaces a live answer">
                <p>
                  <strong>Replaces a live answer.</strong> An override for “{live.match_query}” is being served now:
                </p>
                <blockquote>{live.reply}</blockquote>
              </section>
            ) : null}
            <div className="ov-form-actions">
              <button type="submit" className="ops-btn is-primary" disabled={!ready || save.isPending}>
                {save.isPending ? "Saving…" : live ? "Replace the live answer" : "Save override"}
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
            <InfoCircleIcon />
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
        bare
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

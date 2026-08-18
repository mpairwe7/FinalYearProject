"use client";

/**
 * Staff answer-override CMS (G31). Exact-match only. Not a FAQ editor.
 */
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import StaffGuard, { type StaffIdentity } from "../../../components/StaffGuard";
import { analyticsApi } from "../../../services/analyticsApi";
import "../admin.css";

function OverridesBoard({ who }: { who: StaffIdentity }) {
  const client = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["adminOverrides"],
    queryFn: () => analyticsApi.overrides(),
    staleTime: 10_000,
  });
  const [query, setQuery] = useState("");
  const [reply, setReply] = useState("");
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
    onSuccess: () => client.invalidateQueries({ queryKey: ["adminOverrides"] }),
  });
  const canEdit = who.role === "ura_admin";

  return (
    <main className="ov-page" id="staff-main">
      <header className="ov-head">
        <div>
          <h1>Answer overrides</h1>
          <p className="ov-sub">
            Exact-match staff replies. Does not change the FAQ corpus. Used only
            when FLAG_ANSWER_OVERRIDES is on.
          </p>
        </div>
      </header>
      {isLoading ? <p className="ov-empty">Loading overrides…</p> : null}
      {error ? <p className="ov-empty">Could not load overrides.</p> : null}
      {canEdit ? (
        <form
          className="ov-panel"
          onSubmit={(e) => {
            e.preventDefault();
            if (query.trim() && reply.trim()) save.mutate();
          }}
        >
          <label>
            Taxpayer question
            <input value={query} onChange={(e) => setQuery(e.target.value)} required />
          </label>
          <label>
            Staff reply
            <textarea value={reply} onChange={(e) => setReply(e.target.value)} required rows={3} />
          </label>
          <button type="submit" disabled={save.isPending}>
            Save override
          </button>
        </form>
      ) : null}
      <ul className="ov-queue">
        {(data?.overrides ?? []).map((row) => (
          <li key={row.id}>
            <strong>{row.match_query}</strong>
            <p>{row.reply}</p>
            {canEdit ? (
              <button type="button" onClick={() => remove.mutate(row.id)}>
                Delete
              </button>
            ) : null}
          </li>
        ))}
      </ul>
    </main>
  );
}

export default function OverridesPage() {
  return (
    <StaffGuard current="/admin/overrides" requireRoles={["ura_admin", "ura_auditor"]}>
      {(who) => <OverridesBoard who={who} />}
    </StaffGuard>
  );
}

"use client";

/** Mock notification outbox (G14). Nothing is sent. */
import React from "react";
import { useQuery } from "@tanstack/react-query";
import StaffGuard from "../../../components/StaffGuard";
import { analyticsApi } from "../../../services/analyticsApi";
import "../admin.css";

function OutboxBoard() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["adminOutbox"],
    queryFn: () => analyticsApi.outbox(),
    staleTime: 10_000,
  });

  return (
    <main className="ov-page" id="staff-main">
      <header className="ov-head">
        <div>
          <h1>Notification outbox</h1>
          <p className="ov-sub">
            Email and SMS rows queued with provider=mock. This prototype does
            not send messages.
          </p>
        </div>
      </header>
      {isLoading ? <p className="ov-empty">Loading outbox…</p> : null}
      {error ? <p className="ov-empty">Could not load the outbox.</p> : null}
      <ul className="ov-queue">
        {(data?.items ?? []).map((row) => (
          <li key={row.id}>
            <strong>{row.channel}</strong>
            <p>
              {row.provider} · {row.status}
            </p>
          </li>
        ))}
      </ul>
      {data && data.items.length === 0 ? <p className="ov-empty">Outbox is empty.</p> : null}
    </main>
  );
}

export default function OutboxPage() {
  return (
    <StaffGuard current="/admin/outbox" requireRoles={["ura_admin", "ura_auditor"]}>
      {() => <OutboxBoard />}
    </StaffGuard>
  );
}

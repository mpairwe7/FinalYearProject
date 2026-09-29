"use client";

/**
 * Audit trail — who changed or read what, and proof the record is intact.
 *
 * For administrators and auditors. Reads the hash-chained audit ledger through
 * GET /v1/admin/audit/events, checks it through GET /v1/admin/audit/verify and
 * seals it on demand through POST /v1/admin/audit/seal.
 * Added after the QA audit of 2026-09-29 found the ledger existed, with a
 * verifier, but nobody could see it: an auditor could not answer "who resolved
 * this ticket" or "who read this taxpayer's transcript", nor show the record
 * had not been edited since.
 *
 * The integrity check comes first on purpose. A list of events is only
 * evidence if the chain behind it verifies; a broken chain is stated before
 * anything else on the page. Seals (a Merkle root plus the chain head, also
 * written to the log pipeline) are what catch a chain rewritten with every
 * fingerprint recomputed, so the seal status sits in the same panel; sealing
 * is paused while the record fails its check, because a new seal would fix
 * the altered record in place.
 */
import React, { useMemo, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import StaffGuard from "../../../components/StaffGuard";
import { OpsPage, OpsPanel, TableScroll } from "../../../components/ops/OpsPage";
import { PeriodPicker } from "../../../components/ops/Controls";
import { EmptyState, ErrorState, SkeletonRows } from "../../../components/ops/States";
import { analyticsApi } from "../../../services/analyticsApi";
import type { AuditVerification } from "../../../services/analyticsApi";
import { queryKeys } from "../../../lib/queryKeys";
import {
  AUDIT_FILTERS,
  auditEventsToCsv,
  checkCoverage,
  eventLabel,
  sealStatus,
  summarizePayload,
} from "../../../lib/auditTrail";
import "../admin.css";

const WHEN_FMT = new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" });

function when(ts: number | undefined): string {
  if (ts == null || !Number.isFinite(ts)) return "—";
  return WHEN_FMT.format(ts * 1000);
}

function shortHash(hash: string): string {
  return hash ? `${hash.slice(0, 10)}…` : "—";
}

/** Save *csv* as a file. Plain browser download; this page is not sandboxed. */
function downloadCsv(csv: string, filename: string): void {
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

interface SealControl {
  onSeal: () => void;
  pending: boolean;
  message: string | null;
}

function Integrity({ data, seal }: { data: AuditVerification; seal: SealControl }) {
  if (!data.valid) {
    const first = data.breaks[0];
    const sealBreak = data.anchor_breaks[0];
    return (
      <div className="ops-note" role="alert">
        <span className="ops-note-mark" aria-hidden="true">
          !
        </span>
        <div>
          <p className="ops-note-title">
            <span className="ops-chip is-danger">Record altered</span> The audit trail does not verify
          </p>
          <p className="ops-note-body">
            {first
              ? `Event #${first.seq} fails its check (${first.reason}). Everything from that event on cannot be relied on as evidence until the cause is found.`
              : null}
            {first && data.breaks.length > 1 ? ` ${data.breaks.length} events fail in total.` : null}
            {!first && sealBreak
              ? `Events #${sealBreak.first_seq} to #${sealBreak.last_seq} no longer match their seal (${sealBreak.reason}). Every fingerprint in that range was recomputed to hide the change, which takes direct access to the database.`
              : null}
            {!first && !sealBreak ? "One or more events fail their check." : null}
          </p>
          <p className="ops-note-body">
            Sealing is paused until the break is explained: a new seal would fix the altered record in
            place. See the audit-trail runbook for what to preserve.
          </p>
        </div>
      </div>
    );
  }
  return (
    <div className="ops-note" role="status">
      <span className="ops-note-mark" aria-hidden="true">
        ✓
      </span>
      <div>
        <p className="ops-note-title">
          <span className="ops-chip is-good">Record intact</span> {checkCoverage(data)}
        </p>
        <p className="ops-note-body">
          Every event&rsquo;s fingerprint covers what it says, who did it, when, and where it sits in the
          chain; an edited, deleted, reordered or re-attributed event would show here as a break. Latest
          fingerprint <code>{shortHash(data.head_hash)}</code>.
        </p>
        <p className="ops-note-body au-seal">
          <span>{sealStatus(data, when)}</span>
          <button
            type="button"
            className="ops-btn is-sm"
            onClick={seal.onSeal}
            disabled={seal.pending || data.unsealed_rows === 0}
          >
            {seal.pending ? "Sealing…" : "Seal now"}
          </button>
          {seal.message ? (
            <span className="ops-cell-sub" role="status">
              {seal.message}
            </span>
          ) : null}
        </p>
      </div>
    </div>
  );
}

function AuditBoard() {
  const [days, setDays] = useState(30);
  const [eventType, setEventType] = useState("staff.");
  const [actorDraft, setActorDraft] = useState("");
  const [actor, setActor] = useState("");
  const [sealMessage, setSealMessage] = useState<string | null>(null);
  const client = useQueryClient();

  const verify = useQuery({
    queryKey: queryKeys.admin.auditVerify(),
    queryFn: () => analyticsApi.auditVerify(),
    staleTime: 60_000,
  });

  const events = useInfiniteQuery({
    queryKey: queryKeys.admin.auditEvents(eventType, actor, days),
    queryFn: ({ pageParam }) =>
      analyticsApi.auditEvents({
        eventType,
        actor,
        since: Date.now() / 1000 - days * 86_400,
        beforeSeq: pageParam,
        limit: 50,
      }),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next_before_seq ?? undefined,
    staleTime: 15_000,
  });

  const seal = useMutation({
    mutationFn: () => analyticsApi.auditSeal(),
    onSuccess: (result) => {
      setSealMessage(
        result.sealed && result.anchor
          ? `Sealed #${result.anchor.first_seq} to #${result.anchor.last_seq}.`
          : "Nothing new to seal.",
      );
      void client.invalidateQueries({ queryKey: queryKeys.admin.audit() });
    },
    onError: () => setSealMessage("The seal was not recorded; try again."),
  });

  const rows = useMemo(() => events.data?.pages.flatMap((page) => page.events) ?? [], [events.data]);
  const recording = events.data?.pages[0]?.ledger_enabled ?? verify.data?.ledger_enabled;

  const exportCsv = () => {
    const stamp = new Date().toISOString().slice(0, 10);
    downloadCsv(auditEventsToCsv(rows), `audit-trail-${stamp}.csv`);
  };

  return (
    <OpsPage
      eyebrow="Observe"
      title="Audit trail"
      description="Who changed a ticket, a feature switch or a staff-written answer, who opened a taxpayer's transcript, and what the assistant did — in a record that proves it has not been edited."
      actions={<PeriodPicker days={days} onChange={setDays} />}
      toolbar={
        <form
          className="au-filters"
          onSubmit={(event) => {
            event.preventDefault();
            setActor(actorDraft.trim());
          }}
        >
          <label className="ops-field" htmlFor="audit-type">
            <span className="ops-field-label">Show</span>
            <select
              id="audit-type"
              className="ops-select"
              value={eventType}
              onChange={(event) => setEventType(event.target.value)}
            >
              {AUDIT_FILTERS.map((filter) => (
                <option key={filter.value} value={filter.value}>
                  {filter.label}
                </option>
              ))}
            </select>
          </label>
          <label className="ops-field" htmlFor="audit-actor">
            <span className="ops-field-label">By person</span>
            <input
              id="audit-actor"
              className="ops-input"
              value={actorDraft}
              onChange={(event) => setActorDraft(event.target.value)}
              placeholder="User id, then Enter"
              maxLength={128}
            />
          </label>
          <button type="submit" className="ops-btn is-sm">
            Apply
          </button>
          <button
            type="button"
            className="ops-btn is-sm is-ghost"
            onClick={exportCsv}
            disabled={rows.length === 0}
          >
            Export {rows.length} as CSV
          </button>
        </form>
      }
    >
      {verify.data ? (
        <Integrity
          data={verify.data}
          seal={{ onSeal: () => seal.mutate(), pending: seal.isPending, message: sealMessage }}
        />
      ) : null}
      {verify.isError ? (
        <ErrorState
          title="The integrity check did not answer"
          body="The events below cannot be shown to be unaltered until the check runs."
          onRetry={() => void verify.refetch()}
        />
      ) : null}

      {recording === false ? (
        <div className="ops-note" role="note">
          <span className="ops-note-mark" aria-hidden="true">
            ⓘ
          </span>
          <div>
            <p className="ops-note-title">This deployment is not recording new events</p>
            <p className="ops-note-body">
              The <code>audit_ledger</code> switch is off, so staff actions and transcript reads
              are not being written. Production refuses to start without it; on this deployment
              the list below holds only what was recorded before.
            </p>
          </div>
        </div>
      ) : null}

      <OpsPanel id="audit-events" title="Events, newest first" flush bare>
        {events.isLoading ? <SkeletonRows rows={5} height={44} /> : null}
        {events.isError ? (
          <ErrorState body="The audit trail did not answer." onRetry={() => void events.refetch()} />
        ) : null}
        {!events.isLoading && !events.isError && rows.length === 0 ? (
          <EmptyState
            title="Nothing recorded for these filters"
            body="Widen the period or choose Everything. Staff actions appear here as soon as they happen."
          />
        ) : null}
        {rows.length > 0 ? (
          <TableScroll label="Audit events">
            <table className="ops-table">
              <thead>
                <tr>
                  <th scope="col">When</th>
                  <th scope="col">What happened</th>
                  <th scope="col">Who</th>
                  <th scope="col">Details</th>
                  <th scope="col">#</th>
                  <th scope="col">Fingerprint</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((event) => (
                  <tr key={event.event_id}>
                    <td className="au-when">{when(event.ts)}</td>
                    <td>
                      <span className="ops-cell-strong">{eventLabel(event.event_type)}</span>
                      <span className="ops-cell-sub au-type">{event.event_type}</span>
                    </td>
                    <td>
                      <span>{event.actor || "—"}</span>
                      {typeof event.payload?.actor_role === "string" ? (
                        <span className="ops-cell-sub">{event.payload.actor_role.replace("ura_", "")}</span>
                      ) : null}
                    </td>
                    <td>
                      <span className="ops-cell-clamp ops-cell-sub">{summarizePayload(event.payload)}</span>
                    </td>
                    <td className="au-num">{event.seq}</td>
                    <td>
                      <code title={event.row_hash}>{shortHash(event.row_hash)}</code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
        ) : null}
        {events.hasNextPage ? (
          <div className="au-more">
            <button
              type="button"
              className="ops-btn is-sm"
              onClick={() => void events.fetchNextPage()}
              disabled={events.isFetchingNextPage}
            >
              {events.isFetchingNextPage ? "Loading…" : "Show older events"}
            </button>
          </div>
        ) : null}
      </OpsPanel>
    </OpsPage>
  );
}

export default function AuditTrailPage() {
  return (
    <StaffGuard current="/admin/audit" requireRoles={["ura_admin", "ura_auditor"]}>
      {() => <AuditBoard />}
    </StaffGuard>
  );
}

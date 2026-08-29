"use client";

/**
 * Staff ticket console — every status, every team, a case to work.
 *
 * The escalation backend already carries everything an officer needs.
 * This page is the shared workbench: claim, brief, full transcript,
 * taxpayer reply vs internal note, then resolve.
 *
 * Two decisions that stay load-bearing:
 * - The transcript is shown in full and unedited.
 * - `officer_reply` and `staff_note` are separate inputs.
 *
 * What the redesign changed: the filter row was fourteen identical pills in a
 * single wrap with two hairline separators doing all the grouping work, and no
 * way to tell what was currently in effect other than reading all fourteen. Now
 * status is a segmented control (it is one choice), priority and team are
 * labelled groups, and whatever is filtered is summarised in one line with a
 * single control to clear it.
 */
import React, { useMemo } from "react";
import StaffGuard, { type StaffIdentity } from "../../../components/StaffGuard";
import { OpsPage } from "../../../components/ops/OpsPage";
import { KeyHint } from "../../../components/ops/Controls";
import {
  EmptyState,
  ErrorState,
  Skeleton,
  SkeletonRows,
} from "../../../components/ops/States";
import { QueueRow } from "../../../components/staff/QueueRow";
import { TicketCase } from "../../../components/staff/TicketCase";
import {
  useTicket,
  useTicketQueueFull,
  useTicketSla,
  useUpdateTicket,
} from "../../../hooks/useAnalyticsDashboard";
import {
  formatDuration,
  officerHandle,
  PRIORITIES,
  STATUSES,
  STATUS_LABEL,
  ticketMatchesQuery,
  useQueueHotkeys,
  useQueueView,
} from "../../../lib/ticketUi";
import "./tickets.css";

export function StaffTicketQueue({ who }: { who?: StaffIdentity }) {
  const [view, setView] = useQueueView();
  const {
    data: queue,
    isLoading,
    error,
    refetch,
  } = useTicketQueueFull(view.status, view.priority, view.team);
  const { data: sla } = useTicketSla(30);
  const update = useUpdateTicket();

  const tickets = useMemo(() => {
    const rows = queue?.tickets ?? [];
    return rows.filter((ticket) => ticketMatchesQuery(ticket, view.q));
  }, [queue, view.q]);

  const teams = queue?.teams ?? [];
  const selected = view.ticket || null;
  const handle = officerHandle(who);

  const {
    data: detail,
    isLoading: detailLoading,
    error: detailError,
  } = useTicket(selected);

  const select = (id: string | null) => setView({ ticket: id || "" });

  useQueueHotkeys({
    ids: tickets.map((ticket) => ticket.id),
    selectedId: selected,
    onSelect: select,
    onAssignMe:
      selected && handle
        ? () =>
            update.mutate({
              id: selected,
              patch: { assignee: handle, status: "assigned" },
            })
        : undefined,
    replySelector: "[data-ticket-reply]",
    searchSelector: "[data-ticket-search]",
  });

  const narrowed = Boolean(view.priority || view.team || view.q);

  return (
    <OpsPage
      eyebrow="Work"
      title="Escalation queue"
      description="Urgent first, then longest waiting. Claim a case, read the brief, reply to the taxpayer, keep notes internal."
      actions={
        sla ? (
          // The scroll lives on a wrapper, not on the <dl>. Putting
          // role="group" on the list itself to make it focusable stripped its
          // implicit list role, and every <dt>/<dd> inside was then "not
          // contained by a dl" — ten new axe failures in place of one.
          <div
            className="sla-scroll"
            tabIndex={0}
            role="group"
            aria-label="Service levels over the last 30 days"
          >
            <dl className="sla-strip">
              <div>
                <dt>Awaiting first response</dt>
                <dd
                  className={sla.awaiting_first_response > 0 ? "is-warn" : ""}
                >
                  {sla.awaiting_first_response}
                </dd>
              </div>
              <div>
                <dt>Median response</dt>
                <dd>{formatDuration(sla.median_response_seconds)}</dd>
              </div>
              <div>
                <dt>Median resolution</dt>
                <dd>{formatDuration(sla.median_resolution_seconds)}</dd>
              </div>
              <div>
                <dt>Past 24h SLA</dt>
                <dd className={(sla.breaching ?? 0) > 0 ? "is-warn" : ""}>
                  {sla.breaching ?? 0}
                </dd>
              </div>
              <div>
                <dt>Awaiting next reply</dt>
                <dd>{sla.awaiting_next_response ?? 0}</dd>
              </div>
            </dl>
          </div>
        ) : null
      }
      toolbar={
        <>
          {/* Status is one choice out of four, so it is a segmented control
              rather than four pills that look like independent switches. */}
          <div className="ops-segmented" role="group" aria-label="Status">
            {STATUSES.map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={view.status === value}
                onClick={() => setView({ status: value, ticket: "" })}
              >
                {STATUS_LABEL[value]}
              </button>
            ))}
          </div>

          <span className="ops-toolbar-sep" aria-hidden="true" />

          <span
            className="tickets-filter-group"
            role="group"
            aria-label="Priority"
          >
            <span className="tickets-filter-label">Priority</span>
            <button
              type="button"
              className={`ops-filter${view.priority === "" ? " is-active" : ""}`}
              aria-pressed={view.priority === ""}
              aria-label="All priorities"
              onClick={() => setView({ priority: "" })}
            >
              any
            </button>
            {PRIORITIES.map((value) => (
              <button
                key={value}
                type="button"
                className={`ops-filter${view.priority === value ? " is-active" : ""}`}
                aria-pressed={view.priority === value}
                onClick={() => setView({ priority: value })}
              >
                {value}
              </button>
            ))}
          </span>

          {teams.length > 0 ? (
            <>
              <span className="ops-toolbar-sep" aria-hidden="true" />
              <span
                className="tickets-filter-group"
                role="group"
                aria-label="Team"
              >
                <span className="tickets-filter-label">Team</span>
                <button
                  type="button"
                  className={`ops-filter${view.team === "" ? " is-active" : ""}`}
                  aria-pressed={view.team === ""}
                  aria-label="All teams"
                  onClick={() => setView({ team: "" })}
                >
                  any
                </button>
                {teams.map((value) => (
                  <button
                    key={value}
                    type="button"
                    className={`ops-filter${view.team === value ? " is-active" : ""}`}
                    aria-pressed={view.team === value}
                    onClick={() => setView({ team: value })}
                  >
                    {value.replace(/_/g, " ")}
                  </button>
                ))}
              </span>
            </>
          ) : null}

          <span className="ops-toolbar-end tickets-toolbar-search">
            <input
              type="search"
              className="ops-input ops-search"
              data-ticket-search="1"
              /* The long form clipped to "Search reason, taxpayer, ass…" in the
                 field's own width; the accessible name below carries the full
                 meaning for anyone who needs it. */
              placeholder="Search the queue…"
              value={view.q}
              onChange={(event) => setView({ q: event.target.value })}
              aria-label="Search tickets"
            />
          </span>
        </>
      }
    >
      <div className="tickets-meta">
        <span className="tickets-count" aria-live="polite">
          {isLoading ? (
            <Skeleton width={64} height={13} />
          ) : (
            <>
              {`${tickets.length} ${tickets.length === 1 ? "case" : "cases"}`}
              {narrowed ? " matching" : ""}
            </>
          )}
        </span>
        {narrowed ? (
          <button
            type="button"
            className="ops-btn is-ghost is-sm"
            onClick={() => setView({ priority: "", team: "", q: "" })}
          >
            Clear filters
          </button>
        ) : null}
        <span className="ops-hints tickets-hints">
          <KeyHint keys={["j", "k"]}>move</KeyHint>
          <KeyHint keys={["/"]}>search</KeyHint>
          <KeyHint keys={["r"]}>reply</KeyHint>
          <KeyHint keys={["a"]}>assign to me</KeyHint>
        </span>
      </div>

      <div className={`tickets-body${selected ? " is-open" : ""}`}>
        <section className="tickets-list" aria-label="Ticket queue">
          {isLoading ? <SkeletonRows rows={7} height={62} /> : null}
          {error ? (
            <ErrorState
              body="The queue did not load."
              onRetry={() => void refetch()}
            />
          ) : null}
          {!isLoading && !error && tickets.length === 0 ? (
            <EmptyState
              title="Nothing matches"
              body={
                narrowed
                  ? "No case in this status matches the current filters."
                  : `No case is currently ${STATUS_LABEL[view.status]?.toLowerCase() ?? view.status}.`
              }
              action={
                narrowed ? (
                  <button
                    type="button"
                    className="ops-btn is-sm"
                    onClick={() => setView({ priority: "", team: "", q: "" })}
                  >
                    Clear filters
                  </button>
                ) : null
              }
            />
          ) : null}
          {tickets.map((ticket) => (
            <QueueRow
              key={ticket.id}
              ticket={ticket}
              selected={selected === ticket.id}
              onSelect={select}
            />
          ))}
        </section>

        <section className="tickets-detail" aria-label="Ticket detail">
          {selected ? (
            <TicketCase
              ticket={detail}
              loading={detailLoading}
              error={Boolean(detailError)}
              who={who}
              pending={update.isPending}
              isError={update.isError}
              isSuccess={update.isSuccess}
              onPatch={(patch) => update.mutate({ id: selected, patch })}
              onBack={() => select(null)}
            />
          ) : (
            <EmptyState
              title="Select a case"
              body="Pick a ticket from the queue to read its handoff brief, the conversation as it stood at escalation, and reply."
            />
          )}
        </section>
      </div>
    </OpsPage>
  );
}

export default function StaffTicketQueuePage() {
  return (
    <StaffGuard current="/admin/tickets">
      {(who) => <StaffTicketQueue who={who} />}
    </StaffGuard>
  );
}

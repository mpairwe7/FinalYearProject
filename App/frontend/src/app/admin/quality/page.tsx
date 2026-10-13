"use client";

/**
 * Service-quality audit — what taxpayers said about the service they got.
 *
 * For administrators and auditors. Officers give the service and do not audit
 * it; administrators no longer handle taxpayers live (docs/plans/
 * admin-service-quality-audit-plan.md), so this page is where their work is.
 *
 * This first section audits the AI's chat answers from the taxpayers' 👍/👎.
 * A rating names the answer it rates, so the page can read each conversation's
 * ratings in order: the shape "✓ ✓ ✕" is a conversation that went wrong at its
 * third answer, after two good ones, and the timeline opens at that answer.
 *
 * Reads only. `?conversation=` makes an opened conversation a link.
 */
import React, { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { useIsFetching, useQueryClient } from "@tanstack/react-query";
import StaffGuard from "../../../components/StaffGuard";
import { OpsPage, OpsPanel, TableScroll } from "../../../components/ops/OpsPage";
import { Freshness, PeriodPicker } from "../../../components/ops/Controls";
import { StatCard } from "../../../components/ops/StatCard";
import { EmptyState, ErrorState, Skeleton, SkeletonRows, SkeletonStats } from "../../../components/ops/States";
import { patternLabel, retrievalModeLabel } from "../../../components/charts/chartTheme";
import { PATTERN_ORDER, PatternBreakdown } from "../../../components/quality/PatternBreakdown";
import { RatingStrip } from "../../../components/quality/RatingStrip";
import { ConversationTimeline } from "../../../components/quality/ConversationTimeline";
import { useAiChatAudit } from "../../../hooks/useQualityAudit";
import { useSkeletonVisible } from "../../../hooks/useLoadingPhase";
import { callLanguageName } from "../../../lib/callLanguage";
import { queryKeys } from "../../../lib/queryKeys";
import type { AiChatSummary, QualityBreakdownRow, QualityPattern, RatedConversation } from "../../../services/analyticsApi";
import "../admin.css";
import "./quality.css";

const PositionChart = dynamic(() => import("../../../components/quality/PositionChart"), {
  ssr: false,
  loading: () => <Skeleton height={300} radius="var(--ops-radius-md)" />,
});

const PAGE = 25;

function readConversation(): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get("conversation");
}

function writeConversation(id: string | null): void {
  if (typeof window === "undefined") return;
  const params = new URLSearchParams(window.location.search);
  if (id) params.set("conversation", id);
  else params.delete("conversation");
  const qs = params.toString();
  try {
    window.history.replaceState(null, "", qs ? `${window.location.pathname}?${qs}` : window.location.pathname);
  } catch {
    /* jsdom without a writable Location */
  }
}

function pct(value: number | null | undefined): string {
  return value == null ? "—" : `${value}%`;
}

/** "was 72% the period before", or nothing when there was nothing to compare. */
function previousHint(summary: AiChatSummary): string {
  const prev = summary.previous;
  if (!prev.rated_answers || prev.helpful_pct == null || summary.helpful_pct == null) return "nothing rated in the period before";
  const change = Math.round((summary.helpful_pct - prev.helpful_pct) * 10) / 10;
  const direction = change === 0 ? "unchanged" : change > 0 ? `up ${change} points` : `down ${-change} points`;
  return `${direction} from ${prev.helpful_pct}% in the period before`;
}

function when(ts: number): string {
  return new Date(ts * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function BreakdownTable({
  title,
  rows,
  label,
}: {
  title: string;
  rows: QualityBreakdownRow[];
  label: (key: string) => string;
}) {
  if (!rows.length) return null;
  return (
    <div className="ops-chart-card">
      <div className="ops-chart-head">
        <h3 className="ops-chart-title">{title}</h3>
        <span className="ops-chart-sub">most unhelpful first</span>
      </div>
      <TableScroll label={title}>
        <table className="ops-table">
          <thead>
            <tr>
              <th scope="col">{title.split(" ")[0]}</th>
              <th scope="col" className="is-num">Rated</th>
              <th scope="col" className="is-num">Not helpful</th>
              <th scope="col" className="is-num">Found helpful</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key}>
                <td>{label(r.key)}</td>
                <td className="is-num">{r.rated}</td>
                <td className="is-num">{r.unhelpful}</td>
                <td className="is-num">{pct(r.helpful_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
    </div>
  );
}

function ConversationRow({
  conv,
  selected,
  onOpen,
}: {
  conv: RatedConversation;
  selected: boolean;
  onOpen: (id: string) => void;
}) {
  const id = conv.conversation_id;
  const body = (
    <>
      <span className="qa-conv-top">
        <RatingStrip ratings={conv.ratings} breakAnswer={conv.break_answer} compact />
        <span className={`qa-pattern-name is-${conv.pattern}`}>{patternLabel(conv.pattern)}</span>
      </span>
      {conv.break_answer ? (
        <span className="qa-conv-break">
          Went wrong at answer {conv.break_answer}
          {conv.helpful_before_break ? ", after a helpful one" : ""}
          {conv.break_question ? <>: <q>{conv.break_question}</q></> : null}
        </span>
      ) : null}
      <span className="qa-conv-meta">
        <span>{conv.rated_answers} rated</span>
        {conv.comments ? <span>{conv.comments} with a comment</span> : null}
        {conv.topic ? <span>{conv.topic.replaceAll("_", " ")}</span> : null}
        {conv.locale ? <span>{callLanguageName(conv.locale)}</span> : null}
        <span>{when(conv.last_rated_at)}</span>
        {!conv.linked ? <span>rated before answers were linked</span> : null}
      </span>
    </>
  );
  if (!id) return <div className="qa-conv is-static">{body}</div>;
  return (
    <button type="button" className={`qa-conv${selected ? " is-selected" : ""}`} aria-current={selected || undefined} onClick={() => onOpen(id)}>
      {body}
    </button>
  );
}

export function AiChatAudit({ days, onUpdated }: { days: number; onUpdated?: (at: number) => void }) {
  const [pattern, setPattern] = useState<QualityPattern | "">("");
  const [withComment, setWithComment] = useState(false);
  const [limit, setLimit] = useState(PAGE);
  const [selected, setSelected] = useState<string | null>(() => readConversation());

  useEffect(() => writeConversation(selected), [selected]);
  // A new filter starts the list from the top.
  useEffect(() => setLimit(PAGE), [pattern, withComment, days]);

  const audit = useAiChatAudit({ days, pattern, withComment, limit });
  const summaryLoading = useSkeletonVisible(audit.isLoading);
  const summary = audit.data?.summary;
  const page = audit.data?.page;

  useEffect(() => {
    if (audit.dataUpdatedAt) onUpdated?.(audit.dataUpdatedAt);
  }, [audit.dataUpdatedAt, onUpdated]);

  const open = useCallback((id: string) => setSelected(id), []);

  if (audit.isError && !audit.data) {
    return (
      <ErrorState
        title="The audit did not load"
        body={`GET /v1/admin/quality/ai-chat answered ${(audit.error as Error).message}. Nothing below is current.`}
        onRetry={() => void audit.refetch()}
      />
    );
  }

  return (
    <div className="qa-section">
      {summary?.truncated ? (
        <p className="qa-warning" role="status">
          More answers were rated in this period than one view reads, so these figures cover the most recent ones
          only. Choose a shorter period for the full picture.
        </p>
      ) : null}

      <section aria-label="AI answer ratings summary">
        {summaryLoading || !summary ? (
          <SkeletonStats count={4} cols={4} />
        ) : (
          <div className="ops-stat-grid qa-stats">
            <StatCard
              label="Answers found helpful"
              value={pct(summary.helpful_pct)}
              hint={`${summary.helpful} of ${summary.rated_answers} rated · ${previousHint(summary)}`}
            />
            <StatCard
              label="Rated answers"
              value={String(summary.rated_answers)}
              hint={`${summary.comments} with a comment · ${summary.conversations} conversations`}
            />
            <StatCard
              label="Went wrong after a helpful answer"
              value={String(summary.patterns.went_wrong)}
              hint="conversations whose last rated answer was not helpful, after one that was"
            />
            <StatCard
              label="Where they went wrong"
              value={summary.median_break_answer ? `answer ${summary.median_break_answer}` : "—"}
              hint="the middle answer number at which those conversations broke"
            />
          </div>
        )}
      </section>

      {summary && summary.rated_answers > 0 ? (
        <>
          <section className="ops-chart-grid is-2" aria-label="Where answers fail">
            <PositionChart byPosition={summary.by_position} />
            <PatternBreakdown patterns={summary.patterns} active={pattern} onSelect={setPattern} />
          </section>
          <section className="ops-chart-grid is-2" aria-label="Unhelpful answers by topic and route">
            <BreakdownTable title="Topics with the most unhelpful answers" rows={summary.by_topic} label={(k) => k.replaceAll("_", " ")} />
            <BreakdownTable title="Routes with the most unhelpful answers" rows={summary.by_route} label={retrievalModeLabel} />
          </section>
        </>
      ) : null}

      <OpsPanel
        id="qa-conv-h"
        title="Rated conversations"
        bare
        note="Worst first: conversations that went wrong after a helpful answer, then those never found helpful. Open one to read it answer by answer."
        end={
          <div className="qa-filters">
            <div className="ops-segmented" role="group" aria-label="Show conversations">
              <button type="button" aria-pressed={pattern === ""} className={pattern === "" ? "is-active" : undefined} onClick={() => setPattern("")}>
                All
              </button>
              {PATTERN_ORDER.map((p) => (
                <button key={p} type="button" aria-pressed={pattern === p} className={pattern === p ? "is-active" : undefined} onClick={() => setPattern(p)}>
                  {patternLabel(p)}
                </button>
              ))}
            </div>
            <label className="qa-check">
              <input type="checkbox" checked={withComment} onChange={(e) => setWithComment(e.target.checked)} />
              With a comment
            </label>
          </div>
        }
      >
        <div className={`qa-split${selected ? " is-open" : ""}`}>
          <div className="qa-list-pane">
            {summaryLoading && !page ? (
              <SkeletonRows rows={5} height={72} />
            ) : page && page.conversations.length ? (
              <>
                <p className="qa-count" aria-live="polite">
                  {page.total} {page.total === 1 ? "conversation" : "conversations"}
                  {pattern ? ` · ${patternLabel(pattern).toLowerCase()}` : ""}
                </p>
                <ul className="qa-convs">
                  {page.conversations.map((c) => (
                    <li key={c.key}>
                      <ConversationRow conv={c} selected={c.conversation_id === selected} onOpen={open} />
                    </li>
                  ))}
                </ul>
                {page.total > page.conversations.length ? (
                  <button type="button" className="ops-btn is-ghost" onClick={() => setLimit((n) => Math.min(n + PAGE, 100))} disabled={limit >= 100 || audit.isFetching}>
                    {limit >= 100 ? "Showing the first 100 — narrow the filter" : "Show more"}
                  </button>
                ) : null}
              </>
            ) : (
              <EmptyState
                title={pattern || withComment ? "No conversation matches" : "No rated answers in this period"}
                body={
                  pattern || withComment
                    ? "Clear the filter to see every rated conversation."
                    : "Ratings appear here as taxpayers mark answers helpful or not helpful."
                }
              />
            )}
          </div>
          {selected ? (
            <div className="qa-detail-pane">
              <button type="button" className="ops-btn is-ghost is-sm qa-back" onClick={() => setSelected(null)}>
                Close conversation
              </button>
              <ConversationTimeline conversationId={selected} />
            </div>
          ) : null}
        </div>
      </OpsPanel>
    </div>
  );
}

function QualityAudit() {
  const [days, setDays] = useState(30);
  const [updatedAt, setUpdatedAt] = useState(0);
  const queryClient = useQueryClient();
  const fetching = useIsFetching({ queryKey: queryKeys.quality.all() }) > 0;

  return (
    <OpsPage
      eyebrow="Audit"
      title="Service quality audit"
      description={`What taxpayers said about the answers they got in the last ${days} days. Read-only: it shows where the service went wrong so it can be put right.`}
      actions={
        <>
          <Freshness
            updatedAt={updatedAt || undefined}
            isFetching={fetching}
            onRefresh={() => void queryClient.invalidateQueries({ queryKey: queryKeys.quality.all() })}
          />
          <PeriodPicker days={days} onChange={setDays} />
        </>
      }
    >
      <h2 className="qa-section-title">AI answers in chat</h2>
      <AiChatAudit days={days} onUpdated={setUpdatedAt} />
    </OpsPage>
  );
}

export default function QualityAuditPage() {
  return (
    <StaffGuard current="/admin/quality" requireRoles={["ura_admin", "ura_auditor"]}>
      {() => <QualityAudit />}
    </StaffGuard>
  );
}

"use client";

/**
 * How callers rated the AI on the phone — the general opinion, not call by
 * call.
 *
 * The AI asks once a call, after it has answered something: at goodbye
 * ("was this call helpful?") and, for a caller who asked for an officer,
 * while the transfer goes ahead ("were my answers helpful so far?"). The
 * spoken answer is classified as helped / partly / didn't help / unclear.
 * The two moments are shown side by side because they ask different people:
 * a caller leaving satisfied, and a caller who wanted a person. Then what
 * callers actually said, the unhappy answers first.
 */
import React, { useMemo, useState } from "react";
import { TableScroll } from "../ops/OpsPage";
import { StatCard } from "../ops/StatCard";
import { EmptyState, ErrorState, SkeletonRows, SkeletonStats } from "../ops/States";
import { ChartNote, ChartTable, sentimentLabel } from "../charts/chartTheme";
import { useSkeletonVisible } from "../../hooks/useLoadingPhase";
import { useRatingsAudit } from "../../hooks/useQualityAudit";
import { callLanguageName } from "../../lib/callLanguage";
import type { RatingSentiment, RatingsAudit, ServiceRating } from "../../services/analyticsApi";

const SENTIMENTS: readonly RatingSentiment[] = ["positive", "neutral", "negative", "unclear"];

export const MOMENT_LABEL: Record<string, string> = {
  call_ai_review_end: "At the end of the call",
  call_ai_review_transfer: "While being transferred to an officer",
};

function share(part: number, whole: number): number | null {
  return whole ? Math.round((part / whole) * 1000) / 10 : null;
}

function pct(value: number | null | undefined): string {
  return value == null ? "—" : `${value}%`;
}

function when(ts: number | null | undefined): string {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** Both moments together: asked, answered and how the answers came out. */
export function combineAiCalls(audits: RatingsAudit[]) {
  const asked = audits.reduce((n, a) => n + a.summary.asked, 0);
  const answered = audits.reduce((n, a) => n + a.summary.answered, 0);
  const counts = Object.fromEntries(
    SENTIMENTS.map((s) => [s, audits.reduce((n, a) => n + (a.summary.sentiment[s] || 0), 0)]),
  ) as Record<RatingSentiment, number>;
  const prevAnswered = audits.reduce((n, a) => n + (a.previous.answered || 0), 0);
  const prevPositive = audits.reduce(
    (n, a) => n + Math.round(((a.previous.positive_pct || 0) * (a.previous.answered || 0)) / 100),
    0,
  );
  const prevNegative = audits.reduce(
    (n, a) => n + Math.round(((a.previous.negative_pct || 0) * (a.previous.answered || 0)) / 100),
    0,
  );
  return {
    asked,
    answered,
    noAnswer: audits.reduce((n, a) => n + a.summary.no_answer + a.summary.skipped, 0),
    responseRate: share(answered, asked),
    counts,
    positivePct: share(counts.positive, answered),
    negativePct: share(counts.negative, answered),
    previous: {
      asked: audits.reduce((n, a) => n + a.previous.asked, 0),
      positivePct: share(prevPositive, prevAnswered),
      negativePct: share(prevNegative, prevAnswered),
    },
  };
}

function changeHint(now: number | null, before: number | null, unit = " points"): string {
  if (now == null || before == null) return "nothing answered in the period before";
  const change = Math.round((now - before) * 10) / 10;
  if (change === 0) return `unchanged from ${before}% in the period before`;
  return `${change > 0 ? "up" : "down"} ${Math.abs(change)}${unit} from ${before}% in the period before`;
}

/** The sentence under the comparison: where callers were happier. */
export function momentReading(rows: Array<{ label: string; answered: number; positivePct: number | null }>): string {
  const rated = rows.filter((r) => r.answered > 0 && r.positivePct != null);
  if (!rated.length) return "No caller has answered the question in this period yet.";
  if (rated.length === 1) {
    const only = rated[0];
    return `${only.label}: ${only.positivePct}% of ${only.answered} callers who answered said the AI helped.`;
  }
  const [a, b] = rated;
  const gap = Math.round(((a.positivePct as number) - (b.positivePct as number)) * 10) / 10;
  const verdict =
    Math.abs(gap) < 5
      ? "about the same at both moments"
      : gap > 0
        ? `${gap} points higher at the end of a call than from callers who wanted an officer`
        : `${-gap} points lower at the end of a call than from callers who wanted an officer`;
  return `Callers who answered said the AI helped ${a.positivePct}% of the time at the end of a call (${a.answered} answered) and ${b.positivePct}% while being transferred (${b.answered}): ${verdict}.`;
}

function SentimentByMoment({ audits }: { audits: RatingsAudit[] }) {
  const rows = audits.map((a) => ({
    label: MOMENT_LABEL[a.source] ?? a.source,
    asked: a.summary.asked,
    answered: a.summary.answered,
    counts: a.summary.sentiment,
    positivePct: a.summary.positive_pct,
  }));
  const title = "What callers said, by when they were asked";
  return (
    <div className="ops-chart-card">
      <div className="ops-chart-head">
        <h3 className="ops-chart-title">{title}</h3>
        <span className="ops-chart-sub">share of callers who answered</span>
      </div>
      <ul className="qa-moments" aria-label={title}>
        {rows.map((row) => (
          <li key={row.label} className="qa-moment">
            <span className="qa-moment-name">
              {row.label}
              <span className="qa-moment-n">
                {" "}
                · {row.answered} of {row.asked} answered
              </span>
            </span>
            <span className="qa-moment-bar" aria-hidden="true">
              {SENTIMENTS.map((s) =>
                row.counts[s] ? (
                  <span
                    key={s}
                    className={`qa-moment-seg is-${s}`}
                    style={{ width: `${((row.counts[s] || 0) / Math.max(1, row.answered)) * 100}%` }}
                  />
                ) : null,
              )}
            </span>
            <span className="qa-moment-legend">
              {SENTIMENTS.map((s) => (
                <span key={s} className={`qa-moment-key is-${s}`}>
                  <span className="qa-moment-swatch" aria-hidden="true" />
                  {sentimentLabel(s)}: {row.counts[s] || 0}
                </span>
              ))}
            </span>
          </li>
        ))}
      </ul>
      <ChartNote>{momentReading(rows)}</ChartNote>
      <ChartTable
        caption={title}
        columns={["When asked", "Asked", "Answered", ...SENTIMENTS.map(sentimentLabel)]}
        rows={rows.map((r) => [r.label, r.asked, r.answered, ...SENTIMENTS.map((s) => r.counts[s] || 0)])}
      />
    </div>
  );
}

function mergeWeeks(audits: RatingsAudit[]) {
  const weeks = new Map<string, { rated: number; positive: number }>();
  for (const a of audits) {
    for (const w of a.summary.by_week ?? []) {
      const row = weeks.get(w.week) ?? { rated: 0, positive: 0 };
      row.rated += w.rated;
      row.positive += Math.round(((w.positive_pct || 0) * w.rated) / 100);
      weeks.set(w.week, row);
    }
  }
  return [...weeks.entries()].sort(([x], [y]) => x.localeCompare(y)).map(([week, r]) => ({ week, ...r }));
}

function mergeLanguages(audits: RatingsAudit[]) {
  const langs = new Map<string, { asked: number; rated: number; positive: number }>();
  for (const a of audits) {
    for (const l of a.summary.by_language ?? []) {
      const row = langs.get(l.locale) ?? { asked: 0, rated: 0, positive: 0 };
      row.asked += l.asked;
      row.rated += l.rated;
      row.positive += Math.round(((l.positive_pct || 0) * l.rated) / 100);
      langs.set(l.locale, row);
    }
  }
  return [...langs.entries()].sort(([, x], [, y]) => y.asked - x.asked).map(([locale, r]) => ({ locale, ...r }));
}

const SAID_ORDER: Record<string, number> = { negative: 0, unclear: 1, neutral: 2, positive: 3 };

function Verbatim({ rating }: { rating: ServiceRating }) {
  return (
    <li className="qa-said">
      <span className="qa-conv-top">
        <span className={`qa-said-chip is-${rating.sentiment}`}>{sentimentLabel(rating.sentiment)}</span>
        <span className="qa-rating-officer">{MOMENT_LABEL[rating.source] ?? rating.source}</span>
      </span>
      {rating.comment ? (
        <span className="qa-conv-break">
          <q>{rating.comment}</q>
        </span>
      ) : null}
      <span className="qa-conv-meta">
        {rating.locale ? <span>{callLanguageName(rating.locale)}</span> : null}
        {rating.topic ? <span>{rating.topic.replaceAll("_", " ")}</span> : null}
        <span>{when(rating.answered_at || rating.created_at)}</span>
      </span>
    </li>
  );
}

export function AiCallsPanel({ days, onUpdated }: { days: number; onUpdated?: (at: number) => void }) {
  const end = useRatingsAudit({ source: "call_ai_review_end", days, status: "answered", limit: 50 });
  const transfer = useRatingsAudit({ source: "call_ai_review_transfer", days, status: "answered", limit: 50 });
  const [said, setSaid] = useState<"" | RatingSentiment>("");
  const loading = useSkeletonVisible(end.isLoading || transfer.isLoading);
  const audits = useMemo(
    () => [end.data, transfer.data].filter((a): a is RatingsAudit => Boolean(a)),
    [end.data, transfer.data],
  );
  const updatedAt = Math.max(end.dataUpdatedAt || 0, transfer.dataUpdatedAt || 0);
  React.useEffect(() => {
    if (updatedAt) onUpdated?.(updatedAt);
  }, [updatedAt, onUpdated]);

  const failed = (end.isError && !end.data) || (transfer.isError && !transfer.data);
  if (failed) {
    const error = (end.error || transfer.error) as Error;
    return (
      <ErrorState
        title="The call reviews did not load"
        body={`GET /v1/admin/quality/ratings answered ${error?.message}. Nothing below is current.`}
        onRetry={() => {
          void end.refetch();
          void transfer.refetch();
        }}
      />
    );
  }

  const ready = audits.length === 2;
  const total = ready ? combineAiCalls(audits) : null;
  const verbatims = ready
    ? audits
        .flatMap((a) => a.ratings)
        .filter((r) => (said ? r.sentiment === said : r.sentiment !== "positive"))
        .sort(
          (x, y) =>
            (SAID_ORDER[x.sentiment] ?? 9) - (SAID_ORDER[y.sentiment] ?? 9) ||
            (y.answered_at || y.created_at) - (x.answered_at || x.created_at),
        )
        .slice(0, 20)
    : [];

  return (
    <div className="qa-section">
      {audits.some((a) => a.truncated) ? (
        <p className="qa-warning" role="status">
          More calls were reviewed in this period than one view reads, so these figures cover the most recent ones
          only. Choose a shorter period for the full picture.
        </p>
      ) : null}

      <section aria-label="Callers' review of the AI summary">
        {loading || !total ? (
          <SkeletonStats count={4} cols={4} />
        ) : (
          <div className="ops-stat-grid qa-stats">
            <StatCard
              label="Callers asked"
              value={String(total.asked)}
              hint={`after the AI answered something · ${total.previous.asked} in the period before`}
            />
            <StatCard
              label="Answered the question"
              value={pct(total.responseRate)}
              hint={`${total.answered} answered · ${total.noAnswer} hung up, met an officer first or said nothing`}
            />
            <StatCard
              label="Said the AI helped"
              value={pct(total.positivePct)}
              hint={changeHint(total.positivePct, total.previous.positivePct)}
            />
            <StatCard
              label="Said it didn't help"
              value={pct(total.negativePct)}
              hint={changeHint(total.negativePct, total.previous.negativePct)}
            />
          </div>
        )}
      </section>

      {total && total.asked > 0 ? (
        <>
          <section aria-label="Callers' review by moment">
            <SentimentByMoment audits={audits} />
          </section>
          <section className="ops-chart-grid is-2" aria-label="Callers' review by week and by language">
            <div className="ops-chart-card">
              <div className="ops-chart-head">
                <h3 className="ops-chart-title">By week</h3>
                <span className="ops-chart-sub">callers who answered, and how many said it helped</span>
              </div>
              <TableScroll label="Callers' review by week">
                <table className="ops-table">
                  <thead>
                    <tr>
                      <th scope="col">Week</th>
                      <th scope="col" className="is-num">Answered</th>
                      <th scope="col" className="is-num">Said it helped</th>
                    </tr>
                  </thead>
                  <tbody>
                    {mergeWeeks(audits).map((w) => (
                      <tr key={w.week}>
                        <td>{w.week}</td>
                        <td className="is-num">{w.rated}</td>
                        <td className="is-num">{pct(share(w.positive, w.rated))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableScroll>
            </div>
            <div className="ops-chart-card">
              <div className="ops-chart-head">
                <h3 className="ops-chart-title">By language</h3>
                <span className="ops-chart-sub">the language the call was in</span>
              </div>
              <TableScroll label="Callers' review by language">
                <table className="ops-table">
                  <thead>
                    <tr>
                      <th scope="col">Language</th>
                      <th scope="col" className="is-num">Asked</th>
                      <th scope="col" className="is-num">Answered</th>
                      <th scope="col" className="is-num">Said it helped</th>
                    </tr>
                  </thead>
                  <tbody>
                    {mergeLanguages(audits).map((l) => (
                      <tr key={l.locale}>
                        <td>{l.locale === "unknown" ? "Not recorded" : callLanguageName(l.locale)}</td>
                        <td className="is-num">{l.asked}</td>
                        <td className="is-num">{l.rated}</td>
                        <td className="is-num">{pct(share(l.positive, l.rated))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableScroll>
            </div>
          </section>
        </>
      ) : null}

      <section className="qa-said-panel" aria-labelledby="qa-said-h">
        <div className="qa-said-head">
          <h3 id="qa-said-h" className="ops-chart-title">
            What callers said
          </h3>
          <div className="ops-segmented" role="group" aria-label="Show answers">
            {(["", "negative", "unclear", "neutral", "positive"] as const).map((s) => (
              <button
                key={s || "all"}
                type="button"
                aria-pressed={said === s}
                className={said === s ? "is-active" : undefined}
                onClick={() => setSaid(s)}
              >
                {s ? sentimentLabel(s) : "Not a clear yes"}
              </button>
            ))}
          </div>
        </div>
        <p className="ops-chart-note">
          The callers&apos; own words, transcribed. The unhappy answers come first; each is one caller&apos;s answer,
          not a call to open.
        </p>
        {loading && !ready ? (
          <SkeletonRows rows={4} height={56} />
        ) : verbatims.length ? (
          <ul className="qa-convs">
            {verbatims.map((r) => (
              <Verbatim key={r.id} rating={r} />
            ))}
          </ul>
        ) : (
          <EmptyState
            title={said ? "No answer of this kind" : "No unhappy or unclear answer in this period"}
            body={
              total && total.answered
                ? "Choose another filter to read the other answers."
                : "When the AI asks a caller whether the call helped, their answers appear here."
            }
          />
        )}
      </section>
    </div>
  );
}

'use client';

import React from 'react';
import Link from 'next/link';
import { TableScroll } from '@/components/ops/OpsPage';
import { EmptyState, SkeletonRows } from '@/components/ops/States';
import { formatClock } from '@/hooks/useNow';
import { callLanguageName } from '@/lib/callLanguage';
import { callTopicLabel } from '@/lib/callTopic';
import type { CallRecord } from '@/services/callsApi';

export function CallHistoryTable({
  calls,
  total,
  limit,
  offset,
  loading,
  onPageChange,
  onSelectCall,
}: {
  calls: CallRecord[];
  total: number;
  limit: number;
  offset: number;
  loading: boolean;
  onPageChange: (nextOffset: number) => void;
  onSelectCall: (callId: string) => void;
}) {
  if (loading && calls.length === 0) {
    return <SkeletonRows rows={8} />;
  }

  if (calls.length === 0) {
    return <EmptyState title="No calls match your filters" body="Try clearing search or filters to see past calls." />;
  }

  const formatStarted = (epochSec: number) => {
    const d = new Date(epochSec * 1000);
    return `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
  };

  const durationSec = (c: CallRecord) => {
    const end = c.ended_at ?? c.started_at;
    return Math.max(0, Math.round(end - c.started_at));
  };

  const canPrev = offset > 0;
  const canNext = offset + limit < total;

  return (
    <div className="ch-table-wrap">
      <TableScroll label="Call history">
        <table className="ops-table ch-table">
          <thead>
            <tr>
              <th scope="col">Started</th>
              <th scope="col">Duration</th>
              <th scope="col">Language</th>
              <th scope="col">Topic</th>
              <th scope="col">Outcome</th>
              <th scope="col">Officer</th>
              <th scope="col">Ticket</th>
              <th scope="col">Rating</th>
            </tr>
          </thead>
          <tbody>
            {calls.map((c) => (
              <tr
                key={c.call_id}
                className="ch-row"
                onClick={() => onSelectCall(c.call_id)}
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') onSelectCall(c.call_id);
                }}
              >
                <td className="ops-tabular-num">{formatStarted(c.started_at)}</td>
                <td className="ops-tabular-num">{formatClock(durationSec(c))}</td>
                <td>
                  <span className="st-chip--language">{callLanguageName(c.locale)}</span>
                </td>
                <td className="ch-cell-topic">{callTopicLabel(c.topic) || 'Tax Inquiry'}</td>
                <td>
                  <span className={`ch-outcome-chip ch-outcome--${c.outcome || 'ended'}`}>
                    {c.outcome ? c.outcome.replace(/_/g, ' ') : 'ended'}
                  </span>
                </td>
                <td>{c.officer_id || 'AI only'}</td>
                <td>
                  {c.ticket_id ? (
                    <Link
                      href={`/admin/tickets?ticket=${encodeURIComponent(c.ticket_id)}`}
                      className="cw-ticket"
                      onClick={(e) => e.stopPropagation()}
                    >
                      #{c.ticket_id.slice(0, 8)}
                    </Link>
                  ) : (
                    '—'
                  )}
                </td>
                <td>
                  {c.officer_rating ? (
                    <span className="ch-stars">{'★'.repeat(c.officer_rating)}</span>
                  ) : (
                    '—'
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>

      <footer className="ch-pagination">
        <span className="ch-page-info">
          Showing {total === 0 ? 0 : offset + 1}–{Math.min(offset + limit, total)} of {total} calls
        </span>
        <div className="ch-page-buttons">
          <button
            type="button"
            className="cc-btn cc-btn--quiet"
            disabled={!canPrev || loading}
            onClick={() => onPageChange(Math.max(0, offset - limit))}
          >
            Previous
          </button>
          <button
            type="button"
            className="cc-btn cc-btn--quiet"
            disabled={!canNext || loading}
            onClick={() => onPageChange(offset + limit)}
          >
            Next
          </button>
        </div>
      </footer>
    </div>
  );
}

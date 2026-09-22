import React from 'react';
import { CallSummary } from '@/services/callsApi';

interface CallSummaryCardProps {
  summary: CallSummary;
}

export function CallSummaryCard({ summary }: CallSummaryCardProps) {
  const resolutionColor =
    summary.resolution === 'answered'
      ? '#059669'
      : summary.resolution === 'transferred'
      ? '#2563eb'
      : '#d97706';

  return (
    <div className="st-case-card" style={{ marginBottom: '1rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.75rem' }}>
        <h4 style={{ margin: 0, fontSize: '0.9375rem', fontWeight: 600 }}>Call Summary: {summary.subject}</h4>
        <span
          className="st-chip"
          style={{
            background: `${resolutionColor}15`,
            color: resolutionColor,
            borderColor: `${resolutionColor}30`,
          }}
        >
          {summary.resolution}
        </span>
      </div>

      <p style={{ margin: '0 0 0.875rem 0', fontSize: '0.875rem', lineHeight: 1.5, color: 'var(--text-primary)' }}>
        {summary.summary}
      </p>

      {summary.key_facts && summary.key_facts.length > 0 && (
        <div style={{ marginBottom: '0.75rem' }}>
          <div style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', marginBottom: '0.25rem' }}>
            Key Facts Recorded
          </div>
          <ul style={{ margin: 0, paddingLeft: '1.25rem', fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>
            {summary.key_facts.map((fact, idx) => (
              <li key={idx}>{fact}</li>
            ))}
          </ul>
        </div>
      )}

      {summary.follow_ups && summary.follow_ups.length > 0 && (
        <div style={{ marginBottom: '0.75rem' }}>
          <div style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', marginBottom: '0.25rem' }}>
            Recommended Follow-Ups
          </div>
          <ul style={{ margin: 0, paddingLeft: '1.25rem', fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>
            {summary.follow_ups.map((action, idx) => (
              <li key={idx}>{action}</li>
            ))}
          </ul>
        </div>
      )}

      {summary.ai_handling_notes && (
        <div style={{ fontSize: '0.75rem', fontStyle: 'italic', color: 'var(--text-secondary)' }}>
          Handling notes: {summary.ai_handling_notes}
        </div>
      )}
    </div>
  );
}

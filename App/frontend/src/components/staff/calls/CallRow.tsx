import React from 'react';
import { CallRecord } from '@/services/callsApi';
import { callLanguageName } from '@/lib/callLanguage';

interface CallRowProps {
  call: CallRecord;
  isSelected: boolean;
  onSelect: (callId: string) => void;
}

function formatElapsed(startedAt: number, endedAt?: number | null): string {
  const end = endedAt || Math.floor(Date.now() / 1000);
  const diff = Math.max(0, Math.floor(end - startedAt));
  const m = Math.floor(diff / 60);
  const s = diff % 60;
  return `${m}m ${s}s`;
}

export function CallRow({ call, isSelected, onSelect }: CallRowProps) {
  const isTransferWaiting = call.status === 'transferring';
  const isBridged = call.status === 'bridged';
  const isEnded = call.status === 'ended';

  const statusLabel = isTransferWaiting
    ? 'Transfer waiting'
    : isBridged
    ? 'With officer'
    : isEnded
    ? 'Ended'
    : 'AI active';

  const statusClass = isTransferWaiting
    ? 'st-pill--urgent st-pill--pulse'
    : isBridged
    ? 'st-pill--assigned'
    : isEnded
    ? 'st-pill--resolved'
    : 'st-pill--new';

  const topic = call.summary?.subject || (call.turns && call.turns[0]?.text) || 'Tax Consultation';

  return (
    <div
      role="button"
      tabIndex={0}
      className={`ag-row ${isSelected ? 'ag-row--selected' : ''}`}
      onClick={() => onSelect(call.call_id)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelect(call.call_id);
        }
      }}
    >
      <div className="ag-row-head">
        <span className={`st-pill ${statusClass}`}>{statusLabel}</span>
        <span className="st-chip--language" title="Call language">
          {callLanguageName(call.locale)}
        </span>
        <span className="ag-row-time">{formatElapsed(call.started_at, call.ended_at)}</span>
      </div>
      <div className="ag-row-subject">{topic}</div>
      <div className="ag-row-meta">
        <span>ID: {call.call_id.slice(0, 10)}</span>
        {call.ticket_id && (
          <span style={{ color: '#2563eb', fontWeight: 500 }}>Ticket #{call.ticket_id.slice(0, 8)}</span>
        )}
      </div>
    </div>
  );
}

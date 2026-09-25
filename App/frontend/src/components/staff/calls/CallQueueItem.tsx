'use client';

import React from 'react';
import { callLanguageName } from '@/lib/callLanguage';
import { callTopicLabel, isRaisedPriority } from '@/lib/callTopic';
import { formatClock, useNow } from '@/hooks/useNow';
import type { LobbyCall, SessionState } from '@/store/useCallConsoleStore';
import { WaitRing } from './console/WaitRing';

export type QueueRole = 'waiting' | 'mine' | 'officer' | 'ai';

function stateChip(role: QueueRole, call: LobbyCall, mine: SessionState | null): { label: string; tone: string } {
  if (role === 'mine') {
    return mine === 'bridged'
      ? { label: 'On call', tone: 'good' }
      : { label: mine === 'ending' ? 'Ending' : 'Connecting', tone: 'warn' };
  }
  if (role === 'waiting') return { label: 'Waiting', tone: 'bad' };
  if (role === 'officer') {
    const who = call.officer_name || call.claimed_name || 'an officer';
    return call.status === 'bridged' ? { label: `With ${who}`, tone: 'good' } : { label: `${who} joining`, tone: 'warn' };
  }
  return { label: 'AI handling', tone: 'info' };
}

export function CallQueueItem({
  call,
  role,
  selected,
  sessionState = null,
  onSelect,
}: {
  call: LobbyCall;
  role: QueueRole;
  selected: boolean;
  sessionState?: SessionState | null;
  onSelect: (callId: string) => void;
}) {
  const now = useNow();
  const chip = stateChip(role, call, sessionState);
  const topic = callTopicLabel(call.topic) || (role === 'ai' ? 'Talking to the AI' : 'General tax support');
  return (
    <li>
      <button
        type="button"
        className={`cq-item cq-item--${role}${selected ? ' is-selected' : ''}`}
        aria-current={selected ? 'true' : undefined}
        data-call-id={call.call_id}
        onClick={() => onSelect(call.call_id)}
      >
        <span className="cq-item-head">
          {role === 'waiting' ? (
            <WaitRing since={call.waiting_since} />
          ) : (
            <span className="cq-time">{formatClock(now / 1000 - call.started_at)}</span>
          )}
          <span className={`cq-chip cq-chip--${chip.tone}`}>{chip.label}</span>
          {call.risk_level === 'at_risk' && (
            <span className="cq-risk-badge cq-risk-badge--at-risk" title="At risk conversation">
              ▲ at risk
            </span>
          )}
          {call.risk_level === 'watch' && (
            <span className="cq-risk-badge cq-risk-badge--watch" title="Watching risk signals">
              ▲ watch
            </span>
          )}
          {isRaisedPriority(call.priority) && role !== 'ai' && (
            <span className={`call-chip call-chip--${call.priority}`}>{call.priority}</span>
          )}
          {call.needs_callback && <span className="call-chip call-chip--callback">Callback</span>}
        </span>
        <span className="cq-topic">{topic}</span>
        <span className="cq-meta">
          <span className="st-chip--language">{callLanguageName(call.language)}</span>
          <span className="cq-id">#{call.call_id.slice(5, 13)}</span>
        </span>
      </button>
    </li>
  );
}

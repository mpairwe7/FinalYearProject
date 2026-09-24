'use client';

import React from 'react';
import { EmptyState } from '@/components/ops/States';
import type { QueueSections, SessionState } from '@/store/useCallConsoleStore';
import { CallQueueItem, type QueueRole } from './CallQueueItem';

const SECTIONS: Array<{ key: keyof QueueSections; role: QueueRole; title: string }> = [
  { key: 'waiting', role: 'waiting', title: 'Waiting for an officer' },
  { key: 'mine', role: 'mine', title: 'My call' },
  { key: 'withOfficers', role: 'officer', title: 'With officers' },
  { key: 'ai', role: 'ai', title: 'AI handling' },
];

/** The ids in the order the queue shows them — what J / K walk through. */
export function queueOrder(sections: QueueSections): string[] {
  return SECTIONS.flatMap(({ key }) => sections[key].map((c) => c.call_id));
}

/** The Desk's queue (plan §3.2): who is waiting first, then everyone else. */
export function CallQueue({
  sections,
  selectedId,
  sessionState,
  onSelect,
}: {
  sections: QueueSections;
  selectedId: string | null;
  sessionState: SessionState | null;
  onSelect: (callId: string) => void;
}) {
  const total = SECTIONS.reduce((n, { key }) => n + sections[key].length, 0);
  if (total === 0) {
    return <EmptyState title="No live calls" body="Calls appear here the moment a taxpayer rings." />;
  }
  return (
    <nav className="cq" aria-label="Live calls">
      {SECTIONS.map(({ key, role, title }) =>
        sections[key].length === 0 ? null : (
          <section key={key} className="cq-section" aria-label={title}>
            <h3 className="cq-heading">
              {title} <span className="cq-count">{sections[key].length}</span>
            </h3>
            <ul className="cq-list">
              {sections[key].map((call) => (
                <CallQueueItem
                  key={call.call_id}
                  call={call}
                  role={role}
                  selected={call.call_id === selectedId}
                  sessionState={role === 'mine' ? sessionState : null}
                  onSelect={onSelect}
                />
              ))}
            </ul>
          </section>
        ),
      )}
    </nav>
  );
}

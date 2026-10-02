'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { callsApi, type CallRecord, type CallerHistoryResponse } from '@/services/callsApi';
import { callLanguageName } from '@/lib/callLanguage';

export function CallContextRail({
  callId,
  call,
  onOpenCall,
}: {
  callId: string;
  call: CallRecord;
  onOpenCall?: (callId: string) => void;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const [history, setHistory] = useState<CallerHistoryResponse | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(false);

  // Knowledge assist state
  const [kbQuery, setKbQuery] = useState('');
  const [kbAnswer, setKbAnswer] = useState<string | null>(null);
  const [kbSources, setKbSources] = useState<string[]>([]);
  const [kbLoading, setKbLoading] = useState(false);

  // Scratchpad notes
  const [scratchpad, setScratchpad] = useState<string>(() => {
    if (typeof window !== 'undefined') {
      return localStorage.getItem(`officer_notes_${callId}`) || '';
    }
    return '';
  });

  useEffect(() => {
    if (!callId) return;
    setLoadingHistory(true);
    callsApi
      .getCallerHistory(callId)
      .then(setHistory)
      .catch(() => setHistory(null))
      .finally(() => setLoadingHistory(false));
  }, [callId]);

  const saveScratchpad = (text: string) => {
    setScratchpad(text);
    if (typeof window !== 'undefined') {
      localStorage.setItem(`officer_notes_${callId}`, text);
    }
  };

  const askKnowledge = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!kbQuery.trim() || kbLoading) return;
    setKbLoading(true);
    setKbAnswer(null);
    setKbSources([]);
    try {
      const res = await fetch('/api/v1/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: kbQuery.trim(),
          conversation_id: `officer-kb-${callId}`,
          locale: 'en',
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setKbAnswer(data.reply || 'No direct answer found.');
        setKbSources(data.sources || []);
      } else {
        setKbAnswer('Failed to consult knowledge base.');
      }
    } catch {
      setKbAnswer('Knowledge assistant temporarily unavailable.');
    } finally {
      setKbLoading(false);
    }
  };

  return (
    <aside className={`cw-rail ${collapsed ? 'is-collapsed' : ''}`} aria-label="Call context">
      <div className="cw-rail-toggle-row">
        <button
          type="button"
          className="cc-btn cc-btn--quiet cw-rail-toggle"
          onClick={() => setCollapsed((c) => !c)}
          aria-expanded={!collapsed}
        >
          {collapsed ? '◀ Context' : 'Context ▶'}
        </button>
      </div>

      {!collapsed && (
        <div className="cw-rail-content">
          {/* Caller History */}
          <section className="cw-rail-card">
            <h4>Caller</h4>
            {loadingHistory ? (
              <p className="cc-hint">Checking history…</p>
            ) : history?.anonymous ? (
              <div className="cc-badge--anon">Anonymous caller</div>
            ) : history ? (
              <div className="cw-rail-history">
                <span className="cw-rail-meta-text">
                  {history.calls.length} prior {history.calls.length === 1 ? 'call' : 'calls'} ·{' '}
                  {history.tickets.length} open {history.tickets.length === 1 ? 'ticket' : 'tickets'}
                </span>
                {history.calls.length > 0 && (
                  <ul className="cw-rail-list">
                    {history.calls.slice(0, 3).map((c) => (
                      <li key={c.call_id} className="cw-rail-list-item">
                        <button
                          type="button"
                          className="cw-rail-link-btn"
                          onClick={() => onOpenCall?.(c.call_id)}
                        >
                          {c.topic || 'Inquiry'} · {callLanguageName(c.locale)}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ) : (
              <p className="cc-hint">No prior record.</p>
            )}
          </section>

          {/* Ticket context */}
          {call.ticket_id && (
            <section className="cw-rail-card">
              <h4>Linked Ticket</h4>
              <p className="cw-rail-meta-text">#{call.ticket_id.slice(0, 8)}</p>
              <Link
                href={`/admin/tickets?ticket=${encodeURIComponent(call.ticket_id)}`}
                className="cc-btn cc-btn--quiet"
              >
                Open ticket view
              </Link>
            </section>
          )}

          {/* Knowledge Assistant */}
          <section className="cw-rail-card">
            <h4>Ask Knowledge Base</h4>
            <form onSubmit={askKnowledge} className="cw-kb-form">
              <input
                type="text"
                className="cc-input"
                placeholder="Ask statute or rate…"
                value={kbQuery}
                onChange={(e) => setKbQuery(e.target.value)}
                disabled={kbLoading}
              />
              <button type="submit" className="cc-btn cc-btn--quiet" disabled={kbLoading || !kbQuery.trim()}>
                {kbLoading ? '…' : 'Search'}
              </button>
            </form>
            {kbAnswer && (
              <div className="cw-kb-answer">
                <p>{kbAnswer}</p>
                {kbSources.length > 0 && (
                  <span className="cw-kb-sources">Sources: {kbSources.join(', ')}</span>
                )}
              </div>
            )}
          </section>

          {/* Private scratchpad */}
          <section className="cw-rail-card">
            <h4>Private Notes</h4>
            <textarea
              className="cc-textarea"
              rows={4}
              placeholder="Scratchpad notes for this call (stored locally)…"
              value={scratchpad}
              onChange={(e) => saveScratchpad(e.target.value)}
            />
          </section>
        </div>
      )}
    </aside>
  );
}

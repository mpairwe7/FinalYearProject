'use client';

import React, { useEffect, useState } from 'react';
import { TableScroll } from '@/components/ops/OpsPage';
import { formatClock, useNow } from '@/hooks/useNow';
import { callsApi, type OfficerPresence, type CallAggregates } from '@/services/callsApi';
import type { LobbyCall } from '@/store/useCallConsoleStore';

export function SupervisorBoard({
  waitingCalls,
  metrics,
  onOpenCall,
}: {
  waitingCalls: LobbyCall[];
  metrics?: CallAggregates | null;
  onOpenCall?: (callId: string) => void;
}) {
  const now = useNow();
  const [officers, setOfficers] = useState<OfficerPresence[]>([]);
  const [teams, setTeams] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let active = true;
    const load = () => {
      callsApi
        .getPresence()
        .then((data) => {
          if (!active) return;
          setOfficers(data.officers || []);
          setTeams(data.teams || []);
        })
        .catch(() => {});
    };
    load();
    const interval = setInterval(load, 10000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, []);

  const handleManualRefresh = () => {
    setLoading(true);
    callsApi
      .getPresence()
      .then((data) => {
        setOfficers(data.officers || []);
        setTeams(data.teams || []);
      })
      .finally(() => setLoading(false));
  };

  const longestWaitS = waitingCalls.reduce((max, c) => {
    const wait = c.waiting_since ? now / 1000 - c.waiting_since : 0;
    return Math.max(max, wait);
  }, 0);

  const onlineCount = officers.filter((o) => o.status !== 'offline').length;
  const availableCount = officers.filter((o) => o.status === 'available').length;
  const onCallCount = officers.filter((o) => o.status === 'on_call').length;

  return (
    <div className="sup-board" role="region" aria-label="Supervisor board">
      <div className="sup-stats-grid">
        <div className="st-case-card">
          <div className="st-metric-label">Waiting SLA (90s limit)</div>
          <div className={`st-metric-value ${longestWaitS > 90 ? 'ops-bad' : ''}`}>
            {waitingCalls.length > 0 ? formatClock(longestWaitS) : '00:00'}
          </div>
          <p className="cc-hint">
            {waitingCalls.length} {waitingCalls.length === 1 ? 'caller' : 'callers'} currently waiting
          </p>
        </div>

        <div className="st-case-card">
          <div className="st-metric-label">Officer Presence</div>
          <div className="st-metric-value">
            {availableCount} / {onlineCount}
          </div>
          <p className="cc-hint">Available / Total online officers ({onCallCount} on call)</p>
        </div>

        <div className="st-case-card">
          <div className="st-metric-label">Containment (7d)</div>
          <div className="st-metric-value">
            {metrics ? `${Math.round(metrics.containment_rate * 100)}%` : '—'}
          </div>
          <p className="cc-hint">Calls resolved without officer escalation</p>
        </div>

        <div className="st-case-card">
          <div className="st-metric-label">Active Teams</div>
          <div className="st-metric-value">{teams.length}</div>
          <p className="cc-hint">{teams.join(', ')}</p>
        </div>
      </div>

      <section className="sup-officers-section">
        <div className="sup-section-head">
          <h3>Staff Roster & Real-Time Availability</h3>
          <button type="button" className="cc-btn cc-btn--quiet" onClick={handleManualRefresh} disabled={loading}>
            {loading ? 'Refreshing…' : '↻ Refresh'}
          </button>
        </div>

        <TableScroll label="Officer roster">
          <table className="ops-table">
            <thead>
              <tr>
                <th scope="col">Officer</th>
                <th scope="col">Status</th>
                <th scope="col">Current Call</th>
                <th scope="col">Languages</th>
                <th scope="col">Teams</th>
                <th scope="col">Last Seen</th>
              </tr>
            </thead>
            <tbody>
              {officers.length === 0 ? (
                <tr>
                  <td colSpan={6} style={{ textAlign: 'center', padding: '1.5rem', color: 'var(--ops-ink-muted)' }}>
                    No officers registered on duty.
                  </td>
                </tr>
              ) : (
                officers.map((o) => (
                  <tr key={o.user_id}>
                    <td>
                      <strong>{o.display_name || o.user_id}</strong>
                    </td>
                    <td>
                      <span className={`cq-chip cq-chip--${o.status === 'available' ? 'good' : o.status === 'on_call' ? 'info' : o.status === 'offline' ? 'neutral' : 'warn'}`}>
                        {o.status.replace(/_/g, ' ')}
                      </span>
                    </td>
                    <td>
                      {o.current_call_id ? (
                        <button
                          type="button"
                          className="cw-rail-link-btn"
                          onClick={() => onOpenCall?.(o.current_call_id)}
                        >
                          #{o.current_call_id.slice(0, 8)}
                        </button>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td>{o.languages?.join(', ') || 'en'}</td>
                    <td>{o.teams?.join(', ') || 'general'}</td>
                    <td className="ops-tabular-num">
                      {formatClock(Math.max(0, now / 1000 - o.last_seen))} ago
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </TableScroll>
      </section>
    </div>
  );
}

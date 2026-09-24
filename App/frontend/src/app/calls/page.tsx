'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import StaffGuard, { type StaffIdentity } from '@/components/StaffGuard';
import { OpsPage } from '@/components/ops/OpsPage';
import { EmptyState, ErrorState, SkeletonRows } from '@/components/ops/States';
import { CallQueue, queueOrder } from '@/components/staff/calls/CallQueue';
import { CallRow } from '@/components/staff/calls/CallRow';
import { CallWorkspace } from '@/components/staff/calls/CallWorkspace';
import SloGaugeCard from '@/components/charts/SloGaugeCard';
import { ChartNote } from '@/components/charts/chartTheme';
import { useCallMetrics, useCalls } from '@/hooks/useCalls';
import { isTypingTarget } from '@/lib/ticketUi';
import { acquireLobby } from '@/services/callLobbySocket';
import { takeCall } from '@/services/officerCallSession';
import { queueSections, useCallConsoleStore } from '@/store/useCallConsoleStore';
import '@/styles/call/call.css';
import './calls.css';
import '@/app/agent/agent.css';

const CALL_TABS = ['desk', 'history', 'performance'] as const;
type CallTab = (typeof CALL_TABS)[number];

const TAB_LABEL: Record<CallTab, string> = {
  desk: 'Desk',
  history: 'History',
  performance: 'Performance',
};

function readUrl(): { tab: CallTab; call: string | null } {
  if (typeof window === 'undefined') return { tab: 'desk', call: null };
  const p = new URLSearchParams(window.location.search);
  const tab = p.get('tab');
  return {
    tab: (CALL_TABS as readonly string[]).includes(tab || '') ? (tab as CallTab) : 'desk',
    call: p.get('call'),
  };
}

function writeUrl(tab: CallTab, call: string | null): void {
  if (typeof window === 'undefined') return;
  const p = new URLSearchParams();
  if (tab !== 'desk') p.set('tab', tab);
  if (call) p.set('call', call);
  const qs = p.toString();
  try {
    window.history.replaceState(null, '', qs ? `${window.location.pathname}?${qs}` : window.location.pathname);
  } catch {
    /* jsdom without a writable Location */
  }
}

/**
 * The officer's Call Desk (docs/plans/officer-call-desk-plan.md §3.2): the
 * live queue and one call in the workspace, plus history and performance.
 * `?tab=` and `?call=` make any view a link.
 */
export function StaffCalls({ who }: { who?: StaffIdentity }) {
  const role = who?.role || 'ura_staff';
  const canTake = role === 'ura_staff' || role === 'ura_admin';
  // StaffGuard renders this only on the client, after the identity check, so
  // the URL (a shared or refreshed link) is there to read on the first render.
  // A call another page asked for ("Preview", "Back to call") wins over it.
  const [tab, setTab] = useState<CallTab>(() => (useCallConsoleStore.getState().deskRequest ? 'desk' : readUrl().tab));
  const [selectedId, setSelectedId] = useState<string | null>(
    () => useCallConsoleStore.getState().deskRequest ?? readUrl().call,
  );

  const calls = useCallConsoleStore((s) => s.calls);
  const activeCall = useCallConsoleStore((s) => s.activeCall);
  const lobbyStatus = useCallConsoleStore((s) => s.lobbyStatus);
  const resync = useCallConsoleStore((s) => s.resync);

  const { data: liveData } = useCalls('live');
  const { data: historyData, isLoading: loadingHistory, error: historyError } = useCalls('ended');
  const { data: metricsData, isLoading: loadingMetrics } = useCallMetrics(7);

  useEffect(() => acquireLobby(), []);

  // Without the lobby socket, the 5 s poll keeps the queue honest.
  useEffect(() => {
    if (liveData && lobbyStatus !== 'open') resync(liveData.calls ?? []);
  }, [liveData, lobbyStatus, resync]);

  // An alert's "Preview" or the dock's "Back to call": taken on the first
  // render if it came with the navigation here, and live while on this page.
  useEffect(() => {
    useCallConsoleStore.getState().requestDesk(null);
    return useCallConsoleStore.subscribe((state, prev) => {
      if (!state.deskRequest || state.deskRequest === prev.deskRequest) return;
      setTab('desk');
      setSelectedId(state.deskRequest);
      state.requestDesk(null);
    });
  }, []);

  useEffect(() => writeUrl(tab, selectedId), [tab, selectedId]);

  const myCallId = activeCall && activeCall.state !== 'idle' ? activeCall.callId : null;
  const sections = useMemo(() => queueSections(calls, myCallId), [calls, myCallId]);
  const order = useMemo(() => queueOrder(sections), [sections]);
  const historyCalls = useMemo(() => historyData?.calls || [], [historyData]);

  // A chosen call stays chosen — the workspace shows it ended when it ends.
  // Nothing chosen: my call, else the head of the queue (longest wait first).
  const deskCallId = tab === 'desk' ? selectedId ?? myCallId ?? order[0] ?? null : null;
  const historyCallId = tab === 'history' ? selectedId ?? historyCalls[0]?.call_id ?? null : null;

  const select = useCallback((id: string) => setSelectedId(id), []);

  // J / K walk the queue, A takes the call in view (or the longest wait), Enter
  // moves focus into the workspace. Ignored while typing.
  useEffect(() => {
    if (tab !== 'desk') return;
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey || isTypingTarget(event.target)) return;
      const key = event.key;
      if ((key === 'j' || key === 'k' || key === 'ArrowDown' || key === 'ArrowUp') && order.length) {
        event.preventDefault();
        const idx = deskCallId ? order.indexOf(deskCallId) : -1;
        const delta = key === 'j' || key === 'ArrowDown' ? 1 : -1;
        setSelectedId(order[Math.max(0, Math.min(order.length - 1, (idx < 0 ? 0 : idx) + delta))]);
      } else if ((key === 'a' || key === 'A') && canTake && !myCallId) {
        event.preventDefault();
        const button = document.querySelector<HTMLButtonElement>('.cw [data-action="take"]');
        if (button && !button.disabled) {
          button.click();
        } else if (sections.waiting[0]) {
          setSelectedId(sections.waiting[0].call_id);
          void takeCall(sections.waiting[0].call_id).catch(() => {});
        }
      } else if (key === 'Enter' && deskCallId) {
        document.querySelector<HTMLElement>('.cw button, .cw a')?.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [tab, order, deskCallId, canTake, myCallId, sections.waiting]);

  const waitingCount = sections.waiting.length;

  return (
    <OpsPage title="Phone Calls" description="Live calls, callers waiting for an officer, and how the receptionist is doing">
      <div className="ag-tabbar">
        <div className="ag-tabs" role="tablist">
          {CALL_TABS.map((t) => (
            <button
              key={t}
              role="tab"
              aria-selected={tab === t}
              className={`ag-tab ${tab === t ? 'ag-tab--active' : ''}`}
              onClick={() => setTab(t)}
            >
              {TAB_LABEL[t]}
              {t === 'desk' && waitingCount > 0 && <span className="ag-tab-count">{waitingCount}</span>}
            </button>
          ))}
        </div>
        {tab === 'desk' && (
          <span className="ag-hints calls-hints">
            <kbd>J</kbd>/<kbd>K</kbd> move · <kbd>A</kbd> take · <kbd>M</kbd> mute · <kbd>E</kbd> end
          </span>
        )}
      </div>

      {tab === 'performance' ? (
        <div className="calls-perf">
          {loadingMetrics ? (
            <SkeletonRows rows={3} />
          ) : metricsData ? (
            <>
              <div className="calls-perf-gauges">
                <SloGaugeCard
                  label="Containment rate"
                  value={Math.round((metricsData.containment_rate || 0) * 100)}
                  target={60}
                  unit="%"
                  note="Calls resolved by AI without requiring officer escalation"
                  term="Containment"
                />
                <SloGaugeCard
                  label="Clarification success"
                  value={Math.round((metricsData.clarification_first_try_rate || 0) * 100)}
                  target={70}
                  unit="%"
                  note="Caller confirmed AI clarification hypothesis on the first attempt"
                  term="First-try accuracy"
                />
                <SloGaugeCard
                  label="p95 first audio latency"
                  value={Math.round((metricsData.latency_p95_ms || 0) / 100) / 10}
                  target={3.0}
                  unit="s"
                  invert
                  note="Time from end of caller speech to first synthesized audio response"
                  term="Turn latency"
                />
                <SloGaugeCard
                  label="Transfer rate"
                  value={Math.round((metricsData.transfer_rate || 0) * 100)}
                  target={30}
                  unit="%"
                  invert
                  note="Proportion of calls transferred to human officer workbench"
                  term="Escalation rate"
                />
              </div>
              <div className="calls-perf-stats">
                <div className="st-case-card">
                  <div className="st-metric-label">Total Calls (7d)</div>
                  <div className="st-metric-value">{metricsData.total_calls}</div>
                  <ChartNote>All simulated calls logged across the 7-day period</ChartNote>
                </div>
                <div className="st-case-card">
                  <div className="st-metric-label">Average Call Duration</div>
                  <div className="st-metric-value">{metricsData.avg_duration_s}s</div>
                  <ChartNote>Mean total connected seconds from call start to hangup</ChartNote>
                </div>
                <div className="st-case-card">
                  <div className="st-metric-label">Mean Word Confidence</div>
                  <div className="st-metric-value">{Math.round((metricsData.mean_word_prob || 0) * 100)}%</div>
                  <ChartNote>Average per-word Whisper-SALT ASR acoustic confidence</ChartNote>
                </div>
                <div className="st-case-card">
                  <div className="st-metric-label">Officer Rating</div>
                  <div className="st-metric-value">
                    {metricsData.avg_officer_rating ? `${metricsData.avg_officer_rating} / 5` : '—'}
                  </div>
                  <ChartNote>Average human review score submitted by staff officers</ChartNote>
                </div>
              </div>
            </>
          ) : (
            <EmptyState title="No metrics available" body="Make demo calls to populate metrics." />
          )}
        </div>
      ) : tab === 'desk' ? (
        <div className={`ag-split calls-split${deskCallId ? ' is-open' : ''}`}>
          <div className="ag-queue-pane">
            <CallQueue sections={sections} selectedId={deskCallId} sessionState={activeCall?.state ?? null} onSelect={select} />
          </div>
          <div className="ag-detail">
            {deskCallId ? (
              <CallWorkspace key={deskCallId} callId={deskCallId} role={role} />
            ) : (
              <EmptyState title="No call selected" body="Waiting callers and live calls appear on the left." />
            )}
          </div>
        </div>
      ) : (
        <div className={`ag-split calls-split${historyCallId ? ' is-open' : ''}`}>
          <div className="ag-queue-pane calls-history-list">
            {loadingHistory ? (
              <SkeletonRows rows={4} />
            ) : historyError ? (
              <ErrorState title="Failed to load calls" body="Unable to reach the call registry." />
            ) : historyCalls.length === 0 ? (
              <EmptyState title="No call history" body="No calls recorded yet." />
            ) : (
              historyCalls.map((c) => (
                <CallRow key={c.call_id} call={c} isSelected={historyCallId === c.call_id} onSelect={select} />
              ))
            )}
          </div>
          <div className="ag-detail">
            {historyCallId ? (
              <CallWorkspace key={historyCallId} callId={historyCallId} role={role} />
            ) : (
              <EmptyState title="No call selected" body="Pick a call on the left." />
            )}
          </div>
        </div>
      )}
    </OpsPage>
  );
}

export default function Page() {
  return (
    <StaffGuard current="/calls" requireRoles={['ura_staff', 'ura_admin', 'ura_auditor']}>
      {(who) => <StaffCalls who={who} />}
    </StaffGuard>
  );
}

'use client';

import React, { useMemo, useState } from 'react';
import StaffGuard, { type StaffIdentity } from '@/components/StaffGuard';
import { OpsPage } from '@/components/ops/OpsPage';
import { EmptyState, ErrorState, SkeletonRows } from '@/components/ops/States';
import { CallRow } from '@/components/staff/calls/CallRow';
import { CallCase } from '@/components/staff/calls/CallCase';
import SloGaugeCard from '@/components/charts/SloGaugeCard';
import { ChartNote } from '@/components/charts/chartTheme';
import { useCalls, useCallMetrics, useCallsLobby } from '@/hooks/useCalls';
import { callLanguageName } from '@/lib/callLanguage';
import '@/styles/call/call.css';
import './calls.css';
import '@/app/agent/agent.css';

const CALL_TABS = ['live', 'history', 'overview'] as const;
type CallTab = (typeof CALL_TABS)[number];

const TAB_LABEL: Record<CallTab, string> = {
  live: 'Live Calls',
  history: 'History',
  overview: 'Performance Overview',
};

export function StaffCalls({ who }: { who?: StaffIdentity }) {
  const [activeTab, setActiveTab] = useState<CallTab>('live');
  const [selectedCallId, setSelectedCallId] = useState<string | null>(null);

  const { data: liveData, isLoading: loadingLive, error: liveError } = useCalls('live');
  const { data: historyData, isLoading: loadingHistory, error: historyError } = useCalls('ended');
  const { data: metricsData, isLoading: loadingMetrics } = useCallMetrics(7);
  const { liveBanner, clearBanner } = useCallsLobby();

  const userRole = who?.role || 'ura_staff';

  const liveCalls = useMemo(() => liveData?.calls || [], [liveData]);
  const historyCalls = useMemo(() => historyData?.calls || [], [historyData]);

  const activeCallList = activeTab === 'live' ? liveCalls : historyCalls;

  // Selected call record
  const selectedCall = useMemo(() => {
    if (!selectedCallId && activeCallList.length > 0) {
      return activeCallList[0];
    }
    return activeCallList.find((c) => c.call_id === selectedCallId) || null;
  }, [activeCallList, selectedCallId]);

  return (
    <OpsPage
      title="Phone Calls"
      description="Simulated AI receptionist live calls, escalations, and performance"
    >
      {/* Incoming Transfer Banner */}
      {liveBanner && (
        <div
          style={{
            margin: '0 0 1rem 0',
            padding: '0.75rem 1.25rem',
            background: '#fee2e2',
            border: '1px solid #f87171',
            borderRadius: '8px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <span className="st-pill st-pill--urgent st-pill--pulse">Transfer Requested</span>
            <span style={{ fontSize: '0.875rem', fontWeight: 600, color: '#991b1b' }}>
              Caller waiting: {liveBanner.topic} ({liveBanner.reason})
              {liveBanner.language && ` · ${callLanguageName(liveBanner.language)} caller`}
            </span>
          </div>
          <button
            type="button"
            onClick={() => {
              setSelectedCallId(liveBanner.callId);
              setActiveTab('live');
              clearBanner();
            }}
            style={{
              padding: '0.35rem 0.75rem',
              background: '#dc2626',
              color: '#ffffff',
              border: 'none',
              borderRadius: '6px',
              fontWeight: 600,
              fontSize: '0.8125rem',
              cursor: 'pointer',
            }}
          >
            Take Case →
          </button>
        </div>
      )}

      {/* Navigation Tabs */}
      <div className="ag-tabs" role="tablist">
        {CALL_TABS.map((tab) => (
          <button
            key={tab}
            role="tab"
            aria-selected={activeTab === tab}
            className={`ag-tab ${activeTab === tab ? 'ag-tab--active' : ''}`}
            onClick={() => setActiveTab(tab)}
          >
            {TAB_LABEL[tab]}
            {tab === 'live' && liveCalls.length > 0 && (
              <span className="ag-tab-count">{liveCalls.length}</span>
            )}
          </button>
        ))}
      </div>

      {/* Main Tab Content */}
      {activeTab === 'overview' ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem', marginTop: '1rem' }}>
          {loadingMetrics ? (
            <div style={{ padding: '2rem' }}>Loading performance metrics…</div>
          ) : metricsData ? (
            <>
              {/* SLO Gauge Cards */}
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))',
                  gap: '1rem',
                }}
              >
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

              {/* Summary Metrics Row */}
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
                  gap: '1rem',
                }}
              >
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
                  <div className="st-metric-value">
                    {Math.round((metricsData.mean_word_prob || 0) * 100)}%
                  </div>
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
      ) : (
        /* Split view for Live & History tabs */
        <div className="ag-split" style={{ marginTop: '1rem', height: 'calc(100vh - 220px)', minHeight: '500px' }}>
          {/* Left: Queue List */}
          <div className="ag-list-col" style={{ width: '320px', overflowY: 'auto', borderRight: '1px solid var(--border-default, #e5e7eb)' }}>
            {(activeTab === 'live' ? loadingLive : loadingHistory) ? (
              <SkeletonRows rows={4} />
            ) : (activeTab === 'live' ? liveError : historyError) ? (
              <ErrorState title="Failed to load calls" body="Unable to reach the call registry." />
            ) : activeCallList.length === 0 ? (
              <EmptyState
                title={activeTab === 'live' ? 'No live calls' : 'No call history'}
                body={activeTab === 'live' ? 'No taxpayers currently on the line.' : 'No calls recorded yet.'}
              />
            ) : (
              activeCallList.map((c) => (
                <CallRow
                  key={c.call_id}
                  call={c}
                  isSelected={selectedCall?.call_id === c.call_id}
                  onSelect={(id) => setSelectedCallId(id)}
                />
              ))
            )}
          </div>

          {/* Right: Case Detail View */}
          <div className="ag-case-col" style={{ flex: 1, minWidth: 0, height: '100%' }}>
            {selectedCall ? (
              <CallCase initialCall={selectedCall} userRole={userRole} />
            ) : (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-secondary)' }}>
                Select a call from the list to view live audio and transcript.
              </div>
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

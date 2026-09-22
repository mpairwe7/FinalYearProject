import React from 'react';

interface CallMetricsCardProps {
  metrics: {
    duration_s?: number;
    caller_turns?: number;
    clarifications_asked?: number;
    mean_word_prob?: number;
    latency?: {
      turn_to_audio_ms_p50?: number;
      turn_to_audio_ms_p95?: number;
    };
  } | null;
}

export function CallMetricsCard({ metrics }: CallMetricsCardProps) {
  if (!metrics) return null;

  return (
    <div className="st-case-card" style={{ marginBottom: '1rem' }}>
      <h4 style={{ margin: '0 0 0.75rem 0', fontSize: '0.9375rem', fontWeight: 600 }}>Call Performance Metrics</h4>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))', gap: '0.75rem' }}>
        <div className="st-metric-tile">
          <div className="st-metric-label">Duration</div>
          <div className="st-metric-value">{metrics.duration_s || 0}s</div>
        </div>
        <div className="st-metric-tile">
          <div className="st-metric-label">Turns</div>
          <div className="st-metric-value">{metrics.caller_turns || 0}</div>
        </div>
        <div className="st-metric-tile">
          <div className="st-metric-label">Clarifications</div>
          <div className="st-metric-value">{metrics.clarifications_asked || 0}</div>
        </div>
        <div className="st-metric-tile">
          <div className="st-metric-label">Avg Word Conf</div>
          <div className="st-metric-value">
            {metrics.mean_word_prob !== undefined ? `${Math.round(metrics.mean_word_prob * 100)}%` : 'N/A'}
          </div>
        </div>
        <div className="st-metric-tile">
          <div className="st-metric-label">Turn Latency p50</div>
          <div className="st-metric-value">
            {metrics.latency?.turn_to_audio_ms_p50 ? `${metrics.latency.turn_to_audio_ms_p50}ms` : '—'}
          </div>
        </div>
        <div className="st-metric-tile">
          <div className="st-metric-label">Turn Latency p95</div>
          <div className="st-metric-value">
            {metrics.latency?.turn_to_audio_ms_p95 ? `${metrics.latency.turn_to_audio_ms_p95}ms` : '—'}
          </div>
        </div>
      </div>
    </div>
  );
}

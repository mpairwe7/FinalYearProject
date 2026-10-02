'use client';

import React, { useState } from 'react';
import { formatClock, useNow } from '@/hooks/useNow';
import type { BriefClaim, CallBrief } from '@/services/callsApi';

const MOOD_LABEL: Record<CallBrief['sentiment'], string> = {
  calm: 'Calm',
  confused: 'Confused',
  frustrated: 'Frustrated',
  distressed: 'Distressed',
};

function Evidence({ seqs, onEvidence }: { seqs: number[]; onEvidence: (seq: number) => void }) {
  if (!seqs.length) return null;
  return (
    <span className="cb-evidence">
      {seqs.map((seq) => (
        <button
          key={seq}
          type="button"
          className="cb-ev"
          aria-label={`Show turn ${seq} in the transcript`}
          onClick={() => onEvidence(seq)}
        >
          ↗{seq}
        </button>
      ))}
    </span>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="cb-row">
      <dt className="cb-label">{label}</dt>
      <dd className="cb-value">{children}</dd>
    </div>
  );
}

function Claim({ claim, onEvidence }: { claim: BriefClaim; onEvidence: (seq: number) => void }) {
  return (
    <span className="cb-claim">
      {claim.text} <Evidence seqs={claim.turn_seqs} onEvidence={onEvidence} />
    </span>
  );
}

/**
 * The officer's brief (plan §3.3 A): the caller understood in ten seconds,
 * every line one click from the transcript turn it came from.
 */
export function CallBriefCard({
  brief,
  loading,
  refreshing,
  onRefresh,
  onEvidence,
  compact = false,
}: {
  brief: CallBrief | null;
  loading: boolean;
  refreshing: boolean;
  onRefresh: () => void;
  onEvidence: (seq: number) => void;
  /** One line with the caller's goal, expandable — for a call already in progress. */
  compact?: boolean;
}) {
  const now = useNow();
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);

  if (!brief) {
    return (
      <div className="cb-card cb-card--pending" role="status">
        {loading || refreshing ? 'Writing the brief…' : 'No brief yet — the caller has not said enough.'}
      </div>
    );
  }

  if (compact && !expanded) {
    return (
      <div className="cb-card cb-card--compact">
        <span className="cb-label">Brief</span>
        <span className="cb-value">{brief.caller_goal.text}</span>
        <button type="button" className="cc-btn cc-btn--quiet" aria-expanded={false} onClick={() => setExpanded(true)}>
          Expand
        </button>
      </div>
    );
  }

  const copyOpener = async () => {
    try {
      await navigator.clipboard.writeText(brief.suggested_opener);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable — the line is on screen */
    }
  };

  return (
    <section className="cb-card" aria-label="Brief for the officer">
      <dl className="cb-rows">
        {brief.why_officer && (
          <Row label="Why they need you">
            <Claim claim={brief.why_officer} onEvidence={onEvidence} />
          </Row>
        )}
        <Row label="Asked">
          <Claim claim={brief.caller_goal} onEvidence={onEvidence} />
        </Row>
        {brief.ai_already_said.length > 0 && (
          <Row label="AI already said">
            {brief.ai_already_said.map((c, i) => (
              <Claim key={i} claim={c} onEvidence={onEvidence} />
            ))}
          </Row>
        )}
        {brief.still_open.length > 0 && (
          <Row label="Still open">
            {brief.still_open.map((c, i) => (
              <Claim key={i} claim={c} onEvidence={onEvidence} />
            ))}
          </Row>
        )}
        {brief.details_given.length > 0 && (
          <Row label="Details given">
            {brief.details_given.map((d, i) => (
              <span key={i} className="cb-claim">
                {d.label} ({d.value}) <Evidence seqs={d.turn_seqs} onEvidence={onEvidence} />
              </span>
            ))}
          </Row>
        )}
        <Row label="Mood">{MOOD_LABEL[brief.sentiment] ?? brief.sentiment}</Row>
        {brief.suggested_opener && (
          <Row label="Say first">
            <span className="cb-claim cb-opener">“{brief.suggested_opener}”</span>
            <button type="button" className="cc-btn cc-btn--quiet" onClick={copyOpener}>
              {copied ? 'Copied' : 'Copy'}
            </button>
          </Row>
        )}
      </dl>
      <div className="cb-foot">
        <span>
          Updated {formatClock(now / 1000 - brief.generated_at)} ago · covers {brief.turns_covered} turns ·{' '}
          {brief.fallback ? 'no model answered — check the transcript' : brief.model}
        </span>
        <span className="cb-foot-actions">
          {compact && (
            <button type="button" className="cc-btn cc-btn--quiet" onClick={() => setExpanded(false)}>
              Collapse
            </button>
          )}
          <button type="button" className="cc-btn cc-btn--quiet" onClick={onRefresh} disabled={refreshing}>
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        </span>
      </div>
    </section>
  );
}

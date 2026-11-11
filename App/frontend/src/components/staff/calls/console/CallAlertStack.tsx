'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { callLanguageName } from '@/lib/callLanguage';
import { callTopicLabel } from '@/lib/callTopic';
import { useMyUserId } from '@/hooks/useMyUserId';
import { formatClock, useNow } from '@/hooks/useNow';
import { claimRefusalMessage } from '@/services/callsApi';
import { playAlertChime } from '@/services/callTones';
import { takeCall } from '@/services/officerCallSession';
import { alertCalls, reservedForMe, useCallConsoleStore, waitingCalls } from '@/store/useCallConsoleStore';
import { CallAlertToast } from './CallAlertToast';

/** How long "Taken by …" stays on other officers' screens. */
const TAKEN_NOTICE_MS = 3000;
const TITLE_BADGE = /^\(\d+\) Caller waiting — /;

let chimeContext: AudioContext | null = null;
function chime(): void {
  try {
    if (typeof window === 'undefined' || !window.AudioContext) return;
    chimeContext = chimeContext || new AudioContext();
    playAlertChime(chimeContext);
  } catch {
    /* no audio device — the card and the title still say it */
  }
}

function notify(title: string, body: string): void {
  try {
    if (typeof document === 'undefined' || !document.hidden) return;
    if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return;
    new Notification(title, { body, tag: 'ura-call-waiting' });
  } catch {
    /* notifications unavailable */
  }
}

/**
 * Transfer alerts, bottom right of every staff page (plan §3.1). Cards for an
 * officer who is Available and not on a call; everyone else — and auditors —
 * get the counter in the call bar only.
 */
export function CallAlertStack({
  canTake,
  onPreview,
}: {
  /** Auditors watch: no cards, no sound. */
  canTake: boolean;
  onPreview: (callId: string) => void;
}) {
  const { calls, dismissed, availability, activeCall, takenBy, soundEnabled, dismissAlert } = useCallConsoleStore(
    useShallow((s) => ({
      calls: s.calls,
      dismissed: s.dismissed,
      availability: s.availability,
      activeCall: s.activeCall,
      takenBy: s.takenBy,
      soundEnabled: s.soundEnabled,
      dismissAlert: s.dismissAlert,
    })),
  );
  const now = useNow();
  const myUserId = useMyUserId();
  const nowSeconds = Math.floor(now / 1000);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [announcement, setAnnouncement] = useState('');
  const announced = useRef<Set<string>>(new Set());

  const alerts = useMemo(
    // The clock is an input: a reservation that runs out starts ringing for
    // everyone even if its `call.unreserved` event was missed.
    () => (canTake ? alertCalls({ calls, dismissed, availability, activeCall, myUserId, nowSeconds }) : []),
    [canTake, calls, dismissed, availability, activeCall, myUserId, nowSeconds],
  );
  const waitingCount = useMemo(() => waitingCalls(calls).length, [calls]);

  // A new waiting caller: announce once, chime once, notify a hidden tab.
  useEffect(() => {
    for (const call of alerts) {
      if (announced.current.has(call.call_id)) continue;
      announced.current.add(call.call_id);
      const topic = callTopicLabel(call.topic) || 'General tax support';
      const language = callLanguageName(call.language);
      setAnnouncement(`Caller waiting: ${topic}, ${language}`);
      if (soundEnabled) chime();
      notify('Caller waiting for an officer', `${topic} · ${language}`);
    }
  }, [alerts, soundEnabled]);

  // The tab says so too, on every staff page, until nobody is waiting.
  useEffect(() => {
    if (typeof document === 'undefined' || !canTake) return;
    const base = document.title.replace(TITLE_BADGE, '');
    document.title = waitingCount > 0 ? `(${waitingCount}) Caller waiting — ${base}` : base;
  }, [waitingCount, canTake]);
  useEffect(
    () => () => {
      if (typeof document !== 'undefined') document.title = document.title.replace(TITLE_BADGE, '');
    },
    [],
  );

  const take = async (callId: string) => {
    setBusyId(callId);
    setErrors((e) => ({ ...e, [callId]: '' }));
    try {
      await takeCall(callId);
    } catch (err) {
      setErrors((e) => ({ ...e, [callId]: claimRefusalMessage(err) }));
    } finally {
      setBusyId(null);
    }
  };

  const taken = canTake
    ? Object.entries(takenBy)
        .filter(([id, t]) => now - t.at < TAKEN_NOTICE_MS && id !== activeCall?.callId && !dismissed[id])
        .map(([id, t]) => ({ id, name: t.name }))
    : [];

  if (!canTake) return null;

  const longest = alerts.reduce<(typeof alerts)[number] | null>(
    (oldest, c) => (!oldest || (c.waiting_since ?? 0) < (oldest.waiting_since ?? 0) ? c : oldest),
    null,
  );

  return (
    <>
      <div className="cc-sr-only" aria-live="polite">
        {announcement}
      </div>
      {(alerts.length > 0 || taken.length > 0) && (
        <div className="cc-alerts" role="region" aria-label="Callers waiting for an officer">
          {taken.map((t) => (
            <div key={`taken-${t.id}`} className="cc-alert cc-alert--taken" role="status">
              Taken by {t.name || 'another officer'}
            </div>
          ))}
          {alerts.length > 2 && longest ? (
            <div className="cc-alert" role="status">
              <div className="cc-alert-title">{alerts.length} callers waiting</div>
              <div className="cc-alert-meta">
                Longest {formatClock(now / 1000 - (longest.waiting_since ?? now / 1000))}
              </div>
              <div className="cc-alert-actions">
                <button type="button" className="cc-btn cc-btn--primary" onClick={() => onPreview(longest.call_id)}>
                  Open queue
                </button>
              </div>
            </div>
          ) : (
            alerts.map((call) => (
              <CallAlertToast
                key={call.call_id}
                call={call}
                canTake={canTake}
                passedToMe={reservedForMe(call, myUserId, nowSeconds)}
                busy={busyId === call.call_id}
                error={errors[call.call_id] || null}
                onTake={() => take(call.call_id)}
                onPreview={() => onPreview(call.call_id)}
                onDismiss={() => dismissAlert(call.call_id)}
              />
            ))
          )}
        </div>
      )}
    </>
  );
}

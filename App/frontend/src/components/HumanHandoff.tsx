"use client";

import React, { useCallback, useState } from 'react';
import { authHeaders } from '../lib/authSession';
import { getAnalyticsSessionId } from '../store/useAnalyticsStore';
import { useTranslation } from '../lib/i18n';
import { LoadingDots, UserIcon } from './Icons';

/**
 * "Talk to a person" — the taxpayer's own way into the officer queue.
 *
 * Every other route into that queue is a judgement the system makes on the
 * taxpayer's behalf: the supervisor's ESCALATE route, the response judge
 * escalating an answer it doubts, the `escalate_to_human` tool the model may
 * call mid-turn. Someone who has simply decided the assistant cannot help
 * them had no way to say so — the escalation banner listed phone numbers,
 * which asks them to start the whole conversation over with a stranger who
 * cannot see any of it.
 *
 * POST /v1/escalate attaches the transcript to a ticket, routes it to the
 * owning team, and — this is the part that makes it worth pressing — the
 * officer's reply comes back into *this* conversation, which the delivery
 * path in service.py already handles. So the promise made here is one the
 * system keeps.
 *
 * The reference is shown because a taxpayer who does phone the contact centre
 * can quote it, and because a request that vanishes without a receipt reads
 * as not having happened.
 */

type State = 'idle' | 'requesting' | 'queued' | 'failed';

interface HumanHandoffProps {
  conversationId: string | null;
  locale: string;
  /** What the taxpayer last asked — the officer's first piece of context. */
  reason?: string;
}

export default function HumanHandoff({ conversationId, locale, reason }: HumanHandoffProps) {
  const t = useTranslation();
  const [state, setState] = useState<State>('idle');
  const [ticketRef, setTicketRef] = useState('');
  const [message, setMessage] = useState('');

  const request = useCallback(async () => {
    setState('requesting');
    try {
      const res = await fetch('/api/v1/escalate', {
        method: 'POST',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          conversation_id: conversationId || undefined,
          session_id: getAnalyticsSessionId(),
          reason: (reason || '').slice(0, 1000),
          locale,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok || !body?.ok) {
        // The backend already says what to do instead — it knows whether the
        // queue is off or the write failed, and this component does not.
        setState('failed');
        setMessage(body?.message || t('handoff.failed'));
        return;
      }
      setState('queued');
      // Short enough to read back over a phone, long enough to be unique in a
      // queue an officer is looking at.
      setTicketRef(String(body.ticket_id || '').slice(0, 8));
      setMessage(body.message || t('handoff.queued'));
    } catch {
      setState('failed');
      setMessage(t('handoff.offline'));
    }
  }, [conversationId, locale, reason, t]);

  if (state === 'queued') {
    return (
      <div className="handoff handoff-queued" role="status">
        <p className="handoff-msg">{message}</p>
        {ticketRef && (
          <p className="handoff-ref">
            {t('handoff.reference')} <code>{ticketRef}</code>
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="handoff">
      <button
        type="button"
        className="handoff-btn"
        onClick={request}
        disabled={state === 'requesting'}
        data-testid="handoff-request"
      >
        {state === 'requesting' ? <LoadingDots /> : <UserIcon />}
        <span>{state === 'requesting' ? t('handoff.requesting') : t('handoff.ask')}</span>
      </button>
      {state === 'failed' && (
        <p className="handoff-msg handoff-failed" role="alert">
          {message}
        </p>
      )}
    </div>
  );
}

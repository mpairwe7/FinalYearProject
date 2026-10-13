'use client';

/**
 * The taxpayer's side of a live chat with a URA officer.
 *
 * Once a conversation has a support case, this follows the case's thread
 * (`GET /v1/escalate/{id}/messages`) every few seconds — slower when the tab
 * is hidden, backing off on errors — and turns what it hears into the chat:
 *
 *  - `waiting`: the case is in the queue; the assistant still answers.
 *  - `officer`: an officer took the case. A notice says who joined, their
 *    messages appear as officer bubbles, and the composer sends to them
 *    (`sendToOfficer`) while the assistant is paused.
 *  - `ended`: the officer ended the chat. The rating dialog opens once; after
 *    it is sent or skipped a notice hands the taxpayer back to the assistant
 *    and the case is cleared from the conversation.
 *
 * While an answer is streaming (`hold`) nothing is appended: the stream
 * writes into the last turn, so a bubble added under it would be overwritten.
 * The poll waits and catches up the moment the answer finishes.
 *
 * What has been shown is kept per conversation (`officerChat` in the chat
 * store: last thread number and state), and every bubble and notice has an id
 * derived from the case, so a reload or a second poll never shows one twice.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useChatStore, type ChatTurn, type OfficerChatState, type OfficerChatSync } from '../store/useChatStore';
import { analyticsApi } from '../services/analyticsApi';
import { useTranslation } from '../lib/i18n';

const VISIBLE_MS = 3_000;
const HIDDEN_MS = 10_000;
const MAX_BACKOFF_MS = 30_000;

type PollOutcome = 'continue' | 'stop' | 'error';

function statusOf(error: unknown): number | null {
  const match = error instanceof Error ? /^(\d{3})\b/.exec(error.message) : null;
  return match ? Number(match[1]) : null;
}

export interface OfficerChat {
  /** Where the chat stands; null when the conversation has no case. */
  state: OfficerChatState | null;
  officerName: string;
  /** The taxpayer's messages go to the officer, not the assistant. */
  withOfficer: boolean;
  /** The rating dialog should be showing. */
  ratingOpen: boolean;
  submitRating: (score: number, comment: string) => Promise<void>;
  skipRating: () => void;
  /** Send one message to the officer; false if it did not arrive. */
  sendToOfficer: (text: string) => Promise<boolean>;
  sendFailed: boolean;
}

export function useOfficerChat({ hold = false }: { hold?: boolean } = {}): OfficerChat {
  const t = useTranslation();
  const ticketId = useChatStore((s) => s.activeTicketId);
  const sync = useChatStore((s) => s.officerChat);
  const locale = useChatStore((s) => s.locale);
  const [ratingFor, setRatingFor] = useState<string | null>(null);
  const [sendFailed, setSendFailed] = useState(false);
  const pollNowRef = useRef<(() => void) | null>(null);
  const tRef = useRef(t);
  const holdRef = useRef(hold);
  // Read by the poll between renders; kept current after each render.
  useEffect(() => {
    tRef.current = t;
    holdRef.current = hold;
  }, [t, hold]);

  const current = sync && ticketId && sync.ticketId === ticketId ? sync : null;

  /** Hand the taxpayer back to the assistant: notice, then clear the case. */
  const finish = useCallback((forTicket: string, officerName?: string) => {
    const store = useChatStore.getState();
    if (store.activeTicketId !== forTicket) return;
    const id = `notice-left-${forTicket}`;
    if (!store.chat.some((turn) => turn.id === id)) {
      store.addTurns([
        {
          id,
          role: 'assistant',
          content: tRef.current('officerChat.ended'),
          timestamp: Date.now(),
          notice: 'officer_left',
          officerName,
        },
      ]);
    }
    setRatingFor(null);
    store.setOfficerChat(null);
    store.setActiveTicketId(null);
  }, []);

  const pollOnce = useCallback(
    async (forTicket: string): Promise<PollOutcome> => {
      const before = useChatStore.getState();
      if (before.activeTicketId !== forTicket) return 'stop';
      if (holdRef.current) return 'continue';
      const known: OfficerChatSync =
        before.officerChat && before.officerChat.ticketId === forTicket
          ? before.officerChat
          : { ticketId: forTicket, lastSeq: 0, state: 'waiting' };
      let res;
      try {
        res = await analyticsApi.publicTicketMessages(forTicket, known.lastSeq);
      } catch (error) {
        // The case is gone (or never was one this chat can follow).
        if (statusOf(error) === 404 || statusOf(error) === 422) return 'stop';
        return 'error';
      }
      const store = useChatStore.getState();
      if (store.activeTicketId !== forTicket) return 'stop';
      // An answer started streaming while this was in flight: read again later.
      if (holdRef.current) return 'continue';

      const name = res.officer_name || known.officerName || '';
      const shown = new Set(store.chat.map((turn) => turn.id));
      const turns: ChatTurn[] = [];
      const joinedId = `notice-joined-${forTicket}`;
      if (res.chat_state === 'officer' && !shown.has(joinedId)) {
        turns.push({
          id: joinedId,
          role: 'assistant',
          content: tRef.current('officerChat.joined', { name: name || tRef.current('officerChat.officerLabel') }),
          timestamp: Date.now(),
          notice: 'officer_joined',
          officerName: name,
        });
      }
      for (const message of res.messages) {
        // The taxpayer's own messages are already on screen: they were added
        // when sent. A system message is the end marker, which `state` covers.
        if (message.sender !== 'officer') continue;
        const id = `officer-${forTicket}-${message.seq}`;
        if (shown.has(id) || !message.text) continue;
        turns.push({
          id,
          role: 'assistant',
          content: message.text,
          timestamp: message.created_at ? message.created_at * 1000 : Date.now(),
          retrievalMode: 'officer_reply',
          officerName: message.officer_name || name,
        });
      }
      if (turns.length) store.addTurns(turns);

      const next: OfficerChatSync = {
        ticketId: forTicket,
        lastSeq: Math.max(known.lastSeq, res.last_seq || 0),
        state: res.chat_state,
        ...(name ? { officerName: name } : {}),
      };
      const changed =
        next.lastSeq !== known.lastSeq || next.state !== known.state || next.officerName !== known.officerName;
      if (changed || !store.officerChat) store.setOfficerChat(next);
      else if (turns.length) store.saveCurrentSession();

      if (res.chat_state === 'none') return 'stop';
      if (res.chat_state === 'ended') {
        if (res.csat.requested && !res.csat.submitted) setRatingFor(forTicket);
        else finish(forTicket, name);
        return 'stop';
      }
      return 'continue';
    },
    [finish],
  );

  useEffect(() => {
    if (!ticketId) return;
    let cancelled = false;
    // Set once the chat needs no more polling (ended, a call, or gone), so a
    // tab coming back into view or an answer finishing cannot restart it.
    let stopped = false;
    let inFlight = false;
    let failures = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const interval = () =>
      typeof document !== 'undefined' && document.visibilityState === 'hidden' ? HIDDEN_MS : VISIBLE_MS;

    const tick = async () => {
      if (cancelled || stopped || inFlight) return;
      inFlight = true;
      let outcome: PollOutcome = 'error';
      try {
        outcome = await pollOnce(ticketId);
      } finally {
        inFlight = false;
      }
      if (outcome === 'stop') stopped = true;
      if (cancelled || stopped) return;
      failures = outcome === 'error' ? Math.min(failures + 1, 4) : 0;
      timer = setTimeout(tick, Math.min(MAX_BACKOFF_MS, interval() * 2 ** failures));
    };
    pollNowRef.current = () => {
      if (cancelled || stopped || inFlight) return;
      if (timer) clearTimeout(timer);
      void tick();
    };
    const onVisible = () => {
      if (document.visibilityState === 'visible') pollNowRef.current?.();
    };
    document.addEventListener('visibilitychange', onVisible);
    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisible);
      pollNowRef.current = null;
    };
  }, [ticketId, pollOnce]);

  // The answer finished: catch up now rather than at the next tick.
  useEffect(() => {
    if (!hold) pollNowRef.current?.();
  }, [hold]);

  const submitRating = useCallback(
    async (score: number, comment: string) => {
      if (!ticketId) return;
      try {
        await analyticsApi.submitTicketCsat(ticketId, score, comment);
      } catch (error) {
        // Already rated (another tab): nothing left to ask.
        if (statusOf(error) !== 409) throw error;
      }
      finish(ticketId, current?.officerName);
    },
    [ticketId, current?.officerName, finish],
  );

  const skipRating = useCallback(() => {
    if (!ticketId) return;
    // A skip is an answer the audit counts; the hand-back does not wait on it.
    analyticsApi.submitTicketCsat(ticketId, null).catch(() => undefined);
    finish(ticketId, current?.officerName);
  }, [ticketId, current?.officerName, finish]);

  const sendToOfficer = useCallback(
    async (text: string) => {
      if (!ticketId) return false;
      setSendFailed(false);
      try {
        await analyticsApi.replyToPublicTicket(ticketId, text, locale);
      } catch (error) {
        // 409: the officer ended the chat a moment ago — the next poll says so.
        if (statusOf(error) === 409) pollNowRef.current?.();
        else setSendFailed(true);
        return false;
      }
      const store = useChatStore.getState();
      store.addTurns([{ id: `taxpayer-${ticketId}-${Date.now()}`, role: 'user', content: text, timestamp: Date.now() }]);
      store.saveCurrentSession();
      pollNowRef.current?.();
      return true;
    },
    [ticketId, locale],
  );

  const state: OfficerChatState | null = ticketId ? current?.state ?? 'waiting' : null;
  return {
    state,
    officerName: current?.officerName || '',
    withOfficer: state === 'officer',
    ratingOpen: Boolean(ticketId && ratingFor === ticketId),
    submitRating,
    skipRating,
    sendToOfficer,
    sendFailed,
  };
}

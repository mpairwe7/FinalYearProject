/**
 * The staff console's view of live phone calls — shared by every staff page.
 *
 * `StaffGuard` mounts per page, so anything a page component owns dies when the
 * officer navigates. This store (and the singletons in
 * `services/callLobbySocket.ts` / `services/officerCallSession.ts` that feed it)
 * lives at module level instead: the queue, the alerts and the call in progress
 * survive a trip to `/admin/tickets` and back. Not persisted — a reload
 * resyncs from `GET /v1/admin/calls?status=live`.
 *
 * Lobby events carry metadata only (topic, priority, language, wait); the
 * transcript and the brief are fetched per call.
 */

import { create } from 'zustand';
import type { CallRecord } from '@/services/callsApi';

export type LiveCallStatus = 'ai' | 'transferring' | 'bridged';

export interface LobbyCall {
  call_id: string;
  status: LiveCallStatus;
  started_at: number;
  topic: string;
  priority: string;
  language: string;
  /** When the caller started waiting for an officer (transferring only). */
  waiting_since: number | null;
  reason: string;
  ticket_ref: string;
  attempt: number;
  claimed_by: string;
  claimed_name: string;
  officer_name: string;
  needs_callback: boolean;
}

export type Availability = 'available' | 'busy' | 'away';

/** The officer's own call, mirrored from `officerCallSession`. */
export type SessionState = 'idle' | 'claiming' | 'connecting' | 'bridged' | 'ending';

export interface ActiveCall {
  callId: string;
  state: SessionState;
  /** When the current state began — the dock's timer on a bridged call. */
  since: number;
  muted: boolean;
  officerName: string;
  error: string | null;
}

export type LobbyStatus = 'idle' | 'connecting' | 'open' | 'retrying' | 'disabled';

interface LobbyEvent {
  type: string;
  data?: Record<string, unknown>;
}

interface CallConsoleState {
  calls: Record<string, LobbyCall>;
  dismissed: Record<string, true>;
  /** "Taken by Officer Nakato" — shown on everyone else's alert for a moment. */
  takenBy: Record<string, { name: string; at: number }>;
  availability: Availability;
  soundEnabled: boolean;
  micReady: boolean;
  lobbyStatus: LobbyStatus;
  activeCall: ActiveCall | null;
  /** A page asked the Desk to open this call (alert "Preview", dock "Back to call"). */
  deskRequest: string | null;
  /** The call the Desk is showing right now — the dock hides when it is the officer's own. */
  deskFocus: string | null;
  /** "End call?" is being asked (E pressed, or End clicked) — the dock or the Desk shows it. */
  confirmEnd: boolean;

  applyLobbyEvent: (event: LobbyEvent) => void;
  resync: (records: CallRecord[]) => void;
  dismissAlert: (callId: string) => void;
  setAvailability: (availability: Availability) => void;
  setSoundEnabled: (on: boolean) => void;
  setMicReady: (ready: boolean) => void;
  setLobbyStatus: (status: LobbyStatus) => void;
  setActiveCall: (call: ActiveCall | null) => void;
  requestDesk: (callId: string | null) => void;
  setDeskFocus: (callId: string | null) => void;
  setConfirmEnd: (open: boolean) => void;
  reset: () => void;
}

const PRIORITY_RANK: Record<string, number> = { urgent: 0, high: 1, normal: 2, low: 3 };

function str(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function blank(call_id: string): LobbyCall {
  return {
    call_id,
    status: 'ai',
    started_at: Date.now() / 1000,
    topic: '',
    priority: 'normal',
    language: 'en',
    waiting_since: null,
    reason: '',
    ticket_ref: '',
    attempt: 0,
    claimed_by: '',
    claimed_name: '',
    officer_name: '',
    needs_callback: false,
  };
}

function fromRecord(r: CallRecord): LobbyCall | null {
  if (r.status !== 'ai' && r.status !== 'transferring' && r.status !== 'bridged') return null;
  return {
    ...blank(r.call_id),
    status: r.status,
    started_at: r.started_at,
    topic: r.topic || '',
    priority: r.priority || 'normal',
    language: r.locale || 'en',
    waiting_since: r.status === 'transferring' ? (r.transfer_requested_at ?? r.started_at) : null,
    reason: r.transfer_reason || '',
    ticket_ref: r.ticket_id || '',
    claimed_by: r.claimed_by || '',
    officer_name: r.status === 'bridged' ? r.officer_id || '' : '',
    needs_callback: Boolean(r.needs_callback) && !r.callback_done_at,
  };
}

const INITIAL = {
  calls: {} as Record<string, LobbyCall>,
  dismissed: {} as Record<string, true>,
  takenBy: {} as Record<string, { name: string; at: number }>,
  availability: 'available' as Availability,
  soundEnabled: true,
  micReady: false,
  lobbyStatus: 'idle' as LobbyStatus,
  activeCall: null as ActiveCall | null,
  deskRequest: null as string | null,
  deskFocus: null as string | null,
  confirmEnd: false,
};

export const useCallConsoleStore = create<CallConsoleState>((set) => ({
  ...INITIAL,

  applyLobbyEvent: (event) =>
    set((state) => {
      const data = event.data || {};
      const id = str(data.call_id);
      if (!id) return state;
      const current = state.calls[id] || blank(id);
      const put = (patch: Partial<LobbyCall>) => ({ calls: { ...state.calls, [id]: { ...current, ...patch } } });

      switch (event.type) {
        case 'call.started':
          return put({ status: 'ai', started_at: num(data.started_at) ?? current.started_at });
        case 'call.language':
          return put({ language: str(data.language, current.language) });
        case 'call.transfer_requested': {
          const dismissed = { ...state.dismissed };
          delete dismissed[id]; // a new wait is a new alert
          return {
            ...put({
              status: 'transferring',
              started_at: num(data.started_at) ?? current.started_at,
              topic: str(data.topic, current.topic),
              priority: str(data.priority, 'normal'),
              language: str(data.language, current.language),
              waiting_since: num(data.waiting_since) ?? Date.now() / 1000,
              reason: str(data.reason),
              ticket_ref: str(data.ticket_ref),
              attempt: num(data.attempt) ?? current.attempt + 1,
              claimed_by: '',
              claimed_name: '',
            }),
            dismissed,
          };
        }
        case 'call.brief_ready':
          return put({
            topic: str(data.topic) || current.topic,
            priority: str(data.priority) || current.priority,
          });
        case 'call.claimed': {
          const name = str(data.officer_name);
          return {
            ...put({ claimed_by: str(data.officer_id), claimed_name: name }),
            takenBy: { ...state.takenBy, [id]: { name, at: Date.now() } },
          };
        }
        case 'call.unclaimed': {
          const takenBy = { ...state.takenBy };
          delete takenBy[id];
          return { ...put({ claimed_by: '', claimed_name: '' }), takenBy };
        }
        case 'call.bridged':
          return put({
            status: 'bridged',
            officer_name: str(data.officer_name, current.claimed_name),
            waiting_since: null,
          });
        case 'call.transfer_timed_out':
          return put({ status: 'ai', waiting_since: null, needs_callback: true, claimed_by: '', claimed_name: '' });
        case 'call.ended': {
          const calls = { ...state.calls };
          delete calls[id];
          return { calls };
        }
        default:
          return state;
      }
    }),

  resync: (records) =>
    set(() => {
      const calls: Record<string, LobbyCall> = {};
      for (const record of records ?? []) {
        const call = fromRecord(record);
        if (call) calls[call.call_id] = call;
      }
      return { calls };
    }),

  dismissAlert: (callId) => set((state) => ({ dismissed: { ...state.dismissed, [callId]: true } })),
  setAvailability: (availability) => set({ availability }),
  setSoundEnabled: (soundEnabled) => set({ soundEnabled }),
  setMicReady: (micReady) => set({ micReady }),
  setLobbyStatus: (lobbyStatus) => set({ lobbyStatus }),
  setActiveCall: (activeCall) =>
    set((state) => ({ activeCall, confirmEnd: activeCall?.state === 'bridged' ? state.confirmEnd : false })),
  requestDesk: (deskRequest) => set({ deskRequest }),
  setDeskFocus: (deskFocus) => set({ deskFocus }),
  setConfirmEnd: (confirmEnd) => set({ confirmEnd }),
  reset: () => set({ ...INITIAL }),
}));

// ---------------------------------------------------------------------------
// Selectors — pure, so the queue's ordering is tested without React.
// ---------------------------------------------------------------------------

/** Waiting for an officer and not yet claimed: highest priority, then longest wait. */
export function waitingCalls(calls: Record<string, LobbyCall>): LobbyCall[] {
  return Object.values(calls)
    .filter((c) => c.status === 'transferring' && !c.claimed_by)
    .sort(
      (a, b) =>
        (PRIORITY_RANK[a.priority] ?? 2) - (PRIORITY_RANK[b.priority] ?? 2) ||
        (a.waiting_since ?? a.started_at) - (b.waiting_since ?? b.started_at),
    );
}

export interface QueueSections {
  waiting: LobbyCall[];
  mine: LobbyCall[];
  withOfficers: LobbyCall[];
  ai: LobbyCall[];
}

/** The Desk's queue, in the order an officer reads it (plan §3.2). */
export function queueSections(calls: Record<string, LobbyCall>, myCallId: string | null): QueueSections {
  const all = Object.values(calls);
  const notMine = (c: LobbyCall) => c.call_id !== myCallId;
  return {
    waiting: waitingCalls(calls).filter(notMine),
    mine: all.filter((c) => c.call_id === myCallId),
    withOfficers: all
      .filter((c) => notMine(c) && (c.status === 'bridged' || (c.status === 'transferring' && c.claimed_by)))
      .sort((a, b) => a.started_at - b.started_at),
    ai: all.filter((c) => notMine(c) && c.status === 'ai').sort((a, b) => a.started_at - b.started_at),
  };
}

/** The calls that deserve an alert card for this officer right now. */
export function alertCalls(
  state: Pick<CallConsoleState, 'calls' | 'dismissed' | 'availability' | 'activeCall'>,
): LobbyCall[] {
  const onACall = state.activeCall !== null && state.activeCall.state !== 'idle';
  if (state.availability !== 'available' || onACall) return [];
  return waitingCalls(state.calls).filter((c) => !state.dismissed[c.call_id]);
}

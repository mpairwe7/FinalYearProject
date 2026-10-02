import { beforeEach, describe, expect, it } from 'vitest';
import {
  alertCalls,
  queueSections,
  useCallConsoleStore,
  waitingCalls,
} from '@/store/useCallConsoleStore';
import { callRecord, lobbyCall } from '../helpers/callConsole';

const store = () => useCallConsoleStore.getState();
const event = (type: string, data: Record<string, unknown>) => store().applyLobbyEvent({ type, data });

describe('useCallConsoleStore', () => {
  beforeEach(() => store().reset());

  it('builds the queue from lobby events: highest priority, then longest wait', () => {
    const now = Date.now() / 1000;
    event('call.started', { call_id: 'ai_1', started_at: now - 30 });
    event('call.transfer_requested', { call_id: 'normal_old', priority: 'normal', waiting_since: now - 50 });
    event('call.transfer_requested', { call_id: 'high_new', priority: 'high', waiting_since: now - 5 });
    event('call.transfer_requested', { call_id: 'normal_new', priority: 'normal', waiting_since: now - 10 });
    expect(waitingCalls(store().calls).map((c) => c.call_id)).toEqual(['high_new', 'normal_old', 'normal_new']);
    const sections = queueSections(store().calls, null);
    expect(sections.ai.map((c) => c.call_id)).toEqual(['ai_1']);
    expect(sections.mine).toEqual([]);
  });

  it('moves a claimed call out of waiting and says who took it', () => {
    event('call.transfer_requested', { call_id: 'c1', waiting_since: 1 });
    event('call.claimed', { call_id: 'c1', officer_id: 'nakato', officer_name: 'Officer Nakato' });
    expect(waitingCalls(store().calls)).toEqual([]);
    expect(queueSections(store().calls, null).withOfficers.map((c) => c.call_id)).toEqual(['c1']);
    expect(store().takenBy.c1.name).toBe('Officer Nakato');
    event('call.unclaimed', { call_id: 'c1', reason: 'claim_expired' });
    expect(waitingCalls(store().calls).map((c) => c.call_id)).toEqual(['c1']);
  });

  it('puts my own call in its own section', () => {
    event('call.transfer_requested', { call_id: 'c1', waiting_since: 1 });
    event('call.claimed', { call_id: 'c1', officer_id: 'me', officer_name: 'Officer Me' });
    const sections = queueSections(store().calls, 'c1');
    expect(sections.mine.map((c) => c.call_id)).toEqual(['c1']);
    expect(sections.withOfficers).toEqual([]);
  });

  it('a timed-out transfer is back with the AI and owed a callback; an ended call leaves', () => {
    event('call.transfer_requested', { call_id: 'c1', waiting_since: 1 });
    event('call.transfer_timed_out', { call_id: 'c1', ticket_ref: 'T1' });
    expect(store().calls.c1).toMatchObject({ status: 'ai', needs_callback: true, waiting_since: null });
    event('call.ended', { call_id: 'c1' });
    expect(store().calls.c1).toBeUndefined();
  });

  it('alerts only an available officer who is not on a call, and not for dismissed calls', () => {
    event('call.transfer_requested', { call_id: 'c1', waiting_since: 1 });
    event('call.transfer_requested', { call_id: 'c2', waiting_since: 2 });
    store().dismissAlert('c1');
    expect(alertCalls(store()).map((c) => c.call_id)).toEqual(['c2']);
    store().setAvailability('busy');
    expect(alertCalls(store())).toEqual([]);
    store().setAvailability('available');
    store().setActiveCall({ callId: 'x', state: 'bridged', since: 0, muted: false, officerName: '', error: null });
    expect(alertCalls(store())).toEqual([]);
    // A finished call that left a message does not count as being on one.
    store().setActiveCall({ callId: 'x', state: 'idle', since: 0, muted: false, officerName: '', error: 'The call ended.' });
    expect(alertCalls(store()).map((c) => c.call_id)).toEqual(['c2']);
  });

  it('a new transfer of a dismissed call alerts again', () => {
    event('call.transfer_requested', { call_id: 'c1', waiting_since: 1 });
    store().dismissAlert('c1');
    event('call.transfer_requested', { call_id: 'c1', waiting_since: 2, attempt: 2 });
    expect(alertCalls(store()).map((c) => c.call_id)).toEqual(['c1']);
  });

  it('resyncs from the live call list', () => {
    store().resync([
      callRecord({ call_id: 'w', status: 'transferring' }),
      callRecord({ call_id: 'gone', status: 'ended' }),
      callRecord({ call_id: 'owed', status: 'ai', needs_callback: 1, callback_done_at: null }),
    ]);
    expect(Object.keys(store().calls).sort()).toEqual(['owed', 'w']);
    expect(store().calls.w).toMatchObject({ topic: 'objection_or_dispute', priority: 'high', language: 'lg' });
    expect(store().calls.owed.needs_callback).toBe(true);
    expect(lobbyCall().call_id).toBe('call_waiting_1');
  });
});

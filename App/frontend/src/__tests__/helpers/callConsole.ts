/**
 * Test doubles for the staff call console: a WebSocket that records itself,
 * and lobby calls / call records in the shapes the backend sends.
 */
import type { CallBrief, CallRecord } from '@/services/callsApi';
import type { LobbyCall } from '@/store/useCallConsoleStore';

export class FakeSocket {
  static all: FakeSocket[] = [];
  static OPEN = 1;
  readyState = 0;
  binaryType = 'blob';
  sent: unknown[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: unknown }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(public url: string) {
    FakeSocket.all.push(this);
  }

  send(data: unknown) {
    this.sent.push(data);
  }

  close() {
    this.readyState = 3;
  }

  open() {
    this.readyState = 1;
    this.onopen?.();
  }

  emit(data: unknown) {
    this.onmessage?.({ data: typeof data === 'string' || data instanceof ArrayBuffer ? data : JSON.stringify(data) });
  }

  shut(code = 1000) {
    this.readyState = 3;
    this.onclose?.({ code });
  }

  static find(part: string): FakeSocket | undefined {
    return [...FakeSocket.all].reverse().find((s) => s.url.includes(part));
  }

  static reset() {
    FakeSocket.all = [];
  }
}

export function lobbyCall(patch: Partial<LobbyCall> = {}): LobbyCall {
  return {
    call_id: 'call_waiting_1',
    status: 'transferring',
    started_at: Date.now() / 1000 - 120,
    topic: 'objection_or_dispute',
    priority: 'normal',
    language: 'lg',
    waiting_since: Date.now() / 1000 - 12,
    reason: 'caller_requested',
    ticket_ref: 'tkt-8f2c-0000',
    attempt: 1,
    claimed_by: '',
    claimed_name: '',
    officer_name: '',
    needs_callback: false,
    ...patch,
  };
}

export function callRecord(patch: Partial<CallRecord> = {}): CallRecord {
  return {
    call_id: 'call_waiting_1',
    conversation_id: 'conv_1',
    user_id: 'taxpayer-1',
    tenant_id: 'default',
    channel: 'browser_sim',
    locale: 'lg',
    status: 'transferring',
    started_at: Date.now() / 1000 - 120,
    ended_at: null,
    end_reason: '',
    transferred: true,
    transfer_reason: 'caller_requested',
    ticket_id: 'tkt-8f2c-0000',
    officer_id: null,
    officer_rating: null,
    officer_note: null,
    topic: 'objection_or_dispute',
    priority: 'high',
    transfer_requested_at: Date.now() / 1000 - 12,
    turns: [
      {
        id: 't1', call_id: 'call_waiting_1', seq: 1, speaker: 'caller', kind: 'utterance',
        text: 'I want to object to my assessment', low_conf_words: [{ word: 'assessment', prob: 0.31 }],
        mean_word_prob: 0.7, faithfulness: null, latencies: {}, created_at: Date.now() / 1000 - 100,
      },
      {
        id: 't2', call_id: 'call_waiting_1', seq: 2, speaker: 'assistant', kind: 'answer',
        text: 'You can lodge an objection within 45 days.', low_conf_words: [], mean_word_prob: null,
        faithfulness: 0.9, latencies: {}, created_at: Date.now() / 1000 - 95,
      },
      {
        id: 't3', call_id: 'call_waiting_1', seq: 3, speaker: 'caller', kind: 'utterance',
        text: 'I need a person to look at it', low_conf_words: [], mean_word_prob: 0.9, faithfulness: null,
        latencies: {}, created_at: Date.now() / 1000 - 60,
      },
    ],
    ...patch,
  };
}

export function brief(patch: Partial<CallBrief> = {}): CallBrief {
  return {
    why_officer: { text: 'Wants a person to review the assessment', turn_seqs: [3] },
    caller_goal: { text: 'Object to a tax assessment', turn_seqs: [1] },
    ai_already_said: [{ text: 'Objections are lodged within 45 days', turn_seqs: [2] }],
    still_open: [{ text: 'Whether the 45 days have passed', turn_seqs: [] }],
    details_given: [{ label: 'TIN', value: 'given (redacted)', turn_seqs: [1] }],
    sentiment: 'frustrated',
    topic: 'Assessment objection',
    priority: 'high',
    suggested_opener: 'I can see you want your assessment looked at — let me help.',
    language: 'lg',
    turns_covered: 3,
    generated_at: Date.now() / 1000 - 4,
    model: 'gemini-2.5-flash-lite',
    fallback: false,
    ...patch,
  };
}

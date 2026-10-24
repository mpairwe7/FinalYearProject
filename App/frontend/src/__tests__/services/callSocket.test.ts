import { afterEach, describe, expect, it, vi } from 'vitest';
import { AUTH_TOKEN_STORAGE_KEY } from '@/lib/authSession';
import { CallSocket } from '@/services/callSocket';
import { FakeSocket } from '../helpers/callConsole';

describe('CallSocket authentication', () => {
  afterEach(() => {
    FakeSocket.reset();
    localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
    vi.unstubAllGlobals();
  });

  it('asks for a call-back with its own message', () => {
    vi.stubGlobal('WebSocket', FakeSocket);
    const socket = new CallSocket({ onAudio: vi.fn(), onMessage: vi.fn(), onError: vi.fn(), onClose: vi.fn() });
    socket.connect({ locale: 'en', voice_consent_accepted: true });
    const fake = FakeSocket.find('/calls/stream')!;
    fake.open();
    socket.requestCallback();
    expect(JSON.parse(String(fake.sent[fake.sent.length - 1]))).toEqual({ type: 'request_callback' });
    socket.close();
  });

  it('sends credentials in call_start, not in the WebSocket URL', () => {
    const token = 'header.payload.signature';
    localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token);
    vi.stubGlobal('WebSocket', FakeSocket);

    const socket = new CallSocket({
      onAudio: vi.fn(),
      onMessage: vi.fn(),
      onError: vi.fn(),
      onClose: vi.fn(),
    });
    socket.connect({ locale: 'en', voice_consent_accepted: true });

    const fake = FakeSocket.find('/calls/stream')!;
    expect(fake.url).not.toContain('token=');
    fake.open();
    expect(JSON.parse(String(fake.sent[0]))).toMatchObject({
      type: 'call_start',
      access_token: token,
      voice_consent_accepted: true,
    });
    socket.close();
  });

  it('names the chat the call was started from, with the session that owns it', () => {
    vi.stubGlobal('WebSocket', FakeSocket);
    const socket = new CallSocket({ onAudio: vi.fn(), onMessage: vi.fn(), onError: vi.fn(), onClose: vi.fn() });
    socket.connect({
      locale: 'lg',
      voice_consent_accepted: true,
      parent_conversation_id: 'conv-123',
      chat_session: 'sess-456',
    });
    const fake = FakeSocket.find('/calls/stream')!;
    fake.open();
    expect(JSON.parse(String(fake.sent[0]))).toMatchObject({
      type: 'call_start',
      parent_conversation_id: 'conv-123',
      chat_session: 'sess-456',
    });
    socket.close();
  });

  it('a call not started from a chat carries nothing', () => {
    vi.stubGlobal('WebSocket', FakeSocket);
    const socket = new CallSocket({ onAudio: vi.fn(), onMessage: vi.fn(), onError: vi.fn(), onClose: vi.fn() });
    socket.connect({ locale: 'en', voice_consent_accepted: true });
    const fake = FakeSocket.find('/calls/stream')!;
    fake.open();
    const sent = JSON.parse(String(fake.sent[0]));
    expect(sent).not.toHaveProperty('parent_conversation_id');
    expect(sent).not.toHaveProperty('chat_session');
    socket.close();
  });

  it('sends in-call control actions, typed text turns, and interrupts over the call socket', () => {
    vi.stubGlobal('WebSocket', FakeSocket);
    const socket = new CallSocket({ onAudio: vi.fn(), onMessage: vi.fn(), onError: vi.fn(), onClose: vi.fn() });
    socket.connect({ locale: 'en', voice_consent_accepted: true });
    const fake = FakeSocket.find('/calls/stream')!;
    fake.open();

    socket.sendControl('repeat');
    expect(JSON.parse(String(fake.sent[fake.sent.length - 1]))).toEqual({
      type: 'call_control',
      action: 'repeat',
    });

    socket.sendTextTurn('  What is my PAYE on UGX 4,000,000?  ');
    expect(JSON.parse(String(fake.sent[fake.sent.length - 1]))).toEqual({
      type: 'text_turn',
      text: 'What is my PAYE on UGX 4,000,000?',
    });

    socket.interrupt();
    expect(JSON.parse(String(fake.sent[fake.sent.length - 1]))).toEqual({ type: 'interrupt' });
    socket.close();
  });
});

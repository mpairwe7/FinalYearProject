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
});

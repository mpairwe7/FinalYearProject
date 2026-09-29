import { afterEach, describe, expect, it, vi } from 'vitest';

const sdk = vi.hoisted(() => {
  const roomEvent = {
    TrackSubscribed: 'track-subscribed',
    TrackUnsubscribed: 'track-unsubscribed',
    DataReceived: 'data-received',
    Disconnected: 'disconnected',
    Reconnecting: 'reconnecting',
    Reconnected: 'reconnected',
  };
  const rooms: Array<InstanceType<typeof MockRoom>> = [];
  class MockRoom {
    handlers = new Map<string, (...args: unknown[]) => void>();
    connect = vi.fn(async () => {});
    disconnect = vi.fn(async () => {});
    localParticipant = { setMicrophoneEnabled: vi.fn(async () => {}) };
    constructor() { rooms.push(this); }
    // `never[]` accepts every typed SDK callback; stored as `unknown[]` so the
    // test can emit the SDK's positional event arguments without `any`.
    on(event: string, callback: (...args: never[]) => void) {
      this.handlers.set(event, callback as (...args: unknown[]) => void);
      return this;
    }
    emit(event: string, ...args: unknown[]) { this.handlers.get(event)?.(...args); }
  }
  return { roomEvent, rooms, MockRoom };
});

vi.mock('livekit-client', () => ({
  Room: sdk.MockRoom,
  RoomEvent: sdk.roomEvent,
  Track: { Kind: { Audio: 'audio' } },
}));

import { LiveKitCallSession } from '@/services/liveKitCallSession';

describe('LiveKitCallSession', () => {
  afterEach(() => {
    sdk.rooms.length = 0;
    document.body.replaceChildren();
  });

  it('joins scoped WebRTC media, routes JSON data, and releases tracks on close', async () => {
    const onMessage = vi.fn();
    const onDisconnected = vi.fn();
    const onReconnecting = vi.fn();
    const onReconnected = vi.fn();
    const session = new LiveKitCallSession({ onMessage, onDisconnected, onReconnecting, onReconnected });
    await session.connect({
      transport: 'livekit',
      url: 'wss://rtc.example.test',
      room: 'ura-call-1',
      identity: 'caller-1',
      token: 'signed-room-token',
    }, false);
    const room = sdk.rooms[0];

    expect(room.connect).toHaveBeenCalledWith(
      'wss://rtc.example.test',
      'signed-room-token',
      { autoSubscribe: true },
    );
    expect(room.localParticipant.setMicrophoneEnabled).not.toHaveBeenCalled();
    await session.setMuted(false);
    expect(room.localParticipant.setMicrophoneEnabled).toHaveBeenCalledWith(true);

    room.emit('data-received', new TextEncoder().encode('{"type":"caption","text":"Hello"}'));
    expect(onMessage).toHaveBeenCalledWith({ type: 'caption', text: 'Hello' });
    room.emit('reconnecting', undefined);
    room.emit('reconnected', undefined);
    expect(onReconnecting).toHaveBeenCalledOnce();
    expect(onReconnected).toHaveBeenCalledOnce();

    const element = document.createElement('audio');
    vi.spyOn(element, 'play').mockResolvedValue(undefined);
    vi.spyOn(element, 'pause').mockImplementation(() => {});
    const track = { kind: 'audio', attach: vi.fn(() => element), detach: vi.fn(() => [element]) };
    room.emit('track-subscribed', track);
    expect(document.body.contains(element)).toBe(true);
    await session.setMuted(true);
    expect(room.localParticipant.setMicrophoneEnabled).toHaveBeenLastCalledWith(false);

    await session.close();
    expect(room.disconnect).toHaveBeenCalledOnce();
    expect(document.body.contains(element)).toBe(false);
  });

  it('rejects insecure media URLs before connecting', async () => {
    const session = new LiveKitCallSession({ onMessage: vi.fn(), onDisconnected: vi.fn() });
    await expect(session.connect({
      transport: 'livekit',
      url: 'http://rtc.example.test',
      room: 'ura-call-1',
      identity: 'caller-1',
      token: 'signed-room-token',
    })).rejects.toThrow('media server URL is invalid');
    expect(sdk.rooms).toHaveLength(0);
  });
});

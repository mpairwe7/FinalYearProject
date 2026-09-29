import { afterEach, describe, expect, it, vi } from 'vitest';

const sdk = vi.hoisted(() => {
  const roomEvent = {
    TrackSubscribed: 'track-subscribed',
    TrackUnsubscribed: 'track-unsubscribed',
    DataReceived: 'data-received',
    Disconnected: 'disconnected',
    Reconnecting: 'reconnecting',
    Reconnected: 'reconnected',
    AudioPlaybackStatusChanged: 'audio-playback',
    LocalTrackPublished: 'local-track-published',
    LocalTrackUnpublished: 'local-track-unpublished',
  };
  const analysers: Array<{ volume: number; cleanup: ReturnType<typeof vi.fn> }> = [];
  const rooms: Array<InstanceType<typeof MockRoom>> = [];
  class MockRoom {
    handlers = new Map<string, (...args: unknown[]) => void>();
    connect = vi.fn(async () => {});
    disconnect = vi.fn(async () => {});
    localParticipant = { setMicrophoneEnabled: vi.fn(async () => {}) };
    canPlaybackAudio = true;
    startAudio = vi.fn(async () => { this.canPlaybackAudio = true; });
    constructor() { rooms.push(this); }
    // `never[]` accepts every typed SDK callback; stored as `unknown[]` so the
    // test can emit the SDK's positional event arguments without `any`.
    on(event: string, callback: (...args: never[]) => void) {
      this.handlers.set(event, callback as (...args: unknown[]) => void);
      return this;
    }
    emit(event: string, ...args: unknown[]) { this.handlers.get(event)?.(...args); }
  }
  const createAudioAnalyser = vi.fn(() => {
    const entry = { volume: 0, cleanup: vi.fn(async () => {}) };
    analysers.push(entry);
    return { calculateVolume: () => entry.volume, cleanup: entry.cleanup, analyser: {} };
  });
  return { roomEvent, rooms, analysers, MockRoom, createAudioAnalyser };
});

vi.mock('livekit-client', () => ({
  Room: sdk.MockRoom,
  RoomEvent: sdk.roomEvent,
  Track: { Kind: { Audio: 'audio' } },
  createAudioAnalyser: sdk.createAudioAnalyser,
}));

import { isAllowedMediaUrl, LiveKitCallSession } from '@/services/liveKitCallSession';

const CREDENTIALS = {
  transport: 'livekit' as const,
  url: 'wss://rtc.example.test',
  room: 'ura-call-1',
  identity: 'caller-1',
  token: 'signed-room-token',
};
const AGENT = { identity: 'ura-agent-call_1' };

describe('LiveKitCallSession', () => {
  afterEach(() => {
    sdk.rooms.length = 0;
    sdk.analysers.length = 0;
    sdk.createAudioAnalyser.mockClear();
    document.body.replaceChildren();
    vi.useRealTimers();
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

    room.emit('data-received', new TextEncoder().encode('{"type":"caption","text":"Hello"}'), AGENT);
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

  it('allows ws:// only on a plain-http page', () => {
    expect(isAllowedMediaUrl('wss://rtc.example.test', 'https:')).toBe(true);
    expect(isAllowedMediaUrl('wss://rtc.example.test', 'http:')).toBe(true);
    expect(isAllowedMediaUrl('ws://localhost:7880', 'http:')).toBe(true);
    expect(isAllowedMediaUrl('ws://rtc.example.test', 'https:')).toBe(false);
    expect(isAllowedMediaUrl('https://rtc.example.test', 'https:')).toBe(false);
  });

  it('takes call events from the agent only', async () => {
    const onMessage = vi.fn();
    const session = new LiveKitCallSession({ onMessage, onDisconnected: vi.fn() });
    await session.connect(CREDENTIALS, false);
    const room = sdk.rooms[0];
    const status = new TextEncoder().encode('{"type":"status","status":"ended"}');
    room.emit('data-received', status, { identity: 'officer-9f2' });
    room.emit('data-received', status, undefined);
    expect(onMessage).not.toHaveBeenCalled();
    room.emit('data-received', status, AGENT);
    expect(onMessage).toHaveBeenCalledWith({ type: 'status', status: 'ended' });
    await session.close();
  });

  it('reports blocked audio and resumes it on the next user gesture', async () => {
    const onAudioBlockedChange = vi.fn();
    const session = new LiveKitCallSession({ onMessage: vi.fn(), onDisconnected: vi.fn(), onAudioBlockedChange });
    await session.connect(CREDENTIALS, false);
    const room = sdk.rooms[0];
    expect(onAudioBlockedChange).not.toHaveBeenCalled();

    room.canPlaybackAudio = false;
    room.emit('audio-playback');
    expect(onAudioBlockedChange).toHaveBeenLastCalledWith(true);

    document.dispatchEvent(new Event('pointerdown'));
    await vi.waitFor(() => expect(onAudioBlockedChange).toHaveBeenLastCalledWith(false));
    expect(room.startAudio).toHaveBeenCalledOnce();
    document.dispatchEvent(new Event('pointerdown'));
    expect(room.startAudio).toHaveBeenCalledOnce(); // the one-shot listener is gone
    await session.close();
  });

  it('reads microphone and call levels, silent while muted, released on close', async () => {
    vi.useFakeTimers();
    const onLevels = vi.fn();
    const session = new LiveKitCallSession({ onMessage: vi.fn(), onDisconnected: vi.fn(), onLevels });
    await session.connect(CREDENTIALS, false);
    const room = sdk.rooms[0];
    room.emit('local-track-published', { trackSid: 'mic', track: { kind: 'audio' } });
    const element = document.createElement('audio');
    vi.spyOn(element, 'play').mockResolvedValue(undefined);
    room.emit('track-subscribed', { kind: 'audio', sid: 'agent-audio', attach: () => element, detach: () => [element] });
    const [mic, agent] = sdk.analysers;
    mic.volume = 0.2;
    agent.volume = 0.25;

    vi.advanceTimersByTime(50);
    expect(onLevels).toHaveBeenLastCalledWith({ input: 0.4, output: 0.5 });
    await session.setMuted(true);
    vi.advanceTimersByTime(50);
    expect(onLevels).toHaveBeenLastCalledWith({ input: 0, output: 0.5 });

    await session.close();
    const calls = onLevels.mock.calls.length;
    vi.advanceTimersByTime(200);
    expect(onLevels).toHaveBeenCalledTimes(calls);
    expect(mic.cleanup).toHaveBeenCalledOnce();
    expect(agent.cleanup).toHaveBeenCalledOnce();
  });

  it('does not build analysers when nobody reads the levels', async () => {
    const session = new LiveKitCallSession({ onMessage: vi.fn(), onDisconnected: vi.fn() });
    await session.connect(CREDENTIALS, false);
    sdk.rooms[0].emit('local-track-published', { trackSid: 'mic', track: { kind: 'audio' } });
    expect(sdk.createAudioAnalyser).not.toHaveBeenCalled();
    await session.close();
  });
});

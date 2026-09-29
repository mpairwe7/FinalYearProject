import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { callsApi, ClaimConflictError } from '@/services/callsApi';
import { useCallConsoleStore } from '@/store/useCallConsoleStore';
import { FakeSocket } from '../helpers/callConsole';

type LiveKitCallbacks = {
  onMessage: (message: Record<string, unknown>) => void;
  onDisconnected: () => void;
  onReconnecting?: () => void;
  onReconnected?: () => void;
};

const livekit = vi.hoisted(() => ({ callbacks: null as LiveKitCallbacks | null }));

vi.mock('@/services/liveKitCallSession', () => ({
  LiveKitCallSession: class {
    constructor(callbacks: LiveKitCallbacks) { livekit.callbacks = callbacks; }
    async connect() {}
    async close() {}
    async setMuted() {}
  },
}));

const mic = vi.hoisted(() => ({
  onChunk: null as ((chunk: ArrayBuffer) => void) | null,
  stop: vi.fn(),
  fail: false,
}));

vi.mock('@/services/pcmPlayer', () => ({
  PCMPlayer: class {
    push = vi.fn();
    onLevel() {}
    async init() {}
    close() {}
  },
}));

vi.mock('@/services/voiceService', () => ({
  AudioRecorder: class {
    async startStreaming(onChunk: (chunk: ArrayBuffer) => void) {
      if (mic.fail) throw new Error('NotAllowedError');
      mic.onChunk = onChunk;
      return mic.stop;
    }
  },
}));

import {
  endCall,
  finishWrapUp,
  resetSessionForTests,
  takeCall,
  toggleHold,
  toggleMute,
  transferCall,
  wrapUpCall,
} from '@/services/officerCallSession';

const active = () => useCallConsoleStore.getState().activeCall;

describe('officerCallSession', () => {
  beforeEach(() => {
    FakeSocket.reset();
    vi.stubGlobal('WebSocket', FakeSocket);
    mic.fail = false;
    mic.onChunk = null;
    livekit.callbacks = null;
    vi.spyOn(callsApi, 'claimCall').mockResolvedValue({
      claimed: true, call_id: 'c1', officer_name: 'Officer Okello', claim_expires_at: 0,
    });
    vi.spyOn(callsApi, 'releaseCall').mockResolvedValue({ released: true });
    vi.spyOn(callsApi, 'endCall').mockResolvedValue({ ended: true });
  });

  afterEach(() => {
    resetSessionForTests();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('claims and bridges the compatibility media socket after authentication', async () => {
    await takeCall('c1');
    expect(callsApi.claimCall).toHaveBeenCalledWith('c1');
    expect(active()).toMatchObject({ callId: 'c1', state: 'connecting', officerName: 'Officer Okello' });
    const audio = FakeSocket.find('/admin/calls/c1/audio');
    expect(audio?.binaryType).toBe('arraybuffer');
    expect(audio?.url).not.toContain('token=');
    audio!.open();
    expect(JSON.parse(String(audio!.sent[0]))).toMatchObject({ type: 'authenticate' });
    audio!.emit({ type: 'authenticated' });
    expect(active()?.state).toBe('bridged');
  });

  it('keeps a transfer in wrap-up when server revocation closes media during the request', async () => {
    const transferControl: {
      resolve?: (value: { transferred: boolean; call_id: string; target_team: string }) => void;
    } = {};
    vi.spyOn(callsApi, 'transferCall').mockImplementation(() => new Promise((resolve) => {
      transferControl.resolve = resolve;
    }));

    await takeCall('c1');
    const control = FakeSocket.find('/audio')!;
    control.open();
    control.emit({
      type: 'livekit_ready',
      url: 'wss://rtc.example.test',
      room: 'ura-c1',
      identity: 'officer-1',
      token: 'room-token',
    });
    await vi.waitFor(() => expect(active()?.state).toBe('bridged'));

    const pending = transferCall({ team: 'disputes' });
    await vi.waitFor(() => expect(callsApi.transferCall).toHaveBeenCalled());
    livekit.callbacks?.onDisconnected();
    control.shut(1000);
    expect(active()?.state).toBe('bridged');

    transferControl.resolve?.({ transferred: true, call_id: 'c1', target_team: 'disputes' });
    await pending;
    expect(active()?.state).toBe('wrap_up');
  });

  it('shows media reconnection and restores the bridged state when LiveKit recovers', async () => {
    await takeCall('c1');
    const audio = FakeSocket.find('/audio')!;
    audio.open();
    audio.emit({
      type: 'livekit_ready',
      url: 'wss://rtc.example.test',
      room: 'ura-c1',
      identity: 'officer-1',
      token: 'room-token',
    });
    await vi.waitFor(() => expect(active()?.state).toBe('bridged'));

    livekit.callbacks?.onReconnecting?.();
    expect(active()?.state).toBe('reconnecting');
    livekit.callbacks?.onReconnected?.();
    expect(active()?.state).toBe('bridged');
  });

  it('sends the microphone unless muted', async () => {
    await takeCall('c1');
    const audio = FakeSocket.find('/audio')!;
    audio.open();
    audio.emit({ type: 'authenticated' });
    await vi.waitFor(() => expect(mic.onChunk).toBeTypeOf('function'));
    const chunk = new Int16Array([100, -100, 200, -200]).buffer;
    mic.onChunk!(chunk);
    expect(audio.sent[1]).toBe(chunk);
    toggleMute();
    expect(active()?.muted).toBe(true);
    mic.onChunk!(chunk);
    expect(audio.sent).toHaveLength(2);
  });

  it('ends the call and transitions to wrap_up when the server hangs up', async () => {
    await takeCall('c1');
    const audio = FakeSocket.find('/audio')!;
    audio.open();
    audio.emit({ type: 'authenticated' });
    await endCall();
    expect(callsApi.endCall).toHaveBeenCalledWith('c1');
    expect(active()?.state).toBe('ending');
    audio.shut(1000);
    expect(active()?.state).toBe('wrap_up');
    expect(mic.stop).toHaveBeenCalled();
    finishWrapUp();
    expect(active()).toBeNull();
  });

  it('puts the call on hold and resumes', async () => {
    vi.spyOn(callsApi, 'holdCall').mockResolvedValue({ hold: true, call_id: 'c1' });
    await takeCall('c1');
    const audio = FakeSocket.find('/audio')!;
    audio.open();
    audio.emit({ type: 'authenticated' });
    await toggleHold(true);
    expect(callsApi.holdCall).toHaveBeenCalledWith('c1', true);
    expect(active()?.onHold).toBe(true);

    vi.spyOn(callsApi, 'holdCall').mockResolvedValue({ hold: false, call_id: 'c1' });
    await toggleHold(false);
    expect(callsApi.holdCall).toHaveBeenCalledWith('c1', false);
    expect(active()?.onHold).toBe(false);
  });

  it('transfers the call and transitions to wrap_up', async () => {
    vi.spyOn(callsApi, 'transferCall').mockResolvedValue({ transferred: true, call_id: 'c1', target_team: 'disputes' });
    await takeCall('c1');
    const audio = FakeSocket.find('/audio')!;
    audio.open();
    audio.emit({ type: 'authenticated' });
    await transferCall({ team: 'disputes', note: 'Escalating' });
    expect(callsApi.transferCall).toHaveBeenCalledWith('c1', { team: 'disputes', note: 'Escalating' });
    expect(active()?.state).toBe('wrap_up');
    expect(mic.stop).toHaveBeenCalled();
  });

  it('submits wrap-up and returns to idle', async () => {
    vi.spyOn(callsApi, 'wrapUpCall').mockResolvedValue({ wrapped_up: true, call_id: 'c1', outcome: 'resolved' });
    await takeCall('c1');
    const audio = FakeSocket.find('/audio')!;
    audio.open();
    audio.emit({ type: 'authenticated' });
    await endCall();
    audio.shut(1000);
    expect(active()?.state).toBe('wrap_up');

    await wrapUpCall({ outcome: 'resolved', note: 'All set' });
    expect(callsApi.wrapUpCall).toHaveBeenCalledWith('c1', { outcome: 'resolved', note: 'All set' });
    expect(active()).toBeNull();
  });

  it('gives the call back when the microphone is refused', async () => {
    mic.fail = true;
    await takeCall('c1');
    const audio = FakeSocket.find('/audio')!;
    audio.open();
    audio.emit({ type: 'authenticated' });
    await vi.waitFor(() => expect(callsApi.releaseCall).toHaveBeenCalledWith('c1'));
    expect(callsApi.releaseCall).toHaveBeenCalledWith('c1');
    expect(active()).toMatchObject({ state: 'idle' });
    expect(active()?.error).toMatch(/Microphone access was denied/);
  });

  it('reports who won when another officer claimed first', async () => {
    vi.mocked(callsApi.claimCall).mockRejectedValue(new ClaimConflictError('taken', 'nakato', 'Officer Nakato'));
    await expect(takeCall('c1')).rejects.toBeInstanceOf(ClaimConflictError);
    expect(active()).toBeNull();
  });

  it('says so when the audio bridge refuses this officer', async () => {
    await takeCall('c1');
    FakeSocket.find('/audio')!.shut(4409);
    expect(active()?.error).toBe('Another officer has this call.');
  });

  it('refuses a second call while on one', async () => {
    await takeCall('c1');
    await expect(takeCall('c2')).rejects.toThrow(/already on a call/);
  });

  it('asks before the tab is closed mid-call', async () => {
    const add = vi.spyOn(window, 'addEventListener');
    await takeCall('c1');
    expect(add).toHaveBeenCalledWith('beforeunload', expect.any(Function));
  });
});

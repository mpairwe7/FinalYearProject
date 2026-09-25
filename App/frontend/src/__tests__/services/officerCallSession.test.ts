import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { callsApi, ClaimConflictError } from '@/services/callsApi';
import { useCallConsoleStore } from '@/store/useCallConsoleStore';
import { FakeSocket } from '../helpers/callConsole';

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

  it('claims, asks for the microphone, then bridges', async () => {
    await takeCall('c1');
    expect(callsApi.claimCall).toHaveBeenCalledWith('c1');
    expect(active()).toMatchObject({ callId: 'c1', state: 'connecting', officerName: 'Officer Okello' });
    const audio = FakeSocket.find('/admin/calls/c1/audio');
    expect(audio?.binaryType).toBe('arraybuffer');
    audio!.open();
    expect(active()?.state).toBe('bridged');
  });

  it('sends the microphone unless muted', async () => {
    await takeCall('c1');
    const audio = FakeSocket.find('/audio')!;
    audio.open();
    const chunk = new Int16Array([100, -100, 200, -200]).buffer;
    mic.onChunk!(chunk);
    expect(audio.sent).toEqual([chunk]);
    toggleMute();
    expect(active()?.muted).toBe(true);
    mic.onChunk!(chunk);
    expect(audio.sent).toHaveLength(1);
  });

  it('ends the call and transitions to wrap_up when the server hangs up', async () => {
    await takeCall('c1');
    const audio = FakeSocket.find('/audio')!;
    audio.open();
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

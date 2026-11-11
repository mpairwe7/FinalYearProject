/**
 * The caller's call, as the hook runs it: a real CallSocket over a fake
 * WebSocket, with the microphone, the player and LiveKit replaced.
 *
 * Phase 4 of the November 2026 call-receptionist review (ADR-0042).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { FakeSocket } from '../helpers/callConsole';
import { en } from '@/lib/i18n/en';

const mic = vi.hoisted(() => ({
  prompts: [] as Array<{
    resolve: () => void;
    reject: (error: Error) => void;
    stop: ReturnType<typeof import('vitest').vi.fn>;
    onChunk: (chunk: ArrayBuffer) => void;
  }>,
}));

vi.mock('@/services/voiceService', () => ({
  AudioRecorder: class {
    startStreaming(onChunk: (chunk: ArrayBuffer) => void) {
      return new Promise<() => void>((resolve, reject) => {
        const stop = vi.fn();
        mic.prompts.push({ resolve: () => resolve(stop), reject, stop, onChunk });
      });
    }
  },
}));

vi.mock('@/services/pcmPlayer', () => ({
  PCMPlayer: class {
    push() {}
    flush() {}
    onLevel() {}
    onBlockedChange() {}
    async init() {}
    async resume() {}
    close() {}
  },
}));

vi.mock('@/services/callTones', () => ({
  playDialTone: () => () => {},
  playHoldTone: () => () => {},
  playJoinChime: () => {},
}));

vi.mock('@/services/accountApi', () => ({ accountApi: { grantConsents: () => Promise.resolve() } }));

const livekit = vi.hoisted(() => ({
  setMuted: [] as boolean[],
  failUnmute: false,
  failMute: false,
  connectError: null as Error | null,
  /** Leave the next setMuted pending; the test settles it through `held`. */
  holdNext: false,
  held: [] as Array<{ resolve: () => void; reject: (error: Error) => void }>,
}));

vi.mock('@/services/liveKitCallSession', () => ({
  SPEAKING_LEVEL: 0.1,
  LiveKitCallSession: class {
    constructor(_callbacks: unknown) {}
    async connect() {
      if (livekit.connectError) throw livekit.connectError;
    }
    async setMuted(muted: boolean) {
      livekit.setMuted.push(muted);
      if (livekit.holdNext) {
        livekit.holdNext = false;
        await new Promise<void>((resolve, reject) => livekit.held.push({ resolve, reject }));
        return;
      }
      if (!muted && livekit.failUnmute) throw new Error('Permission denied');
      if (muted && livekit.failMute) throw new Error('Track not found');
    }
    async close() {}
    async resumeAudio() {}
  },
}));

import { useCall } from '@/hooks/useCall';
import { useCallStore } from '@/store/useCallStore';

const state = () => useCallStore.getState();
const callSocket = () => FakeSocket.find('/calls/stream')!;

async function settle() {
  for (let i = 0; i < 5; i += 1) await Promise.resolve();
}

describe('useCall: the caller screen', () => {
  beforeEach(() => {
    FakeSocket.reset();
    vi.stubGlobal('WebSocket', FakeSocket);
    mic.prompts = [];
    livekit.setMuted = [];
    livekit.failUnmute = false;
    livekit.connectError = null;
    livekit.holdNext = false;
    livekit.held = [];
    livekit.failMute = false;
    useCallStore.getState().reset();
    useCallStore.getState().openCall();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function startCall() {
    const hook = renderHook(() => useCall());
    act(() => {
      void hook.result.current.startCall('en');
    });
    const socket = callSocket();
    act(() => socket.open());
    return { hook, socket };
  }

  function connect(socket: FakeSocket, media?: Record<string, unknown>) {
    act(() => socket.emit({ type: 'call_ready', language: 'en', ...(media ? { media } : {}) }));
  }

  describe('the microphone after the call has ended (G601)', () => {
    it('is released when the permission prompt is answered after hang-up', async () => {
      const { hook, socket } = startCall();
      connect(socket);
      expect(mic.prompts).toHaveLength(1);

      act(() => hook.result.current.hangup());
      await act(async () => {
        mic.prompts[0].resolve();
        await settle();
      });

      expect(mic.prompts[0].stop).toHaveBeenCalled();
      expect(state().error).toBeNull();
    });

    it('a refusal answered after hang-up puts no error on the ended screen', async () => {
      const { hook, socket } = startCall();
      connect(socket);

      act(() => hook.result.current.hangup());
      await act(async () => {
        mic.prompts[0].reject(new Error('Permission denied'));
        await settle();
      });

      expect(state().error).toBeNull();
      expect(state().status).toBe('ended');
    });

    it('a late answer from the last call never reaches the next one', async () => {
      const { hook, socket: first } = startCall();
      connect(first);
      act(() => hook.result.current.hangup());

      act(() => {
        void hook.result.current.startCall('en');
      });
      const second = callSocket();
      expect(second).not.toBe(first);
      act(() => second.open());
      connect(second);

      await act(async () => {
        mic.prompts[0].resolve();
        await settle();
      });
      mic.prompts[0].onChunk(new Int16Array(320).buffer);

      expect(mic.prompts[0].stop).toHaveBeenCalled();
      expect(second.sent.filter((frame) => frame instanceof ArrayBuffer)).toHaveLength(0);
    });
  });

  describe('a refused microphone on a LiveKit call (G603)', () => {
    const media = { transport: 'livekit', url: 'wss://rtc.example.test', room: 'r', identity: 'i', token: 't' };

    it('keeps the call, mutes, and says the caller can listen and type', async () => {
      livekit.failUnmute = true;
      const { socket } = startCall();
      await act(async () => {
        connect(socket, media);
        await settle();
      });

      expect(state().status).toBe('ai');
      expect(state().isMuted).toBe(true);
      expect(state().error).toBe(en['call.micFallback']);
    });

    it('lets the caller try the microphone again', async () => {
      livekit.failUnmute = true;
      const { hook, socket } = startCall();
      await act(async () => {
        connect(socket, media);
        await settle();
      });

      livekit.failUnmute = false;
      await act(async () => {
        hook.result.current.toggleMute();
        await settle();
      });

      expect(state().isMuted).toBe(false);
      expect(state().error).toBeNull();
    });

    it('a media connection that fails still ends the call, in the caller\'s language', async () => {
      livekit.connectError = new Error('could not reach rtc.example.test');
      const { socket } = startCall();
      await act(async () => {
        connect(socket, media);
        await settle();
      });

      expect(state().status).toBe('ended');
      expect(state().error).toBe(en['call.error.noConnection']);
    });
  });

  describe('a mute that did not happen (G609)', () => {
    it('is not shown as live when un-muting fails', async () => {
      const { hook, socket } = startCall();
      await act(async () => {
        connect(socket, { transport: 'livekit', url: 'wss://rtc.example.test', room: 'r', identity: 'i', token: 't' });
        await settle();
      });
      await act(async () => {
        hook.result.current.toggleMute();
        await settle();
      });
      expect(state().isMuted).toBe(true);

      livekit.failUnmute = true;
      await act(async () => {
        hook.result.current.toggleMute();
        await settle();
      });

      expect(state().isMuted).toBe(true);
      expect(state().error).toBe(en['call.micFallback']);
    });

    it('a slow request the caller has since overtaken does not decide what the screen shows', async () => {
      const { hook, socket } = startCall();
      await act(async () => {
        connect(socket, { transport: 'livekit', url: 'wss://rtc.example.test', room: 'r', identity: 'i', token: 't' });
        await settle();
      });
      await act(async () => {
        hook.result.current.toggleMute(); // mute
        await settle();
      });
      livekit.holdNext = true;
      await act(async () => {
        hook.result.current.toggleMute(); // un-mute, still pending
        hook.result.current.toggleMute(); // mute
        hook.result.current.toggleMute(); // un-mute again, and it works
        await settle();
      });
      expect(state().isMuted).toBe(false);

      await act(async () => {
        livekit.held[0].reject(new Error('Permission denied'));
        await settle();
      });

      expect(state().isMuted).toBe(false);
      expect(state().error).toBeNull();
    });

    it('a mute that failed and then worked leaves no message, and the call ends as a normal one', async () => {
      const { hook, socket } = startCall();
      await act(async () => {
        connect(socket, { transport: 'livekit', url: 'wss://rtc.example.test', room: 'r', identity: 'i', token: 't' });
        await settle();
      });
      livekit.failMute = true;
      await act(async () => {
        hook.result.current.toggleMute();
        await settle();
      });
      expect(state().error).toBe(en['call.error.generic']);

      livekit.failMute = false;
      await act(async () => {
        hook.result.current.toggleMute();
        await settle();
      });
      expect([state().isMuted, state().error]).toEqual([true, null]);
    });

    it('a mute failure still on screen is not why a normal call ended', async () => {
      const { hook, socket } = startCall();
      await act(async () => {
        connect(socket, { transport: 'livekit', url: 'wss://rtc.example.test', room: 'r', identity: 'i', token: 't' });
        await settle();
      });
      livekit.failMute = true;
      await act(async () => {
        hook.result.current.toggleMute();
        await settle();
      });

      act(() => socket.emit({ type: 'status', status: 'ended' }));

      expect(state().error).toBeNull();
    });

    it('a new call starts unmuted, with no officer and no ticket from the last one', () => {
      useCallStore.setState({ isMuted: true, officerName: 'Okello', ticketRef: 'tkt-1' });
      useCallStore.getState().openCall();
      expect([state().isMuted, state().officerName, state().ticketRef]).toEqual([false, null, null]);

      useCallStore.setState({ isMuted: true, officerName: 'Okello', ticketRef: 'tkt-1', status: 'ended' });
      startCall();
      expect([state().isMuted, state().officerName, state().ticketRef]).toEqual([false, null, null]);
    });
  });

  describe('why a call ended (G605, G607)', () => {
    it.each([
      [1013, 'call.error.busy'],
      [1001, 'call.error.unavailable'],
      [4401, 'call.error.signin'],
    ] as const)('a refusal closing with %i says so', (code, key) => {
      const { socket } = startCall();
      act(() => socket.shut(code));

      expect(state().status).toBe('ended');
      expect(state().error).toBe(en[key]);
    });

    it('a call cut off after it connected says it was cut off', () => {
      const { socket } = startCall();
      connect(socket);
      act(() => {
        socket.onerror?.();
        socket.shut(1006);
      });

      expect(state().error).toBe(en['call.error.dropped']);
    });

    it('a call the server ended normally is not an error', () => {
      const { socket } = startCall();
      connect(socket);
      act(() => socket.emit({ type: 'status', status: 'ended' }));
      act(() => socket.shut(1000));

      expect(state().status).toBe('ended');
      expect(state().error).toBeNull();
    });

    it('a caller who listened and typed without a microphone is thanked at the end', async () => {
      const { socket } = startCall();
      connect(socket);
      await act(async () => {
        mic.prompts[0].reject(new Error('Permission denied'));
        await settle();
      });
      expect(state().error).toBe(en['call.micFallback']);

      act(() => socket.emit({ type: 'status', status: 'ended' }));

      expect(state().status).toBe('ended');
      expect(state().error).toBeNull();
    });

    it('a server error is shown by its code, never as the server\'s English', () => {
      const { socket } = startCall();
      act(() => socket.emit({ type: 'error', code: 'signin', detail: 'Authentication required' }));
      expect(state().error).toBe(en['call.error.signin']);

      act(() => socket.emit({ type: 'error', detail: 'Something internal happened' }));
      expect(state().error).toBe(en['call.error.generic']);
    });
  });

  describe('the time limit (G602)', () => {
    it('is announced in the call thread', () => {
      const { socket } = startCall();
      connect(socket);
      act(() => socket.emit({ type: 'duration_warning', seconds_remaining: 58 }));

      const notes = state().captions.filter((caption) => caption.speaker === 'system').map((c) => c.text);
      expect(notes).toContain(en['call.durationWarning']);
    });
  });
});

/**
 * The officer's console when something goes wrong: the device check, the
 * live transcript and lobby connections, presence, and actions that fail.
 *
 * Phase 4 of the November 2026 call-receptionist review (ADR-0042).
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { FakeSocket, callRecord } from '../helpers/callConsole';
import { callsApi, type CallRecord } from '@/services/callsApi';
import { useCallConsoleStore } from '@/store/useCallConsoleStore';

// ---------------------------------------------------------------------------
// G604: the device check
// ---------------------------------------------------------------------------

describe('DeviceCheckDialog', () => {
  const prompts: Array<{ resolve: () => void; tracks: Array<{ stop: ReturnType<typeof vi.fn> }> }> = [];

  beforeEach(() => {
    prompts.length = 0;
    useCallConsoleStore.getState().reset();
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      writable: true,
      value: {
        getUserMedia: vi.fn(
          () =>
            new Promise((resolve) => {
              const tracks = [{ stop: vi.fn() }];
              prompts.push({ resolve: () => resolve({ getTracks: () => tracks }), tracks });
            }),
        ),
      },
    });
    vi.stubGlobal(
      'AudioContext',
      class {
        createAnalyser() {
          return { fftSize: 0, getByteTimeDomainData() {} };
        }
        createMediaStreamSource() {
          return { connect() {} };
        }
        close() {
          return Promise.resolve();
        }
      },
    );
    vi.stubGlobal('requestAnimationFrame', () => 1);
    vi.stubGlobal('cancelAnimationFrame', () => {});
  });

  afterEach(() => vi.unstubAllGlobals());

  async function dialog(open = true) {
    const { DeviceCheckDialog } = await import('@/components/staff/calls/console/DeviceCheckDialog');
    const onClose = vi.fn();
    const view = render(<DeviceCheckDialog open={open} onClose={onClose} />);
    return { ...view, onClose, DeviceCheckDialog };
  }

  it('stops the first microphone when the check is run again', async () => {
    await dialog();
    fireEvent.click(screen.getByRole('button', { name: 'Test microphone' }));
    await act(async () => prompts[0].resolve());
    fireEvent.click(screen.getByRole('button', { name: 'Microphone on' }));
    await act(async () => prompts[1].resolve());

    expect(prompts[0].tracks[0].stop).toHaveBeenCalled();
    expect(prompts[1].tracks[0].stop).not.toHaveBeenCalled();
  });

  it('stops a microphone granted after the dialog was closed, and does not call it ready', async () => {
    await dialog();
    fireEvent.click(screen.getByRole('button', { name: 'Test microphone' }));
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    await act(async () => prompts[0].resolve());

    expect(prompts[0].tracks[0].stop).toHaveBeenCalled();
    expect(useCallConsoleStore.getState().micReady).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// G606: the live transcript and the lobby
// ---------------------------------------------------------------------------

describe('useCallLive', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeSocket.reset();
    vi.stubGlobal('WebSocket', FakeSocket);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  async function live() {
    const { useCallLive } = await import('@/hooks/useCalls');
    return renderHook(() => useCallLive('call_waiting_1'));
  }

  it('says when the transcript stopped, and comes back on its own', async () => {
    const { result } = await live();
    const first = FakeSocket.find('call_id=call_waiting_1')!;
    act(() => {
      first.open();
      first.emit({ type: 'snapshot', call: callRecord({ status: 'bridged' }), turns: [] });
    });
    expect(result.current.status).toBe('live');

    act(() => first.shut(1006));
    expect(result.current.status).toBe('reconnecting');

    act(() => vi.advanceTimersByTime(1500));
    const second = FakeSocket.find('call_id=call_waiting_1')!;
    expect(second).not.toBe(first);
    act(() => {
      second.open();
      second.emit({ type: 'snapshot', call: callRecord({ status: 'bridged' }), turns: [] });
    });
    expect(result.current.status).toBe('live');
  });

  it('does not reconnect to a call that has ended, or after a final refusal', async () => {
    const { result } = await live();
    const first = FakeSocket.find('call_id=call_waiting_1')!;
    act(() => {
      first.open();
      first.emit({ type: 'snapshot', call: callRecord({ status: 'ended' }), turns: [] });
      first.shut(1006);
    });
    act(() => vi.advanceTimersByTime(60000));
    expect(FakeSocket.all.filter((s) => s.url.includes('call_id=call_waiting_1'))).toHaveLength(1);

    const { unmount } = await live();
    const refused = FakeSocket.find('call_id=call_waiting_1')!;
    act(() => refused.shut(4401));
    expect(result.current.status).not.toBe('live');
    act(() => vi.advanceTimersByTime(60000));
    expect(FakeSocket.find('call_id=call_waiting_1')).toBe(refused);
    unmount();
  });

  it('passes on the time-limit warning (G602)', async () => {
    const { result } = await live();
    const socket = FakeSocket.find('call_id=call_waiting_1')!;
    act(() => {
      socket.open();
      socket.emit({ type: 'duration_warning', call_id: 'call_waiting_1', data: { type: 'duration_warning', seconds_remaining: 57 } });
    });

    expect(result.current.secondsRemaining).toBe(57);
  });
});

describe('the lobby connection', () => {
  beforeEach(async () => {
    vi.useFakeTimers();
    FakeSocket.reset();
    vi.stubGlobal('WebSocket', FakeSocket);
    vi.spyOn(callsApi, 'listCalls').mockResolvedValue({ calls: [] } as never);
    useCallConsoleStore.getState().reset();
    const lobby = await import('@/services/callLobbySocket');
    lobby.resetLobbyForTests();
  });

  afterEach(async () => {
    const lobby = await import('@/services/callLobbySocket');
    lobby.resetLobbyForTests();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('is open only once the server says it is ready, and backs off until then', async () => {
    const { acquireLobby } = await import('@/services/callLobbySocket');
    const release = acquireLobby();
    const first = FakeSocket.find('/admin/calls/stream')!;
    act(() => first.open());
    expect(useCallConsoleStore.getState().lobbyStatus).toBe('connecting');

    act(() => first.shut(1013)); // refused after opening: the retry backs off
    act(() => vi.advanceTimersByTime(1000));
    const second = FakeSocket.find('/admin/calls/stream')!;
    expect(second).not.toBe(first);
    act(() => {
      second.open();
      second.shut(1013);
    });
    act(() => vi.advanceTimersByTime(1500));
    expect(FakeSocket.find('/admin/calls/stream')).toBe(second); // not reset to one second by the open

    act(() => vi.advanceTimersByTime(1000));
    const third = FakeSocket.find('/admin/calls/stream')!;
    act(() => {
      third.open();
      third.emit({ type: 'ready' });
    });
    expect(useCallConsoleStore.getState().lobbyStatus).toBe('open');
    release();
  });

  it('is shown on every staff page when it is down', async () => {
    const { CallConsoleBar } = await import('@/components/staff/calls/console/CallConsoleBar');
    useCallConsoleStore.getState().setLobbyStatus('retrying');
    render(<CallConsoleBar canTake onOpenCall={vi.fn()} onCheckMic={vi.fn()} />);

    expect(screen.getByRole('status').textContent).toMatch(/reconnecting/i);
  });
});

// ---------------------------------------------------------------------------
// G610: presence
// ---------------------------------------------------------------------------

describe('usePresenceHeartbeat', () => {
  beforeEach(() => {
    useCallConsoleStore.getState().reset();
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' });
  });

  afterEach(() => vi.restoreAllMocks());

  it.each(['claiming', 'connecting', 'bridged', 'reconnecting', 'ending'] as const)(
    'reports an officer whose call is %s as on a call',
    async (state) => {
      const { usePresenceHeartbeat } = await import('@/components/staff/calls/console/usePresenceHeartbeat');
      const sent = vi.spyOn(callsApi, 'updatePresence').mockResolvedValue({} as never);
      useCallConsoleStore.getState().setActiveCall({
        callId: 'c1', state, since: Date.now(), muted: false, officerName: '', error: null,
      });

      renderHook(() => usePresenceHeartbeat('okello'));

      expect(sent).toHaveBeenCalledWith({ status: 'on_call' });
    },
  );
});

// ---------------------------------------------------------------------------
// G611: actions that fail say so
// ---------------------------------------------------------------------------

describe('a failed action', () => {
  afterEach(() => vi.restoreAllMocks());

  function owed(): CallRecord {
    return callRecord({ call_id: 'cb1', status: 'ended', needs_callback: 1 as never, callback_reason: 'no_officer_available' });
  }

  it('marking a callback done', async () => {
    const { CallbacksList } = await import('@/components/staff/calls/CallbacksList');
    vi.spyOn(callsApi, 'markCallbackDone').mockRejectedValue(new Error('Callback already completed by another officer'));
    render(<CallbacksList calls={[owed()]} onRefresh={vi.fn()} onOpenCall={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: 'Mark done' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));

    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/already completed/));
  });

  it('saving the call review', async () => {
    const { CallReviewForm } = await import('@/components/staff/calls/CallReviewForm');
    const onSubmit = vi.fn().mockRejectedValue(new Error('Service unavailable'));
    render(<CallReviewForm callId="c1" initialRating={null} onSubmit={onSubmit} />);

    fireEvent.click(screen.getByRole('button', { name: 'Submit Review' }));

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Submit Review' })).toBeInTheDocument();
  });
});

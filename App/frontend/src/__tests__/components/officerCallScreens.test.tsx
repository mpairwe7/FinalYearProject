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

  it('opens fresh after a finished check: no meter is claimed without a microphone', async () => {
    const { rerender, DeviceCheckDialog, onClose } = await dialog();
    fireEvent.click(screen.getByRole('button', { name: 'Test microphone' }));
    await act(async () => prompts[0].resolve());
    expect(screen.getByRole('button', { name: 'Microphone on' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    rerender(<DeviceCheckDialog open={false} onClose={onClose} />);
    rerender(<DeviceCheckDialog open onClose={onClose} />);

    expect(screen.getByRole('button', { name: 'Test microphone' })).toBeInTheDocument();
  });

  it('gives focus back to what opened it when it closes', async () => {
    const opener = document.createElement('button');
    opener.textContent = 'Check mic';
    document.body.appendChild(opener);
    opener.focus();
    const { rerender, DeviceCheckDialog, onClose } = await dialog(false);

    rerender(<DeviceCheckDialog open onClose={onClose} />);
    expect(screen.getByRole('dialog').contains(document.activeElement)).toBe(true);
    rerender(<DeviceCheckDialog open={false} onClose={onClose} />);

    expect(document.activeElement).toBe(opener);
    opener.remove();
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

    const second = await live();
    const refused = FakeSocket.find('call_id=call_waiting_1')!;
    act(() => refused.shut(4401));
    expect(second.result.current.status).toBe('stopped');
    act(() => vi.advanceTimersByTime(60000));
    expect(FakeSocket.find('call_id=call_waiting_1')).toBe(refused);
    expect(result.current.status).toBe('stopped');
    second.unmount();
  });

  it('drops a half-heard caption when a reconnect brings a fresh snapshot', async () => {
    const { result } = await live();
    const first = FakeSocket.find('call_id=call_waiting_1')!;
    act(() => {
      first.open();
      first.emit({ type: 'snapshot', call: callRecord({ status: 'bridged' }), turns: [] });
      first.emit({ type: 'caption', data: { speaker: 'caller', text: 'I was charged a pen' } });
      first.shut(1006);
    });
    expect(result.current.interimCaption).not.toBeNull();

    act(() => vi.advanceTimersByTime(1500));
    const second = FakeSocket.find('call_id=call_waiting_1')!;
    act(() => {
      second.open();
      second.emit({ type: 'snapshot', call: callRecord({ status: 'bridged' }), turns: [] });
    });

    expect(result.current.interimCaption).toBeNull();
  });

  it('passes on the time-limit warning (G602)', async () => {
    const { result } = await live();
    const socket = FakeSocket.find('call_id=call_waiting_1')!;
    act(() => {
      socket.open();
      socket.emit({ type: 'duration_warning', call_id: 'call_waiting_1', data: { type: 'duration_warning', seconds_remaining: 57 } });
    });

    expect(result.current.secondsRemaining).toBe(57);
    // A deadline, so the notice can count down to it rather than keep "57".
    expect(Math.round(((result.current.limitAt ?? 0) - Date.now()) / 1000)).toBe(57);
  });

  it('picks up a time limit announced before the console opened the call', async () => {
    const { result } = await live();
    const socket = FakeSocket.find('call_id=call_waiting_1')!;
    act(() => {
      socket.open();
      socket.emit({ type: 'snapshot', call: { call_id: 'call_waiting_1', status: 'ai' }, turns: [], seconds_remaining: 33 });
    });

    expect(Math.round(((result.current.limitAt ?? 0) - Date.now()) / 1000)).toBe(33);
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

  it('stays marked as paused while it reconnects, until the server says it is ready', async () => {
    const { acquireLobby } = await import('@/services/callLobbySocket');
    const release = acquireLobby();
    const first = FakeSocket.find('/admin/calls/stream')!;
    act(() => {
      first.open();
      first.emit({ type: 'ready' });
      first.shut(1006);
    });
    act(() => vi.advanceTimersByTime(1000));
    expect(FakeSocket.find('/admin/calls/stream')).not.toBe(first);

    expect(useCallConsoleStore.getState().lobbyStatus).toBe('retrying');
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

  // ADR-0045: a call that ended under the officer on an error, or before a
  // reload, still owes its wrap-up. The server names it; the console shows it.
  it('brings up the wrap-up the server says is owed when the console has no call', async () => {
    const { usePresenceHeartbeat } = await import('@/components/staff/calls/console/usePresenceHeartbeat');
    const { resetSessionForTests } = await import('@/services/officerCallSession');
    const { ActiveCallDock } = await import('@/components/staff/calls/console/ActiveCallDock');
    resetSessionForTests();
    const sent = vi
      .spyOn(callsApi, 'updatePresence')
      .mockResolvedValue({ status: 'wrap_up', wrap_up_call_id: 'call_owed' } as never);

    const hook = renderHook(() => usePresenceHeartbeat('okello'));

    await waitFor(() =>
      expect(useCallConsoleStore.getState().activeCall).toMatchObject({ callId: 'call_owed', state: 'wrap_up' }),
    );
    await waitFor(() => expect(sent).toHaveBeenLastCalledWith({ status: 'wrap_up' }));
    render(<ActiveCallDock onBackToCall={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Finish wrap-up' })).toBeInTheDocument();
    hook.unmount();
    resetSessionForTests();
  });

  it('keeps the wrap-up already open when the server names another owed call', async () => {
    const { usePresenceHeartbeat } = await import('@/components/staff/calls/console/usePresenceHeartbeat');
    const { resetSessionForTests, resumeOwedWrapUp } = await import('@/services/officerCallSession');
    resetSessionForTests();
    resumeOwedWrapUp('call_first');
    const sent = vi
      .spyOn(callsApi, 'updatePresence')
      .mockResolvedValue({ status: 'wrap_up', wrap_up_call_id: 'call_older' } as never);

    const hook = renderHook(() => usePresenceHeartbeat('okello'));

    await waitFor(() => expect(sent).toHaveBeenCalledWith({ status: 'wrap_up' }));
    await act(async () => {});
    expect(useCallConsoleStore.getState().activeCall).toMatchObject({ callId: 'call_first', state: 'wrap_up' });
    hook.unmount();
    resetSessionForTests();
  });
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

// ---------------------------------------------------------------------------
// G606: a transcript that stopped for good says what to do
// ---------------------------------------------------------------------------

describe('LiveStreamNotices', () => {
  it('counts down to the time limit rather than keeping the estimate it was warned with', async () => {
    vi.useFakeTimers();
    try {
      const { LiveStreamNotices } = await import('@/components/staff/calls/LiveStreamNotices');
      const limitAt = Date.now() + 61_000;
      render(<LiveStreamNotices status="live" limitAt={limitAt} callStatus="bridged" />);
      const notice = () => screen.getByRole('status').textContent;

      act(() => vi.advanceTimersByTime(1_000));
      expect(notice()).toBe('This call reaches its time limit in about a minute and will end then.');
      act(() => vi.advanceTimersByTime(30_000));
      expect(notice()).toBe('This call reaches its time limit in about 30 seconds and will end then.');
      // In 15-second steps: a screen reader reads the region out on every change.
      act(() => vi.advanceTimersByTime(3_000));
      expect(notice()).toBe('This call reaches its time limit in about 30 seconds and will end then.');
      act(() => vi.advanceTimersByTime(12_000));
      expect(notice()).toBe('This call reaches its time limit in about 15 seconds and will end then.');
      act(() => vi.advanceTimersByTime(15_000));
      expect(notice()).toBe('This call has reached its time limit and is ending.');
    } finally {
      vi.useRealTimers();
    }
  });

  it('says a transcript stopped for good needs the officer to sign in again', async () => {
    const { LiveStreamNotices } = await import('@/components/staff/calls/LiveStreamNotices');
    render(<LiveStreamNotices status="stopped" limitAt={null} callStatus="bridged" />);

    expect(screen.getByRole('status').textContent).toMatch(/stopped.*sign in again/i);
  });

  it('says nothing about a call that has ended', async () => {
    const { LiveStreamNotices } = await import('@/components/staff/calls/LiveStreamNotices');
    const { container } = render(<LiveStreamNotices status="stopped" limitAt={Date.now() + 57_000} callStatus="ended" />);

    expect(container.textContent).toBe('');
  });
});


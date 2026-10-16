import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useCallLive } from '@/hooks/useCalls';
import { FakeSocket, callRecord } from '../helpers/callConsole';

describe('useCallLive', () => {
  afterEach(() => {
    FakeSocket.reset();
    vi.unstubAllGlobals();
  });

  it('takes a raised priority from the call channel, so the officer sees why (G175)', () => {
    vi.stubGlobal('WebSocket', FakeSocket);
    const { result } = renderHook(() => useCallLive('call_waiting_1'));
    const socket = FakeSocket.find('call_id=call_waiting_1')!;
    act(() => {
      socket.open();
      socket.emit({ type: 'snapshot', call: callRecord({ priority: 'normal', transfer_reason: 'caller_requested' }), turns: [] });
    });
    expect(result.current.call?.priority).toBe('normal');

    act(() => socket.emit({ type: 'priority', data: { priority: 'urgent', reason: 'safety_concern' } }));
    expect(result.current.call).toMatchObject({ priority: 'urgent', transfer_reason: 'safety_concern' });
  });
});

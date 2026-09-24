import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

/*
 * The call's language chip, driven the way a call drives it: startCall() opens
 * the (mocked) socket, and the server's `call_ready` / `language` messages
 * decide what the chip says. Picking a language sends `set_language`.
 */

const sockets: Array<{
  callbacks: { onMessage: (msg: Record<string, unknown>) => void };
  connect: ReturnType<typeof vi.fn>;
  setLanguage: ReturnType<typeof vi.fn>;
}> = [];

vi.mock('@/services/callSocket', () => ({
  CallSocket: class {
    callbacks: { onMessage: (msg: Record<string, unknown>) => void };
    connect = vi.fn();
    setLanguage = vi.fn();
    sendAudio = vi.fn();
    requestOfficer = vi.fn();
    hangup = vi.fn();
    close = vi.fn();
    constructor(callbacks: { onMessage: (msg: Record<string, unknown>) => void }) {
      this.callbacks = callbacks;
      sockets.push(this);
    }
  },
}));

vi.mock('@/services/pcmPlayer', () => ({
  PCMPlayer: class {
    onLevel = vi.fn();
    init = vi.fn().mockResolvedValue(undefined);
    push = vi.fn();
    flush = vi.fn();
    close = vi.fn();
  },
}));

vi.mock('@/services/voiceService', () => ({
  AudioRecorder: class {
    startStreaming = vi.fn().mockResolvedValue(() => {});
  },
}));

vi.mock('@/services/callTones', () => ({
  playDialTone: vi.fn(() => () => {}),
  playJoinChime: vi.fn(),
}));

import { CallScreen } from '@/components/call/CallScreen';
import { useCallStore } from '@/store/useCallStore';

async function startCall() {
  useCallStore.getState().openCall();
  render(<CallScreen />);
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Call URA' }));
  });
  return sockets[sockets.length - 1];
}

function serverSays(socket: (typeof sockets)[number], msg: Record<string, unknown>) {
  act(() => socket.callbacks.onMessage(msg));
}

describe('call language chip', () => {
  beforeEach(() => {
    sockets.length = 0;
    useCallStore.getState().reset();
    Object.defineProperty(globalThis, 'AudioContext', {
      value: vi.fn().mockImplementation(() => ({ state: 'running', close: vi.fn().mockResolvedValue(undefined) })),
      writable: true,
    });
  });

  it('sends the chat language as a hint when the call starts', async () => {
    const socket = await startCall();
    expect(socket.connect).toHaveBeenCalledWith(
      expect.objectContaining({ locale: 'en', preferred_locale: 'en' }),
    );
  });

  it('stays hidden on a call without language detection', async () => {
    const socket = await startCall();
    serverSays(socket, { type: 'call_ready', call_id: 'c1', language_detection: false, languages: ['en'] });
    expect(screen.queryByRole('button', { name: /Call language/ })).toBeNull();
  });

  it('shows the detected language and follows a switch', async () => {
    const socket = await startCall();
    serverSays(socket, {
      type: 'call_ready',
      call_id: 'c1',
      language_detection: true,
      languages: ['en', 'lg', 'sw'],
      language: 'en',
    });
    expect(screen.getByRole('button', { name: 'Call language: English · auto' })).toBeDefined();

    serverSays(socket, { type: 'language', language: 'lg', source: 'auto', confidence: 0.93 });
    expect(screen.getByRole('button', { name: 'Call language: Luganda · auto' })).toBeDefined();
    // The switch is noted in the transcript, once.
    expect(screen.getAllByText('Now speaking Luganda').length).toBe(1);

    // A lock on the language already in use adds no note.
    serverSays(socket, { type: 'language', language: 'lg', source: 'auto' });
    expect(screen.getAllByText('Now speaking Luganda').length).toBe(1);
  });

  it('ignores a language the call does not offer', async () => {
    const socket = await startCall();
    serverSays(socket, { type: 'call_ready', call_id: 'c1', language_detection: true, languages: ['en', 'lg', 'sw'] });
    serverSays(socket, { type: 'language', language: 'fr', source: 'auto' });
    expect(useCallStore.getState().language).toBe('en');
  });

  it('pins the language chosen from the menu', async () => {
    const socket = await startCall();
    serverSays(socket, { type: 'call_ready', call_id: 'c1', language_detection: true, languages: ['en', 'lg', 'sw'] });

    fireEvent.click(screen.getByRole('button', { name: 'Call language: English · auto' }));
    const options = screen.getAllByRole('menuitemradio');
    expect(options.map((o) => o.textContent)).toEqual(['English', 'Luganda', 'Kiswahili']);
    expect(options[0].getAttribute('aria-checked')).toBe('true');

    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Kiswahili' }));
    expect(socket.setLanguage).toHaveBeenCalledWith('sw');
    expect(screen.queryByRole('menu')).toBeNull();
    // Pinned: no longer "auto".
    expect(screen.getByRole('button', { name: 'Call language: Kiswahili' })).toBeDefined();
  });

  it('closes the menu on Escape', async () => {
    const socket = await startCall();
    serverSays(socket, { type: 'call_ready', call_id: 'c1', language_detection: true, languages: ['en', 'lg', 'sw'] });
    fireEvent.click(screen.getByRole('button', { name: 'Call language: English · auto' }));
    expect(screen.getByRole('menu')).toBeDefined();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('menu')).toBeNull();
  });
});

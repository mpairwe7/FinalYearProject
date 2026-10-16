import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';

import { useVoiceEscape } from '@/hooks/useVoiceEscape';

function press(key = 'Escape'): KeyboardEvent {
  const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
  document.dispatchEvent(event);
  return event;
}

function setup(state: { recording?: boolean; dictating?: boolean; reading?: boolean }) {
  const handlers = { onCancelRecording: vi.fn(), onStopDictation: vi.fn(), onStopReading: vi.fn() };
  renderHook(() =>
    useVoiceEscape({ recording: false, dictating: false, reading: false, ...state, ...handlers }),
  );
  return handlers;
}

describe('useVoiceEscape (G204)', () => {
  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('cancels a recording first', () => {
    const h = setup({ recording: true, reading: true });
    press();
    expect(h.onCancelRecording).toHaveBeenCalledTimes(1);
    expect(h.onStopReading).not.toHaveBeenCalled();
  });

  it('stops dictation, keeping what was heard', () => {
    const h = setup({ dictating: true });
    press();
    expect(h.onStopDictation).toHaveBeenCalledTimes(1);
  });

  it('stops a reply being read aloud', () => {
    const h = setup({ reading: true });
    press();
    expect(h.onStopReading).toHaveBeenCalledTimes(1);
  });

  it('leaves Escape to an open dialog, to other keys, and to whoever handled it first', () => {
    const h = setup({ reading: true });
    press('Enter');
    const handled = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    handled.preventDefault();
    document.dispatchEvent(handled);
    const dialog = document.createElement('div');
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    document.body.appendChild(dialog);
    press();
    expect(h.onStopReading).not.toHaveBeenCalled();
  });

  it('does nothing when the voice is idle', () => {
    const h = setup({});
    press();
    expect(h.onCancelRecording).not.toHaveBeenCalled();
    expect(h.onStopDictation).not.toHaveBeenCalled();
    expect(h.onStopReading).not.toHaveBeenCalled();
  });
});

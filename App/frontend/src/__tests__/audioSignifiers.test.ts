import { describe, expect, it } from 'vitest';
import { audioSignifiers } from '../lib/audioSignifiers';

describe('audioSignifiers', () => {
  it('instantiates and toggles enabled state', () => {
    expect(audioSignifiers.isEnabled()).toBe(true);
    audioSignifiers.setEnabled(false);
    expect(audioSignifiers.isEnabled()).toBe(false);
    audioSignifiers.setEnabled(true);
  });

  it('safely handles play calls in headless environment', () => {
    expect(() => {
      audioSignifiers.playMicStart();
      audioSignifiers.playMicStop();
      audioSignifiers.playError();
    }).not.toThrow();
  });
});

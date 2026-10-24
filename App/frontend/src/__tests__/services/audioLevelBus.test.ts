/**
 * The level bus: live meters kept outside React, for the call orb and voice
 * mode's dock. Levels fade when their producer stops; the end-of-turn
 * countdown holds still between readings, and lapses once they stop.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  getAudioLevels,
  getPauseProgress,
  resetAudioLevels,
  setInputLevel,
  setPauseProgress,
} from '@/services/audioLevelBus';

describe('audioLevelBus', () => {
  afterEach(() => {
    resetAudioLevels();
    vi.useRealTimers();
  });

  it('fades a level whose producer has stopped', () => {
    vi.useFakeTimers({ toFake: ['performance'] });
    setInputLevel(0.8);
    expect(getAudioLevels().input).toBeCloseTo(0.8, 5);
    vi.advanceTimersByTime(75);
    expect(getAudioLevels().input).toBeCloseTo(0.4, 5);
    vi.advanceTimersByTime(100);
    expect(getAudioLevels().input).toBe(0);
  });

  it('holds the countdown between readings, so the ring does not jump back and forth', () => {
    vi.useFakeTimers({ toFake: ['performance'] });
    setPauseProgress(0.5);
    vi.advanceTimersByTime(150);
    expect(getPauseProgress()).toBe(0.5);
    vi.advanceTimersByTime(100);
    expect(getPauseProgress()).toBe(0); // no reading for 250 ms: the turn is over
  });

  it('keeps every value within 0..1, and starts the next session from nothing', () => {
    setPauseProgress(7);
    expect(getPauseProgress()).toBe(1);
    setInputLevel(-3);
    expect(getAudioLevels().input).toBe(0);
    setPauseProgress(0.4);
    resetAudioLevels();
    expect(getPauseProgress()).toBe(0);
  });
});

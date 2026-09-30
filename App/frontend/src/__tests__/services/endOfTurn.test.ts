import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  EndOfTurnDetector,
  levelDbfs,
  watchEndOfTurn,
  type EndOfTurnOptions,
  type TurnEvent,
} from '@/services/endOfTurn';

const FRAME_MS = 50;
const ROOM = -65;
const VOICE = -28;

/** Speech as a level meter sees it: 300 ms words with a 100 ms dip between them, ending on a word. */
function speech(ms: number): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  for (let t = 0; t < ms; ) {
    const word = Math.min(300, ms - t);
    out.push([word, VOICE]);
    t += word;
    if (ms - t > 100) {
      out.push([100, -60]);
      t += 100;
    }
  }
  return out;
}

/** Feed `segments` of [ms, dBFS] frame by frame; returns each event with the time it fired. */
function run(segments: Array<[number, number]>, opts: EndOfTurnOptions = { silenceMs: 1200 }) {
  const detector = new EndOfTurnDetector(opts);
  const events: Array<{ event: TurnEvent; at: number }> = [];
  let now = 0;
  for (const [ms, db] of segments) {
    for (let t = 0; t < ms; t += FRAME_MS, now += FRAME_MS) {
      const event = detector.push(db, now);
      if (event) events.push({ event, at: now });
    }
  }
  return { events, detector };
}

describe('EndOfTurnDetector', () => {
  it('ends the turn after silenceMs of quiet that follows speech', () => {
    const { events } = run([[1000, ROOM], ...speech(1600), [3000, ROOM]]);
    expect(events.map((e) => e.event)).toEqual(['speech', 'end']);
    // Speech needs 300 ms of sound; the pause is measured from the last of it.
    expect(events[0].at).toBeGreaterThanOrEqual(1300);
    expect(events[0].at).toBeLessThan(1500);
    expect(events[1].at).toBeGreaterThanOrEqual(1000 + 1600 + 1200 - FRAME_MS);
    expect(events[1].at).toBeLessThanOrEqual(1000 + 1600 + 1200 + FRAME_MS);
  });

  it('keeps the turn open through a pause shorter than silenceMs', () => {
    const { events } = run([[1000, ROOM], ...speech(1200), [900, ROOM], ...speech(1200), [3000, ROOM]]);
    expect(events.map((e) => e.event)).toEqual(['speech', 'end']);
    expect(events[1].at).toBeGreaterThanOrEqual(1000 + 1200 + 900 + 1200 + 1200 - FRAME_MS);
  });

  it('does not end a long, even turn while the speaker is still talking', () => {
    // 8 s of steady voice with no dips fills the floor's 4 s window. Were the
    // floor still learning, it would rise to the voice and end the turn early.
    const { events } = run([[1000, ROOM], [8000, -45], [3000, ROOM]]);
    expect(events.map((e) => e.event)).toEqual(['speech', 'end']);
    expect(events[1].at).toBeGreaterThanOrEqual(1000 + 8000 + 1200 - FRAME_MS);
  });

  it('does not start a turn on a click or a cough', () => {
    const { events } = run([[1000, ROOM], [150, -20], [2000, ROOM]]);
    expect(events).toEqual([]);
  });

  it('follows a steady noisy room instead of taking it for speech', () => {
    const noisy = -40;
    const { events } = run([[6000, noisy], [1500, -18], [3000, noisy]]);
    expect(events.map((e) => e.event)).toEqual(['speech', 'end']);
    expect(events[0].at).toBeGreaterThanOrEqual(6000);
  });

  it('closes a mic that heard nothing within noSpeechMs', () => {
    const { events } = run([[9000, ROOM]], { silenceMs: 1200, noSpeechMs: 8000 });
    expect(events).toEqual([{ event: 'no-speech', at: 8000 }]);
  });

  it('closes a mic that heard only clicks', () => {
    // Chromium's fake capture device: silence, and a 50 ms beep every 500 ms.
    const beeps: Array<[number, number]> = [[2000, -100]];
    for (let t = 2000; t < 9000; t += 500) beeps.push([50, -8], [450, -100]);
    const { events } = run(beeps, { silenceMs: 1200, noSpeechMs: 8000 });
    expect(events).toEqual([{ event: 'no-speech', at: 8000 }]);
  });

  it('keeps listening when something was heard, even if it never passed for speech', () => {
    // A quiet speaker in a room at -48 dBFS: above the speech minimum, never 12 dB over it.
    const { events } = run([[9000, -48]], { silenceMs: 1200, noSpeechMs: 8000 });
    expect(events).toEqual([]);
  });

  it('ends a turn that reaches maxTurnMs', () => {
    const { events } = run([[500, ROOM], ...speech(8000)], { silenceMs: 1200, maxTurnMs: 5000 });
    expect(events.map((e) => e.event)).toEqual(['speech', 'too-long']);
    expect(events[1].at).toBe(5000);
  });

  it('hears a speaker who starts talking at once', () => {
    const { events } = run([...speech(2400), [2000, ROOM]]);
    expect(events.map((e) => e.event)).toEqual(['speech', 'end']);
  });

  it('decides nothing more after the turn has ended', () => {
    const { detector } = run([[1000, ROOM], ...speech(1000), [2000, ROOM]]);
    expect(detector.push(VOICE, 100_000)).toBeNull();
    expect(detector.hasSpeech).toBe(true);
  });
});

describe('levelDbfs', () => {
  it('reads a full-scale sine near -3 dBFS and silence as the floor value', () => {
    const sine = Float32Array.from({ length: 480 }, (_, i) => Math.sin((2 * Math.PI * i) / 48));
    expect(levelDbfs(sine)).toBeCloseTo(-3.01, 1);
    expect(levelDbfs(new Float32Array(480))).toBe(-100);
  });
});

describe('watchEndOfTurn', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  /** A Web Audio graph whose analyser reads whatever level `script` gives for the current time. */
  function fakeGraph(script: (ms: number) => number) {
    const disconnect = vi.fn();
    const node = () => ({ connect: vi.fn(), disconnect, type: '', frequency: { value: 0 } });
    const ctx = {
      createBiquadFilter: node,
      createAnalyser: () => ({
        ...node(),
        fftSize: 0,
        getFloatTimeDomainData: (out: Float32Array) => out.fill(10 ** (script(performance.now()) / 20)),
      }),
    };
    const source = { connect: vi.fn(), disconnect };
    return { ctx: ctx as unknown as BaseAudioContext, source: source as unknown as AudioNode, disconnect };
  }

  it('reports speech, then the end of the turn, and stops', () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'performance'] });
    const start = performance.now();
    const { ctx, source, disconnect } = fakeGraph((ms) => (ms - start > 1000 && ms - start < 2500 ? VOICE : ROOM));
    const onEvent = vi.fn();
    watchEndOfTurn(ctx, source, { silenceMs: 1200, onEvent });
    vi.advanceTimersByTime(6000);
    expect(onEvent.mock.calls.map((c) => c[0])).toEqual(['speech', 'end']);
    expect(disconnect).toHaveBeenCalled();
  });

  it('stops when asked, before any event', () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'performance'] });
    const { ctx, source } = fakeGraph(() => ROOM);
    const onEvent = vi.fn();
    const stop = watchEndOfTurn(ctx, source, { silenceMs: 1200, onEvent });
    stop();
    vi.advanceTimersByTime(20_000);
    expect(onEvent).not.toHaveBeenCalled();
  });
});

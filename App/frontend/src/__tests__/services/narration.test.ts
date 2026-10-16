/**
 * Pausing, resuming and following a reply read aloud (G200).
 *
 * A reply could only be stopped, from its own button; with narration on it
 * started by itself and nothing on screen paused it (WCAG 2.2 SC 1.4.2).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  closePlaybackContext,
  narrationSnapshot,
  pausePlayback,
  resumePlayback,
  speakStreamed,
  stopPlayback,
  subscribeNarration,
} from '@/services/voiceService';

class FakeSource {
  static all: FakeSource[] = [];
  buffer: { duration: number } | null = null;
  onended: (() => void) | null = null;
  constructor() {
    FakeSource.all.push(this);
  }
  connect() {}
  start() {}
  stop() {
    this.onended?.();
  }
}

class FakeAudioContext {
  static last: FakeAudioContext | null = null;
  currentTime = 10;
  state: AudioContextState = 'running';
  destination = {};
  suspend = vi.fn(async () => {
    this.state = 'suspended';
  });
  resume = vi.fn(async () => {
    this.state = 'running';
  });
  constructor() {
    FakeAudioContext.last = this;
  }
  async close() {
    this.state = 'closed';
  }
  async decodeAudioData(buf: ArrayBuffer) {
    return { duration: Number(new TextDecoder().decode(buf)) };
  }
  createBufferSource() {
    return new FakeSource();
  }
}

const line = (obj: object) => `${JSON.stringify(obj)}\n`;
const piece = (seq: number, text: string) => ({ seq, text, format: 'ogg_opus', audio_base64: btoa('1.5') });

function respondWith(body: string) {
  return vi.fn(async () => new Response(body, { status: 200, headers: { 'Content-Type': 'application/x-ndjson' } }));
}

/**
 * Start reading two sentences; resolves once both are scheduled. The reading's
 * own promise is wrapped: returned bare from an async function it would be
 * adopted, and this would wait for the reading to end.
 */
async function startReading(): Promise<{ spoken: Promise<unknown> }> {
  vi.stubGlobal(
    'fetch',
    respondWith(line(piece(0, 'VAT is 18 percent.')) + line(piece(1, 'File by the 15th.')) + line({ done: true })),
  );
  const spoken = speakStreamed('VAT is 18 percent. File by the 15th.', { language: 'en' });
  await vi.waitFor(() => expect(FakeSource.all).toHaveLength(2));
  return { spoken };
}

describe('narration controls', () => {
  beforeEach(() => {
    FakeSource.all = [];
    FakeAudioContext.last = null;
    vi.stubGlobal('AudioContext', FakeAudioContext);
  });
  afterEach(() => {
    closePlaybackContext();
    vi.unstubAllGlobals();
  });

  it('pauses and resumes, and follows the sentence being heard', async () => {
    const { spoken } = await startReading();
    expect(narrationSnapshot()).toEqual({ active: true, paused: false, sentence: 'VAT is 18 percent.' });

    await pausePlayback();
    expect(FakeAudioContext.last?.suspend).toHaveBeenCalledTimes(1);
    expect(narrationSnapshot().paused).toBe(true);

    await resumePlayback();
    expect(FakeAudioContext.last?.resume).toHaveBeenCalled();
    expect(narrationSnapshot().paused).toBe(false);

    FakeSource.all[0].onended?.();
    expect(narrationSnapshot().sentence).toBe('File by the 15th.');
    FakeSource.all[1].onended?.();
    await spoken;
    expect(narrationSnapshot()).toEqual({ active: false, paused: false, sentence: '' });
  });

  it('a stop is not a pause: the next reply plays', async () => {
    await startReading();
    await pausePlayback();
    stopPlayback();
    expect(narrationSnapshot()).toEqual({ active: false, paused: false, sentence: '' });
    expect(FakeAudioContext.last?.state).toBe('running');
  });

  it('says it paused only once the audio really stopped, and resumed only once it runs', async () => {
    const { spoken } = await startReading();
    FakeAudioContext.last!.suspend.mockRejectedValueOnce(new Error('cannot suspend'));
    expect(await pausePlayback()).toBe(false);
    expect(narrationSnapshot().paused).toBe(false); // still playing: Pause is still offered

    expect(await pausePlayback()).toBe(true);
    FakeAudioContext.last!.resume.mockRejectedValueOnce(new Error('cannot resume'));
    expect(await resumePlayback()).toBe(false);
    expect(narrationSnapshot().paused).toBe(true); // still paused: Resume is still offered

    expect(await resumePlayback()).toBe(true);
    expect(narrationSnapshot().paused).toBe(false);
    FakeSource.all.forEach((s) => s.onended?.());
    await spoken;
  });

  it('pausing with nothing playing does nothing', async () => {
    await pausePlayback();
    expect(FakeAudioContext.last?.suspend ?? vi.fn()).not.toHaveBeenCalled();
    expect(narrationSnapshot().paused).toBe(false);
  });

  it('tells subscribers of changes only, with a snapshot that is stable in between', async () => {
    const heard = vi.fn();
    const unsubscribe = subscribeNarration(heard);
    const { spoken } = await startReading();
    const before = heard.mock.calls.length;
    const snapshot = narrationSnapshot();
    expect(narrationSnapshot()).toBe(snapshot); // the same object until something changes
    await pausePlayback();
    expect(heard.mock.calls.length).toBe(before + 1);
    unsubscribe();
    await resumePlayback();
    expect(heard.mock.calls.length).toBe(before + 1);
    FakeSource.all.forEach((s) => s.onended?.());
    await spoken;
  });
});

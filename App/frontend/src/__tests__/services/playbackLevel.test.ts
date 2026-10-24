/**
 * How loud the reply being read aloud is — what moves voice mode's orb while
 * the assistant has the floor.
 *
 * Speech now plays through an analyser on its way to the speakers. Where a
 * context cannot make one, it must still play, straight to the speakers.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  closePlaybackContext,
  pausePlayback,
  readPlaybackLevel,
  speakStreamed,
  stopPlayback,
} from '@/services/voiceService';

class FakeSource {
  static all: FakeSource[] = [];
  buffer: { duration: number } | null = null;
  onended: (() => void) | null = null;
  connectedTo: unknown = null;
  constructor() {
    FakeSource.all.push(this);
  }
  connect(node: unknown) {
    this.connectedTo = node;
  }
  start() {}
  stop() {
    this.onended?.();
  }
}

class FakeAnalyser {
  fftSize = 0;
  connectedTo: unknown = null;
  connect(node: unknown) {
    this.connectedTo = node;
  }
  /** A steady signal at 0.1 RMS. */
  getFloatTimeDomainData(out: Float32Array) {
    out.fill(0.1);
  }
}

class FakeAudioContext {
  static last: FakeAudioContext | null = null;
  static withAnalyser = true;
  analyser: FakeAnalyser | null = null;
  currentTime = 10;
  state: AudioContextState = 'running';
  destination = { name: 'speakers' };
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
  createAnalyser() {
    if (!FakeAudioContext.withAnalyser) throw new Error('not supported here');
    this.analyser = new FakeAnalyser();
    return this.analyser;
  }
}

const line = (obj: object) => `${JSON.stringify(obj)}\n`;

async function startReading() {
  const body = line({ seq: 0, text: 'VAT is 18 percent.', format: 'ogg_opus', audio_base64: btoa('1.5') }) + line({ done: true });
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(body, { status: 200, headers: { 'Content-Type': 'application/x-ndjson' } })),
  );
  const spoken = speakStreamed('VAT is 18 percent.', { language: 'en' });
  await vi.waitFor(() => expect(FakeSource.all).toHaveLength(1));
  return { spoken };
}

describe('readPlaybackLevel', () => {
  beforeEach(() => {
    FakeSource.all = [];
    FakeAudioContext.last = null;
    FakeAudioContext.withAnalyser = true;
    vi.stubGlobal('AudioContext', FakeAudioContext);
  });
  afterEach(() => {
    stopPlayback();
    closePlaybackContext();
    vi.unstubAllGlobals();
  });

  it('is silent when nothing is being read', () => {
    expect(readPlaybackLevel()).toBe(0);
  });

  it('follows the reply through an analyser on its way to the speakers', async () => {
    await startReading();
    const ctx = FakeAudioContext.last!;
    expect(FakeSource.all[0].connectedTo).toBe(ctx.analyser);
    expect(ctx.analyser?.connectedTo).toBe(ctx.destination);
    // 0.1 RMS, scaled so a normal voice spans the orb's range.
    expect(readPlaybackLevel()).toBeCloseTo(0.4, 5);
  });

  it('goes quiet when the reading is paused or stopped', async () => {
    await startReading();
    await pausePlayback();
    expect(readPlaybackLevel()).toBe(0);
    stopPlayback();
    expect(readPlaybackLevel()).toBe(0);
  });

  it('still plays where no analyser can be made, straight to the speakers', async () => {
    FakeAudioContext.withAnalyser = false;
    await startReading();
    expect(FakeSource.all[0].connectedTo).toBe(FakeAudioContext.last!.destination);
    expect(readPlaybackLevel()).toBe(0);
  });
});

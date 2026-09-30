import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { isPlaying, speakStreamed, stopPlayback } from '@/services/voiceService';

/** A Web Audio stand-in: each "decoded" piece lasts `duration` seconds and records when it was started. */
class FakeSource {
  static all: FakeSource[] = [];
  buffer: { duration: number } | null = null;
  startedAt: number | null = null;
  stopped = false;
  onended: (() => void) | null = null;
  constructor() {
    FakeSource.all.push(this);
  }
  connect() {}
  start(when: number) {
    this.startedAt = when;
  }
  stop() {
    this.stopped = true;
    this.onended?.();
  }
}

class FakeAudioContext {
  currentTime = 10;
  state = 'running';
  destination = {};
  async resume() {}
  async close() {}
  async decodeAudioData(buf: ArrayBuffer) {
    const text = new TextDecoder().decode(buf);
    if (text === 'broken') throw new Error('cannot decode');
    return { duration: Number(text) };
  }
  createBufferSource() {
    return new FakeSource();
  }
}

const line = (obj: object) => `${JSON.stringify(obj)}\n`;
const piece = (seq: number, seconds: number | string) => ({ seq, text: `piece ${seq}`, format: 'ogg_opus', audio_base64: btoa(String(seconds)) });

/** A fetch whose body yields the given NDJSON chunks, one read at a time. */
function respondWith(chunks: string[], { hang = false, onCancel = () => {} } = {}) {
  return vi.fn(async (_url: string, init: RequestInit) => {
    const encoder = new TextEncoder();
    let i = 0;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        // As a browser's fetch does: aborting rejects the read in progress.
        init.signal?.addEventListener('abort', () => controller.error(new DOMException('Aborted', 'AbortError')));
      },
      pull(controller) {
        if (i < chunks.length) controller.enqueue(encoder.encode(chunks[i++]));
        else if (!hang) controller.close();
      },
      cancel: onCancel,
    });
    return new Response(body, { status: 200, headers: { 'Content-Type': 'application/x-ndjson' } });
  });
}

/** End every started source, as the audio clock would. */
const finishAudio = () => FakeSource.all.forEach((s) => s.onended?.());

describe('speakStreamed', () => {
  beforeEach(() => {
    FakeSource.all = [];
    vi.stubGlobal('AudioContext', FakeAudioContext);
  });
  afterEach(() => {
    stopPlayback();
    vi.unstubAllGlobals();
  });

  it('schedules each piece to start as the one before ends', async () => {
    // Split across reads, as a network delivers it.
    const ndjson = line(piece(0, 1.5)) + line(piece(1, 2)) + line({ done: true, pieces: 2, failed: 0 });
    vi.stubGlobal('fetch', respondWith([ndjson.slice(0, 20), ndjson.slice(20)]));
    const firstAudio = vi.fn();
    const spoken = speakStreamed('Two pieces.', { language: 'lg', onFirstAudio: firstAudio });
    await vi.waitFor(() => expect(FakeSource.all).toHaveLength(2));
    finishAudio();
    const outcome = await spoken;
    const [a, b] = FakeSource.all;
    expect(b.startedAt).toBeCloseTo((a.startedAt as number) + 1.5);
    expect(firstAudio).toHaveBeenCalledTimes(1);
    expect(outcome).toMatchObject({ pieces: 2, failed: 0, stopped: false });
    expect(outcome.firstAudioMs).not.toBeNull();
  });

  it('asks for Opus and says which language', async () => {
    const fetchMock = respondWith([line(piece(0, 1)) + line({ done: true, pieces: 1, failed: 0 })]);
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('Audio', class { canPlayType() { return 'probably'; } });
    const spoken = speakStreamed('One.', { language: 'sw' });
    await vi.waitFor(() => expect(FakeSource.all).toHaveLength(1));
    finishAudio();
    await spoken;
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/v1\/tts\/stream$/);
    expect(JSON.parse(init.body as string)).toMatchObject({ text: 'One.', language: 'sw', format: 'opus' });
  });

  it('skips a piece that could not be voiced and plays the rest', async () => {
    const ndjson = line(piece(0, 1)) + line({ seq: 1, text: 'x', error: 'voice down' }) + line(piece(2, 1)) + line({ done: true });
    vi.stubGlobal('fetch', respondWith([ndjson]));
    const spoken = speakStreamed('Three pieces.');
    await vi.waitFor(() => expect(FakeSource.all).toHaveLength(2));
    finishAudio();
    expect(await spoken).toMatchObject({ pieces: 2, failed: 1 });
  });

  it('skips a piece it cannot decode once speech has started', async () => {
    const ndjson = line(piece(0, 1)) + line(piece(1, 'broken')) + line(piece(2, 1)) + line({ done: true });
    vi.stubGlobal('fetch', respondWith([ndjson]));
    const spoken = speakStreamed('Three pieces.');
    await vi.waitFor(() => expect(FakeSource.all).toHaveLength(2));
    finishAudio();
    expect(await spoken).toMatchObject({ pieces: 2, failed: 1 });
  });

  it('closes the request when it gives up, so the server stops voicing', async () => {
    const onCancel = vi.fn();
    vi.stubGlobal('fetch', respondWith([line(piece(0, 'broken')) + line(piece(1, 1))], { hang: true, onCancel }));
    await expect(speakStreamed('Hello.')).rejects.toThrow(/cannot decode/);
    expect(onCancel).toHaveBeenCalled();
  });

  it('stops everything queued when stopPlayback is called', async () => {
    vi.stubGlobal('fetch', respondWith([line(piece(0, 5)) + line(piece(1, 5))], { hang: true }));
    const spoken = speakStreamed('Long answer.');
    await vi.waitFor(() => expect(FakeSource.all).toHaveLength(2));
    expect(isPlaying()).toBe(true);
    stopPlayback();
    expect(await spoken).toMatchObject({ stopped: true });
    expect(FakeSource.all.every((s) => s.stopped)).toBe(true);
    expect(isPlaying()).toBe(false);
  });

  it('throws when nothing could be played, so the caller can fall back', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('down', { status: 503 })));
    await expect(speakStreamed('Hello.')).rejects.toThrow(/503/);
    vi.stubGlobal('fetch', respondWith([line(piece(0, 'broken')) + line({ done: true })]));
    await expect(speakStreamed('Hello.')).rejects.toThrow(/cannot decode/);
  });

  it('ends quietly after what is queued when the server stops sending', async () => {
    vi.stubGlobal('fetch', respondWith([line(piece(0, 1))], { hang: true }));
    const spoken = speakStreamed('Stalls.', { stallMs: 50 });
    await vi.waitFor(() => expect(FakeSource.all).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 80));
    finishAudio();
    expect(await spoken).toMatchObject({ pieces: 1, stopped: false });
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LOCALE_OPTIONS } from '@/lib/locales';
import { pcm16ToWav, transcribe, voiceChat } from '@/services/voiceService';

const ascii = (bytes: Uint8Array, start: number, end: number) => String.fromCharCode(...bytes.slice(start, end));

/** Four samples; the first is -1 (bytes FF FF), which a server sniffer read as an MP3 frame sync. */
const PCM = new Int16Array([-1, 200, -300, 400]).buffer;

describe('pcm16ToWav', () => {
  it('wraps 16-bit mono PCM in a WAV header the server cannot mistake', () => {
    const wav = new Uint8Array(pcm16ToWav(PCM, 16000));
    const view = new DataView(wav.buffer);
    expect(ascii(wav, 0, 4)).toBe('RIFF');
    expect(ascii(wav, 8, 16)).toBe('WAVEfmt ');
    expect(ascii(wav, 36, 40)).toBe('data');
    expect(view.getUint32(4, true)).toBe(36 + PCM.byteLength);
    expect(view.getUint16(20, true)).toBe(1); // PCM
    expect(view.getUint16(22, true)).toBe(1); // mono
    expect(view.getUint32(24, true)).toBe(16000);
    expect(view.getUint16(34, true)).toBe(16);
    expect(view.getUint32(40, true)).toBe(PCM.byteLength);
    expect(Array.from(wav.slice(44))).toEqual(Array.from(new Uint8Array(PCM)));
  });
});

describe('uploads', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(async () => new Response(JSON.stringify({ text: 'namba ya TIN' }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  const sent = () => {
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = new Uint8Array(init.body as ArrayBuffer);
    return { url: new URL(url, 'http://x'), headers: init.headers as Record<string, string>, body };
  };

  it('sends dictation as WAV and asks for tax terms to be repaired', async () => {
    await transcribe(PCM, 'sw', undefined, { domain: 'tax' });
    const { url, headers, body } = sent();
    expect(url.pathname).toMatch(/\/v1\/asr$/);
    expect(url.searchParams.get('language')).toBe('sw');
    expect(url.searchParams.get('domain')).toBe('tax');
    expect(headers['Content-Type']).toBe('audio/wav');
    expect(ascii(body, 0, 4)).toBe('RIFF');
  });

  it('sends a voice-chat turn as WAV', async () => {
    await voiceChat(PCM, { language: 'lg' });
    const { url, headers, body } = sent();
    expect(url.pathname).toMatch(/\/v1\/voice\/chat$/);
    expect(headers['Content-Type']).toBe('audio/wav');
    expect(ascii(body, 0, 4)).toBe('RIFF');
  });
});

describe('who transcribes dictation', () => {
  it('keeps Luganda and Swahili on the local model; English on the browser engine', () => {
    const owner = Object.fromEntries(LOCALE_OPTIONS.map((o) => [o.value, o.dictation]));
    expect(owner).toEqual({ en: 'browser', lg: 'server', sw: 'server' });
  });
});

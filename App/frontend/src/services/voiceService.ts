/**
 * Voice service — production-grade speech I/O for the URA Chatbot web client.
 *
 * Capabilities:
 *   - MediaRecorder-based audio capture (WAV PCM16 at 16 kHz)
 *   - Server-side ASR via /v1/asr (fallback when browser Speech API unavailable)
 *   - TTS playback via /v1/tts with AudioContext decoding
 *   - Translation via /v1/translate
 *   - Compound voice chat via /v1/voice/chat (audio in -> text+audio out)
 *   - Speech health check via /v1/speech/health
 *
 * All fetch calls respect a 30-second timeout and gracefully degrade on error.
 */

import { authHeaders } from '@/lib/authSession';

const API_URL = '/api';
const FETCH_TIMEOUT_MS = 30_000;
const TARGET_SAMPLE_RATE = 16000;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SpeechHealthStatus {
  status: 'ready' | 'unavailable';
  enabled: boolean;
  asr_backend: string;
  tts_backend: string;
  mt_backend: string;
}

export interface TranscribeResult {
  text: string;
  language: string | null;
  duration_s: number | null;
  latency_s: number | null;
  rtf: number | null;
  backend: string;
  error: string | null;
}

export interface SynthesizeResult {
  sample_rate: number;
  num_samples: number;
  duration_s: number;
  latency_s: number;
  backend: string;
  voice: string;
  audio_base64: string;
  error: string | null;
}

export interface TranslateResult {
  text: string;
  source_lang: string;
  target_lang: string;
  latency_s: number;
  backend: string;
  error: string | null;
}

export interface VoiceChatResult {
  transcript: string;
  transcript_language: string | null;
  conversation_id?: string | null;
  reply: string;
  reply_audio_base64: string;
  /** Server skipped inline narration (time budget) — fetch audio via /v1/tts. */
  tts_skipped?: boolean;
  sample_rate: number;
  duration_s: number;
  sources: string[];
  citations: Array<{
    ref: string;
    source: string;
    page?: string;
    section?: string;
    passage?: string;
  }>;
  faithfulness_score: number | null;
  retrieval_mode: string;
  asr_latency_s: number;
  mt_latency_s: number;
  llm_latency_s: number;
  tts_latency_s: number;
  total_latency_s: number;
  asr_backend: string;
  tts_backend: string;
  mt_backend: string;
  error: string | null;
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function withTimeout(ms: number): AbortSignal {
  const controller = new AbortController();
  setTimeout(() => controller.abort(), ms);
  return controller.signal;
}

/**
 * Downsample a Float32Array from `srcRate` to `targetRate` using linear
 * interpolation. Returns int16 PCM bytes (little-endian).
 */
function downsampleToPCM16(
  buffer: Float32Array,
  srcRate: number,
  targetRate: number
): ArrayBuffer {
  const ratio = srcRate / targetRate;
  const outLength = Math.floor(buffer.length / ratio);
  const result = new Int16Array(outLength);
  for (let i = 0; i < outLength; i++) {
    const srcIdx = i * ratio;
    const lo = Math.floor(srcIdx);
    const hi = Math.min(lo + 1, buffer.length - 1);
    const frac = srcIdx - lo;
    const sample = buffer[lo] * (1 - frac) + buffer[hi] * frac;
    result[i] = Math.max(-32768, Math.min(32767, Math.round(sample * 32768)));
  }
  return result.buffer;
}

/**
 * Wrap 16-bit mono PCM in a 44-byte WAV header for upload.
 *
 * The server sniffs a headerless body to guess its format, and a recording
 * whose first sample is -1 starts FF FF, which is an MP3 frame sync: a
 * Luganda question was decoded as MP3 noise that way and heard as "e e e e".
 * With a RIFF header there is nothing to guess.
 */
export function pcm16ToWav(pcm16: ArrayBuffer, sampleRate: number): ArrayBuffer {
  const out = new ArrayBuffer(44 + pcm16.byteLength);
  const view = new DataView(out);
  const ascii = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
  };
  ascii(0, 'RIFF');
  view.setUint32(4, 36 + pcm16.byteLength, true);
  ascii(8, 'WAVE');
  ascii(12, 'fmt ');
  view.setUint32(16, 16, true); // fmt chunk size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  ascii(36, 'data');
  view.setUint32(40, pcm16.byteLength, true);
  new Uint8Array(out, 44).set(new Uint8Array(pcm16));
  return out;
}

// ---------------------------------------------------------------------------
// Audio recorder (MediaRecorder → PCM16 at 16 kHz)
// ---------------------------------------------------------------------------

export class AudioRecorder {
  private mediaRecorder: MediaRecorder | null = null;
  private audioContext: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private chunks: Blob[] = [];
  private _recording = false;

  get isRecording(): boolean {
    return this._recording;
  }

  getStream(): MediaStream | null {
    return this.stream;
  }

  /** Check if MediaRecorder + microphone are available. */
  static isSupported(): boolean {
    return (
      typeof navigator !== 'undefined' &&
      typeof navigator.mediaDevices !== 'undefined' &&
      typeof MediaRecorder !== 'undefined'
    );
  }

  /** Start capturing audio from the microphone. */
  async start(): Promise<void> {
    if (this._recording) return;
    this.chunks = [];
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        sampleRate: { ideal: TARGET_SAMPLE_RATE },
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
      },
    });
    this.mediaRecorder = new MediaRecorder(this.stream, {
      mimeType: MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : 'audio/webm',
    });
    this.mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) this.chunks.push(e.data);
    };
    this.mediaRecorder.start(250); // collect every 250ms
    this._recording = true;
  }

  /** Stop recording and return raw PCM16 bytes at 16 kHz. */
  async stop(): Promise<ArrayBuffer> {
    return new Promise((resolve, reject) => {
      if (!this.mediaRecorder || !this._recording) {
        resolve(new ArrayBuffer(0));
        return;
      }
      this.mediaRecorder.onstop = async () => {
        this._recording = false;
        try {
          const blob = new Blob(this.chunks, { type: this.mediaRecorder!.mimeType });
          const arrayBuf = await blob.arrayBuffer();

          // Decode to AudioBuffer and downsample to 16 kHz PCM16
          this.audioContext = this.audioContext || new AudioContext({ sampleRate: 48000 });
          const audioBuf = await this.audioContext.decodeAudioData(arrayBuf);
          const channelData = audioBuf.getChannelData(0);
          const pcm16 = downsampleToPCM16(channelData, audioBuf.sampleRate, TARGET_SAMPLE_RATE);
          resolve(pcm16);
        } catch (err) {
          reject(err);
        } finally {
          this.releaseStream();
        }
      };
      this.mediaRecorder.stop();
    });
  }

  /** Cancel recording without returning audio. */
  cancel(): void {
    if (this.mediaRecorder && this._recording) {
      this.mediaRecorder.stop();
    }
    this._recording = false;
    this.releaseStream();
  }

  private releaseStream(): void {
    if (this.stream) {
      this.stream.getTracks().forEach((t) => t.stop());
      this.stream = null;
    }
  }

  /**
   * Start streaming audio chunks via AudioWorklet.
   *
   * Each chunk is a PCM16 LE ArrayBuffer (~20ms of audio).
   * The caller is responsible for sending chunks to the WebSocket.
   *
   * Returns a cleanup function to stop streaming.
   */
  async startStreaming(
    onChunk: (pcm16: ArrayBuffer) => void,
    options?: { echoCancellation?: boolean; noiseSuppression?: boolean },
  ): Promise<() => void> {
    const ctx = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
    if (ctx.state === 'suspended') {
      await ctx.resume().catch(() => {});
    }

    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        sampleRate: { ideal: TARGET_SAMPLE_RATE },
        channelCount: 1,
        echoCancellation: options?.echoCancellation ?? true,
        noiseSuppression: options?.noiseSuppression ?? true,
      },
    });

    try {
      const source = ctx.createMediaStreamSource(this.stream);
      // Prefer AudioWorklet (modern browsers)
      await ctx.audioWorklet.addModule('/audio-worklet-processor.js');
      const workletNode = new AudioWorkletNode(ctx, 'pcm16-processor');
      workletNode.port.onmessage = (e: MessageEvent) => {
        if (e.data instanceof ArrayBuffer) {
          onChunk(e.data);
        }
      };
      source.connect(workletNode);
      workletNode.connect(ctx.destination);

      this._recording = true;

      return () => {
        this._recording = false;
        workletNode.disconnect();
        source.disconnect();
        ctx.close();
        this.releaseStream();
      };
    } catch {
      // Fallback: ScriptProcessorNode (deprecated but widely supported)
      const source = ctx.createMediaStreamSource(this.stream);
      const bufSize = 4096;
      const processor = ctx.createScriptProcessor(bufSize, 1, 1);
      processor.onaudioprocess = (e: AudioProcessingEvent) => {
        const input = e.inputBuffer.getChannelData(0);
        const pcm16 = new Int16Array(input.length);
        for (let i = 0; i < input.length; i++) {
          const s = Math.max(-1, Math.min(1, input[i]));
          pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        onChunk(pcm16.buffer);
      };
      source.connect(processor);
      processor.connect(ctx.destination);

      this._recording = true;

      return () => {
        this._recording = false;
        processor.disconnect();
        source.disconnect();
        ctx.close();
        this.releaseStream();
      };
    }
  }
}

// ---------------------------------------------------------------------------
// Audio playback (WAV base64 → AudioContext)
// ---------------------------------------------------------------------------

let _playbackCtx: AudioContext | null = null;
let _currentSource: AudioBufferSourceNode | null = null;

function getPlaybackContext(): AudioContext {
  if (!_playbackCtx || _playbackCtx.state === 'closed') {
    _playbackCtx = new AudioContext();
  }
  return _playbackCtx;
}

/** Play a base64-encoded WAV audio blob. Returns a promise that resolves on end. */
export async function playAudioBase64(base64: string): Promise<void> {
  if (!base64) return;
  stopPlayback();
  const ctx = getPlaybackContext();
  if (ctx.state === 'suspended') await ctx.resume();

  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);

  // Copy to a new ArrayBuffer (decodeAudioData detaches the original)
  const audioBuf = bytes.buffer.slice(0);
  const buffer = await ctx.decodeAudioData(audioBuf);
  const source = ctx.createBufferSource();
  source.buffer = buffer;
  source.connect(ctx.destination);
  _currentSource = source;

  return new Promise<void>((resolve) => {
    source.onended = () => {
      _currentSource = null;
      resolve();
    };
    source.start(0);
  });
}

/** Close the global playback AudioContext (call on page unmount). */
export function closePlaybackContext(): void {
  stopPlayback();
  if (_playbackCtx && _playbackCtx.state !== 'closed') {
    _playbackCtx.close().catch(() => {});
    _playbackCtx = null;
  }
}

/** Stop any currently playing audio, including a streamed reply and what it has queued. */
export function stopPlayback(): void {
  if (_currentSource) {
    try {
      _currentSource.stop();
    } catch {
      // already stopped
    }
    _currentSource = null;
  }
  if (_speechStream) {
    _speechStream.abort();
    _speechStream = null;
  }
  for (const source of _streamSources) {
    try {
      source.stop();
    } catch {
      // already stopped
    }
  }
  _streamSources.clear();
}

/** Returns true if audio is currently playing (or a streamed reply is still arriving). */
export function isPlaying(): boolean {
  return _currentSource !== null || _speechStream !== null || _streamSources.size > 0;
}

// ---------------------------------------------------------------------------
// Streamed speech (/v1/tts/stream)
// ---------------------------------------------------------------------------

/** One piece of a streamed reply, in speaking order. `error` instead of audio when it could not be voiced. */
export interface SpeechPiece {
  seq: number;
  text: string;
  format?: 'ogg_opus' | 'wav' | 'mp3';
  audio_base64?: string;
  duration_s?: number;
  backend?: string;
  error?: string;
}

export interface SpokenOutcome {
  /** From asking to the first sound, in ms; null when nothing played. */
  firstAudioMs: number | null;
  pieces: number;
  failed: number;
  /** Stopped by stopPlayback() before the end. */
  stopped: boolean;
}

let _speechStream: AbortController | null = null;
const _streamSources = new Set<AudioBufferSourceNode>();

/** 'opus' where this browser plays Ogg/Opus (about a tenth of WAV's size), else 'wav'. */
export function speechFormat(): 'opus' | 'wav' {
  try {
    return typeof Audio !== 'undefined' && new Audio().canPlayType('audio/ogg; codecs="opus"') ? 'opus' : 'wav';
  } catch {
    return 'wav';
  }
}

function base64ToArrayBuffer(base64: string): ArrayBuffer {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

/** The pieces of speech for `text`, in speaking order, as the server finishes each one. */
export async function* streamSpeech(
  text: string,
  opts: { language?: string; voice?: string; signal?: AbortSignal } = {},
): AsyncGenerator<SpeechPiece> {
  const res = await fetch(`${API_URL}/v1/tts/stream`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ text, language: opts.language ?? 'en', voice: opts.voice, format: speechFormat() }),
    signal: opts.signal,
  });
  if (!res.ok || !res.body) throw new Error(`TTS stream failed: ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffered = '';
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (value) buffered += decoder.decode(value, { stream: true });
      for (let nl = buffered.indexOf('\n'); nl >= 0; nl = buffered.indexOf('\n')) {
        const line = buffered.slice(0, nl).trim();
        buffered = buffered.slice(nl + 1);
        if (!line) continue;
        const message = JSON.parse(line) as SpeechPiece & { done?: boolean };
        if (message.done) return;
        yield message;
      }
      if (done) return;
    }
  } finally {
    // A caller that stops listening early closes the request, so the server
    // stops voicing the rest.
    reader.cancel().catch(() => {});
  }
}

/**
 * Speak `text` as it is synthesised. Each piece of /v1/tts/stream is decoded
 * when it arrives and scheduled to start as the one before ends, so a long
 * answer is heard after its first sentence, not after all of it (9–20 s on
 * the GPU stack before, the whole reply as one WAV).
 *
 * Throws only when nothing could be played: the caller then asks /v1/tts for
 * the whole reply once. A failure part-way, or a server that stops sending
 * for `stallMs`, ends quietly after what is already queued: the answer is on
 * screen.
 */
export async function speakStreamed(
  text: string,
  opts: { language?: string; voice?: string; onFirstAudio?: () => void; stallMs?: number } = {},
): Promise<SpokenOutcome> {
  stopPlayback();
  const controller = new AbortController();
  _speechStream = controller;
  const ctx = getPlaybackContext();
  if (ctx.state === 'suspended') await ctx.resume();
  const started = performance.now();
  const stallMs = opts.stallMs ?? 20_000;
  let stalled = false;
  let watchdog = setTimeout(() => ((stalled = true), controller.abort()), stallMs);
  let nextStart = 0;
  let firstAudioMs: number | null = null;
  let pieces = 0;
  let failed = 0;
  let lastEnded: Promise<void> = Promise.resolve();
  try {
    for await (const piece of streamSpeech(text, { ...opts, signal: controller.signal })) {
      clearTimeout(watchdog);
      watchdog = setTimeout(() => ((stalled = true), controller.abort()), stallMs);
      if (!piece.audio_base64) {
        failed += 1;
        continue;
      }
      let buffer: AudioBuffer;
      try {
        buffer = await ctx.decodeAudioData(base64ToArrayBuffer(piece.audio_base64));
      } catch (err) {
        // Before anything has played, the format is the likely fault: give up
        // at once so the caller falls back to one WAV. After, skip the piece.
        if (pieces === 0) throw err;
        failed += 1;
        continue;
      }
      if (controller.signal.aborted) break;
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(ctx.destination);
      const startAt = Math.max(ctx.currentTime + 0.02, nextStart);
      source.start(startAt);
      nextStart = startAt + buffer.duration;
      pieces += 1;
      _streamSources.add(source);
      lastEnded = new Promise<void>((resolve) => {
        source.onended = () => {
          _streamSources.delete(source);
          resolve();
        };
      });
      if (firstAudioMs === null) {
        firstAudioMs = Math.round(performance.now() - started);
        opts.onFirstAudio?.();
      }
    }
  } catch (err) {
    if (pieces === 0 && !(controller.signal.aborted && !stalled)) {
      if (_speechStream === controller) _speechStream = null;
      throw err;
    }
  } finally {
    clearTimeout(watchdog);
  }
  const stopped = controller.signal.aborted && !stalled;
  if (_speechStream === controller) _speechStream = null;
  if (pieces === 0 && !stopped) throw new Error(failed ? 'No piece of the reply could be voiced' : 'Empty speech stream');
  if (!stopped) await lastEnded;
  return { firstAudioMs, pieces, failed, stopped };
}

// ---------------------------------------------------------------------------
// API calls
// ---------------------------------------------------------------------------

/** Check backend speech health. */
export async function checkSpeechHealth(): Promise<SpeechHealthStatus> {
  try {
    const res = await fetch(`${API_URL}/v1/speech/health`, {
      headers: authHeaders(),
      signal: withTimeout(5000),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch {
    return {
      status: 'unavailable',
      enabled: false,
      asr_backend: '',
      tts_backend: '',
      mt_backend: '',
    };
  }
}

/**
 * Send PCM16 audio to /v1/asr for server-side transcription, as WAV.
 *
 * `domain: 'tax'` asks for Whisper's TIN/URA mishears to be repaired
 * ("namba ya timu" → "namba ya TIN"), as the voice chat already does.
 */
export async function transcribe(
  pcm16: ArrayBuffer,
  language?: string,
  sampleRate = TARGET_SAMPLE_RATE,
  opts: { domain?: 'tax' } = {},
): Promise<TranscribeResult> {
  const params = new URLSearchParams({ sample_rate: String(sampleRate) });
  if (language) params.set('language', language);
  if (opts.domain) params.set('domain', opts.domain);

  const res = await fetch(`${API_URL}/v1/asr?${params}`, {
    method: 'POST',
    headers: authHeaders({
      'Content-Type': 'audio/wav',
      'X-Voice-Consent': 'true',
    }),
    body: pcm16ToWav(pcm16, sampleRate),
    signal: withTimeout(FETCH_TIMEOUT_MS),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => '');
    throw new Error(`ASR failed: ${res.status} ${detail}`);
  }
  return res.json();
}

/** Synthesize text to audio via /v1/tts. */
export async function synthesize(
  text: string,
  language = 'en',
  voice?: string
): Promise<SynthesizeResult> {
  const res = await fetch(`${API_URL}/v1/tts`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ text, language, voice: voice ?? null }),
    signal: withTimeout(FETCH_TIMEOUT_MS),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => '');
    throw new Error(`TTS failed: ${res.status} ${detail}`);
  }
  return res.json();
}

/** Translate text via /v1/translate. */
export async function translate(
  text: string,
  sourceLang: string,
  targetLang: string
): Promise<TranslateResult> {
  const res = await fetch(`${API_URL}/v1/translate`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({
      text,
      source_lang: sourceLang,
      target_lang: targetLang,
    }),
    signal: withTimeout(FETCH_TIMEOUT_MS),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => '');
    throw new Error(`MT failed: ${res.status} ${detail}`);
  }
  return res.json();
}

/**
 * Compound voice chat: audio in -> ASR -> [MT] -> LLM -> [MT] -> TTS -> audio out.
 * Sends raw PCM16 body with metadata in query params.
 */
export async function voiceChat(
  pcm16: ArrayBuffer,
  opts: {
    language?: string;
    sampleRate?: number;
    voice?: string;
    topK?: number;
    conversationId?: string;
    ttsEnabled?: boolean;
    sessionId?: string;
  } = {}
): Promise<VoiceChatResult> {
  const params = new URLSearchParams({
    language: opts.language ?? 'en',
    sample_rate: String(opts.sampleRate ?? TARGET_SAMPLE_RATE),
    tts_enabled: String(opts.ttsEnabled ?? true),
    top_k: String(opts.topK ?? 4),
  });
  if (opts.voice) params.set('voice', opts.voice);
  if (opts.conversationId) params.set('conversation_id', opts.conversationId);

  const headers: Record<string, string> = authHeaders({
    'Content-Type': 'audio/wav',
    'X-Voice-Consent': 'true',
  });
  if (opts.sessionId) headers['X-Session-ID'] = opts.sessionId;

  const res = await fetch(`${API_URL}/v1/voice/chat?${params}`, {
    method: 'POST',
    headers,
    body: pcm16ToWav(pcm16, opts.sampleRate ?? TARGET_SAMPLE_RATE),
    signal: withTimeout(60_000), // voice chat is multi-stage, allow 60s
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => '');
    throw new Error(`Voice chat failed: ${res.status} ${detail}`);
  }
  return res.json();
}

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
  ): Promise<() => void> {
    const ctx = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });

    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        sampleRate: { ideal: TARGET_SAMPLE_RATE },
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
      },
    });

    const source = ctx.createMediaStreamSource(this.stream);

    try {
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

/** Stop any currently playing audio. */
export function stopPlayback(): void {
  if (_currentSource) {
    try {
      _currentSource.stop();
    } catch {
      // already stopped
    }
    _currentSource = null;
  }
}

/** Returns true if audio is currently playing. */
export function isPlaying(): boolean {
  return _currentSource !== null;
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

/** Send raw PCM16 audio to /v1/asr for server-side transcription. */
export async function transcribe(
  pcm16: ArrayBuffer,
  language?: string,
  sampleRate = TARGET_SAMPLE_RATE
): Promise<TranscribeResult> {
  const params = new URLSearchParams({ sample_rate: String(sampleRate) });
  if (language) params.set('language', language);

  const res = await fetch(`${API_URL}/v1/asr?${params}`, {
    method: 'POST',
    headers: authHeaders({
      'Content-Type': 'application/octet-stream',
      'X-Voice-Consent': 'true',
    }),
    body: pcm16,
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
    'Content-Type': 'application/octet-stream',
    'X-Voice-Consent': 'true',
  });
  if (opts.sessionId) headers['X-Session-ID'] = opts.sessionId;

  const res = await fetch(`${API_URL}/v1/voice/chat?${params}`, {
    method: 'POST',
    headers,
    body: pcm16,
    signal: withTimeout(60_000), // voice chat is multi-stage, allow 60s
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => '');
    throw new Error(`Voice chat failed: ${res.status} ${detail}`);
  }
  return res.json();
}

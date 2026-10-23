/**
 * Streaming PCM16 LE audio player using AudioWorklet (with inline Blob + WebAudio
 * buffer-queue fallback) and a single shared initialization promise so concurrent
 * incoming WebSocket audio frames never spawn multiple AudioContexts.
 */

const INLINE_PCM_WORKLET_SOURCE = `
class PCMPlayerProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(16000 * 60);
    this.readIndex = 0;
    this.writeIndex = 0;
    this.bufferedSamples = 0;
    this.isPlaying = false;
    this.minBufferSamples = 16000 * 0.06;
    this.quantaSinceLevel = 0;
    this.levelPeak = 0;
    this.port.onmessage = (event) => {
      const data = event.data;
      if (!data) return;
      if (data.type === 'flush') {
        this.readIndex = 0;
        this.writeIndex = 0;
        this.bufferedSamples = 0;
        this.isPlaying = false;
        this.levelPeak = 0;
        this.quantaSinceLevel = 0;
        this.port.postMessage({ type: 'level', level: 0 });
        return;
      }
      const pcmBuffer = data.pcm || (data instanceof ArrayBuffer ? data : null);
      if (pcmBuffer) {
        this._enqueuePCM16(pcmBuffer);
      }
    };
  }
  _enqueuePCM16(arrayBuffer) {
    const int16 = new Int16Array(arrayBuffer);
    const len = int16.length;
    for (let i = 0; i < len; i++) {
      this.buffer[this.writeIndex] = int16[i] / 32768.0;
      this.writeIndex = (this.writeIndex + 1) % this.buffer.length;
    }
    this.bufferedSamples = Math.min(this.bufferedSamples + len, this.buffer.length);
    if (!this.isPlaying && this.bufferedSamples >= this.minBufferSamples) {
      this.isPlaying = true;
    }
  }
  process(inputs, outputs) {
    const output = outputs[0];
    if (!output || output.length === 0) return true;
    const channel = output[0];
    const framesNeeded = channel.length;
    if (!this.isPlaying || this.bufferedSamples === 0) {
      channel.fill(0);
      this._reportLevel(channel, framesNeeded);
      return true;
    }
    if (this.bufferedSamples < framesNeeded) {
      for (let i = 0; i < this.bufferedSamples; i++) {
        channel[i] = this.buffer[this.readIndex];
        this.readIndex = (this.readIndex + 1) % this.buffer.length;
      }
      for (let i = this.bufferedSamples; i < framesNeeded; i++) {
        channel[i] = 0;
      }
      this.bufferedSamples = 0;
      this.isPlaying = false;
      this.port.postMessage({ type: 'underrun' });
      this._reportLevel(channel, framesNeeded);
      return true;
    }
    for (let i = 0; i < framesNeeded; i++) {
      channel[i] = this.buffer[this.readIndex];
      this.readIndex = (this.readIndex + 1) % this.buffer.length;
    }
    this.bufferedSamples -= framesNeeded;
    this._reportLevel(channel, framesNeeded);
    return true;
  }
  _reportLevel(channel, framesNeeded) {
    for (let i = 0; i < framesNeeded; i++) {
      const m = channel[i] < 0 ? -channel[i] : channel[i];
      if (m > this.levelPeak) this.levelPeak = m;
    }
    this.quantaSinceLevel += 1;
    if (this.quantaSinceLevel < 8) return;
    this.port.postMessage({ type: 'level', level: this.levelPeak });
    this.quantaSinceLevel = 0;
    this.levelPeak = 0;
  }
}
registerProcessor('pcm-player-processor', PCMPlayerProcessor);
`;

export class PCMPlayer {
  private ctx: AudioContext | null = null;
  private workletNode: AudioWorkletNode | null = null;
  private initialized = false;
  private initPromise: Promise<void> | null = null;
  private isClosed = false;
  private sampleRate: number;
  private levelHandler: ((level: number) => void) | null = null;
  private pendingChunks: ArrayBuffer[] = [];
  private useFallbackBufferQueue = false;
  private nextStartTime = 0;
  private activeSources: Set<AudioBufferSourceNode> = new Set();

  constructor(sampleRate = 16000) {
    this.sampleRate = sampleRate;
  }

  /**
   * Subscribe to playback loudness (0..1), measured in the worklet as the
   * audio is actually rendered. Used to pulse the call orb with the voice the
   * caller is hearing. Safe to call before `init()`.
   */
  onLevel(handler: ((level: number) => void) | null): void {
    this.levelHandler = handler;
    this.bindPort();
  }

  private bindPort(): void {
    if (!this.workletNode) return;
    this.workletNode.port.onmessage = (event: MessageEvent) => {
      const data = event.data;
      if (!data || data.type !== 'level') return;
      if (this.levelHandler) this.levelHandler(Number(data.level) || 0);
    };
  }

  async resume(): Promise<void> {
    if (this.ctx && this.ctx.state === 'suspended') {
      await this.ctx.resume().catch(() => {});
    }
  }

  async init(): Promise<void> {
    if (this.initialized || this.isClosed) return;
    if (this.initPromise) return this.initPromise;

    this.initPromise = this._doInit();
    try {
      await this.initPromise;
    } finally {
      this.initPromise = null;
    }
  }

  private async _doInit(): Promise<void> {
    if (this.initialized || this.isClosed) return;
    if (typeof window === 'undefined' || !window.AudioContext) {
      this.initialized = true;
      return;
    }

    try {
      if (!this.ctx || this.ctx.state === 'closed') {
        this.ctx = new AudioContext({ sampleRate: this.sampleRate });
      }
      if (this.ctx.state === 'suspended') {
        await this.ctx.resume().catch(() => {});
      }

      if (this.ctx.audioWorklet && typeof this.ctx.audioWorklet.addModule === 'function') {
        let workletLoaded = false;
        try {
          await this.ctx.audioWorklet.addModule('/pcm-player-worklet.js');
          workletLoaded = true;
        } catch {
          if (typeof Blob !== 'undefined' && typeof URL !== 'undefined' && URL.createObjectURL) {
            const blobUrl = URL.createObjectURL(
              new Blob([INLINE_PCM_WORKLET_SOURCE], { type: 'application/javascript' }),
            );
            try {
              await this.ctx.audioWorklet.addModule(blobUrl);
              workletLoaded = true;
            } finally {
              URL.revokeObjectURL(blobUrl);
            }
          }
        }

        if (workletLoaded && !this.isClosed && this.ctx) {
          this.workletNode = new AudioWorkletNode(this.ctx, 'pcm-player-processor');
          this.workletNode.connect(this.ctx.destination);
          this.bindPort();
          this.useFallbackBufferQueue = false;
        } else {
          this.useFallbackBufferQueue = true;
        }
      } else {
        this.useFallbackBufferQueue = true;
      }
    } catch {
      // Fall back to scheduled AudioBufferSourceNodes rather than re-creating AudioContexts
      this.useFallbackBufferQueue = true;
    }

    this.initialized = true;
    if (!this.isClosed && this.pendingChunks.length > 0) {
      const queued = this.pendingChunks.splice(0, this.pendingChunks.length);
      for (const chunk of queued) {
        this._dispatchChunk(chunk);
      }
    }
  }

  private _playFallbackBuffer(pcmChunk: ArrayBuffer): void {
    if (!this.ctx || this.isClosed) return;
    try {
      const int16 = new Int16Array(pcmChunk);
      if (int16.length === 0) return;
      const audioBuf = this.ctx.createBuffer(1, int16.length, this.sampleRate);
      const ch = audioBuf.getChannelData(0);
      let peak = 0;
      for (let i = 0; i < int16.length; i++) {
        const v = int16[i] / 32768.0;
        ch[i] = v;
        const abs = v < 0 ? -v : v;
        if (abs > peak) peak = abs;
      }
      if (this.levelHandler) this.levelHandler(peak);

      const src = this.ctx.createBufferSource();
      src.buffer = audioBuf;
      src.connect(this.ctx.destination);
      const now = this.ctx.currentTime;
      const startAt = Math.max(now + 0.01, this.nextStartTime);
      this.nextStartTime = startAt + audioBuf.duration;
      this.activeSources.add(src);
      src.onended = () => {
        this.activeSources.delete(src);
        if (this.activeSources.size === 0 && this.levelHandler) {
          this.levelHandler(0);
        }
      };
      src.start(startAt);
    } catch {
      // Ignore audio device errors in headless environments
    }
  }

  private _dispatchChunk(pcmChunk: ArrayBuffer): void {
    if (this.isClosed) return;
    if (this.ctx && this.ctx.state === 'suspended') {
      this.ctx.resume().catch(() => {});
    }
    if (this.workletNode) {
      this.workletNode.port.postMessage({ pcm: pcmChunk });
    } else if (this.useFallbackBufferQueue) {
      this._playFallbackBuffer(pcmChunk);
    }
  }

  push(pcmChunk: ArrayBuffer): void {
    if (this.isClosed || !pcmChunk || pcmChunk.byteLength === 0) return;

    if (!this.initialized) {
      this.pendingChunks.push(pcmChunk.slice(0));
      void this.init();
      return;
    }

    this._dispatchChunk(pcmChunk);
  }

  flush(): void {
    this.pendingChunks = [];
    if (this.workletNode) {
      this.workletNode.port.postMessage({ type: 'flush' });
    }
    for (const src of this.activeSources) {
      try {
        src.stop();
        src.disconnect();
      } catch {
        // Ignore already-stopped source
      }
    }
    this.activeSources.clear();
    this.nextStartTime = this.ctx ? this.ctx.currentTime : 0;
    if (this.levelHandler) this.levelHandler(0);
  }

  close(): void {
    this.isClosed = true;
    this.flush();
    this.levelHandler = null;
    if (this.workletNode) {
      this.workletNode.port.onmessage = null;
      this.workletNode.disconnect();
      this.workletNode = null;
    }
    if (this.ctx && this.ctx.state !== 'closed') {
      this.ctx.close().catch(() => {});
      this.ctx = null;
    }
  }
}

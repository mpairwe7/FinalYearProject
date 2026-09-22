/**
 * Streaming PCM16 LE audio player using AudioWorklet and jitter buffer.
 */

export class PCMPlayer {
  private ctx: AudioContext | null = null;
  private workletNode: AudioWorkletNode | null = null;
  private initialized = false;
  private isClosed = false;
  private sampleRate: number;

  constructor(sampleRate = 16000) {
    this.sampleRate = sampleRate;
  }

  async init(): Promise<void> {
    if (this.initialized || this.isClosed) return;

    if (typeof window === 'undefined' || !window.AudioContext) {
      return;
    }

    try {
      this.ctx = new AudioContext({ sampleRate: this.sampleRate });
      if (this.ctx.state === 'suspended') {
        await this.ctx.resume();
      }

      await this.ctx.audioWorklet.addModule('/pcm-player-worklet.js');
      this.workletNode = new AudioWorkletNode(this.ctx, 'pcm-player-processor');
      this.workletNode.connect(this.ctx.destination);
      this.initialized = true;
    } catch {
      // Degrade gracefully if worklet loading fails
      this.initialized = false;
    }
  }

  push(pcmChunk: ArrayBuffer): void {
    if (this.isClosed || !pcmChunk || pcmChunk.byteLength === 0) return;

    if (!this.initialized) {
      this.init().then(() => {
        if (this.workletNode) {
          this.workletNode.port.postMessage({ pcm: pcmChunk });
        }
      }).catch(() => {});
      return;
    }

    if (this.ctx && this.ctx.state === 'suspended') {
      this.ctx.resume().catch(() => {});
    }

    if (this.workletNode) {
      this.workletNode.port.postMessage({ pcm: pcmChunk });
    }
  }

  flush(): void {
    if (this.workletNode) {
      this.workletNode.port.postMessage({ type: 'flush' });
    }
  }

  close(): void {
    this.isClosed = true;
    this.flush();
    if (this.workletNode) {
      this.workletNode.disconnect();
      this.workletNode = null;
    }
    if (this.ctx && this.ctx.state !== 'closed') {
      this.ctx.close().catch(() => {});
      this.ctx = null;
    }
  }
}

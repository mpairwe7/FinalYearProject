/**
 * AudioWorklet processor for streaming PCM16 capture.
 *
 * Registers as "pcm16-processor".  Buffers incoming audio frames
 * and posts PCM16 LE chunks to the main thread every ~20ms.
 *
 * Usage:
 *   const ctx = new AudioContext({ sampleRate: 16000 });
 *   await ctx.audioWorklet.addModule('/audio-worklet-processor.js');
 *   const node = new AudioWorkletNode(ctx, 'pcm16-processor');
 *   node.port.onmessage = (e) => ws.sendAudioChunk(e.data);
 *   source.connect(node);
 */

class PCM16Processor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._targetRate = 16000;
    this._inRate = typeof sampleRate === 'number' && sampleRate > 0 ? sampleRate : 16000;
    this._chunkSize = 320; // 20ms at 16kHz (640 bytes PCM16 LE)
    this._buffer = [];
    this._srcPos = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;

    const samples = input[0]; // Float32Array, mono channel 0
    if (samples.length === 0) return true;

    if (Math.abs(this._inRate - this._targetRate) < 100) {
      for (let i = 0; i < samples.length; i++) {
        this._buffer.push(samples[i]);
      }
    } else {
      const step = this._inRate / this._targetRate;
      while (this._srcPos < samples.length) {
        const start = Math.floor(this._srcPos);
        const end = Math.min(samples.length, Math.max(start + 1, Math.floor(this._srcPos + step)));
        let sum = 0;
        for (let i = start; i < end; i++) sum += samples[i];
        this._buffer.push(sum / (end - start));
        this._srcPos += step;
      }
      this._srcPos -= samples.length;
    }

    // When we have enough 16kHz samples (320 = 20ms), convert to PCM16 LE and post
    while (this._buffer.length >= this._chunkSize) {
      const chunk = this._buffer.splice(0, this._chunkSize);
      const pcm16 = new Int16Array(chunk.length);
      for (let i = 0; i < chunk.length; i++) {
        // Clamp to [-1, 1] then scale to Int16 range
        const s = Math.max(-1, Math.min(1, chunk[i]));
        pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
      }
      this.port.postMessage(pcm16.buffer, [pcm16.buffer]);
    }

    return true; // Keep processor alive
  }
}

registerProcessor('pcm16-processor', PCM16Processor);

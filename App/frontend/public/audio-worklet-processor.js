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
    this._buffer = [];
    // ~20ms of samples at whatever sampleRate the context uses
    this._chunkSize = Math.floor(sampleRate * 0.02);
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;

    const samples = input[0]; // Float32Array, mono channel 0

    for (let i = 0; i < samples.length; i++) {
      this._buffer.push(samples[i]);
    }

    // When we have enough samples, convert to PCM16 LE and post
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

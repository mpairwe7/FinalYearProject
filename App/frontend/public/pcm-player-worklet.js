/**
 * Jitter-buffered PCM16 LE AudioWorklet player.
 * Receives PCM16 audio chunks over MessagePort, converts to Float32, and streams to output.
 */

class PCMPlayerProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    // Pre-allocated circular or growable buffer of float32 samples
    this.buffer = new Float32Array(16000 * 5); // 5 seconds capacity at 16kHz
    this.readIndex = 0;
    this.writeIndex = 0;
    this.bufferedSamples = 0;
    this.isPlaying = false;
    this.minBufferSamples = 16000 * 0.06; // 60ms initial jitter buffer

    this.port.onmessage = (event) => {
      const data = event.data;
      if (!data) return;

      if (data.type === 'flush') {
        this.readIndex = 0;
        this.writeIndex = 0;
        this.bufferedSamples = 0;
        this.isPlaying = false;
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
      const floatVal = int16[i] / 32768.0;
      this.buffer[this.writeIndex] = floatVal;
      this.writeIndex = (this.writeIndex + 1) % this.buffer.length;
    }

    this.bufferedSamples += len;
    if (!this.isPlaying && this.bufferedSamples >= this.minBufferSamples) {
      this.isPlaying = true;
    }
  }

  process(inputs, outputs) {
    const output = outputs[0];
    if (!output || output.length === 0) return true;

    const channel = output[0];
    const framesNeeded = channel.length; // usually 128 frames

    if (!this.isPlaying || this.bufferedSamples === 0) {
      channel.fill(0);
      return true;
    }

    if (this.bufferedSamples < framesNeeded) {
      // Underrun condition
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
      return true;
    }

    for (let i = 0; i < framesNeeded; i++) {
      channel[i] = this.buffer[this.readIndex];
      this.readIndex = (this.readIndex + 1) % this.buffer.length;
    }
    this.bufferedSamples -= framesNeeded;

    return true;
  }
}

registerProcessor('pcm-player-processor', PCMPlayerProcessor);

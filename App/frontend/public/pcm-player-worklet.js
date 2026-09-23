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
    // Level reporting for the call orb. Measured here rather than where chunks
    // are enqueued, because the jitter buffer means audio is handed over well
    // before it is heard — an orb driven by enqueue time pulses ahead of the
    // voice. Posting every 8 render quanta is ~30Hz at 16kHz, enough for a
    // smooth pulse and cheap enough for the audio thread.
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
        // A barge-in cuts the sentence off mid-word; say so, or the orb keeps
        // glowing at whatever level the discarded audio last reached.
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
      this._reportLevel(channel, framesNeeded);
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

  /**
   * Post the loudest sample of the last ~8 quanta to the main thread.
   *
   * Peak rather than RMS: speech is mostly quiet between syllables, and an
   * averaged level makes the orb look sluggish against a voice that is plainly
   * audible. The peak is reset after each post so the next window measures
   * itself rather than decaying from an earlier shout.
   */
  _reportLevel(channel, framesNeeded) {
    for (let i = 0; i < framesNeeded; i++) {
      const magnitude = channel[i] < 0 ? -channel[i] : channel[i];
      if (magnitude > this.levelPeak) this.levelPeak = magnitude;
    }

    this.quantaSinceLevel += 1;
    if (this.quantaSinceLevel < 8) return;

    this.port.postMessage({ type: 'level', level: this.levelPeak });
    this.quantaSinceLevel = 0;
    this.levelPeak = 0;
  }
}

registerProcessor('pcm-player-processor', PCMPlayerProcessor);

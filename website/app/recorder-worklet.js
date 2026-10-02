/* AudioWorklet processor for the web studio's real-time recorder.
 * Copies each render quantum of input to the main thread with the audio
 * clock time of its first frame so takes align sample-accurately with the
 * transport. It never resamples or compresses: the recorded PCM is the
 * device input at the AudioContext sample rate. */
class AnharmonicCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.active = true;
    this.port.onmessage = event => { if (event.data === 'stop') this.active = false; };
  }
  process(inputs) {
    if (!this.active) return false;
    const input = inputs[0];
    if (!input || !input.length) return true;
    const channels = input.slice(0, 2).map(channel => Float32Array.from(channel));
    this.port.postMessage({ time: currentTime, channels }, channels.map(channel => channel.buffer));
    return true;
  }
}
registerProcessor('anharmonic-capture', AnharmonicCaptureProcessor);

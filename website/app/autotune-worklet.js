/* Live vocal monitor for the web studio: tuner plus low-latency tuned monitoring.
 *
 * The pitch tracker is the same YIN detector as the offline Autotune
 * (vocal-dsp.js), run on the last 2048 input samples every 256 samples. Target
 * notes use the same scale, range and hysteresis rules. Correction is a
 * two-tap delay-line pitch shifter (about 12 ms of delay, its window locked to
 * whole voice periods) so a singer hears the
 * tuned voice while recording. Like the desktop, this is monitoring only:
 * takes are always recorded dry and tuned later with the full render.
 * GPL-3.0-or-later. */
import './vocal-dsp.js';

const { pitchFrame, allowedNotes, DEFAULTS, VOICING } = globalThis.AnharmonicVocal;
const FRAME = 2048, HOP = 256, RING = 8192, WINDOW = 1152;
// The taps sit half a window apart; keeping that a whole number of voice periods
// makes each crossfade phase-coherent, so the shifted pitch is exact.
const coherentWindow = hz => { const period = sampleRate / hz; return Math.min(2 * RING / 5, Math.max(512, 2 * period * Math.max(1, Math.round(WINDOW / 2 / period)))); };

class AnharmonicAutotuneProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const config = options.processorOptions || {};
    this.mode = config.mode || 'tuned';
    this.history = new Float32Array(FRAME); this.frame = new Float32Array(FRAME); this.filled = 0; this.sinceHop = 0;
    this.ring = new Float32Array(RING); this.write = 0; this.phase = 0; this.window = WINDOW; this.targetWindow = WINDOW;
    this.ratio = 1; this.desired = 1; this.previous = null; this.unvoiced = 3; this.scratch = {}; this.reports = 0; this.alive = true;
    this.configure(config.settings);
    this.port.onmessage = event => {
      const data = event.data || {};
      if (data.type === 'settings') this.configure(data.settings);
      if (data.type === 'mode') this.mode = data.mode;
      if (data.type === 'stop') this.alive = false;
    };
  }
  configure(settings) {
    const s = { ...DEFAULTS, ...(settings || {}) };
    this.settings = s; this.choices = allowedNotes(s);
    this.lowHz = 440 * Math.pow(2, (s.low_note - 69) / 12); this.highHz = 440 * Math.pow(2, (s.high_note - 69) / 12);
    this.previous = null;
  }
  // One detection on the most recent FRAME samples; sets the desired shift ratio.
  detect() {
    const start = this.filled % FRAME;
    this.frame.set(this.history.subarray(start), 0); this.frame.set(this.history.subarray(0, start), FRAME - start);
    const [hz, confidence] = pitchFrame(this.frame, sampleRate, this.lowHz, this.highHz, this.scratch);
    const s = this.settings;
    let midi = NaN, target = NaN;
    if (hz > 0) {
      midi = 69 + 12 * Math.log2(hz / 440);
      let candidate = this.choices[0];
      for (const choice of this.choices) if (Math.abs(choice - midi) < Math.abs(candidate - midi)) candidate = choice;
      if (this.previous !== null && this.unvoiced < 3 && candidate !== this.previous && Math.abs(candidate - midi) + .15 >= Math.abs(this.previous - midi)) candidate = this.previous;
      this.previous = candidate; this.unvoiced = 0; target = candidate + (s.transpose | 0); this.targetWindow = coherentWindow(hz);
      const depth = s.enabled ? s.strength * (1 - s.humanize * Math.min(1, confidence)) : 0;
      this.desired = Math.min(2.04, Math.max(.49, Math.pow(2, (target - midi) * depth / 12)));
    } else { this.unvoiced++; this.desired = 1; }
    if (++this.reports % 4 === 0) this.port.postMessage({ type: 'pitch', hz, midi, target, confidence: confidence >= VOICING ? confidence : 0, ratio: this.ratio });
  }
  process(inputs, outputs) {
    if (!this.alive) return false;
    const input = inputs[0]?.[0], output = outputs[0], left = output[0], right = output[1] || output[0];
    if (!input) { left.fill(0); if (right !== left) right.fill(0); return true; }
    const s = this.settings, retune = Math.max(0, s.retune_ms / 1000);
    const smoothing = retune <= 0 ? 1 : 1 - Math.exp(-1 / (retune * sampleRate));
    const wet = this.mode === 'tuned' ? Math.min(1, Math.max(0, s.mix)) : 0, silent = this.mode === 'off';
    for (let i = 0; i < input.length; i++) {
      const sample = input[i];
      this.history[this.filled % FRAME] = sample; this.filled++;
      if (++this.sinceHop >= HOP && this.filled >= FRAME) { this.sinceHop = 0; this.detect(); }
      this.ring[this.write] = sample;
      this.ratio += (this.desired - this.ratio) * smoothing;
      this.window += (this.targetWindow - this.window) * .002;
      // Two read taps sweep through the window half a cycle apart; sin² gains sum to one.
      this.phase += (1 - this.ratio) / this.window; this.phase -= Math.floor(this.phase);
      let shifted = 0;
      for (let tap = 0; tap < 2; tap++) {
        const phase = (this.phase + tap * .5) % 1, gain = Math.sin(Math.PI * phase) ** 2;
        const read = this.write - (phase * this.window + 2), base = Math.floor(read), frac = read - base;
        const a = this.ring[(base % RING + RING) % RING], b = this.ring[((base + 1) % RING + RING) % RING];
        shifted += gain * (a + (b - a) * frac);
      }
      this.write = (this.write + 1) % RING;
      const value = silent ? 0 : sample * (1 - wet) + shifted * wet;
      left[i] = value; if (right !== left) right[i] = value;
    }
    return true;
  }
}
registerProcessor('anharmonic-autotune', AnharmonicAutotuneProcessor);

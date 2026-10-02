/* Anharmonic Native and Prism instrument DSP for the web studio.
 *
 * `mpcSynth` is a line-for-line port of `mpc_synth` in mpclab/native/synth.cpp,
 * the voice renderer shared by the desktop Native synth and the Prism plugin.
 * `SynthEngine` ports prism::Engine (plugins/prism/src/Engine.h) and
 * `PrismProcessor` ports the plugin's arp, motion, macros, layer mix and
 * effects (plugins/prism/src/Processor.cpp). Reverb follows JUCE's Freeverb
 * algorithm exactly; the chorus follows juce::dsp::Chorus. Everything is plain
 * double-precision JavaScript so the same code runs in an AudioWorklet, in an
 * OfflineAudioContext export and in Node tests. GPL-3.0-or-later. */
(function (root) {
  'use strict';
  const PI = 3.14159265358979323846264338327950288;
  const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
  const exp2 = value => Math.pow(2, value);

  function wrapPhase(phase) {
    if (phase >= 0 && phase < 2147483648) return phase - Math.trunc(phase);
    return phase % 1;
  }
  function blep(phase, step) {
    let out = 0;
    if (phase < step) { const x = phase / step; out = x + x - x * x - 1; }
    if (phase > 1 - step) { const x = (phase - 1) / step; out = x * x + x + x + 1; }
    return out;
  }
  // Kinds 0–3 match the C code: saw, sine, triangle, pulse. Kind 4 is a web
  // extension for older browser projects that chose a noise oscillator.
  function oscillator(kind, phase, step, pw, noise) {
    if (kind === 1) return Math.sin(2 * PI * phase);
    if (kind === 2) return 1 - 4 * Math.abs(phase - 0.5);
    if (kind === 3) {
      let shifted = phase - pw;
      if (shifted < 0) shifted += 1;
      return (phase < pw ? 1 : -1) + blep(phase, step) - blep(shifted, step);
    }
    if (kind === 4) return noise;
    return 2 * phase - 1 - blep(phase, step);
  }

  /* params p[0..22]: sr, f1, f2, lfo_rate, attack_inc, decay_dec, sustain,
   * release_frames, osc1, osc2, mix, spread, pulse_width, sub, noise,
   * lfo_pitch, lfo_filter, filter_env, cutoff, resonance_k, drive, gain, norm.
   * state s[0..18] as documented in synth.cpp. `out` is interleaved stereo
   * Float32 and is accumulated into, exactly like the C function. */
  function mpcSynth(out, n, noise, p, s, age, gate) {
    const sr = p[0], sums = [0, 0, 0];
    let env = s[4], release = s[6];
    let stage = s[5] | 0, dead = s[7] | 0, pending = s[12] | 0;
    let pendingL = s[13], pendingR = s[14], pendingEnv = s[15], pendingLfo = s[16];
    let heldL = s[17], heldR = s[18];
    const kind1 = p[8] | 0, kind2 = p[9] | 0;
    const steps = [0, 0, 0], phases = [0, 0, 0];
    for (let i = 0; i < n; i++) {
      const lfoPhase = wrapPhase(s[3] + i * p[3] / sr);
      const lfo = Math.sin(2 * PI * lfoPhase);
      const pitch = exp2(lfo * p[15] / 1200);
      steps[0] = Math.min(0.45, p[1] * pitch / sr);
      steps[1] = Math.min(0.45, p[2] * pitch / sr);
      steps[2] = Math.min(0.45, p[1] * 0.5 * pitch / sr);
      for (let c = 0; c < 3; c++) { phases[c] = wrapPhase(s[c] + sums[c]); sums[c] += steps[c]; }
      const osc1 = oscillator(kind1, phases[0], steps[0], p[12], noise[i * 2]);
      const osc2 = oscillator(kind2, phases[1], steps[1], p[12], noise[i * 2 + 1]);
      const sub = Math.sin(2 * PI * phases[2]);
      const left = ((osc1 * (1 - p[10]) * (1 + p[11]) + osc2 * p[10] * (1 - p[11])) + (sub * p[13] + noise[i * 2] * p[14])) * p[22];
      const right = ((osc1 * (1 - p[10]) * (1 - p[11]) + osc2 * p[10] * (1 + p[11])) + (sub * p[13] + noise[i * 2 + 1] * p[14])) * p[22];
      if (gate >= 0 && age + i >= gate && stage !== 3 && !dead) { stage = 3; release = Math.max(1e-9, env / p[7]); }
      if (stage === 0) { env += p[4]; if (env >= 1) { env = 1; stage = 1; } }
      else if (stage === 1) { env -= p[5]; if (env <= p[6]) { env = p[6]; stage = 2; } }
      else if (stage === 2) env = p[6];
      else { env = Math.max(0, env - release); if (env <= 0) dead = 1; }
      if (pending) {
        const pairL = Math.tanh((pendingL + left) * 0.5 * p[20]);
        const pairR = Math.tanh((pendingR + right) * 0.5 * p[20]);
        const filterRate = sr * 0.5;
        let cutoff = p[18] * exp2(p[17] * pendingEnv * 4.5 + p[16] * pendingLfo * 3);
        cutoff = Math.min(filterRate * 0.44, Math.max(30, cutoff));
        const blend = Math.min(1, Math.max(0, (cutoff - filterRate * 0.28) / (filterRate * 0.16)));
        const g = Math.tan(PI * cutoff / filterRate);
        const a1 = 1 / (1 + g * (g + p[19]));
        const a2 = g * a1, a3 = g * a2;
        let v3 = pairL - s[9], v1 = a1 * s[8] + a2 * v3;
        const v2L = s[9] + a2 * s[8] + a3 * v3;
        s[8] = 2 * v1 - s[8]; s[9] = 2 * v2L - s[9];
        v3 = pairR - s[11]; v1 = a1 * s[10] + a2 * v3;
        const v2R = s[11] + a2 * s[10] + a3 * v3;
        s[10] = 2 * v1 - s[10]; s[11] = 2 * v2R - s[11];
        heldL = v2L * (1 - blend) + pairL * blend;
        heldR = v2R * (1 - blend) + pairR * blend;
        pending = 0;
      } else {
        pending = 1; pendingL = left; pendingR = right; pendingEnv = env; pendingLfo = lfo;
      }
      out[i * 2] = Math.fround(out[i * 2] + Math.fround(heldL * env * p[21]));
      out[i * 2 + 1] = Math.fround(out[i * 2 + 1] + Math.fround(heldR * env * p[21]));
    }
    for (let c = 0; c < 3; c++) s[c] = wrapPhase(s[c] + sums[c]);
    s[3] = (s[3] + n * p[3] / sr) % 1;
    s[4] = env; s[5] = stage; s[6] = release; s[7] = dead;
    s[12] = pending; s[13] = pendingL; s[14] = pendingR; s[15] = pendingEnv; s[16] = pendingLfo;
    s[17] = heldL; s[18] = heldR;
  }

  const WAVE_KIND = { saw: 0, sawtooth: 0, sine: 1, triangle: 2, square: 3, pulse: 3, noise: 4 };
  const PRISM_WAVES = ['saw', 'sine', 'triangle', 'square'];
  const finite = (value, fallback) => Number.isFinite(Number(value)) ? Number(value) : fallback;

  // Native SynthPatch fields (desktop names) to the Prism engine's patch struct.
  function patchFromNative(patch = {}) {
    return {
      osc1: WAVE_KIND[patch.osc1 ?? 'saw'] ?? 0, osc2: WAVE_KIND[patch.osc2 ?? 'square'] ?? 0,
      mix: clamp(finite(patch.osc_mix, .42), 0, 1), octave: finite(patch.osc2_octave, 0), detune: finite(patch.detune, 8),
      pulse: clamp(finite(patch.pulse_width, .5), .1, .9), sub: finite(patch.sub, .18), noise: finite(patch.noise, .015),
      attack: finite(patch.attack, .025), decay: finite(patch.decay, .32), sustain: finite(patch.sustain, .68), release: finite(patch.release, .65),
      cutoff: Math.min(20000, finite(patch.cutoff, 2400)), resonance: clamp(finite(patch.resonance, .28), 0, 1), filterEnv: finite(patch.filter_env, .38),
      drive: Math.max(0, finite(patch.drive, .18)), spread: clamp(finite(patch.spread, .42), 0, 1), lfoRate: finite(patch.lfo_rate, .32),
      lfoPitch: finite(patch.lfo_pitch, 2), lfoFilter: finite(patch.lfo_filter, .08), volume: Math.max(0, finite(patch.volume, .42))
    };
  }
  function defaultPatch() { return patchFromNative({}); }

  class Voice {
    constructor() { this.state = new Float64Array(19); this.reset(); }
    reset() { this.state.fill(0); this.note = -1; this.channel = 1; this.age = 0; this.gate = -1; this.velocity = 0; this.random = 1; this.id = null; this.releaseFrames = 0; }
  }

  // prism::Engine: fixed voices, oldest-voice stealing, per-voice xorshift noise.
  class SynthEngine {
    constructor(sampleRate = 48000, voiceCount = 32) {
      this.sr = sampleRate; this.patch = defaultPatch();
      this.voices = Array.from({ length: voiceCount }, () => new Voice());
      this.bend = new Float64Array(16); this.sustain = new Array(16).fill(false); this.keys = new Uint8Array(2048);
      this.out = new Float32Array(128); this.noise = new Float64Array(128); this.params = new Float64Array(23);
    }
    reset() { this.voices.forEach(voice => voice.reset()); this.keys.fill(0); this.sustain.fill(false); this.bend.fill(0); }
    active() { return this.voices.some(voice => voice.note >= 0); }
    on(note, channel, velocity, id = null) {
      if (note < 0 || note > 127 || channel < 1 || channel > 16) return null;
      this.keys[(channel - 1) * 128 + note] = 1;
      let chosen = this.voices.find(voice => voice.note < 0);
      if (!chosen) chosen = this.voices.reduce((oldest, voice) => voice.age > oldest.age ? voice : oldest, this.voices[0]);
      chosen.reset();
      chosen.note = note; chosen.channel = channel; chosen.velocity = velocity; chosen.random = (note * 7919 + channel) >>> 0; chosen.id = id;
      return chosen;
    }
    off(note, channel) {
      if (note < 0 || note > 127 || channel < 1 || channel > 16) return;
      this.keys[(channel - 1) * 128 + note] = 0;
      if (!this.sustain[channel - 1]) for (const voice of this.voices) if (voice.note === note && voice.channel === channel && voice.gate < 0) voice.gate = voice.age;
    }
    // Web extension: release one scheduled note by its identity, optionally with a short fade.
    release(id, fadeFrames = 0) {
      for (const voice of this.voices) if (voice.note >= 0 && voice.id === id) {
        if (fadeFrames > 0) voice.releaseFrames = fadeFrames;
        if (voice.gate < 0) voice.gate = voice.age;
        else if (fadeFrames > 0 && voice.state[5] === 3) voice.state[6] = Math.max(voice.state[6], voice.state[4] / fadeFrames);
      }
    }
    pedal(channel, down) {
      this.sustain[channel - 1] = down;
      if (!down) for (const voice of this.voices) if (voice.note >= 0 && voice.channel === channel && !this.keys[(channel - 1) * 128 + voice.note] && voice.gate < 0) voice.gate = voice.age;
    }
    render(left, right, count, offset = 0) {
      const patch = this.patch, out = this.out, noise = this.noise, p = this.params;
      while (count > 0) {
        const n = Math.min(count, 64);
        out.fill(0, 0, n * 2);
        for (const voice of this.voices) {
          if (voice.note < 0) continue;
          for (let i = 0; i < n * 2; i++) {
            let r = voice.random;
            r ^= r << 13; r >>>= 0; r ^= r >>> 17; r ^= r << 5; r >>>= 0;
            voice.random = r; noise[i] = r / 4294967295 * 2 - 1;
          }
          const hz = 440 * exp2((voice.note - 69 + this.bend[voice.channel - 1]) / 12);
          p[0] = this.sr; p[1] = hz; p[2] = hz * exp2(patch.octave + patch.detune / 1200); p[3] = Math.max(.01, patch.lfoRate);
          p[4] = 1 / Math.max(1, Math.floor(Math.max(.0005, patch.attack) * this.sr));
          p[5] = (1 - patch.sustain) / Math.max(1, Math.floor(Math.max(.001, patch.decay) * this.sr));
          p[6] = patch.sustain;
          p[7] = voice.releaseFrames > 0 ? voice.releaseFrames : Math.max(1, Math.floor(Math.max(.005, patch.release) * this.sr));
          p[8] = patch.osc1; p[9] = patch.osc2; p[10] = patch.mix; p[11] = patch.spread * .32; p[12] = patch.pulse;
          p[13] = patch.sub; p[14] = patch.noise; p[15] = patch.lfoPitch; p[16] = patch.lfoFilter; p[17] = patch.filterEnv;
          p[18] = patch.cutoff; p[19] = 2 - 1.92 * Math.min(.98, patch.resonance); p[20] = 1 + patch.drive * 9;
          p[21] = voice.velocity * patch.volume; p[22] = 1 / Math.max(1, 1 + patch.sub + patch.noise);
          mpcSynth(out, n, noise, p, voice.state, voice.age, voice.gate);
          voice.age += n;
          if (voice.state[7]) voice.note = -1;
        }
        for (let i = 0; i < n; i++) { left[offset + i] = out[i * 2]; right[offset + i] = out[i * 2 + 1]; }
        offset += n; count -= n;
      }
    }
  }

  // JUCE juce::Reverb (Freeverb) with its exact scale factors and tunings.
  class Comb {
    constructor(size) { this.buffer = new Float32Array(size); this.index = 0; this.last = 0; }
    process(input, damp, feedback) {
      const output = this.buffer[this.index];
      this.last = Math.fround(output * (1 - damp) + this.last * damp);
      this.buffer[this.index] = input + this.last * feedback;
      this.index = (this.index + 1) % this.buffer.length;
      return output;
    }
  }
  class AllPass {
    constructor(size) { this.buffer = new Float32Array(size); this.index = 0; }
    process(input) {
      const buffered = this.buffer[this.index];
      this.buffer[this.index] = input + buffered * 0.5;
      this.index = (this.index + 1) % this.buffer.length;
      return buffered - input;
    }
  }
  class Freeverb {
    constructor(sampleRate) {
      const combs = [1116, 1188, 1277, 1356, 1422, 1491, 1557, 1617], passes = [556, 441, 341, 225], spread = 23, scale = sampleRate / 44100;
      this.combs = [0, 1].map(side => combs.map(size => new Comb(Math.round((size + side * spread) * scale))));
      this.passes = [0, 1].map(side => passes.map(size => new AllPass(Math.round((size + side * spread) * scale))));
      this.set({ roomSize: .5, damping: .45, wetLevel: 0, dryLevel: 1, width: 1 });
    }
    set({ roomSize, damping, wetLevel, dryLevel, width }) {
      const wet = wetLevel * 3;
      this.dry = dryLevel * 2; this.wet1 = .5 * wet * (1 + width); this.wet2 = .5 * wet * (1 - width);
      this.damp = damping * .4; this.feedback = roomSize * .28 + .7;
    }
    reset() { this.combs.flat().forEach(comb => { comb.buffer.fill(0); comb.last = 0; }); this.passes.flat().forEach(pass => pass.buffer.fill(0)); }
    process(left, right, n) {
      for (let i = 0; i < n; i++) {
        const input = (left[i] + right[i]) * 0.015;
        let outL = 0, outR = 0;
        for (let j = 0; j < 8; j++) { outL += this.combs[0][j].process(input, this.damp, this.feedback); outR += this.combs[1][j].process(input, this.damp, this.feedback); }
        for (let j = 0; j < 4; j++) { outL = this.passes[0][j].process(outL); outR = this.passes[1][j].process(outR); }
        left[i] = outL * this.wet1 + outR * this.wet2 + left[i] * this.dry;
        right[i] = outR * this.wet1 + outL * this.wet2 + right[i] * this.dry;
      }
    }
  }
  // juce::dsp::Chorus: sine LFO, 20 ms modulation, linear-interpolated delay, feedback, linear dry/wet.
  class Chorus {
    constructor(sampleRate) {
      this.sr = sampleRate; this.size = Math.ceil((20 * .5 + 100) * sampleRate / 1000) + 2;
      this.lines = [new Float32Array(this.size), new Float32Array(this.size)]; this.write = 0; this.last = [0, 0]; this.phase = 0;
      this.rate = .35; this.depth = .3; this.centre = 12; this.feedback = .12; this.mix = 0;
    }
    reset() { this.lines.forEach(line => line.fill(0)); this.last = [0, 0]; this.phase = 0; }
    process(left, right, n) {
      if (this.mix <= 0) { this.idle(left, right, n); return; }
      const channels = [left, right];
      for (let i = 0; i < n; i++) {
        const lfo = Math.sin(this.phase) * this.depth * .5;
        this.phase += 2 * PI * this.rate / this.sr; if (this.phase > PI) this.phase -= 2 * PI;
        const delay = Math.max(1, 20 * lfo + this.centre) * this.sr / 1000;
        for (let c = 0; c < 2; c++) {
          const input = channels[c][i], line = this.lines[c];
          line[this.write] = input - this.last[c];
          let read = this.write - delay; while (read < 0) read += this.size;
          const index = Math.floor(read), frac = read - index;
          const output = line[index] * (1 - frac) + line[(index + 1) % this.size] * frac;
          this.last[c] = output * this.feedback;
          channels[c][i] = input * (1 - this.mix) + output * this.mix;
        }
        this.write = (this.write + 1) % this.size;
      }
    }
    // Keep the delay line moving while bypassed so turning it on starts clean.
    idle(left, right, n) { for (let i = 0; i < n; i++) { this.lines[0][this.write] = 0; this.lines[1][this.write] = 0; this.write = (this.write + 1) % this.size; } }
  }

  // Prism parameter table (plugins/prism/parameters.json order) and normalized↔plain mapping.
  const PRISM_SPECS = [
    ['osc1', 0, 3, 0, 1], ['osc2', 0, 3, 3, 1], ['osc_mix', 0, 1, .42, 0], ['osc2_octave', -4, 4, 0, 1], ['detune', 0, 30, 8, 0], ['pulse_width', .1, .9, .5, 0], ['sub', 0, .8, .18, 0], ['noise', 0, .35, .015, 0],
    ['attack', .0005, 3, .025, 0], ['decay', .005, 3, .32, 0], ['sustain', 0, 1, .68, 0], ['release', .005, 5, .65, 0], ['cutoff', 50, 18000, 2400, 0], ['resonance', 0, .96, .28, 0], ['filter_env', 0, 1, .38, 0],
    ['drive', 0, 1, .18, 0], ['spread', 0, 1, .42, 0], ['lfo_rate', .03, 12, .32, 0], ['lfo_pitch', 0, 30, 2, 0], ['lfo_filter', 0, .8, .08, 0], ['volume', 0, .75, .42, 0],
    ['arp_on', 0, 1, 0, 1], ['arp_rate', 0, 5, 2, 1], ['arp_mode', 0, 3, 0, 1], ['arp_octaves', 1, 4, 1, 1], ['arp_gate', .05, 1, .72, 0],
    ['echo', 0, .6, 0, 0], ['feedback', 0, .85, .36, 0], ['echo_rate', 0, 5, 3, 1], ['space', 0, .6, 0, 0], ['size', 0, 1, .55, 0], ['chorus', 0, 1, 0, 0],
    ['b_osc1', 0, 3, 0, 1], ['b_osc2', 0, 3, 3, 1], ['b_osc_mix', 0, 1, .42, 0], ['b_osc2_octave', -4, 4, 0, 1], ['b_detune', 0, 30, 8, 0], ['b_pulse_width', .1, .9, .5, 0], ['b_sub', 0, .8, .18, 0], ['b_noise', 0, .35, .015, 0],
    ['b_attack', .0005, 3, .025, 0], ['b_decay', .005, 3, .32, 0], ['b_sustain', 0, 1, .68, 0], ['b_release', .005, 5, .65, 0], ['b_cutoff', 50, 18000, 2400, 0], ['b_resonance', 0, .96, .28, 0], ['b_filter_env', 0, 1, .38, 0],
    ['b_drive', 0, 1, .18, 0], ['b_spread', 0, 1, .42, 0], ['b_lfo_rate', .03, 12, .32, 0], ['b_lfo_pitch', 0, 30, 2, 0], ['b_lfo_filter', 0, .8, .08, 0], ['b_volume', 0, .75, .42, 0],
    ['layer_mix', 0, 1, 0, 0], ['macro_tone', 0, 1, 0, 0], ['macro_motion', 0, 1, 0, 0], ['macro_space', 0, 1, 0, 0], ['macro_texture', 0, 1, 0, 0],
    ['seq_depth', -1, 1, 0, 0], ['seq_rate', 0, 5, 2, 1], ['seq_target', 0, 4, 0, 1], ['seq_1', 0, 1, .5, 0], ['seq_2', 0, 1, .8, 0], ['seq_3', 0, 1, .35, 0], ['seq_4', 0, 1, 1, 0], ['seq_5', 0, 1, .5, 0], ['seq_6', 0, 1, .2, 0], ['seq_7', 0, 1, .7, 0], ['seq_8', 0, 1, 0, 0],
    ['mod_rate', .03, 12, .5, 0], ['mod_depth', -1, 1, 0, 0], ['mod_target', 0, 4, 0, 1], ['crush_bits', 4, 16, 16, 1], ['crush_mix', 0, 1, 0, 0], ['trem_rate', .03, 12, 2, 0], ['trem_depth', 0, 1, 0, 0]
  ].map(([id, low, high, initial, step], index) => ({ id, index, low, high, initial, step }));
  const LOG_IDS = new Set(['cutoff', 'attack', 'decay', 'release', 'lfo_rate', 'b_cutoff', 'b_attack', 'b_decay', 'b_release', 'b_lfo_rate', 'mod_rate', 'trem_rate']);
  const PRISM_INDEX = Object.fromEntries(PRISM_SPECS.map(spec => [spec.id, spec.index]));
  function prismNormalize(spec, value) {
    const proportion = clamp((value - spec.low) / (spec.high - spec.low), 0, 1);
    return LOG_IDS.has(spec.id) ? Math.pow(proportion, .3) : proportion;
  }
  function prismPlain(spec, normalized) {
    const proportion = clamp(finite(normalized, 0), 0, 1);
    let value = spec.low + (LOG_IDS.has(spec.id) ? Math.pow(proportion, 1 / .3) : proportion) * (spec.high - spec.low);
    if (spec.step > 0) value = clamp(Math.round(value), spec.low, spec.high);
    return value;
  }
  function prismDefaults() { return Float64Array.from(PRISM_SPECS, spec => spec.initial); }
  // Desktop projects store Prism as {"0": normalized, …}; missing entries take neutral defaults.
  function prismValuesFromParameters(parameters = {}) {
    const values = prismDefaults();
    for (const spec of PRISM_SPECS) if (Object.hasOwn(parameters, String(spec.index))) values[spec.index] = prismPlain(spec, parameters[String(spec.index)]);
    return values;
  }
  function prismParametersFromValues(values) {
    return Object.fromEntries(PRISM_SPECS.map(spec => [String(spec.index), prismNormalize(spec, values[spec.index])]));
  }
  // PrismProcessor::loadSound / loadLayer, applied to a plain value array.
  function prismLoadSound(values, item) {
    const patch = item?.patch; if (!patch || typeof patch !== 'object') return values;
    const next = Float64Array.from(values);
    for (let i = 21; i < PRISM_SPECS.length; i++) next[i] = PRISM_SPECS[i].initial;
    for (let i = 0; i < 21; i++) {
      const spec = PRISM_SPECS[i], value = patch[spec.id] ?? spec.initial;
      next[i] = clamp(i < 2 ? Math.max(0, PRISM_WAVES.indexOf(String(value))) : finite(value, spec.initial), spec.low, spec.high);
    }
    const arp = item.arp;
    if (arp && typeof arp === 'object') {
      next[21] = arp.enabled ? 1 : 0;
      const divisions = [.125, 1 / 3, .25, .5, .75, 1]; const beats = finite(arp.rate_beats, .25);
      next[22] = Math.max(0, divisions.findIndex(value => Math.abs(beats - value) < .001)); if (next[22] < 0) next[22] = 2;
      next[23] = Math.max(0, ['up', 'down', 'up/down', 'random'].indexOf(arp.mode ?? 'up'));
      next[24] = clamp(finite(arp.octaves, 1), 1, 4); next[25] = clamp(finite(arp.gate, .72), .05, 1);
    }
    const effects = item.effects;
    if (effects && typeof effects === 'object') for (let i = 26; i < PRISM_SPECS.length; i++) {
      const spec = PRISM_SPECS[i]; if (Object.hasOwn(effects, spec.id)) next[i] = clamp(finite(effects[spec.id], spec.initial), spec.low, spec.high);
    }
    return next;
  }
  function prismLoadLayer(values, item, second) {
    const patch = item?.patch; if (!patch || typeof patch !== 'object') return values;
    const next = Float64Array.from(values), offset = second ? 32 : 0;
    for (let i = 0; i < 21; i++) {
      const spec = PRISM_SPECS[i], value = patch[spec.id] ?? spec.initial;
      next[offset + i] = clamp(i < 2 ? Math.max(0, PRISM_WAVES.indexOf(String(value))) : finite(value, spec.initial), spec.low, spec.high);
    }
    if (second && next[53] === 0) next[53] = .5;
    return next;
  }

  const DIVISIONS = [.125, 1 / 3, .25, .5, .75, 1];
  // PrismProcessor: two engines, arp, step/LFO motion, macros and effects.
  class PrismProcessor {
    constructor(sampleRate = 48000, values = prismDefaults()) {
      this.sr = sampleRate; this.values = Float64Array.from(values); this.tempo = 120;
      this.engine = new SynthEngine(sampleRate, 32); this.layerB = new SynthEngine(sampleRate, 32);
      this.held = new Float32Array(16 * 128); this.arpEnabled = false; this.rng = 0x9e3779b9;
      this.delay = [new Float32Array(Math.floor(sampleRate * 4) + 1), new Float32Array(Math.floor(sampleRate * 4) + 1)]; this.delayIndex = 0;
      this.reverb = new Freeverb(sampleRate); this.chorus = new Chorus(sampleRate); this.tremPhase = 0;
      this.layerLeft = new Float32Array(128); this.layerRight = new Float32Array(128);
      this.resetNotes();
    }
    resetNotes() {
      this.engine.reset(); this.layerB.reset(); this.sequenceBeat = this.motionPhase = 0;
      this.held.fill(0); this.arpNote = -1; this.arpChannel = 1; this.arpIndex = 0; this.arpNext = this.arpGate = 0;
    }
    panic() { this.resetNotes(); this.delay.forEach(line => line.fill(0)); this.reverb.reset(); this.chorus.reset(); }
    setValues(values) { this.values = Float64Array.from(values); }
    readPatch() {
      const v = this.values;
      const read = offset => ({ osc1: v[offset], osc2: v[offset + 1], mix: v[offset + 2], octave: v[offset + 3], detune: v[offset + 4], pulse: v[offset + 5], sub: v[offset + 6], noise: v[offset + 7],
        attack: v[offset + 8], decay: v[offset + 9], sustain: v[offset + 10], release: v[offset + 11], cutoff: v[offset + 12], resonance: v[offset + 13], filterEnv: v[offset + 14],
        drive: v[offset + 15], spread: v[offset + 16], lfoRate: v[offset + 17], lfoPitch: v[offset + 18], lfoFilter: v[offset + 19], volume: v[offset + 20] });
      this.baseA = read(0); this.baseB = read(32);
      for (const patch of [this.baseA, this.baseB]) {
        patch.cutoff = clamp(patch.cutoff * exp2(v[54] * 3), 50, 18000);
        patch.lfoFilter = clamp(patch.lfoFilter + v[55] * .5, 0, .8);
        patch.lfoPitch = clamp(patch.lfoPitch + v[55] * 6, 0, 30);
        patch.drive = clamp(patch.drive + v[57] * .5, 0, 1);
        patch.noise = clamp(patch.noise + v[57] * .08, 0, .35);
        patch.detune = clamp(patch.detune + v[57] * 10, 0, 30);
      }
      const enabled = v[21] > .5;
      if (enabled !== this.arpEnabled) { this.resetNotes(); this.arpEnabled = enabled; }
    }
    noteOn(note, velocity, channel = 1, id = null) {
      this.held[(channel - 1) * 128 + note] = velocity;
      if (!this.arpEnabled) { this.engine.on(note, channel, velocity, id); this.layerB.on(note, channel, velocity, id); }
    }
    noteOff(note, channel = 1) {
      this.held[(channel - 1) * 128 + note] = this.engine.sustain[channel - 1] ? -Math.abs(this.held[(channel - 1) * 128 + note]) : 0;
      if (!this.arpEnabled) { this.engine.off(note, channel); this.layerB.off(note, channel); }
    }
    release(id, note, fadeFrames) {
      this.held[note] = 0;
      if (!this.arpEnabled) { this.engine.release(id, fadeFrames); this.layerB.release(id, fadeFrames); }
    }
    render(l, r, count, offset = 0) {
      const v = this.values;
      while (count > 0) {
        if (this.arpEnabled) {
          if (this.arpNote >= 0 && this.arpGate <= 0) {
            for (const synth of [this.engine, this.layerB]) for (const voice of synth.voices) if (voice.note === this.arpNote && voice.channel === this.arpChannel && voice.gate < 0) voice.gate = voice.age;
            this.arpNote = -1;
          }
          if (this.arpNext <= 0) {
            const notes = [], channels = [], velocities = [];
            const octaves = v[24] | 0;
            for (let o = 0; o < octaves; o++) for (let n = 0; n < 128; n++) for (let c = 1; c <= 16; c++) {
              const held = this.held[(c - 1) * 128 + n];
              if (held !== 0 && n + o * 12 < 128 && notes.length < 512) { notes.push(n + o * 12); channels.push(c); velocities.push(Math.abs(held)); }
            }
            const size = notes.length;
            if (size > 0) {
              let index = this.arpIndex % size; const mode = v[23] | 0;
              if (mode === 1) index = size - 1 - index;
              if (mode === 2 && size > 1) { index = this.arpIndex % (2 * size - 2); if (index >= size) index = 2 * size - 2 - index; }
              if (mode === 3) { let x = this.rng; x ^= x << 13; x >>>= 0; x ^= x >>> 17; x ^= x << 5; x >>>= 0; this.rng = x; index = x % size; }
              this.arpNote = notes[index]; this.arpChannel = channels[index];
              this.engine.on(this.arpNote, this.arpChannel, velocities[index]); this.layerB.on(this.arpNote, this.arpChannel, velocities[index]);
              this.arpIndex = (this.arpIndex + 1) % 1000000;
            }
            this.arpNext += this.sr * 60 / this.tempo * DIVISIONS[clamp(v[22] | 0, 0, 5)];
            this.arpGate = this.arpNext * v[25];
          }
        }
        let n = Math.min(count, 64);
        if (this.arpEnabled) {
          n = Math.min(n, Math.max(1, Math.ceil(this.arpNext)));
          if (this.arpNote >= 0) n = Math.min(n, Math.max(1, Math.ceil(this.arpGate)));
        }
        this.engine.patch = { ...this.baseA }; this.layerB.patch = { ...this.baseB };
        const stepLength = DIVISIONS[clamp(v[59] | 0, 0, 5)];
        const step = Math.floor(this.sequenceBeat / stepLength) % 8;
        this.motionStep = step;
        const sequence = (v[61 + step] * 2 - 1) * v[58];
        const motion = Math.sin(this.motionPhase * 2 * PI) * v[70];
        let balance = 0;
        const modulate = (amount, target) => {
          for (const patch of [this.engine.patch, this.layerB.patch]) {
            if (target === 0) patch.cutoff = clamp(patch.cutoff * exp2(amount * 4), 50, 18000);
            if (target === 1) patch.detune = clamp(patch.detune + amount * 30, 0, 30);
            if (target === 2) patch.volume *= clamp(1 + amount, 0, 1);
            if (target === 4) patch.mix = clamp(patch.mix + amount * .5, 0, 1);
          }
          if (target === 3) balance = clamp(balance + amount, -1, 1);
        };
        modulate(sequence, v[60] | 0); modulate(motion, v[71] | 0);
        const mix = Math.fround(v[53]);
        this.engine.render(l, r, n, offset);
        this.layerLeft.fill(0, 0, n); this.layerRight.fill(0, 0, n);
        // Rendering is skipped only for a layer that is completely muted by the mix.
        if (mix > 0 || this.layerB.active()) this.layerB.render(this.layerLeft, this.layerRight, n, 0);
        for (let i = 0; i < n; i++) {
          l[offset + i] = (l[offset + i] * (1 - mix) + this.layerLeft[i] * mix) * Math.fround(1 - Math.max(0, balance));
          r[offset + i] = (r[offset + i] * (1 - mix) + this.layerRight[i] * mix) * Math.fround(1 + Math.min(0, balance));
        }
        this.sequenceBeat = (this.sequenceBeat + n / this.sr * this.tempo / 60) % (stepLength * 8);
        this.motionPhase = (this.motionPhase + n / this.sr * v[69]) % 1;
        if (this.arpEnabled) { this.arpNext -= n; this.arpGate -= n; }
        offset += n; count -= n;
      }
    }
    // The post-instrument chain of processBlock: chorus, echo, reverb, crush and tremolo, then clamp.
    effects(l, r, n) {
      const v = this.values;
      this.chorus.mix = v[31]; this.chorus.process(l, r, n);
      const length = this.delay[0].length;
      const time = clamp(Math.trunc(this.sr * 60 / this.tempo * DIVISIONS[clamp(v[28] | 0, 0, 5)]), 1, length - 1);
      const mix = Math.min(.6, Math.fround(v[26]) + Math.fround(v[56]) * .25), feedback = Math.fround(v[27]);
      const dl = this.delay[0], dr = this.delay[1];
      for (let i = 0; i < n; i++) {
        const read = (this.delayIndex + length - time) % length;
        const a = dl[read], b = dr[read];
        dl[this.delayIndex] = Math.tanh(l[i] + b * feedback); dr[this.delayIndex] = Math.tanh(r[i] + a * feedback);
        l[i] += a * mix; r[i] += b * mix;
        this.delayIndex = (this.delayIndex + 1) % length;
      }
      this.reverb.set({ roomSize: v[30], wetLevel: Math.min(.6, v[29] + v[56] * .3), dryLevel: 1, width: 1, damping: .45 });
      this.reverb.process(l, r, n);
      const steps = exp2(v[72] - 1), crush = v[73];
      for (let i = 0; i < n; i++) {
        const trem = 1 - v[75] * (.5 - .5 * Math.cos(this.tremPhase * 2 * PI));
        this.tremPhase = (this.tremPhase + v[74] / this.sr) % 1;
        l[i] = clamp((l[i] * (1 - crush) + Math.round(l[i] * steps) / steps * crush) * trem, -1, 1);
        r[i] = clamp((r[i] * (1 - crush) + Math.round(r[i] * steps) / steps * crush) * trem, -1, 1);
      }
    }
  }

  // Native: the desktop's built-in synth, a single engine without Prism effects.
  class NativeInstrument {
    constructor(sampleRate = 48000, patch = {}) { this.engine = new SynthEngine(sampleRate, 64); this.setPatch(patch); }
    setPatch(patch) { this.engine.patch = patchFromNative(patch); }
    noteOn(note, velocity, channel = 1, id = null) { this.engine.on(note, channel, clamp(velocity, 0, 1), id); }
    noteOff(note, channel = 1) { this.engine.off(note, channel); }
    release(id, note, fadeFrames) { this.engine.release(id, fadeFrames); }
    panic() { this.engine.reset(); }
    readPatch() {}
    render(l, r, count, offset = 0) { this.engine.render(l, r, count, offset); }
    effects() {}
  }

  const api = { mpcSynth, SynthEngine, PrismProcessor, NativeInstrument, Freeverb, Chorus, patchFromNative, PRISM_SPECS, PRISM_INDEX, LOG_IDS, PRISM_WAVES,
    prismNormalize, prismPlain, prismDefaults, prismValuesFromParameters, prismParametersFromValues, prismLoadSound, prismLoadLayer };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.AnharmonicDSP = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);

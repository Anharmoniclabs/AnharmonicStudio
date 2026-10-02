/* Vocal pitch correction and cleanup for the web studio.
 *
 * A port of the desktop's default ("legacy") Autotune in mpclab/vocal.py:
 * YIN pitch tracking (2048-sample frames, 512 hop), scale quantization with
 * hysteresis, 170 ms overlapping resampled grains, the tone filter (high-pass,
 * presence, de-esser as a 768-tap FIR from the biquad cascade), the gate and
 * compressor, output gain and the 0.98 peak guard. Tests compare it with the
 * Python implementation. Runs in a Web Worker (vocal-worker.js) and in Node.
 * GPL-3.0-or-later. */
(function (root) {
  'use strict';
  const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
  const SCALES = { chromatic: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11], major: [0, 2, 4, 5, 7, 9, 11], minor: [0, 2, 3, 5, 7, 8, 10], pentatonic: [0, 2, 4, 7, 9] };
  const VOICING = 0.35;
  const f32 = Math.fround;
  const clip = (value, low, high) => Math.min(high, Math.max(low, value));
  // Python's round(): halves go to the even neighbour.
  const roundEven = value => { const floor = Math.floor(value), diff = value - floor; return diff > .5 || (diff === .5 && floor % 2 !== 0) ? floor + 1 : floor; };
  const DEFAULTS = { enabled: true, key: 'C', scale: 'chromatic', strength: 1, retune_ms: 25, humanize: .15, mix: 1, transpose: 0, formant: .75, low_note: 36, high_note: 84, gate_db: -55, highpass_hz: 80, deesser: .25, compression: .35, presence_db: 1.5, output_db: 0 };
  const settingsOf = settings => ({ ...DEFAULTS, ...(settings || {}) });

  // In-place iterative radix-2 complex FFT (sign -1 forward, +1 inverse, unscaled).
  function fft(re, im, inverse = false) {
    const n = re.length;
    for (let i = 1, j = 0; i < n; i++) {
      let bit = n >> 1;
      for (; j & bit; bit >>= 1) j ^= bit;
      j ^= bit;
      if (i < j) { let t = re[i]; re[i] = re[j]; re[j] = t; t = im[i]; im[i] = im[j]; im[j] = t; }
    }
    for (let size = 2; size <= n; size <<= 1) {
      const angle = (inverse ? 2 : -2) * Math.PI / size, wr = Math.cos(angle), wi = Math.sin(angle), half = size >> 1;
      for (let start = 0; start < n; start += size) {
        let cr = 1, ci = 0;
        for (let k = 0; k < half; k++) {
          const a = start + k, b = a + half;
          const tr = re[b] * cr - im[b] * ci, ti = re[b] * ci + im[b] * cr;
          re[b] = re[a] - tr; im[b] = im[a] - ti; re[a] += tr; im[a] += ti;
          const next = cr * wr - ci * wi; ci = cr * wi + ci * wr; cr = next;
        }
      }
    }
  }
  const nextPow2 = value => { let size = 1; while (size < value) size <<= 1; return size; };

  // mpclab.vocal._pitch_frame: YIN cumulative-mean normalized difference via FFT autocorrelation.
  function pitchFrame(frame, sr, lowHz, highHz, scratch) {
    const n = frame.length;
    let mean = 0; for (let i = 0; i < n; i++) mean += frame[i]; mean /= n;
    const take = (name, size) => { if (!scratch) return new Float64Array(size); if (scratch[name]?.length !== size) scratch[name] = new Float64Array(size); return scratch[name]; };
    const x = take('x', n); let power = 0;
    for (let i = 0; i < n; i++) { x[i] = f32(frame[i] - f32(mean)); power += x[i] * x[i]; }
    if (Math.sqrt(power / n) < 2e-4) return [0, 0];
    const size = nextPow2(Math.max(2, n * 2 - 1));
    const re = take('re', size).fill(0), im = take('im', size).fill(0);
    re.set(x);
    fft(re, im);
    for (let k = 0; k < size; k++) { re[k] = re[k] * re[k] + im[k] * im[k]; im[k] = 0; }
    fft(re, im, true);
    const ac = k => re[k] / size;
    const energy = take('energy', n + 1);
    for (let i = 0; i < n; i++) energy[i + 1] = energy[i] + x[i] * x[i];
    if (energy[n] <= 1e-10) return [0, 0];
    const normalized = take('normalized', n).fill(1);
    let cumulative = 0;
    for (let lag = 1; lag < n; lag++) {
      const difference = energy[n - lag] + energy[n] - energy[lag] - 2 * ac(lag);
      cumulative += difference;
      normalized[lag] = difference * lag / Math.max(cumulative, 1e-12);
    }
    const lo = Math.max(2, Math.floor(sr / Math.max(highHz, 1))), hi = Math.min(n - 2, Math.ceil(sr / Math.max(lowHz, 1)));
    if (hi <= lo) return [0, 0];
    let lag = -1;
    for (let index = lo; index <= hi; index++) if (normalized[index] < .18) { lag = index; break; }
    if (lag >= 0) { while (lag < hi && normalized[lag + 1] < normalized[lag]) lag++; }
    else { lag = lo; for (let index = lo + 1; index <= hi; index++) if (normalized[index] < normalized[lag]) lag = index; }
    const confidence = clip(1 - normalized[lag], 0, 1);
    if (confidence < VOICING) return [0, confidence];
    const denom = normalized[lag - 1] - 2 * normalized[lag] + normalized[lag + 1];
    const delta = Math.abs(denom) > 1e-12 ? .5 * (normalized[lag - 1] - normalized[lag + 1]) / denom : 0;
    return [sr / (lag + clip(delta, -.5, .5)), confidence];
  }

  function allowedNotes(settings) {
    const s = settingsOf(settings), rootIndex = Math.max(0, NOTE_NAMES.indexOf(s.key)), intervals = SCALES[s.scale] || SCALES.chromatic;
    const notes = [];
    for (let midi = s.low_note | 0; midi <= (s.high_note | 0); midi++) if (intervals.includes(((midi - rootIndex) % 12 + 12) % 12)) notes.push(midi);
    return notes.length ? notes : [60];
  }
  // _quantize_pitch_curve: nearest allowed note with hysteresis so held notes never chatter.
  function quantize(midi, voiced, choices, transpose, hysteresis = .15, resetFrames = 3) {
    const targets = Float32Array.from(midi); let previous = null, unvoiced = resetFrames;
    for (let index = 0; index < midi.length; index++) {
      if (!voiced[index]) { unvoiced++; continue; }
      const pitch = midi[index];
      let candidate = choices[0], best = Math.abs(f32(choices[0] - pitch));
      for (const choice of choices) { const distance = Math.abs(f32(choice - pitch)); if (distance < best) { best = distance; candidate = choice; } }
      if (previous !== null && unvoiced < resetFrames && candidate !== previous) {
        if (Math.abs(candidate - pitch) + hysteresis >= Math.abs(previous - pitch)) candidate = previous;
      }
      targets[index] = candidate + (transpose | 0);
      previous = candidate; unvoiced = 0;
    }
    return targets;
  }
  // channels: array of Float32Array (1 or 2). Follows the loudest channel, like the desktop.
  function analyzePitch(channels, settings, sr, progress) {
    const s = settingsOf(settings);
    let mono = channels[0];
    if (channels.length > 1) {
      const energy = channels.map(channel => { let sum = 0; for (let i = 0; i < channel.length; i++) sum += channel[i] * channel[i]; return sum; });
      mono = channels[energy.indexOf(Math.max(...energy))];
    }
    const frameSize = 2048, hop = 512;
    if (mono.length < frameSize) { const padded = new Float32Array(frameSize); padded.set(mono); mono = padded; }
    const count = 1 + Math.max(0, Math.floor((mono.length - frameSize) / hop));
    const hz = new Float32Array(count), confidence = new Float32Array(count), scratch = {};
    const lowHz = 440 * Math.pow(2, (s.low_note - 69) / 12), highHz = 440 * Math.pow(2, (s.high_note - 69) / 12);
    for (let index = 0; index < count; index++) {
      if (progress && index % 64 === 0) progress(index / count);
      const [frequency, conf] = pitchFrame(mono.subarray(index * hop, index * hop + frameSize), sr, lowHz, highHz, scratch);
      hz[index] = frequency; confidence[index] = conf;
    }
    const midi = new Float32Array(count).fill(NaN), voiced = new Uint8Array(count);
    for (let index = 0; index < count; index++) if (hz[index] > 0) { voiced[index] = 1; midi[index] = 69 + 12 * Math.log2(hz[index] / 440); }
    const target = quantize(midi, voiced, allowedNotes(s), s.transpose);
    const times = Float32Array.from({ length: count }, (_, index) => (index * hop + frameSize * .5) / sr);
    return { times, hz, midi, target, confidence };
  }
  function detectKey(analysis) {
    const classes = [], weights = [];
    for (let i = 0; i < analysis.midi.length; i++) if (analysis.hz[i] > 0 && Number.isFinite(analysis.midi[i]) && analysis.confidence[i] >= VOICING) { classes.push(((Math.round(analysis.midi[i]) % 12) + 12) % 12); weights.push(analysis.confidence[i]); }
    if (!classes.length) return { key: 'C', scale: 'major', confidence: 0 };
    const total = Math.max(weights.reduce((a, b) => a + b, 0), 1e-9);
    let best = null;
    for (const scale of ['major', 'minor']) for (let rootIndex = 0; rootIndex < 12; rootIndex++) {
      let inside = 0, tonic = 0, fifth = 0;
      classes.forEach((pc, i) => { if (SCALES[scale].includes((pc - rootIndex + 12) % 12)) inside += weights[i]; if (pc === rootIndex) tonic += weights[i]; if (pc === (rootIndex + 7) % 12) fifth += weights[i]; });
      const score = inside / total + .06 * tonic / total + .025 * fifth / total;
      if (!best || score > best.score || (score === best.score && (rootIndex > best.root || scale > best.scale))) best = { score, root: rootIndex, scale };
    }
    return { key: NOTE_NAMES[best.root], scale: best.scale, confidence: Math.min(1, best.score) };
  }

  // RBJ biquads exactly as mpclab.fx._biquad, then the cascade's 768-tap FIR (cascade_ir).
  function biquad(kind, frequency, q, gainDb, sr) {
    frequency = clip(frequency, 20, sr * .45); q = Math.max(.05, q);
    const w = 2 * Math.PI * frequency / sr, cos = Math.cos(w), sin = Math.sin(w), alpha = sin / (2 * q), amp = Math.pow(10, gainDb / 40);
    if (kind === 'highpass') return [[(1 + cos) / 2, -(1 + cos), (1 + cos) / 2], [1 + alpha, -2 * cos, 1 - alpha]];
    if (kind === 'peak') return [[1 + alpha * amp, -2 * cos, 1 - alpha * amp], [1 + alpha / amp, -2 * cos, 1 - alpha / amp]];
    const s = 2 * Math.sqrt(amp) * alpha; // highshelf
    return [[amp * ((amp + 1) + (amp - 1) * cos + s), -2 * amp * ((amp - 1) + (amp + 1) * cos), amp * ((amp + 1) + (amp - 1) * cos - s)],
      [(amp + 1) - (amp - 1) * cos + s, 2 * ((amp - 1) - (amp + 1) * cos), (amp + 1) - (amp - 1) * cos - s]];
  }
  function cascadeIR(sections, sr, taps = 768, size = 4096) {
    const half = size / 2, hr = new Float64Array(half + 1), hi = new Float64Array(half + 1);
    for (let k = 0; k <= half; k++) {
      const angle = -2 * Math.PI * k / size, zr = Math.cos(angle), zi = Math.sin(angle), z2r = zr * zr - zi * zi, z2i = 2 * zr * zi;
      let r = 1, i = 0;
      for (const [b, a] of sections) {
        const nr = b[0] + b[1] * zr + b[2] * z2r, ni = b[1] * zi + b[2] * z2i, dr = a[0] + a[1] * zr + a[2] * z2r, di = a[1] * zi + a[2] * z2i;
        const den = dr * dr + di * di, qr = (nr * dr + ni * di) / den, qi = (ni * dr - nr * di) / den;
        const tr = r * qr - i * qi; i = r * qi + i * qr; r = tr;
      }
      hr[k] = r; hi[k] = i;
    }
    // irfft: Hermitian spectrum to a real impulse response.
    const re = new Float64Array(size), im = new Float64Array(size);
    for (let k = 0; k <= half; k++) { re[k] = hr[k]; im[k] = k === 0 || k === half ? 0 : hi[k]; }
    for (let k = 1; k < half; k++) { re[size - k] = hr[k]; im[size - k] = -hi[k]; }
    fft(re, im, true);
    const ir = new Float32Array(taps); for (let n = 0; n < taps; n++) ir[n] = re[n] / size;
    const fade = Math.max(8, Math.floor(taps / 8));
    for (let n = 0; n < fade; n++) ir[taps - fade + n] *= fade === 1 ? 0 : 1 - n / (fade - 1);
    return ir;
  }
  // Linear convolution without latency (overlap-add via FFT), truncated to the input length.
  function convolve(signal, ir) {
    const block = 4096, size = nextPow2(block + ir.length - 1), out = new Float32Array(signal.length);
    const hr = new Float64Array(size), hi = new Float64Array(size); hr.set(ir); fft(hr, hi);
    const re = new Float64Array(size), im = new Float64Array(size);
    for (let start = 0; start < signal.length; start += block) {
      re.fill(0); im.fill(0); re.set(signal.subarray(start, Math.min(signal.length, start + block)));
      fft(re, im);
      for (let k = 0; k < size; k++) { const r = re[k] * hr[k] - im[k] * hi[k]; im[k] = re[k] * hi[k] + im[k] * hr[k]; re[k] = r; }
      fft(re, im, true);
      for (let n = 0; n < size && start + n < signal.length; n++) out[start + n] += re[n] / size;
    }
    return out;
  }
  function toneFilter(channels, s, sr) {
    const sections = [];
    if (s.highpass_hz > 22) sections.push(biquad('highpass', s.highpass_hz, .707, 0, sr));
    if (s.presence_db) sections.push(biquad('peak', 4200, .85, s.presence_db, sr));
    if (s.deesser > .001) sections.push(biquad('highshelf', 6200, .7, -10 * s.deesser, sr));
    if (!sections.length) return channels.map(channel => Float32Array.from(channel));
    const ir = cascadeIR(sections, sr);
    return channels.map(channel => convolve(channel, ir));
  }
  // _dynamics: 10 ms RMS blocks, soft gate and a level-dependent compressor, interpolated per sample.
  function dynamics(channels, s, sr) {
    const length = channels[0].length; if (!length) return channels;
    const block = Math.max(64, Math.trunc(sr * .01)), starts = [];
    for (let start = 0; start < length; start += block) starts.push(start);
    const amount = clip(s.compression, 0, 1), threshold = -12 - amount * 18, ratio = 1 + amount * 5;
    const control = starts.map(start => {
      const end = Math.min(length, start + block); let sum = 0;
      for (const channel of channels) for (let i = start; i < end; i++) sum += channel[i] * channel[i];
      const level = f32(Math.sqrt(sum / ((end - start) * channels.length) + 1e-12));
      const db = 20 * Math.log10(Math.max(level, 1e-7)), gate = clip((db - s.gate_db) / 8, 0, 1), above = Math.max(db - threshold, 0);
      return gate * Math.pow(10, -above * (1 - 1 / ratio) / 20);
    });
    const x = [...starts, length - 1], y = [...control, control[control.length - 1]];
    const envelope = new Float32Array(length); let segment = 0;
    for (let i = 0; i < length; i++) {
      while (segment < x.length - 2 && i > x[segment + 1]) segment++;
      const x0 = x[segment], x1 = x[segment + 1];
      envelope[i] = x1 === x0 ? y[segment + 1] : y[segment] + (y[segment + 1] - y[segment]) * (i - x0) / (x1 - x0);
    }
    return channels.map(channel => { const out = new Float32Array(length); for (let i = 0; i < length; i++) out[i] = channel[i] * envelope[i]; return out; });
  }
  function reflectIndex(index, length) {
    if (length === 1) return 0;
    const period = 2 * (length - 1); index %= period; if (index < 0) index += period;
    return index < length ? index : period - index;
  }
  // render_autotune (legacy backend): returns {channels, analysis}. Input mono becomes stereo like the desktop.
  function renderAutotune(input, settings, sr, progress) {
    const s = settingsOf(settings);
    let source = input.map(channel => Float32Array.from(channel, value => Number.isFinite(value) ? value : 0));
    if (source.length === 1) source = [source[0], Float32Array.from(source[0])];
    source = source.slice(0, 2);
    const length = source[0].length;
    if (!length) throw new Error('The vocal take is empty.');
    const report = (value, from, span) => progress && progress(from + span * value);
    const analysis = analyzePitch(source, s, sr, value => report(value, 0, .32));
    let corrected;
    if (!s.enabled || s.strength <= 0 || s.mix <= 0) corrected = source.map(channel => Float32Array.from(channel));
    else {
      const frameSize = 8192, hop = 2048, pad = frameSize * 2;
      const window = Float32Array.from({ length: frameSize }, (_, n) => .5 - .5 * Math.cos(2 * Math.PI * n / (frameSize - 1)));
      corrected = [new Float32Array(length), new Float32Array(length)];
      const weight = new Float32Array(length);
      const retune = Math.max(0, s.retune_ms / 1000), smoothing = retune <= 0 ? 1 : 1 - Math.exp(-(hop / sr) / retune);
      // Grains read where the previous grain's resampled waveform continues, wrapped by
      // whole detected periods, so overlapping grains stay in phase and held notes shift.
      let ratioState = 1, offset = 0, previousRatio = 1;
      const grains = Math.ceil(length / hop);
      for (let index = 0; index < grains; index++) {
        if (index % 16 === 0) report(index / grains, .32, .48);
        const outStart = index * hop, center = outStart + frameSize / 2, time = center / sr;
        let lo = 0, hi = analysis.times.length;
        while (lo < hi) { const mid = (lo + hi) >> 1; if (analysis.times[mid] < time) lo = mid + 1; else hi = mid; }
        const at = clip(lo, 0, analysis.times.length - 1);
        let desired = 1;
        if (Number.isFinite(analysis.midi[at]) && analysis.confidence[at] >= VOICING) {
          const error = f32(analysis.target[at] - analysis.midi[at]);
          const depth = s.strength * (1 - s.humanize * clip(analysis.confidence[at], 0, 1));
          desired = Math.pow(2, error * depth / 12);
        }
        ratioState += (desired - ratioState) * smoothing;
        const ratio = clip(ratioState, .49, 2.04), take = Math.min(frameSize, length - outStart);
        if (index) offset += hop * (.5 * (previousRatio + ratio) - 1);
        previousRatio = ratio;
        if (analysis.hz[at] > 0 && analysis.confidence[at] >= VOICING) { const period = sr / analysis.hz[at]; offset -= period * roundEven(offset / period); }
        for (let n = 0; n < take; n++) {
          const read = center + offset + f32(n - frameSize * .5) * ratio + pad, base = Math.floor(read), frac = f32(read - base);
          const a = reflectIndex(base - pad, length), b = reflectIndex(base + 1 - pad, length), w = window[n];
          for (let c = 0; c < 2; c++) corrected[c][outStart + n] += (source[c][a] * (1 - frac) + source[c][b] * frac) * w;
          weight[outStart + n] += w;
        }
      }
      for (let c = 0; c < 2; c++) for (let i = 0; i < length; i++) corrected[c][i] /= Math.max(weight[i], 1e-5);
    }
    report(1, .8, 0);
    const body = clip(s.formant, 0, 1) * .16, wet = clip(s.mix, 0, 1);
    let rendered = source.map((channel, c) => Float32Array.from(channel, (value, i) => { const blended = corrected[c][i] * (1 - body) + value * body; return value * (1 - wet) + blended * wet; }));
    rendered = toneFilter(rendered, s, sr); report(1, .9, 0);
    rendered = dynamics(rendered, s, sr);
    const gain = f32(Math.pow(10, s.output_db / 20));
    let peak = 0; for (const channel of rendered) for (let i = 0; i < length; i++) { channel[i] *= gain; peak = Math.max(peak, Math.abs(channel[i])); }
    if (peak > .98) { const scale = f32(.98 / peak); for (const channel of rendered) for (let i = 0; i < length; i++) channel[i] *= scale; }
    report(1, 1, 0);
    return { channels: rendered, analysis };
  }
  const noteName = midi => Number.isFinite(midi) ? NOTE_NAMES[((Math.round(midi) % 12) + 12) % 12] + (Math.floor(Math.round(midi) / 12) - 1) : '—';
  const api = { NOTE_NAMES, SCALES, VOICING, DEFAULTS, fft, pitchFrame, allowedNotes, quantize, analyzePitch, detectKey, biquad, cascadeIR, convolve, renderAutotune, noteName };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.AnharmonicVocal = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);

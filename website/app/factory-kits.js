/* Anharmonic official one-shot kits: original, deterministic drum synthesis.
 *
 * Every sound is generated from a fixed recipe and seed, so the kits ship as
 * a few kilobytes of code instead of audio files, regenerate identically in
 * every browser, and carry no third-party sample licences. Kits follow the
 * layout producers expect from trap and hip-hop packs: kicks, 808s, snares,
 * claps, closed/open hats, rims, percussion and cymbals. The Live kit models
 * acoustic drums (membrane modes, snare wires, cymbal partials and a small
 * room) rather than drum-machine circuits. GPL-3.0-or-later. */
(function (root) {
  'use strict';
  const SR = 48000, TAU = Math.PI * 2;

  // Small deterministic toolkit operating on Float64Array buffers.
  function rng(seed) {
    let state = seed >>> 0;
    return () => { state = (state + 0x6D2B79F5) >>> 0; let t = state; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  }
  const buffer = seconds => new Float64Array(Math.max(1, Math.round(seconds * SR)));
  function noise(length, seed) { const random = rng(seed), out = new Float64Array(length); for (let i = 0; i < length; i++) out[i] = random() * 2 - 1; return out; }
  function mixInto(target, source, gain = 1, offset = 0) { for (let i = 0; i < source.length && i + offset < target.length; i++) target[i + offset] += source[i] * gain; return target; }
  function envelope(out, attack, tau, hold = 0) {
    for (let i = 0; i < out.length; i++) {
      const t = i / SR, rise = attack > 0 ? Math.min(1, t / attack) : 1;
      out[i] *= rise * (t < attack + hold ? 1 : Math.exp(-(t - attack - hold) / tau));
    }
    return out;
  }
  // Sine with an exponential pitch glide from `start` to `end` Hz; returns phase-continuous audio.
  function sweep(length, start, end, glide, amplitude = 1, phase = 0) {
    const out = new Float64Array(length);
    for (let i = 0; i < length; i++) { const t = i / SR, f = end + (start - end) * Math.exp(-t / glide); phase += TAU * f / SR; out[i] = Math.sin(phase) * amplitude; }
    return out;
  }
  // A decaying sinusoidal mode via a rotating phasor (fast and stable).
  function partial(out, frequency, amplitude, tau, delay = 0, phase = 0) {
    if (frequency <= 0 || frequency >= SR / 2) return out;
    const w = TAU * frequency / SR, c = Math.cos(w), s = Math.sin(w), decay = Math.exp(-1 / (tau * SR));
    let re = Math.cos(phase) * amplitude, im = Math.sin(phase) * amplitude;
    for (let i = Math.round(delay * SR); i < out.length; i++) {
      out[i] += im;
      const nextRe = re * c - im * s; im = (re * s + im * c) * decay; re = nextRe * decay;
      if (Math.abs(re) + Math.abs(im) < 1e-7) break;
    }
    return out;
  }
  // RBJ biquad filters.
  function biquad(input, type, frequency, q = .707, gainDb = 0) {
    const w = TAU * Math.min(frequency, SR * .49) / SR, cos = Math.cos(w), alpha = Math.sin(w) / (2 * q), A = Math.pow(10, gainDb / 40);
    let b0, b1, b2, a0, a1, a2;
    if (type === 'lowpass') { b0 = (1 - cos) / 2; b1 = 1 - cos; b2 = b0; a0 = 1 + alpha; a1 = -2 * cos; a2 = 1 - alpha; }
    else if (type === 'highpass') { b0 = (1 + cos) / 2; b1 = -(1 + cos); b2 = b0; a0 = 1 + alpha; a1 = -2 * cos; a2 = 1 - alpha; }
    else if (type === 'bandpass') { b0 = alpha; b1 = 0; b2 = -alpha; a0 = 1 + alpha; a1 = -2 * cos; a2 = 1 - alpha; }
    else { b0 = 1 + alpha * A; b1 = -2 * cos; b2 = 1 - alpha * A; a0 = 1 + alpha / A; a1 = -2 * cos; a2 = 1 - alpha / A; }
    const out = new Float64Array(input.length); let x1 = 0, x2 = 0, y1 = 0, y2 = 0;
    for (let i = 0; i < input.length; i++) {
      const x = input[i], y = (b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2) / a0;
      x2 = x1; x1 = x; y2 = y1; y1 = y; out[i] = y;
    }
    return out;
  }
  const drive = (input, amount) => { const norm = Math.tanh(amount); return input.map(x => Math.tanh(x * amount) / norm); };
  // Small room: early reflections plus a short diffuse tail.
  function room(input, size = .5, mix = .2, seed = 7) {
    const out = Float64Array.from(input), random = rng(seed);
    for (let tap = 0; tap < 14; tap++) {
      const delay = Math.round((3 + random() * 28 * (.5 + size)) / 1000 * SR), gain = (.55 - tap * .03) * (random() * .5 + .5) * (tap % 2 ? -1 : 1);
      for (let i = 0; i < input.length - delay; i++) out[i + delay] += input[i] * gain * mix;
    }
    const combs = [1116, 1277, 1422, 1557].map(length => ({ line: new Float64Array(Math.round(length * size * .6 + 120)), index: 0, last: 0 }));
    for (let i = 0; i < out.length; i++) {
      let wet = 0;
      for (const comb of combs) { const y = comb.line[comb.index]; comb.last = y * .6 + comb.last * .4; comb.line[comb.index] = input[i] + comb.last * (.62 + size * .25); comb.index = (comb.index + 1) % comb.line.length; wet += y; }
      out[i] += wet * mix * .18;
    }
    return out;
  }
  // Classic 808 metal: six detuned square waves (the TR-808 cymbal/hat oscillators).
  function metal(length, scale = 1) {
    const out = new Float64Array(length);
    for (const f of [205.3, 304.4, 369.6, 522.7, 540, 800]) { const step = f * scale / SR; let phase = (f % 1); for (let i = 0; i < length; i++) { phase += step; out[i] += (phase % 1) < .5 ? 1 : -1; } }
    return out.map(x => x / 6);
  }
  // Acoustic cymbal partial cloud: many inharmonic modes with frequency-dependent decay.
  function cymbal(length, seed, { low = 350, high = 14000, count = 70, tau = 1.4, bright = 1 } = {}) {
    const out = new Float64Array(length), random = rng(seed);
    for (let k = 0; k < count; k++) {
      const f = low * Math.pow(high / low, Math.pow(random(), .8)), amp = (.3 + random() * .7) * Math.pow(f / 1000, .15 * bright - .35);
      partial(out, f, amp / count * 6, tau * (.35 + random() * .9) * Math.pow(2000 / f, .25), 0, random() * TAU);
    }
    return out;
  }
  // Bessel-membrane drum: modal ratios of an ideal circular membrane, plus pitch drop on the strike.
  const MEMBRANE = [1, 1.594, 2.136, 2.296, 2.653, 2.918, 3.156, 3.501];
  function membrane(length, f0, { tau = .35, drop = .05, modes = 6, brightness = .55, seed = 3 } = {}) {
    const out = new Float64Array(length), random = rng(seed);
    for (let m = 0; m < Math.min(modes, MEMBRANE.length); m++) {
      const ratio = MEMBRANE[m], amp = Math.pow(brightness, m) * (m ? .8 : 1), modeTau = tau / Math.pow(ratio, 1.15);
      let phase = random() * .2;
      for (let i = 0; i < length; i++) {
        const t = i / SR, f = f0 * ratio * (1 + drop * Math.exp(-t / .045));
        phase += TAU * f / SR; const level = amp * Math.exp(-t / modeTau); out[i] += Math.sin(phase) * level;
        if (level < 1e-6) break;
      }
    }
    return out;
  }
  // Dusty sampler character: tape drive, lower sample rate, 12-bit steps and a softened top.
  function dust(input, { rate = 26040, bits = 12, tone = 9500, heat = 1.6 } = {}) {
    const hot = drive(input, heat), held = new Float64Array(hot.length), hold = SR / rate, steps = Math.pow(2, bits - 1);
    let next = 0, value = 0;
    for (let i = 0; i < hot.length; i++) { if (i >= next) { value = Math.round(hot[i] * steps) / steps; next += hold; } held[i] = value; }
    return biquad(biquad(held, 'lowpass', tone, .6), 'highpass', 28, .7);
  }
  function finish(input, { fadeIn = .0006, fadeOut = .012, peak = .891 } = {}) {
    const out = Float32Array.from(input), length = out.length, rise = Math.round(fadeIn * SR), fall = Math.min(length, Math.round(fadeOut * SR));
    for (let i = 0; i < rise && i < length; i++) out[i] *= i / rise;
    for (let i = 0; i < fall; i++) out[length - 1 - i] *= i / fall;
    let max = 0; for (const x of out) max = Math.max(max, Math.abs(x));
    if (max > 0) for (let i = 0; i < length; i++) out[i] *= peak / max;
    // Trim inaudible tail below -72 dBFS to keep one-shots tight.
    let end = length; while (end > 1 && Math.abs(out[end - 1]) < peak * 2.5e-4) end--;
    return out.slice(0, Math.max(end, Math.min(length, 64)));
  }

  // ---- Shared voices -----------------------------------------------------
  function kick({ seconds = .5, start = 190, end = 52, glide = .028, tau = .2, click = .5, heat = 3, seed = 1 }) {
    const out = buffer(seconds), body = envelope(sweep(out.length, start, end, glide), .0008, tau);
    mixInto(out, body);
    mixInto(out, envelope(biquad(noise(out.length, seed), 'bandpass', 3800, .9), 0, .003), click);
    mixInto(out, envelope(sweep(out.length, start * 3, start * 1.5, .004), 0, .004), click * .5);
    return drive(out, heat);
  }
  function eight08({ seconds = 2.4, note = 43, tau = 1.1, heat = 1.4, punch = 1.5, tone = 0, seed = 2 }) {
    const f = 440 * Math.pow(2, (note - 69) / 12), out = buffer(seconds);
    mixInto(out, envelope(sweep(out.length, f * punch, f, .05), .001, tau, .02));
    mixInto(out, envelope(biquad(noise(out.length, seed), 'lowpass', 2500, .7), 0, .002), .15);
    let shaped = drive(out, heat);
    if (tone) shaped = biquad(shaped, 'lowpass', tone, .8);
    return biquad(shaped, 'highpass', 24, .7);
  }
  function snare808({ seconds = .38, body = 185, bodyTau = .06, noiseTau = .12, crack = 5200, seed = 11, heat = 1.4 }) {
    const out = buffer(seconds);
    const tone = envelope(sweep(out.length, body * 1.35, body, .012), 0, bodyTau);
    mixInto(tone, envelope(sweep(out.length, body * 1.9, body * 1.75, .02), 0, bodyTau * .6), .5);
    mixInto(out, tone, .7);
    let wires = biquad(biquad(noise(out.length, seed), 'highpass', 1500, .7), 'lowpass', 7800, .6);
    wires = biquad(wires, 'peaking', crack, 1.1, 5);
    mixInto(out, envelope(wires, .0005, noiseTau), .9);
    return drive(out, heat);
  }
  function clap({ seconds = .5, bursts = [0, .009, .019, .03], centre = 1250, tau = .12, space = .35, seed = 21 }) {
    const out = buffer(seconds), source = noise(out.length, seed);
    const band = mixInto(biquad(source, 'bandpass', centre, 1.3), biquad(source, 'bandpass', centre * 2.1, 1.6), .6);
    for (let i = 0; i < out.length; i++) {
      const t = i / SR; let level = 0;
      for (const at of bursts.slice(0, -1)) if (t >= at) level += Math.exp(-(t - at) / .0045);
      const last = bursts[bursts.length - 1]; if (t >= last) level += Math.exp(-(t - last) / tau) * 1.2;
      out[i] = band[i] * level;
    }
    return space ? room(out, .45, space, seed) : out;
  }
  function hat808({ seconds = .15, tau = .028, scale = 1, centre = 10000, seed = 31, open = false }) {
    const out = buffer(seconds);
    const metallic = mixInto(metal(out.length, scale), noise(out.length, seed), .25);
    let band = biquad(biquad(metallic, 'bandpass', centre, .8), 'highpass', open ? 6500 : 7400, .7);
    band = envelope(band, .0003, tau);
    return band;
  }
  function liveSnare({ seconds = .55, f0 = 200, tau = .16, wires = 1, rim = 0, seed = 41, space = .22 }) {
    const out = buffer(seconds);
    mixInto(out, membrane(out.length, f0, { tau, drop: .06, modes: 8, brightness: .62, seed }), .8);
    const click = envelope(biquad(noise(out.length, seed + 1), 'bandpass', 3200, .8), 0, .0025);
    mixInto(out, click, .9);
    // Snare wires buzz against the resonant head: noise shaped by the head's own decay, slightly delayed.
    let buzz = biquad(biquad(noise(out.length, seed + 2), 'highpass', 1900, .7), 'peaking', 6200, .9, 4);
    buzz = envelope(buzz, .0015, tau * .85);
    mixInto(out, buzz, .75 * wires);
    if (rim) { partial(out, 920, .35 * rim, .045); partial(out, 1490, .25 * rim, .03); partial(out, 3150, .18 * rim, .02); }
    return room(drive(out, 1.3), .5, space, seed);
  }
  function liveKick({ seconds = .7, f0 = 56, tau = .28, beater = .6, seed = 51, space = .12 }) {
    const out = buffer(seconds);
    mixInto(out, envelope(sweep(out.length, f0 * 1.9, f0, .022), .0006, tau));
    mixInto(out, membrane(out.length, f0 * 1.15, { tau: tau * .6, drop: .08, modes: 4, brightness: .35, seed }), .35);
    partial(out, f0 * 1.85, .12, .09);
    mixInto(out, envelope(biquad(noise(out.length, seed + 1), 'lowpass', 4200, .8), 0, .004), beater);
    return room(drive(out, 1.8), .4, space, seed);
  }
  function tom(f0, seconds, tau, seed) {
    const out = buffer(seconds);
    mixInto(out, membrane(out.length, f0, { tau, drop: .07, modes: 5, brightness: .5, seed }));
    mixInto(out, envelope(biquad(noise(out.length, seed + 1), 'bandpass', 2600, .7), 0, .003), .5);
    return room(drive(out, 1.4), .55, .2, seed);
  }
  function liveHat({ seconds = .2, tau = .035, tick = .6, low = 3800, seed = 61, space = .1 }) {
    const out = buffer(seconds);
    mixInto(out, cymbal(out.length, seed, { low, high: 12500, count: 90, tau: tau * 2.2, bright: 1.1 }));
    let sizzle = biquad(biquad(noise(out.length, seed + 1), 'highpass', 6000, .7), 'lowpass', 13000, .7);
    sizzle = biquad(sizzle, 'peaking', 8200, 1.1, 4);
    mixInto(out, sizzle, .45);
    envelope(out, .0004, tau);
    mixInto(out, envelope(biquad(noise(out.length, seed + 2), 'bandpass', 5200, 1.1), 0, .0018), tick);
    return room(biquad(out, 'highpass', 2600, .7), .3, space, seed);
  }
  function shaker(seconds, seed, character = 1) {
    const out = buffer(seconds), random = rng(seed), grains = new Float64Array(out.length);
    for (let i = 0; i < out.length; i++) {
      const t = i / SR, density = (t < .016 ? t / .016 : Math.exp(-(t - .016) / (.05 * character))) * .5 + (t > .07 ? Math.exp(-(t - .07) / .03) * .35 * (t < .07 + .2 ? 1 : 0) : 0);
      if (random() < density * .22) grains[i] = (random() * 2 - 1);
    }
    return biquad(biquad(grains, 'bandpass', 6800, .9), 'highpass', 3500, .7).map(x => x * 2.5);
  }
  function tambourine(seconds, seed) {
    const out = buffer(seconds), random = rng(seed);
    for (let jingle = 0; jingle < 10; jingle++) {
      const delay = random() * .012;
      for (let k = 0; k < 5; k++) partial(out, 4800 + random() * 7000, .25 * (.5 + random()), .05 + random() * .07, delay, random() * TAU);
    }
    mixInto(out, envelope(biquad(noise(out.length, seed + 1), 'highpass', 6000, .7), .001, .06), .5);
    partial(out, 290, .2, .05);
    return out;
  }
  function wood(frequencies, seconds, tau, seed, clickLevel = .4) {
    const out = buffer(seconds);
    frequencies.forEach(([f, amp], index) => partial(out, f, amp, tau / (1 + index * .6), 0, index));
    mixInto(out, envelope(biquad(noise(out.length, seed), 'bandpass', frequencies[0][0] * 1.4, 1), 0, .0015), clickLevel);
    return out;
  }
  function conga(f0, seconds, tau, seed, slap = .3) {
    const out = buffer(seconds);
    mixInto(out, membrane(out.length, f0, { tau, drop: .03, modes: 5, brightness: .4, seed }));
    mixInto(out, envelope(biquad(noise(out.length, seed + 1), 'bandpass', f0 * 6, 1.5), 0, .004), slap);
    return room(out, .4, .15, seed);
  }
  function reverseOf(input) { return Float64Array.from(input).reverse(); }

  // ---- Kits ----------------------------------------------------------------
  const HAT = 1, BASS = 2;
  const KITS = [
    {
      id: 'trap', name: 'Anharmonic Trap', description: 'Hard kicks, tuned 808s in G (49 Hz), crisp snares, stacked claps, rollable hats and trap percussion.',
      sounds: [
        ['kick-hard', 'Kick Hard', 'Kick', () => kick({ seconds: .45, start: 210, end: 54, glide: .024, tau: .16, click: .7, heat: 3.4, seed: 101 }), { gain: 1 }],
        ['snare-crisp', 'Snare Crisp', 'Snare', () => snare808({ body: 195, bodyTau: .055, noiseTau: .1, crack: 5600, seed: 111, heat: 1.6 }), { gain: .9 }],
        ['hat-closed', 'Hat Closed', 'Hat', () => hat808({ seconds: .14, tau: .026, seed: 121 }), { gain: .55, choke: HAT }],
        ['clap-stack', 'Clap Stack', 'Clap', () => clap({ seed: 131, centre: 1300, tau: .11, space: .3 }), { gain: .82 }],
        ['808-long', '808 Long', '808', () => eight08({ seconds: 2.6, note: 31, tau: 1.15, heat: 1.5, punch: 1.6, seed: 141 }), { gain: .95, choke: BASS, root_note: 31, mono: true }],
        ['snare-fat', 'Snare Fat', 'Snare', () => room(snare808({ seconds: .45, body: 168, bodyTau: .08, noiseTau: .16, crack: 3800, seed: 151, heat: 1.9 }), .5, .18, 151), { gain: .9 }],
        ['hat-tight', 'Hat Tight', 'Hat', () => hat808({ seconds: .08, tau: .012, scale: 1.08, centre: 11500, seed: 161 }), { gain: .5, choke: HAT }],
        ['hat-open', 'Hat Open', 'Hat', () => hat808({ seconds: .75, tau: .24, scale: .98, centre: 9500, seed: 171, open: true }), { gain: .48, choke: HAT }],
        ['kick-round', 'Kick Round', 'Kick', () => kick({ seconds: .55, start: 130, end: 50, glide: .04, tau: .24, click: .25, heat: 1.8, seed: 181 }), { gain: 1 }],
        ['808-punch', '808 Punch', '808', () => eight08({ seconds: 1.1, note: 31, tau: .45, heat: 3.2, punch: 2.2, tone: 5200, seed: 191 }), { gain: .95, choke: BASS, root_note: 31, mono: true }],
        ['808-dirty', '808 Dirty', '808', () => eight08({ seconds: 1.8, note: 31, tau: .85, heat: 7, punch: 1.8, tone: 2600, seed: 201 }), { gain: .88, choke: BASS, root_note: 31, mono: true }],
        ['snap', 'Snap', 'Clap', () => { const out = buffer(.22); mixInto(out, envelope(biquad(noise(out.length, 211), 'bandpass', 2300, 2.2), .0004, .022)); partial(out, 2900, .25, .012); return room(out, .3, .22, 211); }, { gain: .75 }],
        ['rim', 'Rim', 'Perc', () => wood([[1690, .9], [470, .45]], .12, .018, 221, .5), { gain: .7 }],
        ['perc-wood', 'Perc Wood', 'Perc', () => wood([[2480, .9], [3900, .25]], .2, .06, 231, .3), { gain: .62 }],
        ['perc-bell', 'Perc Bell', 'Perc', () => { const out = buffer(.6); const tone = mixInto(metal(out.length, 1).fill(0), (() => { const b = new Float64Array(out.length); for (const f of [540, 800]) { let p = 0; for (let i = 0; i < b.length; i++) { p += f / SR; b[i] += (p % 1) < .5 ? .5 : -.5; } } return b; })()); mixInto(out, biquad(tone, 'bandpass', 800, 1.6)); for (let i = 0; i < out.length; i++) { const t = i / SR; out[i] *= .6 * Math.exp(-t / .015) + .4 * Math.exp(-t / .16); } return out; }, { gain: .55 }],
        ['crash', 'Crash', 'Cymbal', () => { const out = buffer(2.6); mixInto(out, biquad(mixInto(metal(out.length, .93), noise(out.length, 251), .6), 'highpass', 4200, .7)); return envelope(out, .002, .9); }, { gain: .5 }]
      ]
    },
    {
      id: 'live', name: 'Anharmonic Live', description: 'Acoustic-style kit: modeled kick, snare with wires, rimshot, cross-stick, hats, ride, crash, three toms, shaker and tambourine.',
      sounds: [
        ['kick', 'Kick', 'Kick', () => liveKick({ seed: 301 }), { gain: 1 }],
        ['snare', 'Snare', 'Snare', () => liveSnare({ seed: 311 }), { gain: .9 }],
        ['hat-closed', 'Hat Closed', 'Hat', () => liveHat({ seed: 321 }), { gain: .55, choke: HAT }],
        ['cross-stick', 'Cross-stick', 'Snare', () => room(wood([[1120, .7], [1930, .45], [2840, .3], [410, .5]], .25, .05, 331, .7), .4, .2, 331), { gain: .7 }],
        ['kick-soft', 'Kick Soft', 'Kick', () => liveKick({ seconds: .5, f0: 62, tau: .17, beater: .25, seed: 341, space: .08 }), { gain: .95 }],
        ['snare-rimshot', 'Snare Rimshot', 'Snare', () => liveSnare({ f0: 215, tau: .2, rim: 1, seed: 351, space: .28 }), { gain: .88 }],
        ['hat-pedal', 'Hat Pedal', 'Hat', () => liveHat({ seconds: .12, tau: .018, tick: .2, low: 2800, seed: 361 }), { gain: .5, choke: HAT }],
        ['hat-open', 'Hat Open', 'Hat', () => liveHat({ seconds: .9, tau: .32, tick: .5, seed: 371, space: .15 }), { gain: .5, choke: HAT }],
        ['tom-high', 'Tom High', 'Tom', () => tom(185, .8, .38, 381), { gain: .85 }],
        ['tom-mid', 'Tom Mid', 'Tom', () => tom(132, .95, .45, 391), { gain: .85 }],
        ['tom-floor', 'Tom Floor', 'Tom', () => tom(88, 1.2, .6, 401), { gain: .9 }],
        ['ride', 'Ride', 'Cymbal', () => { const out = buffer(3); mixInto(out, cymbal(out.length, 411, { low: 300, high: 12000, count: 110, tau: 1.6 })); partial(out, 3900, .25, .05); mixInto(out, envelope(biquad(noise(out.length, 412), 'bandpass', 4500, 1.3), 0, .003), .5); return biquad(out, 'highpass', 250, .7); }, { gain: .5 }],
        ['ride-bell', 'Ride Bell', 'Cymbal', () => { const out = buffer(2.6); [[712, .5], [1583, .45], [2648, .35], [3719, .25], [5133, .15]].forEach(([f, a], i) => partial(out, f, a, 1.6 / (1 + i * .35), 0, i)); mixInto(out, cymbal(out.length, 421, { low: 500, high: 11000, count: 50, tau: 1.2 }), .5); return out; }, { gain: .48 }],
        ['crash', 'Crash', 'Cymbal', () => { const out = buffer(3.4); mixInto(out, cymbal(out.length, 431, { low: 280, high: 15500, count: 140, tau: 1.9, bright: 1.3 })); mixInto(out, envelope(biquad(noise(out.length, 432), 'highpass', 3200, .7), .006, 1.1), .55); return biquad(out, 'highpass', 200, .7); }, { gain: .45 }],
        ['shaker', 'Shaker', 'Perc', () => shaker(.32, 441), { gain: .55 }],
        ['tambourine', 'Tambourine', 'Perc', () => tambourine(.5, 451), { gain: .5 }]
      ]
    },
    {
      id: 'boombap', name: 'Anharmonic Boom Bap', description: 'Dusty sampler-era drums: knocking kicks, cracking snares, room claps, loose hats, congas, bongo and cowbell.',
      sounds: [
        ['kick-dusty', 'Kick Dusty', 'Kick', () => dust(liveKick({ seconds: .55, f0: 58, tau: .2, beater: .7, seed: 501, space: .1 })), { gain: 1 }],
        ['snare-crack', 'Snare Crack', 'Snare', () => dust(liveSnare({ f0: 235, tau: .13, rim: .6, seed: 511, space: .3 })), { gain: .9 }],
        ['hat-closed', 'Hat Closed', 'Hat', () => dust(liveHat({ seconds: .16, tau: .03, seed: 521 }), { tone: 8500 }), { gain: .55, choke: HAT }],
        ['clap-room', 'Clap Room', 'Clap', () => dust(clap({ seconds: .65, seed: 531, centre: 1150, tau: .14, space: .6 })), { gain: .8 }],
        ['kick-knock', 'Kick Knock', 'Kick', () => dust(kick({ seconds: .4, start: 160, end: 72, glide: .02, tau: .11, click: .8, heat: 2.4, seed: 541 })), { gain: 1 }],
        ['snare-dusty', 'Snare Dusty', 'Snare', () => dust(room(liveSnare({ f0: 190, tau: .18, seed: 551, space: .2 }), .6, .25, 551), { bits: 12, rate: 26040, tone: 9500, heat: 1.4 }), { gain: .9 }],
        ['hat-loose', 'Hat Loose', 'Hat', () => dust(liveHat({ seconds: .4, tau: .11, seed: 561 }), { tone: 9000 }), { gain: .5, choke: HAT }],
        ['hat-open', 'Hat Open', 'Hat', () => dust(liveHat({ seconds: .8, tau: .28, seed: 571 }), { tone: 9000 }), { gain: .48, choke: HAT }],
        ['rim-click', 'Rim Click', 'Perc', () => dust(wood([[1240, .7], [2210, .4], [420, .4]], .2, .04, 581, .7)), { gain: .7 }],
        ['shaker', 'Shaker', 'Perc', () => dust(shaker(.3, 591, 1.2), { tone: 10000 }), { gain: .55 }],
        ['tambourine', 'Tambourine', 'Perc', () => dust(tambourine(.45, 601), { tone: 10500 }), { gain: .5 }],
        ['cowbell', 'Cowbell', 'Perc', () => dust(room(wood([[562, .7], [845, .6], [1330, .3], [1790, .2]], .45, .2, 611, .3), .3, .15, 611)), { gain: .55 }],
        ['conga-high', 'Conga High', 'Perc', () => dust(conga(318, .5, .22, 621, .4)), { gain: .75 }],
        ['conga-low', 'Conga Low', 'Perc', () => dust(conga(212, .6, .28, 631, .3)), { gain: .78 }],
        ['bongo', 'Bongo', 'Perc', () => dust(conga(452, .35, .13, 641, .5)), { gain: .7 }],
        ['reverse-cymbal', 'Reverse Cymbal', 'FX', () => { const out = buffer(1.6); mixInto(out, cymbal(out.length, 651, { low: 400, high: 13000, count: 90, tau: .9 })); mixInto(out, envelope(biquad(noise(out.length, 652), 'highpass', 4000, .7), 0, .6), .4); return dust(reverseOf(out), { tone: 9500 }); }, { gain: .45 }]
      ]
    }
  ].map(kit => ({ ...kit, sounds: kit.sounds.map(([id, name, category, recipe, settings], index) => ({ id, name, category, recipe, pad: index, ...settings })) }));

  const cache = new Map();
  function render(kitId, soundId) {
    const key = kitId + '/' + soundId;
    if (cache.has(key)) return cache.get(key);
    const kit = KITS.find(item => item.id === kitId), sound = kit?.sounds.find(item => item.id === soundId);
    if (!sound) throw new Error('Unknown factory sound: ' + key);
    const data = finish(sound.recipe());
    const result = { sampleRate: SR, channels: [data] };
    cache.set(key, result);
    return result;
  }
  const mediaId = (kitId, soundId) => 'factory-' + kitId + '-' + soundId;
  function parseMediaId(id) {
    if (typeof id !== 'string' || !id.startsWith('factory-')) return null;
    for (const kit of KITS) for (const sound of kit.sounds) if (mediaId(kit.id, sound.id) === id) return { kit, sound };
    return null;
  }
  // 16-bit PCM WAV, mono or stereo, from Float32 channel arrays.
  function wav({ sampleRate, channels }) {
    const frames = channels[0].length, count = channels.length, bytes = new ArrayBuffer(44 + frames * count * 2), view = new DataView(bytes);
    const text = (offset, value) => { for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i)); };
    text(0, 'RIFF'); view.setUint32(4, bytes.byteLength - 8, true); text(8, 'WAVE'); text(12, 'fmt ');
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, count, true); view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * count * 2, true); view.setUint16(32, count * 2, true); view.setUint16(34, 16, true); text(36, 'data'); view.setUint32(40, frames * count * 2, true);
    let offset = 44;
    for (let i = 0; i < frames; i++) for (let c = 0; c < count; c++) { const x = Math.max(-1, Math.min(1, channels[c][i])); view.setInt16(offset, Math.round(x < 0 ? x * 32768 : x * 32767), true); offset += 2; }
    return bytes;
  }
  const publicKits = KITS.map(({ sounds, ...kit }) => ({ ...kit, sounds: sounds.map(({ recipe, ...sound }) => ({ ...sound, mediaId: mediaId(kit.id, sound.id) })) }));
  const api = Object.freeze({ SAMPLE_RATE: SR, KITS: publicKits, render, mediaId, parseMediaId: id => { const found = parseMediaId(id); return found && { kit: publicKits.find(kit => kit.id === found.kit.id), sound: publicKits.find(kit => kit.id === found.kit.id).sounds.find(sound => sound.id === found.sound.id) }; }, wav });
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.AnharmonicKits = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);

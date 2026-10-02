const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const dsp = require(path.join(__dirname, '../../website/app/instrument-dsp.js'));

const repo = path.join(__dirname, '../..');
const compiler = ['c++', 'g++', 'clang++'].find(name => { try { execFileSync(name, ['--version'], { stdio: 'ignore' }); return true; } catch { return false; } });

// Same scripts as tests/web/reference/prism_engine_reference.cpp.
const SCENARIOS = {
  0: () => ({}),
  1: () => ({ osc1: 3, osc2: 2, pulse: .27, cutoff: 900, resonance: .9, drive: .7, lfoRate: 6, lfoPitch: 25, lfoFilter: .5, attack: .002, decay: .05, sustain: .3, release: .03, octave: -1, detune: 17, sub: .6, noise: .2 }),
  2: () => ({ osc1: 1, osc2: 0, cutoff: 16000, spread: 1, attack: .3, release: .2, mix: .8 })
};
function renderWeb(scenario) {
  const engine = new dsp.SynthEngine(48000, 32);
  Object.assign(engine.patch, SCENARIOS[scenario]());
  const total = 12000, left = new Float32Array(total), right = new Float32Array(total);
  const events = { 0: () => engine.on(60, 1, .8), 2000: () => engine.on(67, 1, .6), 5000: () => engine.off(60, 1), 7000: () => { engine.on(55, 1, 1); engine.off(67, 1); } };
  // A different block partition from the C harness: the renderer must be partition invariant.
  const marks = [0, 2000, 5000, 7000, total];
  for (let index = 0; index < marks.length - 1; index++) {
    events[marks[index]]();
    let at = marks[index];
    while (at < marks[index + 1]) { const n = Math.min(211, marks[index + 1] - at); engine.render(left, right, n, at); at += n; }
  }
  return { left, right };
}

test('web Native/Prism voice engine matches the desktop C++ engine sample for sample', { skip: !compiler && 'no C++ compiler available' }, () => {
  const binary = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'anharmonic-ref-')), 'prism_ref');
  execFileSync(compiler, ['-O2', '-std=c++17', '-o', binary, path.join(repo, 'tests/web/reference/prism_engine_reference.cpp'), path.join(repo, 'mpclab/native/synth.cpp')]);
  for (const scenario of [0, 1, 2]) {
    const bytes = execFileSync(binary, [String(scenario)], { maxBuffer: 1 << 24 });
    const reference = new Float32Array(bytes.buffer, bytes.byteOffset, bytes.length / 4);
    const web = renderWeb(scenario);
    let worst = 0, peak = 0;
    for (let i = 0; i < web.left.length; i++) {
      worst = Math.max(worst, Math.abs(web.left[i] - reference[i * 2]), Math.abs(web.right[i] - reference[i * 2 + 1]));
      peak = Math.max(peak, Math.abs(reference[i * 2]));
    }
    assert.ok(peak > .01, `scenario ${scenario} reference is audible`);
    assert.ok(worst < 1e-6, `scenario ${scenario} differs by ${worst}`);
  }
});

test('Prism normalized parameters round-trip with the desktop skew rules', () => {
  for (const spec of dsp.PRISM_SPECS) {
    const values = [spec.low, spec.initial, spec.high];
    for (const value of values) {
      const back = dsp.prismPlain(spec, dsp.prismNormalize(spec, value));
      assert.ok(Math.abs(back - value) < (spec.high - spec.low) * 1e-9 + (spec.step ? .5 : 0), `${spec.id} ${value} -> ${back}`);
    }
  }
  const cutoff = dsp.PRISM_SPECS[dsp.PRISM_INDEX.cutoff];
  assert.ok(Math.abs(dsp.prismNormalize(cutoff, 2400) - ((2400 - 50) / (18000 - 50)) ** .3) < 1e-12);
  const values = dsp.prismValuesFromParameters({ 12: dsp.prismNormalize(cutoff, 900) });
  assert.ok(Math.abs(values[12] - 900) < 1e-6);
  assert.equal(values[0], 0);
  assert.equal(values[1], 3, 'missing parameters take Prism defaults');
});

test('loading a Prism sound resets motion and effects, then applies the sound', () => {
  const start = dsp.prismDefaults(); start[31] = 1; start[70] = .5;
  const loaded = dsp.prismLoadSound(start, { patch: { osc1: 'sine', osc2: 'triangle', cutoff: 777, volume: .5 }, arp: { enabled: true, rate_beats: .5, mode: 'down', octaves: 2, gate: .4 }, effects: { chorus: .3, b_cutoff: 3000, layer_mix: .4 } });
  assert.equal(loaded[0], 1); assert.equal(loaded[1], 2); assert.equal(loaded[12], 777);
  assert.equal(loaded[21], 1); assert.equal(loaded[22], 3); assert.equal(loaded[23], 1); assert.equal(loaded[24], 2);
  assert.equal(loaded[31], .3); assert.equal(loaded[44], 3000); assert.equal(loaded[53], .4); assert.equal(loaded[70], 0);
  const layered = dsp.prismLoadLayer(dsp.prismDefaults(), { patch: { osc1: 'square' } }, true);
  assert.equal(layered[32], 3); assert.equal(layered[53], .5);
});

test('Prism processor renders audible, bounded stereo through arp and effects', () => {
  const values = dsp.prismDefaults();
  values[21] = 1; values[29] = .3; values[31] = .4; values[26] = .3;
  const prism = new dsp.PrismProcessor(48000, values); prism.tempo = 120; prism.readPatch();
  prism.noteOn(60, .9); prism.noteOn(64, .9);
  const left = new Float32Array(128), right = new Float32Array(128);
  let peak = 0, onsets = 0, wasQuiet = true;
  for (let block = 0; block < 375; block++) {
    left.fill(0); right.fill(0); prism.readPatch(); prism.render(left, right, 128); prism.effects(left, right, 128);
    for (let i = 0; i < 128; i++) { assert.ok(Number.isFinite(left[i]) && Math.abs(left[i]) <= 1); peak = Math.max(peak, Math.abs(left[i])); }
  }
  assert.ok(peak > .02, 'arp output is audible');
  assert.ok(prism.arpIndex >= 6, `arp advanced ${prism.arpIndex} steps in one second at 1/16`);
  void onsets; void wasQuiet;
});

test('native instrument maps desktop SynthPatch fields and releases by note identity', () => {
  const native = new dsp.NativeInstrument(48000, { osc1: 'square', osc2: 'sine', cutoff: 30000, pulse_width: .02, release: .01 });
  assert.equal(native.engine.patch.osc1, 3); assert.equal(native.engine.patch.cutoff, 20000); assert.equal(native.engine.patch.pulse, .1);
  native.noteOn(60, .8, 1, 'a'); native.noteOn(60, .8, 1, 'b');
  const l = new Float32Array(4800), r = new Float32Array(4800);
  native.render(l, r, 2400);
  native.release('a', 0, 0);
  native.render(l, r, 2400, 2400);
  const live = native.engine.voices.filter(voice => voice.note >= 0).map(voice => voice.id);
  assert.deepEqual(live, ['b']);
});

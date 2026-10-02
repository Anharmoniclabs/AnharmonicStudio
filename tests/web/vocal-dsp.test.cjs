const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const vocal = require(path.join(__dirname, '../../website/app/vocal-dsp.js'));

const SR = 48000;
const root = path.join(__dirname, '../..');
// A breathy, slightly flat sung phrase: harmonics, vibrato, gaps and a held note.
function phrase(seconds = 2.4) {
  const data = new Float32Array(Math.round(SR * seconds)); let phase = 0, seed = 7;
  const notes = [[0, .55, 57.6], [.6, 1.2, 60.3], [1.3, 2.3, 63.7]];
  for (let i = 0; i < data.length; i++) {
    const t = i / SR, note = notes.find(([start, end]) => t >= start && t < end);
    seed = (Math.imul(seed, 1103515245) + 12345) >>> 0; const noise = (seed / 4294967296 - .5) * .004;
    if (!note) { data[i] = noise; continue; }
    const midi = note[2] + .25 * Math.sin(2 * Math.PI * 5.5 * t), hz = 440 * Math.pow(2, (midi - 69) / 12);
    phase += 2 * Math.PI * hz / SR;
    const envelope = Math.min(1, (t - note[0]) * 30, (note[1] - t) * 30);
    data[i] = envelope * (.3 * Math.sin(phase) + .12 * Math.sin(2 * phase) + .05 * Math.sin(3 * phase)) + noise;
  }
  return data;
}
// Runs the desktop Python implementation on the same samples.
function python(data, settings) {
  const script = `
import sys, json, numpy as np
from mpclab.model import VocalSettings
from mpclab.vocal import render_autotune, detect_key
raw = np.frombuffer(sys.stdin.buffer.read(), dtype=np.float32)
settings = VocalSettings(**json.loads(sys.argv[1]))
out, analysis = render_autotune(raw, settings, 48000)
key = detect_key(analysis)
sys.stdout.buffer.write(json.dumps({"key": key[:2], "frames": len(analysis.times)}).encode() + b"\\n")
sys.stdout.buffer.write(np.ascontiguousarray(out, dtype=np.float32).tobytes())
sys.stdout.buffer.write(np.nan_to_num(analysis.target_midi, nan=-1).astype(np.float32).tobytes())
`;
  const bytes = execFileSync('python3', ['-c', script, JSON.stringify(settings)], { cwd: root, input: Buffer.from(data.buffer), maxBuffer: 1 << 28 });
  const line = bytes.indexOf(10), header = JSON.parse(bytes.subarray(0, line).toString());
  const body = bytes.subarray(line + 1), floats = new Float32Array(body.buffer.slice(body.byteOffset, body.byteOffset + body.length));
  return { ...header, left: floats.subarray(0, data.length * 2).filter((_, i) => i % 2 === 0), right: floats.subarray(0, data.length * 2).filter((_, i) => i % 2 === 1), target: floats.subarray(data.length * 2) };
}
const worst = (a, b) => { let max = 0; for (let i = 0; i < a.length; i++) max = Math.max(max, Math.abs(a[i] - b[i])); return max; };

let havePython = true;
try { execFileSync('python3', ['-c', 'import numpy, mpclab.vocal'], { cwd: root, stdio: 'ignore' }); } catch { havePython = false; }
if (!havePython && process.env.REQUIRE_DESKTOP_PARITY) throw new Error('Desktop parity was required but python3 with numpy, soundfile and mpclab is unavailable.');

test('pitch tracking finds the sung notes and the key', () => {
  const analysis = vocal.analyzePitch([phrase()], { scale: 'major', key: 'C' }, SR);
  const at = time => { const index = analysis.times.findIndex(value => value >= time); return [analysis.midi[index], analysis.target[index]]; };
  const [held, target] = at(1.8);
  assert.ok(Math.abs(held - 63.7) < .4, 'detected ' + held);
  assert.equal(target, 64, 'E is the nearest C-major note');
  assert.ok(Number.isNaN(at(1.25)[0]), 'gaps stay unvoiced');
  assert.equal(vocal.noteName(64), 'E4');
});

test('the web autotune matches the desktop render', { skip: !havePython && 'python3 with numpy and mpclab is unavailable' }, () => {
  const data = phrase();
  for (const settings of [{ key: 'C', scale: 'major' }, { key: 'A', scale: 'minor', retune_ms: 0, humanize: 0, transpose: 2, compression: .8, presence_db: 4, output_db: 3 }, { enabled: false, highpass_hz: 20, deesser: 0, presence_db: 0 }]) {
    const desktop = python(data, settings), web = vocal.renderAutotune([data], settings, SR);
    const key = vocal.detectKey(web.analysis);
    assert.deepEqual([key.key, key.scale], desktop.key);
    assert.equal(web.analysis.times.length, desktop.frames);
    const target = Array.from(web.analysis.target, value => Number.isNaN(value) ? -1 : value);
    assert.deepEqual(target, Array.from(desktop.target), 'same target notes');
    const left = worst(web.channels[0], desktop.left), right = worst(web.channels[1], desktop.right);
    assert.ok(left < 5e-5 && right < 5e-5, JSON.stringify({ settings, left, right }));
  }
});

test('stereo takes keep their layout and peaks never pass 0.98', () => {
  const data = phrase(1), loud = Float32Array.from(data, value => value * 3);
  const result = vocal.renderAutotune([loud, Float32Array.from(data, value => -value)], { output_db: 12 }, SR);
  assert.equal(result.channels.length, 2);
  let peak = 0; for (const channel of result.channels) for (const value of channel) { assert.ok(Number.isFinite(value)); peak = Math.max(peak, Math.abs(value)); }
  assert.ok(peak <= .9800001 && peak > .9, 'peak ' + peak);
  assert.throws(() => vocal.renderAutotune([new Float32Array(0)], {}, SR), /empty/);
});

test('hard tune corrects a held note longer than a grain', () => {
  const sr = 48000, flat = 220 * Math.pow(2, -.35 / 12);
  const data = Float32Array.from({ length: sr * 3 }, (_, i) => .25 * Math.sin(2 * Math.PI * flat * i / sr) + .12 * Math.sin(4 * Math.PI * flat * i / sr));
  const out = vocal.renderAutotune([data], { key: 'A', scale: 'minor', retune_ms: 0, humanize: 0 }, sr).channels[0].subarray(sr / 2, sr * 5 / 2);
  let first = null, last = null, count = 0;
  for (let i = 1; i < out.length; i++) if (out[i - 1] < 0 && out[i] >= 0) { const t = i - 1 + out[i - 1] / (out[i - 1] - out[i]); if (first === null) first = t; else count++; last = t; }
  const hz = count * sr / (last - first);
  assert.ok(Math.abs(1200 * Math.log2(hz / 220)) < 2, 'tuned to ' + hz.toFixed(2) + ' Hz');
});

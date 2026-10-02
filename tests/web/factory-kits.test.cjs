const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const kits = require(path.join(__dirname, '../../website/app/factory-kits.js'));
const bank = require(path.join(__dirname, '../../website/app/instrument-bank.js'));

// Dominant frequency by a direct DFT over a coarse grid; enough to verify tuning.
function dominant(data, sampleRate, low, high, step = 1) {
  let best = low, bestPower = 0;
  const length = Math.min(data.length, sampleRate);
  for (let f = low; f <= high; f += step) {
    let re = 0, im = 0; const w = 2 * Math.PI * f / sampleRate;
    for (let i = 0; i < length; i += 2) { re += data[i] * Math.cos(w * i); im += data[i] * Math.sin(w * i); }
    const power = re * re + im * im; if (power > bestPower) { bestPower = power; best = f; }
  }
  return best;
}
const highShare = data => { let total = 0, high = 0, previous = 0; for (const value of data) { const difference = value - previous; previous = value; total += value * value; high += difference * difference; } return high / Math.max(1e-12, total); };

test('three official kits fill a 16-pad bank each with unique, stable IDs', () => {
  assert.deepEqual(kits.KITS.map(kit => kit.id), ['trap', 'live', 'boombap']);
  const ids = new Set();
  for (const kit of kits.KITS) {
    assert.equal(kit.sounds.length, 16);
    assert.deepEqual(kit.sounds.map(sound => sound.pad), Array.from({ length: 16 }, (_, index) => index));
    for (const sound of kit.sounds) {
      assert.match(sound.mediaId, /^factory-[a-z]+-[a-z0-9-]+$/); assert.ok(!ids.has(sound.mediaId)); ids.add(sound.mediaId);
      assert.deepEqual(kits.parseMediaId(sound.mediaId).sound.name, sound.name);
    }
  }
  assert.equal(kits.parseMediaId('sample-123'), null);
  const trap = kits.KITS[0].sounds.map(sound => sound.category);
  for (const category of ['Kick', '808', 'Snare', 'Clap', 'Hat', 'Perc', 'Cymbal']) assert.ok(trap.includes(category), category);
});

test('every one-shot renders finite, bounded, faded and deterministic audio', () => {
  for (const kit of kits.KITS) for (const sound of kit.sounds) {
    const { channels, sampleRate } = kits.render(kit.id, sound.id), data = channels[0];
    assert.equal(sampleRate, 48000);
    assert.ok(data.length > 1000 && data.length < 48000 * 4, sound.name + ' length');
    let peak = 0; for (const value of data) { assert.ok(Number.isFinite(value)); peak = Math.max(peak, Math.abs(value)); }
    assert.ok(peak > .85 && peak <= .8915, sound.name + ' peak ' + peak);
    assert.ok(Math.abs(data[data.length - 1]) < .01, sound.name + ' ends silently');
  }
  // A fresh process regenerates identical audio.
  const script = "const k=require(" + JSON.stringify(path.join(__dirname, '../../website/app/factory-kits.js')) + ");const d=k.render('live','snare').channels[0];let h=0;for(const v of d)h=(h*31+Math.round(v*1e6))|0;console.log(h,d.length)";
  const first = execFileSync(process.execPath, ['-e', script]).toString(), second = execFileSync(process.execPath, ['-e', script]).toString();
  assert.equal(first, second);
});

test('808s are tuned to G1 and sit in the trap sub range; hats are bright', () => {
  for (const id of ['808-long', '808-punch', '808-dirty']) {
    const sound = kits.KITS[0].sounds.find(item => item.id === id);
    assert.equal(sound.root_note, 31); assert.equal(sound.choke, 2); assert.equal(sound.mono, true);
    const { channels, sampleRate } = kits.render('trap', id);
    const f = dominant(channels[0].subarray(Math.round(sampleRate * .08)), sampleRate, 30, 120);
    assert.ok(Math.abs(f - 49) <= 2, id + ' fundamental ' + f + ' Hz');
  }
  for (const kit of kits.KITS) for (const sound of kit.sounds.filter(item => item.category === 'Hat')) {
    assert.equal(sound.choke, 1, sound.name + ' chokes with the other hats');
    assert.ok(highShare(kits.render(kit.id, sound.id).channels[0]) > .7, kit.id + ' ' + sound.name + ' is bright');
  }
  const kick = kits.render('trap', 'kick-hard');
  assert.ok(dominant(kick.channels[0], kick.sampleRate, 30, 150) < 70, 'kick energy is in the low end');
});

test('WAV encoding produces a valid 16-bit PCM file', () => {
  const bytes = new DataView(kits.wav(kits.render('boombap', 'bongo')));
  const text = offset => String.fromCharCode(...[0, 1, 2, 3].map(index => bytes.getUint8(offset + index)));
  assert.equal(text(0), 'RIFF'); assert.equal(text(8), 'WAVE'); assert.equal(bytes.getUint16(22, true), 1);
  assert.equal(bytes.getUint32(24, true), 48000); assert.equal(bytes.getUint16(34, true), 16);
});

test('the web instrument bank mirrors the desktop Native and Prism banks', () => {
  assert.equal(bank.tones.length, 54);
  assert.equal(bank.performances.length, 120);
  assert.ok(bank.tones.every(item => item.patch.osc1 && Number.isFinite(item.patch.cutoff)));
  assert.ok(bank.performances.every(item => item.effects && typeof item.effects === 'object'));
  const out = execFileSync('python3', [path.join(__dirname, '../../scripts/build_web_instruments.py'), '--check']).toString();
  assert.match(out, /current/);
});

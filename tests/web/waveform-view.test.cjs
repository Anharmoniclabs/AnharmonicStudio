const test = require('node:test');
const assert = require('node:assert/strict');
const { envelope } = require('../../website/app/waveform-view.js');
function buffer(values, sampleRate = 1) {
  return { sampleRate, duration: values[0].length / sampleRate, length: values[0].length,
    numberOfChannels: values.length, getChannelData: index => values[index] };
}
test('source offset and trim end match nonloop audio duration without stretching', () => {
  const audio = buffer([[0, .2, .4, .8, 1, 0]]);
  const view = envelope(audio, { offset: 1, sourceLength: 2, duration: 4 }, 4);
  assert.deepEqual([...view.high].map(x => +x.toFixed(2)), [.2, .4, 0, 0]);
});
test('loop and reverse preserve the source trim and repeated phase', () => {
  const audio = buffer([[0, .2, .4, .8, 1, 0]]);
  const view = envelope(audio, { offset: 1, sourceLength: 2, duration: 4, reverse: true, loop: true }, 4);
  assert.deepEqual([...view.high].map(x => +x.toFixed(2)), [.4, .2, .4, .2]);
  const zoomed = envelope(audio, { offset: 1, sourceLength: 2, duration: 4, loop: true, start: .75, end: 1 }, 1);
  assert.ok(Math.abs(zoomed.high[0] - .4) < 1e-6);
});
test('coarse repeated loops include all extrema without iterating each repetition', () => {
  const audio = buffer([[.2, -.8]]);
  const view = envelope(audio, { sourceLength: 2, duration: 1e9, loop: true }, 1);
  assert.ok(Math.abs(view.low[0] + .8) < 1e-6);
  assert.ok(Math.abs(view.high[0] - .2) < 1e-6);
});
test('both channels remain visible even when they would cancel in a mono sum', () => {
  const view = envelope(buffer([[.7], [-.7]]), {}, 1);
  assert.ok(view.low[0] < -.69 && view.high[0] > .69);
});
test('cached peak pyramid gives exact extrema and avoids rescanning full PCM on redraw', () => {
  let reads = 0;
  const values = Array.from({ length: 65536 }, (_, i) => Math.sin(i * .017));
  const samples = new Proxy(values, { get(target, key) { if (/^\d+$/.test(String(key))) reads++; return target[key]; } });
  const audio = buffer([samples], 16384);
  envelope(audio, {}, 16);
  reads = 0;
  const options = { start: .123, end: .876 };
  const view = envelope(audio, options, 16);
  assert.ok(reads < 8192, 'redraw should use cached block extrema');
  for (let x = 0; x < 16; x++) {
    const from = Math.floor((options.start + (options.end - options.start) * x / 16) * values.length);
    const to = Math.ceil((options.start + (options.end - options.start) * (x + 1) / 16) * values.length);
    assert.ok(Math.abs(view.low[x] - Math.min(0, ...values.slice(from, to))) < 1e-6);
    assert.ok(Math.abs(view.high[x] - Math.max(0, ...values.slice(from, to))) < 1e-6);
  }
});
test('single-frame views and invalid empty ranges stay bounded', () => {
  const audio = buffer([[0, .5, 0, -.5]], 48000);
  assert.equal(envelope(audio, { start: .25, end: .5 }, 1).high[0], .5);
  assert.deepEqual([...envelope(audio, { start: 1, end: 1 }, 2).high], [0, 0]);
  assert.equal(envelope(audio, {}, 1e9).high.length, 4096);
});

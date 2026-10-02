const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { PCMCapture, roundTripLatency } = require(path.join(__dirname, '../../website/app/recorder.js'));

const block = (values, channels = 1) => Array.from({ length: channels }, (_, channel) => Float32Array.from(values, value => value * (channel + 1)));

test('capture keeps uncompressed samples in order and records the first block clock time', () => {
  const capture = new PCMCapture(48000);
  capture.push(block([.1, .2, .3]), 1.25);
  capture.push(block([.4, .5]), 1.5);
  assert.equal(capture.startTime, 1.25);
  assert.equal(capture.frames, 5);
  assert.deepEqual([...capture.channelData()[0]].map(value => Math.round(value * 10) / 10), [.1, .2, .3, .4, .5]);
  assert.equal(capture.duration, 5 / 48000);
  assert.ok(Math.abs(capture.peak - .5) < 1e-6);
});

test('capture trims a latency head across block boundaries', () => {
  const capture = new PCMCapture(4);
  capture.push(block([1, 2, 3]), 0);
  capture.push(block([4, 5, 6]), .75);
  assert.deepEqual([...capture.channelData(1)[0]], [5, 6]);
  assert.deepEqual([...capture.channelData(.5)[0]], [3, 4, 5, 6]);
  assert.equal(capture.channelData(10)[0].length, 0);
});

test('stereo input stays stereo; extra channels are ignored', () => {
  const capture = new PCMCapture(48000);
  capture.push(block([.1, .2], 3), 0);
  const data = capture.channelData();
  assert.equal(data.length, 2);
  assert.ok(Math.abs(data[1][1] - .4) < 1e-6);
});

test('capture stops accepting audio at its maximum length', () => {
  const capture = new PCMCapture(10, { maxFrames: 4 });
  assert.equal(capture.push(block([1, 2, 3]), 0), true);
  capture.push(block([4, 5, 6]), .3);
  assert.equal(capture.full, true);
  assert.equal(capture.frames, 4);
  assert.equal(capture.push(block([7]), .6), false);
  assert.deepEqual([...capture.channelData()[0]], [1, 2, 3, 4]);
});

test('empty pushes do not start a take', () => {
  const capture = new PCMCapture(48000);
  assert.equal(capture.push([], 1), false);
  assert.equal(capture.push([new Float32Array(0)], 1), false);
  assert.equal(capture.startTime, null);
});

test('round-trip latency adds reported input and output buffering, never negative', () => {
  assert.equal(roundTripLatency({ baseLatency: .01, outputLatency: .02 }), .03);
  assert.equal(roundTripLatency({ baseLatency: undefined, outputLatency: NaN }), 0);
  assert.equal(roundTripLatency(null), 0);
});

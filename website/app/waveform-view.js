/* Source waveforms: cached extrema, bounded raster size, and audio-clip timing. */
(function (root) {
  'use strict';
  const cache = new WeakMap(), BLOCK = 128;
  const finite = (value, fallback) => Number.isFinite(value) ? value : fallback;
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  function peaks(buffer) {
    if (cache.has(buffer)) return cache.get(buffer);
    const channels = Array.from({ length: buffer.numberOfChannels }, (_, index) => buffer.getChannelData(index));
    const count = Math.ceil(buffer.length / BLOCK), low = new Float32Array(count), high = new Float32Array(count);
    for (let block = 0; block < count; block++) {
      let min = 0, max = 0;
      for (const data of channels) for (let frame = block * BLOCK; frame < Math.min(buffer.length, (block + 1) * BLOCK); frame++) {
        const value = finite(data[frame], 0); min = Math.min(min, value); max = Math.max(max, value);
      }
      low[block] = min; high[block] = max;
    }
    const levels = [{ low, high }];
    while (levels.at(-1).low.length > 1) {
      const previous = levels.at(-1), size = Math.ceil(previous.low.length / 2);
      const next = { low: new Float32Array(size), high: new Float32Array(size) };
      for (let index = 0; index < size; index++) {
        next.low[index] = Math.min(previous.low[index * 2], previous.low[index * 2 + 1] ?? 0);
        next.high[index] = Math.max(previous.high[index * 2], previous.high[index * 2 + 1] ?? 0);
      }
      levels.push(next);
    }
    const result = { channels, levels }; cache.set(buffer, result); return result;
  }
  function range(buffer, cached, from, to) {
    let first = clamp(Math.floor(from), 0, buffer.length), last = clamp(Math.ceil(to), first, buffer.length), min = 0, max = 0;
    const sample = frame => { for (const data of cached.channels) { const value = finite(data[frame], 0); min = Math.min(min, value); max = Math.max(max, value); } };
    while (first < last && first % BLOCK) sample(first++);
    while (last > first && last % BLOCK) sample(--last);
    let block = first / BLOCK, end = last / BLOCK;
    while (block < end) {
      let level = 0;
      while (level + 1 < cached.levels.length && block % (2 ** (level + 1)) === 0 && block + 2 ** (level + 1) <= end) level++;
      const index = block / 2 ** level;
      min = Math.min(min, cached.levels[level].low[index]); max = Math.max(max, cached.levels[level].high[index]);
      block += 2 ** level;
    }
    return [min, max];
  }
  function envelope(buffer, options = {}, width = 256) {
    width = clamp(Math.round(finite(width, 256)), 1, 4096);
    const low = new Float32Array(width), high = new Float32Array(width);
    if (!buffer || !buffer.length || !buffer.numberOfChannels) return { low, high };
    const cached = peaks(buffer), rate = buffer.sampleRate;
    const offset = clamp(finite(options.offset, 0), 0, buffer.duration);
    const length = Math.min(buffer.duration - offset, options.sourceLength > 0 ? options.sourceLength : buffer.duration - offset);
    const duration = Math.max(0, finite(options.duration, length));
    const start = clamp(finite(options.start, 0), 0, 1), end = clamp(finite(options.end, 1), start, 1);
    if (!(length > 0 && duration > 0 && end > start)) return { low, high };
    const sourceRange = (from, to) => options.reverse
      ? range(buffer, cached, (offset + length - to) * rate, (offset + length - from) * rate)
      : range(buffer, cached, (offset + from) * rate, (offset + to) * rate);
    for (let x = 0; x < width; x++) {
      const from = duration * (start + (end - start) * x / width), to = duration * (start + (end - start) * (x + 1) / width);
      let extrema;
      if (!options.loop) {
        if (from >= length) continue;
        extrema = sourceRange(from, Math.min(to, length));
      } else if (to - from >= length) extrema = sourceRange(0, length);
      else {
        const phase = from % length, finish = phase + to - from;
        extrema = sourceRange(phase, Math.min(finish, length));
        if (finish > length) {
          const wrapped = sourceRange(0, finish - length);
          extrema = [Math.min(extrema[0], wrapped[0]), Math.max(extrema[1], wrapped[1])];
        }
      }
      low[x] = extrema[0]; high[x] = extrema[1];
    }
    return { low, high };
  }
  function draw(canvas, buffer, options = {}) {
    if (!canvas) return;
    const bounds = canvas.getBoundingClientRect(), ratio = Math.min(3, root.devicePixelRatio || 1);
    canvas.width = clamp(Math.ceil(bounds.width * ratio), 1, 4096);
    canvas.height = clamp(Math.ceil(bounds.height * ratio), 1, 1024);
    const context = canvas.getContext('2d'); context.clearRect(0, 0, canvas.width, canvas.height);
    const result = envelope(buffer, options, canvas.width), amplitude = clamp(finite(options.amplitude, 1), .1, 24);
    context.strokeStyle = options.color || '#c692a4'; context.lineWidth = 1; context.beginPath();
    for (let x = 0; x < canvas.width; x++) {
      context.moveTo(x + .5, canvas.height / 2 - clamp(result.high[x] * amplitude, -1, 1) * canvas.height * .46);
      context.lineTo(x + .5, canvas.height / 2 - clamp(result.low[x] * amplitude, -1, 1) * canvas.height * .46);
    }
    context.stroke();
  }
  const api = { envelope, draw };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.AnharmonicWaveform = api;
})(typeof window !== 'undefined' ? window : globalThis);

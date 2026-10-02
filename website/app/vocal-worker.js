/* Runs the vocal Autotune (vocal-dsp.js) off the main thread so the studio
 * stays responsive while a long take is analysed or rendered.
 * GPL-3.0-or-later. */
'use strict';
importScripts('vocal-dsp.js');

self.onmessage = event => {
  const { id, op, channels, settings, sampleRate } = event.data || {};
  const progress = value => self.postMessage({ id, type: 'progress', value });
  try {
    if (op === 'analyze') {
      const analysis = AnharmonicVocal.analyzePitch(channels, settings, sampleRate, progress);
      const key = AnharmonicVocal.detectKey(analysis);
      const arrays = [analysis.times, analysis.hz, analysis.midi, analysis.target, analysis.confidence];
      self.postMessage({ id, type: 'done', analysis, key }, arrays.map(array => array.buffer));
    } else if (op === 'render') {
      const { channels: rendered, analysis } = AnharmonicVocal.renderAutotune(channels, settings, sampleRate, progress);
      const key = AnharmonicVocal.detectKey(analysis);
      const arrays = [...rendered, analysis.times, analysis.hz, analysis.midi, analysis.target, analysis.confidence];
      self.postMessage({ id, type: 'done', channels: rendered, analysis, key }, arrays.map(array => array.buffer));
    } else throw new Error('Unknown vocal operation');
  } catch (error) {
    self.postMessage({ id, type: 'error', message: error?.message || String(error) });
  }
};

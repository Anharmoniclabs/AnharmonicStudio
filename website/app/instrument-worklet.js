/* AudioWorklet host for the Native and Prism instruments (see instrument-dsp.js).
 * One node is one instrument instance. Notes arrive as timed events, either
 * posted live or passed up front for an offline WAV export, and are applied at
 * their exact sample frame. GPL-3.0-or-later. */
import './instrument-dsp.js';

const { NativeInstrument, PrismProcessor } = globalThis.AnharmonicDSP;

class AnharmonicInstrumentProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const config = options.processorOptions || {};
    this.kind = config.kind === 'prism' ? 'prism' : 'native';
    this.instrument = this.kind === 'prism' ? new PrismProcessor(sampleRate, config.values) : new NativeInstrument(sampleRate, config.patch || {});
    if (this.kind === 'prism') this.instrument.tempo = config.bpm || 120;
    this.events = []; this.cancelled = new Set(); this.notes = new Map();
    for (const event of config.events || []) this.events.push(event);
    this.events.sort((a, b) => a.frame - b.frame);
    this.left = new Float32Array(128); this.right = new Float32Array(128);
    this.port.onmessage = event => this.message(event.data);
  }
  frameOf(time) { return Math.round(time * sampleRate); }
  enqueue(event) {
    let index = this.events.length;
    while (index > 0 && this.events[index - 1].frame > event.frame) index--;
    this.events.splice(index, 0, event);
  }
  message(data) {
    if (!data) return;
    if (data.type === 'events') for (const event of data.events) this.enqueue({ ...event, frame: this.frameOf(event.time) });
    else if (data.type === 'patch' && this.kind === 'native') this.instrument.setPatch(data.patch);
    else if (data.type === 'values' && this.kind === 'prism') this.instrument.setValues(data.values);
    else if (data.type === 'tempo' && this.kind === 'prism') this.instrument.tempo = Math.min(400, Math.max(20, data.bpm));
    else if (data.type === 'panic') { this.events = []; this.instrument.panic(); }
    else if (data.type === 'stop') {
      // Cancel a note that has not started; otherwise release it at the given time.
      const frame = this.frameOf(data.time);
      const pending = this.events.find(event => event.id === data.id && event.type === 'on');
      if (pending && pending.frame >= frame) { this.events = this.events.filter(event => event.id !== data.id); return; }
      this.events = this.events.filter(event => !(event.id === data.id && event.type === 'off'));
      this.enqueue({ type: 'off', id: data.id, note: data.note, frame, fade: data.fade });
    } else if (data.type === 'retime') {
      this.events = this.events.filter(event => !(event.id === data.id && event.type === 'off'));
      this.enqueue({ type: 'off', id: data.id, note: data.note, frame: this.frameOf(data.time) });
    }
  }
  apply(event) {
    if (event.type === 'on') { this.notes.set(event.id, event.note); this.instrument.noteOn(event.note, event.velocity, 1, event.id); }
    else if (event.type === 'off') {
      const fade = event.fade > 0 ? Math.max(1, Math.round(event.fade * sampleRate)) : 0;
      // Prism keys are MIDI-like: release the held key so its arp and both layers follow.
      if (this.kind === 'prism' && !fade) this.instrument.noteOff(event.note);
      else this.instrument.release(event.id, event.note, fade);
      this.notes.delete(event.id);
    }
  }
  process(_inputs, outputs) {
    const output = outputs[0], left = output[0], right = output[1] || output[0];
    const frames = left.length, start = currentFrame;
    this.instrument.readPatch();
    let at = 0;
    while (this.events.length && this.events[0].frame < start + frames) {
      const event = this.events.shift(), offset = Math.max(0, event.frame - start);
      if (offset > at) { this.instrument.render(left, right, offset - at, at); at = offset; }
      this.apply(event);
    }
    if (at < frames) this.instrument.render(left, right, frames - at, at);
    this.instrument.effects(left, right, frames);
    return true;
  }
}
registerProcessor('anharmonic-instrument', AnharmonicInstrumentProcessor);

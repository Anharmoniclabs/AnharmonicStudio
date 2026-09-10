(() => {
  'use strict';

  const FORMAT_VERSION = 5;
  const PAD_COUNT = 64;
  const TRACK_COUNT = 8;
  const MAX_DOCUMENT_BYTES = 32 * 1024 * 1024;
  const MAX_HISTORY_BYTES = 32 * 1024 * 1024;
  const uid = prefix => `${prefix || 'id'}-${crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
  const clone = value => JSON.parse(JSON.stringify(value));

  function defaultPads() {
    return Array.from({ length: PAD_COUNT }, (_, index) => ({
      id: uid('pad'), sample_id: '', name: `Pad ${String(index + 1).padStart(2, '0')}`,
      start: 0, end: 0, gain: 1, pan: 0, pitch: 0, sync_beats: 0, attack: 0.002, release: 0.03, mode: 'one-shot', loop_crossfade: .005, reverse: false, choke: 0, track: index % TRACK_COUNT, root_note: 60, mono: false
    }));
  }

  function defaultTracks() {
    return Array.from({ length: TRACK_COUNT }, (_, index) => ({
      id: `mixer:${String(index).padStart(2, '0')}`, name: index === 0 ? 'Drums' : `Track ${String(index + 1).padStart(2, '0')}`,
      gain: 0.85, pan: 0, mute: false, solo: false,
      fx: { low: 0, mid: 0, mid_freq: 900, high: 0, filter_type: 'off', cutoff: 20000, resonance: 0.15, drive: 0, comp: false, threshold: -18, ratio: 4, attack: 0.01, release: 0.12, makeup: 0, send_delay: 0, send_reverb: 0 }
    }));
  }

  function defaultPattern() {
    return { id: uid('pattern'), name: 'pattern 1', bars: 1, div: 4, notes: [], steps: {} };
  }

  function defaultSynth() {
    return { name: 'Midnight Brass', osc1: 'saw', osc2: 'square', osc_mix: .42, osc2_octave: 0, detune: 8, sub: .18, noise: .015, attack: .025, decay: .32, sustain: .68, release: .65, cutoff: 2400, resonance: .28, drive: .18, spread: .42, lfo_rate: .32, lfo_pitch: 2, volume: .42, track: 2 };
  }

  function defaultArp() {
    return { enabled: false, rate_beats: .25, mode: 'up', octaves: 1, gate: .72 };
  }

  function defaultRows() {
    return ['Drums', 'Bass', 'Keys', 'Atmosphere', 'Melody'].map((name, index) => ({
      id: uid('row'), name, mute: false, solo: false, record_armed: false, clips: [], record_source: 'audio', record_track: index
    }));
  }

  function defaultProject(name = 'Untitled project') {
    const pattern = defaultPattern();
    return {
      format_version: FORMAT_VERSION, name, bpm: 110, swing: 0, master: 0.82,
      pads: defaultPads(), patterns: [pattern], selected_pattern: 0, current_pattern: pattern.id,
      tracks: defaultTracks(), synth: defaultSynth(), arp: defaultArp(), rows: defaultRows(), automation: [], vocal_settings: {}, vocal_record: {}, loop_start: 0, loop_end: 8,
      media: []
    };
  }

  // Check before JSON cloning: JSON.stringify silently changes NaN/Infinity to null.
  // Unknown desktop extensions are preserved, but must be bounded, safe JSON.
  function validateJSON(document) {
    const ancestors = new Set();
    let nodes = 0;
    let bytes = 0;
    function visit(value, depth) {
      if (++nodes > 1000000 || depth > 40) throw new Error('Project exceeds the complexity limit');
      if (typeof value === 'string') bytes += value.length * 2;
      else if (typeof value === 'number') {
        if (!Number.isFinite(value)) throw new Error('Project contains a non-finite number');
        bytes += 16;
      } else if (value !== null && typeof value === 'object') {
        if (ancestors.has(value)) throw new Error('Project contains a cyclic value');
        ancestors.add(value);
        for (const [key, child] of Object.entries(value)) {
          if (['__proto__', 'prototype', 'constructor'].includes(key)) throw new Error('Project contains an unsafe property');
          bytes += key.length * 2 + 8;
          visit(child, depth + 1);
        }
        ancestors.delete(value);
      } else if (value !== null && typeof value !== 'boolean') throw new Error('Project must contain JSON values only');
      if (bytes > MAX_DOCUMENT_BYTES) throw new Error('Project exceeds the 32 MiB document limit; media belongs in the portable bundle');
    }
    visit(document, 0);
  }

  function object(value, name) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${name} must be an object`);
    return value;
  }
  function records(value, name, limit) {
    if (value === undefined) return [];
    if (!Array.isArray(value) || value.length > limit) throw new Error(`Invalid project ${name}`);
    value.forEach(item => object(item, name));
    return value;
  }
  function number(value, fallback, minimum, maximum, integer = false) {
    if (value === undefined) return fallback;
    if (typeof value !== 'number' || !Number.isFinite(value) || value < minimum || value > maximum || (integer && !Number.isInteger(value))) throw new Error('Project contains an out-of-range or invalid number');
    return value;
  }
  function boolean(value, fallback = false) {
    if (value === undefined) return fallback;
    if (typeof value !== 'boolean') throw new Error('Project contains an invalid boolean');
    return value;
  }
  function string(value, fallback = '', limit = 4096) {
    if (value === undefined) return fallback;
    if (typeof value !== 'string' || value.length > limit) throw new Error('Project contains an invalid string');
    return value;
  }
  function identity(value, fallback) {
    if (value === undefined || value === '') return fallback;
    const result = string(value, fallback, 128);
    if (!result.trim() || result.trim() !== result) throw new Error('Project IDs must not have surrounding whitespace');
    return result;
  }
  function choice(value, fallback, choices) {
    const result = value === undefined ? fallback : value;
    if (!choices.includes(result)) throw new Error(`Unsupported project option: ${String(result)}`);
    return result;
  }
  function unique(items, name) {
    const ids = new Set();
    for (const item of items) {
      if (ids.has(item.id)) throw new Error(`Duplicate ${name} ID`);
      ids.add(item.id);
    }
    return items;
  }
  function numericFields(target, fields) {
    for (const [key, [fallback, low, high, integer]] of Object.entries(fields)) target[key] = number(target[key], fallback, low, high, integer);
    return target;
  }

  function normalizeSteps(steps) {
    if (steps === undefined || steps === null) return {};
    const normalized = {};
    const index = (key, maximum) => {
      if (!/^(0|[1-9]\d*)$/.test(String(key))) throw new Error('Invalid step or pad index');
      return number(Number(key), 0, 0, maximum, true);
    };
    if (Array.isArray(steps)) {
      if (steps.length > PAD_COUNT) throw new Error('Too many pad step rows');
      steps.forEach((hits, pad) => {
        if (!Array.isArray(hits) || hits.length > 32768) throw new Error('Invalid legacy step row');
        if (hits.length) normalized[pad] = Object.fromEntries(hits.map(step => [index(step, 32767), 1]));
      });
    } else {
      object(steps, 'steps');
      for (const [pad, hits] of Object.entries(steps)) {
        index(pad, PAD_COUNT - 1);
        object(hits, 'step row');
        const row = {};
        for (const [step, velocity] of Object.entries(hits)) row[index(step, 32767)] = number(velocity, 1, 0, 1);
        if (Object.keys(row).length) normalized[pad] = row;
      }
    }
    return normalized;
  }

  function normalize(document) {
    object(document, 'Project');
    validateJSON(document);
    number(document.format_version, 0, 0, FORMAT_VERSION, true);
    const source = clone(document);
    const base = defaultProject(string(source.name, 'Untitled project'));
    const normalized = { ...base, ...source, format_version: FORMAT_VERSION,
      bpm: number(source.bpm ?? source.tempo, 110, 20, 400), master: number(source.master, base.master, 0, 2), swing: number(source.swing, 0, 0, 100) };
    normalized.pads = records(source.pads, 'pads', PAD_COUNT).map((pad, index) => {
      const result = numericFields({ ...base.pads[index], ...pad, id: identity(pad.id, base.pads[index].id) }, {
        start: [0, 0, 1000000], end: [0, 0, 1000000], gain: [1, 0, 4], pan: [0, -1, 1], pitch: [0, -96, 96], sync_beats: [0, 0, 1000000],
        attack: [.002, 0, 60], release: [.03, 0, 60], loop_crossfade: [.005, 0, 60], choke: [0, 0, 8, true], track: [0, 0, TRACK_COUNT - 1, true], root_note: [60, 0, 127, true]
      });
      result.sample_id = pad.sample_id === null ? '' : string(pad.sample_id);
      result.name = string(pad.name, base.pads[index].name);
      result.mode = choice(pad.mode, 'one-shot', ['one-shot', 'gate', 'loop']);
      for (const key of ['reverse', 'mono']) result[key] = boolean(pad[key]);
      return result;
    });
    while (normalized.pads.length < PAD_COUNT) normalized.pads.push(base.pads[normalized.pads.length]);
    unique(normalized.pads, 'pad');
    const patterns = records(source.patterns, 'patterns', 1024);
    if (!patterns.length) patterns.push(source.steps !== undefined || source.notes !== undefined ? { steps: source.steps ?? {}, notes: source.notes ?? [], bars: source.bars ?? 1, div: source.div ?? 4 } : defaultPattern());
    normalized.patterns = unique(patterns.map((pattern, index) => ({
      ...pattern, id: identity(pattern.id, uid('pattern')), name: string(pattern.name, `pattern ${index + 1}`),
      bars: number(pattern.bars, 1, 1, 256, true), div: number(pattern.div, 4, 1, 32, true), steps: normalizeSteps(pattern.steps),
      notes: unique(records(pattern.notes, 'notes', 100000).map(note => ({ ...note, id: identity(note.id, uid('note')),
        pitch: number(note.pitch, 60, 0, 127, true), start: number(note.start, 0, 0, 1000000), duration: number(note.duration, .25, Number.MIN_VALUE, 4096), velocity: number(note.velocity, .8, Number.MIN_VALUE, 1),
        pad: note.pad === undefined || note.pad === null ? null : number(note.pad, 0, 0, PAD_COUNT - 1, true) })), 'note')
    })), 'pattern');
    normalized.tracks = records(source.tracks, 'tracks', TRACK_COUNT).map((track, index) => {
      if (source.format_version === FORMAT_VERSION && !track.id) throw new Error('Mixer track ID required in project format 5');
      const result = numericFields({ ...base.tracks[index], ...track, id: identity(track.id, base.tracks[index].id) }, { gain: [.85, 0, 4], pan: [0, -1, 1] });
      result.name = string(track.name, base.tracks[index].name);
      for (const key of ['mute', 'solo']) result[key] = boolean(track[key]);
      result.fx = numericFields({ ...base.tracks[index].fx, ...object(track.fx ?? {}, 'track effects') }, {
        low: [0, -36, 36], mid: [0, -36, 36], high: [0, -36, 36], mid_freq: [900, 20, 24000], cutoff: [20000, 20, 24000], resonance: [.15, 0, 1], drive: [0, 0, 1],
        threshold: [-18, -100, 0], ratio: [4, 1, 40], attack: [.01, 0, 10], release: [.12, 0, 60], makeup: [0, -36, 36], send_delay: [0, 0, 1], send_reverb: [0, 0, 1]
      });
      result.fx.comp = boolean(result.fx.comp);
      result.fx.filter_type = choice(result.fx.filter_type, 'off', ['off', 'lowpass', 'highpass']);
      return result;
    });
    while (normalized.tracks.length < TRACK_COUNT) normalized.tracks.push(base.tracks[normalized.tracks.length]);
    unique(normalized.tracks, 'mixer track');
    normalized.media = unique(records(source.media, 'media', 10000).map(media => ({ ...media, id: identity(media.id, uid('sample')), name: string(media.name, 'Untitled sample'), mime: string(media.mime, 'audio/wav', 128),
      duration: number(media.duration, 0, 0, 1000000), sample_rate: number(media.sample_rate, 0, 0, 384000, true), size: number(media.size, 0, 0, Number.MAX_SAFE_INTEGER, true) })), 'media');
    normalized.synth = numericFields({ ...base.synth, ...object(source.synth ?? {}, 'synth') }, {
      osc_mix: [.42, 0, 1], osc2_octave: [0, -4, 4, true], detune: [8, 0, 1200], sub: [.18, 0, 1], noise: [.015, 0, 1],
      attack: [.025, 0, 60], decay: [.32, 0, 60], sustain: [.68, 0, 1], release: [.65, 0, 60], cutoff: [2400, 20, 24000], resonance: [.28, 0, 1],
      drive: [.18, 0, 1], spread: [.42, 0, 1], lfo_rate: [.32, 0, 100], lfo_pitch: [2, 0, 1200], volume: [.42, 0, 2], track: [2, 0, TRACK_COUNT - 1, true]
    });
    for (const key of ['osc1', 'osc2']) normalized.synth[key] = choice(normalized.synth[key], 'saw', ['saw', 'sawtooth', 'square', 'sine', 'triangle', 'pulse', 'noise']);
    normalized.arp = numericFields({ ...base.arp, ...object(source.arp ?? {}, 'arp') }, { rate_beats: [.25, 1 / 128, 16], octaves: [1, 1, 8, true], gate: [.72, .01, 1] });
    normalized.arp.enabled = boolean(normalized.arp.enabled);
    normalized.arp.mode = choice(normalized.arp.mode, 'up', ['up', 'down', 'up/down', 'random']);
    normalized.rows = unique(records(source.rows, 'rows', 4096).map(row => ({ ...row, id: identity(row.id, uid('row')), name: string(row.name, 'Track'),
      mute: boolean(row.mute), solo: boolean(row.solo), record_armed: boolean(row.record_armed), record_source: choice(row.record_source, 'audio', ['audio', 'notes']), record_track: number(row.record_track, 3, 0, TRACK_COUNT - 1, true),
      clips: unique(records(row.clips, 'clips', 100000).map(clip => ({ ...clip, id: identity(clip.id, uid('clip')), kind: choice(clip.kind, 'pattern', ['pattern', 'audio']), ref: string(clip.ref),
        start_beat: number(clip.start_beat, 0, 0, 1000000), length_beats: number(clip.length_beats, 4, 0, 1000000), offset: number(clip.offset, 0, 0, 1000000), source_length: number(clip.source_length, 0, 0, 1000000),
        gain: number(clip.gain, 1, 0, 4), track: number(clip.track, 3, 0, TRACK_COUNT - 1, true), loop: boolean(clip.loop), loop_crossfade: number(clip.loop_crossfade, .005, 0, 60), reverse: boolean(clip.reverse), mute: boolean(clip.mute) })), 'clip')
    })), 'row');
    if (source.rows === undefined) normalized.rows = defaultRows();
    numericFields(normalized, { loop_start: [0, 0, 1000000], loop_end: [8, 0, 1000000] });
    if (source.web_effects !== undefined) normalized.web_effects = numericFields({ ...object(source.web_effects, 'web effects') }, { tone: [0, -12, 12], compression: [1, 1, 12], delay: [0, 0, 40] });
    const currentIndex = normalized.patterns.findIndex(pattern => pattern.id === source.current_pattern);
    normalized.selected_pattern = currentIndex >= 0 ? currentIndex : Math.min(normalized.patterns.length - 1, number(source.selected_pattern, 0, 0, 1023, true));
    normalized.current_pattern = normalized.patterns[normalized.selected_pattern].id;
    for (const field of ['loop_enabled', 'self_choke']) if (source[field] !== undefined) normalized[field] = boolean(source[field]);
    if (source.master_fx !== undefined) {
      normalized.master_fx = numericFields({ ...object(source.master_fx, 'master effects') }, { low: [0, -36, 36], mid: [0, -36, 36], high: [0, -36, 36], drive: [0, 0, 1], glue_amount: [.4, 0, 1] });
      normalized.master_fx.glue = boolean(source.master_fx.glue);
    }
    if (source.delay_fx !== undefined) {
      normalized.delay_fx = numericFields({ ...object(source.delay_fx, 'delay effects') }, { feedback: [.36, 0, 1], damping: [.4, 0, 1], level: [.9, 0, 4] });
      normalized.delay_fx.enabled = boolean(source.delay_fx.enabled, true);
      normalized.delay_fx.ping_pong = boolean(source.delay_fx.ping_pong, true);
      normalized.delay_fx.sync = choice(source.delay_fx.sync, '1/8', ['1/1', '1/2', '1/4', '1/8', '1/16', '1/8.', '1/8D', '1/8T', '1/16T']);
    }
    if (source.reverb_fx !== undefined) {
      normalized.reverb_fx = numericFields({ ...object(source.reverb_fx, 'reverb effects') }, { size: [.55, 0, 1], damping: [.45, 0, 1], width: [1, 0, 2], predelay: [.018, 0, .3], level: [.9, 0, 4] });
      normalized.reverb_fx.enabled = boolean(source.reverb_fx.enabled, true);
    }
    validateJSON(normalized);
    return normalized;
  }

  class ProjectStore {
    constructor(document) {
      this.document = normalize(document || defaultProject());
      this.history = [];
      this.future = [];
      this.listeners = new Set();
    }
    get project() { return this.document; }
    get pattern() { return this.document.patterns[this.document.selected_pattern]; }
    subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
    notify() { this.listeners.forEach(listener => listener(this.document)); }
    transact(label, change) {
      const before = clone(this.document);
      const next = clone(this.document);
      change(next);
      const selected = number(next.selected_pattern, 0, 0, next.patterns.length - 1, true);
      next.current_pattern = next.patterns[selected].id;
      const after = normalize(next);
      const beforeJSON = JSON.stringify(before);
      const afterJSON = JSON.stringify(after);
      if (beforeJSON === afterJSON) return;
      this.document = after;
      const bytes = 2 * (beforeJSON.length + afterJSON.length);
      this.history.push({ label, before, after: clone(after), bytes });
      let historyBytes = this.history.reduce((total, entry) => total + entry.bytes, 0);
      while (this.history.length > 80 || historyBytes > MAX_HISTORY_BYTES) historyBytes -= this.history.shift().bytes;
      this.future = [];
      this.notify();
    }
    undo() {
      const entry = this.history.pop(); if (!entry) return false;
      this.future.push(entry); this.document = clone(entry.before); this.notify(); return true;
    }
    redo() {
      const entry = this.future.pop(); if (!entry) return false;
      this.history.push(entry); this.document = clone(entry.after); this.notify(); return true;
    }
    toggleStep(pad, step, velocity = 1) {
      number(pad, 0, 0, PAD_COUNT - 1, true);
      number(step, 0, 0, 32767, true);
      number(velocity, 1, 0, 1);
      this.transact('toggle step', document => {
        const row = document.patterns[document.selected_pattern].steps[pad] || (document.patterns[document.selected_pattern].steps[pad] = {});
        if (row[step] !== undefined) delete row[step]; else row[step] = Math.max(0.01, Math.min(1, velocity));
        if (!Object.keys(row).length) delete document.patterns[document.selected_pattern].steps[pad];
      });
    }
    setStep(pad, step, velocity) {
      number(pad, 0, 0, PAD_COUNT - 1, true);
      number(step, 0, 0, 32767, true);
      if (velocity !== null && velocity !== undefined) number(velocity, 1, 0, 1);
      this.transact('edit step', document => {
        const pattern = document.patterns[document.selected_pattern];
        const row = pattern.steps[pad] || (pattern.steps[pad] = {});
        if (velocity === null || velocity === undefined || velocity <= 0) delete row[step];
        else row[step] = Math.max(.01, Math.min(1, Number(velocity) || 1));
        if (!Object.keys(row).length) delete pattern.steps[pad];
      });
    }
    setPad(pad, changes) { number(pad, 0, 0, PAD_COUNT - 1, true); this.transact('edit pad', document => { document.pads[pad] = { ...document.pads[pad], ...changes }; }); }
    setTrack(track, changes) { number(track, 0, 0, TRACK_COUNT - 1, true); this.transact('edit mixer track', document => { document.tracks[track] = { ...document.tracks[track], ...changes }; }); }
    setRow(row, changes) { number(row, 0, 0, this.document.rows.length - 1, true); this.transact('edit arrangement row', document => { document.rows[row] = { ...document.rows[row], ...changes }; }); }
    setSynth(changes) { this.transact('edit synth patch', document => { document.synth = { ...document.synth, ...changes }; }); }
    setArp(changes) { this.transact('edit arpeggiator', document => { document.arp = { ...document.arp, ...changes }; }); }
    addMedia(metadata) {
      const media = { id: metadata.id || uid('sample'), name: metadata.name || 'Untitled sample', mime: metadata.mime || 'audio/wav', duration: Number(metadata.duration) || 0, sample_rate: Number(metadata.sample_rate) || 0, size: Number(metadata.size) || 0 };
      this.transact('add media', document => { document.media = document.media.filter(item => item.id !== media.id); document.media.push(media); });
      return media.id;
    }
    assignPad(pad, mediaId, changes = {}) {
      number(pad, 0, 0, PAD_COUNT - 1, true);
      this.transact('assign sample to pad', document => {
        document.pads[pad] = { ...document.pads[pad], ...changes, sample_id: mediaId };
      });
    }
    setTempo(bpm) { const value = number(bpm, 110, 20, 400); this.transact('change tempo', document => { document.bpm = value; }); }
    toJSON() { return clone(this.document); }
    load(document) { this.document = normalize(document); this.history = []; this.future = []; this.notify(); }
  }

  window.AnharmonicProject = Object.freeze({ FORMAT_VERSION, PAD_COUNT, TRACK_COUNT, defaultProject, normalize, ProjectStore });
})();

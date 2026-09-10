(() => {
  'use strict';

  const FORMAT_VERSION = 5;
  const PAD_COUNT = 64;
  const TRACK_COUNT = 8;
  const uid = prefix => `${prefix || 'id'}-${crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
  const clone = value => JSON.parse(JSON.stringify(value));

  function defaultPads() {
    return Array.from({ length: PAD_COUNT }, (_, index) => ({
      id: uid('pad'), sample_id: '', name: index < 16 ? ['Kick', 'Snare', 'Closed Hat', 'Open Hat', 'Clap', 'Low Tom', 'Perc', 'Texture'][index] || `Pad ${String(index + 1).padStart(2, '0')}` : `Pad ${String(index + 1).padStart(2, '0')}`,
      start: 0, end: 0, gain: 1, pan: 0, pitch: 0, attack: 0.002, release: 0.03, mode: 'one-shot', reverse: false, choke: 0, track: index % TRACK_COUNT, root_note: 60, mono: false
    }));
  }

  function defaultTracks() {
    return Array.from({ length: TRACK_COUNT }, (_, index) => ({
      id: uid('track'), name: index === 0 ? 'Drums' : `Track ${String(index + 1).padStart(2, '0')}`,
      gain: 0.85, pan: 0, mute: false, solo: false,
      fx: { low: 0, mid: 0, mid_freq: 900, high: 0, filter_type: 'off', cutoff: 20000, resonance: 0.15, drive: 0, comp: false, threshold: -18, ratio: 4, attack: 0.01, release: 0.12, makeup: 0, send_delay: 0, send_reverb: 0 }
    }));
  }

  function defaultPattern() {
    return { id: uid('pattern'), name: 'pattern 1', bars: 1, div: 4, notes: [], steps: { 0: { 0: 1, 4: 1, 8: 1, 12: 1 }, 1: { 4: 1, 12: 1 }, 2: { 0: 1, 2: 1, 4: 1, 6: 1, 8: 1, 10: 1, 12: 1, 14: 1 }, 3: { 7: 1, 15: 1 } } };
  }

  function defaultSynth() {
    return { name: 'Midnight Brass', osc1: 'saw', osc2: 'square', osc_mix: .42, osc2_octave: 0, detune: 8, sub: .18, noise: .015, attack: .025, decay: .32, sustain: .68, release: .65, cutoff: 2400, resonance: .28, drive: .18, spread: .42, lfo_rate: .32, lfo_pitch: 2, volume: .42, track: 2 };
  }

  function defaultArp() {
    return { enabled: false, rate_beats: .25, mode: 'up', octaves: 1, gate: .72 };
  }

  function defaultRows(patternId) {
    return ['Drums', 'Bass', 'Keys', 'Atmosphere', 'Melody'].map((name, index) => ({
      id: uid('row'), name, mute: false, solo: false, record_armed: false, clips: index < 3 ? [{ id: uid('clip'), kind: index === 2 ? 'audio' : 'pattern', ref: patternId, start_beat: 0, length_beats: index === 0 ? 8 : 6, offset: 0, source_length: 0, gain: 1, track: index, loop: false, reverse: false, mute: false }] : [], record_source: 'audio', record_track: index
    }));
  }

  function defaultProject(name = 'Untitled project') {
    const pattern = defaultPattern();
    return {
      format_version: FORMAT_VERSION, name, bpm: 110, swing: 0, master: 0.82,
      pads: defaultPads(), patterns: [pattern], selected_pattern: 0,
      tracks: defaultTracks(), synth: defaultSynth(), arp: defaultArp(), rows: defaultRows(pattern.id), automation: [], vocal_settings: {}, vocal_record: {}, loop_start: 0, loop_end: 8,
      media: []
    };
  }

  function normalizeSteps(steps) {
    const normalized = {};
    if (Array.isArray(steps)) {
      steps.forEach((hits, pad) => { if (Array.isArray(hits) && hits.length) normalized[pad] = Object.fromEntries(hits.map(step => [step, 1])); });
    } else if (steps && typeof steps === 'object') {
      Object.entries(steps).forEach(([pad, hits]) => {
        if (!hits || typeof hits !== 'object') return;
        normalized[pad] = {};
        Object.entries(hits).forEach(([step, velocity]) => { normalized[pad][step] = Math.max(0.01, Math.min(1, Number(velocity) || 1)); });
      });
    }
    return normalized;
  }

  function normalize(document) {
    const base = defaultProject(document?.name || 'Untitled project');
    const source = document && typeof document === 'object' ? document : {};
    const patterns = Array.isArray(source.patterns) && source.patterns.length ? source.patterns : [source];
    const normalized = { ...base, ...clone(source), format_version: FORMAT_VERSION, bpm: Number(source.bpm ?? source.tempo) || 110 };
    normalized.pads = Array.isArray(source.pads) ? source.pads.map((pad, index) => ({ ...base.pads[index % PAD_COUNT], ...pad, id: pad.id || uid('pad') })) : base.pads;
    while (normalized.pads.length < PAD_COUNT) normalized.pads.push(base.pads[normalized.pads.length]);
    normalized.patterns = patterns.map((pattern, index) => ({
      ...defaultPattern(), ...clone(pattern), id: pattern.id || uid('pattern'), name: pattern.name || `pattern ${index + 1}`,
      bars: Number(pattern.bars) || 1, div: Number(pattern.div) || 4, steps: normalizeSteps(pattern.steps), notes: Array.isArray(pattern.notes) ? pattern.notes.map(note => ({ id: note.id || uid('note'), pitch: Number(note.pitch) || 60, start: Number(note.start) || 0, duration: Math.max(.0625, Number(note.duration) || .25), velocity: Math.max(.01, Math.min(1, Number(note.velocity) || .8)), pad: note.pad ?? null })) : []
    }));
    normalized.tracks = Array.isArray(source.tracks) ? source.tracks.map((track, index) => ({ ...base.tracks[index % TRACK_COUNT], ...track, id: track.id || uid('track') })) : base.tracks;
    while (normalized.tracks.length < TRACK_COUNT) normalized.tracks.push(base.tracks[normalized.tracks.length]);
    normalized.media = Array.isArray(source.media) ? source.media : [];
    normalized.synth = { ...base.synth, ...(source.synth || {}) };
    normalized.arp = { ...base.arp, ...(source.arp || {}) };
    normalized.rows = Array.isArray(source.rows) && source.rows.length ? source.rows.map(row => ({ ...row, id: row.id || uid('row'), clips: Array.isArray(row.clips) ? row.clips.map(clip => ({ id: clip.id || uid('clip'), kind: clip.kind || 'pattern', ref: clip.ref || normalized.patterns[0].id, start_beat: Number(clip.start_beat) || 0, length_beats: Math.max(.25, Number(clip.length_beats) || 4, 0), offset: Number(clip.offset) || 0, source_length: Number(clip.source_length) || 0, gain: Number(clip.gain) || 1, track: Number(clip.track) || 0, loop: Boolean(clip.loop), reverse: Boolean(clip.reverse), mute: Boolean(clip.mute) })) : [] })) : defaultRows(normalized.patterns[0].id);
    normalized.selected_pattern = Math.max(0, Math.min(normalized.patterns.length - 1, Number(source.selected_pattern) || 0));
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
      change(this.document);
      this.history.push({ label, before, after: clone(this.document) });
      if (this.history.length > 80) this.history.shift();
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
      this.transact('toggle step', document => {
        const row = document.patterns[document.selected_pattern].steps[pad] || (document.patterns[document.selected_pattern].steps[pad] = {});
        if (row[step] !== undefined) delete row[step]; else row[step] = Math.max(0.01, Math.min(1, velocity));
        if (!Object.keys(row).length) delete document.patterns[document.selected_pattern].steps[pad];
      });
    }
    setStep(pad, step, velocity) {
      this.transact('edit step', document => {
        const pattern = document.patterns[document.selected_pattern];
        const row = pattern.steps[pad] || (pattern.steps[pad] = {});
        if (velocity === null || velocity === undefined || velocity <= 0) delete row[step];
        else row[step] = Math.max(.01, Math.min(1, Number(velocity) || 1));
        if (!Object.keys(row).length) delete pattern.steps[pad];
      });
    }
    setPad(pad, changes) { this.transact('edit pad', document => { document.pads[pad] = { ...document.pads[pad], ...changes }; }); }
    setTrack(track, changes) { this.transact('edit mixer track', document => { document.tracks[track] = { ...document.tracks[track], ...changes }; }); }
    setRow(row, changes) { this.transact('edit arrangement row', document => { document.rows[row] = { ...document.rows[row], ...changes }; }); }
    setSynth(changes) { this.transact('edit synth patch', document => { document.synth = { ...document.synth, ...changes }; }); }
    setArp(changes) { this.transact('edit arpeggiator', document => { document.arp = { ...document.arp, ...changes }; }); }
    addMedia(metadata) {
      const media = { id: metadata.id || uid('sample'), name: metadata.name || 'Untitled sample', mime: metadata.mime || 'audio/wav', duration: Number(metadata.duration) || 0, sample_rate: Number(metadata.sample_rate) || 0, size: Number(metadata.size) || 0 };
      this.transact('add media', document => { document.media = document.media.filter(item => item.id !== media.id); document.media.push(media); });
      return media.id;
    }
    assignPad(pad, mediaId, changes = {}) {
      this.transact('assign sample to pad', document => {
        document.pads[pad] = { ...document.pads[pad], ...changes, sample_id: mediaId };
      });
    }
    setTempo(bpm) { this.transact('change tempo', document => { document.bpm = Math.max(40, Math.min(240, Number(bpm) || 110)); }); }
    toJSON() { return clone(this.document); }
    load(document) { this.document = normalize(document); this.history = []; this.future = []; this.notify(); }
  }

  window.AnharmonicProject = Object.freeze({ FORMAT_VERSION, PAD_COUNT, TRACK_COUNT, defaultProject, normalize, ProjectStore });
})();

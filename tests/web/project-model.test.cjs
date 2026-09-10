const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const sandbox = { window: {}, crypto: require('node:crypto').webcrypto };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../website/app/project-model.js'), 'utf8'), sandbox);
const { ProjectStore, defaultProject } = sandbox.window.AnharmonicProject;

test('desktop current pattern, MIDI zero, silence and clip metadata survive roundtrip', () => {
  const project = defaultProject();
  const second = { ...project.patterns[0], id: 'second', notes: [{ pitch: 0, start: 0, duration: 1, velocity: .8, pad: 0 }] };
  project.patterns.push(second);
  project.current_pattern = 'second';
  project.master = 0;
  project.rows[0].clips = [{ kind: 'audio', ref: 'sample', start_beat: 0, length_beats: 2, gain: 0, loop_crossfade: .012, custom: { preserve: true } }];
  const store = new ProjectStore(project);
  assert.equal(store.pattern.id, 'second');
  assert.equal(store.pattern.notes[0].pitch, 0);
  const saved = store.toJSON();
  assert.equal(saved.master, 0);
  assert.equal(saved.rows[0].clips[0].gain, 0);
  assert.equal(saved.rows[0].clips[0].loop_crossfade, .012);
  assert.equal(saved.rows[0].clips[0].custom.preserve, true);
});

test('invalid imports leave the current document and history intact', () => {
  const store = new ProjectStore();
  store.setTempo(125);
  const before = JSON.stringify(store.toJSON());
  for (const invalid of [[], null, { format_version: 6 }, { pads: Array(65).fill({}) }, { bpm: Infinity }, { tracks: 'broken' }]) {
    assert.throws(() => store.load(invalid));
    assert.equal(JSON.stringify(store.toJSON()), before);
    assert.equal(store.history.length, 1);
  }
});

test('failed edits are atomic and switching patterns updates desktop identity', () => {
  const store = new ProjectStore();
  const before = JSON.stringify(store.toJSON());
  assert.throws(() => store.transact('failure', document => { document.name = 'lost'; throw new Error('failure'); }));
  assert.equal(JSON.stringify(store.toJSON()), before);
  store.transact('new pattern', document => { document.patterns.push({ ...document.patterns[0], id: 'new' }); document.selected_pattern = 1; });
  assert.equal(store.toJSON().current_pattern, 'new');
  store.undo();
  assert.equal(JSON.stringify(store.toJSON()), before);
  store.redo();
  assert.equal(store.toJSON().current_pattern, 'new');
});

test('new and empty arrangements contain no fictitious audio clips', () => {
  assert.ok(defaultProject().rows.every(row => row.clips.length === 0));
  assert.ok(defaultProject().pads.every(pad => pad.sample_id === '' && !/Kick|Snare/.test(pad.name)));
  assert.equal(Object.keys(defaultProject().patterns[0].steps).length, 0);
  assert.equal(new ProjectStore({ rows: [] }).toJSON().rows.length, 0);
});

test('nested native metadata is preserved and detached from caller ownership', () => {
  const document = { format_version: 4, tracks: [{ name: 'legacy' }], workflow: { routing: ['mixer:00'], extra: { enabled: true } }, pro_daw: { automation: [] } };
  const store = new ProjectStore(document);
  assert.equal(store.project.tracks[0].id, 'mixer:00');
  document.workflow.extra.enabled = false;
  assert.equal(store.project.workflow.extra.enabled, true);
  store.setTempo(130);
  assert.deepEqual(JSON.parse(JSON.stringify(store.toJSON().workflow)), { routing: ['mixer:00'], extra: { enabled: true } });
});

test('invalid nested state and ambiguous identities are rejected atomically', () => {
  const store = new ProjectStore();
  store.setTempo(125);
  const before = JSON.stringify(store.toJSON());
  const cases = [
    { pads: [{ gain: Infinity }] }, { pads: [{ gain: -1 }] }, { pads: [{ track: .5 }] },
    { pads: [{ root_note: 127.5 }] }, { pads: [{ mono: 'false' }] },
    { patterns: [{ bars: 1.5 }] }, { patterns: [{ notes: [{ pitch: .5 }] }] },
    { patterns: [{ notes: [{ pad: 64 }] }] }, { patterns: [{ notes: 'broken' }] },
    { patterns: [{ id: 'same' }, { id: 'same' }] },
    { tracks: [{ id: 'same' }, { id: 'same' }] }, { format_version: 5, tracks: [{}] },
    { rows: [{ clips: [{ track: -1 }] }] }, { rows: [{ clips: 'broken' }] },
    { synth: { volume: NaN } }, { arp: { rate_beats: 0 } }, { workflow: { bad: Infinity } },
    JSON.parse('{"patterns":[{"steps":{"__proto__":{"0":1}}}]}'),
    { patterns: [{ steps: { 64: { 0: 1 } } }] }, { patterns: [{ steps: { 0: { '-1': 1 } } }] },
    { patterns: [{ steps: { 0: { '0.5': 1 } } }] }, { patterns: [{ steps: { 0: { 0: 2 } } }] },
  ];
  for (const invalid of cases) {
    assert.throws(() => store.load(invalid), JSON.stringify(invalid));
    assert.equal(JSON.stringify(store.toJSON()), before);
    assert.equal(store.history.length, 1);
  }
});

test('unsafe edits do not enter history or modify the document', () => {
  const store = new ProjectStore();
  const before = JSON.stringify(store.toJSON());
  for (const edit of [
    () => store.setPad(-1, { gain: 1 }), () => store.setPad(0, { gain: Infinity }),
    () => store.setTrack(8, { mute: true }), () => store.toggleStep('__proto__', 0),
    () => store.setStep(0, -1, 1), () => store.setTempo(NaN),
    () => store.transact('bad selected pattern', p => { p.selected_pattern = 10; })
  ]) {
    assert.throws(edit);
    assert.equal(JSON.stringify(store.toJSON()), before);
    assert.equal(store.history.length, 0);
  }
});

test('zero-velocity steps survive; native-invalid zero notes are rejected', () => {
  const store = new ProjectStore({ patterns: [{ steps: { 0: { 0: 0 } }, notes: [{ pitch: 0, velocity: .1, duration: .1 }] }] });
  assert.equal(store.pattern.steps[0][0], 0);
  assert.equal(store.pattern.notes[0].pitch, 0);
  assert.throws(() => new ProjectStore({ patterns: [{ notes: [{ duration: 0 }] }] }));
  assert.throws(() => new ProjectStore({ patterns: [{ notes: [{ velocity: 0 }] }] }));
});

test('supported native effects reject malformed controls and retain bypass', () => {
  const store = new ProjectStore({ loop_enabled: false, self_choke: false, master_fx: { glue: false, low: 0 }, delay_fx: { enabled: false, sync: '1/8.', level: 1.09 }, reverb_fx: { enabled: false, level: 0 } });
  assert.equal(store.project.master_fx.glue, false);
  assert.equal(store.project.delay_fx.enabled, false);
  assert.equal(store.project.delay_fx.sync, '1/8.');
  assert.equal(store.project.delay_fx.level, 1.09);
  assert.equal(store.project.reverb_fx.level, 0);
  for (const invalid of [{ loop_enabled: 'false' }, { self_choke: 1 }, { master_fx: [] }, { master_fx: { glue: 'false' } }, { delay_fx: { enabled: 'false' } }, { delay_fx: { feedback: -1 } }, { reverb_fx: { size: '1' } }]) assert.throws(() => new ProjectStore(invalid));
});

test('document complexity and history memory have hard bounds', () => {
  const deep = {};
  let cursor = deep;
  for (let i = 0; i < 42; i++) cursor = cursor.nested = {};
  assert.throws(() => new ProjectStore(deep), /complexity/);
  const cyclic = {}; cyclic.again = cyclic;
  assert.throws(() => new ProjectStore(cyclic), /cyclic/);
  const store = new ProjectStore({ custom: 'x'.repeat(300000) });
  for (let i = 0; i < 40; i++) store.setTempo(100 + i);
  assert.ok(store.history.length > 0 && store.history.length < 40);
  assert.ok(store.history.reduce((total, entry) => total + entry.bytes, 0) <= 32 * 1024 * 1024);
  const count = store.history.length;
  store.setTempo(139);
  assert.equal(store.history.length, count, 'no-op edits must not consume history');
});

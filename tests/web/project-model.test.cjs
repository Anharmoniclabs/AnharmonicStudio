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
  assert.equal(new ProjectStore({ rows: [] }).toJSON().rows.length, 0);
});

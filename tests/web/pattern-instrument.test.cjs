const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const sandbox = { window: {}, crypto: require('node:crypto').webcrypto };
for (const file of ['project-model.js', 'audio-engine.js']) {
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../website/app', file), 'utf8'), sandbox);
}
const { ProjectStore, defaultProject } = sandbox.window.AnharmonicProject;
const { collectEvents } = sandbox.window.AnharmonicAudio;
const plain = value => JSON.parse(JSON.stringify(value));

test('patterns share the project synth until given an independent instrument', () => {
  const store = new ProjectStore();
  assert.equal(store.pattern.instrument, undefined);
  assert.equal(store.instrument.name, store.project.synth.name);
  store.setPatternInstrumentIndependent(true);
  store.setInstrument({ cutoff: 500 });
  assert.equal(store.pattern.instrument.cutoff, 500);
  assert.notEqual(store.project.synth.cutoff, 500);
  store.setPatternInstrumentIndependent(false);
  assert.equal(store.pattern.instrument, undefined);
  store.setInstrument({ cutoff: 700 });
  assert.equal(store.project.synth.cutoff, 700);
});

test('presets replace sound but keep the instrument mixer route', () => {
  const store = new ProjectStore();
  store.setPatternInstrumentIndependent(true);
  store.setInstrument({ track: 5 });
  store.applyInstrumentPreset('Deep Bass', { osc1: 'saw', cutoff: 420, volume: .55 });
  assert.equal(store.pattern.instrument.name, 'Deep Bass');
  assert.equal(store.pattern.instrument.cutoff, 420);
  assert.equal(store.pattern.instrument.track, 5);
});

test('pattern instruments survive save and reload and reject invalid patches', () => {
  const project = defaultProject();
  project.patterns[0].instrument = { ...project.synth, name: 'Own', cutoff: 900 };
  const restored = new ProjectStore(plain(new ProjectStore(project).toJSON()));
  assert.equal(restored.pattern.instrument.name, 'Own');
  assert.equal(restored.pattern.instrument.cutoff, 900);
  assert.throws(() => new ProjectStore({ patterns: [{ instrument: { cutoff: Infinity } }] }));
  assert.throws(() => new ProjectStore({ patterns: [{ instrument: { osc1: 'kazoo' } }] }));
  assert.equal('instrument' in new ProjectStore().toJSON().patterns[0], false);
});

test('scheduled synth notes carry their pattern instrument; shared patterns carry none', () => {
  const project = defaultProject();
  project.patterns[0].notes = [{ id: 'a', pitch: 60, start: 0, duration: 1, velocity: .8, pad: null }];
  assert.equal(collectEvents(project, 'pattern', 0, 4)[0].synth, null);
  project.patterns[0].instrument = { ...project.synth, name: 'Own' };
  assert.equal(collectEvents(project, 'pattern', 0, 4)[0].synth.name, 'Own');
});

test('merged edits form a single undo step and a new key starts another', () => {
  const store = new ProjectStore();
  store.setStep(0, 0, 1, 'take-1'); store.setStep(0, 4, 1, 'take-1'); store.setStep(1, 8, 1, 'take-1');
  assert.equal(store.history.length, 1);
  store.setStep(2, 0, 1, 'take-2');
  assert.equal(store.history.length, 2);
  store.undo();
  assert.equal(store.pattern.steps[2], undefined);
  assert.deepEqual(Object.keys(store.pattern.steps).sort(), ['0', '1']);
  store.undo();
  assert.deepEqual(plain(store.pattern.steps), {});
  store.redo();
  assert.deepEqual(Object.keys(store.pattern.steps).sort(), ['0', '1']);
  store.setStep(3, 0, 1);
  store.setStep(3, 1, 1);
  assert.equal(store.history.length, 3, 'unkeyed edits stay separate');
});

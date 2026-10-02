const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const sandbox = { window: {}, crypto: require('node:crypto').webcrypto, console };
for (const file of ['project-model.js', 'instrument-dsp.js', 'audio-engine.js']) {
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../website/app', file), 'utf8'), sandbox);
}
sandbox.window.AnharmonicDSP = sandbox.AnharmonicDSP;
const { ProjectStore, defaultProject } = sandbox.window.AnharmonicProject;
const { collectEvents, instrumentSpec, requireSupportedProject } = sandbox.window.AnharmonicAudio;
const dsp = sandbox.AnharmonicDSP;
const plain = value => JSON.parse(JSON.stringify(value));

test('inserting Native and Prism mirrors the desktop project format', () => {
  const store = new ProjectStore();
  const native = store.insertInstrument('native');
  assert.equal(store.pattern.selected_instrument, native);
  assert.deepEqual(plain(store.pattern.instrument_ids), [native]);
  assert.equal(store.instrumentInfo(native).kind, 'native');
  assert.match(native, /^[A-Za-z0-9_-]{1,128}$/);
  const prism = store.insertInstrument('prism', dsp.prismParametersFromValues(dsp.prismDefaults()));
  const saved = plain(store.toJSON());
  assert.equal(saved.instruments.length, 2);
  assert.equal(saved.instrument_plugins[prism].plugin_name, 'Anharmonic Prism');
  assert.match(saved.instrument_plugins[prism].path, /Anharmonic Prism\.vst3$/);
  assert.equal(Object.keys(saved.instrument_plugins[prism].parameters).length, 76);
  assert.deepEqual(saved.patterns[0].instrument_ids, [native, prism]);
  assert.equal(store.instrumentInfo(prism).kind, 'prism');
  store.undo();
  assert.equal(store.project.instruments.length, 1);
});

test('instrument patches and Prism parameters edit independently of the shared synth', () => {
  const store = new ProjectStore();
  const id = store.insertInstrument('native');
  store.setInstrumentPatch(id, { cutoff: 600 });
  assert.equal(store.instrumentInfo(id).patch.cutoff, 600);
  assert.equal(store.project.synth.cutoff, 2400);
  store.setInstrumentPatch(null, { cutoff: 900 });
  assert.equal(store.project.synth.cutoff, 900);
  const prism = store.insertInstrument('prism');
  const cutoff = dsp.PRISM_SPECS[dsp.PRISM_INDEX.cutoff];
  store.setPrismParameters(prism, { 12: dsp.prismNormalize(cutoff, 777) }, 'drag');
  store.setPrismParameters(prism, { 12: dsp.prismNormalize(cutoff, 888) }, 'drag');
  assert.ok(Math.abs(dsp.prismValuesFromParameters(store.instrumentInfo(prism).plugin.parameters)[12] - 888) < 1e-6);
  const length = store.history.length; store.undo();
  assert.equal(store.history.length, length - 1, 'a knob drag undoes as one step');
  assert.throws(() => store.setPrismParameters(id, { 12: .5 }), /not Prism/);
});

test('notes route to their instrument and survive save and reload', () => {
  const store = new ProjectStore();
  const prism = store.insertInstrument('prism');
  store.transact('notes', project => {
    project.patterns[0].notes.push({ id: 'n1', pitch: 60, start: 0, duration: 1, velocity: .8, pad: null, instrument: prism });
    project.patterns[0].notes.push({ id: 'n2', pitch: 64, start: 1, duration: 1, velocity: .8, pad: null });
  });
  const restored = new ProjectStore(plain(store.toJSON()));
  const events = collectEvents(restored.project, 'pattern', 0, 4);
  assert.equal(events.find(event => event.pitch === 60).instrument, prism);
  assert.equal(events.find(event => event.pitch === 64).instrument, null);
  const spec = instrumentSpec(restored.project, prism);
  assert.equal(spec.kind, 'prism'); assert.equal(spec.values.length, 76); assert.equal(spec.key, 'prism:' + prism);
  assert.equal(instrumentSpec(restored.project, null).key, 'synth');
});

test('invalid instrument references and third-party instrument plugins are rejected', () => {
  const project = defaultProject();
  project.patterns[0].notes = [{ pitch: 60, start: 0, duration: 1, velocity: .8, pad: null, instrument: 'missing' }];
  assert.throws(() => new ProjectStore(project), /missing instrument/);
  const both = defaultProject();
  both.instruments = [{ id: 'a', name: 'A', patch: {} }];
  both.patterns[0].notes = [{ pitch: 60, start: 0, duration: 1, velocity: .8, pad: 0, instrument: 'a' }];
  assert.throws(() => new ProjectStore(both), /pad or an instrument/);
  const hosted = defaultProject();
  hosted.instruments = [{ id: 'vst', name: 'Some synth', patch: {} }];
  hosted.instrument_plugins = { vst: { path: '/plugins/Other.vst3', parameters: {}, state: '', bypass: false } };
  const saved = new ProjectStore(hosted).toJSON();
  assert.throws(() => requireSupportedProject(saved), /third-party instrument plugins/);
  assert.throws(() => new ProjectStore({ ...defaultProject(), instrument_plugins: { ghost: { path: 'x.vst3' } } }), /unknown instrument/);
});

test('a desktop project with Native and Prism instruments opens unchanged', () => {
  const desktop = defaultProject();
  desktop.instruments = [{ id: 'native1', name: 'Keys · Native 1', patch: { ...desktop.synth, name: 'Velvet Poly', cutoff: 1750, filter_env: .24, lfo_filter: .1, sample_source: '', sample_layer: '', layer_mix: .35, layer_octave: 0, sample_reverse: false, motion: 0 }, midi_channel: null },
    { id: 'prism1', name: 'Pad · Prism 2', patch: { ...desktop.synth }, midi_channel: 3 }];
  desktop.instrument_plugins = { prism1: { path: '/home/user/AnharmonicStudio/plugins/bundled/Anharmonic Prism.vst3', plugin_name: '', parameters: { 0: 0, 12: .4 }, state: '', bypass: false } };
  desktop.patterns[0].instrument_ids = ['native1', 'prism1']; desktop.patterns[0].selected_instrument = 'prism1';
  const saved = plain(new ProjectStore(desktop).toJSON());
  assert.deepEqual(saved.instruments, plain(desktop.instruments));
  assert.deepEqual(saved.instrument_plugins.prism1.parameters, { 0: 0, 12: .4 });
  assert.equal(saved.patterns[0].selected_instrument, 'prism1');
  assert.equal(instrumentSpec(saved, 'prism1').kind, 'prism', 'the bundled Prism path is recognised without a plugin name');
  assert.equal(instrumentSpec(saved, 'native1').patch.name, 'Velvet Poly');
});

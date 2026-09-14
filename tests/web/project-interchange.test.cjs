const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/core-project.json'), 'utf8'));
const sandbox = { window: {}, crypto: require('node:crypto').webcrypto };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../website/app/project-model.js'), 'utf8'), sandbox);
const { ProjectStore } = sandbox.window.AnharmonicProject;

function assertSemantics(expected, actual, location = 'project') {
  if (Array.isArray(expected)) {
    assert.ok(actual.length >= expected.length, location);
    expected.forEach((item, index) => assertSemantics(item, actual[index], `${location}[${index}]`));
  } else if (expected !== null && typeof expected === 'object') {
    for (const [key, value] of Object.entries(expected)) {
      assert.ok(Object.hasOwn(actual, key), `${location}.${key}`);
      assertSemantics(value, actual[key], `${location}.${key}`);
    }
  } else assert.equal(actual, expected, location);
}

test('native and browser shared fixture retains all explicit music fields', () => {
  const store = new ProjectStore(fixture);
  assertSemantics(fixture, store.toJSON());
  assert.equal(store.pattern.id, fixture.current_pattern);
  assert.equal(store.pattern.notes[0].pitch, 0);
  assert.equal(store.pattern.notes[0].pad, null);
  assert.equal(store.pattern.notes[1].pad, 0);
});

test('browser save/reload retains the cross-language music contract', () => {
  const saved = new ProjectStore(fixture).toJSON();
  const restored = new ProjectStore(JSON.parse(JSON.stringify(saved))).toJSON();
  assertSemantics(fixture, restored);
  assert.deepEqual(JSON.parse(JSON.stringify(restored)), JSON.parse(JSON.stringify(saved)));
});

test('native vocal payload survives browser import and export without schema loss', () => {
  const native = {
    format_version: 6,
    vocal: {
      enabled: true, key: 'B', scale: 'pentatonic', strength: .73, retune_ms: 11,
      humanize: .35, mix: .88, transpose: 4, formant: .7, low_note: 43,
      high_note: 91, gate_db: -52, highpass_hz: 95, deesser: .3,
      compression: .45, presence_db: 1.25, output_db: -2,
      native_extension: { model: 'golden-vocal', settings: { preserve: true } }
    }
  };
  const exported = new ProjectStore(JSON.parse(JSON.stringify(new ProjectStore(native).toJSON()))).toJSON();
  assert.deepEqual(JSON.parse(JSON.stringify(exported.vocal)), native.vocal);
});

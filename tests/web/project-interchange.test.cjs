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

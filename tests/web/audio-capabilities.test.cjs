const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const sandbox = { window: {}, crypto: require('node:crypto').webcrypto };
for (const file of ['project-model.js', 'audio-engine.js']) {
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../website/app', file), 'utf8'), sandbox);
}
const { defaultProject, ProjectStore } = sandbox.window.AnharmonicProject;
const { requireSupportedProject } = sandbox.window.AnharmonicAudio;

for (const settings of [{ mute: true, gain: 1 }, { mute: false, gain: 0 }, { mute: false, gain: .5 }, { mute: false, gain: 2 }]) {
  test(`native mixer group ${JSON.stringify(settings)} is rejected without changing its saved state`, () => {
    const project = defaultProject();
    project.workflow = { groups: [{ id: 'group', name: 'Native group', members: [project.tracks[0].id], ...settings }] };
    const saved = new ProjectStore(project).toJSON();
    const before = JSON.stringify(saved);
    assert.throws(() => requireSupportedProject(saved), /mixer group gain or mute/);
    assert.equal(JSON.stringify(saved), before);
  });
}

test('absent, empty, neutral and unassigned groups remain usable and preserved', () => {
  const project = defaultProject();
  const groups = [
    undefined,
    [],
    [{ id: 'neutral', members: [project.tracks[0].id], gain: 1, mute: false }],
    [{ id: 'defaults', members: [project.tracks[0].id] }],
    [{ id: 'empty', members: [], gain: 0, mute: true }],
    [{ id: 'unassigned', members: ['missing-track'], gain: 0, mute: true }],
  ];
  for (const value of groups) {
    project.workflow = value === undefined ? {} : { groups: value };
    const saved = new ProjectStore(project).toJSON();
    const before = JSON.stringify(saved);
    assert.doesNotThrow(() => requireSupportedProject(saved));
    assert.equal(JSON.stringify(saved), before);
  }
});

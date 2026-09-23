"""Instance plugin state requires a reader that cannot silently discard it."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from mpclab.model import COMPATIBLE_PROJECT_FORMAT_VERSION, PROJECT_FORMAT_VERSION, Project
from mpclab.project_migrations import migrate_project_document


def plugin_spec():
    return {
        "path": "/synthetic/Instrument.vst3",
        "parameters": {"gain": 0.25},
        "plugin_name": "",
        "state": "",
        "bypass": False,
    }


def test_instance_plugin_document_requires_new_reader_and_roundtrips(tmp_path):
    project = Project()
    instrument = project.add_instrument("Independent plugin", replace(project.synth))
    instrument.plugin = plugin_spec()
    project.add_instrument("Native patch", replace(project.synth))
    path = tmp_path / "song.json"
    project.save(path)
    document = json.loads(path.read_text())

    assert document["format_version"] == PROJECT_FORMAT_VERSION == 7
    with pytest.raises(ValueError, match="newer than this build"):
        migrate_project_document(document, target_version=COMPATIBLE_PROJECT_FORMAT_VERSION)
    assert Project.load(path).to_dict() == document
    assert project.instruments[0].plugin == plugin_spec()
    assert project.instruments[1].plugin is None

    instrument.plugin = None
    compatible = project.to_dict()
    assert compatible["format_version"] == COMPATIBLE_PROJECT_FORMAT_VERSION == 6
    assert all("plugin" not in item for item in compatible["instruments"])


@pytest.mark.parametrize("version", range(7))
def test_legacy_songs_stay_compatible_without_new_plugin_keys(version):
    source = {"format_version": version, "name": "Legacy song"}
    original = deepcopy(source)
    project = Project.from_dict(source)
    instrument = project.add_instrument("Native instrument", replace(project.synth))
    instrument.patch.cutoff = 1234
    project.plugins["instrument"] = plugin_spec()  # Existing primary slot is format 6.
    payload = project.to_dict()

    assert source == original
    assert payload["format_version"] == COMPATIBLE_PROJECT_FORMAT_VERSION
    assert "plugin" not in payload["instruments"][0]
    assert Project.from_dict(payload).to_dict() == payload
    assert migrate_project_document(payload, target_version=6) is payload


def test_format_six_to_seven_is_an_explicit_nondestructive_identity_migration():
    source = Project().to_dict()
    source["extension"] = {"keep": [1, 2]}
    original = deepcopy(source)
    migrated = migrate_project_document(source, target_version=PROJECT_FORMAT_VERSION)
    assert source == original
    assert migrated == {**source, "format_version": 7}
    assert migrated is not source
    assert migrated["extension"] is not source["extension"]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node required for browser schema check")
def test_actual_desktop_documents_roundtrip_or_fail_closed_in_browser():
    ordinary = Project(name="Browser-compatible song")
    ordinary.add_instrument("Native patch", replace(ordinary.synth)).patch.cutoff = 1234
    compatible = ordinary.to_dict()
    ordinary.instruments[0].plugin = plugin_spec()
    plugin = ordinary.to_dict()
    script = """
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const sandbox = {window: {}, crypto: require('node:crypto').webcrypto};
vm.runInNewContext(fs.readFileSync('website/app/project-model.js', 'utf8'), sandbox);
const {ProjectStore} = sandbox.window.AnharmonicProject;
const documents = JSON.parse(fs.readFileSync(0, 'utf8'));
const store = new ProjectStore(documents.compatible);
const saved = store.toJSON();
assert.equal(saved.format_version, 6);
assert.equal(saved.instruments[0].patch.cutoff, 1234);
assert.equal(Object.hasOwn(saved.instruments[0], 'plugin'), false);
const before = JSON.stringify(saved);
assert.throws(() => store.load(documents.plugin));
assert.equal(JSON.stringify(store.toJSON()), before);
process.stdout.write(JSON.stringify(saved));
"""
    result = subprocess.run(
        ["node", "-e", script],
        input=json.dumps({"compatible": compatible, "plugin": plugin}),
        text=True,
        capture_output=True,
        cwd=Path(__file__).resolve().parents[1],
        timeout=15,
        check=True,
    )
    restored = Project.from_dict(json.loads(result.stdout))
    assert restored.name == ordinary.name
    assert restored.instruments[0].patch.cutoff == 1234
    assert restored.instruments[0].plugin is None
    assert restored.to_dict()["format_version"] == COMPATIBLE_PROJECT_FORMAT_VERSION

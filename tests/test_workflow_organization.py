from __future__ import annotations

import json

import pytest

from mpclab.model import Clip, Project
from mpclab.workflow_organization import (
    create_track_folder,
    folders,
    install_organization_state,
    load_project_template,
    save_project_template,
    validate_track_folders,
)
from mpclab.workflow_state import install_project_workflow_state


def install_state():
    install_project_workflow_state()
    install_organization_state()


def test_track_folder_roundtrips_without_weakening_workflow_validation():
    install_state()
    project = Project()
    folder = create_track_folder(project, "Rhythm", [project.tracks[0].id, project.tracks[1].id])
    folder["collapsed"] = True
    payload = project.to_dict()
    assert payload["workflow"]["track_folders"][0]["name"] == "Rhythm"
    restored = Project.from_dict(payload)
    assert folders(restored) == [folder]


def test_track_folder_rejects_missing_tracks_and_unbounded_members():
    project = Project()
    with pytest.raises(ValueError, match="missing mixer track"):
        validate_track_folders(
            [{"id": "folder", "name": "Bad", "members": ["missing"], "collapsed": False}],
            project,
        )
    with pytest.raises(ValueError, match="at most"):
        validate_track_folders([{}] * 65, project)


class DummyWindow:
    def __init__(self, root, project):
        self.root = root
        self.project = project
        self.snapshots = 0
        self.dirty = False
        self.applied = []

    def snapshot(self):
        self.snapshots += 1

    def _apply_project(self, project):
        self.project = project
        self.applied.append(project)

    def _set_dirty(self, value):
        self.dirty = bool(value)


def test_project_template_is_atomic_structure_only_and_reusable(tmp_path):
    install_state()
    project = Project(name="Song", bpm=123)
    project.pads[0].sample_id = "kick-audio"
    project.pads[0].name = "Kick"
    project.pattern().steps = {0: {0: 1.0}}
    project.rows[0].clips.append(
        Clip(kind="audio", ref="kick-audio", start_beat=0, length_beats=1, source_length=0.5)
    )
    project.tracks[0].name = "KICK BUS"
    create_track_folder(project, "Drums", [project.tracks[0].id])
    window = DummyWindow(tmp_path, project)

    path = save_project_template(window, "House starter")
    document = json.loads(path.read_text(encoding="utf-8"))
    saved = Project.from_dict(document["project"])
    assert saved.bpm == 123
    assert saved.tracks[0].name == "KICK BUS"
    assert folders(saved)[0]["name"] == "Drums"
    assert all(pad.sample_id is None for pad in saved.pads)
    assert all(not pattern.steps and not pattern.notes for pattern in saved.patterns)
    assert all(not row.clips for row in saved.rows)

    window.project = Project()
    loaded = load_project_template(window, path)
    assert loaded.name == "untitled"
    assert loaded.tracks[0].name == "KICK BUS"
    assert window.applied == [loaded]
    assert window.snapshots == 1 and window.dirty


def test_template_loader_rejects_oversize_and_bad_format(tmp_path):
    install_state()
    window = DummyWindow(tmp_path, Project())
    bad = tmp_path / "bad.json"
    bad.write_text('{"template_format":2,"project":{}}', encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported"):
        load_project_template(window, bad)

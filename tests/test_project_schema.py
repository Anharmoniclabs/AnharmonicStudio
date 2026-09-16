"""Authoritative desktop project schema and extension roundtrips."""

from __future__ import annotations

import pytest

from mpclab.model import PROJECT_FORMAT_VERSION, Project
from mpclab.project_schema import EXTENSION_FIELDS
from mpclab.workflow_state import install_project_workflow_state


def test_current_version_is_content_independent():
    empty = Project().to_dict()
    with_instrument = Project()
    with_instrument.add_instrument("Second", with_instrument.synth)
    assert empty["format_version"] == PROJECT_FORMAT_VERSION == 6
    assert with_instrument.to_dict()["format_version"] == PROJECT_FORMAT_VERSION


def test_all_optional_project_fields_roundtrip_without_install_order():
    project = Project(name="schema roundtrip")
    project.workflow = {"recording": {"loop_takes": True}}
    project.pro_daw = {"plugin_chains": {}}
    project.automation_control = {"modes": {"master": "touch"}}
    project.timeline_markers = {"version": 1, "items": []}
    project.midi_files = {"version": 1, "sources": []}
    project.track_folders = [
        {
            "id": "folder",
            "name": "Folder",
            "members": [project.tracks[0].id],
            "collapsed": False,
        }
    ]

    payload = project.to_dict()
    assert payload["workflow"]["recording"]["loop_takes"] is True
    assert payload["workflow"]["track_folders"][0]["id"] == "folder"
    restored = Project.from_dict(payload)
    assert restored.track_folders[0]["members"] == [project.tracks[0].id]
    assert restored.automation_control["modes"]["master"] == "touch"
    assert "timeline_markers" not in payload
    assert "midi_files" not in payload

    before = Project.to_dict
    install_project_workflow_state()
    assert Project.to_dict is before
    assert set(EXTENSION_FIELDS) == {
        "workflow",
        "pro_daw",
        "automation_control",
        "timeline_markers",
        "midi_files",
        "track_folders",
    }


@pytest.mark.parametrize(
    "document",
    [
        {"bpm": 0},
        {"format_version": 6, "self_choke": "false"},
        {
            "format_version": 6,
            "workflow": {"recording": {"punch_enabled": True, "punch_start": 2, "punch_end": 1}},
        },
        {"format_version": 6, "automation_control": {"modes": {"master": "invalid"}}},
    ],
)
def test_malformed_extension_and_core_values_fail_at_boundary(document):
    with pytest.raises(ValueError):
        Project.from_dict(document)

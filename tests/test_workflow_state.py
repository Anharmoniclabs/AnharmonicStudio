from __future__ import annotations

import pytest

from mpclab.model import PROJECT_FORMAT_VERSION, Project
from mpclab.workflow_state import ensure_workflow, install_project_workflow_state, validate_workflow


def test_optional_workflow_metadata_round_trips_without_format_bump():
    install_project_workflow_state()
    project = Project()
    workflow = ensure_workflow(project)
    workflow["groups"] = [
        {
            "id": "group-a",
            "name": "DRUM BUS",
            "members": [project.tracks[0].id, project.tracks[1].id],
            "gain": 0.8,
            "mute": False,
        }
    ]
    workflow["sidechains"] = [
        {
            "source": project.tracks[0].id,
            "target": project.tracks[1].id,
            "amount": 5.0,
            "threshold": 0.04,
            "enabled": True,
        }
    ]
    workflow["routing"] = {
        "buses": [
            {
                "id": "bus:drums",
                "name": "DRUM AUX",
                "gain": 0.9,
                "pan": 0.0,
                "mute": False,
                "output": "master",
            }
        ],
        "track_outputs": {project.tracks[0].id: "bus:drums"},
        "sends": [
            {
                "id": "send:parallel",
                "source": project.tracks[1].id,
                "target": "bus:drums",
                "gain": 0.25,
                "pre_fader": True,
                "enabled": True,
            }
        ],
    }

    payload = project.to_dict()
    assert payload["format_version"] == PROJECT_FORMAT_VERSION
    assert "workflow" in payload

    reopened = Project.from_dict(payload)
    assert reopened.workflow["groups"][0]["name"] == "DRUM BUS"
    assert reopened.workflow["sidechains"][0]["amount"] == 5.0
    assert reopened.workflow["routing"]["buses"][0]["name"] == "DRUM AUX"
    assert reopened.workflow["routing"]["track_outputs"][project.tracks[0].id] == "bus:drums"
    assert reopened.workflow["routing"]["sends"][0]["pre_fader"] is True


def test_old_project_without_workflow_stays_valid():
    install_project_workflow_state()
    payload = Project().to_dict()
    payload.pop("workflow", None)
    reopened = Project.from_dict(payload)
    assert ensure_workflow(reopened) == {}


def test_workflow_validation_rejects_unbounded_or_unknown_data():
    with pytest.raises(ValueError, match="unsupported"):
        validate_workflow({"unknown": []})


def test_routing_validation_rejects_missing_targets_and_cycles():
    with pytest.raises(ValueError, match="output does not exist"):
        validate_workflow(
            {"routing": {"buses": [{"id": "a", "name": "A", "output": "missing"}]}}
        )

    with pytest.raises(ValueError, match="acyclic"):
        validate_workflow(
            {
                "routing": {
                    "buses": [
                        {"id": "a", "name": "A", "output": "b"},
                        {"id": "b", "name": "B", "output": "a"},
                    ]
                }
            }
        )

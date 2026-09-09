from __future__ import annotations

from mpclab.model import Project
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

    payload = project.to_dict()
    assert payload["format"] == 5
    assert "workflow" in payload

    reopened = Project.from_dict(payload)
    assert reopened.workflow["groups"][0]["name"] == "DRUM BUS"
    assert reopened.workflow["sidechains"][0]["amount"] == 5.0


def test_old_project_without_workflow_stays_valid():
    install_project_workflow_state()
    payload = Project().to_dict()
    payload.pop("workflow", None)
    reopened = Project.from_dict(payload)
    assert ensure_workflow(reopened) == {}


def test_workflow_validation_rejects_unbounded_or_unknown_data():
    try:
        validate_workflow({"unknown": []})
    except ValueError as exc:
        assert "unsupported" in str(exc)
    else:
        raise AssertionError("unknown workflow key should be rejected")

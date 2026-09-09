from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from mpclab.model import Project
from mpclab.workflow_mixing import advanced_track_controls
from mpclab.workflow_state import ensure_workflow, install_project_workflow_state


def engine_for(project):
    return SimpleNamespace(project=project, meters=np.zeros(len(project.tracks), dtype=np.float32))


def test_group_gain_multiplies_existing_track_controls():
    install_project_workflow_state()
    project = Project()
    project.tracks[0].gain = 0.8
    ensure_workflow(project)["groups"] = [
        {
            "id": "group-a",
            "name": "DRUMS",
            "members": [project.tracks[0].id],
            "gain": 0.5,
            "mute": False,
        }
    ]
    left, right = advanced_track_controls(engine_for(project), 0, None)
    assert left == 0.4
    assert right == 0.4


def test_group_mute_silences_members_without_mutating_track():
    project = Project()
    ensure_workflow(project)["groups"] = [
        {
            "id": "group-a",
            "name": "DRUMS",
            "members": [project.tracks[0].id],
            "gain": 1.0,
            "mute": True,
        }
    ]
    left, right = advanced_track_controls(engine_for(project), 0, None)
    assert (left, right) == (0.0, 0.0)
    assert not project.tracks[0].mute


def test_sidechain_duck_uses_previous_source_meter():
    project = Project()
    ensure_workflow(project)["sidechains"] = [
        {
            "source": project.tracks[0].id,
            "target": project.tracks[1].id,
            "amount": 8.0,
            "threshold": 0.05,
            "enabled": True,
        }
    ]
    engine = engine_for(project)
    dry_left, _ = advanced_track_controls(engine, 1, None)
    engine.meters[0] = 0.5
    ducked_left, _ = advanced_track_controls(engine, 1, None)
    assert dry_left == 1.0
    assert 0.0 < ducked_left < dry_left

from __future__ import annotations

import numpy as np
import pytest

from mpclab.model import Project
from mpclab.workflow_routing import (
    MAX_ROUTING_BUSES,
    clear_bus_buffers,
    compile_routing,
    finish_buses,
    route_track,
)


def test_default_project_routes_tracks_directly_to_master():
    project = Project()
    plan = compile_routing(project)
    assert not plan.active
    assert not plan.buses
    assert set(plan.track_outputs) == {-1}


def test_track_bus_and_pre_fader_send_sum_in_topological_order():
    project = Project()
    project.workflow = {
        "routing": {
            "buses": [
                {"id": "a", "name": "A", "gain": 0.5, "pan": 0.0, "mute": False, "output": "b"},
                {
                    "id": "b",
                    "name": "B",
                    "gain": 1.0,
                    "pan": 0.0,
                    "mute": False,
                    "output": "master",
                },
            ],
            "track_outputs": {project.tracks[0].id: "a"},
            "sends": [
                {
                    "id": "parallel",
                    "source": project.tracks[0].id,
                    "target": "b",
                    "gain": 0.5,
                    "pre_fader": True,
                    "enabled": True,
                }
            ],
        }
    }
    plan = compile_routing(project)
    buses = np.zeros((MAX_ROUTING_BUSES, 4, 2), dtype=np.float32)
    master = np.zeros((4, 2), dtype=np.float32)
    scratch = np.zeros((4, 2), dtype=np.float32)
    pre = np.full((4, 2), 2.0, dtype=np.float32)
    post = np.ones((4, 2), dtype=np.float32)

    clear_bus_buffers(plan, buses, 4)
    route_track(plan, 0, pre, post, master, buses, scratch)
    finish_buses(plan, master, buses, scratch, 4)

    # 1.0 post-fader to A -> 0.5 after A fader, plus 1.0 pre-fader send to B.
    np.testing.assert_allclose(master, 1.5, atol=1e-6)


def test_bus_cycle_is_rejected_before_realtime_use():
    project = Project()
    project.workflow = {
        "routing": {
            "buses": [
                {"id": "a", "name": "A", "output": "b"},
                {"id": "b", "name": "B", "output": "a"},
            ]
        }
    }
    with pytest.raises(ValueError, match="acyclic"):
        compile_routing(project)


def test_routing_bus_limit_is_bounded():
    project = Project()
    project.workflow = {
        "routing": {
            "buses": [
                {"id": f"bus-{index}", "name": str(index), "output": "master"}
                for index in range(MAX_ROUTING_BUSES + 1)
            ]
        }
    }
    with pytest.raises(ValueError, match="at most"):
        compile_routing(project)

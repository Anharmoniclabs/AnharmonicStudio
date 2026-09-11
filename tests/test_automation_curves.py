from __future__ import annotations

import numpy as np
import pytest

from mpclab.model import Project
from mpclab.music import AutomationLane, AutomationPoint


def test_smooth_curve_preserves_endpoints_and_eases_each_segment():
    lane = AutomationLane(
        points=[AutomationPoint(0, 0), AutomationPoint(4, 1)],
        interpolation="smooth",
    )
    np.testing.assert_allclose(
        lane.values([-1, 0, 1, 2, 3, 4, 5]),
        [0, 0, 0.15625, 0.5, 0.84375, 1, 1],
    )


def test_smooth_curve_uses_each_neighboring_segment_independently():
    lane = AutomationLane(
        points=[AutomationPoint(0, 0), AutomationPoint(2, 1), AutomationPoint(6, 0.5)],
        interpolation="smooth",
    )
    np.testing.assert_allclose(lane.values([1, 2, 4, 6]), [0.5, 1, 0.75, 0.5])


def test_smooth_curve_roundtrips_through_project_format(tmp_path):
    project = Project()
    project.automation = [
        AutomationLane(
            "master",
            [AutomationPoint(0, 0.2), AutomationPoint(4, 1.0)],
            interpolation="smooth",
        )
    ]
    path = tmp_path / "smooth.json"
    project.save(path)
    loaded = Project.load(path)
    assert loaded.automation[0].interpolation == "smooth"
    np.testing.assert_allclose(loaded.automation[0].values([0, 2, 4]), [0.2, 0.6, 1.0])


def test_unknown_automation_curve_is_rejected_explicitly():
    with pytest.raises(ValueError, match="linear, step or smooth"):
        Project.from_dict(
            {
                "automation": [
                    {
                        "target": "master",
                        "interpolation": "bezier",
                        "points": [{"beat": 0, "value": 1}],
                    }
                ]
            }
        )


@pytest.mark.parametrize(
    ("lane", "message"),
    [
        ({"enabled": "false"}, "automation enabled must be a boolean"),
        ({"unexpected": 1}, "automation lane contains unsupported fields"),
        (
            {"points": [{"beat": 0, "value": 1, "unexpected": 2}]},
            "automation point contains unsupported fields",
        ),
        ({"points": [{"beat": None, "value": 1}]}, "automation point is invalid"),
    ],
)
def test_malformed_automation_json_fails_without_coercion_or_internal_exceptions(lane, message):
    with pytest.raises(ValueError, match=message):
        Project.from_dict({"automation": [lane]})

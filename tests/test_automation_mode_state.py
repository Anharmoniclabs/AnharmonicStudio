import pytest

from mpclab.automation_mode_state import (
    automation_mode,
    install_automation_mode_state,
    set_automation_mode,
    validate_automation_control,
)
from mpclab.model import Project
from mpclab.premium_workflows import install_premium_runtime


def test_automation_modes_roundtrip_without_format_bump(tmp_path):
    install_premium_runtime()
    install_automation_mode_state()
    project = Project()
    set_automation_mode(project, "track:0:gain", "touch")
    set_automation_mode(project, "track:0:pan", "latch")
    path = tmp_path / "automation.json"
    project.save(path)
    payload = project.to_dict()
    assert payload["format_version"] == 5
    restored = Project.load(path)
    assert automation_mode(restored, "track:0:gain") == "touch"
    assert automation_mode(restored, "track:0:pan") == "latch"
    assert automation_mode(restored, "master") == "read"


def test_read_mode_is_default_and_not_serialized():
    project = Project()
    set_automation_mode(project, "master", "read")
    assert validate_automation_control(project.automation_control) == {}
    assert automation_mode(project, "master") == "read"


@pytest.mark.parametrize(
    "state",
    [
        {"modes": {"unknown": "touch"}},
        {"modes": {"master": "overwrite-everything"}},
        {"modes": []},
        {"future": {}},
    ],
)
def test_automation_mode_state_rejects_invalid_or_unbounded_shapes(state):
    with pytest.raises(ValueError):
        validate_automation_control(state)

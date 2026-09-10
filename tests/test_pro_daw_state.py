import base64

import pytest

from mpclab.model import Project
from mpclab.pro_daw_state import install_pro_daw_state, validate_pro_daw
from mpclab.workflow_state import install_project_workflow_state


def _spec(path="/plugins/Test.vst3"):
    return {
        "path": path,
        "plugin_name": "Test",
        "parameters": {"1": 0.5},
        "state": base64.b64encode(b"preset").decode(),
        "bypass": False,
    }


def test_plugin_chain_sidecar_roundtrips_without_project_format_bump(tmp_path):
    install_project_workflow_state()
    install_pro_daw_state()
    project = Project()
    project.pro_daw = {
        "plugin_chains": {
            f"track:{project.tracks[0].id}": [_spec(), _spec("/plugins/Second.vst3")],
            "master": [_spec("/plugins/Master.component")],
        }
    }
    path = tmp_path / "song.json"
    project.save(path)
    payload = project.to_dict()
    assert payload["format_version"] == 5
    restored = Project.load(path)
    assert restored.pro_daw == validate_pro_daw(project.pro_daw)


def test_pro_daw_sidecar_is_optional_for_legacy_projects():
    install_project_workflow_state()
    install_pro_daw_state()
    project = Project.from_dict({"format_version": 5})
    assert project.pro_daw == {}


@pytest.mark.parametrize(
    "value, message",
    [
        ({"plugin_chains": {"unknown": [_spec()]}}, "target"),
        ({"plugin_chains": {"master": [_spec()] * 9}}, "at most 8"),
        ({"plugin_chains": {"master": [_spec("bad.clap")]}}, "VST3 or Audio Unit"),
        (
            {"plugin_chains": {"master": [{**_spec(), "parameters": {"1": float("nan")}}]}},
            "normalized finite",
        ),
        ({"future": {}}, "unsupported"),
    ],
)
def test_pro_daw_sidecar_rejects_unbounded_or_unsupported_state(value, message):
    with pytest.raises(ValueError, match=message):
        validate_pro_daw(value)

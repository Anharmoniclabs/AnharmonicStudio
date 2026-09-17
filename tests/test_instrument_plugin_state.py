import base64

import pytest

from mpclab.model import Project, SynthPatch
from mpclab.instrument_plugin_state import validate_instrument_plugins


def spec(path="owned.vst3"):
    return {
        "path": path,
        "plugin_name": "Owned",
        "parameters": {"1": 0.5},
        "state": base64.b64encode(b"state").decode(),
        "bypass": False,
    }


def test_instrument_plugins_roundtrip_by_stable_id():
    project = Project()
    instrument = project.add_instrument("Hosted", SynthPatch())
    project.instrument_plugins[instrument.id] = spec()
    loaded = Project.from_dict(project.to_dict())
    assert loaded.instrument_plugins == {instrument.id: spec()}


def test_instrument_plugins_reject_unknown_owner():
    project = Project()
    with pytest.raises(ValueError, match="unknown instrument"):
        validate_instrument_plugins({"missing": spec()}, project)


def test_instrument_plugins_reuse_plugin_spec_validation():
    project = Project()
    instrument = project.add_instrument("Hosted", SynthPatch())
    bad = spec("unsafe.txt")
    with pytest.raises(ValueError, match="VST3 or Audio Unit"):
        validate_instrument_plugins({instrument.id: bad}, project)

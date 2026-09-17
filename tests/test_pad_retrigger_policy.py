import numpy as np

from mpclab.engine import Engine, FADE
from mpclab.model import Pad, Project

SR = 8000


class MemoryLibrary:
    def __init__(self):
        self.data = np.full((SR, 2), 0.1, dtype=np.float32)

    def audio(self, ref):
        return self.data


def engine_with_policy(policy):
    engine = Engine(MemoryLibrary(), sample_rate=SR, blocksize=128)
    engine.project.self_choke = False
    engine.project.pads[0] = Pad(
        sample_id="sample",
        start=0,
        end=1,
        mode="one-shot",
        retrigger=policy,
    )
    return engine


def test_retrigger_restart_releases_previous_voice():
    engine = engine_with_policy("restart")
    pad = engine.project.pads[0]
    engine._spawn(pad, 0, 1)
    first = engine.voices[0]
    engine._spawn(pad, 0, 1)
    assert len(engine.voices) == 2
    assert first.length == max(1, int(FADE * SR))


def test_retrigger_layer_preserves_previous_voice():
    engine = engine_with_policy("layer")
    pad = engine.project.pads[0]
    engine._spawn(pad, 0, 1)
    first = engine.voices[0]
    natural = first.length
    engine._spawn(pad, 0, 1)
    assert len(engine.voices) == 2
    assert first.length == natural


def test_retrigger_ignore_drops_new_hit_and_traces_reason():
    engine = engine_with_policy("ignore")
    engine.set_audio_trace_enabled(True, clear=True)
    pad = engine.project.pads[0]
    engine._spawn(pad, 0, 1)
    first = engine.voices[0]
    engine._spawn(pad, 0, 1)
    assert engine.voices == [first]
    ignored = [event for event in engine.audio_trace_snapshot() if event.kind == "pad_ignore"]
    assert len(ignored) == 1
    assert ignored[0].voice == id(first)
    assert ignored[0].reason == "ignore_retrigger"


def test_retrigger_crossfade_releases_old_and_fades_in_new_voice():
    engine = engine_with_policy("crossfade")
    pad = engine.project.pads[0]
    engine._spawn(pad, 0, 1)
    first = engine.voices[0]
    engine._spawn(pad, 0, 1)
    second = engine.voices[-1]
    fade = max(1, int(FADE * SR))
    assert first.length == fade
    assert second.attack >= fade


def test_legacy_pad_retrigger_defaults_preserve_old_mode_semantics():
    project = Project()
    document = project.to_dict()
    document["pads"][0].pop("retrigger", None)
    document["pads"][0]["mode"] = "one-shot"
    document["pads"][1].pop("retrigger", None)
    document["pads"][1]["mode"] = "gate"
    loaded = Project.from_dict(document)
    assert loaded.pads[0].retrigger == "layer"
    assert loaded.pads[1].retrigger == "restart"


def test_retrigger_policy_roundtrips_in_project():
    project = Project()
    project.pads[0].retrigger = "crossfade"
    loaded = Project.from_dict(project.to_dict())
    assert loaded.pads[0].retrigger == "crossfade"


def test_ignore_does_not_override_explicit_choke_group():
    engine = engine_with_policy("ignore")
    pad = engine.project.pads[0]
    pad.choke = 2
    engine._spawn(pad, 0, 1)
    first = engine.voices[0]
    engine._spawn(pad, 0, 1)
    assert len(engine.voices) == 2
    assert first.length == max(1, int(FADE * SR))


def test_ignore_does_not_override_global_self_choke():
    engine = engine_with_policy("ignore")
    engine.project.self_choke = True
    pad = engine.project.pads[0]
    engine._spawn(pad, 0, 1)
    first = engine.voices[0]
    engine._spawn(pad, 0, 1)
    assert len(engine.voices) == 2
    assert first.length == max(1, int(FADE * SR))

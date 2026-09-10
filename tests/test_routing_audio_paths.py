from __future__ import annotations

import numpy as np

from mpclab.engine import Engine
from mpclab.model import Project
from mpclab.premium_workflows import install_premium_runtime
from mpclab.workflow_state import ensure_workflow


class _Clip:
    def __init__(self, clip_id, duration):
        self.id = clip_id
        self.name = "routing tone"
        self.duration = duration


class _Library:
    def __init__(self, sample_rate=8_000, frames=8_000):
        self.sr = sample_rate
        self._audio = np.full((frames, 2), 0.05, dtype=np.float32)
        self.clips = {"tone": _Clip("tone", frames / sample_rate)}

    def audio(self, _clip_id):
        return self._audio

    def reversed_audio(self, _clip_id):
        return self._audio[::-1]

    def peaks(self, _clip_id):
        return None


def _engine(*, routed: bool) -> Engine:
    install_premium_runtime()
    engine = Engine(_Library(), sample_rate=8_000, blocksize=128)
    project = Project(bpm=120.0)
    project.master = 1.0
    project.tracks[0].gain = 1.0
    project.pads[0].sample_id = "tone"
    project.pads[0].end = 0.5
    project.pads[0].track = 0
    project.pattern().bars = 1
    project.pattern().steps[0] = {0: 1.0}
    if routed:
        ensure_workflow(project)["routing"] = {
            "buses": [
                {
                    "id": "bus:half",
                    "name": "HALF",
                    "gain": 0.5,
                    "pan": 0.0,
                    "mute": False,
                    "output": "master",
                }
            ],
            "track_outputs": {project.tracks[0].id: "bus:half"},
        }
    engine.project = project
    engine.prepare_fx(project)
    return engine


def _one_live_block(engine: Engine) -> np.ndarray:
    engine.trigger_pad(0)
    out = np.zeros((engine.blocksize, 2), dtype=np.float32)
    engine._callback(out, engine.blocksize, None, False)
    return out


def test_realtime_bus_gain_changes_actual_callback_audio():
    direct = _one_live_block(_engine(routed=False))
    routed = _one_live_block(_engine(routed=True))
    np.testing.assert_allclose(routed, direct * 0.5, rtol=2e-5, atol=2e-6)


def test_streaming_export_and_reference_both_follow_bus_graph():
    direct = _engine(routed=False).render_offline(mode="pattern", tail=0.0)
    routed_engine = _engine(routed=True)
    routed = routed_engine.render_offline(mode="pattern", tail=0.0)
    reference = routed_engine._render_offline_reference(mode="pattern", tail=0.0)

    np.testing.assert_allclose(routed, direct * 0.5, rtol=3e-4, atol=3e-5)
    np.testing.assert_allclose(reference, routed, rtol=3e-4, atol=3e-5)

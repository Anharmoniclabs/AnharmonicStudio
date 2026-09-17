from __future__ import annotations

import numpy as np
import pytest

from mpclab.daw_expansion_state import default_daw_expansion, validate_daw_expansion
from mpclab.model import Project, SynthPatch
from mpclab.physical_model import FDNReverb
from mpclab.pro_audio_graph import (
    Float64Accumulator,
    GraphNode,
    HardwarePatchbay,
    MultichannelTrackStore,
    ParallelGraphScheduler,
)
from mpclab.read_ahead import ReadAheadManager


def test_daw_expansion_round_trips_through_project_schema():
    project = Project()
    instrument = project.add_instrument("Vector", SynthPatch(track=2))
    source = project.tracks[3].id
    target = project.tracks[0].id
    state = default_daw_expansion()
    state.update(sample_rate=96_000, summation_precision="float64", parallel_workers=3)
    state["streaming"] = {
        "enabled": True,
        "read_ahead_frames": 131_072,
        "request_capacity": 128,
    }
    state["sidechains"] = [
        {
            "id": "kick-duck",
            "source": f"track:{source}",
            "target": f"track:{target}",
            "slot": 0,
            "gain": 0.75,
            "pre_fader": True,
            "enabled": True,
        }
    ]
    state["track_channels"] = {source: 4}
    state["hardware_patchbay"] = {"master": [0, 1], f"track:{source}": [2, 3, 4, 5]}
    state["instrument_engines"] = {
        instrument.id: {
            "type": "vector",
            "enabled": True,
            "state": {
                "sources": ["sine", "saw", "square", "triangle"],
                "x": 0.25,
                "y": 0.75,
                "gesture": [{"at": 0.0, "x": 0.1, "y": 0.2}, {"at": 1.0, "x": 0.9, "y": 0.8}],
                "gesture_loop": True,
            },
        }
    }
    project.daw_expansion = validate_daw_expansion(state, project=project)

    restored = Project.from_dict(project.to_dict())
    assert restored.daw_expansion == project.daw_expansion
    assert restored.daw_expansion["sample_rate"] == 96_000
    assert restored.daw_expansion["track_channels"][source] == 4
    assert restored.daw_expansion["instrument_engines"][instrument.id]["type"] == "vector"


def test_sidechain_state_rejects_sources_without_a_realtime_audio_tap():
    project = Project()
    state = default_daw_expansion()
    state["sidechains"] = [
        {
            "id": "invalid-source",
            "source": "master",
            "target": f"track:{project.tracks[0].id}",
            "slot": 0,
            "gain": 1.0,
            "pre_fader": False,
            "enabled": True,
        }
    ]
    with pytest.raises(ValueError, match="source"):
        validate_daw_expansion(state, project=project)


def test_read_ahead_warms_memmap_and_pause_is_a_true_noop(tmp_path):
    path = tmp_path / "large-audio.f32"
    audio = np.memmap(path, dtype=np.float32, mode="w+", shape=(32_768, 2))
    audio[:, 0] = np.linspace(-1, 1, len(audio), dtype=np.float32)
    audio[:, 1] = audio[:, 0][::-1]
    audio.flush()

    manager = ReadAheadManager(read_ahead_frames=8192, capacity=32)
    manager.register("clip", audio)
    manager.start()
    try:
        assert manager.request("clip", 4096)
        assert manager.wait_idle()
        before = manager.stats()
        assert before.completed >= 1
        assert before.warmed_frames >= 8192
        assert before.running

        manager.pause()
        assert not manager.running
        assert not manager.request("clip", 16_000)
        assert manager.stats().registered == 1

        manager.start()
        assert manager.request("clip", 16_000, reverse=True)
        assert manager.wait_idle()
        assert manager.stats().completed > before.completed
    finally:
        manager.close()


def test_fdn_is_finite_has_a_tail_and_obeys_processor_lifecycle():
    fdn = FDNReverb(feedback=0.74, damping=0.3, wet=0.5)
    fdn.prepare(48_000, 128, 2)
    block = np.zeros((128, 2), dtype=np.float32)
    block[0] = 1.0
    energy = 0.0
    for _ in range(32):
        fdn.process(block)
        assert np.isfinite(block).all()
        energy += float(np.sum(block * block))
        block.fill(0.0)
    assert energy > 0.0
    assert fdn.latency_samples() == 0
    assert fdn.tail_samples() > 128
    saved = fdn.save_state()
    fdn.restore_state(saved)
    fdn.reset()


def test_float64_multichannel_patchbay_and_parallel_scheduler_are_functional():
    accumulator = Float64Accumulator(4, 2)
    accumulator.clear(4)
    tiny = np.full((4, 2), 1e-7, dtype=np.float32)
    for _ in range(1000):
        accumulator.add(tiny)
    out = np.zeros((4, 2), dtype=np.float32)
    accumulator.to_float32(out)
    np.testing.assert_allclose(out, 1e-4, rtol=2e-5, atol=1e-8)

    store = MultichannelTrackStore(("drums", "ambience"), {"ambience": 6}, 4)
    stereo = np.arange(8, dtype=np.float32).reshape(4, 2)
    block = store.ingest_stereo("ambience", stereo)
    assert block.shape == (4, 6)
    np.testing.assert_array_equal(block[:, :2], stereo)
    np.testing.assert_array_equal(block[:, 2:], 0.0)

    patchbay = HardwarePatchbay({"master": [0, 1], "surround": [2, 3, 4, 5]})
    output = np.zeros((4, 6), dtype=np.float32)
    surround = np.ones((4, 4), dtype=np.float32)
    patchbay.render({"master": stereo, "surround": surround}, output)
    np.testing.assert_array_equal(output[:, :2], stereo)
    np.testing.assert_array_equal(output[:, 2:], surround)

    scheduler = ParallelGraphScheduler(workers=2)
    nodes = [
        GraphNode("a", (), lambda _state: 2),
        GraphNode("b", ("a",), lambda state: state["a"] + 3),
        GraphNode("c", ("a",), lambda state: state["a"] * 5),
        GraphNode("d", ("b", "c"), lambda state: state["b"] + state["c"]),
    ]
    state = scheduler.run(nodes, {})
    assert state["d"] == 15
    with pytest.raises(ValueError, match="cycle"):
        scheduler.layers(
            [
                GraphNode("x", ("y",), lambda _state: 0),
                GraphNode("y", ("x",), lambda _state: 0),
            ]
        )

import numpy as np

from mpclab.model import Project
from mpclab.plugin_chain_runtime import (
    LivePluginChains,
    OfflinePluginChains,
    RoutingDelayBank,
    compile_chain_latency_plan,
)
from mpclab.workflow_routing import compile_routing, finish_buses, route_track


def _spec(path="/plugins/Test.vst3", *, bypass=False):
    return {
        "path": path,
        "plugin_name": "Test",
        "parameters": {},
        "state": "",
        "bypass": bypass,
    }


def test_nested_bus_latency_is_aligned_at_each_summing_junction():
    project = Project()
    t0, t1, t2 = project.tracks[:3]
    project.workflow = {
        "routing": {
            "buses": [
                {"id": "a", "name": "A", "gain": 1.0, "pan": 0.0, "mute": False, "output": "b"},
                {"id": "b", "name": "B", "gain": 1.0, "pan": 0.0, "mute": False, "output": "master"},
            ],
            "track_outputs": {t0.id: "a", t1.id: "b"},
        }
    }
    plan = compile_routing(project)
    latency = compile_chain_latency_plan(
        plan,
        {
            f"track:{t0.id}": 20,
            f"track:{t1.id}": 10,
            f"track:{t2.id}": 0,
            "bus:a": 30,
            "bus:b": 40,
            "master": 50,
        },
    )

    # A arrives at B at sample 50; direct Track 2 arrives there at sample 10.
    assert latency.edge_delays[f"track-output:{t1.id}"] == 40
    assert latency.node_latencies["bus:a"] == 50
    assert latency.node_latencies["bus:b"] == 90
    # Every direct-to-master track is delayed to B's 90-sample arrival.
    assert latency.edge_delays[f"track-output:{t2.id}"] == 90
    assert latency.master_input_latency == 90
    assert latency.output_latency == 140


def test_parallel_send_and_direct_path_get_independent_compensation():
    project = Project()
    track = project.tracks[0]
    project.workflow = {
        "routing": {
            "buses": [
                {
                    "id": "parallel",
                    "name": "Parallel",
                    "gain": 1.0,
                    "pan": 0.0,
                    "mute": False,
                    "output": "master",
                }
            ],
            "sends": [
                {
                    "id": "p",
                    "source": track.id,
                    "target": "parallel",
                    "gain": 1.0,
                    "pre_fader": False,
                    "enabled": True,
                }
            ],
        }
    }
    plan = compile_routing(project)
    latency = compile_chain_latency_plan(plan, {"bus:parallel": 64})
    assert latency.edge_delays[f"track-output:{track.id}"] == 64
    assert "send:p" not in latency.edge_delays
    assert latency.master_input_latency == 64


def test_routing_delay_bank_is_sample_exact_across_uneven_blocks():
    project = Project()
    plan = compile_routing(project)
    latency = compile_chain_latency_plan(plan, {f"track:{project.tracks[0].id}": 3})
    bank = RoutingDelayBank(8)
    bank.configure(latency)

    edge = f"track-output:{project.tracks[1].id}"
    source = np.arange(20, dtype=np.float32).reshape(10, 2)
    pieces = []
    offset = 0
    for frames in (2, 5, 3):
        pieces.append(bank.process(edge, source[offset : offset + frames]).copy())
        offset += frames
    output = np.concatenate(pieces)
    expected = np.zeros_like(source)
    expected[3:] = source[:-3]
    assert np.array_equal(output, expected)


def test_bus_processor_runs_before_bus_fader_and_output_routing():
    project = Project()
    track = project.tracks[0]
    project.workflow = {
        "routing": {
            "buses": [
                {
                    "id": "fx",
                    "name": "FX",
                    "gain": 0.5,
                    "pan": 0.0,
                    "mute": False,
                    "output": "master",
                }
            ],
            "track_outputs": {track.id: "fx"},
        }
    }
    plan = compile_routing(project)
    master = np.zeros((4, 2), np.float32)
    buses = np.zeros((16, 4, 2), np.float32)
    scratch = np.zeros((4, 2), np.float32)
    source = np.ones((4, 2), np.float32)
    route_track(plan, 0, source, source, master, buses, scratch)

    called = []

    def process(target, block):
        called.append(target)
        block *= np.float32(2.0)

    finish_buses(plan, master, buses, scratch, 4, bus_processor=process)
    # 2x insert then 0.5x fader = unity.
    assert called == ["bus:fx"]
    assert np.allclose(master, 1.0)


def test_live_chain_bank_keeps_dry_audio_on_bridge_failure():
    class Bridge:
        blocksize = 16
        info = {"latency_samples": 5}
        error = ""

        def __init__(self):
            self.closed = False

        def render(self, audio, frames):
            return audio * np.float32(2.0)

        def close(self):
            self.closed = True

    bank = LivePluginChains()
    first = Bridge()
    bank.install("master", first)
    block = np.ones((4, 2), np.float32)
    bank.render("master", block)
    assert np.allclose(block, 2.0)
    assert bank.latencies()["master"] == 37  # intrinsic 5 + two 16-frame bridge blocks
    first.error = "failed"
    dry = np.ones((4, 2), np.float32)
    bank.render("master", dry)
    assert np.allclose(dry, 1.0)
    bank.remove("master")
    assert first.closed


def test_offline_chain_bank_loads_only_known_non_bypassed_targets():
    project = Project()
    target = f"track:{project.tracks[0].id}"
    project.pro_daw = {
        "plugin_chains": {
            target: [_spec(), _spec("/plugins/Bypassed.vst3", bypass=True)],
            "track:missing": [_spec("/plugins/Unknown.vst3")],
        }
    }

    class Chain:
        def __init__(self, specs, sample_rate):
            assert len(specs) == 1
            self.info = {"latency_samples": 11}
            self.closed = False

        def render(self, audio, frames):
            return audio * np.float32(0.25)

        def close(self):
            self.closed = True

    bank = OfflinePluginChains(project, 48000, factory=Chain)
    assert set(bank.chains) == {target}
    block = np.ones((4, 2), np.float32)
    bank.render(target, block)
    assert np.allclose(block, 0.25)
    assert bank.latencies()[target] == 11
    chain = bank.chains[target]
    bank.close()
    assert chain.closed

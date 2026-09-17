from __future__ import annotations

import numpy as np
import pytest

from mpclab.daw_expansion_state import default_daw_expansion
from mpclab.model import Project
from mpclab.plugin_chain_host import IsolatedPluginChain
from mpclab.plugin_host import receive_packet, send_packet
from mpclab.sidechain import SidechainRouter


def auxiliary_echo_worker(connection, specifications, sample_rate):
    del specifications, sample_rate
    try:
        send_packet(
            connection,
            {
                "name": "Aux fixture",
                "effect": True,
                "instrument": False,
                "latency_samples": 0,
                "sidechain_slots": [0],
                "chain": [],
            },
        )
        while True:
            request, raw = receive_packet(connection)
            if request.get("command") == "close":
                return
            frames = request["frames"]
            channels = request["input_channels"]
            packed = np.frombuffer(raw, dtype="<f4").reshape(frames, channels)
            main = packed[:, :2]
            aux = packed[:, 2:4]
            output = np.ascontiguousarray(main + aux * np.float32(0.5), dtype=np.float32)
            send_packet(connection, {"frames": frames}, output)
    finally:
        connection.close()


def _sidechain_project():
    project = Project()
    state = default_daw_expansion()
    source = project.tracks[3].id
    target = project.tracks[0].id
    state["sidechains"] = [
        {
            "id": "pre",
            "source": f"track:{source}",
            "target": f"track:{target}",
            "slot": 0,
            "gain": 0.5,
            "pre_fader": True,
            "enabled": True,
        },
        {
            "id": "post",
            "source": f"track:{source}",
            "target": f"track:{target}",
            "slot": 1,
            "gain": 1.0,
            "pre_fader": False,
            "enabled": True,
        },
    ]
    project.daw_expansion = state
    return project, source, target


def test_track_sidechain_order_processes_source_before_earlier_target():
    project, source, target = _sidechain_project()
    router = SidechainRouter(project, 64)
    source_index = project.track_index(source)
    target_index = project.track_index(target)
    assert source_index > target_index
    order = router.track_order()
    assert order.index(source_index) < order.index(target_index)


def test_sidechain_captures_real_pre_and_post_fader_audio_per_plugin_slot():
    project, source, target = _sidechain_project()
    router = SidechainRouter(project, 8)
    pre = np.full((8, 2), 2.0, dtype=np.float32)
    post = np.full((8, 2), 3.0, dtype=np.float32)
    router.begin_block(1, 8)
    router.capture(f"track:{source}", pre, 8, pre_fader=True)
    router.capture(f"track:{source}", post, 8, pre_fader=False)
    slots = router.for_target(f"track:{target}", 8)
    np.testing.assert_array_equal(slots[0], 1.0)
    np.testing.assert_array_equal(slots[1], 3.0)


def test_sidechain_cycles_are_rejected_before_audio_processing():
    project = Project()
    first, second = project.tracks[0].id, project.tracks[1].id
    state = default_daw_expansion()
    state["sidechains"] = [
        {
            "id": "a-b",
            "source": f"track:{first}",
            "target": f"track:{second}",
            "slot": 0,
            "gain": 1.0,
            "pre_fader": False,
            "enabled": True,
        },
        {
            "id": "b-a",
            "source": f"track:{second}",
            "target": f"track:{first}",
            "slot": 0,
            "gain": 1.0,
            "pre_fader": False,
            "enabled": True,
        },
    ]
    project.daw_expansion = state
    with pytest.raises(ValueError, match="acyclic"):
        SidechainRouter(project, 64)


def test_auxiliary_samples_cross_the_isolated_plugin_process_as_audio():
    chain = IsolatedPluginChain(
        [{"path": "fixture.vst3"}],
        48_000,
        worker=auxiliary_echo_worker,
        timeout=5,
    )
    try:
        main = np.full((32, 2), 0.25, dtype=np.float32)
        aux = np.linspace(0.0, 1.0, 64, dtype=np.float32).reshape(32, 2)
        result = chain.render(main, 32, sidechains={0: aux})
        np.testing.assert_allclose(result, main + aux * 0.5, rtol=0, atol=1e-7)
    finally:
        chain.close()


def test_auxiliary_route_refuses_plugin_slots_without_an_audio_input():
    chain = IsolatedPluginChain(
        [{"path": "fixture.vst3"}],
        48_000,
        worker=auxiliary_echo_worker,
        timeout=5,
    )
    try:
        main = np.zeros((8, 2), dtype=np.float32)
        aux = np.ones((8, 2), dtype=np.float32)
        with pytest.raises(Exception, match="does not expose auxiliary audio"):
            chain.render(main, 8, sidechains={1: aux})
    finally:
        chain.close()

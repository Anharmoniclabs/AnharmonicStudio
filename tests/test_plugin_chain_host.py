import os
import sys
import time
from types import SimpleNamespace

import numpy as np
import pytest

from mpclab.plugin_chain_host import IsolatedPluginChain, plugin_chain_worker
from mpclab.plugin_host import PluginError, receive_packet, send_packet


def fixture_chain_worker(connection, specifications, sample_rate):
    mode = specifications[0].get("mode")
    if mode == "load_crash":
        os._exit(7)
    if mode == "load_hang":
        time.sleep(30)
    send_packet(
        connection,
        {
            "name": "Fixture chain",
            "effect": True,
            "instrument": False,
            "latency_samples": 17,
            "chain": [{"name": "Fixture", "latency_samples": 17}],
        },
    )
    while True:
        try:
            request, raw = receive_packet(connection)
        except EOFError:
            break
        if mode == "process_crash":
            os._exit(9)
        audio = np.frombuffer(raw, dtype="<f4").reshape(request["frames"], 2)
        send_packet(connection, {"frames": len(audio)}, audio * 0.5)


def native_chain_worker(connection, specifications, sample_rate):
    class Parameter:
        index = 1
        is_automatable = True
        name = "Gain"
        raw_value = 0.5
        string_value = "50%"

    class NativePlugin:
        _parameters = (Parameter(),)
        raw_state = b"fixture-state"
        is_instrument = False
        is_effect = True
        reported_latency_samples = 3

        def __init__(self, path, **kwargs):
            self.name = path
            os.write(1, b"plugin chain native stdout noise\n")
            os.write(2, b"plugin chain native stderr noise\n")

        def reset(self):
            pass

        def process(self, audio, *args, **kwargs):
            os.write(1, b"plugin chain process noise\n")
            return np.asarray(audio, dtype=np.float32) * np.float32(0.5)

    sys.modules["pedalboard_native"] = SimpleNamespace(
        ExternalPlugin=NativePlugin,
        VST3Plugin=NativePlugin,
        AudioUnitPlugin=NativePlugin,
    )
    plugin_chain_worker(connection, specifications, sample_rate)


def test_isolated_chain_processes_audio_and_reports_latency():
    chain = IsolatedPluginChain([{}], worker=fixture_chain_worker)
    try:
        result = chain.render(np.ones((128, 2), np.float32), 128)
        assert np.allclose(result, 0.5)
        assert chain.info["latency_samples"] == 17
    finally:
        chain.close()
    assert not chain.process.is_alive()


@pytest.mark.parametrize("mode", ["load_crash", "load_hang"])
def test_chain_load_failure_returns_control(mode):
    with pytest.raises(PluginError):
        IsolatedPluginChain([{"mode": mode}], worker=fixture_chain_worker, timeout=0.5)


def test_chain_process_crash_is_contained():
    chain = IsolatedPluginChain([{"mode": "process_crash"}], worker=fixture_chain_worker)
    try:
        with pytest.raises(PluginError, match="closed unexpectedly"):
            chain.render(np.ones((64, 2), np.float32), 64)
    finally:
        chain.close()


def test_native_chain_runs_multiple_effects_in_one_child(capfd):
    chain = IsolatedPluginChain(
        [{"path": "One.vst3"}, {"path": "Two.vst3"}],
        worker=native_chain_worker,
    )
    try:
        result = chain.render(np.ones((64, 2), np.float32), 64)
        assert np.allclose(result, 0.25)
        assert chain.info["latency_samples"] == 6
        assert len(chain.info["chain"]) == 2
    finally:
        chain.close()
    captured = capfd.readouterr()
    assert "native" not in captured.out + captured.err


def test_chain_rejects_empty_and_unbounded_specs():
    with pytest.raises(PluginError):
        IsolatedPluginChain([], worker=fixture_chain_worker)
    with pytest.raises(PluginError):
        IsolatedPluginChain([{}] * 9, worker=fixture_chain_worker)

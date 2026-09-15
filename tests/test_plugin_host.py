import base64
import os
import queue
import time

import numpy as np
import pytest

from mpclab.model import Project
from mpclab.plugin_host import IsolatedPlugin, LivePlugin, PluginError, receive_packet, send_packet
from mpclab.plugin_registry import discover_plugins, validate_project_plugins


def fixture_worker(connection, specification, sample_rate):
    """Deliberate crash/hang and deterministic gain in an actual spawned child."""
    mode = specification.get("mode")
    if mode == "load_crash":
        os._exit(7)
    if mode == "load_hang":
        time.sleep(30)
    send_packet(connection, {"name": "Fixture", "effect": True})
    while True:
        try:
            request, raw = receive_packet(connection)
        except EOFError:
            break
        if mode == "process_crash":
            os._exit(9)
        audio = np.frombuffer(raw, dtype="<f4").reshape(request["frames"], 2)
        send_packet(connection, {"frames": len(audio)}, audio * 0.25)


def noisy_native_worker(connection, specification, sample_rate):
    import sys
    from types import SimpleNamespace
    from mpclab.plugin_host import plugin_worker

    class NativePlugin:
        _parameters = ()
        raw_state = b""
        name = "Noisy plugin"
        is_instrument = False
        is_effect = True
        reported_latency_samples = 0

        def __init__(self, *args, **kwargs):
            os.write(1, b"native stdout noise\n")
            os.write(2, b"native stderr noise\n")

        def process(self, audio, *args, **kwargs):
            os.write(1, b"native processing noise\n")
            return audio

    sys.modules["pedalboard_native"] = SimpleNamespace(
        ExternalPlugin=NativePlugin, VST3Plugin=NativePlugin
    )
    plugin_worker(connection, specification, sample_rate)


def test_native_plugin_output_cannot_corrupt_export_protocol(capfd):
    plugin = IsolatedPlugin({"path": "Noisy.vst3"}, worker=noisy_native_worker)
    try:
        assert np.all(plugin.render(np.ones((128, 2), np.float32), 128) == 1)
    finally:
        plugin.close()
    captured = capfd.readouterr()
    assert "native" not in captured.out + captured.err


def test_isolated_audio_and_crash_handling():
    plugin = IsolatedPlugin({}, worker=fixture_worker)
    try:
        assert np.allclose(plugin.render(np.ones((128, 2), np.float32), 128), 0.25)
    finally:
        plugin.close()
    assert not plugin.process.is_alive()
    plugin = IsolatedPlugin({"mode": "process_crash"}, worker=fixture_worker)
    try:
        with pytest.raises(PluginError, match="closed unexpectedly"):
            plugin.render(np.ones((128, 2), np.float32), 128)
    finally:
        plugin.close()


@pytest.mark.parametrize("mode", ["load_crash", "load_hang"])
def test_failed_or_hung_plugin_load_returns_control(mode):
    with pytest.raises(PluginError):
        IsolatedPlugin({"mode": mode}, worker=fixture_worker, timeout=0.5)


def live_fixture():
    # Supply already-completed worker responses: timing tests are deterministic.
    bridge = LivePlugin.__new__(LivePlugin)
    bridge.blocksize = 16
    bridge.requests = queue.Queue(maxsize=4)
    bridge.results = queue.Queue(maxsize=4)
    bridge.error = ""
    bridge.misses = 0
    bridge._position = 0
    bridge._pending = {}
    return bridge


def test_live_pipeline_preserves_samples_across_loop_split_blocks():
    bridge = live_fixture()
    output = []
    source = np.arange(160, dtype=np.float32).reshape(80, 2)
    offset = 0
    for frames in [16, 7, 9, 16, 3, 13, 16]:
        block = source[offset : offset + frames]
        output.append(bridge.render(block, frames))
        position, audio, n, midi, reset = bridge.requests.get_nowait()
        bridge.results.put((position, audio))
        offset += frames
    assert not bridge.error
    combined = np.concatenate(output)
    assert np.array_equal(combined[:32], np.zeros((32, 2)))
    assert np.array_equal(combined[32:], source[: len(combined) - 32])


def test_live_timeout_fails_without_waiting_or_replaying_old_audio():
    bridge = live_fixture()
    audio = np.ones((16, 2), np.float32)
    assert bridge.render(audio, 16) is not None
    assert bridge.render(audio, 16) is not None
    assert bridge.render(audio, 16) is None
    assert bridge.misses == 1 and "deadline" in bridge.error


def test_plugin_state_roundtrip_and_legacy_projects(tmp_path):
    project = Project()
    project.plugins = {
        "instrument": {
            "path": "/plugins/Example.vst3",
            "plugin_name": "Example",
            "parameters": {"gain": 0.5},
            "state": base64.b64encode(b"opaque preset").decode(),
            "bypass": False,
        }
    }
    path = tmp_path / "song.json"
    project.save(path)
    assert Project.load(path).plugins == project.plugins
    assert Project.from_dict({"format_version": 3}).plugins == {}
    with pytest.raises(ValueError):
        validate_project_plugins({"instrument": {"path": "bad.py"}})
    with pytest.raises(ValueError):
        validate_project_plugins(
            {"effect": {"path": "test.vst3", "parameters": {"gain": float("nan")}}}
        )


def test_recursive_discovery_does_not_enter_plugin_bundles_or_symlink_loops(tmp_path):
    bundle = tmp_path / "Vendor/Synth.vst3"
    bundle.mkdir(parents=True)
    (bundle / "Internal.vst3").mkdir()
    (tmp_path / "Vendor/loop").symlink_to(tmp_path, target_is_directory=True)
    candidates = discover_plugins([tmp_path, tmp_path / "Vendor"])
    assert [p.path for p in candidates] == [str(bundle)]

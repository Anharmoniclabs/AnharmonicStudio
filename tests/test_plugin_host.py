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
        if mode == "process_hang":
            time.sleep(30)
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
    with pytest.raises(PluginError, match="closed unexpectedly"):
        plugin.render(np.ones((128, 2), np.float32), 128)
    assert plugin.closed
    assert not plugin.process.is_alive()


def test_hung_plugin_process_is_quarantined_after_render_timeout():
    plugin = IsolatedPlugin({"mode": "process_hang"}, worker=fixture_worker)
    with pytest.raises(PluginError, match="stopped responding"):
        plugin.render(np.ones((64, 2), np.float32), 64, timeout=0.1)
    assert plugin.closed
    assert not plugin.process.is_alive()
    with pytest.raises(PluginError, match="closed"):
        plugin.render(np.ones((64, 2), np.float32), 64)


@pytest.mark.parametrize("mode", ["load_crash", "load_hang"])
def test_failed_or_hung_plugin_load_returns_control(mode):
    with pytest.raises(PluginError):
        IsolatedPlugin({"mode": mode}, worker=fixture_worker, timeout=0.5)


def live_fixture():
    bridge = LivePlugin.__new__(LivePlugin)
    bridge.blocksize = 16
    bridge.requests = queue.Queue(maxsize=4 * bridge.blocksize)
    bridge.results = queue.Queue(maxsize=4 * bridge.blocksize)
    bridge.error = ""
    bridge.misses = 0
    bridge.backlog_recoveries = 0
    bridge._consecutive_misses = 0
    bridge._position = 0
    bridge._received_position = 0
    bridge._missing_frames = 0
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


def test_live_timeout_recovers_without_waiting_or_replaying_old_audio():
    bridge = live_fixture()
    audio = np.ones((16, 2), np.float32)
    assert bridge.render(audio, 16) is not None
    assert bridge.render(audio, 16) is not None
    note_off = [([0x80, 60, 0], 0)]
    assert np.array_equal(bridge.render(audio, 16, note_off, reset=True), np.zeros((16, 2)))
    assert bridge.misses == 1 and not bridge.error
    # The first result arrives late. It must never be played in a later window.
    bridge.results.put((0, np.full((16, 2), 99, np.float32)))
    bridge.results.put((16, np.full((16, 2), 2, np.float32)))
    result = bridge.render(audio, 16)
    assert np.array_equal(result, np.full((16, 2), 2, np.float32))
    assert not bridge.error and bridge._consecutive_misses == 0
    assert 0 not in bridge._pending
    queued = [bridge.requests.get_nowait() for _ in range(4)]
    assert queued[2][3:] == (note_off, True)


def test_live_deadline_misses_do_not_permanently_disable_a_responding_worker():
    bridge = live_fixture()
    for _ in range(10):
        result = bridge.render(None, 16)
        # Simulate a worker accepting requests but always returning too late.
        request = bridge.requests.get_nowait()
        bridge._received_position = request[0] + request[2]
    assert result is not None
    assert bridge.misses == 8 and not bridge.error


def test_live_partial_deadline_miss_retains_only_timely_samples():
    bridge = live_fixture()
    bridge.render(None, 16)
    bridge.render(None, 16)
    bridge.results.put((0, np.full((8, 2), 3, np.float32)))
    result = bridge.render(None, 16)
    assert np.array_equal(result[:8], np.full((8, 2), 3, np.float32))
    assert np.array_equal(result[8:], np.zeros((8, 2)))
    assert bridge.misses == 1 and not bridge.error


def test_live_request_backlog_recovers_with_bounded_queue():
    bridge = live_fixture()
    for _ in range(5):
        result = bridge.render(None, 16)
    assert result is not None and not bridge.error
    assert bridge.requests.qsize() == 1
    assert bridge.backlog_recoveries == 1


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


def test_live_dense_midi_chunks_fit_the_same_bounded_audio_backlog():
    bridge = live_fixture()
    source = np.arange(128, dtype=np.float32).reshape(64, 2)
    # Worker receives one whole callback split by many MIDI events, then
    # publishes every result before the next callback starts.
    for frame in range(16):
        assert bridge.render(source[frame : frame + 1], 1, [([0x90, 60, 90], 0)]) is not None
    while not bridge.requests.empty():
        position, audio, frames, midi, reset = bridge.requests.get_nowait()
        bridge.results.put_nowait((position, audio))
    assert bridge.render(source[16:32], 16) is not None
    result = bridge.render(source[32:48], 16, [([0x80, 60, 0], 0)])
    assert np.array_equal(result, source[:16])
    assert not bridge.error and bridge.misses == 0


def test_live_tiny_chunks_still_have_a_four_block_backlog_limit():
    bridge = live_fixture()
    for _ in range(64):
        assert bridge.render(None, 1) is not None
    assert bridge.render(None, 1) is not None
    assert not bridge.error and bridge.requests.qsize() == 1
    assert bridge.backlog_recoveries == 1


def test_backlog_recovery_preserves_note_off_sustain_and_parameter_order():
    bridge = live_fixture()
    events = [([0x90, 60, 100], 0), ([0xB0, 64, 127], 0), ([0x80, 60, 0], 0), ([0xB0, 64, 0], 0)]
    for index, event in enumerate(events):
        bridge.render(None, 16, [event], parameters={"gain": index / 4})
    bridge.render(None, 16, [([0x90, 67, 90], 0)], parameters={"gain": 0.9})
    request = bridge.requests.get_nowait()
    assert request[0] == 64
    assert request[3] == events + [([0x90, 67, 90], 0)]
    assert request[5] == {"gain": 0.9}
    # Return the recovered chunk, then render far enough to hear it on time.
    bridge.results.put((64, np.ones((16, 2), np.float32)))
    bridge.render(None, 16)
    output = bridge.render(None, 16)
    assert np.array_equal(output, np.ones((16, 2), np.float32))
    assert not bridge.error

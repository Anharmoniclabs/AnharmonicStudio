"""Independent audio-continuity checks for multi-instrument operations."""

from dataclasses import replace

import numpy as np

from mpclab.external_dsp import ExternalDSP
from mpclab.model import Project


class ConstantPlugin:
    def __init__(self, latency=0):
        self.info = {"latency_samples": latency}
        self.blocksize = 0

    def render(self, _audio, frames, *_args, **_kwargs):
        return np.ones((frames, 2), np.float32)

    def close(self):
        pass


def test_refreshing_unchanged_plugin_paths_preserves_buffered_instrument_audio():
    project = Project()
    project.synth.track = 0
    other = project.add_instrument("Second", replace(project.synth, track=1))
    external = ExternalDSP()
    external.instrument = ConstantPlugin()
    external.ensure_route(other.id).instrument = ConstantPlugin(latency=8)
    external.prepare_mix(4)
    tracks = np.zeros((len(project.tracks), 4, 2), np.float32)
    for _ in range(3):
        tracks.fill(0)
        external.render_tracks(project, tracks, 4, 48000)
    np.testing.assert_array_equal(tracks[0], 1)

    # Adding/removing a master effect refreshes these otherwise unchanged paths.
    external.prepare_mix(4)
    tracks.fill(0)
    external.render_tracks(project, tracks, 4, 48000)
    np.testing.assert_array_equal(tracks[0], 1)


def test_live_release_preserves_overlapping_sequence_note_on_same_midi_channel():
    class GatePlugin(ConstantPlugin):
        def __init__(self):
            super().__init__()
            self.active = set()

        def render(self, _audio, frames, midi=(), **_kwargs):
            for message, _offset in midi:
                key = (message[0] & 15, message[1])
                if message[0] & 0xF0 == 0x90:
                    self.active.add(key)
                elif message[0] & 0xF0 == 0x80:
                    self.active.discard(key)
            return np.full((frames, 2), bool(self.active), np.float32)

    external = ExternalDSP()
    plugin = external.instrument = GatePlugin()
    output = np.zeros((4, 2), np.float32)
    external.note_on(60, 0.8, gate=100, live=False, channel=0)
    external.render_instrument(output, 4, 48000)
    external.note_on(60, 0.8, live=True, channel=0)
    external.render_instrument(output, 4, 48000)
    external.release_live()
    output.fill(0)
    external.render_instrument(output, 4, 48000)
    assert plugin.active == {(0, 60)}
    np.testing.assert_array_equal(output, 1)
    for _ in range(25):
        external.render_instrument(output, 4, 48000)
    assert not plugin.active


def test_refreshing_latency_preserves_dry_history_and_publishes_changed_config():
    from mpclab.engine import Engine
    from mpclab.workflow_routing import install_engine_routing_extensions

    install_engine_routing_extensions()
    engine = Engine(object(), sample_rate=48000, blocksize=4)
    engine.external.instrument = ConstantPlugin(latency=8)
    engine.prepare_plugin_latency()
    previous = engine.plugin_pdc
    tracks = np.ones((len(engine.project.tracks), 4, 2), np.float32)
    previous.process(tracks, 4)
    position, history = previous.position, previous.history.copy()
    engine.prepare_plugin_latency()
    assert engine.plugin_pdc is previous
    assert previous.position == position
    np.testing.assert_array_equal(previous.history, history)
    engine.external.instrument.info["latency_samples"] = 12
    engine.prepare_plugin_latency()
    assert engine.plugin_pdc is not previous
    assert previous.delay_samples == 8
    np.testing.assert_array_equal(previous.history, history)


def test_replaced_bridge_does_not_reuse_previous_instrument_delay_history():
    project = Project()
    project.synth.track = 0
    other = project.add_instrument("Second", replace(project.synth, track=1))
    external = ExternalDSP()
    external.instrument = ConstantPlugin()
    external.ensure_route(other.id).instrument = ConstantPlugin(latency=8)
    external.prepare_mix(4)
    previous = external.mix_delays[None]
    previous.history.fill(1)
    external.instrument = ConstantPlugin()
    external.prepare_mix(4)
    assert external.mix_delays[None] is not previous
    assert not external.mix_delays[None].history.any()


def test_gated_live_arp_notes_finish_and_do_not_release_newer_gate():
    external = ExternalDSP()
    external.instrument = ConstantPlugin()
    output = np.zeros((4, 2), np.float32)
    external.note_on(60, 0.8, gate=8, live=True)
    external.render_instrument(output, 4, 48000)
    external.release_live()
    external.note_on(60, 0.8, gate=20, live=True)
    external.render_instrument(output, 4, 48000)
    external.render_instrument(output, 4, 48000)
    assert external._note_owners[(0, 60)]
    for _ in range(5):
        external.render_instrument(output, 4, 48000)
    assert not external._note_owners


def test_sequence_gate_end_preserves_held_live_note():
    external = ExternalDSP()
    external.instrument = ConstantPlugin()
    output = np.zeros((4, 2), np.float32)
    external.note_on(60, 0.8, gate=8, live=False, channel=0)
    external.note_on(60, 0.8, live=True, channel=0)
    for _ in range(3):
        external.render_instrument(output, 4, 48000)
    assert external._note_owners[(0, 60)] == {(True, 0)}
    external.note_off(60)
    external.render_instrument(output, 4, 48000)
    assert not external._note_owners


def test_primary_arp_toggle_preserves_other_instrument_backing(tmp_path, monkeypatch):
    from mpclab.engine import Engine
    from mpclab.ui.main_window import MainWindow

    monkeypatch.setattr(Engine, "start", lambda self: None)
    window = MainWindow(tmp_path, restore_session=False)
    try:
        engine = window.engine
        other = window.project.add_instrument("Second", replace(window.project.synth, track=1))
        engine._spawn_synth(67, 0.7, live_trigger=False, instrument_id=other.id)
        backing = engine.synth_voices[-1]
        route = engine.external.ensure_route(other.id)
        route.instrument = ConstantPlugin()
        route.note_on(65, 0.7, gate=100, live=False)
        events, ends = list(route.events), list(route.ends)
        window.synth_panel._arp_toggled(True)
        engine._process_commands()
        assert backing.stage != "release"
        assert route.events == events
        assert route.ends == ends
    finally:
        window.close()


def test_overlapping_sequence_export_has_same_sustained_gate(monkeypatch):
    from mpclab.external_dsp import OfflinePlugins

    plugin = ConstantPlugin()
    plugin.info["instrument"] = True
    emitted = []

    def render(_audio, frames, midi=(), **_kwargs):
        emitted.extend((round(offset * 48000), message[0]) for message, offset in midi)
        return np.zeros((frames, 2), np.float32)

    plugin.render = render
    monkeypatch.setattr("mpclab.plugin_host.IsolatedPlugin", lambda *_args: plugin)
    offline = OfflinePlugins(
        {"instrument": {"path": "/synthetic.vst3"}},
        48000,
        [(0, 60, 0.8, 8, 0), (4, 60, 0.8, 8, 0)],
    )
    offline.render_instrument(np.zeros((16, 2), np.float32), 0, 16)
    assert emitted == [(0, 0x90), (12, 0x80)]
    offline.close()


def test_all_notes_off_allows_same_key_to_play_again():
    class RecordingPlugin(ConstantPlugin):
        def __init__(self):
            super().__init__()
            self.messages = []

        def render(self, audio, frames, midi=(), **kwargs):
            self.messages.extend(message for message, _at in midi)
            return super().render(audio, frames, midi, **kwargs)

    external = ExternalDSP()
    plugin = external.instrument = RecordingPlugin()
    output = np.zeros((4, 2), np.float32)
    external.note_on(60, 0.8)
    external.render_instrument(output, 4, 48000)
    external.events.append(([0xB0, 123, 0], 0))
    external.render_instrument(output, 4, 48000)
    external.note_on(60, 0.8)
    external.render_instrument(output, 4, 48000)
    assert sum(message[0] == 0x90 for message in plugin.messages) == 2

"""Independent instrument targets must survive UI edits, playback and recall."""

from dataclasses import replace

import numpy as np
import pytest

from mpclab.engine import Engine
from mpclab.model import Project
from mpclab.music import Note
from mpclab.ui.main_window import MainWindow


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(Engine, "start", lambda self: None)
    window = MainWindow(tmp_path, restore_session=False)
    yield window
    window.close()


def test_native_instance_can_be_created_without_replacing_recorded_sound(window):
    primary = replace(window.project.synth)
    window.project.pattern().notes = [Note(60, 0, 1, 0.7)]
    window.synth_panel.add_instrument()
    identity = window.project.selected_instrument
    assert identity is not None
    window.synth_panel._set_patch("cutoff", 1234)
    assert window.project.synth == primary
    assert window.project.selected_patch.cutoff == 1234
    assert window.project.pattern().notes[0].instrument is None
    restored = Project.from_dict(window.project.to_dict())
    assert restored.instrument_patch(identity).cutoff == 1234
    assert restored.synth == primary


def test_existing_native_engine_routes_distinct_instances_to_distinct_tracks():
    engine = Engine(object(), sample_rate=48000, blocksize=512)
    project = engine.project
    project.synth = replace(project.synth, track=0, noise=0, volume=0.1)
    second = project.add_instrument("Bass", replace(project.synth, track=1, osc1="sine"))
    engine.synth_note_on(60)
    engine.synth_note_on(48, instrument_id=second.id)
    output = np.zeros((512, 2), dtype=np.float32)
    engine._callback(output, len(output), None, False)
    assert {voice.instrument_id for voice in engine.synth_voices} == {None, second.id}
    assert {voice.track for voice in engine.synth_voices} == {0, 1}
    assert np.max(np.abs(engine._tbuf[0])) > 0
    assert np.max(np.abs(engine._tbuf[1])) > 0
    assert not np.array_equal(engine._tbuf[0], engine._tbuf[1])


class SyntheticPlugin:
    """No binary, worker, audio device or external process; deterministic instrument."""

    def __init__(self, specification=None, sample_rate=48000):
        specification = specification or {}
        self.specification = specification
        self.gain = specification.get("parameters", {}).get("gain", 0.2)
        self.info = {
            "instrument": True,
            "name": "Synthetic",
            "state": "",
            "latency_samples": 0,
            "parameters": {"gain": {"name": "Gain", "value": self.gain}},
        }
        self.blocksize = 128
        self.error = ""
        self.closed = False
        self.messages = []
        self.active = set()

    def render(self, audio, frames, midi=(), **kwargs):
        output = np.zeros((frames, 2), np.float32)
        if kwargs.get("reset"):
            self.active.clear()
        cursor = 0
        for message, seconds in midi:
            at = min(frames, round(seconds * 48000))
            output[cursor:at] = self.gain * len(self.active)
            status, note, velocity = message
            self.messages.append(list(message))
            if status & 0xF0 == 0x90 and velocity:
                self.active.add((status & 15, note))
            elif status & 0xF0 == 0x80 or (status & 0xF0 == 0x90 and not velocity):
                self.active.discard((status & 15, note))
            elif status & 0xF0 == 0xB0 and note == 123:
                self.active = {key for key in self.active if key[0] != status & 15}
            cursor = at
        output[cursor:] = self.gain * len(self.active)
        return output

    def close(self):
        self.closed = True


def plugin_spec(gain=0.2):
    return {"path": "/synthetic/SamePlugin.vst3", "parameters": {"gain": gain}}


def test_per_instance_plugin_state_survives_reload_and_rejects_invalid_state():
    project = Project()
    first = project.add_instrument("First", replace(project.synth, track=0))
    second = project.add_instrument("Second", replace(project.synth, track=1))
    first.plugin, second.plugin = plugin_spec(0.2), plugin_spec(0.7)
    restored = Project.from_dict(project.to_dict())
    assert restored.instruments[0].plugin["path"] == restored.instruments[1].plugin["path"]
    restored.instruments[1].plugin["parameters"]["gain"] = 0.9
    assert restored.instruments[0].plugin["parameters"]["gain"] == 0.2
    assert project.instruments[1].plugin["parameters"]["gain"] == 0.7
    restored.instruments[1].plugin["parameters"]["gain"] = float("nan")
    with pytest.raises(ValueError, match="normalized"):
        restored._validate_instruments()


def test_live_plugins_own_separate_midi_queues_audio_tracks_and_releases():
    engine = Engine(object(), sample_rate=48000, blocksize=128)
    project = engine.project
    project.synth.track = 0
    second = project.add_instrument("Second", replace(project.synth, track=1))
    primary = engine.external.instrument = SyntheticPlugin(plugin_spec(0.2))
    other_route = engine.external.ensure_route(second.id)
    other = other_route.instrument = SyntheticPlugin(plugin_spec(0.7))
    engine.external.prepare_mix(128)
    engine.synth_note_on(60)
    engine.synth_note_on(67, instrument_id=second.id)
    output = np.zeros((128, 2), np.float32)
    engine._callback(output, len(output), None, False)
    assert primary.active == {(0, 60)}
    assert other.active == {(0, 67)}
    np.testing.assert_allclose(engine._tbuf[0], 0.2)
    np.testing.assert_allclose(engine._tbuf[1], 0.7)
    engine.synth_note_off(67, instrument_id=second.id)
    engine._callback(output, len(output), None, False)
    assert primary.active == {(0, 60)}
    assert not other.active
    assert not engine.synth_voices
    engine.external.close()
    assert primary.closed and other.closed


def test_export_uses_independent_plugin_states_and_mute_routes(tmp_path, monkeypatch):
    from mpclab.library import Library

    instances = []

    def construct(specification, rate):
        instance = SyntheticPlugin(specification, rate)
        instances.append(instance)
        return instance

    monkeypatch.setattr("mpclab.plugin_host.IsolatedPlugin", construct)
    engine = Engine(Library(tmp_path / "library"), sample_rate=48000, blocksize=128)
    project = engine.project
    project.bpm = 240
    project.pattern().bars = 1
    project.synth.track = 0
    project.plugins["instrument"] = plugin_spec(0.2)
    second = project.add_instrument("Second", replace(project.synth, track=1))
    second.plugin = plugin_spec(0.7)
    project.pattern().notes = [Note(60, 0, 1, 0.7), Note(67, 0, 1, 0.7, instrument=second.id)]
    project.tracks[1].mute = True
    first = engine.render_offline("pattern", tail=0)
    second.plugin = plugin_spec(0.9)
    unchanged = engine.render_offline("pattern", tail=0)
    np.testing.assert_array_equal(first, unchanged)
    assert all(instance.closed for instance in instances)
    assert [message[1] for message in instances[0].messages if message[0] & 0xF0 == 0x90] == [60]
    assert [message[1] for message in instances[1].messages if message[0] & 0xF0 == 0x90] == [67]
    project.tracks[0].mute, project.tracks[1].mute = True, False
    isolated_second = engine.render_offline("pattern", tail=0)
    assert np.max(np.abs(isolated_second)) > np.max(np.abs(first))


def test_async_plugin_completion_keeps_requested_identity_and_current_selection(
    window, monkeypatch
):
    controller = window.devices
    second = window.project.add_instrument("Second", replace(window.project.synth, track=1))
    slot = f"instrument:{second.id}"
    monkeypatch.setattr(controller, "_continue_load", lambda: None)
    primary = window.engine.external.instrument = SyntheticPlugin(plugin_spec(0.2))
    window.project.plugins["instrument"] = plugin_spec(0.2)
    controller.load_plugin(slot, plugin_spec(0.7))
    generation, specification, save = controller._pending_loads[slot]
    # User switches to primary before the requested second instance finishes loading.
    window.project.selected_instrument = None
    other = SyntheticPlugin(specification)
    controller._plugin_loaded(generation, slot, other, (specification, save), "")
    assert window.engine.external.instrument is primary
    assert not primary.closed
    assert window.project.selected_instrument is None
    assert second.plugin["parameters"]["gain"] == 0.7
    assert window.engine.external.route(second.id).instrument is other
    assert window.project.plugins["instrument"]["parameters"]["gain"] == 0.2


def test_stale_plugin_result_after_project_replacement_is_closed(window, monkeypatch):
    controller = window.devices
    second = window.project.add_instrument("Second", window.project.synth)
    slot = f"instrument:{second.id}"
    monkeypatch.setattr(controller, "_continue_load", lambda: None)
    controller.load_plugin(slot, plugin_spec())
    generation, specification, save = controller._pending_loads[slot]
    window._apply_project(Project())
    stale = SyntheticPlugin(specification)
    controller._plugin_loaded(generation, slot, stale, (specification, save), "")
    assert stale.closed
    assert not window.project.instruments
    assert not window.engine.external.instances


def test_project_replacement_queues_per_instance_plugins_without_primary_slot(window, monkeypatch):
    controller = window.devices
    monkeypatch.setattr(controller, "_continue_load", lambda: None)
    project = Project()
    second = project.add_instrument("Second", replace(project.synth, track=1))
    second.plugin = plugin_spec()
    window._apply_project(Project.from_dict(project.to_dict()))
    assert not window.project.plugins
    assert f"instrument:{second.id}" in controller._pending_loads
    assert window.engine.external.route(second.id).instrument is not None


def test_switch_input_cleanup_keeps_native_backing_and_other_instance(window):
    from mpclab.ui.window_transport import release_instrument_input

    engine = window.engine
    second = window.project.add_instrument("Second", replace(window.project.synth, track=1))
    engine._spawn_synth(60, 0.7, live_trigger=False)
    engine._spawn_synth(62, 0.7, live_trigger=True)
    engine._spawn_synth(67, 0.7, live_trigger=False, instrument_id=second.id)
    backing, live, other = engine.synth_voices
    release_instrument_input(window)
    engine._process_commands()
    assert live.stage == "release"
    assert backing.stage != "release"
    assert other.stage != "release"


def test_preset_change_releases_only_edited_native_instance(window):
    engine = window.engine
    second = window.project.add_instrument("Second", replace(window.project.synth, track=1))
    window.project.selected_instrument = second.id
    engine._spawn_synth(60, 0.7, live_trigger=False)
    engine._spawn_synth(67, 0.7, live_trigger=False, instrument_id=second.id)
    primary, other = engine.synth_voices
    from mpclab.synth import PATCHES

    name = next(iter(PATCHES))
    window.synth_panel._commit_preset(name)
    engine._process_commands()
    assert primary.stage != "release"
    assert other.stage == "release"


def test_targeted_primary_panic_preserves_other_plugin_notes_and_gates():
    engine = Engine(object(), sample_rate=48000, blocksize=128)
    second = engine.project.add_instrument("Second", replace(engine.project.synth, track=1))
    route = engine.external.ensure_route(second.id)
    route.instrument = SyntheticPlugin()
    route.note_on(67, 0.7, gate=10000, live=False)
    events, ends = list(route.events), list(route.ends)
    engine.synth_instance_panic(None)
    engine._process_commands()
    assert route.events == events
    assert route.ends == ends
    assert not route.reset_requested


def test_master_effect_removal_preserves_all_instrument_midi_gates(window):
    controller = window.devices
    external = window.engine.external
    external.instrument = SyntheticPlugin()
    external.effect = SyntheticPlugin()
    external.note_on(60, 0.7, gate=10000, live=False)
    events, ends = list(external.events), list(external.ends)
    controller.remove_plugin("effect")
    assert external.events == events
    assert external.ends == ends


def test_multi_plugin_latency_aligns_both_instruments_and_dry_audio_exactly():
    from mpclab.external_dsp import ExternalDSP
    from mpclab.plugin_latency import PluginDelayCompensator

    class ImpulsePlugin:
        def __init__(self, latency):
            self.info = {"latency_samples": latency}
            self.blocksize = 0
            self.latency = latency
            self.position = 0

        def render(self, _audio, frames, *_args, **_kwargs):
            output = np.zeros((frames, 2), np.float32)
            if self.position <= self.latency < self.position + frames:
                output[self.latency - self.position] = 1
            self.position += frames
            return output

    project = Project()
    project.synth.track = 0
    second = project.add_instrument("Second", replace(project.synth, track=1))
    external = ExternalDSP()
    external.instrument = ImpulsePlugin(2)
    external.ensure_route(second.id).instrument = ImpulsePlugin(7)
    dry = PluginDelayCompensator(len(project.tracks), 4)
    dry.configure(external.prepare_mix(4))
    assert dry.delay_samples == 7
    blocks = []
    for start in range(0, 12, 4):
        block = np.zeros((len(project.tracks), 4, 2), np.float32)
        if start == 0:
            block[2, 0] = 1
        external.render_tracks(project, block, 4, 48000, dry)
        blocks.append(block)
    actual = np.concatenate(blocks, axis=1)
    for track in (0, 1, 2):
        assert np.flatnonzero(actual[track, :, 0]).tolist() == [7]
        assert actual[track, 7, 0] == 1


def test_plugin_editor_parameters_remain_bound_to_explicit_instance(window, monkeypatch):
    from mpclab.ui.devices import DevicesDialog

    controller = window.devices
    second = window.project.add_instrument("Second", replace(window.project.synth, track=1))
    window.project.plugins["instrument"] = plugin_spec(0.2)
    second.plugin = plugin_spec(0.7)
    window.engine.external.instrument = SyntheticPlugin(plugin_spec(0.2))
    window.engine.external.ensure_route(second.id).instrument = SyntheticPlugin(plugin_spec(0.7))
    dialog = DevicesDialog(controller, window)
    slot = f"instrument:{second.id}"
    dialog.slot.setCurrentIndex(dialog.slot.findData(slot))
    assert dialog.parameters["gain"].value() == 0.7
    dialog.parameters["gain"].setValue(0.9)
    calls = []
    monkeypatch.setattr(
        controller, "load_plugin", lambda target, spec: calls.append((target, spec))
    )
    dialog._apply_parameters()
    assert calls[0][0] == slot
    assert calls[0][1]["parameters"]["gain"] == 0.9
    assert window.project.plugins["instrument"]["parameters"]["gain"] == 0.2
    dialog.close()


def test_creating_native_instrument_is_undoable_with_stable_recalled_identity(window):
    instrument = window.synth_panel.add_instrument()
    identity = instrument.id
    window.undo()
    assert not window.project.instruments
    assert window.project.selected_instrument is None
    window.redo()
    assert window.project.selected_instrument == identity
    assert window.project.instruments[0].id == identity
    assert window.synth_panel.instance.currentData() == identity
    assert window.piano_roll.target_instrument == identity

"""Mixer navigation must pick a sound without corrupting saved selection."""

import pytest
from types import SimpleNamespace

from mpclab.engine import Engine
from mpclab.ui.main_window import MainWindow


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(Engine, "start", lambda self: None)
    window = MainWindow(tmp_path, restore_session=False)
    yield window
    window._dirty = False
    window.close()


def test_mixer_navigation_selects_corresponding_existing_instrument(window):
    window.project.synth.track = 0
    first = window.project.add_instrument("First sound", window.project.synth)
    second = window.project.add_instrument("Second sound", window.project.synth)
    first.patch.track = 1
    second.patch.track = 2
    window.synth_panel.sync()
    window.mixer.select_track(1)
    assert window.project.selected_instrument == first.id
    window.mixer.select_track(2)
    assert window.project.selected_instrument == second.id
    assert first.patch.track == 1
    assert second.patch.track == 2


def test_mixer_refresh_preserves_selected_instrument(window):
    instrument = window.project.add_instrument("Selected sound", window.project.synth)
    instrument.patch.track = 2
    window.project.selected_instrument = instrument.id
    window.mixer.sync()
    assert window.project.selected_instrument == instrument.id


def test_empty_mixer_channel_does_not_move_existing_instrument(window):
    instrument = window.project.add_instrument("Keep this sound", window.project.synth)
    instrument.patch.track = 1
    window.project.selected_instrument = instrument.id
    before = window.project.to_dict()
    window.mixer.select_track(7)
    assert window.project.to_dict() == before


def test_empty_mixer_track_cannot_edit_previous_sound(window):
    window.mixer.select_track(7)
    before = window.project.to_dict()
    panel = window.synth_panel
    assert not panel.sound_cards.isEnabled()
    assert not panel.plugin_button.isEnabled()
    assert not panel.track.isEnabled()
    assert "Empty track" in panel.patch_name.text()
    panel._set_patch("cutoff", 400)
    panel.load_preset("Midnight Brass")
    window.devices.load_plugin("instrument", {"path": "/synthetic/never-load.vst3"})
    assert not window.devices._pending_loads and not window.devices._loading
    assert "+ SOUND" in window.devices.plugin_status
    window.print_synth_to_pad()
    assert window.project.to_dict() == before
    instrument = panel.add_instrument()
    assert instrument.patch.track == 7
    assert window.project.selected_instrument == instrument.id
    assert panel.sound_cards.isEnabled() and panel.plugin_button.isEnabled()


def test_freeze_checks_independent_external_instruments(window):
    from mpclab.premium_workflows import PremiumWorkflowController

    instrument = window.project.add_instrument("External lead", window.project.synth)
    instrument.patch.track = 4
    route = window.engine.external.ensure_route(instrument.id)
    route.instrument = SimpleNamespace(close=lambda: None)
    controller = SimpleNamespace(window=window, _selected_track_index=lambda: 4)
    with pytest.raises(ValueError, match="external instrument"):
        PremiumWorkflowController.freeze_selected_track(controller)


def test_latency_report_lists_independent_external_instrument(window, monkeypatch):
    from mpclab.routing_ui import RoutingController, QMessageBox

    instrument = window.project.add_instrument("Independent lead", window.project.synth)
    route = window.engine.external.ensure_route(instrument.id)
    route.instrument = SimpleNamespace(
        info={"name": "Synthetic VST", "latency_samples": 96}, blocksize=128, close=lambda: None
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *_: None)
    report = RoutingController.show_latency_report(SimpleNamespace(window=window))
    assert "Independent lead" in report
    assert "Synthetic VST" in report
    assert "352 samples" in report

"""Only disposable offscreen DAW windows; no physical streams or user GUI."""

from types import SimpleNamespace

import pytest

from mpclab.application_features import install_application_runtime, attach_application_features
from mpclab.engine import Engine
from mpclab.model import Project
from mpclab.ui.main_window import MainWindow
from mpclab.ui.track_management import add_mixer_track, require_idle_capture


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(Engine, "start", lambda self: None)
    install_application_runtime()
    window = MainWindow(tmp_path, restore_session=False)
    attach_application_features(window)
    yield window
    window.close()


def test_add_track_button_is_undoable_and_routes_to_new_strip(window):
    before = [track.id for track in window.project.tracks]
    window.mixer.add_track_button.click()
    assert len(window.project.tracks) == len(window.mixer.strips) == 9
    assert [track.id for track in window.project.tracks[:8]] == before
    identity = window.project.tracks[8].id
    assert window.mixer.selected == 8
    assert window.engine._tbuf.shape[0] == 9
    window.undo()
    assert len(window.project.tracks) == len(window.mixer.strips) == 8
    assert window.mixer.selected <= 7
    window.redo()
    assert window.project.tracks[8].id == identity
    assert len(window.mixer.strips) == 9


def test_128_track_ui_controls_match_actual_project_and_survive_roundtrip(window):
    project = Project.from_dict(window.project.to_dict())
    while len(project.tracks) < 128:
        project.add_track()
    project.synth.track = 127
    project.vocal_record.mixer_track = 127
    project.pads[0].track = 127
    window._apply_project(Project.from_dict(project.to_dict()))
    assert len(window.mixer.strips) == 128
    assert not window.mixer.add_track_button.isEnabled()
    assert window.synth_panel.track.count() == 128
    assert window.synth_panel.track.currentIndex() == 127
    assert window.vocal_panel.track_box.currentData() == 127
    assert window.automation_panel.target.count() == 257
    assert window.automation_panel.target.findData("track:127:gain") >= 0
    assert window.clip_track.count() == 128
    window.mixer.select_track(127)
    window.mixer.strips[127].fader.setValue(42)
    assert window.project.tracks[127].gain == pytest.approx(0.42)
    assert window.project.tracks[7].gain != pytest.approx(0.42)
    assert window.mixer.rack.index == 127


def test_count_change_stops_only_owned_stream_before_rebuild(window, monkeypatch):
    events = []

    class OwnedTestStream:
        def stop(self):
            events.append("stop")

        def close(self):
            events.append("close")

    def restart(engine):
        assert engine._tbuf.shape[0] == 9
        events.append("restart")

    window.engine.stream = OwnedTestStream()
    monkeypatch.setattr(Engine, "start", restart)
    add_mixer_track(window)
    assert events == ["stop", "close", "restart"]


def test_new_strip_is_connected_to_automation_and_removed_gestures_are_cleared(window):
    add_mixer_track(window)
    controller = window.automation_mode_controller
    target = "track:8:gain"
    controller.set_mode(target, "touch")
    window.engine.mode = "song"
    window.engine.playing = True
    window.engine.beat = 1.0
    fader = window.mixer.strips[8].fader
    fader.sliderPressed.emit()
    fader.setValue(37)
    lane = next(lane for lane in window.project.automation if lane.target == target)
    assert any(point.value == pytest.approx(0.37) for point in lane.points)
    assert target in controller._gesture
    window.engine.playing = False
    window._apply_project(Project())
    assert len(window.mixer.strips) == 8
    assert not controller._gesture and not controller._latched and not controller._manual
    assert window.automation_panel.target.findData(target) == -1


def test_failed_rebuild_restores_project_and_history(window, monkeypatch):
    prepare = window.engine.prepare_fx

    def fail_new_layout(project=None):
        if len((project or window.engine.project).tracks) == 9:
            raise RuntimeError("Simulated preparation failure")
        return prepare(project)

    before = window.project.to_dict()
    undo, redo, dirty = list(window._undo), list(window._redo), window._dirty
    monkeypatch.setattr(window.engine, "prepare_fx", fail_new_layout)
    with pytest.raises(RuntimeError, match="Simulated"):
        add_mixer_track(window)
    assert window.project.to_dict() == before
    assert window.engine.project is window.project
    assert len(window.engine._tbuf) == 8
    assert window._undo == undo and window._redo == redo and window._dirty == dirty


@pytest.mark.parametrize("extra_tracks", [0, 1])
def test_project_replacement_rebinds_unchanged_selected_rack(window, extra_tracks):
    from PySide6.QtWidgets import QLabel
    from mpclab.ui.fxrack import ParamSlider

    previous = window.project
    previous.tracks[0].fx.low = 1.0
    window.mixer.sync()
    replacement = Project.from_dict(previous.to_dict())
    for _ in range(extra_tracks):
        replacement.add_track()
    replacement.tracks[0].name = "Replacement track"
    replacement.tracks[0].fx.low = 9.0
    window._apply_project(replacement)

    rack = window.mixer.rack
    assert rack.index == window.mixer.selected == 0
    assert "1 · Replacement track" in {label.text() for label in rack.findChildren(QLabel)}
    low = next(
        control for control in rack.findChildren(ParamSlider) if control.name.text() == "LOW"
    )
    assert low.value.text() == "+9.0dB"
    low.slider.setValue(low._to_slider(-3.0))
    assert replacement.tracks[0].fx.low == pytest.approx(-3.0)
    assert previous.tracks[0].fx.low == pytest.approx(1.0)


def test_live_playback_cannot_silently_change_graph(window):
    window.engine.playing = True
    with pytest.raises(RuntimeError, match="Stop playback"):
        add_mixer_track(window)
    assert len(window.project.tracks) == 8
    window.engine.playing = False


@pytest.mark.parametrize("field", ["busy", "recording", "temporary_path", "counting"])
def test_recording_guards_protect_unsaved_takes(field):
    window = SimpleNamespace(
        track_capture=SimpleNamespace(busy=field == "busy"),
        vocal_panel=SimpleNamespace(
            _counting=field == "counting",
            recorder=SimpleNamespace(
                recording=field == "recording",
                temporary_path="take" if field == "temporary_path" else None,
            ),
        ),
    )
    with pytest.raises(RuntimeError, match="recording"):
        require_idle_capture(window)

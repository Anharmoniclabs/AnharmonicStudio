"""Hand input never needs a camera for mapping, persistence or render tests."""

import numpy as np
import pytest

from mpclab.model import Project
from mpclab.music import AutomationLane, AutomationPoint, read_automation, target_range
from mpclab.prism_motion import (
    GestureFrame,
    GestureTake,
    PinchMapper,
    automation_parameters,
    CONTROLS,
    DEFAULT_MAPPING,
)


def hand(timestamp, x=0, pinch=True):
    points = [(0.5, 0.7, 0.0)] * 21
    points[5] = (0.4, 0.5, 0)
    points[17] = (0.6, 0.5, 0)
    points[4] = (0.5 + x, 0.4, 0)
    points[8] = (0.51 + x if pinch else 0.7 + x, 0.4, 0)
    points[12] = (0.45, 0.2, 0)
    points[20] = (0.65, 0.3, 0)
    return GestureFrame(timestamp, tuple(points))


def test_pinch_has_pickup_debounce_smoothing_and_no_jump():
    mapper = PinchMapper()
    current = dict(zip(DEFAULT_MAPPING, (0.3, 0.4, 0.5), strict=True))
    assert mapper.update(hand(0), current) == {}
    assert mapper.update(hand(0.08), current) == pytest.approx(current)
    changed = mapper.update(hand(0.12, 0.2), current)
    assert 0.3 < changed["54"] < 0.6
    assert changed["56"] == pytest.approx(0.4)
    assert mapper.update(hand(0.2, pinch=False), current) == {}
    assert not mapper.active
    assert mapper.update(hand(0.3, -0.1), changed) == {}
    assert mapper.update(hand(0.4, -0.1), changed) == pytest.approx(changed)


@pytest.mark.parametrize(
    "frame",
    [
        GestureFrame(0.2, ()),
        GestureFrame(float("nan"), ()),
        GestureFrame(0.2, ((float("nan"), 0, 0),) * 21),
        hand(1),
    ],
)
def test_lost_invalid_or_stale_tracking_disengages(frame):
    mapper = PinchMapper()
    mapper.update(hand(0), {})
    mapper.update(hand(0.1), {})
    assert mapper.active
    assert mapper.update(frame, {}) == {}
    assert not mapper.active


def test_mapping_rejects_duplicate_targets_and_clamps_motion():
    with pytest.raises(ValueError):
        PinchMapper(("54", "54", "57"))
    mapper = PinchMapper(sensitivity=4)
    mapper.update(hand(0), {"54": 0.99})
    mapper.update(hand(0.1), {"54": 0.99})
    for i in range(2, 20):
        values = mapper.update(hand(i / 10, x=0.45), {})
    assert all(0 <= v <= 1 for v in values.values())


def test_gesture_overdub_preserves_other_curves_and_project_roundtrip():
    project = Project()
    existing = AutomationLane(
        "prism:54", [AutomationPoint(0, 0.2), AutomationPoint(4, 0.4), AutomationPoint(8, 0.8)]
    )
    unrelated = AutomationLane("master", [AutomationPoint(0, 0.7)])
    project.automation = [existing, unrelated]
    take = GestureTake()
    assert take.write(project, 3, {"54": 0.9})
    assert not take.write(project, 3.001, {"54": 0.8})
    assert take.write(project, 5, {"54": 0.8})
    take.end()
    assert existing.points[0] == AutomationPoint(0, 0.2)
    assert existing.points[-1] == AutomationPoint(8, 0.8)
    assert all(p.beat != 4 for p in existing.points)
    assert float(existing.values([6])[0]) == pytest.approx(0.6)
    assert project.automation[1] is unrelated
    restored = Project.from_dict(project.to_dict())
    assert automation_parameters(restored, 5)["54"] == pytest.approx(0.8)
    assert automation_parameters(restored, 5, {"54"}) == {}
    existing.enabled = False
    assert automation_parameters(project, 5) == {}
    assert target_range("prism:54") == (0, 1)
    with pytest.raises(ValueError):
        read_automation([{"target": "prism:999", "points": []}])


def test_control_ids_match_the_plugin_contract():
    from mpclab.ui.prism_controls import SPECS

    assert SPECS[29]["id"] == "space"
    assert all(int(key) < len(SPECS) for key in CONTROLS)


def test_camera_is_off_on_dialog_creation_and_close_releases_override(window, monkeypatch):  # noqa: F811
    from mpclab.ui.prism_camera import PrismCameraDialog

    dialog = PrismCameraDialog(window.synth_panel.prism_surface)
    assert dialog.session.process is None
    assert not dialog.write.isChecked()
    window.engine.prism_gesture_targets = frozenset({"54"})
    dialog.close()
    assert not window.engine.prism_gesture_targets
    assert not dialog.timer.isActive()


def test_live_and_offline_transport_forward_parameter_values():
    from mpclab.external_dsp import ExternalDSP, OfflinePlugins

    class Plugin:
        info = {"name": "Anharmonic Prism"}

        def render(self, audio, frames, midi, **kwargs):
            self.parameters = kwargs["parameters"]
            return np.zeros((frames, 2), np.float32)

    plugin = Plugin()
    routing = ExternalDSP()
    routing.instrument = plugin
    routing.render_instrument(np.zeros((128, 2), np.float32), 128, 48000, 120, {"54": 0.6})
    assert plugin.parameters == {"54": 0.6}
    offline = OfflinePlugins.__new__(OfflinePlugins)
    offline.instrument = plugin
    offline.bpm = 120
    offline.sample_rate = 48000
    offline.events = []
    offline.index = 0
    offline.render_instrument(np.zeros((128, 2), np.float32), 0, 128, {"54": 0.2})
    assert plugin.parameters == {"54": 0.2}


def test_real_prism_applies_automation_in_audio_request():
    from mpclab.plugin_host import IsolatedPlugin
    from mpclab.prism import bundled_plugin

    if bundled_plugin() is None:
        pytest.skip("Build Prism first")
    with_plugin = IsolatedPlugin({"path": str(bundled_plugin())}, 48000)
    try:
        with_plugin.render(None, 128, reset=True)
        with_plugin.render(None, 128, [([0x90, 60, 100], 0)], parameters={"54": 0.9})
        loud = np.concatenate(
            [with_plugin.render(None, 128, parameters={"12": 1.0}) for _ in range(32)]
        )
        quiet = np.concatenate(
            [with_plugin.render(None, 128, parameters={"12": 0.0, "54": 0.0}) for _ in range(64)]
        )
        assert np.max(np.abs(loud)) > 0.01
        assert np.sqrt(np.mean(quiet[-1024:] ** 2)) < np.sqrt(np.mean(loud**2))
    finally:
        with_plugin.close()


from tests.test_product_hardening_ui import window  # noqa: F401, E402


def test_saved_gesture_changes_song_export_with_camera_absent(tmp_path):
    from mpclab.engine import Engine
    from mpclab.library import Library
    from mpclab.model import Clip, Row
    from mpclab.music import Note
    from mpclab.prism import bundled_plugin

    path = bundled_plugin()
    if path is None:
        pytest.skip("Build Prism first")
    project = Project()
    project.bpm = 120
    project.plugins = {"instrument": {"path": str(path), "parameters": {"12": 1.0}}}
    project.pattern().notes = [Note(60, 0, 1.8, 0.8)]
    project.rows = [Row(clips=[Clip(ref=project.pattern().id, length_beats=2)])]
    project.automation = [AutomationLane("prism:12", [AutomationPoint(0, 0.0)])]
    project.save(tmp_path / "motion.json")
    engine = Engine(Library(tmp_path / "library"), blocksize=512)
    engine.project = Project.load(tmp_path / "motion.json")
    filtered = engine.render_offline("song", tail=0)
    engine.project.automation[0].enabled = False
    open_filter = engine.render_offline("song", tail=0)
    assert np.isfinite(filtered).all()
    assert float(np.sqrt(np.mean(open_filter**2))) > float(np.sqrt(np.mean(filtered**2))) * 1.5


def test_camera_panel_records_only_when_armed_in_song_playback(window, monkeypatch):  # noqa: F811
    from types import SimpleNamespace
    from mpclab.ui import prism_camera

    parameters = {key: 0.2 for key in ("12", "26", "29", "57")}
    bridge = SimpleNamespace(
        info={"name": "Anharmonic Prism", "parameters": {}},
        error="",
        set_parameters=lambda values: parameters.update(values),
        close=lambda: None,
    )
    window.engine.external.instrument = bridge
    window.project.plugins = {
        "instrument": {"path": "Anharmonic Prism.vst3", "parameters": dict(parameters)}
    }
    panel = window.synth_panel.prism_surface
    panel.values.update(parameters)
    dialog = prism_camera.PrismCameraDialog(panel)
    clock = [100.0]
    pending = [None]
    monkeypatch.setattr(prism_camera.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(dialog.session, "poll", lambda: pending[0])
    rgb = np.zeros((36, 48, 3), np.uint8)

    def frame(t, x):
        clock[0] = t
        window.engine.beat = t - 99
        pending[0] = ("frame", t, rgb, hand(t, x).points)
        dialog.tick()

    try:
        frame(100.0, 0)
        frame(100.1, 0)
        frame(100.2, 0.1)
        assert parameters["12"] > 0.2
        assert not window.project.automation
        dialog.write.setChecked(True)
        window.engine.playing = True
        window.engine.mode = "song"
        frame(100.3, 0.1)
        frame(100.4, 0.1)
        frame(100.5, 0.2)
        assert len(window.project.automation) == 1
        assert window.engine.prism_gesture_targets == frozenset({"12"})
        window.engine.playing = False
        pending[0] = None
        dialog.tick()
        assert not window.engine.prism_gesture_targets
        counts = [len(lane.points) for lane in window.project.automation]
        frame(100.6, 0.2)
        frame(100.7, 0.3)
        assert [len(lane.points) for lane in window.project.automation] == counts
    finally:
        dialog.close()
        window.engine.external.instrument = None


@pytest.mark.parametrize("finger,key", list(enumerate(("12", "26", "29", "57"))))
def test_each_finger_controls_only_its_effect(finger, key):
    from mpclab.prism_motion import FingerFXMapper

    def frame(t, offset=0, closed=True):
        points = list(hand(t, pinch=False).points)
        for tip in (8, 12, 16, 20):
            points[tip] = (0.8, 0.1, 0)
        points[4] = (0.5 + offset, 0.4, 0)
        if closed:
            points[(8, 12, 16, 20)[finger]] = (0.51 + offset, 0.4, 0)
        return GestureFrame(t, tuple(points))

    mapper = FingerFXMapper()
    assert mapper.update(frame(0), {key: 0.3}) == {}
    assert mapper.update(frame(0.1), {key: 0.3}) == pytest.approx({key: 0.3})
    values = mapper.update(frame(0.2, 0.1), {key: 0.3})
    assert set(values) == {key}
    assert 0.3 < values[key] < 0.5
    assert mapper.finger == finger
    assert mapper.update(frame(0.3, closed=False), values) == {}
    assert not mapper.active
    mapper.update(frame(0.4), values)
    assert mapper.update(frame(0.5), values) == pytest.approx(values)
    assert mapper.update(GestureFrame(0.6, ()), values) == {}
    assert not mapper.active


def test_finger_switch_requires_a_fresh_pinch_and_pickup():
    from mpclab.prism_motion import FingerFXMapper

    mapper = FingerFXMapper()
    mapper.update(hand(0), {"12": 0.4})
    mapper.update(hand(0.1), {"12": 0.4})
    points = list(hand(0.2, pinch=False).points)
    points[12] = (0.51, 0.4, 0)
    assert mapper.update(GestureFrame(0.2, tuple(points)), {}) == {}
    assert mapper.update(GestureFrame(0.3, tuple(points)), {}) == {}
    assert mapper.update(GestureFrame(0.4, tuple(points)), {"26": 0.7}) == pytest.approx(
        {"26": 0.7}
    )
    assert mapper.update(GestureFrame(1.0, tuple(points)), {}) == {}

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from mpclab.engine import Engine

from mpclab.automation_mode_state import install_automation_mode_state
from mpclab.automation_modes import TOUCH_RETURN_BEATS, attach_automation_modes
from mpclab.music import AutomationLane, AutomationPoint
from mpclab.premium_workflows import attach_premium_workflows, install_premium_runtime
from mpclab.ui.main_window import MainWindow


@pytest.fixture(autouse=True)
def no_physical_audio(monkeypatch):
    monkeypatch.setattr(Engine, "start", lambda self: None)


def make_window(tmp_path):
    install_premium_runtime()
    install_automation_mode_state()
    window = MainWindow(tmp_path, restore_session=False)
    commands = attach_premium_workflows(window)
    controller = attach_automation_modes(window, commands)
    return window, controller


def lane_for(window, target):
    return next(item for item in window.project.automation if item.target == target)


def test_read_locks_automated_fader_but_touch_keeps_it_movable(tmp_path):
    window, controller = make_window(tmp_path)
    try:
        window.engine.mode = "song"
        window.project.automation = [
            AutomationLane(
                "track:0:gain",
                points=[AutomationPoint(0.0, 0.2), AutomationPoint(4.0, 0.8)],
            )
        ]
        strip = window.mixer.strips[0]
        strip.update_meter()
        assert not strip.fader.isEnabled()

        controller.set_mode("track:0:gain", "touch")
        strip.update_meter()
        assert strip.fader.isEnabled()
    finally:
        window.close()


def test_touch_overwrites_only_gesture_then_returns_to_prior_envelope(tmp_path):
    window, controller = make_window(tmp_path)
    try:
        target = "track:0:gain"
        window.project.automation = [
            AutomationLane(
                target,
                points=[AutomationPoint(0.0, 0.2), AutomationPoint(4.0, 0.8)],
            )
        ]
        controller.set_mode(target, "touch")
        window.engine.mode = "song"
        window.engine.playing = True
        window.engine.beat = 1.0
        controller.begin_gesture(target, 0.35)
        window.project.tracks[0].gain = 0.55
        controller.control_changed(target, 0.55)
        window.engine.beat = 2.0
        controller.control_changed(target, 0.65)
        controller.end_gesture(target)

        lane = lane_for(window, target)
        values = {point.beat: point.value for point in lane.points}
        assert values[1.0] == pytest.approx(0.55)
        assert values[2.0] == pytest.approx(0.65)
        return_beat = round(2.0 + TOUCH_RETURN_BEATS, 6)
        assert values[return_beat] == pytest.approx(0.2 + return_beat / 4.0 * 0.6)
        assert values[4.0] == pytest.approx(0.8)
        assert not controller.manual_override(target)
    finally:
        window.close()


def test_latch_keeps_writing_after_release_until_stop(tmp_path):
    window, controller = make_window(tmp_path)
    try:
        target = "track:0:pan"
        controller.set_mode(target, "latch")
        window.engine.mode = "song"
        window.engine.playing = True
        window.engine.beat = 1.0
        controller.begin_gesture(target, 0.1)
        window.project.tracks[0].pan = 0.4
        controller.control_changed(target, 0.4)
        controller.end_gesture(target)
        assert controller.manual_override(target)

        window.engine.beat = 2.0
        controller.tick()
        lane = lane_for(window, target)
        assert any(point.beat == 2.0 and point.value == pytest.approx(0.4) for point in lane.points)

        window.engine.playing = False
        controller.tick()
        assert not controller.manual_override(target)
    finally:
        window.close()


def test_write_records_underlying_manual_value_for_whole_transport_pass(tmp_path):
    window, controller = make_window(tmp_path)
    try:
        target = "master"
        window.project.automation = [
            AutomationLane(
                target,
                points=[AutomationPoint(0.0, 0.2), AutomationPoint(8.0, 0.8)],
            )
        ]
        window.project.master = 0.72
        controller.set_mode(target, "write")
        window.engine.mode = "song"
        window.engine.beat = 2.0
        window.engine.playing = True
        controller.tick()
        window.engine.beat = 3.0
        controller.tick()
        window.engine.playing = False
        controller.tick()

        lane = lane_for(window, target)
        written = {point.beat: point.value for point in lane.points}
        assert written[2.0] == pytest.approx(0.72)
        assert written[3.0] == pytest.approx(0.72)
        assert written[8.0] == pytest.approx(0.8)
    finally:
        window.close()


def test_last_touched_control_selects_matching_automation_lane(tmp_path):
    window, controller = make_window(tmp_path)
    try:
        controller.control_changed("track:3:pan", -0.25)
        assert window.automation_panel.target.currentData() == "track:3:pan"
    finally:
        window.close()

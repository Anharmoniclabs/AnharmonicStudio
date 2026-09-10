"""Professional automation write modes layered over existing envelope playback."""

from __future__ import annotations

from copy import deepcopy

import numpy as np
from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QWidget

from .automation_mode_state import AUTOMATION_MODES, automation_mode, set_automation_mode
from .music import AutomationLane, automation_targets, automation_values
from .workflow_commands import CommandSpec

MIN_WRITE_BEATS = 1.0 / 32.0
TOUCH_RETURN_BEATS = 0.125
MAX_AUTOMATION_POINTS = 100_000


def _project_value(project, target: str) -> float:
    if target == "master":
        return float(project.master)
    _track, raw_index, parameter = target.split(":", 2)
    track = project.tracks[int(raw_index)]
    return float(getattr(track, parameter))


def _lane(project, target: str, create: bool = False) -> AutomationLane | None:
    lane = next((item for item in project.automation if item.target == target), None)
    if lane is None and create:
        lane = AutomationLane(target)
        project.automation = project.automation + [lane]
    return lane


def attach_automation_modes(window, command_controller):
    existing = getattr(window, "automation_mode_controller", None)
    if existing is not None:
        return existing
    controller = AutomationModeController(window, command_controller)
    window.automation_mode_controller = controller
    controller.attach_controls()
    return controller


class AutomationModeController(QObject):
    """Record mixer gestures into the existing sample-time automation lanes."""

    def __init__(self, window, command_controller):
        super().__init__(window)
        self.app = window
        self.command_controller = command_controller
        self.registry = command_controller.registry
        self._gesture: set[str] = set()
        self._latched: set[str] = set()
        self._passes: dict[str, dict] = {}
        self._manual: dict[str, float] = {}
        self._was_playing = False
        self._last_touched = "master"
        self._follow_last_touched = True
        self._closed = False
        self.mode_combo = None
        self.follow_checkbox = None
        self._register_commands()
        command_controller._reindex_bindings()
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        window.destroyed.connect(self.shutdown)

    def _register_commands(self) -> None:
        for mode in AUTOMATION_MODES:
            spec = CommandSpec(
                f"automation.mode.{mode}",
                f"Automation mode: {mode.title()}",
                lambda mode=mode: self.set_current_mode(mode),
                category="Automation",
                keywords=("automation", "record", mode),
            )
            try:
                self.registry.register(spec)
            except ValueError as exc:
                if "duplicate command id" not in str(exc):
                    raise

    def attach_controls(self) -> None:
        self._install_mixer_display_hook()
        for index, strip in enumerate(self.app.mixer.strips):
            self._connect_slider(strip.fader, f"track:{index}:gain", 100.0)
            self._connect_slider(strip.pan, f"track:{index}:pan", 100.0)
        self._connect_slider(self.app.mixer.master_strip.fader, "master", 100.0)
        master = getattr(self.app, "master_slider", None)
        if master is not None:
            self._connect_slider(master, "master", 100.0)
        self._inject_panel_controls()
        self.sync_panel()
        self._sync_master_slider()

    def _connect_slider(self, slider, target: str, scale: float) -> None:
        if getattr(slider, "_automation_modes_connected", False):
            return
        slider._automation_modes_connected = True
        slider.sliderPressed.connect(
            lambda target=target, slider=slider, scale=scale: self.begin_gesture(
                target, slider.value() / scale
            )
        )
        slider.valueChanged.connect(
            lambda value, target=target, scale=scale: self.control_changed(target, value / scale)
        )
        slider.sliderReleased.connect(lambda target=target: self.end_gesture(target))

    def _inject_panel_controls(self) -> None:
        panel = self.app.automation_panel
        row = QWidget(panel)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Write mode"))
        self.mode_combo = QComboBox()
        for mode in AUTOMATION_MODES:
            self.mode_combo.addItem(mode.title(), mode)
        self.mode_combo.setToolTip(
            "Read follows the envelope. Write overwrites while transport runs. "
            "Touch writes only while held. Latch keeps writing after release until Stop."
        )
        self.mode_combo.currentIndexChanged.connect(self._mode_combo_changed)
        layout.addWidget(self.mode_combo)
        self.follow_checkbox = QCheckBox("Follow last touched")
        self.follow_checkbox.setChecked(True)
        self.follow_checkbox.toggled.connect(self._set_follow)
        layout.addWidget(self.follow_checkbox)
        layout.addStretch(1)
        panel.layout().insertWidget(2, row)
        panel.target.currentIndexChanged.connect(self.sync_panel)

    @staticmethod
    def _install_mixer_display_hook() -> None:
        from .ui.mixer import Strip

        if getattr(Strip, "_automation_modes_hooked", False):
            return
        original = Strip.update_meter

        def update_meter(strip):
            original(strip)
            controller = getattr(strip.app, "automation_mode_controller", None)
            if controller is None:
                return
            controls = (
                [("master", strip.fader, 100.0)]
                if strip.master
                else [
                    (f"track:{strip.index}:gain", strip.fader, 100.0),
                    (f"track:{strip.index}:pan", strip.pan, 100.0),
                ]
            )
            for target, slider, scale in controls:
                lane = _lane(strip.app.project, target)
                active = bool(
                    lane and lane.enabled and lane.points and strip.app.engine.mode == "song"
                )
                if not active:
                    slider.setEnabled(True)
                    continue
                mode = controller.mode(target)
                slider.setEnabled(mode != "read")
                if controller.manual_override(target):
                    value = controller.manual_value(target)
                    wanted = round(value * scale)
                    if slider.value() != wanted:
                        slider.blockSignals(True)
                        slider.setValue(wanted)
                        slider.blockSignals(False)
                slider.setToolTip(
                    "Automation read: switch this target to Write, Touch, or Latch to move it"
                    if mode == "read"
                    else f"Automation {mode.title()} · {controller.mode_help(mode)}"
                )

        Strip.update_meter = update_meter
        Strip._automation_modes_hooked = True

    @staticmethod
    def mode_help(mode: str) -> str:
        return {
            "read": "follow the existing envelope",
            "write": "overwrite continuously while Song plays",
            "touch": "write while the control is held, then return to the envelope",
            "latch": "write after first touch until transport stops",
        }[mode]

    def mode(self, target: str) -> str:
        return automation_mode(self.app.project, target)

    def manual_value(self, target: str) -> float:
        return float(self._manual.get(target, _project_value(self.app.project, target)))

    def manual_override(self, target: str) -> bool:
        mode = self.mode(target)
        if mode == "write":
            return True
        if mode == "touch":
            return target in self._gesture
        if mode == "latch":
            return target in self._gesture or target in self._latched
        return False

    def set_current_mode(self, mode: str) -> None:
        target = self.app.automation_panel.target.currentData()
        if target:
            self.set_mode(str(target), mode)

    def set_mode(self, target: str, mode: str) -> None:
        if mode not in AUTOMATION_MODES:
            raise ValueError("unsupported automation mode")
        if self.mode(target) == mode:
            return
        self.app.snapshot()
        set_automation_mode(self.app.project, target, mode)
        self._gesture.discard(target)
        self._latched.discard(target)
        self._passes.pop(target, None)
        self._manual[target] = _project_value(self.app.project, target)
        self.app._set_dirty(True)
        self.sync_panel()
        self._sync_master_slider()

    def _mode_combo_changed(self, *_args) -> None:
        if self.mode_combo is None:
            return
        mode = self.mode_combo.currentData()
        target = self.app.automation_panel.target.currentData()
        if target and mode:
            self.set_mode(str(target), str(mode))

    def _set_follow(self, enabled: bool) -> None:
        self._follow_last_touched = bool(enabled)

    def _select_last_touched(self, target: str) -> None:
        self._last_touched = target
        if not self._follow_last_touched:
            return
        panel = self.app.automation_panel
        index = panel.target.findData(target)
        if index >= 0 and panel.target.currentIndex() != index:
            panel.target.setCurrentIndex(index)

    def sync_panel(self, *_args) -> None:
        if self.mode_combo is None:
            return
        target = str(self.app.automation_panel.target.currentData() or "master")
        mode = self.mode(target)
        index = self.mode_combo.findData(mode)
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentIndex(max(0, index))
        self.mode_combo.blockSignals(False)
        self.app.automation_panel.update_status()

    def _transport_writing(self) -> bool:
        return bool(self.app.engine.playing and self.app.engine.mode == "song")

    def begin_gesture(self, target: str, value: float) -> None:
        self._select_last_touched(target)
        self._manual[target] = float(value)
        self._gesture.add(target)
        if not self._transport_writing() or self.mode(target) == "read":
            return
        if self.mode(target) == "latch":
            self._latched.add(target)
        self._begin_pass(target, snapshot=self.mode(target) != "write")
        self._write_point(target, value, force=True)

    def end_gesture(self, target: str) -> None:
        mode = self.mode(target)
        if target in self._gesture and mode == "touch" and self._transport_writing():
            self._write_point(target, self.manual_value(target), force=True)
            self._finish_touch(target)
        self._gesture.discard(target)
        if mode != "latch":
            self._passes.pop(target, None)

    def control_changed(self, target: str, value: float) -> None:
        self._manual[target] = float(value)
        self._select_last_touched(target)
        if not self._transport_writing():
            return
        mode = self.mode(target)
        if mode == "read":
            return
        if mode == "touch" and target not in self._gesture:
            return
        if mode == "latch" and target not in self._latched:
            return
        self._begin_pass(target, snapshot=mode != "write")
        self._write_point(target, value, force=True)

    def _begin_pass(self, target: str, *, snapshot: bool) -> None:
        if target in self._passes:
            return
        if snapshot:
            self.app.snapshot()
        lane = _lane(self.app.project, target)
        self._passes[target] = {
            "last_beat": None,
            "last_value": self.manual_value(target),
            "original": deepcopy(lane),
            "seed": self.manual_value(target),
        }

    def _write_point(self, target: str, value: float, *, force: bool = False) -> None:
        state = self._passes.get(target)
        if state is None:
            return
        beat = round(max(0.0, float(self.app.engine.beat)), 6)
        last = state["last_beat"]
        if last is not None and beat + 1e-7 < last:
            # Song-loop wrap starts a new overwrite segment without corrupting
            # the previous pass range.
            state["last_beat"] = None
            last = None
        if (
            not force
            and last is not None
            and beat - last < MIN_WRITE_BEATS
            and abs(float(value) - state["last_value"]) < 1e-4
        ):
            return
        lane = _lane(self.app.project, target, create=True)
        lane.enabled = True
        if len(lane.points) >= MAX_AUTOMATION_POINTS and not any(
            point.beat == beat for point in lane.points
        ):
            self.app.status.showMessage(
                "automation lane reached its 100,000-point safety limit", 5000
            )
            return
        if last is not None and beat >= last:
            lane.points = [point for point in lane.points if not (last < point.beat <= beat)]
        lane.put(beat, float(value))
        state["last_beat"] = beat
        state["last_value"] = float(value)
        self.app._set_dirty(True)
        if self.app.automation_panel.isVisible():
            self.app.automation_panel.canvas.update()

    def _original_value(self, state: dict, target: str, beat: float) -> float:
        original = state.get("original")
        if original is not None and original.enabled and original.points:
            return float(original.values(np.asarray([beat], dtype=float))[0])
        return float(state.get("seed", _project_value(self.app.project, target)))

    def _finish_touch(self, target: str) -> None:
        state = self._passes.get(target)
        if state is None:
            return
        end = max(0.0, float(self.app.engine.beat))
        return_beat = round(end + TOUCH_RETURN_BEATS, 6)
        lane = _lane(self.app.project, target, create=True)
        lane.points = [point for point in lane.points if not (end < point.beat < return_beat)]
        lane.put(return_beat, self._original_value(state, target, return_beat))
        self._passes.pop(target, None)
        self.app._set_dirty(True)

    def _finish_transport(self) -> None:
        for target in tuple(self._passes):
            mode = self.mode(target)
            if mode in {"write", "latch"}:
                self._write_point(target, self.manual_value(target), force=True)
        self._passes.clear()
        self._latched.clear()

    def _sync_master_slider(self) -> None:
        slider = getattr(self.app, "master_slider", None)
        if slider is None:
            return
        lane = _lane(self.app.project, "master")
        active = bool(lane and lane.enabled and lane.points and self.app.engine.mode == "song")
        if not active:
            slider.setEnabled(True)
            return
        mode = self.mode("master")
        slider.setEnabled(mode != "read")
        if self.manual_override("master"):
            value = self.manual_value("master")
        else:
            beat = max(0.0, float(self.app.engine.beat))
            value = float(
                automation_values(
                    self.app.project,
                    "master",
                    np.asarray([beat], dtype=float),
                    self.app.project.master,
                )[0]
            )
        wanted = round(value * 100.0)
        if slider.value() != wanted:
            slider.blockSignals(True)
            slider.setValue(wanted)
            slider.blockSignals(False)
        slider.setToolTip(
            "Automation read: switch Master to Write, Touch, or Latch to move it"
            if mode == "read"
            else f"Automation {mode.title()} · {self.mode_help(mode)}"
        )

    def tick(self) -> None:
        if self._closed:
            return
        playing = self._transport_writing()
        if playing and not self._was_playing:
            # A gesture can begin and end between Play and the first timer tick.
            # Stop already clears previous passes; retain this transport's latch.
            write_targets = [
                target for target in automation_targets() if self.mode(target) == "write"
            ]
            if write_targets:
                self.app.snapshot()
                for target in write_targets:
                    self._manual.setdefault(target, _project_value(self.app.project, target))
                    self._begin_pass(target, snapshot=False)
                    self._write_point(target, self.manual_value(target), force=True)
        elif not playing and self._was_playing:
            self._finish_transport()

        if playing:
            for target in tuple(self._passes):
                mode = self.mode(target)
                if (
                    mode == "write"
                    or (mode == "touch" and target in self._gesture)
                    or (mode == "latch" and target in self._latched)
                ):
                    self._write_point(target, self.manual_value(target))
        self._was_playing = playing
        self._sync_master_slider()

        # The standard mixer meter timer paints the read value. This controller's
        # hook immediately keeps actively written controls on their manual value.
        if self.app.automation_panel.isVisible():
            self.app.automation_panel.canvas.update()

    def shutdown(self, *_args) -> None:
        if self._closed:
            return
        self._closed = True
        self.timer.stop()
        self._passes.clear()
        self._gesture.clear()
        self._latched.clear()

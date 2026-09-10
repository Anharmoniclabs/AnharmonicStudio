"""Loop, punch and automatic take-lane recording layered over TrackCapture.

The existing recorder remains the simple default. Advanced modes are opt-in
project workflow metadata, so old projects and direct MainWindow tests retain
their original behavior. Loop audio is never duplicated: each pass is a clip
window onto the one captured source file.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import math

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
)

from .model import Clip, Pattern, Row, uid
from .music import Note
from .workflow_commands import CommandSpec
from .workflow_state import ensure_workflow, validate_workflow

_CAPTURE_INSTALLED = False

_DEFAULTS = {
    "loop_takes": False,
    "auto_take_lanes": True,
    "loop_passes": 0,
    "punch_enabled": False,
    "punch_start": 0.0,
    "punch_end": 4.0,
    "pre_roll_bars": 1,
}


def recording_settings(project) -> dict:
    raw = ensure_workflow(project).get("recording", {})
    result = dict(_DEFAULTS)
    if isinstance(raw, dict):
        for key in _DEFAULTS:
            if key in raw:
                result[key] = raw[key]
    return result


def _store_settings(project, values: dict) -> dict:
    workflow = deepcopy(ensure_workflow(project))
    current = dict(workflow.get("recording", {}))
    current.update(values)
    groups = current.get("take_groups", [])
    current = {**_DEFAULTS, **current}
    if groups:
        current["take_groups"] = groups
    workflow["recording"] = current
    checked = validate_workflow(workflow)
    project.workflow = checked
    return checked["recording"]


def _session(capture):
    value = getattr(capture, "_advanced_recording", None)
    return value if isinstance(value, dict) else None


def _observe_beat(capture) -> float:
    session = _session(capture)
    current = float(capture.app.engine.beat)
    if not session or session.get("mode") != "loop":
        return current
    last = float(session.get("last_transport_beat", current))
    if current < last - 1e-7:
        session["cycles"] = int(session.get("cycles", 0)) + 1
    session["last_transport_beat"] = current
    return current + int(session.get("cycles", 0)) * float(session["loop_length"])


def _trim_note(note: Note, start: float, end: float, origin: float = 0.0) -> Note | None:
    left = max(start, float(note.start))
    right = min(end, float(note.start + note.duration))
    if right <= left:
        return None
    return replace(note, start=left - origin, duration=max(0.03125, right - left))


def _unwrap_arp_notes(notes: list[Note], loop_length: float) -> list[Note]:
    if not notes:
        return []
    result = []
    offset = 0.0
    previous = -1.0
    for note in notes:
        raw = float(note.start)
        candidate = raw + offset
        if previous >= 0.0 and candidate + 1e-7 < previous:
            offset += loop_length
            candidate = raw + offset
        result.append(replace(note, start=candidate))
        previous = candidate
    return result


def _new_lane(source: Row, number: int) -> Row:
    return Row(
        name=f"{source.name} · Take {number}",
        color=source.color,
        record_source=source.record_source,
        record_track=source.record_track,
    )


def _append_take_group(app, source: Row, lanes: list[Row], start: float, end: float) -> dict:
    recording = ensure_workflow(app.project).setdefault("recording", dict(_DEFAULTS))
    groups = recording.setdefault("take_groups", [])
    active_lane = lanes[-1].id if lanes else ""
    for lane in lanes:
        lane.mute = lane.id != active_lane
    group = {
        "id": f"takes:{uid()}",
        "name": f"{source.name} Takes {len(groups) + 1}",
        "source_row": source.id,
        "lanes": [lane.id for lane in lanes],
        "start": float(start),
        "end": float(end),
        "active_lane": active_lane,
    }
    groups.append(group)
    # Validate after construction so an accidental unbounded edit never gets
    # persisted or handed to later comping code.
    app.project.workflow = validate_workflow(app.project.workflow)
    return group


def _split_audio_loop(capture, row: Row, clip: Clip, session: dict) -> list[Row]:
    app = capture.app
    source = app.library.clips.get(clip.ref)
    if source is None:
        return []
    bpm = float(app.project.bpm)
    loop_beats = float(session["loop_length"])
    pass_seconds = loop_beats * 60.0 / bpm
    available = float(clip.source_length or source.duration)
    configured = int(session.get("loop_passes", 0))
    possible = max(1, math.ceil(max(0.0, available - 1e-9) / pass_seconds))
    count = min(possible, configured) if configured > 0 else possible
    latency = capture.settings.input_latency_ms * bpm / 60000.0

    row.clips.remove(clip)
    insert_at = app.project.rows.index(row) + 1
    lanes = []
    for index in range(count):
        offset = index * pass_seconds
        duration = min(pass_seconds, available - offset)
        if duration < 0.08:
            break
        lane = _new_lane(row, index + 1)
        lane.clips.append(
            Clip(
                kind="audio",
                ref=clip.ref,
                start_beat=max(0.0, float(session["loop_start"]) - latency),
                length_beats=duration * bpm / 60.0,
                offset=float(clip.offset) + offset,
                source_length=duration,
                gain=clip.gain,
                track=clip.track,
                loop=False,
                reverse=clip.reverse,
                mute=clip.mute,
            )
        )
        app.project.rows.insert(insert_at + len(lanes), lane)
        lanes.append(lane)
    if lanes:
        _append_take_group(
            app,
            row,
            lanes,
            float(session["loop_start"]),
            float(session["loop_end"]),
        )
    return lanes


def _split_note_loop(capture, row: Row, clip: Clip, session: dict) -> list[Row]:
    app = capture.app
    source_pattern = next(
        (pattern for pattern in app.project.patterns if pattern.id == clip.ref), None
    )
    if source_pattern is None:
        return []
    loop_beats = float(session["loop_length"])
    elapsed = max(loop_beats, float(session.get("elapsed_beats", loop_beats)))
    configured = int(session.get("loop_passes", 0))
    count = max(1, math.ceil((elapsed - 1e-9) / loop_beats))
    if configured > 0:
        count = min(count, configured)

    row.clips.remove(clip)
    insert_at = app.project.rows.index(row) + 1
    lanes = []
    created_patterns = []
    for index in range(count):
        pass_start = index * loop_beats
        pass_end = min((index + 1) * loop_beats, elapsed)
        if pass_end - pass_start < 0.03125:
            break
        pattern = Pattern(
            name=f"{row.name} · Take {index + 1}",
            bars=max(1, math.ceil((pass_end - pass_start) / 4.0)),
        )
        pattern.notes = [
            trimmed
            for note in source_pattern.notes
            if (trimmed := _trim_note(note, pass_start, pass_end, pass_start)) is not None
        ]
        app.project.patterns.append(pattern)
        created_patterns.append(pattern)
        lane = _new_lane(row, index + 1)
        lane.clips.append(
            Clip(
                kind="pattern",
                ref=pattern.id,
                start_beat=float(session["loop_start"]),
                length_beats=pass_end - pass_start,
            )
        )
        app.project.rows.insert(insert_at + len(lanes), lane)
        lanes.append(lane)

    if source_pattern in app.project.patterns:
        app.project.patterns.remove(source_pattern)
    if created_patterns:
        app.project.current_pattern = created_patterns[-1].id
        app._sync_pattern_controls()
    if lanes:
        _append_take_group(
            app,
            row,
            lanes,
            float(session["loop_start"]),
            float(session["loop_end"]),
        )
    return lanes


def _apply_punch(capture, row: Row, clip: Clip, session: dict) -> None:
    app = capture.app
    punch_start = float(session["punch_start"])
    punch_end = float(session["punch_end"])
    transport_start = float(session["transport_start"])
    lead_beats = max(0.0, punch_start - transport_start)
    region_beats = punch_end - punch_start
    if clip.kind == "audio":
        source = app.library.clips.get(clip.ref)
        if source is None:
            return
        bpm = float(app.project.bpm)
        lead_seconds = lead_beats * 60.0 / bpm
        wanted = region_beats * 60.0 / bpm
        available = max(0.0, float(source.duration) - lead_seconds)
        take = min(wanted, available)
        latency = capture.settings.input_latency_ms * bpm / 60000.0
        clip.offset = float(clip.offset) + lead_seconds
        clip.source_length = take
        clip.length_beats = take * bpm / 60.0
        clip.start_beat = max(0.0, punch_start - latency)
        return

    pattern = next((pattern for pattern in app.project.patterns if pattern.id == clip.ref), None)
    if pattern is None:
        return
    kept = []
    for note in pattern.notes:
        shifted = replace(note, start=float(note.start) - lead_beats)
        trimmed = _trim_note(shifted, 0.0, region_beats, 0.0)
        if trimmed is not None:
            kept.append(trimmed)
    pattern.notes = kept
    pattern.bars = max(1, math.ceil(region_beats / 4.0))
    clip.start_beat = punch_start
    clip.length_beats = region_beats


def install_recording_capture_extensions() -> None:
    """Install opt-in loop/punch behavior without changing default capture."""
    global _CAPTURE_INSTALLED
    if _CAPTURE_INSTALLED:
        return

    from .ui.track_recording import TrackCapture

    original_prepare = TrackCapture.prepare
    original_start = TrackCapture.start
    original_tick = TrackCapture.tick
    original_note_on = TrackCapture.note_on
    original_note_off = TrackCapture.note_off
    original_finish = TrackCapture.finish
    original_save_take = TrackCapture.save_take
    original_discard = TrackCapture.discard_take

    def prepare(capture):
        if not original_prepare(capture):
            return False
        settings = recording_settings(capture.project)
        mode = None
        session = None
        if settings["loop_takes"]:
            loop_start = float(capture.project.loop_start)
            loop_end = float(capture.project.loop_end)
            if loop_end <= loop_start:
                capture.pending = False
                capture.target = None
                capture.message = "Loop takes need a valid Song loop range"
                capture.changed.emit()
                capture.app.status.showMessage(capture.message, 5000)
                return False
            capture.start_beat = loop_start
            mode = "loop"
            session = {
                "mode": mode,
                "loop_start": loop_start,
                "loop_end": loop_end,
                "loop_length": loop_end - loop_start,
                "loop_passes": int(settings["loop_passes"]),
                "auto_take_lanes": bool(settings["auto_take_lanes"]),
                "origin": loop_start,
                "cycles": 0,
                "last_transport_beat": loop_start,
                "source_row": capture.target.id,
            }
        elif settings["punch_enabled"]:
            punch_start = float(settings["punch_start"])
            punch_end = float(settings["punch_end"])
            pre_roll = int(settings["pre_roll_bars"]) * 4.0
            transport_start = max(0.0, punch_start - pre_roll)
            capture.start_beat = transport_start
            mode = "punch"
            session = {
                "mode": mode,
                "punch_start": punch_start,
                "punch_end": punch_end,
                "transport_start": transport_start,
                "origin": transport_start,
                "source_row": capture.target.id,
            }
        capture._advanced_recording = session
        return True

    def start(capture):
        original_start(capture)
        session = _session(capture)
        if not capture.active or not session:
            return
        if session["mode"] == "loop":
            capture.app.engine.loop_song = True
            session["last_transport_beat"] = float(capture.app.engine.beat)
            capture.message = (
                f"Loop recording · {capture.target.name} · Stop creates take lanes"
                if session.get("auto_take_lanes", True)
                else f"Loop recording · {capture.target.name}"
            )
        else:
            capture.message = (
                f"Punch recording · {session['punch_start']:g} → {session['punch_end']:g} beats"
            )
        capture.app.status.showMessage(capture.message)
        capture.changed.emit()

    def tick(capture):
        original_tick(capture)
        session = _session(capture)
        if not capture.active or not session:
            return
        if session["mode"] == "loop":
            absolute = _observe_beat(capture)
            session["elapsed_beats"] = max(0.0, absolute - float(session["origin"]))
            passes = int(session.get("loop_passes", 0))
            if passes > 0 and int(session.get("cycles", 0)) >= passes:
                capture.app.stop_all()
        elif float(capture.app.engine.beat) >= float(session["punch_end"]):
            capture.app.stop_all()

    def note_on(capture, pitch, velocity, pad=None):
        session = _session(capture)
        if not (
            session
            and session["mode"] == "loop"
            and capture.active
            and capture.target.record_source == "notes"
        ):
            return original_note_on(capture, pitch, velocity, pad)
        note_off(capture, pitch, pad)
        capture.held[(pitch, pad)] = (_observe_beat(capture), velocity)

    def note_off(capture, pitch, pad=None):
        session = _session(capture)
        if not (
            session
            and session["mode"] == "loop"
            and capture.active
            and capture.target.record_source == "notes"
        ):
            return original_note_off(capture, pitch, pad)
        held = capture.held.pop((pitch, pad), None)
        if held is None:
            return
        beat, velocity = held
        now = _observe_beat(capture)
        capture.notes.append(
            Note(
                pitch,
                max(0.0, beat - float(session["origin"])),
                max(0.03125, now - beat),
                velocity,
                pad,
            )
        )

    def finish(capture):
        session = _session(capture)
        if session and capture.active and session["mode"] == "loop":
            absolute = _observe_beat(capture)
            session["elapsed_beats"] = max(0.0, absolute - float(session["origin"]))
        original_finish(capture)
        if session and not capture.busy:
            capture._advanced_recording = None

    def save_take(capture):
        session = _session(capture)
        if session is None or capture.unsaved is None:
            return original_save_take(capture)
        target_id = str(session.get("source_row", ""))
        row = next((item for item in capture.app.project.rows if item.id == target_id), None)
        before = {clip.id for clip in row.clips} if row is not None else set()
        if (
            session["mode"] == "loop"
            and capture.target is not None
            and capture.target.record_source == "notes"
            and capture.project.arp.enabled
        ):
            audio, notes = capture.unsaved
            capture.unsaved = (audio, _unwrap_arp_notes(notes, float(session["loop_length"])))
        original_save_take(capture)
        if capture.unsaved is not None or row is None:
            return None
        clip = next((item for item in row.clips if item.id not in before), None)
        if clip is None:
            return None
        if session["mode"] == "punch":
            _apply_punch(capture, row, clip, session)
        elif session.get("auto_take_lanes", True):
            if clip.kind == "audio":
                lanes = _split_audio_loop(capture, row, clip, session)
            else:
                lanes = _split_note_loop(capture, row, clip, session)
            if lanes:
                capture.message = f"Saved · {len(lanes)} loop take lane(s)"
                capture.app.status.showMessage(capture.message, 5000)
        capture.app._refresh_place_box()
        capture.app.playlist.refresh()
        capture.app._set_dirty(True)
        capture.changed.emit()
        capture._advanced_recording = None
        return clip

    def discard_take(capture):
        result = original_discard(capture)
        if not capture.busy:
            capture._advanced_recording = None
        return result

    TrackCapture.prepare = prepare
    TrackCapture.start = start
    TrackCapture.tick = tick
    TrackCapture.note_on = note_on
    TrackCapture.note_off = note_off
    TrackCapture.finish = finish
    TrackCapture.save_take = save_take
    TrackCapture.discard_take = discard_take
    _CAPTURE_INSTALLED = True


def attach_recording_workflows(window, command_controller):
    existing = getattr(window, "recording_workflow_controller", None)
    if existing is not None:
        return existing
    controller = RecordingWorkflowController(window, command_controller)
    window.recording_workflow_controller = controller
    return controller


class RecordingWorkflowController:
    def __init__(self, window, command_controller):
        self.window = window
        self.command_controller = command_controller
        self.registry = command_controller.registry
        self._register_commands()
        command_controller._reindex_bindings()
        self._build_menu()

    def _register_commands(self):
        specs = (
            CommandSpec(
                "recording.settings",
                "Loop / punch recording settings",
                self.show_settings,
                category="Recording",
                keywords=("loop", "punch", "take", "pre-roll"),
            ),
            CommandSpec(
                "recording.loop_toggle",
                "Toggle loop take recording",
                self.toggle_loop,
                category="Recording",
                keywords=("takes", "cycle", "lanes"),
            ),
            CommandSpec(
                "recording.punch_toggle",
                "Toggle punch recording",
                self.toggle_punch,
                category="Recording",
                keywords=("in", "out", "pre-roll"),
            ),
            CommandSpec(
                "recording.take_groups",
                "Show recorded take groups",
                self.show_take_groups,
                category="Recording",
                keywords=("takes", "lanes", "comp"),
            ),
        )
        for spec in specs:
            try:
                self.registry.register(spec)
            except ValueError as exc:
                if "duplicate command id" not in str(exc):
                    raise

    def _build_menu(self):
        menu = self.window.menuBar().addMenu("Recording")
        self.menu = menu
        # Keep the QAction wrapper alive: temporary QAction.menu() wrappers can
        # transfer ownership under PySide and delete the attached QMenu.
        self.menu_action = menu.menuAction()
        for command_id in (
            "recording.settings",
            "recording.loop_toggle",
            "recording.punch_toggle",
            "track.new_take_lane",
            "recording.take_groups",
        ):
            action = menu.addAction(self.registry.get(command_id).title)
            action.triggered.connect(
                lambda _checked=False, command_id=command_id: self.command_controller._execute(
                    command_id
                )
            )

    def _commit(self, values):
        self.window.snapshot()
        try:
            result = _store_settings(self.window.project, values)
        except Exception:
            self.window.discard_snapshot()
            raise
        self.window._set_dirty(True)
        return result

    def toggle_loop(self):
        settings = recording_settings(self.window.project)
        enabled = not bool(settings["loop_takes"])
        result = self._commit(
            {
                "loop_takes": enabled,
                "punch_enabled": False if enabled else settings["punch_enabled"],
            }
        )
        self.window.status.showMessage(
            "Loop take recording enabled" if enabled else "Loop take recording disabled", 3500
        )
        return result

    def toggle_punch(self):
        settings = recording_settings(self.window.project)
        enabled = not bool(settings["punch_enabled"])
        result = self._commit(
            {"punch_enabled": enabled, "loop_takes": False if enabled else settings["loop_takes"]}
        )
        self.window.status.showMessage(
            "Punch recording enabled" if enabled else "Punch recording disabled", 3500
        )
        return result

    def show_settings(self):
        settings = recording_settings(self.window.project)
        dialog = QDialog(self.window)
        dialog.setWindowTitle("Loop / punch recording")
        layout = QVBoxLayout(dialog)
        note = QLabel(
            "Loop Takes records the current Song loop repeatedly and creates non-destructive take lanes. "
            "Punch records only the selected beat range; pre-roll is captured then trimmed from the saved take."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        loop = QCheckBox("Loop Takes")
        loop.setChecked(bool(settings["loop_takes"]))
        auto_lanes = QCheckBox("Create a lane for every pass")
        auto_lanes.setChecked(bool(settings["auto_take_lanes"]))
        passes = QSpinBox()
        passes.setRange(0, 64)
        passes.setSpecialValueText("Until Stop")
        passes.setValue(int(settings["loop_passes"]))
        punch = QCheckBox("Punch In / Out")
        punch.setChecked(bool(settings["punch_enabled"]))
        punch_start = QDoubleSpinBox()
        punch_start.setRange(0.0, 1_000_000.0)
        punch_start.setDecimals(3)
        punch_start.setSuffix(" beats")
        punch_start.setValue(float(settings["punch_start"]))
        punch_end = QDoubleSpinBox()
        punch_end.setRange(0.0, 1_000_000.0)
        punch_end.setDecimals(3)
        punch_end.setSuffix(" beats")
        punch_end.setValue(float(settings["punch_end"]))
        pre_roll = QSpinBox()
        pre_roll.setRange(0, 8)
        pre_roll.setSuffix(" bars")
        pre_roll.setValue(int(settings["pre_roll_bars"]))
        form.addRow(loop)
        form.addRow("Passes", passes)
        form.addRow(auto_lanes)
        form.addRow(punch)
        form.addRow("Punch start", punch_start)
        form.addRow("Punch end", punch_end)
        form.addRow("Pre-roll", pre_roll)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(520, 360)
        if not dialog.exec():
            return None
        if loop.isChecked() and punch.isChecked():
            QMessageBox.warning(
                self.window,
                "Recording modes",
                "Loop Takes and Punch are separate capture modes. Enable one at a time.",
            )
            return None
        if punch.isChecked() and punch_end.value() <= punch_start.value():
            QMessageBox.warning(self.window, "Punch range", "Punch end must be after punch start.")
            return None
        return self._commit(
            {
                "loop_takes": loop.isChecked(),
                "auto_take_lanes": auto_lanes.isChecked(),
                "loop_passes": passes.value(),
                "punch_enabled": punch.isChecked(),
                "punch_start": punch_start.value(),
                "punch_end": punch_end.value(),
                "pre_roll_bars": pre_roll.value(),
            }
        )

    def show_take_groups(self):
        groups = ensure_workflow(self.window.project).get("recording", {}).get("take_groups", [])
        if not groups:
            QMessageBox.information(
                self.window,
                "Take groups",
                "No automatic loop-take groups have been recorded in this project yet.",
            )
            return []
        dialog = QDialog(self.window)
        dialog.setWindowTitle("Take groups")
        layout = QVBoxLayout(dialog)
        items = QListWidget()
        rows = {row.id: row for row in self.window.project.rows}
        for group in groups:
            lane_names = [rows[lane].name for lane in group["lanes"] if lane in rows]
            items.addItem(
                f"{group['name']} · {group['start']:g} → {group['end']:g} beats · "
                f"{len(lane_names)} lane(s)\n" + "  |  ".join(lane_names)
            )
        layout.addWidget(items)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)
        dialog.resize(700, 420)
        dialog.exec()
        return groups

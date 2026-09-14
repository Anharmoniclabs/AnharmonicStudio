"""Universal non-destructive comping for automatic take groups.

A comp is a normal Song row, not a second hidden project format. Audio regions
reference the existing take sources with adjusted offsets; note regions create
small derived patterns containing only the selected phrase. Re-swiping a range
splits/removes overlapping comp material while the source take material remains
intact. Playback ownership moves to the comp only while it is active.
"""

from __future__ import annotations

from dataclasses import replace
import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from .model import Clip, Pattern, Row
from .workflow_commands import CommandSpec
from .workflow_state import ensure_workflow, validate_workflow

_EPSILON = 1e-7


def take_groups(project) -> list[dict]:
    recording = ensure_workflow(project).get("recording", {})
    groups = recording.get("take_groups", []) if isinstance(recording, dict) else []
    return groups if isinstance(groups, list) else []


def take_group(project, group_id: str) -> dict:
    for group in take_groups(project):
        if group.get("id") == group_id:
            return group
    raise KeyError(group_id)


def row_by_id(project, row_id: str) -> Row | None:
    return next((row for row in project.rows if row.id == row_id), None)


def take_lane_rows(project, group: dict) -> list[Row]:
    return [
        lane
        for lane_id in group.get("lanes", [])
        if (lane := row_by_id(project, lane_id)) is not None
    ]


def comp_row_id(group: dict) -> str:
    return f"{group['id']}:comp"


def comp_row(project, group: dict) -> Row | None:
    return row_by_id(project, comp_row_id(group))


def ensure_comp_row(project, group: dict) -> Row:
    existing = comp_row(project, group)
    if existing is not None:
        return existing
    source = row_by_id(project, group.get("source_row", ""))
    row = Row(
        id=comp_row_id(group),
        name=f"{group.get('name', 'Takes')} · COMP",
        color=source.color if source is not None else "",
        record_source=source.record_source if source is not None else "audio",
        record_track=source.record_track if source is not None else 3,
        mute=True,
    )
    lane_indices = [project.rows.index(lane) for lane in take_lane_rows(project, group)]
    if lane_indices:
        insert_at = max(lane_indices) + 1
    elif source is not None:
        insert_at = project.rows.index(source) + 1
    else:
        insert_at = len(project.rows)
    project.rows.insert(insert_at, row)
    return row


def _range(group: dict, start: float, end: float) -> tuple[float, float]:
    left = max(float(group.get("start", 0.0)), float(start))
    right = min(float(group.get("end", left)), float(end))
    if right <= left + _EPSILON:
        raise ValueError("comp range must have positive length inside the take group")
    return left, right


def _source_selection(row: Row, start: float, end: float) -> tuple[Clip, float, float]:
    candidates = []
    for clip in row.clips:
        clip_start = float(clip.start_beat)
        clip_end = clip_start + float(clip.length_beats)
        left = max(start, clip_start)
        right = min(end, clip_end)
        overlap = right - left
        if overlap > _EPSILON:
            candidates.append((overlap, clip, left, right))
    if not candidates:
        raise ValueError("selected take lane has no material in that comp range")
    _overlap, clip, left, right = max(
        candidates,
        key=lambda item: (item[0], -float(item[1].length_beats)),
    )
    return clip, left, right


def _audio_segment(clip: Clip, start: float, end: float, bpm: float) -> Clip:
    seconds_per_beat = 60.0 / max(1e-6, float(bpm))
    delta = max(0.0, start - clip.start_beat)
    source_length = (end - start) * seconds_per_beat
    if clip.source_length > 0.0:
        available = max(0.0, float(clip.source_length) - delta * seconds_per_beat)
        source_length = min(source_length, available)
    if source_length <= _EPSILON:
        raise ValueError("audio comp segment has no source material")
    return Clip(
        kind="audio",
        ref=clip.ref,
        start_beat=start,
        length_beats=end - start,
        offset=float(clip.offset) + delta * seconds_per_beat,
        source_length=source_length,
        gain=clip.gain,
        track=clip.track,
        loop=False,
        loop_crossfade=clip.loop_crossfade,
        reverse=clip.reverse,
        mute=clip.mute,
    )


def _pattern_for_clip(project, clip: Clip) -> Pattern:
    pattern = next((item for item in project.patterns if item.id == clip.ref), None)
    if pattern is None:
        raise ValueError("take lane pattern is unavailable")
    return pattern


def _pattern_segment(project, clip: Clip, start: float, end: float, name: str) -> Clip:
    pattern = _pattern_for_clip(project, clip)
    local_start = max(0.0, start - clip.start_beat)
    local_end = min(clip.length_beats, end - clip.start_beat)
    segment = Pattern(name=name, bars=max(1, math.ceil((end - start) / 4.0)))
    notes = []
    for note in pattern.notes:
        note_start = float(note.start)
        note_end = note_start + float(note.duration)
        left = max(local_start, note_start)
        right = min(local_end, note_end)
        if right <= left + _EPSILON:
            continue
        notes.append(
            replace(
                note,
                start=left - local_start,
                duration=max(0.03125, right - left),
            )
        )
    segment.notes = notes
    project.patterns.append(segment)
    return Clip(
        kind="pattern",
        ref=segment.id,
        start_beat=start,
        length_beats=end - start,
        mute=clip.mute,
    )


def _segment(project, clip: Clip, start: float, end: float, bpm: float, name: str) -> Clip:
    if clip.kind == "audio":
        return _audio_segment(clip, start, end, bpm)
    if clip.kind == "pattern":
        return _pattern_segment(project, clip, start, end, name)
    raise ValueError("take comp supports audio and note-pattern clips")


def _preflight_source(project, clip: Clip, start: float, end: float, bpm: float) -> None:
    """Reject unusable source material before comp rows or mute state are changed."""
    if clip.kind == "audio":
        _audio_segment(clip, start, end, bpm)
        return
    if clip.kind == "pattern":
        _pattern_for_clip(project, clip)
        return
    raise ValueError("take comp supports audio and note-pattern clips")


def _discard_derived_pattern(project, clip: Clip) -> None:
    if clip.kind != "pattern":
        return
    pattern = next((item for item in project.patterns if item.id == clip.ref), None)
    if pattern is not None and pattern.name.startswith("COMP · "):
        project.patterns.remove(pattern)


def _replace_range(
    project,
    row: Row,
    start: float,
    end: float,
    replacement: Clip | None,
    bpm: float,
) -> None:
    rebuilt = []
    for clip in row.clips:
        clip_start = float(clip.start_beat)
        clip_end = clip_start + float(clip.length_beats)
        if clip_end <= start + _EPSILON or clip_start >= end - _EPSILON:
            rebuilt.append(clip)
            continue
        if clip_start < start - _EPSILON:
            rebuilt.append(
                _segment(project, clip, clip_start, start, bpm, f"COMP · left · {clip.ref}")
            )
        if clip_end > end + _EPSILON:
            rebuilt.append(
                _segment(project, clip, end, clip_end, bpm, f"COMP · right · {clip.ref}")
            )
        _discard_derived_pattern(project, clip)
    if replacement is not None:
        rebuilt.append(replacement)
    rebuilt.sort(key=lambda clip: (clip.start_beat, clip.id))
    row.clips = rebuilt


def _active_lane(project, group: dict) -> Row:
    lane_id = str(group.get("active_lane", ""))
    lane = row_by_id(project, lane_id)
    if lane is None or lane_id not in group.get("lanes", []):
        lanes = take_lane_rows(project, group)
        if not lanes:
            raise ValueError("take group has no available source lanes")
        lane = lanes[-1]
        group["active_lane"] = lane.id
    return lane


def _seed_comp_from_active(project, group: dict, row: Row) -> None:
    """Clone the audible take so activating a comp never creates holes."""
    if row.clips:
        return
    lane = _active_lane(project, group)
    seeded = []
    for clip in sorted(lane.clips, key=lambda item: item.start_beat):
        left = float(clip.start_beat)
        right = left + float(clip.length_beats)
        if right <= left + _EPSILON:
            continue
        seeded.append(
            _segment(
                project,
                clip,
                left,
                right,
                project.bpm,
                f"COMP · baseline · {lane.name}",
            )
        )
    if not seeded:
        raise ValueError("active take lane has no material to comp")
    row.clips = seeded


def _activate_comp_playback(project, group: dict, row: Row) -> None:
    _seed_comp_from_active(project, group, row)
    for lane in take_lane_rows(project, group):
        lane.mute = True
    row.mute = False


def _restore_take_playback(project, group: dict, row: Row | None = None) -> None:
    active = _active_lane(project, group)
    for lane in take_lane_rows(project, group):
        lane.mute = lane.id != active.id
    if row is not None:
        row.mute = True


def _validate_project_workflow(project) -> None:
    project.workflow = validate_workflow(ensure_workflow(project))


def swipe_comp_range(project, group_id: str, lane_id: str, start: float, end: float) -> Row:
    """Paint one lane into the comp over ``start:end`` beats."""
    group = take_group(project, group_id)
    if lane_id not in group.get("lanes", []):
        raise ValueError("selected lane is not part of this take group")
    start, end = _range(group, start, end)
    lane = row_by_id(project, lane_id)
    if lane is None:
        raise ValueError("selected take lane no longer exists")
    source, source_start, source_end = _source_selection(lane, start, end)
    _preflight_source(project, source, source_start, source_end, project.bpm)
    row = ensure_comp_row(project, group)
    _activate_comp_playback(project, group, row)
    replacement = _segment(
        project,
        source,
        source_start,
        source_end,
        project.bpm,
        f"COMP · {lane.name} · {source_start:g}-{source_end:g}",
    )
    _replace_range(project, row, source_start, source_end, replacement, project.bpm)
    group["active_lane"] = lane_id
    _validate_project_workflow(project)
    return row


def erase_comp_range(project, group_id: str, start: float, end: float) -> Row:
    group = take_group(project, group_id)
    start, end = _range(group, start, end)
    row = ensure_comp_row(project, group)
    _activate_comp_playback(project, group, row)
    _replace_range(project, row, start, end, None, project.bpm)
    _validate_project_workflow(project)
    return row


def clear_comp(project, group_id: str) -> Row:
    group = take_group(project, group_id)
    row = ensure_comp_row(project, group)
    for clip in row.clips:
        _discard_derived_pattern(project, clip)
    row.clips.clear()
    _restore_take_playback(project, group, row)
    _validate_project_workflow(project)
    return row


def comp_coverage(row: Row) -> list[tuple[float, float, str]]:
    return [
        (float(clip.start_beat), float(clip.start_beat + clip.length_beats), clip.kind)
        for clip in sorted(row.clips, key=lambda item: item.start_beat)
    ]


def attach_take_comping(window, command_controller):
    existing = getattr(window, "take_comp_controller", None)
    if existing is not None:
        return existing
    controller = TakeCompController(window, command_controller)
    window.take_comp_controller = controller
    return controller


class TakeCompController:
    def __init__(self, window, command_controller):
        self.window = window
        self.command_controller = command_controller
        self.registry = command_controller.registry
        try:
            self.registry.register(
                CommandSpec(
                    "recording.take_comp",
                    "Take comp editor",
                    self.show_editor,
                    category="Recording",
                    keywords=("comp", "swipe", "takes", "lanes"),
                )
            )
        except ValueError as exc:
            if "duplicate command id" not in str(exc):
                raise
        command_controller._reindex_bindings()
        menu = None
        for menu_action in window.menuBar().actions():
            if menu_action.text() == "Recording":
                menu = menu_action.menu()
                if menu is not None:
                    break
        if menu is None:
            menu = window.menuBar().addMenu("Recording")
        self.menu = menu
        self.menu_action = menu.menuAction()
        action = menu.addAction(self.registry.get("recording.take_comp").title)
        action.triggered.connect(
            lambda _checked=False: self.command_controller._execute("recording.take_comp")
        )

    def _refresh_project_ui(self, row: Row | None = None):
        self.window._refresh_place_box()
        self.window.playlist.refresh()
        if row is not None:
            self.window.status.showMessage(
                f"Updated {row.name} · source takes preserved · Undo restores the comp edit",
                5000,
            )
        self.window._set_dirty(True)

    def _edit_error(self, title: str, exc: Exception) -> None:
        QMessageBox.warning(self.window, title, str(exc))

    def show_editor(self):
        groups = take_groups(self.window.project)
        if not groups:
            QMessageBox.information(
                self.window,
                "Take comp",
                "Record loop takes first. Automatic take groups will appear here for swipe comping.",
            )
            return None

        dialog = QDialog(self.window)
        dialog.setWindowTitle("Take comp editor")
        layout = QVBoxLayout(dialog)
        intro = QLabel(
            "Choose a take lane and paint it into the comp over a beat range. The first edit starts "
            "from the active take, then replaces only the selected section. Source material is never "
            "cut or overwritten; source-lane playback mutes are managed automatically."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        grid = QGridLayout()
        group_box = QComboBox()
        lane_box = QComboBox()
        start_box = QDoubleSpinBox()
        end_box = QDoubleSpinBox()
        for spin in (start_box, end_box):
            spin.setRange(0.0, 1_000_000.0)
            spin.setDecimals(3)
            spin.setSuffix(" beats")
        grid.addWidget(QLabel("Take group"), 0, 0)
        grid.addWidget(group_box, 0, 1, 1, 3)
        grid.addWidget(QLabel("Source lane"), 1, 0)
        grid.addWidget(lane_box, 1, 1, 1, 3)
        grid.addWidget(QLabel("Range"), 2, 0)
        grid.addWidget(start_box, 2, 1)
        grid.addWidget(end_box, 2, 2)
        layout.addLayout(grid)

        coverage = QListWidget()
        layout.addWidget(QLabel("Current comp regions"))
        layout.addWidget(coverage, 1)

        swipe = QPushButton("SWIPE RANGE FROM SELECTED TAKE")
        erase = QPushButton("ERASE RANGE")
        clear = QPushButton("CLEAR COMP")
        layout.addWidget(swipe)
        layout.addWidget(erase)
        layout.addWidget(clear)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)

        def current_group() -> dict:
            return take_group(self.window.project, str(group_box.currentData()))

        def refresh_coverage():
            coverage.clear()
            group = current_group()
            row = comp_row(self.window.project, group)
            if row is None or not row.clips:
                coverage.addItem("No active comp material")
                return
            for left, right, kind in comp_coverage(row):
                item = QListWidgetItem(f"{left:g} → {right:g} beats · {kind}")
                item.setData(Qt.UserRole, (left, right))
                coverage.addItem(item)
            coverage.setToolTip(
                "The comp row owns playback while active; recorded source material remains intact."
            )

        def group_changed():
            lane_box.clear()
            group = current_group()
            for lane in take_lane_rows(self.window.project, group):
                lane_box.addItem(lane.name, lane.id)
            start_box.setValue(float(group.get("start", 0.0)))
            end_box.setValue(float(group.get("end", 0.0)))
            active = str(group.get("active_lane", ""))
            index = lane_box.findData(active)
            if index >= 0:
                lane_box.setCurrentIndex(index)
            refresh_coverage()

        def do_swipe():
            lane_id = str(lane_box.currentData() or "")
            if not lane_id:
                return
            self.window.snapshot()
            try:
                row = swipe_comp_range(
                    self.window.project,
                    str(group_box.currentData()),
                    lane_id,
                    start_box.value(),
                    end_box.value(),
                )
            except (KeyError, ValueError) as exc:
                self.window.discard_snapshot()
                self._edit_error("Take comp", exc)
                return
            except Exception:
                self.window.discard_snapshot()
                raise
            self._refresh_project_ui(row)
            refresh_coverage()

        def do_erase():
            self.window.snapshot()
            try:
                row = erase_comp_range(
                    self.window.project,
                    str(group_box.currentData()),
                    start_box.value(),
                    end_box.value(),
                )
            except (KeyError, ValueError) as exc:
                self.window.discard_snapshot()
                self._edit_error("Take comp", exc)
                return
            except Exception:
                self.window.discard_snapshot()
                raise
            self._refresh_project_ui(row)
            refresh_coverage()

        def do_clear():
            self.window.snapshot()
            try:
                row = clear_comp(self.window.project, str(group_box.currentData()))
            except (KeyError, ValueError) as exc:
                self.window.discard_snapshot()
                self._edit_error("Take comp", exc)
                return
            except Exception:
                self.window.discard_snapshot()
                raise
            self._refresh_project_ui(row)
            refresh_coverage()

        for group in groups:
            group_box.addItem(group.get("name", "Takes"), group.get("id"))
        group_box.currentIndexChanged.connect(lambda *_: group_changed())
        swipe.clicked.connect(do_swipe)
        erase.clicked.connect(do_erase)
        clear.clicked.connect(do_clear)
        group_changed()
        dialog.resize(720, 600)
        dialog.exec()
        return comp_row(self.window.project, current_group())

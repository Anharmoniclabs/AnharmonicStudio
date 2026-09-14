"""Native marker/region tracks, editing, navigation and interchange exports."""

from __future__ import annotations

from dataclasses import replace
from html import escape
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..timeline_markers import (
    COLORS,
    KINDS,
    MAX_BEAT,
    MAX_MARKERS,
    TimelineMarker,
    adjacent_marker,
    create_marker,
    delete_marker,
    export_markers,
    marker_items,
    update_marker,
)
from ..workflow_commands import CommandSpec
from .playlist import HEAD_W
from .theme import q
from .window_client import WindowClient

LANE_H = 25


class MarkerEditDialog(QDialog):
    def __init__(self, marker: TimelineMarker, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Timeline entry")
        self.setMinimumWidth(350)
        self.marker = marker
        self.color = marker.color
        layout = QFormLayout(self)
        self.kind = QComboBox()
        for kind in KINDS:
            self.kind.addItem(kind.title(), kind)
        self.kind.setCurrentIndex(KINDS.index(marker.kind))
        self.name = QLineEdit(marker.name)
        self.name.setMaxLength(256)
        self.start = QDoubleSpinBox()
        self.end = QDoubleSpinBox()
        for box in (self.start, self.end):
            box.setRange(0, MAX_BEAT)
            box.setDecimals(4)
            box.setSingleStep(0.25)
            box.setSuffix(" beats")
        self.start.setValue(marker.start_beat)
        self.end.setValue(marker.end_beat or min(MAX_BEAT, marker.start_beat + 4))
        self.color_button = QPushButton(self.color)
        self.color_button.clicked.connect(self.choose_color)
        layout.addRow("Type", self.kind)
        layout.addRow("Name", self.name)
        layout.addRow("Start (zero-based)", self.start)
        layout.addRow("End (exclusive)", self.end)
        layout.addRow("Color", self.color_button)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addRow(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self._kind_changed()

    def _kind_changed(self):
        self.end.setEnabled(self.kind.currentData() == "region")

    def choose_color(self):
        chosen = QColorDialog.getColor(QColor(self.color), self, "Timeline color")
        if chosen.isValid():
            self.color = chosen.name()
            self.color_button.setText(self.color)

    def value(self) -> TimelineMarker:
        return TimelineMarker.from_dict(
            {
                "id": self.marker.id,
                "kind": self.kind.currentData(),
                "name": self.name.text(),
                "start_beat": self.start.value(),
                "end_beat": self.end.value() if self.end.isEnabled() else None,
                "color": self.color,
            }
        )

    def accept(self):
        try:
            self.value()
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        super().accept()


class TimelineMarkerStrip(QWidget):
    """Two pinned-height lanes aligned with the scrollable arrangement grid."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setObjectName("timelineMarkerStrip")
        self.setFixedHeight(LANE_H * 2)
        self.setMouseTracking(True)
        self.setToolTip(
            "Markers / cues above; regions below. Double-click to create or edit. "
            "Drag to move; drag a region's right edge to resize. Uses Song snap."
        )
        self.drag = None
        self.preview = None

    def beat_x(self, beat):
        app = self.controller.app
        return app.playlist.beat_to_x(beat) - app.song_scroll.horizontalScrollBar().value()

    def x_beat(self, x):
        app = self.controller.app
        return app.playlist.x_to_beat(x + app.song_scroll.horizontalScrollBar().value())

    def entry_rect(self, marker):
        x = self.beat_x(marker.start_beat)
        if marker.kind == "region":
            width = max(6.0, self.beat_x(marker.end_beat) - x)
            return QRectF(x, LANE_H + 2, width, LANE_H - 4)
        return QRectF(
            x,
            2,
            min(160, max(28, self.fontMetrics().horizontalAdvance(marker.name) + 18)),
            LANE_H - 4,
        )

    def hit(self, position):
        for marker in reversed(self.controller.items):
            if self.entry_rect(marker).contains(position):
                return marker
        return None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), q("bg2"))
        painter.setPen(q("line"))
        painter.drawLine(0, LANE_H, self.width(), LANE_H)
        painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        for saved in self.controller.items:
            marker = self.preview if self.preview and saved.id == self.preview.id else saved
            rect = self.entry_rect(marker)
            if not rect.intersects(QRectF(self.rect())):
                continue
            color = QColor(marker.color)
            color.setAlpha(90 if marker.id == self.controller.selected_id else 45)
            painter.fillRect(rect, color)
            painter.setPen(
                QPen(QColor(marker.color), 2 if marker.id == self.controller.selected_id else 1)
            )
            if marker.kind == "region":
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(rect)
                painter.drawLine(
                    QPointF(rect.right() - 3, rect.top() + 3),
                    QPointF(rect.right() - 3, rect.bottom() - 3),
                )
            else:
                x = rect.left()
                painter.drawLine(QPointF(x, 2), QPointF(x, LANE_H - 2))
                painter.setBrush(QColor(marker.color))
                points = (
                    [QPointF(x, 2), QPointF(x + 8, 6), QPointF(x, 10)]
                    if marker.kind == "marker"
                    else [QPointF(x, 2), QPointF(x + 5, 6), QPointF(x, 10), QPointF(x - 5, 6)]
                )
                painter.drawPolygon(QPolygonF(points))
            painter.setPen(q("fg"))
            text_rect = rect.adjusted(10, 0, -5, 0)
            text_rect.setLeft(max(2, text_rect.left()))
            painter.drawText(
                text_rect,
                Qt.AlignVCenter | Qt.AlignLeft,
                self.fontMetrics().elidedText(
                    marker.name, Qt.ElideRight, max(0, int(text_rect.width()))
                ),
            )
        header = max(0, HEAD_W - self.controller.app.song_scroll.horizontalScrollBar().value())
        if header:
            painter.fillRect(QRectF(0, 0, header, self.height()), q("bg2"))
            painter.setPen(q("dim"))
            painter.drawText(QRectF(8, 0, header - 12, LANE_H), Qt.AlignVCenter, "MARKERS / CUES")
            painter.drawText(QRectF(8, LANE_H, header - 12, LANE_H), Qt.AlignVCenter, "REGIONS")

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        marker = self.hit(event.position())
        if marker:
            self.controller.select(marker.id)
            resizing = (
                marker.kind == "region"
                and abs(event.position().x() - self.entry_rect(marker).right()) <= 7
            )
            self.drag = (
                marker,
                self.x_beat(event.position().x()),
                resizing,
                self.controller.app.project,
            )
            self.preview = None
        event.accept()

    def mouseMoveEvent(self, event):
        if self.drag is None:
            marker = self.hit(event.position())
            self.setCursor(Qt.SizeHorCursor if marker else Qt.ArrowCursor)
            if marker:
                bounds = f"{marker.start_beat:g}" + (
                    f"–{marker.end_beat:g}" if marker.end_beat is not None else ""
                )
                self.setToolTip(
                    f"{marker.kind.title()}: {escape(marker.name)} · {bounds} beats · double-click to edit"
                )
            return
        marker, origin, resizing, project = self.drag
        if project is not self.controller.app.project:
            self.drag = self.preview = None
            self.update()
            return
        beat = self.x_beat(event.position().x())
        snap = self.controller.app.playlist._snap
        if resizing:
            end = min(MAX_BEAT, max(marker.start_beat + 0.0001, snap(beat)))
            self.preview = replace(marker, end_beat=end)
        else:
            length = (marker.end_beat - marker.start_beat) if marker.end_beat is not None else 0
            start = min(MAX_BEAT - length, max(0, snap(marker.start_beat + beat - origin)))
            self.preview = replace(
                marker,
                start_beat=start,
                end_beat=start + length if marker.end_beat is not None else None,
            )
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or self.drag is None:
            return
        original, _, _, project = self.drag
        preview = self.preview
        self.drag = self.preview = None
        if project is not self.controller.app.project:
            self.update()
            return
        if preview is not None and preview != original:
            self.controller.run(lambda: self.controller.replace_entry(preview))
        else:
            self.controller.navigate(original.id)
        self.update()

    def mouseDoubleClickEvent(self, event):
        self.drag = self.preview = None
        marker = self.hit(event.position())
        if marker:
            self.controller.run(lambda: self.controller.edit(marker.id))
        else:
            kind = "region" if event.position().y() >= LANE_H else "marker"
            self.controller.run(
                lambda: self.controller.add(
                    kind, self.controller.app.playlist._snap(self.x_beat(event.position().x()))
                )
            )

    def contextMenuEvent(self, event):
        marker = self.hit(QPointF(event.pos()))
        menu = QMenu(self)
        if marker:
            self.controller.select(marker.id)
            menu.addAction(
                "Edit name, color and position…",
                lambda: self.controller.run(lambda: self.controller.edit(marker.id)),
            )
            menu.addAction("Go to entry", lambda: self.controller.navigate(marker.id))
            if marker.kind == "region":
                menu.addAction(
                    "Set Song loop to region",
                    lambda: self.controller.run(lambda: self.controller.use_region(marker.id)),
                )
            menu.addAction(
                "Delete entry",
                lambda: self.controller.run(lambda: self.controller.remove(marker.id)),
            )
        else:
            beat = self.controller.app.playlist._snap(self.x_beat(event.pos().x()))
            for kind in KINDS:
                menu.addAction(
                    f"Add {kind} here…",
                    lambda checked=False, kind=kind: self.controller.run(
                        lambda: self.controller.add(kind, beat)
                    ),
                )
        menu.exec(event.globalPos())


class MarkerManager(QDialog):
    def __init__(self, controller):
        super().__init__(controller.app)
        self.controller = controller
        self.setWindowTitle("Timeline markers, cues and regions")
        self.resize(700, 390)
        layout = QVBoxLayout(self)
        hint = QLabel(
            "Positions are zero-based quarter-note beats. Entries are saved with the project.\nExports contain beat and second positions; they do not render audio."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Type", "Name", "Start beat", "End beat", "Color"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._select)
        self.table.cellDoubleClicked.connect(lambda *_: controller.edit())
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        for text, callback in (
            ("+ Marker", lambda: controller.add("marker")),
            ("+ Cue", lambda: controller.add("cue")),
            ("+ Region", lambda: controller.add("region")),
            ("Edit…", controller.edit),
            ("Delete", controller.remove),
            ("Go to", controller.navigate),
            ("Set loop", controller.use_region),
            ("Export…", controller.export),
        ):
            button = QPushButton(text)
            button.clicked.connect(
                lambda checked=False, callback=callback: controller.run(callback)
            )
            row.addWidget(button)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self.refresh()

    def _select(self):
        row = self.table.currentRow()
        if row >= 0:
            self.controller.selected_id = self.table.item(row, 0).data(Qt.UserRole)
            self.controller.strip.update()

    def refresh(self):
        selected = self.controller.selected_id
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.controller.items))
        for row, marker in enumerate(self.controller.items):
            values = (
                marker.kind.title(),
                marker.name,
                f"{marker.start_beat:g}",
                f"{marker.end_beat:g}" if marker.end_beat is not None else "—",
                marker.color,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, marker.id)
                if column == 4:
                    item.setForeground(QColor(marker.color))
                self.table.setItem(row, column, item)
            if selected == marker.id:
                self.table.selectRow(row)
        self.table.resizeColumnToContents(0)
        self.table.setColumnWidth(1, 200)
        self.table.blockSignals(False)


class TimelineMarkerController(WindowClient, QObject):
    def __init__(self, window, command_controller):
        super().__init__(window)
        self.app = window
        self.commands = command_controller
        self.selected_id = None
        self.items = []
        self.manager = None
        self._state_key = None
        specs = (
            ("markers.add", "Add marker at playhead…", lambda: self.add("marker"), "Ctrl+Alt+M"),
            ("markers.cue", "Add cue point at playhead…", lambda: self.add("cue"), ""),
            (
                "markers.region",
                "Create region from Song loop…",
                lambda: self.add("region"),
                "Ctrl+Alt+R",
            ),
            (
                "markers.previous",
                "Previous timeline entry",
                lambda: self.previous_next(-1),
                "Alt+Left",
            ),
            ("markers.next", "Next timeline entry", lambda: self.previous_next(1), "Alt+Right"),
            ("markers.manage", "Manage timeline entries…", self.show_manager, "Ctrl+Shift+M"),
            ("markers.export", "Export markers and regions…", self.export, ""),
        )
        self.menu = window.menuBar().addMenu("Markers")
        # Keep both wrappers alive: some PySide versions give a temporary
        # menuAction wrapper ownership when other controllers inspect menus.
        self.menu_action = self.menu.menuAction()
        for command_id, title, callback, shortcut in specs:
            command_controller.registry.register(
                CommandSpec(
                    command_id,
                    title,
                    callback,
                    category="Markers",
                    keywords=("timeline", "cue", "region", "range", "color"),
                    default_shortcut=shortcut,
                )
            )
            action = self.menu.addAction(title)
            action.triggered.connect(
                lambda checked=False, command_id=command_id: command_controller._execute(command_id)
            )
            # The shared registry owns dispatch; do not add a duplicate QAction shortcut.
            action.setToolTip(shortcut)
        command_controller._reindex_bindings()
        self.panel = QWidget()
        self.panel.setObjectName("timelineMarkerTracks")
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        toolbar = QHBoxLayout()
        self.buttons = {}
        for command_id, label in (
            ("markers.add", "+ Marker"),
            ("markers.cue", "+ Cue"),
            ("markers.region", "+ Region"),
            ("markers.previous", "‹"),
            ("markers.next", "›"),
            ("markers.manage", "Manage…"),
            ("markers.export", "Export…"),
        ):
            button = QPushButton(label)
            button.setObjectName(command_id.replace(".", "_"))
            button.setToolTip(command_controller.registry.get(command_id).title)
            button.clicked.connect(
                lambda checked=False, command_id=command_id: command_controller._execute(command_id)
            )
            toolbar.addWidget(button)
            self.buttons[command_id] = button
        toolbar.addStretch(1)
        layout.addLayout(toolbar)
        self.strip = TimelineMarkerStrip(self, self.panel)
        layout.addWidget(self.strip)
        parent_layout = window.song_scroll.parentWidget().layout()
        parent_layout.insertWidget(parent_layout.indexOf(window.song_scroll), self.panel)
        window.song_scroll.horizontalScrollBar().valueChanged.connect(self.strip.update)
        window.zoom.valueChanged.connect(self.strip.update)
        window.playlist.changed.connect(self.refresh)
        self.timer = QTimer(self)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh(force=True)

    def run(self, callback):
        try:
            return callback()
        except (ValueError, OSError) as exc:
            self.app.status.showMessage(f"Timeline entries · {exc}", 6000)
            return None

    def refresh(self, force=False):
        project = self.app.project
        key = (id(project), id(getattr(project, "timeline_markers", None)))
        if force or key != self._state_key:
            self._state_key = key
            self.items = marker_items(project)
            if not any(item.id == self.selected_id for item in self.items):
                self.selected_id = None
            self.strip.drag = self.strip.preview = None
            self.app.playlist.refresh()
            self.strip.update()
            if self.manager is not None:
                self.manager.refresh()

    def select(self, marker_id):
        self.selected_id = marker_id
        self.strip.update()
        if self.manager is not None:
            self.manager.refresh()

    def selected(self, marker_id=None):
        marker_id = marker_id or self.selected_id
        return next(
            (marker for marker in marker_items(self.app.project) if marker.id == marker_id), None
        )

    def _change(self, callback):
        self.app.snapshot()
        try:
            result = callback()
        except Exception:
            self.app.discard_snapshot()
            raise
        self.app._set_dirty(True)
        self.refresh(force=True)
        return result

    def add(self, kind="marker", beat=None):
        project = self.app.project
        if len(marker_items(project)) >= MAX_MARKERS:
            raise ValueError(f"A project supports at most {MAX_MARKERS} timeline entries")
        start = float(self.app.engine.beat if beat is None else beat)
        end = None
        if kind == "region":
            start = project.loop_start if beat is None else beat
            end = project.loop_end if beat is None else min(MAX_BEAT, start + 4)
        marker = TimelineMarker.from_dict(
            {
                "id": "new",
                "kind": kind,
                "name": f"{kind.title()} {len(self.items) + 1}",
                "start_beat": start,
                "end_beat": end,
                "color": COLORS[kind],
            }
        )
        dialog = MarkerEditDialog(marker, self.app)
        try:
            if dialog.exec() != QDialog.Accepted or self.app.project is not project:
                return None
            value = dialog.value()
        finally:
            dialog.deleteLater()
        result = self._change(
            lambda: create_marker(
                project, value.kind, value.name, value.start_beat, value.end_beat, value.color
            )
        )
        self.select(result.id)
        return result

    def edit(self, marker_id=None):
        marker = self.selected(marker_id)
        if marker is None:
            return
        project = self.app.project
        dialog = MarkerEditDialog(marker, self.app)
        try:
            if dialog.exec() == QDialog.Accepted and self.app.project is project:
                if self.selected(marker.id) != marker:
                    self.app.status.showMessage(
                        "Timeline entry changed while its editor was open; reopen it to edit", 5000
                    )
                    return
                self.replace_entry(dialog.value())
        finally:
            dialog.deleteLater()

    def replace_entry(self, marker):
        marker = TimelineMarker.from_dict(marker.to_dict())
        current = self.selected(marker.id)
        if current is None or current == marker:
            return
        changes = marker.to_dict()
        changes.pop("id")
        result = self._change(lambda: update_marker(self.app.project, marker.id, **changes))
        self.select(result.id)
        return result

    def remove(self, marker_id=None):
        marker = self.selected(marker_id)
        if marker is not None:
            self._change(lambda: delete_marker(self.app.project, marker.id))

    def navigate(self, marker_id=None):
        marker = self.selected(marker_id)
        if marker is None:
            return
        capture = self.app.track_capture
        if capture.active or capture.pending:
            self.app.status.showMessage("Stop the take before moving the playhead", 3000)
            return
        self.select(marker.id)
        self.app.show_tab(self.app.TAB_PLAYLIST)
        self.app._seek_song(marker.start_beat)
        self.app.song_scroll.ensureVisible(
            int(self.app.playlist.beat_to_x(marker.start_beat)), 0, 80, 0
        )
        self.app.playlist.update()

    def previous_next(self, direction):
        marker = adjacent_marker(self.app.project, self.app.engine.beat, direction)
        if marker:
            self.navigate(marker.id)
        else:
            self.app.status.showMessage("No further timeline entries in that direction", 2500)

    def use_region(self, marker_id=None):
        marker = self.selected(marker_id)
        if marker is None or marker.kind != "region":
            self.app.status.showMessage("Select a region to set the Song loop", 3000)
            return
        capture = self.app.track_capture
        if capture.active or capture.pending:
            self.app.status.showMessage("Stop the take before changing its loop range", 3000)
            return
        # The native loop requires at least a quarter beat; never silently enlarge.
        if marker.end_beat - marker.start_beat < 0.25:
            raise ValueError("Song loops require a region of at least 0.25 beats")
        self._change(lambda: self.app.set_song_loop_range(marker.start_beat, marker.end_beat))
        self.app.status.showMessage(
            f"Song loop range set to {marker.name}; use LOOP to enable playback", 4000
        )

    def show_manager(self):
        self.refresh()
        if self.manager is None:
            self.manager = MarkerManager(self)
        self.manager.show()
        self.manager.raise_()
        self.manager.activateWindow()

    def export(self):
        path, selected_filter = QFileDialog.getSaveFileName(
            self.app,
            "Export timeline entries (no audio)",
            "timeline-markers.json",
            "JSON (*.json);;CSV (*.csv)",
        )
        if not path:
            return None
        destination = Path(path)
        format = "csv" if selected_filter.startswith("CSV") else "json"
        if destination.suffix:
            format = destination.suffix.lstrip(".").lower()
            if format not in ("json", "csv"):
                raise ValueError("Choose a .json or .csv file for timeline metadata")
        if not destination.suffix:
            destination = destination.with_suffix("." + format)
            if (
                destination.exists()
                and QMessageBox.question(
                    self.app,
                    "Replace export?",
                    f"Replace {destination.name}?",
                    QMessageBox.Yes | QMessageBox.No,
                )
                != QMessageBox.Yes
            ):
                return None
        export_markers(self.app.project, destination, format)
        self.app.status.showMessage(
            f"Exported {len(marker_items(self.app.project))} timeline entries to {destination.name}",
            4500,
        )
        return destination


def attach_timeline_markers(window, command_controller):
    existing = getattr(window, "timeline_marker_controller", None)
    if existing is None:
        existing = TimelineMarkerController(window, command_controller)
        window.timeline_marker_controller = existing
    return existing

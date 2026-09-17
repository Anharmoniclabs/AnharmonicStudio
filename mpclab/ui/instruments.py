"""Undoable instrument lifecycle and explicit input/output assignments."""

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..model import Project
from ..workflow_commands import CommandSpec
from .track_management import require_idle_capture


def edit_instrument(window, operation, *, name=None, midi_channel=None, track=None):
    require_idle_capture(window)
    if window.engine.playing or window.engine.recording:
        raise RuntimeError("Stop playback before changing instruments")
    project = Project.from_dict(window.project.to_dict())
    selected = next((i for i in project.instruments if i.id == project.selected_instrument), None)
    if operation in ("add", "duplicate"):
        source = project.selected_patch if operation == "duplicate" else type(project.synth)()
        instance = project.add_instrument(name or "New instrument", source, midi_channel)
        project.selected_instrument = instance.id
        if track is not None:
            instance.patch.track = track
    elif operation == "update":
        if selected is None:
            raise ValueError("Choose an independent instrument to edit its name or MIDI channel")
        selected.name = name if name is not None else selected.name
        selected.midi_channel = midi_channel
        if track is not None:
            selected.patch.track = track
    elif operation == "remove":
        if selected is None:
            raise ValueError("The original instrument stays in every project")
        if any(n.instrument == selected.id for p in project.patterns for n in p.notes):
            raise ValueError(
                "This instrument has notes. Move or delete those notes before removing it"
            )
        project.instruments.remove(selected)
        project.selected_instrument = None
    else:
        raise ValueError("Unknown instrument operation")
    project.to_dict()  # Validate before touching history or live state.
    undo, redo, dirty = list(window._undo), list(window._redo), window._dirty
    window.snapshot()
    try:
        window._apply_project(project)
    except Exception:
        window._undo, window._redo = undo, redo
        window._try_save_history()
        window._set_dirty(dirty)
        raise
    window.piano_roll.select_channel(project.selected_instrument)
    window._set_dirty(True)
    return project.selected_instrument


class InstrumentDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Project instruments")
        self.resize(500, 260)
        layout = QVBoxLayout(self)
        hint = QLabel(
            "Select an instrument here, then choose its sound in Instruments. "
            "Notes keep their own instrument when you switch sounds."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        layout.addLayout(form)
        self.selector = QComboBox()
        self.name = QLineEdit()
        self.name.setMaxLength(128)
        self.channel = QComboBox()
        self.channel.addItem("Selected / typing input", None)
        for channel in range(16):
            self.channel.addItem(str(channel + 1), channel)
        self.output = QComboBox()
        for title, widget in (
            ("Instrument", self.selector),
            ("Name", self.name),
            ("MIDI input channel", self.channel),
            ("Mixer output", self.output),
        ):
            form.addRow(title, widget)
        row = QHBoxLayout()
        layout.addLayout(row)
        for title, operation in (
            ("Add", "add"),
            ("Duplicate", "duplicate"),
            ("Apply", "update"),
            ("Remove unused", "remove"),
        ):
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, op=operation: self.change(op))
            row.addWidget(button)
        self.selector.currentIndexChanged.connect(self.select)
        self.refresh()

    def refresh(self):
        project = self.window.project
        self.selector.blockSignals(True)
        self.selector.clear()
        self.selector.addItem("Original instrument", None)
        for instance in project.instruments:
            self.selector.addItem(instance.name, instance.id)
        self.selector.setCurrentIndex(self.selector.findData(project.selected_instrument))
        self.selector.blockSignals(False)
        self.output.clear()
        for index, track in enumerate(project.tracks):
            self.output.addItem(f"{index + 1} · {track.name}", index)
        instance = next(
            (i for i in project.instruments if i.id == project.selected_instrument), None
        )
        self.name.setText(instance.name if instance else "New instrument")
        self.channel.setCurrentIndex(
            self.channel.findData(instance.midi_channel if instance else None)
        )
        self.output.setCurrentIndex(project.selected_patch.track)

    def select(self, index):
        self.window.piano_roll.select_channel(self.selector.itemData(index))
        self.refresh()

    def change(self, operation):
        try:
            edit_instrument(
                self.window,
                operation,
                name=self.name.text().strip(),
                midi_channel=self.channel.currentData(),
                track=self.output.currentData(),
            )
        except (ValueError, RuntimeError, OSError) as exc:
            QMessageBox.warning(self, "Instrument change", str(exc))
        self.refresh()


def attach_instruments(window, controller):
    if hasattr(window, "instrument_dialog"):
        return
    window.instrument_dialog = InstrumentDialog(window)

    def show():
        window.instrument_dialog.refresh()
        window.instrument_dialog.show()
        window.instrument_dialog.raise_()

    controller.registry.register(
        CommandSpec(
            "instruments.manage",
            "Manage project instruments…",
            show,
            category="Instruments",
            keywords=("add", "duplicate", "MIDI", "channel"),
        )
    )
    action = window.menuBar().addMenu("Instruments").addAction("Manage project instruments…")
    action.triggered.connect(show)
    button = QPushButton("Project instruments…")
    button.clicked.connect(show)
    window.synth_panel.layout().insertWidget(0, button)
    controller._reindex_bindings()

"""Undoable instrument lifecycle and explicit input/output assignments."""

from copy import deepcopy
from dataclasses import replace

from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..model import Project
from ..workflow_commands import CommandSpec
from .track_management import require_idle_capture


def insert_pattern_instrument(window, kind, *, layer_notes=False):
    """Append an independent sound without rebuilding the live project or hosts."""
    from ..model import SynthPatch
    from ..prism import bundled_plugin
    from .prism_controls import patch_parameters

    if kind not in ("native", "prism"):
        raise ValueError("Choose Native or Prism")
    path = bundled_plugin() if kind == "prism" else None
    if kind == "prism" and path is None:
        raise ValueError("Prism plugin pack is not installed")
    project, pattern = window.project, window.project.pattern()
    source = project.selected_instrument
    patch = SynthPatch()
    window.snapshot()
    instance = project.add_instrument(
        f"{pattern.name[:140]} · {'Prism' if kind == 'prism' else 'Native'} {len(pattern.instrument_ids) + 1}",
        patch,
    )
    if (
        source is not None
        and pattern.selected_instrument == source
        and source not in pattern.instrument_ids
    ):
        pattern.instrument_ids.append(source)
    pattern.instrument_ids.append(instance.id)
    pattern.selected_instrument = instance.id
    if layer_notes:
        pattern.notes.extend(
            [
                replace(note, instrument=instance.id)
                for note in pattern.notes
                if note.pad is None and note.instrument == source
            ]
        )
    window.piano_roll.select_channel(instance.id)
    window._set_dirty(True)
    if kind == "prism":
        specification = {"path": str(path), "parameters": patch_parameters(patch)}
        # Persist the intent immediately and keep a failed host visibly unavailable,
        # instead of silently playing a native fallback for a Prism slot.
        project.instrument_plugins[instance.id] = specification
        from ..plugin_host import UnavailablePlugin

        window.engine.external.set_instrument(instance.id, UnavailablePlugin("Loading Prism…"))
        window.devices.load_plugin(
            "instrument", specification, instrument_id=instance.id, save=False
        )
    window.synth_panel.sync_plugin_mode()
    return instance.id


class PatternInstrumentRack(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.pattern_id = None
        layout = QHBoxLayout(self)
        self.label = QLabel("Pattern instruments")
        self.selector = QComboBox()
        self.selector.setMinimumWidth(220)
        self.selector.setAccessibleName("Current pattern instruments")
        self.output = QComboBox()
        self.output.setAccessibleName("Instrument mixer destination")
        self.output.setToolTip("This instrument uses the same mixer track in every pattern")
        self.output.activated.connect(self.route_output)
        self.kind = QComboBox()
        self.kind.addItem("Native instrument", "native")
        self.kind.addItem("Prism", "prism")
        self.layer = QCheckBox("Layer current notes")
        self.layer.setToolTip("Copy the selected instrument's notes onto the new sound")
        self.insert = QPushButton("Insert instrument")
        for widget in (self.label, self.selector, self.output, self.kind, self.layer, self.insert):
            layout.addWidget(widget)
        self.selector.activated.connect(self.select)
        self.insert.clicked.connect(self.add)

    def refresh(self):
        project, pattern = self.window.project, self.window.project.pattern()
        ids = list(pattern.instrument_ids)
        if pattern.selected_instrument is not None and pattern.selected_instrument not in ids:
            ids.append(pattern.selected_instrument)
        for note in pattern.notes:
            if note.pad is None and note.instrument not in ids:
                ids.append(note.instrument)
        if project.selected_instrument is None and None not in ids:
            ids.insert(0, None)
        self.label.setText(pattern.name)
        self.selector.blockSignals(True)
        self.selector.clear()
        for key in ids:
            instance = project.instrument(key) if key is not None else None
            spec = (
                project.instrument_plugins.get(key)
                if key is not None
                else project.plugins.get("instrument")
            )
            name = instance.name if instance else "Original instrument"
            self.selector.addItem(f"{name} · {'Plugin' if spec else 'Native'}", key)
        if not ids:
            self.selector.addItem("Insert a sound for this pattern", "")
        else:
            self.selector.setCurrentIndex(self.selector.findData(project.selected_instrument))
        self.selector.blockSignals(False)
        self.output.blockSignals(True)
        self.output.clear()
        for index, track in enumerate(project.tracks):
            self.output.addItem(f"Mixer {index + 1} · {track.name}", index)
        self.output.setCurrentIndex(project.selected_patch.track)
        self.output.blockSignals(False)

    def route_output(self, index):
        project = self.window.project
        track = self.output.itemData(index)
        if not isinstance(track, int) or not 0 <= track < len(project.tracks):
            return
        if project.selected_patch.track == track:
            return
        self.window.snapshot()
        project.selected_patch.track = track
        self.window._set_dirty(True)
        self.window.synth_panel.sync()
        self.window.status.showMessage(
            f"Instrument → Mixer {track + 1} · {project.tracks[track].name} · all patterns", 4000
        )

    def select_pattern(self):
        pattern = self.window.project.pattern()
        if self.pattern_id != pattern.id:
            self.pattern_id = pattern.id
            # None is the original instrument, not an absent selection.
            self.window.piano_roll.select_channel(pattern.selected_instrument)
        self.refresh()

    def select(self, index):
        target = self.selector.itemData(index)
        if target != "":
            self.window.piano_roll.select_channel(target)
            self.refresh()

    def add(self):
        try:
            insert_pattern_instrument(
                self.window, self.kind.currentData(), layer_notes=self.layer.isChecked()
            )
        except (ValueError, RuntimeError, OSError) as exc:
            self.window.status.showMessage(str(exc), 6000)


def edit_instrument(window, operation, *, name=None, midi_channel=None, track=None):
    require_idle_capture(window)
    if window.engine.playing or window.engine.recording:
        raise RuntimeError("Stop playback before changing instruments")
    project = Project.from_dict(window.project.to_dict())
    selected = next((i for i in project.instruments if i.id == project.selected_instrument), None)
    if operation in ("add", "duplicate"):
        source = project.selected_patch if operation == "duplicate" else type(project.synth)()
        plugin = None
        if operation == "duplicate":
            plugin = (
                project.plugins.get("instrument")
                if project.selected_instrument is None
                else project.instrument_plugins.get(project.selected_instrument)
            )
        instance = project.add_instrument(name or "New instrument", source, midi_channel)
        if plugin is not None:
            project.instrument_plugins[instance.id] = deepcopy(plugin)
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
        for pattern in project.patterns:
            pattern.instrument_ids = [key for key in pattern.instrument_ids if key != selected.id]
            if pattern.selected_instrument == selected.id:
                pattern.selected_instrument = None
        project.instruments.remove(selected)
        project.instrument_plugins.pop(selected.id, None)
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

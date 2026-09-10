"""Workflow command ui.

The caller retains Qt/project ownership; these operations receive it explicitly.
"""

from __future__ import annotations
import re
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)
from .workflow_commands import PRESET_BINDINGS


def _build_menu(owner) -> None:
    menu = owner.window.menuBar().addMenu("Workflow")
    groups = (
        (
            "Audio",
            (
                "clip.stretch",
                "clip.tempo_conform",
                "clip.fade",
                "clip.gain",
                "clip.consolidate",
                "clip.bounce_in_place",
                "clip.loop_toggle",
                "clip.reverse_toggle",
            ),
        ),
        (
            "Piano Roll",
            (
                "notes.quantize",
                "notes.strum",
                "notes.chop",
                "notes.legato",
                "notes.random_velocity",
                "notes.scale_lock",
            ),
        ),
        (
            "Recording & Mixer",
            (
                "track.new_take_lane",
                "track.freeze",
                "track.unfreeze",
                "track.bounce",
                "mixer.create_group",
                "mixer.edit_group",
                "mixer.sidechain",
                "track.preset_save",
                "track.preset_recall",
                "master.preset",
            ),
        ),
        ("Automation", ("automation.smooth",)),
        ("Scenes", ("scene.capture", "scene.launch")),
        (
            "Commands & Keymaps",
            (
                "workflow.command_palette",
                "workflow.keymap",
                "workflow.macro_create",
                "workflow.macro_run",
            ),
        ),
    )
    for title, command_ids in groups:
        sub = menu.addMenu(title)
        for command_id in command_ids:
            action = sub.addAction(owner.registry.get(command_id).title)
            action.triggered.connect(lambda _=False, cid=command_id: owner._execute(cid))


def show_command_palette(owner):
    dialog = QDialog(owner.window)
    dialog.setWindowTitle("Command palette")
    layout = QVBoxLayout(dialog)
    search = QLineEdit()
    search.setPlaceholderText("Search commands, freeze, quantize, route, stretch…")
    results = QListWidget()
    layout.addWidget(search)
    layout.addWidget(results, 1)

    def refresh(text=""):
        results.clear()
        for command in owner.registry.search(text, 80):
            shortcut = owner.registry.bindings.get(command.id, "")
            label = f"{command.title}    {shortcut}" if shortcut else command.title
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, ("command", command.id))
            results.addItem(item)
        query = text.casefold().strip()
        for name in sorted(owner.registry.macros):
            if query and query not in name.casefold() and "macro" not in query:
                continue
            item = QListWidgetItem(f"Macro: {name}")
            item.setData(Qt.UserRole, ("macro", name))
            results.addItem(item)
        if results.count():
            results.setCurrentRow(0)

    def run_item(item):
        if item is None:
            return
        kind, value = item.data(Qt.UserRole)
        dialog.accept()
        if kind == "macro":
            owner.registry.run_macro(value)
        else:
            owner._execute(value)

    search.textChanged.connect(refresh)
    search.returnPressed.connect(lambda: run_item(results.currentItem()))
    results.itemActivated.connect(run_item)
    refresh()
    dialog.resize(620, 520)
    search.setFocus()
    dialog.exec()


def show_keymap_editor(owner):
    dialog = QDialog(owner.window)
    dialog.setWindowTitle("Keyboard shortcuts")
    layout = QVBoxLayout(dialog)
    preset = QComboBox()
    preset.addItems([*PRESET_BINDINGS, "Custom"])
    preset.setCurrentText(owner.registry.preset)
    commands = QListWidget()
    editor = QKeySequenceEdit()
    buttons = QHBoxLayout()
    assign = QPushButton("Assign")
    clear = QPushButton("Clear")
    buttons.addWidget(assign)
    buttons.addWidget(clear)
    layout.addWidget(QLabel("Preset"))
    layout.addWidget(preset)
    layout.addWidget(commands, 1)
    layout.addWidget(QLabel("Shortcut for selected command"))
    layout.addWidget(editor)
    layout.addLayout(buttons)
    close = QDialogButtonBox(QDialogButtonBox.Close)
    close.rejected.connect(dialog.reject)
    layout.addWidget(close)

    def refill():
        commands.clear()
        for command in owner.registry.commands:
            shortcut = owner.registry.bindings.get(command.id, "")
            text = f"{command.category}  •  {command.title}    {shortcut}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, command.id)
            commands.addItem(item)
        if commands.count():
            commands.setCurrentRow(0)

    def selected_changed():
        item = commands.currentItem()
        if item:
            command_id = item.data(Qt.UserRole)
            editor.setKeySequence(QKeySequence(owner.registry.bindings.get(command_id, "")))

    def do_assign(clear_value=False):
        item = commands.currentItem()
        if not item:
            return
        sequence = ""
        if not clear_value:
            sequence = editor.keySequence().toString(QKeySequence.PortableText)
        owner.registry.bind(item.data(Qt.UserRole), sequence)
        preset.blockSignals(True)
        preset.setCurrentText("Custom")
        preset.blockSignals(False)
        owner._reindex_bindings()
        refill()

    def apply_preset(name):
        if name == "Custom":
            return
        owner.registry.apply_preset(name)
        owner._reindex_bindings()
        refill()

    commands.currentItemChanged.connect(lambda *_: selected_changed())
    assign.clicked.connect(lambda: do_assign(False))
    clear.clicked.connect(lambda: do_assign(True))
    preset.currentTextChanged.connect(apply_preset)
    refill()
    selected_changed()
    dialog.resize(720, 620)
    dialog.exec()


def create_macro_dialog(owner):
    name, ok = QInputDialog.getText(owner.window, "Create macro", "Macro name")
    if not ok:
        return None
    text, ok = QInputDialog.getMultiLineText(
        owner.window,
        "Create macro",
        "Command ids, one per line\n(use the command palette/keymap editor to inspect ids)",
        "playlist.duplicate\nclip.bounce_in_place",
    )
    if not ok:
        return None
    command_ids = [part.strip() for part in re.split(r"[\n,;]+", text) if part.strip()]
    result = owner.registry.define_macro(name, command_ids)
    owner.window.status.showMessage(f"saved macro {result.name}", 3500)
    return result


def run_macro_dialog(owner):
    if not owner.registry.macros:
        raise ValueError("create a macro first")
    names = sorted(owner.registry.macros)
    name, ok = QInputDialog.getItem(
        owner.window,
        "Run macro",
        "Macro",
        names,
        0,
        False,
    )
    return owner.registry.run_macro(name) if ok else None

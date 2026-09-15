"""Attach the premium workflow layer to the existing desktop workstation.

The core MainWindow remains the stable editor. This controller owns user
commands, remappable keymaps, macros and advanced non-destructive workflow
menus so feature growth does not turn the main window into another monolith.
"""

from __future__ import annotations

from pathlib import Path
import re

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QInputDialog,
    QLineEdit,
    QSpinBox,
)

from .workflow_commands import CommandRegistry
from .workflow_mixing import install_advanced_track_controls
from .workflow_state import ensure_workflow, install_project_workflow_state
from .workflow_tools import (
    bounce_mixer_track_to_library,
    create_scene,
    freeze_mixer_track,
    launch_scene,
    unfreeze_mixer_track,
)

from . import command_catalog, workflow_edit_dialogs, workflow_mixer_dialogs, workflow_command_ui

_RUNTIME_INSTALLED = False


def install_premium_runtime() -> None:
    """Install persistence and callback-safe mixer extensions once."""
    global _RUNTIME_INSTALLED
    if _RUNTIME_INSTALLED:
        return
    install_project_workflow_state()
    install_advanced_track_controls()
    _RUNTIME_INSTALLED = True


def attach_premium_workflows(window):
    install_premium_runtime()
    existing = getattr(window, "workflow_controller", None)
    if existing is not None:
        return existing
    controller = PremiumWorkflowController(window)
    window.workflow_controller = controller
    return controller


class PremiumWorkflowController(QObject):
    """Application-level commands, keymaps and advanced workflow menus."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.binding_path = Path(window.root) / "workflow-bindings.json"
        fresh = not self.binding_path.exists()
        self.registry = CommandRegistry(self.binding_path)
        self._binding_index: dict[str, str] = {}
        self._legacy_sequences: set[str] = set()
        self._register_commands()
        if fresh:
            self.registry.apply_preset("Anharmonic / FL-style")
        self._disable_legacy_qshortcuts()
        self._reindex_bindings()
        self._build_menu()
        QApplication.instance().installEventFilter(self)

    def _register_commands(self) -> None:
        return command_catalog._register_commands(self)

    def _disable_legacy_qshortcuts(self) -> None:
        for shortcut in getattr(self.window, "_shortcuts", []):
            shortcut.setEnabled(False)

    @staticmethod
    def _normal(sequence: str) -> str:
        return QKeySequence(sequence).toString(QKeySequence.PortableText).casefold()

    def _reindex_bindings(self) -> None:
        self._binding_index.clear()
        known = {spec.id for spec in self.registry.commands}
        for command_id, sequence in self.registry.bindings.items():
            if command_id not in known:
                continue
            normalized = self._normal(sequence)
            if normalized:
                self._binding_index[normalized] = command_id

    def eventFilter(self, watched, event):
        if event.type() != QEvent.KeyPress or event.isAutoRepeat():
            return False
        try:
            sequence = QKeySequence(event.keyCombination()).toString(QKeySequence.PortableText)
        except AttributeError:
            return False
        normalized = sequence.casefold()
        if not normalized:
            return False

        focus = QApplication.focusWidget()
        text_widget = isinstance(focus, (QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox))
        modifiers = event.modifiers()
        textual_plain_key = bool(event.text()) and not (
            modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)
        )
        if text_widget and textual_plain_key:
            return False

        command_id = self._binding_index.get(normalized)
        if command_id:
            self._execute(command_id)
            event.accept()
            return True
        if normalized in self._legacy_sequences:
            event.accept()
            return True
        return False

    def _execute(self, command_id: str):
        try:
            return self.registry.execute(command_id)
        except Exception as exc:
            title = self.registry.get(command_id).title
            self.window.status.showMessage(f"{title} · {exc}", 6000)
            return None

    def _run(self, function, *args, **kwargs):
        return function(*args, **kwargs)

    def _build_menu(self) -> None:
        return workflow_command_ui._build_menu(self)

    def _playlist_split(self):
        if self.window.studio.selected != self.window.TAB_PLAYLIST:
            self.window.show_tab(self.window.TAB_PLAYLIST)
        return self.window.playlist.split_clip()

    def _playlist_duplicate(self):
        if self.window.studio.selected != self.window.TAB_PLAYLIST:
            self.window.show_tab(self.window.TAB_PLAYLIST)
        return self.window.playlist.duplicate_clip()

    def _selected_audio_clip(self):
        clip = self.window.playlist.selected_clip
        if clip is None or clip.kind != "audio":
            raise ValueError("select an audio clip in the Playlist")
        return clip

    def _toggle_clip_loop(self):
        clip = self._selected_audio_clip()
        return self.window.set_selected_clip_loop(not clip.loop)

    def _toggle_clip_reverse(self):
        clip = self._selected_audio_clip()
        return self.window.set_selected_clip_reverse(not clip.reverse)

    def set_clip_gain(self):
        return workflow_edit_dialogs.set_clip_gain(self)

    def stretch_dialog(self):
        return workflow_edit_dialogs.stretch_dialog(self)

    def tempo_conform_dialog(self):
        return workflow_edit_dialogs.tempo_conform_dialog(self)

    def fade_dialog(self):
        return workflow_edit_dialogs.fade_dialog(self)

    def strum_dialog(self):
        return workflow_edit_dialogs.strum_dialog(self)

    def chop_dialog(self):
        return workflow_edit_dialogs.chop_dialog(self)

    def velocity_dialog(self):
        return workflow_edit_dialogs.velocity_dialog(self)

    def scale_dialog(self):
        return workflow_edit_dialogs.scale_dialog(self)

    def _selected_track_index(self) -> int:
        last = len(self.window.project.tracks) - 1
        return max(0, min(last, int(self.window.mixer.selected)))

    def freeze_selected_track(self):
        index = self._selected_track_index()
        external = self.window.engine.external.instrument
        if external is not None and self.window.project.synth.track == index:
            raise ValueError(
                "freeze the external instrument after bouncing it; live host state cannot be muted safely"
            )
        result = freeze_mixer_track(self.window, index)
        self.window.status.showMessage(f"froze {self.window.project.tracks[index].name}", 4000)
        return result

    def unfreeze_selected_track(self):
        index = self._selected_track_index()
        result = unfreeze_mixer_track(self.window, index)
        self.window.status.showMessage(
            f"restored {self.window.project.tracks[index].name}",
            4000,
        )
        return result

    def bounce_selected_track(self):
        index = self._selected_track_index()
        rendered = bounce_mixer_track_to_library(self.window, index)
        self.window.browser.refresh(select=rendered.id)
        self.window.status.showMessage(f"{rendered.name} added to the library", 4500)
        return rendered

    def _parse_track_numbers(self, text: str) -> list[int]:
        numbers = []
        for token in re.split(r"[,\s]+", text.strip()):
            if not token:
                continue
            number = int(token) - 1
            if not 0 <= number < len(self.window.project.tracks):
                raise ValueError(f"track {number + 1} is outside the mixer")
            if number not in numbers:
                numbers.append(number)
        if not numbers:
            raise ValueError("choose at least one mixer track")
        return numbers

    def create_group_dialog(self):
        return workflow_mixer_dialogs.create_group_dialog(self)

    def edit_group_dialog(self):
        return workflow_mixer_dialogs.edit_group_dialog(self)

    def sidechain_dialog(self):
        return workflow_mixer_dialogs.sidechain_dialog(self)

    def save_track_preset(self):
        return workflow_mixer_dialogs.save_track_preset(self)

    def recall_track_preset(self):
        return workflow_mixer_dialogs.recall_track_preset(self)

    def mastering_dialog(self):
        return workflow_mixer_dialogs.mastering_dialog(self)

    def capture_scene(self):
        name, ok = QInputDialog.getText(self.window, "Capture scene", "Scene name")
        if not ok:
            return None
        self.window.snapshot()
        return create_scene(self.window, name)

    def launch_scene_dialog(self):
        scenes = ensure_workflow(self.window.project).get("scenes", [])
        if not scenes:
            raise ValueError("capture a scene first")
        labels = [scene["name"] for scene in scenes]
        name, ok = QInputDialog.getItem(
            self.window,
            "Launch scene",
            "Scene",
            labels,
            0,
            False,
        )
        if not ok:
            return None
        return launch_scene(self.window, scenes[labels.index(name)]["id"])

    def show_command_palette(self):
        return workflow_command_ui.show_command_palette(self)

    def show_keymap_editor(self):
        return workflow_command_ui.show_keymap_editor(self)

    def create_macro_dialog(self):
        return workflow_command_ui.create_macro_dialog(self)

    def run_macro_dialog(self):
        return workflow_command_ui.run_macro_dialog(self)

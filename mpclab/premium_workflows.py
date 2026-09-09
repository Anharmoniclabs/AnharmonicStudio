"""Attach the premium workflow layer to the existing desktop workstation.

The core MainWindow remains the stable editor. This controller owns user
commands, remappable keymaps, macros and advanced non-destructive workflow
menus so feature growth does not turn the main window into another monolith.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import re

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QInputDialog,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from .model import MasterFX, TrackFX, uid
from .time_stretch import MODES
from .workflow_commands import PRESET_BINDINGS, CommandRegistry, CommandSpec
from .workflow_mixing import install_advanced_track_controls
from .workflow_notes import (
    SCALES,
    chop_notes,
    legato_notes,
    quantize_notes,
    randomize_note_velocity,
    scale_lock_notes,
    strum_notes,
)
from .workflow_state import ensure_workflow, install_project_workflow_state
from .workflow_tools import (
    bounce_mixer_track_to_library,
    bounce_selected_in_place,
    consolidate_selected_audio,
    create_scene,
    fade_selected_clip,
    freeze_mixer_track,
    launch_scene,
    new_take_lane,
    smooth_current_automation,
    stretch_selected_clip,
    tempo_conform_selected_clip,
    unfreeze_mixer_track,
)

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
        w = self.window

        def spec(command_id, title, callback, category, shortcut="", keywords=()):
            self.registry.register(
                CommandSpec(
                    command_id,
                    title,
                    callback,
                    category=category,
                    default_shortcut=shortcut,
                    keywords=tuple(keywords),
                )
            )
            if shortcut:
                self._legacy_sequences.add(self._normal(shortcut))

        spec("project.new", "New project", w.new_project, "Project", "Ctrl+N")
        spec("project.save", "Save project", w.save_project, "Project", "Ctrl+S")
        spec(
            "project.save_as",
            "Save project as",
            w.save_project_as,
            "Project",
            "Ctrl+Shift+S",
        )
        spec("project.open", "Open project", w.load_project, "Project", "Ctrl+O")
        spec(
            "project.export",
            "Export mix",
            w.export_dialog,
            "Project",
            "Ctrl+E",
            ("render", "bounce"),
        )
        spec("project.export_fl", "Export mix (FL key)", w.export_dialog, "Project", "Ctrl+R")
        spec("history.undo", "Undo", w.undo, "Project", "Ctrl+Z")
        spec("history.redo", "Redo", w.redo, "Project", "Ctrl+Shift+Z")
        spec("ui.typing", "Musical typing", w.toggle_typing_keyboard, "Windows", "Ctrl+T")
        spec("ui.theme", "Toggle light / dark theme", w.toggle_theme, "Windows", "Ctrl+Shift+T")
        spec("ui.search_samples", "Search samples", w.search_samples, "Windows", "Ctrl+F")
        spec("ui.shortcuts", "Keyboard reference", w.show_shortcuts, "Windows", "F1")
        spec("sample.cut_source", "Toggle cut source", w.btn_cut_self.toggle, "Sampler", "Ctrl+K")

        spec("workspace.chop", "Open Chop / Edit", lambda: w.show_tab(w.TAB_CHOP), "Windows", "F2")
        spec("pattern.new", "New pattern", w.new_pattern, "Pattern", "F4")
        spec(
            "workspace.playlist",
            "Open Playlist",
            lambda: w.show_tab(w.TAB_PLAYLIST),
            "Windows",
            "F5",
        )
        spec(
            "workspace.sequencer",
            "Open Sequencer",
            lambda: w.show_tab(w.TAB_SEQ),
            "Windows",
            "F6",
        )
        spec(
            "workspace.instrument",
            "Open Analog / Arp",
            lambda: w.show_tab(w.TAB_SYNTH),
            "Windows",
            "F7",
        )
        spec("workspace.browser", "Show / hide Browser", w.toggle_browser, "Windows", "F8")
        spec("workspace.pads", "Show / hide Pads", w.toggle_pads, "Windows", "Shift+F8")
        spec(
            "workspace.mixer",
            "Open Mixer",
            lambda: w.show_tab(w.TAB_MIXER),
            "Windows",
            "F9",
        )
        spec(
            "workspace.vocals",
            "Open Autotune / Vocals",
            lambda: w.show_tab(w.TAB_VOCALS),
            "Windows",
            "F10",
        )
        spec(
            "workspace.focus",
            "Playlist focus mode",
            lambda: w.btn_playlist_focus.toggle(),
            "Windows",
            "F11",
        )
        spec(
            "workspace.piano",
            "Open Piano Roll",
            lambda: w.show_tab(w.TAB_PIANO),
            "Windows",
            "F12",
        )
        spec(
            "workspace.automation",
            "Open Automation",
            lambda: w.show_tab(w.TAB_AUTO),
            "Windows",
        )

        spec("transport.play_toggle", "Play / pause", w.toggle_play, "Transport", "Space")
        spec("transport.stop", "Stop and rewind", w.stop_all, "Transport", "Esc")
        spec(
            "transport.rewind",
            "Jump to start",
            lambda: w.engine.set_position(0.0),
            "Transport",
            "Home",
        )
        spec("transport.record", "Record arm", lambda: w.btn_rec.toggle(), "Transport", "R")
        spec(
            "transport.record_alt",
            "Record arm (legacy K)",
            lambda: w.btn_rec.toggle(),
            "Transport",
            "K",
        )
        spec(
            "transport.pattern_song",
            "Pattern / Song mode",
            lambda: w.set_mode("pattern" if w.engine.mode == "song" else "song"),
            "Transport",
            "L",
        )
        spec(
            "transport.metronome",
            "Toggle metronome",
            lambda: w.btn_metro.toggle(),
            "Transport",
            "M",
        )
        spec("transport.tap_tempo", "Tap tempo", w.tap_tempo, "Transport", "T")
        spec(
            "pads.previous_bank",
            "Previous pad bank",
            lambda: w.set_bank((w.pads.bank - 1) % 4),
            "Pads",
            ",",
        )
        spec(
            "pads.next_bank",
            "Next pad bank",
            lambda: w.set_bank((w.pads.bank + 1) % 4),
            "Pads",
            ".",
        )

        spec(
            "playlist.split",
            "Split selected clip at playhead",
            self._playlist_split,
            "Playlist",
            "Shift+S",
        )
        spec(
            "playlist.duplicate",
            "Duplicate selected clip(s)",
            self._playlist_duplicate,
            "Playlist",
            "Ctrl+D",
        )
        spec(
            "clip.consolidate",
            "Consolidate selected audio",
            lambda: self._run(consolidate_selected_audio, w),
            "Audio",
            "Ctrl+J",
        )
        spec(
            "clip.bounce_in_place",
            "Bounce selected audio in place",
            lambda: self._run(bounce_selected_in_place, w),
            "Audio",
            "Ctrl+Alt+B",
        )
        spec("clip.loop_toggle", "Toggle selected clip loop", self._toggle_clip_loop, "Audio")
        spec(
            "clip.reverse_toggle",
            "Toggle selected clip reverse",
            self._toggle_clip_reverse,
            "Audio",
        )
        spec("clip.gain", "Set selected clip gain", self.set_clip_gain, "Audio")
        spec("clip.fade", "Render clip fades", self.fade_dialog, "Audio")
        spec("clip.stretch", "Time-stretch selected clip", self.stretch_dialog, "Audio")
        spec(
            "clip.tempo_conform",
            "Conform selected clip to arranged length",
            self.tempo_conform_dialog,
            "Audio",
        )

        spec(
            "notes.quantize",
            "Quantize selected notes",
            lambda: self._run(quantize_notes, w),
            "Piano Roll",
            "Ctrl+Q",
        )
        spec("notes.strum", "Strum selected notes", self.strum_dialog, "Piano Roll")
        spec("notes.chop", "Chop selected notes", self.chop_dialog, "Piano Roll")
        spec(
            "notes.legato",
            "Make selected notes legato",
            lambda: self._run(legato_notes, w),
            "Piano Roll",
        )
        spec(
            "notes.random_velocity",
            "Randomize selected-note velocity",
            self.velocity_dialog,
            "Piano Roll",
        )
        spec("notes.scale_lock", "Lock selected notes to scale", self.scale_dialog, "Piano Roll")

        spec(
            "track.freeze",
            "Freeze selected mixer track",
            self.freeze_selected_track,
            "Mixer",
            "Ctrl+Alt+F",
        )
        spec(
            "track.unfreeze", "Unfreeze selected mixer track", self.unfreeze_selected_track, "Mixer"
        )
        spec(
            "track.bounce",
            "Bounce selected mixer track to library",
            self.bounce_selected_track,
            "Mixer",
        )
        spec(
            "track.new_take_lane",
            "Create and arm new take lane",
            lambda: self._run(new_take_lane, w),
            "Recording",
            "Ctrl+Alt+T",
        )
        spec("mixer.create_group", "Create VCA track group", self.create_group_dialog, "Mixer")
        spec("mixer.edit_group", "Edit track group", self.edit_group_dialog, "Mixer")
        spec("mixer.sidechain", "Create sidechain duck route", self.sidechain_dialog, "Mixer")
        spec("track.preset_save", "Save selected track preset", self.save_track_preset, "Mixer")
        spec("track.preset_recall", "Recall track preset", self.recall_track_preset, "Mixer")
        spec("master.preset", "Apply mastering preset", self.mastering_dialog, "Mastering")

        spec(
            "automation.smooth",
            "Smooth current automation lane",
            lambda: self._run(smooth_current_automation, w),
            "Automation",
        )
        spec("scene.capture", "Capture current pattern as scene", self.capture_scene, "Scenes")
        spec("scene.launch", "Launch scene", self.launch_scene_dialog, "Scenes")

        spec(
            "workflow.command_palette",
            "Command palette",
            self.show_command_palette,
            "Workflow",
            "Ctrl+Shift+P",
            ("search", "actions"),
        )
        spec("workflow.keymap", "Edit keyboard shortcuts", self.show_keymap_editor, "Workflow")
        spec("workflow.macro_create", "Create macro", self.create_macro_dialog, "Workflow")
        spec("workflow.macro_run", "Run macro", self.run_macro_dialog, "Workflow")

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
        menu = self.window.menuBar().addMenu("Workflow")
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
                action = sub.addAction(self.registry.get(command_id).title)
                action.triggered.connect(lambda _=False, cid=command_id: self._execute(cid))

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
        import math

        clip = self._selected_audio_clip()
        current = 20.0 * math.log10(max(1e-6, clip.gain))
        value, ok = QInputDialog.getDouble(
            self.window,
            "Clip gain",
            "Gain (dB)",
            current,
            -60.0,
            24.0,
            2,
        )
        if not ok:
            return None
        self.window.snapshot()
        clip.gain = 10.0 ** (value / 20.0)
        self.window.playlist.update()
        self.window._set_dirty(True)
        return clip.gain

    def stretch_dialog(self):
        clip = self._selected_audio_clip()
        beats, ok = QInputDialog.getDouble(
            self.window,
            "Time stretch",
            "Target length (beats)",
            clip.length_beats,
            0.03125,
            100000.0,
            4,
        )
        if not ok:
            return None
        mode, ok = QInputDialog.getItem(
            self.window,
            "Stretch mode",
            "Algorithm",
            list(MODES),
            list(MODES).index("complex"),
            False,
        )
        if not ok:
            return None
        result = stretch_selected_clip(self.window, beats, mode)
        self.window.status.showMessage(f"rendered {mode} stretch · {beats:g} beats", 4000)
        return result

    def tempo_conform_dialog(self):
        self._selected_audio_clip()
        mode, ok = QInputDialog.getItem(
            self.window,
            "Tempo conform",
            "Algorithm",
            list(MODES),
            list(MODES).index("complex"),
            False,
        )
        if not ok:
            return None
        result = tempo_conform_selected_clip(self.window, mode)
        self.window.status.showMessage("clip conformed to its arranged beat length", 4000)
        return result

    def fade_dialog(self):
        self._selected_audio_clip()
        fade_in, ok = QInputDialog.getDouble(
            self.window,
            "Clip fade",
            "Fade in (ms)",
            5.0,
            0.0,
            60000.0,
            1,
        )
        if not ok:
            return None
        fade_out, ok = QInputDialog.getDouble(
            self.window,
            "Clip fade",
            "Fade out (ms)",
            10.0,
            0.0,
            60000.0,
            1,
        )
        if not ok:
            return None
        result = fade_selected_clip(self.window, fade_in, fade_out)
        self.window.status.showMessage("clip fades rendered non-destructively", 3500)
        return result

    def strum_dialog(self):
        spread, ok = QInputDialog.getDouble(
            self.window,
            "Strum notes",
            "Spread (beats)",
            0.04,
            0.0,
            2.0,
            4,
        )
        return strum_notes(self.window, spread) if ok else None

    def chop_dialog(self):
        divisions, ok = QInputDialog.getInt(
            self.window,
            "Chop notes",
            "Retriggers per note",
            2,
            2,
            32,
        )
        return chop_notes(self.window, divisions) if ok else None

    def velocity_dialog(self):
        amount, ok = QInputDialog.getDouble(
            self.window,
            "Randomize velocity",
            "Maximum change",
            0.15,
            0.0,
            1.0,
            2,
        )
        return randomize_note_velocity(self.window, amount) if ok else None

    def scale_dialog(self):
        roots = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
        root, ok = QInputDialog.getItem(self.window, "Scale lock", "Root", roots, 0, False)
        if not ok:
            return None
        scale, ok = QInputDialog.getItem(
            self.window,
            "Scale lock",
            "Scale",
            list(SCALES),
            0,
            False,
        )
        return scale_lock_notes(self.window, roots.index(root), scale) if ok else None

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
        name, ok = QInputDialog.getText(
            self.window,
            "Track group",
            "Group name",
            text="GROUP",
        )
        if not ok:
            return None
        default = str(self._selected_track_index() + 1)
        text, ok = QInputDialog.getText(
            self.window,
            "Track group",
            "Mixer track numbers (comma separated)",
            text=default,
        )
        if not ok:
            return None
        indices = self._parse_track_numbers(text)
        self.window.snapshot()
        group = {
            "id": uid(),
            "name": name.strip() or "GROUP",
            "members": [self.window.project.tracks[index].id for index in indices],
            "gain": 1.0,
            "mute": False,
        }
        ensure_workflow(self.window.project).setdefault("groups", []).append(group)
        self.window._set_dirty(True)
        self.window.status.showMessage(f"created group {group['name']}", 3500)
        return group

    def edit_group_dialog(self):
        groups = ensure_workflow(self.window.project).get("groups", [])
        if not groups:
            raise ValueError("no track groups exist yet")
        labels = [group["name"] for group in groups]
        label, ok = QInputDialog.getItem(
            self.window,
            "Track group",
            "Group",
            labels,
            0,
            False,
        )
        if not ok:
            return None
        group = groups[labels.index(label)]
        gain, ok = QInputDialog.getDouble(
            self.window,
            "Track group",
            "Gain",
            float(group.get("gain", 1.0)),
            0.0,
            2.0,
            3,
        )
        if not ok:
            return None
        self.window.snapshot()
        group["gain"] = gain
        self.window._set_dirty(True)
        return group

    def sidechain_dialog(self):
        tracks = [
            f"{index + 1}: {track.name}" for index, track in enumerate(self.window.project.tracks)
        ]
        source, ok = QInputDialog.getItem(
            self.window,
            "Sidechain",
            "Source",
            tracks,
            0,
            False,
        )
        if not ok:
            return None
        target, ok = QInputDialog.getItem(
            self.window,
            "Sidechain",
            "Target",
            tracks,
            min(1, len(tracks) - 1),
            False,
        )
        if not ok:
            return None
        source_index, target_index = tracks.index(source), tracks.index(target)
        if source_index == target_index:
            raise ValueError("sidechain source and target must differ")
        amount, ok = QInputDialog.getDouble(
            self.window,
            "Sidechain",
            "Duck amount",
            4.0,
            0.0,
            32.0,
            2,
        )
        if not ok:
            return None
        self.window.snapshot()
        route = {
            "source": self.window.project.tracks[source_index].id,
            "target": self.window.project.tracks[target_index].id,
            "amount": amount,
            "threshold": 0.05,
            "enabled": True,
        }
        ensure_workflow(self.window.project).setdefault("sidechains", []).append(route)
        self.window._set_dirty(True)
        return route

    def save_track_preset(self):
        index = self._selected_track_index()
        track = self.window.project.tracks[index]
        name, ok = QInputDialog.getText(
            self.window,
            "Track preset",
            "Preset name",
            text=track.name,
        )
        if not ok:
            return None
        preset = {
            "gain": track.gain,
            "pan": track.pan,
            "fx": asdict(track.fx),
        }
        presets = ensure_workflow(self.window.project).setdefault("track_presets", {})
        presets[name.strip() or track.name] = preset
        self.window._set_dirty(True)
        return preset

    def recall_track_preset(self):
        presets = ensure_workflow(self.window.project).get("track_presets", {})
        if not presets:
            raise ValueError("no track presets have been saved")
        names = sorted(presets)
        name, ok = QInputDialog.getItem(
            self.window,
            "Track preset",
            "Preset",
            names,
            0,
            False,
        )
        if not ok:
            return None
        index = self._selected_track_index()
        track = self.window.project.tracks[index]
        preset = presets[name]
        self.window.snapshot()
        track.gain = float(preset.get("gain", track.gain))
        track.pan = float(preset.get("pan", track.pan))
        track.fx = TrackFX(**preset.get("fx", {}))
        self.window._mixer_changed()
        return track

    def mastering_dialog(self):
        presets = {
            "Clean": MasterFX(),
            "Glue": MasterFX(glue=True, glue_amount=0.35),
            "Warm": MasterFX(low=0.5, high=-0.2, drive=0.08, glue=True, glue_amount=0.25),
            "Presence": MasterFX(mid=0.3, high=0.5, glue=True, glue_amount=0.2),
        }
        name, ok = QInputDialog.getItem(
            self.window,
            "Mastering preset",
            "Preset",
            list(presets),
            0,
            False,
        )
        if not ok:
            return None
        self.window.snapshot()
        self.window.project.master_fx = presets[name]
        self.window.engine.prepare_fx()
        self.window._set_dirty(True)
        return self.window.project.master_fx

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
        dialog = QDialog(self.window)
        dialog.setWindowTitle("Command palette")
        layout = QVBoxLayout(dialog)
        search = QLineEdit()
        search.setPlaceholderText("Search commands, freeze, quantize, route, stretch…")
        results = QListWidget()
        layout.addWidget(search)
        layout.addWidget(results, 1)

        def refresh(text=""):
            results.clear()
            for command in self.registry.search(text, 80):
                shortcut = self.registry.bindings.get(command.id, "")
                label = f"{command.title}    {shortcut}" if shortcut else command.title
                item = QListWidgetItem(label)
                item.setData(Qt.UserRole, ("command", command.id))
                results.addItem(item)
            query = text.casefold().strip()
            for name in sorted(self.registry.macros):
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
                self.registry.run_macro(value)
            else:
                self._execute(value)

        search.textChanged.connect(refresh)
        search.returnPressed.connect(lambda: run_item(results.currentItem()))
        results.itemActivated.connect(run_item)
        refresh()
        dialog.resize(620, 520)
        search.setFocus()
        dialog.exec()

    def show_keymap_editor(self):
        dialog = QDialog(self.window)
        dialog.setWindowTitle("Keyboard shortcuts")
        layout = QVBoxLayout(dialog)
        preset = QComboBox()
        preset.addItems([*PRESET_BINDINGS, "Custom"])
        preset.setCurrentText(self.registry.preset)
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
            for command in self.registry.commands:
                shortcut = self.registry.bindings.get(command.id, "")
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
                editor.setKeySequence(QKeySequence(self.registry.bindings.get(command_id, "")))

        def do_assign(clear_value=False):
            item = commands.currentItem()
            if not item:
                return
            sequence = ""
            if not clear_value:
                sequence = editor.keySequence().toString(QKeySequence.PortableText)
            self.registry.bind(item.data(Qt.UserRole), sequence)
            preset.blockSignals(True)
            preset.setCurrentText("Custom")
            preset.blockSignals(False)
            self._reindex_bindings()
            refill()

        def apply_preset(name):
            if name == "Custom":
                return
            self.registry.apply_preset(name)
            self._reindex_bindings()
            refill()

        commands.currentItemChanged.connect(lambda *_: selected_changed())
        assign.clicked.connect(lambda: do_assign(False))
        clear.clicked.connect(lambda: do_assign(True))
        preset.currentTextChanged.connect(apply_preset)
        refill()
        selected_changed()
        dialog.resize(720, 620)
        dialog.exec()

    def create_macro_dialog(self):
        name, ok = QInputDialog.getText(self.window, "Create macro", "Macro name")
        if not ok:
            return None
        text, ok = QInputDialog.getMultiLineText(
            self.window,
            "Create macro",
            "Command ids, one per line\n(use the command palette/keymap editor to inspect ids)",
            "playlist.duplicate\nclip.bounce_in_place",
        )
        if not ok:
            return None
        command_ids = [part.strip() for part in re.split(r"[\n,;]+", text) if part.strip()]
        result = self.registry.define_macro(name, command_ids)
        self.window.status.showMessage(f"saved macro {result.name}", 3500)
        return result

    def run_macro_dialog(self):
        if not self.registry.macros:
            raise ValueError("create a macro first")
        names = sorted(self.registry.macros)
        name, ok = QInputDialog.getItem(
            self.window,
            "Run macro",
            "Macro",
            names,
            0,
            False,
        )
        return self.registry.run_macro(name) if ok else None

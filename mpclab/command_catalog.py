"""Command catalog.

The caller retains Qt/project ownership; these operations receive it explicitly.
"""

from __future__ import annotations
from .workflow_commands import CommandSpec
from .workflow_notes import (
    legato_notes,
    quantize_notes,
)
from .workflow_tools import (
    bounce_selected_in_place,
    consolidate_selected_audio,
    new_take_lane,
    smooth_current_automation,
)


def _register_commands(owner) -> None:
    w = owner.window

    def spec(command_id, title, callback, category, shortcut="", keywords=()):
        owner.registry.register(
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
            owner._legacy_sequences.add(owner._normal(shortcut))

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
        owner._playlist_split,
        "Playlist",
        "Shift+S",
    )
    spec(
        "playlist.duplicate",
        "Duplicate selected clip(s)",
        owner._playlist_duplicate,
        "Playlist",
        "Ctrl+D",
    )
    spec(
        "clip.consolidate",
        "Consolidate selected audio",
        lambda: owner._run(consolidate_selected_audio, w),
        "Audio",
        "Ctrl+J",
    )
    spec(
        "clip.bounce_in_place",
        "Bounce selected audio in place",
        lambda: owner._run(bounce_selected_in_place, w),
        "Audio",
        "Ctrl+Alt+B",
    )
    spec("clip.loop_toggle", "Toggle selected clip loop", owner._toggle_clip_loop, "Audio")
    spec(
        "clip.reverse_toggle",
        "Toggle selected clip reverse",
        owner._toggle_clip_reverse,
        "Audio",
    )
    spec("clip.gain", "Set selected clip gain", owner.set_clip_gain, "Audio")
    spec("clip.fade", "Render clip fades", owner.fade_dialog, "Audio")
    spec("clip.stretch", "Time-stretch selected clip", owner.stretch_dialog, "Audio")
    spec(
        "clip.tempo_conform",
        "Conform selected clip to arranged length",
        owner.tempo_conform_dialog,
        "Audio",
    )

    spec(
        "notes.quantize",
        "Quantize selected notes",
        lambda: owner._run(quantize_notes, w),
        "Piano Roll",
        "Ctrl+Q",
    )
    spec("notes.strum", "Strum selected notes", owner.strum_dialog, "Piano Roll")
    spec("notes.chop", "Chop selected notes", owner.chop_dialog, "Piano Roll")
    spec(
        "notes.legato",
        "Make selected notes legato",
        lambda: owner._run(legato_notes, w),
        "Piano Roll",
    )
    spec(
        "notes.random_velocity",
        "Randomize selected-note velocity",
        owner.velocity_dialog,
        "Piano Roll",
    )
    spec("notes.scale_lock", "Lock selected notes to scale", owner.scale_dialog, "Piano Roll")

    spec(
        "track.freeze",
        "Freeze selected mixer track",
        owner.freeze_selected_track,
        "Mixer",
        "Ctrl+Alt+F",
    )
    spec("track.unfreeze", "Unfreeze selected mixer track", owner.unfreeze_selected_track, "Mixer")
    spec(
        "track.bounce",
        "Bounce selected mixer track to library",
        owner.bounce_selected_track,
        "Mixer",
    )
    spec(
        "track.new_take_lane",
        "Create and arm new take lane",
        lambda: owner._run(new_take_lane, w),
        "Recording",
        "Ctrl+Alt+T",
    )
    spec("mixer.create_group", "Create VCA track group", owner.create_group_dialog, "Mixer")
    spec("mixer.edit_group", "Edit track group", owner.edit_group_dialog, "Mixer")
    spec("mixer.sidechain", "Create sidechain duck route", owner.sidechain_dialog, "Mixer")
    spec("track.preset_save", "Save selected track preset", owner.save_track_preset, "Mixer")
    spec("track.preset_recall", "Recall track preset", owner.recall_track_preset, "Mixer")
    spec("master.preset", "Apply mastering preset", owner.mastering_dialog, "Mastering")

    spec(
        "automation.smooth",
        "Smooth current automation lane",
        lambda: owner._run(smooth_current_automation, w),
        "Automation",
    )
    spec("scene.capture", "Capture current pattern as scene", owner.capture_scene, "Scenes")
    spec("scene.launch", "Launch scene", owner.launch_scene_dialog, "Scenes")

    spec(
        "workflow.command_palette",
        "Command palette",
        owner.show_command_palette,
        "Workflow",
        "Ctrl+Shift+P",
        ("search", "actions"),
    )
    spec("workflow.keymap", "Edit keyboard shortcuts", owner.show_keymap_editor, "Workflow")
    spec("workflow.macro_create", "Create macro", owner.create_macro_dialog, "Workflow")
    spec("workflow.macro_run", "Run macro", owner.run_macro_dialog, "Workflow")

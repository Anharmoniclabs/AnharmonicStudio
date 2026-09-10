"""Explicit, undoable mixer-track creation outside the audio callback."""

from ..model import MAX_TRACKS, Project
from ..workflow_commands import CommandSpec


def require_idle_capture(window):
    capture = getattr(window, "track_capture", None)
    vocals = getattr(window, "vocal_panel", None)
    recorder = getattr(vocals, "recorder", None)
    if (
        getattr(capture, "busy", False)
        or getattr(vocals, "_counting", False)
        or getattr(recorder, "recording", False)
        or getattr(recorder, "temporary_path", None) is not None
    ):
        raise RuntimeError("Finish and save the recording before changing mixer tracks")


def add_mixer_track(window, name=None):
    require_idle_capture(window)
    if window.engine.playing:
        raise RuntimeError("Stop playback before adding a mixer track")
    if len(window.project.tracks) >= MAX_TRACKS:
        raise ValueError("This project already contains the maximum 128 mixer tracks")
    project = Project.from_dict(window.project.to_dict())
    track = project.add_track(name or f"Track {len(project.tracks) + 1}")
    undo, redo, dirty = list(window._undo), list(window._redo), window._dirty
    window.snapshot()
    try:
        window._apply_project(project)
    except Exception:
        window._undo, window._redo = undo, redo
        window._try_save_history()
        window._set_dirty(dirty)
        raise
    window._set_dirty(True)
    window.mixer.select_track(len(project.tracks) - 1)
    window.mixer.scroller.ensureWidgetVisible(window.mixer.strips[-1])
    window.status.showMessage(f"Added {track.name} · {len(project.tracks)} mixer tracks", 4000)
    return track


def attach_track_management(window, controller):
    if getattr(window, "_track_management_attached", False):
        return
    controller.registry.register(
        CommandSpec(
            "mixer.track_add",
            "Add mixer track",
            lambda: add_mixer_track(window),
            category="Mixer",
            keywords=("create", "independent", "channel", "128"),
        )
    )
    controller._reindex_bindings()
    window._track_management_attached = True

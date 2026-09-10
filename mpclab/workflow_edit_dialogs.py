"""Workflow edit dialogs.

The caller retains Qt/project ownership; these operations receive it explicitly.
"""

from __future__ import annotations
from PySide6.QtWidgets import (
    QInputDialog,
)
from .time_stretch import MODES
from .workflow_notes import (
    SCALES,
    chop_notes,
    randomize_note_velocity,
    scale_lock_notes,
    strum_notes,
)
from .workflow_tools import (
    fade_selected_clip,
    stretch_selected_clip,
    tempo_conform_selected_clip,
)


def set_clip_gain(owner):
    import math

    clip = owner._selected_audio_clip()
    current = 20.0 * math.log10(max(1e-6, clip.gain))
    value, ok = QInputDialog.getDouble(
        owner.window,
        "Clip gain",
        "Gain (dB)",
        current,
        -60.0,
        24.0,
        2,
    )
    if not ok:
        return None
    owner.window.snapshot()
    clip.gain = 10.0 ** (value / 20.0)
    owner.window.playlist.update()
    owner.window._set_dirty(True)
    return clip.gain


def stretch_dialog(owner):
    clip = owner._selected_audio_clip()
    beats, ok = QInputDialog.getDouble(
        owner.window,
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
        owner.window,
        "Stretch mode",
        "Algorithm",
        list(MODES),
        list(MODES).index("complex"),
        False,
    )
    if not ok:
        return None
    result = stretch_selected_clip(owner.window, beats, mode)
    owner.window.status.showMessage(f"rendered {mode} stretch · {beats:g} beats", 4000)
    return result


def tempo_conform_dialog(owner):
    owner._selected_audio_clip()
    mode, ok = QInputDialog.getItem(
        owner.window,
        "Tempo conform",
        "Algorithm",
        list(MODES),
        list(MODES).index("complex"),
        False,
    )
    if not ok:
        return None
    result = tempo_conform_selected_clip(owner.window, mode)
    owner.window.status.showMessage("clip conformed to its arranged beat length", 4000)
    return result


def fade_dialog(owner):
    owner._selected_audio_clip()
    fade_in, ok = QInputDialog.getDouble(
        owner.window,
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
        owner.window,
        "Clip fade",
        "Fade out (ms)",
        10.0,
        0.0,
        60000.0,
        1,
    )
    if not ok:
        return None
    result = fade_selected_clip(owner.window, fade_in, fade_out)
    owner.window.status.showMessage("clip fades rendered non-destructively", 3500)
    return result


def strum_dialog(owner):
    spread, ok = QInputDialog.getDouble(
        owner.window,
        "Strum notes",
        "Spread (beats)",
        0.04,
        0.0,
        2.0,
        4,
    )
    return strum_notes(owner.window, spread) if ok else None


def chop_dialog(owner):
    divisions, ok = QInputDialog.getInt(
        owner.window,
        "Chop notes",
        "Retriggers per note",
        2,
        2,
        32,
    )
    return chop_notes(owner.window, divisions) if ok else None


def velocity_dialog(owner):
    amount, ok = QInputDialog.getDouble(
        owner.window,
        "Randomize velocity",
        "Maximum change",
        0.15,
        0.0,
        1.0,
        2,
    )
    return randomize_note_velocity(owner.window, amount) if ok else None


def scale_dialog(owner):
    roots = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    root, ok = QInputDialog.getItem(owner.window, "Scale lock", "Root", roots, 0, False)
    if not ok:
        return None
    scale, ok = QInputDialog.getItem(
        owner.window,
        "Scale lock",
        "Scale",
        list(SCALES),
        0,
        False,
    )
    return scale_lock_notes(owner.window, roots.index(root), scale) if ok else None
